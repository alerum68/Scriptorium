"""
Utils.py - Record-type-agnostic helpers and shared constants for Archivist.
"""

import calendar
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Commissioner.textutils import clean_text  # noqa: E402
from Commissioner.normalization import (  # noqa: E402
    capitalize_text_string as _norm_capitalize_text_string,
)

# A raw scalar value as read from a JSON field or DataFrame cell.
CellValue = Union[str, int, float, bool, None]

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent.parent / "Paleographer" / ".env", override=False)


def get_env_int(key: str, default: int) -> int:
    val = os.getenv(key, "")
    try:
        return int(str(val).strip()) if val and str(val).strip() else default
    except ValueError:
        return default


def safe_path(base: str, *parts: str) -> str:
    """Safely joins paths, allowing absolute paths to override the base."""
    non_blank = [p for p in parts if p]
    if not non_blank:
        return ""
    res = base
    for p in non_blank:
        res = p if os.path.isabs(p) else f"{res.rstrip('/\\')}/{p}"
    return res


# Shared, generic settings.
# Research/attribution metadata is hardcoded as plain module constants.
ORG_NAME = os.getenv("ORG_NAME", "")
RESEARCHER = os.getenv("RESEARCHER", "")
SOFTWARE_NAME = "RootsMagic"
SOFTWARE_VERS = "11.0"
COPYRIGHT_START = "2018"
GEDCOM_NOTE = "This file contains original historical translations and research."
GEDCOM_CONC = "Please do not upload this raw GEDCOM to public, collaborative trees without permission and attribution."
REVIEW_COLOR = "1"
SUBM_ADDRESS = os.getenv("SUBM_ADDRESS", "")
MGS_GROUP_URL = os.getenv("MGS_GROUP_URL", "")
ANCESTRY_GROUP_URL = os.getenv("ANCESTRY_GROUP_URL", "")
ROOT_SOURCE_ID = os.getenv("ROOT_SOURCE_ID", "@S1@")

