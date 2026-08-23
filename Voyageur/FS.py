"""
Voyageur (FS) - FamilySearch Gather.

Launches Voyageur.js's runFamilySearchGather() against a FamilySearch record page, waits for
its downloaded raw-scrape JSON, then converts that raw scrape into Paleographer/schema.json's
own shape (sheets -> records -> participants), so Archivist can process an already-indexed
FamilySearch collection exactly the same way it processes Paleographer's AI-transcribed
output. Event/fact vocabulary comes from the shared FactTypes.json (RootsMagic's own fact
types); participant role vocabulary comes from Parish.pmt's roles table, since every FS
gather so far is a parish/church register. Both are read directly as data (YAML/JSON) rather
than imported as code, keeping this tool self-contained like every other one in the toolbox.
(Commissioner.normalization is a deliberate shared-code exception to this usual data-only convention.)

Also handles same-person matching across duplicate index entries (stamping a shared link_id
rather than merging records - see Archivist's generate_uid, which already knows to prefer
link_id over its own hash).

Also fetches the source image itself (see downloadFsImage in Voyageur.js) - FamilySearch's
own image viewer's "Download" button turned out to fetch the complete page directly from its
deepzoomcloud storage service, keyed by the same item_id already scraped for citations, with
no tile-stitching needed at all.
"""

import hashlib
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode, urlunparse

import pandas as pd
from thefuzz import fuzz

import census_schema
from _gather_helpers import (
    cleanup_checkpoint_files,
    extract_census_image_routing_fields,
    find_orphaned_gather_runs,
    launch_gather_browser,
    move_downloaded_images,
    print_incomplete_pages_warning,
    resolve_census_image_dir,
    wait_for_final_json_event,
    write_archivist_json_file,
)

# Commissioner lives in a sibling tool folder, not an installed package - add the repo
# root to sys.path so it can be imported by absolute path, matching census_schema.py's own
# precedent for cross-package imports.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
try:
    from Archivist.Utils import split_full_name  # noqa: E402
except (ImportError, AttributeError):
    import importlib.util
    _u_spec = importlib.util.spec_from_file_location("Archivist_Utils", _REPO_ROOT / "Archivist" / "Utils.py")
    _u_mod = importlib.util.module_from_spec(_u_spec)
    _u_spec.loader.exec_module(_u_mod)
    split_full_name = _u_mod.split_full_name
from Commissioner import normalization  # noqa: E402
from Commissioner.envkit import load_tool_env  # noqa: E402
from Commissioner.models import FACT_DEFINITIONS  # noqa: E402
from Commissioner.record_registry import load_pmt_front_matter  # noqa: E402
from Commissioner.textutils import sanitize_image_filename as sanitize_item_id_filename  # noqa: E402
from Commissioner.winio import (  # noqa: E402
    read_text_with_retry as _read_text_with_retry,
    unlink_with_retry as _unlink_with_retry,
)

ANTIQUARIAN_DIR = Path(__file__).resolve().parent.parent
PARISH_PMT_PATH = ANTIQUARIAN_DIR / "Paleographer" / "prompts" / "Parish.pmt"

# ==========================================
# SHARED VOCABULARY (read as data, not imported as code - see module docstring)
# ==========================================


def load_event_types() -> Dict[str, Dict[str, str]]:
    """Loads the toolbox-wide fact/event vocabulary from Commissioner.models.FACT_DEFINITIONS."""
    return {
        fd.name: {"code": fd.code, "id_prefix": f"{fd.gedcom_tag}-"}
        for fd in FACT_DEFINITIONS
    }


def load_roles() -> Dict[str, Dict[str, Optional[str]]]:
    """Loads Parish.pmt's participant role vocabulary (Primary/Father/Mother/Spouse/Father of
    Spouse/Mother of Spouse/godparents) - every FS gather so far is a parish register, so
    this is the right vocabulary source, same as Paleographer's own AI transcription uses."""
    front_matter = load_pmt_front_matter(PARISH_PMT_PATH)
    return front_matter.get("roles", {})


# Event Type values as FamilySearch's Image Index actually spells them, mapped to
# FactTypes.json's own fact names where they differ (e.g. FamilySearch says
# "Christening", RootsMagic's built-in fact is named "Christen").
EVENT_TYPE_ALIASES = {
    "Christening": "Christen",
}

# Column names as they actually appear in FamilySearch's Image Index table (dynamic per
# record type - a Baptism-only page has no Spouse columns at all, for example), mapped to
# Parish.pmt's own role names. Extend this table as new column names are seen on other
# collections; anything not listed here is simply not extracted rather than guessed at.
ROLE_COLUMN_MAP = {
    "Name": "Primary",
    "Father's Name": "Father",
    "Mother's Name": "Mother",
    "Spouse's Name": "Spouse",
    "Spouse's Father's Name": "Father of Spouse",
    "Spouse's Mother's Name": "Mother of Spouse",
}
SEX_COLUMN_MAP = {
    "Name": "Sex",
    "Father's Name": "Father's Sex",
    "Mother's Name": "Mother's Sex",
    "Spouse's Name": "Spouse's Sex",
    "Spouse's Father's Name": "Spouse's Father's Sex",
    "Spouse's Mother's Name": "Spouse's Mother's Sex",
}

# Keyword match used to auto-detect record_family from the collection title/catalog text -
# generalizes Antiquarian.py's existing _record_type_family() keyword-matching so Archivist's
# single "Generate GEDCOM" button can dispatch without a manual per-type pick.
RECORD_FAMILY_KEYWORDS = {
    "census": ["census", "population schedule"],
    "church": ["church", "baptism", "baptême", "marriage", "mariage", "burial", "sépulture",
               "parish", "paroiss", "christening", "confirmation"],
    "scrip": ["scrip"],
    "wills": ["will", "probate", "estate", "testament"],
}

