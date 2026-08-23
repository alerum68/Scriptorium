"""
Normalizes a raw census gather (Ancestry's or FamilySearch's own {census_year, location,
pages: [{..., people: [{"columns": {...}, ...}]}]} shape - see A.py/FS.py) into the shared
record schema (collection_title/record_type_name/citation/sheets[].records[].
participants[]) that Archivist reads regardless of source.

This is where "Voyageur normalizes at gather time" actually happens: a source's raw
column header text is translated into the shared schema's field names via a declarative
per-source field-map file (Voyageur/field_maps/*.yaml), so Archivist never has to guess
among several possible header spellings downstream. An unmapped header is never dropped
or silently guessed at - it's preserved under type_specific_fields.unmapped and flags the
record for review, so a human can extend the field map instead of data quietly vanishing.

Household grouping (one census dwelling/family = one record, matching a baptism/marriage
entry's own "one record, several participants" shape): people sharing the same normalized
family/dwelling number become one record's participants. Where no such number is present
at all (pre-1850 US census indexes typically carry only a head's name and a page
reference, nothing else), each row is its own single-participant record - there is no
other household member data to group.
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Commissioner.census_consts import get_census_era  # noqa: E402
from Commissioner.normalization import capitalize_text_string  # noqa: E402
from Commissioner.textutils import sanitize_image_filename  # noqa: E402

FIELD_MAPS_DIR = Path(__file__).resolve().parent / "field_maps"

# Mirrors Archivist's get_census_era thresholds exactly (pre1850 <=1840, heuristic
# 1850-1870, relationship >=1880) - duplicated here rather than imported, since Voyageur
# and Archivist are independently standalone-runnable tools (see the design spec) and
# this is a small, stable piece of US census history, not something either tool "owns"
# in a way that would make importing across tool boundaries appropriate.


def load_field_map(name: str) -> Dict[str, Dict[str, str]]:
    """Loads a declarative field-map YAML file by name (e.g. "ancestry_census",
    "familysearch_census") from Voyageur/field_maps/."""
    path = FIELD_MAPS_DIR / f"{name}.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "participant_fields": data.get("participant_fields", {}) or {},
        "participant_facts": data.get("participant_facts", {}) or {},
        "record_fields": data.get("record_fields", {}) or {},
    }


def _parse_year(value: Any) -> int:
    match = re.search(r'(1[789]\d0|19[0-4]\d|1950)', str(value or ""))
    return int(match.group(1)) if match else 0


def _household_key(columns: Dict[str, str], field_map: Dict[str, Dict[str, str]]) -> Optional[str]:
    """Finds this person's normalized family/dwelling number, if their source row has one
    at all - the grouping key for which record (household) they belong to. Falls back to
    a page-number-based grouping (everyone indexed on the same scanned page) when a source
    has no family/dwelling number field at all - confirmed true of FamilySearch's own Image
    Index table (the only per-person view Voyageur.js currently scrapes for FS - it lists
    everyone on one scanned image with no household grouping of its own). A real household
    usually spans less than a full physical page, so this can over-group more than one
    family sharing a page - a real limitation, not a full fix - but it gives Archivist's own
    age/sex/surname household-parsing heuristics (already used for the 1850-1870 "heuristic
    era") something to actually work with, instead of every person arriving as their own
    solitary one-person "household" with nothing to group at all."""
    for raw_key, target in field_map["record_fields"].items():
        if target in ("family_number", "dwelling_number") and raw_key in columns:
            val = str(columns[raw_key]).strip()
            if val:
                return val
    for raw_key, target in field_map["record_fields"].items():
        if target == "page_number_fallback" and raw_key in columns:
            val = str(columns[raw_key]).strip()
            if val:
                return f"page_{val}"
    return None


def _group_household(people: List[dict], field_map: Dict[str, Dict[str, str]]
                     ) -> List[Tuple[Optional[str], List[dict]]]:
    """Groups people sharing the same household_id (a real, stable identifier some
    sources supply directly - e.g. Ancestry's index-panel-data API, see
    docs/superpowers/specs/2026-08-15-ancestry-index-panel-extraction-design.md) when
    present, or the same family/dwelling number (or, failing that, the same page-number
    fallback - see _household_key) otherwise. A person with neither at all becomes their
    own single-person group - there's genuinely nothing to group them with."""
    groups: Dict[Optional[str], List[dict]] = {}
    order: List[Optional[str]] = []
    fallback_counter = 0
    for person in people:
        columns = person.get("columns", {}) or {}
        key = person.get("household_id") or _household_key(columns, field_map)
        if key is None:
            fallback_counter += 1
            key = f"__ungrouped_{fallback_counter}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(person)
    return [(None if k.startswith("__ungrouped_") else k, groups[k]) for k in order]


def _normalize_participant(person: dict, field_map: Dict[str, Dict[str, str]],
                           unmapped_seen: Set[str]) -> dict:
    columns = dict(person.get("columns", {}) or {})
    participant: Dict[str, Any] = {
        "role_number": None, "role_name": None,
        "std_given": "", "std_surname": None, "raw_given": None, "raw_surname": None,
        "dit_name": None, "alternate_names": person.get("alternate_names", []),
        "prefix": None, "suffix": None, "sex": "U", "is_priest": False,
        "age": None, "age_unit": None, "occupation": None, "race": None, "religion": None,
        "residence": None, "birth_date": None, "birth_place": None, "death_date": None,
        "death_place": None, "review": False, "review_reason": None,
        "facts": [], "type_specific_fields": {},
    }

    consumed: Set[str] = set()
    for raw_key, target in field_map["participant_fields"].items():
        if raw_key in columns:
            val = columns[raw_key]
            if (
                target in ("role_name", "race", "birth_place", "death_place", "residence", "occupation", "religion")
                and isinstance(val, str)
            ):
                val = capitalize_text_string(val)
            # Commissioner's Participant.sex is Literal["M", "F", "U"] - FamilySearch's raw
            # SEX_CODE value is the full word ("Male"/"Female", confirmed live 2026-08-20),
            # which fails that validation unconverted. This is a value-format fix required
            # for schema compliance, not the field-renaming/curation the raw-passthrough
            # design forbids at extraction time - extraction still passes the raw word
            # through untouched; this conversion happens only here, at GEDCOM-mapping time.
            if target == "sex" and isinstance(val, str):
                val = {"MALE": "M", "M": "M", "FEMALE": "F", "F": "F"}.get(val.strip().upper(), "U")
            if target.startswith("type_specific_fields."):
                participant["type_specific_fields"][target.split(".", 1)[1]] = val
            else:
                participant[target] = val
            consumed.add(raw_key)

    for raw_key, fact_type in field_map["participant_facts"].items():
        if raw_key in columns and str(columns[raw_key]).strip():
            raw_v = str(columns[raw_key]).strip()
            val = (
                capitalize_text_string(raw_v)
                if fact_type in ("Occupation", "Education", "Military", "Property", "Miscellaneous")
                else raw_v
            )
            participant["facts"].append({"fact_type": fact_type, "value": val})
            consumed.add(raw_key)

    # record_fields (family/dwelling number) are handled at the household-grouping level
    # (_household_key), not here - but they're still a recognized, mapped column, so they
    # must not also show up as "unmapped" on every participant in the household.
    for raw_key in field_map["record_fields"]:
        if raw_key in columns:
            consumed.add(raw_key)

    # Whatever this person's own identifiers already carry (pid/fsftid/etc.) is preserved
    # as-is, not treated as an unmapped column - it never came from `columns` in the
    # first place. record_ark (this persona's record-scoped id, always present) and
    # person_ark (a true enduring Family Tree identifier, only when one was actually
    # found - see docs/plans/2026-08-20-familysearch-viewer-rebuild.md Task 4) are
    # genuinely distinct fields as of 2026-08-20 - both pass through independently.
    for passthrough_key in ("pid", "extracted_url", "fsftid", "record_ark", "person_ark",
                            "familysearch_url", "alternate_birth_places"):
        if person.get(passthrough_key):
            participant["type_specific_fields"][passthrough_key] = person[passthrough_key]

    # 2026-08-21: an unmapped column is no longer treated as something needing human
    # review - with raw-passthrough extraction (Voyageur.js fsRawFieldsFromApiPerson/
    # fsRawFieldsFromImageIndexPerson), most people legitimately have several unmapped
    # fields (metadata, office-use codes, etc. - see field_maps/familysearch_census.yaml's
    # own comments on what's deliberately not mapped) as the normal, expected case, not an
    # anomaly. The data is still fully preserved here, just without flagging every single
    # participant (and, via record_review below, every record) for review over routine,
    # expected gaps.
    unmapped = {k: v for k, v in columns.items() if k not in consumed and str(v).strip()}
    if unmapped:
        participant["type_specific_fields"]["unmapped"] = unmapped
        unmapped_seen.update(unmapped.keys())

    if not participant["std_given"]:
        participant["std_given"] = "[unknown]"
        participant["review"] = True
        participant["review_reason"] = (participant["review_reason"] or "") + " No given-name column mapped."

    return participant


def normalize_census_pages(raw: dict, field_map_name: str, collection_title: str,
                           record_type_name: str) -> dict:
    """Converts a raw {census_year, location, pages: [...]} gather into the shared
    sheets[].records[].participants[] schema, using the named declarative field map."""
    field_map = load_field_map(field_map_name)
    census_year = _parse_year(raw.get("census_year"))
    era = get_census_era(census_year)

    sheets = []
    citation: Dict[str, str] = {}
    for page in raw.get("pages", []):
        people = page.get("people", []) or []
        groups = _group_household(people, field_map)
        records = []
        for household_key, group_people in groups:
            unmapped_seen: Set[str] = set()
            participants = [_normalize_participant(p, field_map, unmapped_seen) for p in group_people]
            place = ", ".join(filter(None, [page.get("city"), page.get("county"), page.get("state")]))
            record_review = any(p["review"] for p in participants)
            type_specific: Dict[str, Any] = {}
            if household_key:
                type_specific["family_number"] = household_key
            # Preserved for cross-source page matching (MergedCensus.py) and citation
            # assembly (Archivist) - these are page-level locators in the raw gather, not
            # a per-person column, so they don't go through the participant field map.
            if page.get("enumeration_district"):
                type_specific["enumeration_district"] = page["enumeration_district"]
            if page.get("roll_number"):
                type_specific["roll_number"] = page["roll_number"]
            if page.get("film_number"):
                type_specific["film_number"] = page["film_number"]
            for loc_key in ("state", "county", "city", "country", "apid_db", "place_details"):
                if page.get(loc_key):
                    type_specific[loc_key] = page[loc_key]
            if era == "pre1850" and len(participants) == 1:
                # Nothing beyond the head is present in a pre-1850 index row - no tally
                # data to attach today (Ancestry's own index for these years doesn't
                # expose it), but the convention has a home (household_tally) if a
                # richer source ever supplies it - see the design spec.
                pass
            records.append({
                "record_id": None, "page": str(page.get("page_number", "")),
                "record_number": household_key or "", "event_type": "Census (family)",
                "year": str(census_year) if census_year else "", "event_date": "",
                "event_place": place, "citation_details": "", "citation_text": "",
                "review": record_review,
                "review_reason": "Missing expected given name or other required data." if record_review else None,
                "continues_on_next_image": False, "continues_from_previous_image": False,
                "type_specific_fields": type_specific,
                "participants": participants,
            })

        file_name = sanitize_image_filename(page.get("image_id", ""))
        sheets.append({
            "page_id": str(page.get("page_number", "")),
            "document_metadata": {
                "file_name": file_name, "file_type": "jpg" if file_name else "", "volume": "",
                "pages": str(page.get("page_number", "")),
                "source_name": page.get("repository", ""),
                "source_location": ", ".join(filter(None, [page.get("state"), page.get("country")])),
            },
            "records": records,
        })

        if not citation:
            citation = {
                "call_number": "", "collection_url": page.get("collection_url", ""),
                "collection_name": page.get("collection_name", "") or collection_title,
                "repository": page.get("repository", ""), "repository_loc": page.get("repository_loc", ""),
                "publisher": page.get("publisher", ""), "pub_loc": page.get("pub_loc", ""),
                "apid_db": page.get("apid_db", ""), "collection_id": page.get("collection_id", ""),
            }

    return {
        "collection_title": collection_title,
        "record_type_name": record_type_name,
        "citation": citation,
        "sheets": sheets,
    }


def normalize_and_validate_census(raw: dict, field_map_name: str, collection_title: str,
                                  record_type_name: str) -> dict:
    """Normalizes then validates in one call - the exact pair both A.py and FS.py apply at
    census gather time. Pulled out as its own function so each call site is one line and
    testable without duplicating the normalize+validate pairing."""
    normalized = normalize_census_pages(raw, field_map_name, collection_title, record_type_name)
    from Commissioner.record_registry import validate_collection_softly
    validate_collection_softly(normalized, "Census", collection_title)
    return normalized
