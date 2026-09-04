"""
Commissioner/textutils.py -- Shared text-normalization primitives.

Single home for the small string-hygiene helpers previously duplicated across
the tool folders (Phase 3 dedup, Slice 1):

    DUP-1  clean_text            (was Utils.py / PDFix.py / census_schema.py)
    DUP-2  to_snake_case         (was census_schema.py / models.py)
    DUP-3  normalize_whitespace  (was Extract.py / census_schema.py)
    DUP-4  sanitize_quotes       (was Utils.py / Gazetteer.py)

Stdlib-only by design, so every standalone-runnable tool can import it safely.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "clean_text",
    "to_snake_case",
    "normalize_whitespace",
    "sanitize_quotes",
    "sanitize_image_filename",
    "pad_string_digits",
    "dynamic_zero_pad_all_except",
]

# Zero-width / invisible characters that survive JSON round-trips and quietly
# break equality checks, diffing, and geocoding lookups downstream.
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

# Curly quotes and apostrophes mapped to their ASCII equivalents.
_SMART_QUOTES = {
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark / apostrophe
    "\u201a": "'",  # single low-9 quotation mark
    "\u201b": "'",  # single high-reversed-9 quotation mark
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u201e": '"',  # double low-9 quotation mark
    "\u201f": '"',  # double high-reversed-9 quotation mark
}

# CamelCase boundaries: lower/digit -> upper (fooBar -> foo_Bar) and an
# uppercase acronym run -> following capitalized word (HTTPServer -> HTTP_Server).
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def sanitize_quotes(text: str) -> str:
    """Replace Unicode smart quotes and apostrophes with ASCII equivalents."""
    if not text:
        return ""
    for smart, plain in _SMART_QUOTES.items():
        text = text.replace(smart, plain)
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse every whitespace run (spaces, tabs, newlines, NBSP) to one space."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def clean_text(value: object) -> str:
    """
    Coerce any scalar to display-safe text: None becomes "", non-breaking and
    zero-width characters are folded away, whitespace is normalized.
    """
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = _INVISIBLE_CHARS_RE.sub("", text)
    return normalize_whitespace(text)


def to_snake_case(name: str) -> str:
    """Convert CamelCase / PascalCase / dashed identifiers to snake_case."""
    if not name:
        return ""
    text = _CAMEL_BOUNDARY_RE.sub("_", str(name).strip())
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.lower().strip("_")


def sanitize_image_filename(image_id: str) -> str:
    """Sanitize an image identifier or item ID to a safe .jpg filename.

    Replaces any character outside [a-zA-Z0-9_-] with an underscore and appends .jpg.
    Returns empty string if image_id is empty/falsy.
    """
    if not image_id:
        return ""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(image_id).strip()) + ".jpg"


# Fields a blanket zero-padding sweep must never touch. Grouped by why padding
# would break them, not just what they're named -- see dynamic_zero_pad_all_except.
_PAD_EXCLUDED_FIELDS = {
    # File/record linkers: padding breaks exact-match lookups against files on
    # disk, dedup sets, and DB joins (e.g. "42.jpg" no longer matches "042.jpg").
    "image_id", "item_id", "record_id", "page_id", "dbId", "collection_id",
    "apid_db", "file_name",
    # Web links: padding a path segment produces a dead URL.
    "url", "collection_url", "repository_url", "fs_url", "ancestry_url", "image_url",
    # Dates & years: "May 12, 1880" must not become "May 012, 1880".
    "year", "census_year", "birth_year", "death_year", "event_date",
    "application_date", "issue_date", "delivery_date", "date",
    # Amounts & personal attributes: padding is semantically wrong in narrative
    # display ("$0160", age "009").
    "scrip_amount", "age", "calculated_age", "estimated_age", "birth_age",
    # Names & relationships: numbered suffixes ("John Doe 3rd") aren't IDs.
    "given_names", "surname", "std_given", "std_surname", "father_name", "mother_name",
    "spouse_name", "name", "primary_name", "role", "relationship_to_head",
    # Free text / locations: unstructured prose, addresses, place names.
    "note", "notes", "summary", "package_summary", "transcription", "remarks",
    "description", "occupation", "birth_place", "death_place", "residence",
    "repository_loc", "pub_loc", "city", "county", "state", "country", "repository",
    "publisher", "collection_name", "source_name", "source_location",
    # Archival citation / routing fields: these are matched by exact substring
    # against alphanumeric codes (Archivist/Scrip.py's select_scrip_template_id
    # matches series_code against literals like "d-ii-8-a" and commission_reference
    # by keyword) or rendered into citation text by convention unpadded (folio
    # "12v", "Vol. 3"). enumeration_district also feeds the on-disk image
    # directory path (Archivist/Census.py's location_parts) -- same file-linker
    # risk as image_id/item_id above.
    "enumeration_district", "rg_series_code", "commission_reference",
    "folio", "volume", "reel_numbers",
}


def pad_string_digits(val: Any, width: int) -> str:
    """Zero-pads every digit run in a string to width (e.g. "5" -> "005")."""
    if not val:
        return ""
    return re.sub(r"\d+", lambda m: m.group(0).zfill(width), str(val))


def dynamic_zero_pad_all_except(data: dict) -> None:
    """
    Zero-pads numeric-bearing fields to the max digit width seen for that field
    name anywhere in `data`, mutating it in place. Every field is padded except
    those in _PAD_EXCLUDED_FIELDS (an exclusion list, not an include list, so new
    ID/reference fields get padded automatically without code changes here).

    `data` should be the full payload being persisted in one write (a whole
    gather file for Voyageur's per-run gatherers, or the whole accumulated
    master DB dict for Voyageur's checkpoint-based gatherers) -- not a single
    page or record -- so that widths are consistent across everything that
    write touches.
    """
    max_lengths: dict[str, int] = {}

    def find_max(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    find_max(v)
                elif k not in _PAD_EXCLUDED_FIELDS and v:
                    for d in re.findall(r"\d+", str(v)):
                        if len(d) > max_lengths.get(k, 0):
                            max_lengths[k] = len(d)
        elif isinstance(obj, list):
            for item in obj:
                find_max(item)

    def apply_pad(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    apply_pad(v)
                elif k not in _PAD_EXCLUDED_FIELDS and k in max_lengths:
                    obj[k] = pad_string_digits(v, max_lengths[k])
        elif isinstance(obj, list):
            for item in obj:
                apply_pad(item)

    find_max(data)
    apply_pad(data)