NAME_MATCH_THRESHOLD = 85
PARENT_MATCH_THRESHOLD = 80

CITATION_RE = re.compile(
    r'^"(?P<collection_name>.+?),"\s+database with images,\s+(?P<repository>.+?)\s*\(.*?\),\s*'
    r'(?P<browse_path>.+?)'
    r'(?:;\s*(?P<publisher>.+?),\s*(?P<pub_loc>[^,]+?))?\.\s*$'
)
# The trailing "; citing NARA microfilm publication ...
# (repo_loc: repo_name, n.d.)." clause is OPTIONAL in Voyageur.js's own
# fsBuildCitationTextFromApiResponse() - it only appends that clause when the image-level
# EXT_FILM_NBR/EXT_REPOSITORY_NAME fields are both present, otherwise the citation text ends
# with a bare period right after browse_path. The old pattern required that clause
# unconditionally, so a real gather missing those two fields failed to match AT ALL -
# collection_name/repository/browse_path/publisher/pub_loc all came back blank even though
# citation_text itself was non-empty (only collection_url, extracted separately, survived).


# ==========================================
# ROW -> RECORD MAPPING
# ==========================================
def split_name_and_dit(full: str) -> Tuple[str, str, Optional[str]]:
    """Splits a single displayed "Name" column into (given, surname, dit_name).
    FamilySearch's index doesn't carry given/surname separately, so this is a plain
    last-word-is-surname heuristic - correct for the vast majority of European-style names
    in these registers, and a single-word name (common for parents who aren't the record's
    own primary) is treated as surname-only rather than guessed at. A "dit" name (a second,
    commonly-used surname convention in French-Canadian and Métis families) is split out
    into its own field when present, e.g. "Jean Morin dit Lagimodiere" -> given="Jean",
    surname="Morin", dit_name="Lagimodiere" - matching how Archivist already reads
    participant['dit_name'] to emit the GEDCOM "dit Name" fact."""
    dit_match = re.search(r'\bdit\b', full, re.IGNORECASE)
    dit_name = None
    if dit_match:
        dit_name = full[dit_match.end():].strip() or None
        full = full[:dit_match.start()].strip()

    parts = full.split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1], dit_name
    return "", full, dit_name


def normalize_sex_code(raw: str) -> str:
    return normalization.normalize_sex_code(raw)


def build_participant(role_name: str, raw_name: str, sex: str,
                      fsftid: str = "", person_ark: str = "") -> Dict[str, Any]:
    given, surname, dit_name = split_name_and_dit(raw_name)
    type_specific_fields: Dict[str, Any] = {}
    if fsftid:
        type_specific_fields["fsftid"] = fsftid
    if person_ark:
        # This row's own ark - always present regardless of Family Tree attachment status
        # (see Voyageur.js's scrapeIndexRows) - used as the citation's web link, since it
        # points directly at this specific indexed entry on FamilySearch.
        type_specific_fields["person_ark"] = person_ark
    return {
        "role_number": None,
        "role_name": normalization.capitalize_text_string(role_name),
        "std_given": given,
        "std_surname": surname or None,
        "raw_given": None,
        "raw_surname": None,
        "dit_name": dit_name,
        "prefix": None,
        "suffix": None,
        "sex": sex,
        "is_priest": False,
        "age": None,
        "occupation": None,
        "race": None,
        "religion": None,
        "residence": None,
        "birth_date": None,
        "birth_place": None,
        "death_date": None,
        "death_place": None,
        "review": False,
        "review_reason": None,
        "type_specific_fields": type_specific_fields,
    }


def row_to_record(row: dict, item_id: str, row_index: int) -> dict:
    """Converts one raw Image Index row into a schema.json-shaped record, reusing Parish.pmt's
    existing role vocabulary and the shared FactTypes.json event vocabulary rather than
    inventing new ones. Extracts every column FamilySearch's Image Index actually provides
    (not just Name/Father/Mother/Spouse), since a real register mixes Baptism, Christening,
    Marriage, and Burial rows with different column sets on the same page.

    item_id/row_index exist purely as a uniqueness fallback: Archivist's generate_uid/
    generate_fam_uid hash individuals and families from (vol, page, record_id, role) alone -
    no name is in the hash at all - on the assumption that a real page/record locator is
    always present, true for Paleographer's AI-read pages. FamilySearch's Image Index often
    has neither "Page Number" nor "Entry Number" (confirmed on real data: ~85% of rows in one
    real register), and leaving both blank would collapse hundreds of genuinely different
    people/families onto the same hash. Falling back to this row's own item_id/position
    keeps every record's (page, record_number) pair globally unique without touching
    Archivist's proven hashing scheme."""
    columns = row.get("columns", {})
    participants = []
    primary: Optional[Dict[str, Any]] = None
    for name_col, role_name in ROLE_COLUMN_MAP.items():
        full = (columns.get(name_col) or "").strip()
        if not full:
            continue
        fsftid = row.get("attached_fsftid", "") if role_name == "Primary" else ""
        person_ark = row.get("person_ark", "") if role_name == "Primary" else ""
        participant = build_participant(role_name, full, normalize_sex_code(columns.get(SEX_COLUMN_MAP[name_col], "")),
                                        fsftid, person_ark)
        participants.append(participant)
        if role_name == "Primary":
            primary = participant

    # Age/birth/death/legitimacy columns describe the record's own primary participant (the
    # baptized child, the deceased, etc.), not any of the other roles.
    if primary is not None:
        primary["age"] = (columns.get("Age") or "").strip() or None
        birth_date = (columns.get("Birth Date") or "").strip() or (columns.get("Birth Year (Estimated)") or "").strip()
        primary["birth_date"] = normalization.parse_date_to_iso_format(birth_date) or (birth_date or None)
        death_date = (columns.get("Death Date") or "").strip()
        primary["death_date"] = normalization.parse_date_to_iso_format(death_date) or (death_date or None)
        legitimacy = (columns.get("Legitimacy") or "").strip()
        if legitimacy:
            primary["type_specific_fields"]["legitimacy"] = legitimacy

    raw_event_type = (columns.get("Event Type") or "").strip()
    event_type = normalization.capitalize_text_string(EVENT_TYPE_ALIASES.get(raw_event_type, raw_event_type))
    event_date_raw = (columns.get("Event Date") or "").strip()

    page = (columns.get("Page Number") or "").strip() or item_id
    record_number = (columns.get("Entry Number") or "").strip() or str(row_index + 1)

    return {
        "record_id": None,
        "page": page,
        "record_number": record_number,
        "record_type_code": None,
        "event_type": event_type,
        "year": (normalization.parse_date_to_iso_format(event_date_raw) or "")[:4] or None,
        "event_date": normalization.parse_date_to_iso_format(event_date_raw) or event_date_raw or None,
        "event_place": normalization.capitalize_text_string((columns.get("Event Place") or "").strip()) or None,
        "citation_details": "",
        "citation_text": "",
        "review": False,
        "review_reason": None,
        "type_specific_fields": {},
        "participants": participants,
    }