PROGRAM_DIR = os.getenv("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent))
GENEALOGY_DIR = os.getenv("GENEALOGY_DIR", "")
RM_DIR = safe_path(GENEALOGY_DIR, os.getenv("RM_DIR", ""))
FTM_DIR = safe_path(GENEALOGY_DIR, os.getenv("FTM_DIR", ""))
GEDCOM_OUTPUT_PATH = safe_path(GENEALOGY_DIR, os.getenv("GEDCOM_OUTPUT_PATH", ""))
GEDCOM_OUTPUT_NAME = os.getenv("GEDCOM_OUTPUT_NAME") or "Family_Register.ged"
GEDCOM_OUTPUT_MODE = os.getenv("GEDCOM_OUTPUT_MODE", "Both").strip()

CURRENT_DATE = datetime.datetime.now().strftime('%d %b %Y').upper()
APID_DB = os.getenv("APID_DB", "")


# ==========================================
# SOURCE ID REGISTRY
# ==========================================
_US_CENSUS_YEARS = [1790, 1800, 1810, 1820, 1830, 1840, 1850, 1860, 1870, 1880,
                    1890, 1900, 1910, 1920, 1930, 1940, 1950]
_CANADIAN_CENSUS_YEARS = [1851, 1861, 1871, 1881, 1891, 1901, 1911, 1921]

PRECODED_SOURCE_IDS: Dict[str, int] = {f"Census_{year}": 1001 + i for i, year in enumerate(_US_CENSUS_YEARS)}
PRECODED_SOURCE_IDS["Census_Slave_Schedule"] = 1020
PRECODED_SOURCE_IDS.update({f"Census_CA_{year}": 1021 + i for i, year in enumerate(_CANADIAN_CENSUS_YEARS)})

NEXT_AUTO_SOURCE_ID = 1030
SOURCE_ID_REGISTRY_PATH = Path(__file__).resolve().parent / "source_id_registry.json"


def resolve_source_id(record_type_name: str, collection_name: str = "") -> int:
    """Returns this document's stable 4-digit Source ID, retrieving or assigning it from the persistent registry."""
    if record_type_name in PRECODED_SOURCE_IDS:
        return PRECODED_SOURCE_IDS[record_type_name]

    try:
        registry = json.loads(SOURCE_ID_REGISTRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(registry, dict):
            registry = {"next": NEXT_AUTO_SOURCE_ID, "entries": {}}
    except (FileNotFoundError, json.JSONDecodeError):
        registry = {"next": NEXT_AUTO_SOURCE_ID, "entries": {}}

    key = f"{record_type_name}|{collection_name}"
    if key in registry["entries"]:
        return registry["entries"][key]

    new_id = registry.get("next", NEXT_AUTO_SOURCE_ID)
    registry["entries"][key] = new_id
    registry["next"] = new_id + 1
    SOURCE_ID_REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return new_id


# ==========================================
# FACT TYPES
# ==========================================
with open(Path(__file__).resolve().parent.parent / "Commissioner" / "FactTypes.json", "r", encoding="utf-8") as _fact_types_file:  # noqa: E501
    FACT_TYPES = json.load(_fact_types_file)


def get_event_gedcom_tag(event_type: str) -> str:
    """Looks up the GEDCOM tag for an event_type, falling back to 'EVEN'."""
    entry = FACT_TYPES.get('person', {}).get(event_type) or FACT_TYPES.get('family', {}).get(event_type)
    return entry.get('gedcom_tag', 'EVEN') if entry else 'EVEN'


def is_family_event(event_type: str) -> bool:
    """Returns True if the event_type is a family-level fact (e.g., Marriage)."""
    return event_type in FACT_TYPES.get('family', {})


# ==========================================
# SHARED UTILITIES
# ==========================================
def clean_val(val: CellValue) -> str:
    """Scrubs literal nulls, normalizes spaces, and clears invisible characters."""
    if val is None or (isinstance(val, float) and pd.isna(val)) or val == "":
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    val_str = clean_text(val)
    return "" if val_str.lower() in ["null", "none", ""] else val_str


_PLACE_QUALIFIER_RE = re.compile(
    r'^(?:near|around|about|approximately|close to|in the vicinity of)\s+', re.IGNORECASE)


def capitalize_text_string(text: CellValue) -> str:
    """Format string to Title Case using the titlecase library while preserving
    genealogical acronyms (HBC, NWT, etc.) and handling nulls/empty strings safely."""
    val = clean_val(text)
    if not val:
        return ""
    return _norm_capitalize_text_string(val)


def clean_place(val: CellValue) -> str:
    """Like clean_val, but also strips a leading descriptive qualifier ('near', 'around',
    'about', ...) off a place name and normalizes to proper Title Case."""
    cleaned = _PLACE_QUALIFIER_RE.sub('', clean_val(val)).strip()
    return capitalize_text_string(cleaned) if cleaned else ""


def split_full_name(full_name: str) -> Tuple[str, str]:
    """Splits a combined 'Given Surname' string into given and surname parts."""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return full_name.strip(), ""
    return " ".join(parts[:-1]), parts[-1]


def format_gedcom_date(date_str: str) -> str:
    """
    Converts dates (ISO YYYY-MM-DD, YYYY-MM, or natural text like "December 12, 1850",
    "12 Dec 1850") into GEDCOM standard format (e.g. "12 DEC 1850", "DEC 1850", "1850").
    Handles genealogical prefixes (BEF, AFT, ABT, CAL, EST, BET, FROM).
    """
    date_str = clean_val(date_str)
    if not date_str:
        return ""

    prefix = ""
    valid_prefixes = ("BEF ", "AFT ", "ABT ", "CAL ", "EST ", "BET ", "FROM ")
    for p in valid_prefixes:
        if date_str.upper().startswith(p):
            prefix = date_str[:len(p)].upper()
            date_str = date_str[len(p):].strip()
            break

    if re.match(r"^\d{4}$", date_str):
        return f"{prefix}{date_str}"

    clean_text = re.sub(r"[,.]", " ", date_str).strip()
    clean_text = re.sub(r"\s+", " ", clean_text)

    formats_day = [
        "%Y-%m-%d",
        "%d %B %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%b %d %Y",
        "%Y/%m/%d",
        "%m/%d/%Y",
    ]
    for fmt in formats_day:
        try:
            dt = datetime.datetime.strptime(clean_text, fmt)
            return f"{prefix}{dt.day} {dt.strftime('%b').upper()} {dt.year}"
        except ValueError:
            pass

    formats_month = [
        "%Y-%m",
        "%B %Y",
        "%b %Y",
        "%Y/%m",
        "%m/%Y",
    ]
    for fmt in formats_month:
        try:
            dt = datetime.datetime.strptime(clean_text, fmt)
            return f"{prefix}{dt.strftime('%b').upper()} {dt.year}"
        except ValueError:
            pass

    return prefix + date_str


def get_proof_status(date_str: str) -> str:
    """Evaluates date prefixes to assign proposed or proven proof status tags."""
    if date_str:
        upper_date = f"{date_str}".upper().strip()
        if upper_date.startswith(("BEF", "ABT", "AFT", "EST", "CAL")):
            return "proposed"
    return "proven"


def estimate_birth_from_age(event_date: str, age_str: str) -> str:
    """
    Calculates an estimated birthdate based on event date precision
    and detailed age metadata (years, months, weeks, days).
    """
    if not event_date or not age_str:
        return ""

    try:
        if len(f"{event_date}") == 10:
            dt = datetime.datetime.strptime(f"{event_date}"[:10], "%Y-%m-%d")
            precision = "day"
        elif len(f"{event_date}") == 7:
            dt = datetime.datetime.strptime(f"{event_date}"[:7], "%Y-%m")
            precision = "month"
        else:
            dt = datetime.datetime.strptime(f"{event_date}"[:4], "%Y")
            precision = "year"
    except ValueError:
        return ""

    age_str = f"{age_str}".strip().lower()
    years = sum(map(int, re.findall(r'(\d+)\s*(?:year|yr|y\b)', age_str)))
    months = sum(map(int, re.findall(r'(\d+)\s*(?:month|mo|m\b)', age_str)))
    weeks = sum(map(int, re.findall(r'(\d+)\s*(?:week|wk|w\b)', age_str)))
    days = sum(map(int, re.findall(r'(\d+)\s*(?:day|d\b)', age_str)))

    if not any([years, months, weeks, days]):
        match = re.search(r'\d+', age_str)
        if match:
            years = int(match.group())
        else:
            return ""

    if precision == "year" and (months or weeks or days):
        return f"CAL {dt.year - years}" if years else f"ABT {dt.year}"

    if precision == "month" and (weeks or days):
        if not (months or years):
            return f"ABT {dt.strftime('%Y-%m')}"
        weeks, days = 0, 0

    if weeks or days:
        dt -= datetime.timedelta(weeks=weeks, days=days)

    if months or years:
        total_months = dt.month - 1 - months
        new_year = dt.year - years + (total_months // 12)
        new_month = (total_months % 12) + 1
        last_day_of_month = calendar.monthrange(new_year, new_month)[1]
        dt = dt.replace(year=new_year, month=new_month, day=min(dt.day, last_day_of_month))

    if precision == "day" and any([months, weeks, days]):
        return f"CAL {dt.strftime('%Y-%m-%d')}"
    elif precision in ["day", "month"] and months:
        return f"CAL {dt.strftime('%Y-%m')}"

    return f"CAL {dt.year}"


def wrap_text(text: str, tag: str = "5 CONT") -> str:
    """Wraps text content natively for GEDCOM, slicing CONC tags mid-word to prevent layout artifacts."""
    if not text:
        return ""
    lines = clean_val(text).split('\n')
    wrapped = []

    for line in lines:
        if not line:
            continue
        wrapped.append(f"{tag} {line[:75]}")
        for i in range(75, len(line), 75):
            wrapped.append(f"{tag[:1]} CONC {line[i:i + 75]}")

    return "\n".join(wrapped)


def resolve_gedcom_output_targets() -> List[str]:
    """Returns the targeted GEDCOM output formats based on GEDCOM_OUTPUT_MODE."""
    if GEDCOM_OUTPUT_MODE == "RM":
        return ["RM"]
    if GEDCOM_OUTPUT_MODE == "FTM":
        return ["FTM"]
    return ["RM", "FTM"]


def resolve_gedcom_output_path(target_software: str) -> Path:
    """Resolves the output .ged path for a given software flavor (RM/FTM)."""
    base_name, ext = Path(GEDCOM_OUTPUT_NAME).stem, Path(GEDCOM_OUTPUT_NAME).suffix
    out_dir = Path(str(GEDCOM_OUTPUT_PATH)) if GEDCOM_OUTPUT_PATH else (
        Path(str(RM_DIR)) if target_software == "RM" else Path(str(FTM_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{target_software}" if len(resolve_gedcom_output_targets()) > 1 else ""
    return out_dir / f"{base_name}{suffix}{ext}"


def dedent_citation_lines(lines: List[str], skip_at_level: Optional[Tuple[int, str]] = None) -> List[str]:
    """Shifts a GEDCOM citation block up one level, for embedding inside a _TASK record.
    If skip_at_level is given as (level, prefix), lines at that exact level whose content
    starts with prefix are dropped entirely instead of shifted (e.g. dropping the 2 _PROOF
    line, which has no meaning inside a _TASK)."""
    result = []
    for line in lines:
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[0].isdigit():
            lvl = int(parts[0])
            if skip_at_level and lvl == skip_at_level[0] and parts[1].startswith(skip_at_level[1]):
                continue
            result.append(f"{lvl - 1} {parts[1]}")
        else:
            result.append(line)
    return result


def weblink_lines(url: str, name: str, target_software: str) -> List[str]:
    """
    RM keeps its proprietary _WEBTAG structure. FTM has no such tag; real Family Tree
    Maker exports use _LINK for the URL and duplicate it as a plain NOTE right after,
    a pattern confirmed against a genuine native FTM GEDCOM export.
    """
    if not url:
        return []
    if target_software == "RM":
        return ["1 _WEBTAG", f"2 NAME {name}", f"2 URL {url}"]
    return [f"1 _LINK {url}", f"1 NOTE {url}"]
