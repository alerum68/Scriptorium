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

__all__ = [
    "clean_text",
    "to_snake_case",
    "normalize_whitespace",
    "sanitize_quotes",
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