# ==========================================
# SAME-PERSON MATCHING (within one item's own records)
# ==========================================
def normalize_date(raw: Optional[str]) -> str:
    """Normalizes a date string for grouping/comparison only (not stored) - "1847-04-17"
    and "17 April 1847" need to compare equal even though FamilySearch's two indexing passes
    don't always agree on date format."""
    if not raw:
        return ""
    try:
        return pd.to_datetime(raw, errors="raise").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return raw.strip().lower()


def get_role(record: dict, role_name: str) -> Optional[dict]:
    for p in record.get("participants", []):
        if p.get("role_name") == role_name:
            return p
    return None


def full_name(participant: dict) -> str:
    return f"{participant.get('std_given', '') or ''} {participant.get('std_surname', '') or ''}".strip()


def match_and_link_records(records: List[dict]) -> None:
    """Groups a single item's records by normalized event date, fuzzy-matches candidate
    pairs within each date group (primary name plus role-aligned parent-name comparison -
    father-vs-father, mother-vs-mother, rather than Registrar's flat any-vs-any relative
    check, since these records already carry typed roles), and stamps a shared link_id onto
    matching participants. Matched records are NOT merged - both stay as their own complete
    record (e.g. one Baptism, one Christening); the shared link_id is what lets Archivist's
    generate_uid resolve them to one person with two event facts instead of two people."""
    by_date: Dict[str, List[int]] = {}
    for idx, rec in enumerate(records):
        key = normalize_date(rec.get("event_date"))
        if key:
            by_date.setdefault(key, []).append(idx)

    for idxs in by_date.values():
        if len(idxs) < 2:
            continue
        matched = set()
        for i, idx_a in enumerate(idxs):
            if idx_a in matched:
                continue
            rec_a = records[idx_a]
            primary_a = get_role(rec_a, "Primary")
            if not primary_a or not full_name(primary_a):
                continue

            for idx_b in idxs[i + 1:]:
                if idx_b in matched:
                    continue
                rec_b = records[idx_b]
                primary_b = get_role(rec_b, "Primary")
                if not primary_b or not full_name(primary_b):
                    continue

                if fuzz.token_set_ratio(full_name(primary_a), full_name(primary_b)) < NAME_MATCH_THRESHOLD:
                    continue

                conflict = False
                for role_name in ("Father", "Mother"):
                    pa, pb = get_role(rec_a, role_name), get_role(rec_b, role_name)
                    if pa and pb and full_name(pa) and full_name(pb):
                        if fuzz.token_set_ratio(full_name(pa), full_name(pb)) < PARENT_MATCH_THRESHOLD:
                            conflict = True
                            break
                if conflict:
                    continue

                date_key = normalize_date(rec_a.get("event_date"))
                link_seed = f"{date_key}_{full_name(primary_a).strip().lower()}"
                for role_name in ("Primary", "Father", "Mother", "Spouse"):
                    pa, pb = get_role(rec_a, role_name), get_role(rec_b, role_name)
                    if pa and pb:
                        shared = link_seed if role_name == "Primary" else f"{link_seed}_{role_name}"
                        pa.setdefault("type_specific_fields", {})["link_id"] = shared
                        pb.setdefault("type_specific_fields", {})["link_id"] = shared

                matched.add(idx_a)
                matched.add(idx_b)
                break


