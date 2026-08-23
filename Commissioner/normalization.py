"""
Shared record-normalization helpers used by both Paleographer.py (AI-transcribed records)
and Voyageur/FS.py (FamilySearch-indexed records) - text casing, date parsing, and
record/role identity derivation that both sources need applied the same way so Archivist
sees one consistent shape regardless of provenance.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from titlecase import titlecase

PRESERVED_ACRONYMS = {"HBC", "NWT", "USA", "NWMP", "RCMP", "UK", "US", "ED", "PID", "RM", "FTM"}


def _titlecase_callback(word: str, **_kwargs) -> Optional[str]:
    w_clean = re.sub(r'^[^\w]+|[^\w]+$', '', word)
    if w_clean.upper() in PRESERVED_ACRONYMS:
        return word.replace(w_clean, w_clean.upper())
    if "-" in word:
        parts = word.split("-")
        return "-".join(
            (p.upper() if re.sub(r'^[^\w]+|[^\w]+$', '', p).upper() in PRESERVED_ACRONYMS
             else titlecase(p, callback=_titlecase_callback).capitalize())
            for p in parts
        )
    return None


def capitalize_text_string(text: Any) -> str:
    """Format string to Title Case using the titlecase library while preserving
    genealogical acronyms (HBC, NWT, etc.) and handling nulls/empty strings safely."""
    if text is None:
        return ""
    val = str(text).strip()
    if not val or val.lower() in ("null", "none"):
        return ""
    return titlecase(val, callback=_titlecase_callback)


MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_ISO_DATE_PATTERN = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
_ISO_YEAR_MONTH_PATTERN = re.compile(r"^\s*(\d{4})-(\d{2})\s*$")

_DATE_PATTERNS = [
    re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{3,4})\s*$"),  # "December 12, 1850"
    re.compile(r"^\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?,?\s+(\d{3,4})\s*$"),  # "12 December 1850"
    re.compile(r"^\s*([A-Za-z]+)\.?\s+(\d{3,4})\s*$"),                                # "December 1850"
    re.compile(r"^\s*(\d{3,4})\s*$"),                                                  # bare year "1850"
]


def parse_date_to_iso_format(reading: Optional[str]) -> Optional[str]:
    """Parses an English-language date reading into ISO format (YYYY-MM-DD, YYYY-MM, or YYYY)."""
    if not reading:
        return None
    text = reading.strip()

    if _ISO_DATE_PATTERN.match(text) or _ISO_YEAR_MONTH_PATTERN.match(text):
        return text

    m = _DATE_PATTERNS[0].match(text)
    if m:
        month = MONTH_NAMES.get(m.group(1).lower())
        if month:
            return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"

    m = _DATE_PATTERNS[1].match(text)
    if m:
        month = MONTH_NAMES.get(m.group(2).lower())
        if month:
            return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"

    m = _DATE_PATTERNS[2].match(text)
    if m:
        month = MONTH_NAMES.get(m.group(1).lower())
        if month:
            return f"{int(m.group(2)):04d}-{month:02d}"

    m = _DATE_PATTERNS[3].match(text)
    if m:
        return f"{int(m.group(1)):04d}"

    return None


def derive_record_identity(record: Dict[str, Any], event_types_table: Dict[str, Dict[str, str]],
                           set_type_code: bool = False) -> None:
    """Sets record_id and optionally record_type_code from event_type."""
    event_type = record.get("event_type")
    entry: Optional[Dict[str, str]] = event_types_table.get(event_type) if event_type else None
    if not entry:
        return

    if set_type_code:
        record["record_type_code"] = entry.get("code")

    record_number: Optional[str] = record.get("record_number")
    if record_number:
        record["record_id"] = f"{entry.get('id_prefix', '')}{record_number}"


def derive_role_number(role_name: str, roles_table: Dict[str, Dict[str, Optional[str]]]) -> Optional[str]:
    """Looks up a participant's role_number from their plain-word role_name."""
    name_to_number = {(role.get("name") or "").strip().lower(): number for number, role in roles_table.items()}
    return name_to_number.get((role_name or "").strip().lower())


def derive_role_semantic(role_number: Optional[str],
                         roles_table: Dict[str, Dict[str, Optional[str]]]) -> Optional[str]:
    """Looks up a participant's role_semantic from their already-resolved role_number."""
    role = roles_table.get(role_number) if role_number else None
    return role.get("semantic") if role else None


def normalize_sex_code(raw: Optional[str], default: str = "") -> str:
    """Normalize a raw sex / gender string into standard 'M', 'F', or fallback."""
    if not raw:
        return default
    text = str(raw).strip().lower()
    if text.startswith("f"):
        return "F"
    if text.startswith("m"):
        return "M"
    return default


def normalize_enum(
    raw: Optional[str],
    allowed: Dict[str, Any] | Iterable[str],
    default: Optional[Any] = None,
) -> Optional[Any]:
    """Normalize a raw string against an allowed set or mapping (case-insensitive)."""
    if not raw:
        return default
    val = str(raw).strip().lower()
    if isinstance(allowed, dict):
        mapping = {str(k).strip().lower(): v for k, v in allowed.items()}
        return mapping.get(val, default)
    mapping = {str(item).strip().lower(): item for item in allowed}
    return mapping.get(val, default)


__all__ = [
    "PRESERVED_ACRONYMS",
    "MONTH_NAMES",
    "capitalize_text_string",
    "parse_date_to_iso_format",
    "derive_record_identity",
    "derive_role_number",
    "derive_role_semantic",
    "normalize_sex_code",
    "normalize_enum",
]