# ==========================================
# CITATION & RECORD-FAMILY DETECTION
# ==========================================
def parse_citation(text: str) -> Dict[str, str]:
    """Parses FamilySearch's own generated citation prose - it follows the same two-clause
    convention Ancestry's citations do (online-database repository, then original-data
    publisher/location), just phrased differently: '"<collection>," database with images,
    <repository> (<url> : <date>), <browse path>; <publisher>, <pub_loc>.'"""
    result = {"repository": "", "repository_loc": "", "publisher": "", "pub_loc": "",
              "collection_name": "", "collection_url": "", "browse_path": ""}
    if not text:
        return result

    # Requires a space before the colon, since a lazy \S+? with a bare \s* stops at the first
    # colon it meets - and FamilySearch ark URLs have colons inside the path itself
    # (ark:/61903/3:1:...), truncating the URL long before the real " : <date>" separator.
    url_match = re.search(r'\((https?://\S+)\s+:', text)
    if url_match:
        url = url_match.group(1)
        result["collection_url"] = url
        # Extract cc if present, e.g., ?cc=1803986
        cc_match = re.search(r'[?&]cc=([^&#]+)', url)
        if cc_match:
            result["collection_id"] = cc_match.group(1)

    m = CITATION_RE.match(text.strip())
    if m:
        result["collection_name"] = m.group("collection_name").strip()
        result["repository"] = m.group("repository").strip()
        # publisher/pub_loc are the optional trailing NARA clause - None (not "") when
        # that clause is absent from this citation, per the pattern's own comment.
        result["publisher"] = (m.group("publisher") or "").strip()
        result["pub_loc"] = (m.group("pub_loc") or "").strip()
        result["browse_path"] = m.group("browse_path").strip()
    return result


def detect_record_family(text: str) -> str:
    lowered = text.lower()
    for family, keywords in RECORD_FAMILY_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(k)}", lowered) for k in keywords):
            return family
    return "other"


def dedup_catalog_items(items_raw: List[dict]) -> Dict[str, dict]:
    """The Film/Digital Note catalog table is collection-level metadata (the same physical
    film's alternate cataloged titles), not per-image data, even though it's re-scraped on
    every image. Dedup by item_number so it's only counted once."""
    catalog_items: Dict[str, dict] = {}
    for it in items_raw:
        for ci in it.get("catalog_items", []):
            item_number = ci.get("item_number", "")
            if item_number and item_number not in catalog_items:
                catalog_items[item_number] = ci
    return catalog_items


def detect_record_family_from_raw(raw: dict, catalog_items: Dict[str, dict]) -> str:
    collection_title = raw.get("collection_title", "")
    catalog_text = " ".join(f"{ci.get('label', '')} {ci.get('note', '')}" for ci in catalog_items.values())
    return detect_record_family(f"{collection_title} {catalog_text}")


# ==========================================
# ASSEMBLY
# ==========================================
RECORD_FAMILY_TO_DOCUMENT_TYPE = {"church": "Parish", "scrip": "Scrip"}


def build_universal_json(raw: dict, items_raw: List[dict], catalog_items: Dict[str, dict],
                         record_family: str) -> dict:
    event_types_table = load_event_types()
    roles_table = load_roles()

    first_citation_text = next((it.get("citation_text", "") for it in items_raw if it.get("citation_text")), "")
    citation = parse_citation(first_citation_text)
    collection_title = raw.get("collection_title", "")

    sheets = []
    for it in items_raw:
        item_id = it.get("item_id", "")
        records = [row_to_record(row, item_id, idx) for idx, row in enumerate(it.get("rows", []))]
        records = [r for r in records if r["participants"]]
        match_and_link_records(records)

        for record in records:
            normalization.derive_record_identity(record, event_types_table, set_type_code=True)
            for participant in record["participants"]:
                role_number = normalization.derive_role_number(participant["role_name"], roles_table)
                if role_number is not None:
                    participant["role_number"] = role_number
                    role_semantic = normalization.derive_role_semantic(role_number, roles_table)
                    if role_semantic is not None:
                        participant["role_semantic"] = role_semantic

        sheets.append({
            "page_id": item_id,
            "document_metadata": {
                "file_name": sanitize_item_id_filename(item_id), "file_type": "jpg" if item_id else "",
                "volume": "", "pages": "", "source_name": "", "source_location": "",
            },
            "records": records,
        })

    return {
        "collection_title": collection_title,
        "record_family": record_family,
        "citation": {**citation, "apid_db": "", "catalog_items": list(catalog_items.values())},
        "sheets": sheets,
    }


def parse_census_browse_path(browse_path: str) -> Dict[str, str]:
    """Splits FamilySearch's citation browse-path text (e.g. "Alabama > Autauga >
    Autaugaville > ED 3 > image 1 of 51") into a location hierarchy, mirroring how
    Ancestry's own JS parses its structured browsePath array (getEnumerationDistrict in
    Voyageur.js) - here from plain text instead, since FS's citation prose is all that's
    available. The trailing "image N of M" segment is stripped first."""
    empty = {"state": "", "county": "", "city": "", "enumeration_district": ""}
    if not browse_path:
        return empty

    segments = [s.strip() for s in browse_path.split(">")]
    segments = [s for s in segments if s and not re.match(r'^image\s+\d+\s+of\s+\d+$', s, re.IGNORECASE)]

    ed_index = next((i for i, s in enumerate(segments)
                     if re.search(r'\bED\b|enumeration district', s, re.IGNORECASE)), None)
    enumeration_district = segments[ed_index] if ed_index is not None else ""
    location_segments = [s for i, s in enumerate(segments) if i != ed_index]

    return {
        "state": location_segments[0] if len(location_segments) > 0 else "",
        "county": location_segments[1] if len(location_segments) > 1 else "",
        "city": location_segments[2] if len(location_segments) > 2 else "",
        "enumeration_district": enumeration_district,
    }


NARA_CITING_RE = re.compile(
    r'citing\s+NARA\s+microfilm\s+publication\s+(?P<publication>\S+?)\s*'
    r'\((?P<repo_loc>[^:()]+):\s*(?P<repo_name>[^,()]+),\s*n\.d\.\)',
    re.IGNORECASE
)

CATALOG_ROLL_RE = re.compile(r'NARA Series\s+(?P<series>[A-Za-z0-9]+),\s*Roll\s+(?P<roll>[A-Za-z0-9]+)',
                             re.IGNORECASE)


def parse_nara_citing_clause(citation_text: str) -> Dict[str, str]:
    """A US census citation ends in "...; citing NARA microfilm publication <ID> (<location>:
    <repository>, n.d.)." - a longer, differently-shaped tail than the simple "Publisher,
    Loc." ending church-register citations use. The general parse_citation() regex still
    technically matches this text (its trailing groups are greedy enough to consume it
    without erroring), but captures garbage into publisher/pub_loc rather than the real NARA
    holder - confirmed against a real "United States, Census, 1860" citation this session.
    This targets that clause specifically."""
    m = NARA_CITING_RE.search(citation_text or "")
    if not m:
        return {"publication": "", "repository": "", "repository_loc": ""}
    return {
        "publication": m.group("publication").strip(),
        "repository": m.group("repo_name").strip(),
        "repository_loc": m.group("repo_loc").strip(),
    }


def parse_catalog_roll(catalog_items: Dict[str, dict]) -> Dict[str, str]:
    """Extracts the NARA microfilm series + roll from the Catalog Record table's Film/Digital
    Note (e.g. "Alabama: Autauga, Baldwin, and Barbour Counties (NARA Series M653, Roll 1)"),
    combined below into an "M653_1"-style string matching Ancestry's own Roll citation-field
    convention, so MergedCensus.py's page-locator matching has a real chance of lining the two
    sources up instead of always falling back to ED + page_number alone."""
    for ci in catalog_items.values():
        note = ci.get("note", "") or ci.get("label", "")
        m = CATALOG_ROLL_RE.search(note)
        if m:
            return {"series": m.group("series"), "roll": m.group("roll")}
    return {"series": "", "roll": ""}


def pid_from_identifier(identifier: str) -> str:
    """Deterministic, numbers-only, 10-digit PID derived from an ark identifier - either
    person_ark (preferred, when a true Family Tree attachment is on file) or record_ark.
    GEDCOM software expects a clean numeric xref/REFN,
    not an ark-shaped string with colons and hyphens, and it must stay stable across
    repeated gathers of the same record/person - Python's own hash() is randomized
    per-process (unless PYTHONHASHSEED is pinned) and so is unsuitable; hashlib is not.
    Hashing person_ark when present means the SAME real person attached across multiple
    records/census years collapses to the same pid, usable later to merge them."""
    digest = hashlib.sha256(identifier.encode('utf-8')).hexdigest()
    return f"{int(digest, 16) % 10 ** 10:010d}"


def build_census_json(raw: dict, items_raw: List[dict], catalog_items: Dict[str, dict]) -> dict:
    """Converts FamilySearch's raw census gather into the same {census_year, location,
    pages: [...]} shape Ancestry's own gather already produces (see Voyageur/A.py), instead
    of the church schema.json shape - census data has no sacramental roles, it needs
    dwelling/family numbers and relationship-to-head, which Archivist's existing
    census-flavor pipeline already knows how to handle. Column names are carried through as
    FamilySearch provides them rather than remapped to Ancestry's own naming - reconciling
    the two sources' naming is MergedCensus.py's job, not this per-source conversion step."""
    first_citation_text = next((it.get("citation_text", "") for it in items_raw if it.get("citation_text")), "")
    citation = parse_citation(first_citation_text)
    location_info = parse_census_browse_path(citation.get("browse_path", ""))

    nara_info = parse_nara_citing_clause(first_citation_text)
    roll_info = parse_catalog_roll(catalog_items)
    roll_number = (f"{roll_info['series']}_{roll_info['roll']}" if roll_info['series'] and roll_info['roll']
                   else roll_info['series'] or nara_info['publication'])

    # The general parse_citation() regex captures garbage into publisher/pub_loc for census
    # citations (see parse_nara_citing_clause) - prefer the NARA-specific parse for those two
    # fields when it found a match; repository/repository_loc (the online-host clause, e.g.
    # "FamilySearch") are unaffected, since the general regex parses that clause correctly.
    publisher = nara_info["repository"] or citation.get("publisher", "")
    pub_loc = nara_info["repository_loc"] or citation.get("pub_loc", "")

    collection_title = raw.get("collection_title", "")
    year_match = re.search(r'(1[789]\d0|19[0-4]\d|1950)', collection_title)
    census_year = year_match.group(1) if year_match else ""
    country = "Canada" if "canada" in collection_title.lower() else "USA"

    pages = []
    for page_number, it in enumerate(items_raw, start=1):
        item_id = it.get("item_id", "")
        # Voyageur.js's buildFsItemData() computes this item's own state/county/city/
        # country/enumeration_district directly (from the Image-Index API response or the
        # scraped citation, depending on page type) - prefer that over the single
        # first-citation-text-derived location_info/country below, which only covers the
        # (common but not universal) case where every item in the gather shares one
        # location; falling back to it only when this item's own fields came back empty.
        item_state = it.get("state") or location_info["state"]
        item_county = it.get("county") or location_info["county"]
        item_city = it.get("city") or location_info["city"]
        item_country = it.get("country") or country
        item_ed = it.get("enumeration_district") or location_info["enumeration_district"]
        # Voyageur.js's scrapeFsCollectionInfo() reads the real FamilySearch numeric
        # collection id from the "Information" tab's "Historical Record Collection" link -
        # citation_text's own cc= parse (below) never finds one under this architecture
        # (see parse_citation's own note), so prefer this per-item value, same convention
        # as the location fields above.
        item_collection_id = it.get("collection_id") or citation.get("collection_id", "")
        item_collection_name = it.get("collection_name") or citation.get("collection_name", "")
        people = []
        for row_index, row in enumerate(it.get("rows", [])):
            # FamilySearch's census index never exposes an actual Line Number on any year
            # checked (confirmed live across all 17 US census years this session) - synthesize
            # from row position the same way Ancestry's own JS already does when a collection
            # lacks one, so MergedCensus.py has a locator to match people by.
            columns = dict(row.get("columns", {}))
            if not any(re.search(r'line', k, re.IGNORECASE) for k in columns):
                columns["Line Number"] = str(row_index + 1)

            # Archivist's census-flavor person loop reads 'Given Name'/'Surname' (Ancestry's
            # own column split) - FamilySearch's index only ever exposes a single combined
            # 'Name' field (confirmed live across all rows checked), so left as-is, every
            # FamilySearch-sourced person came out with no name at all (surfacing as literal
            # "nan" once merged into a DataFrame alongside Ancestry rows that do have the
            # split columns). Split it the same way Archivist splits a combined name
            # elsewhere, and drop the raw 'Name' key so it doesn't also leak through as a
            # redundant dynamic note.
            name_val = columns.pop("Name", None)
            if name_val and not columns.get("Given Name") and not columns.get("Surname"):
                given, surname = split_full_name(str(name_val))
                columns["Given Name"] = given
                columns["Surname"] = surname

            # Archivist's census-flavor loop uses 'pid' directly as this person's REFN/@I@ id
            # - it needs to be one clean identifier, not an ark-shaped string with colons and
            # hyphens. 'pid' is a deterministic, numbers-
            # only, 10-digit hash, preferring person_ark (the true Family Tree profile id)
            # when a genuine attachment is on file - the same real person attached across
            # multiple records/census years then collapses to the same pid, usable later to
            # merge them - falling back to record_ark (this row's own real, already-distinct
            # per-person FamilySearch record/persona identifier - always present, already
            # carries its own "1:1:" GEDCOM X type prefix, e.g. "1:1:MF36-Z6D"), and finally
            # to the old compound "{item_id}-{row_index}" form only on the rare row where
            # record_ark itself is missing too, before hashing. It is NOT an Ancestry APID
            # and must never feed Archivist's _APID GEDCOM tag (that's Ancestry-specific
            # citation-linking syntax) - Census.py guards that tag on real Ancestry data now.
            #
            # person_ark is ALSO still preserved as its own separate field below - the raw
            # value, not the hash - blank when no attachment was found, matching the "don't
            # fabricate" convention everywhere else in this file.
            record_ark = row.get("record_ark", "")
            person_ark = row.get("person_ark", "")
            people.append({
                "columns": columns,
                "pid": pid_from_identifier(person_ark or record_ark or f"{item_id}-{row_index + 1}"),
                "record_ark": record_ark,
                "person_ark": person_ark,
                # load_census_dataframe reads this directly (same construction
                # MergedCensus.py uses for a merged person) - without it, an FS-only
                # (non-merged) census run had no FamilySearch web link on its citation
                # at all, even though record_ark was already right here to build one.
                # record_ark already carries its own "1:1:" prefix (see above) - do not
                # prepend a second one here (confirmed live: doing so produced a broken
                # ".../ark:/61903/1:1:1:1:MF36-Z6D" URL).
                "familysearch_url": f"https://www.familysearch.org/ark:/61903/{record_ark}" if record_ark else "",
            })

        pages.append({
            "page_number": page_number,
            "image_id": item_id,
            "country": item_country,
            "state": item_state,
            "county": item_county,
            "city": item_city,
            "place_details": "",
            "enumeration_district": item_ed,
            "film_number": "",
            "roll_number": roll_number,
            "apid_db": "",
            "collection_id": item_collection_id,
            "collection_name": item_collection_name,
            "collection_url": citation.get("collection_url", ""),
            "repository": citation.get("repository", ""),
            "repository_loc": citation.get("repository_loc", ""),
            "publisher": publisher,
            "pub_loc": pub_loc,
            "people": people,
        })

    return {"census_year": census_year, "location": location_info["state"], "pages": pages}


def normalize_familysearch_census_gather(raw_census: dict, collection_title: str) -> dict:
    """Translates a raw FamilySearch census gather (already grouped into Voyageur's own
    {census_year, pages: [...]} shape by build_census_json) into the shared record schema -
    the exact translation main() applies at gather time, pulled out here so it's testable
    without a browser session."""
    return census_schema.normalize_and_validate_census(
        raw_census, "familysearch_census", collection_title, f"Census_{raw_census.get('census_year', '')}")


# ==========================================
# WATCHDOG TIMEOUT
# ==========================================
GATHER_JSON_TIMEOUT_SECONDS = 30 * 60


def wait_for_final_json_event_with_timeout(downloads_dir: Path, json_prefix: str, label: str) -> Path:
    result: list = [None]
    error: list = [None]

    def _watch():
        try:
            result[0] = wait_for_final_json_event(downloads_dir, json_prefix, label)
        except BaseException as exc:
            error[0] = exc
    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    watcher.join(GATHER_JSON_TIMEOUT_SECONDS)
    if watcher.is_alive():
        print(f"[ERROR] Timed out after {GATHER_JSON_TIMEOUT_SECONDS // 60} minutes waiting for {label}.", flush=True)
        sys.exit(1)
    if error[0] is not None:
        raise error[0]
    return result[0]


# ==========================================
# MAIN EXECUTION
# ==========================================
def convert_raw_gather_to_final(raw_data: dict) -> Tuple[dict, Optional[str]]:
    """Converts a raw FamilySearch scrape dict into (final_data, clean_name) - clean_name is
    None when no sheet/record carried enough location info to build one (see
    build_clean_census_filename), in which case the caller falls back to a name derived from
    the raw download's own filename instead. Pulled out of main() so the startup recovery
    sweep can run the exact same conversion on a stale run's raw JSON, rather than
    duplicating this logic."""
    items_raw = raw_data.get("items", [])
    catalog_items = dedup_catalog_items(items_raw)
    record_family = detect_record_family_from_raw(raw_data, catalog_items)

    if record_family == "census":
        raw_census = build_census_json(raw_data, items_raw, catalog_items)
        final_data = normalize_familysearch_census_gather(raw_census, raw_data.get("collection_title", ""))
        from _gather_helpers import build_detailed_census_filename
        clean_name = build_detailed_census_filename(raw_census.get("census_year", ""), final_data, "FamilySearch")
    else:
        final_data = build_universal_json(raw_data, items_raw, catalog_items, record_family)
        document_type = RECORD_FAMILY_TO_DOCUMENT_TYPE.get(record_family)
        if document_type:
            from Commissioner.record_registry import validate_collection_softly
            validate_collection_softly(final_data, document_type, raw_data.get("collection_title", ""))
        clean_name = None

    return final_data, clean_name


def _recover_orphaned_runs(downloads_dir: Path, current_run_id: str, json_target_dir: Path,
                           genealogy_dir: str, on_collision: str) -> None:
    """Recovers gather output left behind by a previous run that had no watcher present to
    collect it. Unlike A.py's recovery, this completes the FULL pipeline (including the
    raw-to-final conversion) since FS's conversion needs nothing beyond the raw JSON itself -
    no external context like A.py's dbid is required."""
    orphans = find_orphaned_gather_runs(downloads_dir, "TMP_FS_", current_run_id)
    if not orphans:
        return

    for run_id, group in orphans.items():
        if group["final"] is None:
            names = ", ".join(p.name for p in group["checkpoints"] + group["images"])
            print(f"[WARN] Found an incomplete stale gather (run {run_id}) with no final JSON - "
                  f"left in place for manual review: {names}")
            continue

        try:
            raw_data = json.loads(_read_text_with_retry(group["final"]))
            final_data, clean_name = convert_raw_gather_to_final(raw_data)
        except Exception as exc:  # noqa: BLE001 - opportunistic recovery of a PAST run must
            # never crash the CURRENT gather, and a corrupt/unparseable raw JSON (confirmed
            # live 2026-08-21: a stray byte spliced into an otherwise-valid download,
            # unrelated to any of this pipeline's own JSON.stringify-based serialization)
            # could come from json.loads, or just as easily from convert_raw_gather_to_final/
            # validate_against_commissioner further down - both are equally "this run's data
            # is unusable", so both are handled the same way here. Left in Downloads
            # unquarantined, this exact file would be picked up and crash again on every
            # future run (find_orphaned_gather_runs re-scans by filename pattern every time,
            # with no memory of a prior failed attempt) - user-directed design: move it into
            # a "Failed" subfolder of the JSON output directory instead, so the raw data is
            # preserved for manual inspection without blocking future runs or lingering in
            # Downloads.
            failed_dir = json_target_dir / "Failed"
            failed_dir.mkdir(parents=True, exist_ok=True)
            corrupt_path = failed_dir / group["final"].name
            try:
                group["final"].replace(corrupt_path)
            except OSError:
                corrupt_path = group["final"]
            print(f"[WARN] Stale gather (run {run_id}) had unreadable raw JSON ({exc}) - "
                  f"moved to {corrupt_path} for manual review; will not be retried "
                  f"automatically.")
            continue

        out_name = clean_name or group["final"].name[len(f"TMP_FS_{run_id}_"):]
        recovered_json = json_target_dir / out_name

        if on_collision == "skip" and recovered_json.exists():
            print(f"[System] Recovered run {run_id}: {out_name} already exists in Project folder, "
                  f"discarding the stale copy (images below are still recovered).")
        else:
            recovered_json.write_text(json.dumps(final_data, indent=2, ensure_ascii=False), encoding="utf-8")
        _unlink_with_retry(group["final"])

        # Extract routing fields from the normalised data directly - no filename parsing.
        census_year, country, location_folder, _ = \
            extract_census_image_routing_fields(final_data)
        img_target_dir = resolve_census_image_dir(
            "Census", genealogy_dir, census_year, country, location_folder)

        img_moved, img_skipped, img_failed = move_downloaded_images(
            downloads_dir, f"TMP_FS_{run_id}_Images_", 0, img_target_dir, on_collision="skip")
        print(f"[System] Recovered stale run {run_id}: wrote {out_name} and moved {img_moved} image(s) "
              f"to Project folders.")


def normalize_familysearch_gather_url(url: str) -> str:
    """Normalizes to FamilySearch's canonical ark-image URL: <scheme>://<host>/ark:/61903/
    <image-id>?view=index&lang=en - forcing the "index" view (Voyageur.js's extraction
    relies on the index panel being open) instead of the "explore" view. URLs copied from
    search results/tree links carry extra tracking/session query params (wc, cc, i,
    groupId, grid, ...) that aren't part of the ark's own identity and can send the viewer
    to the wrong page - discard the entire original query string rather than patching
    around it, keeping only the ark path itself (collection id + image ark)."""
    parsed_url = urlparse(url)
    return urlunparse(parsed_url._replace(query=urlencode({"view": "index", "lang": "en"})))


def main() -> dict:
    print("========================================")
    print(" Voyageur (FS) - FamilySearch Gather Automation")
    print("========================================")

    # Tool .env overrides global .env (root first, tool second, both override=True).
    load_tool_env(Path(__file__).resolve().parent)

    program_dir = os.getenv("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent))
    genealogy_dir = os.getenv("GENEALOGY_DIR", "")
    url = os.getenv("GATHER_URL", "").strip()
    json_dir = os.getenv("JSON_DIR", "JSON")
    on_collision = os.getenv("GATHER_ON_COLLISION", "overwrite").strip().lower()

    if not url:
        print("[ERROR] Please enter a FamilySearch record URL in the Toolbox settings first.")
        sys.exit(1)

    url = normalize_familysearch_gather_url(url)

    # Voyageur.js used GM_download to land these in their own Downloads subfolder, but
    # confirmed live this was unreliable - GM_download's own "downloads" permission grant
    # resets every time the script's content changes in Tampermonkey, and even when
    # granted, it sometimes ignored the requested name/folder and saved a hash-named file
    # in the Downloads root instead. Reverted to the plain <a download> method the
    # original CensusExtractor.js used in production (no special grant to lose), which
    # always honors the exact name given - real filenames are guaranteed. Chrome silently
    # replaces "/" in a download attribute with "_" instead of creating subfolders though,
    # so these land flat in the Downloads root with a "TMP_FS_" filename prefix (set in
    # Voyageur.js) instead of a real subfolder - stripped back off below once found, since
    # it's not part of the actual desired filename.
    downloads_dir = Path.home() / "Downloads"
    json_target_dir = Path(program_dir) / json_dir if program_dir else Path(json_dir)
    json_target_dir.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex[:8]
    json_prefix = f"TMP_FS_{run_id}_"
    image_prefix = f"TMP_FS_{run_id}_Images_"

    # Recover anything a previous run left stranded before this run's own files - which use
    # a fresh, guaranteed-unique run_id - are even downloaded. See the Downloads Handling
    # Redesign spec (docs/superpowers/specs/2026-08-13-voyageur-downloads-handling-design.md).
    _recover_orphaned_runs(downloads_dir, run_id, json_target_dir, genealogy_dir, on_collision)

    start_time = launch_gather_browser(url, run_id)

    raw_json_file = wait_for_final_json_event_with_timeout(downloads_dir, json_prefix, "raw gather JSON")

    raw_data = json.loads(_read_text_with_retry(raw_json_file))
    print_incomplete_pages_warning(raw_data.get("incomplete_pages", []), "item(s)")
    print("\n[System] Converting raw scrape into Gather JSON...")
    final_data, clean_name = convert_raw_gather_to_final(raw_data)

    # The browser side names the raw download after document.title, which for a
    # FamilySearch collection page is often the full search-result title plus its own ark
    # URL - unusable as a real filename. Prefer a clean "{year} - {location}" name built
    # from the same normalized location fields Archivist itself reads, matching Ancestry's
    # own naming convention (A.py's downloadFinalJson uses this exact "{year} - {location}"
    # shape); fall back to the raw browser-provided name only if no location was found at
    # all (e.g. a record with no state/county/city stated anywhere).
    out_name = clean_name or raw_json_file.name[len(json_prefix):]
    final_json = json_target_dir / out_name
    # Unlike A.py, there's no separate downloaded file to move here - final_data was already
    # built in Python, so a "skip" collision just means don't overwrite the existing file
    # with this run's data (the raw scrape is still discarded either way, same as "moved").
    if on_collision == "skip" and final_json.exists():
        json_status = "skipped"
    else:
        final_json.write_text(json.dumps(final_data, indent=2, ensure_ascii=False), encoding="utf-8")
        json_status = "moved"
    _unlink_with_retry(raw_json_file)
    cleanup_checkpoint_files(downloads_dir, json_prefix, start_time)

    # Mirrors A.py's own nested image-folder convention - both sources' images land under
    # the same Media\Census\Country\Year\State\County\City structure.
    # Extract routing fields from the normalised data directly - no filename parsing.
    census_year, country, location_folder, _ = \
        extract_census_image_routing_fields(final_data)

    # Matches Antiquarian.py's own default ("Census", resolved against
    # MEDIA_DIR by the GUI before this ever runs).
    base_img_setting = "Census"
    img_target_dir = resolve_census_image_dir(
        base_img_setting, genealogy_dir, census_year, country, location_folder)

    img_moved, img_skipped, img_failed = move_downloaded_images(
        downloads_dir, image_prefix, start_time, img_target_dir, on_collision=on_collision)
    json_note = "moved" if json_status == "moved" else "kept existing (skipped)"
    print(f"[System] JSON {json_note}; moved {img_moved} image(s)"
          f"{f', skipped {len(img_skipped)}' if img_skipped else ''}"
          f"{f', {len(img_failed)} FAILED' if img_failed else ''} to Project folder.")

    # JSON_FILE is read by Archivist (see its own ARCHIVIST_VARS entry in Antiquarian.py),
    # so it's written to Archivist's own .env, not this script's own subfolder one -
    # confirmed live this was the actual bug behind Archivist immediately failing with
    # FileNotFoundError right after a real gather succeeded: this used to write to
    # Voyageur/.env, a file Archivist never reads at all.
    write_archivist_json_file(final_json.name)

    # Both branches now produce the same sheets[].records[].participants[] shape (the
    # census path is normalized above via census_schema, same as A.py) - one summary
    # line path for both on that basis, where before there were two differently-shaped
    # ones. The two branches still label themselves differently at the top level
    # (record_type_name vs record_family) - reconciling that is separately tracked
    # (Archivist's record-type dispatch rework), not done here.
    total_records = sum(len(sheet["records"]) for sheet in final_data["sheets"])
    label = final_data.get("record_type_name") or final_data.get("record_family", "")
    print(f"[System] Gather complete: {len(final_data['sheets'])} image(s), {total_records} record(s), "
          f"record type '{label}'.")
    print(f"[System] Run Archivist's \"Generate GEDCOM\" when you're ready ({final_json.name}).")

    return final_json


if __name__ == "__main__":
    main()
