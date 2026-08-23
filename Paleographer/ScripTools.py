"""
ScripTools: Scrip-specific document enrichment for Paleographer.

Enriches, cross-checks against LAC search, partitions by collection, and resolves
maiden/dit names for Scrip claim records extracted by Extract.py. This module is
intentionally Scrip-only — it is not a generalization target for other record types.

Antiquarian.py launches Paleographer.py (the dispatcher) as a subprocess with
cwd=Paleographer/, so this module imports as a plain sibling.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# noinspection DuplicatedCode
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from Voyageur import lac_client
    # noinspection PyPep8Naming
    from Voyageur import LAC as voyageur_lac
except (ImportError, ValueError):
    # noinspection PyUnresolvedReferences
    import lac_client
    # noinspection PyUnresolvedReferences,PyPep8Naming
    import LAC as voyageur_lac

from Commissioner.envkit import load_tool_env  # noqa: E402

load_tool_env(Path(__file__).resolve().parent)


# ==============================================================================
# POSTPROCESS
# ==============================================================================
_MOJIBAKE_MAP = {
    'ã©': 'é', 'ã¨': 'è', 'ãª': 'ê', 'ã«': 'ë', 'ã ': 'à', 'ã¢': 'â',
    'ã®': 'î', 'ã¯': 'ï', 'ã´': 'ô', 'ã¹': 'ù', 'ã»': 'û', 'ã§': 'ç',
    'ã‰': 'É', 'ãˆ': 'È', 'ãŠ': 'Ê', 'ã‹': 'Ë', 'ã€': 'À', 'ã‚': 'Â',
    'ãŽ': 'Î', 'ã”': 'Ô', 'ã™': 'Ù', 'ã›': 'Û', 'ã‡': 'Ç',
    'â€™': "'", 'â€˜': "'", 'â€œ': '"', 'â€\x9d': '"', 'â€"': '—',
    'â€“': '–', 'ãfb': 'ï', 'ã\xad': 'í', 'ã\x89': 'É',
    'ã\x88': 'È', 'ã\x8a': 'ê', 'ã\x8b': 'ë', 'ã\x80': 'À', 'ã\x82': 'Â',
    'Ã©': 'é', 'Ã¨': 'è', 'Ãª': 'ê', 'Ã«': 'ë', 'Ã ': 'à', 'Ã¢': 'â',
    'Ã®': 'î', 'Ã¯': 'ï', 'Ã´': 'ô', 'Ã¹': 'ù', 'Ã»': 'û', 'Ã§': 'ç',
    'Ã‰': 'É', 'Ãˆ': 'È', 'ÃŠ': 'Ê', 'Ã‹': 'Ë', 'Ã€': 'À', 'Ã‚': 'Â',
    'ÃŽ': 'Î', 'Ã”': 'Ô', 'Ã™': 'Ù', 'Ã›': 'Û', 'Ã‡': 'Ç',
    '’': "'", '‘': "'", '“': '"', '”': '"', '—': '—', '–': '–',
}


def fix_mojibake(text: str) -> str:
    """Repairs UTF-8 strings that were decoded as CP1252 / ISO-8859-1 (mojibake)."""
    if not text:
        return ""
    fixed = re.sub(
        r"ã([\x80-\xbf\u00a0-\u00bf\u0080-\u009f\u2018-\u201d\u2022\u20ac])",
        lambda m: 'Ã' + m.group(1),
        str(text)
    )
    if any(ch in fixed for ch in ("Ã", "Â", "â", "ð")):
        try:
            fixed = fixed.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                fixed = fixed.encode("latin1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    for bad, good in _MOJIBAKE_MAP.items():
        if bad in fixed:
            fixed = fixed.replace(bad, good)
    return fixed


COMPOUND_SURNAME_PREFIXES_2 = {
    "de la", "de le", "de les", "de los", "de las",
    "van der", "van den", "van de", "von der", "von den",
}

COMPOUND_SURNAME_PREFIXES_1 = {
    "st.", "st", "ste.", "ste", "saint", "sainte", "san", "santa",
    "de", "du", "des", "del", "della", "degli",
    "la", "le", "les", "l'", "d'",
    "van", "von", "der", "den", "ter", "ten",
    "fitz", "mac", "mc", "o'"
}


def clean_dit_name(dit_str: str) -> str:
    if not dit_str:
        return ""
    cleaned = re.sub(r'^(?:dit|dite|alias)\s+', '', (dit_str or "").strip(), flags=re.I).strip()
    return " ".join(w.capitalize() for w in cleaned.split())


def parse_single_name(raw: str, expected_surname: str = "") -> Tuple[str, str, str]:
    """Parses a raw 'FIRSTNAME LASTNAME' string into (std_given, std_surname, dit_name)."""
    text = (raw or "").strip()
    if not text:
        return "", "", ""

    dit_name = ""
    m_dit = re.search(r'\s+\b(dit|dite)\b\s+(.+)$', text, flags=re.I)
    if m_dit:
        dit_name = clean_dit_name(m_dit.group(2))
        text = text[:m_dit.start()].strip()

    if expected_surname:
        exp_clean = expected_surname.strip()
        exp_base = re.split(r'\s+\b(dit|dite)\b\s+', exp_clean, flags=re.I)[0].strip()
        if exp_base and text.lower().endswith(exp_base.lower()):
            prefix = text[:-len(exp_base)].strip()
            if prefix:
                given = " ".join(w.capitalize() for w in prefix.split())
                surname = " ".join(w.capitalize() for w in exp_base.split())
                if dit_name:
                    surname = f"{surname} dit {dit_name}"
                return given, surname, dit_name

    parts = text.split()
    if not parts:
        return "", "", dit_name
    if len(parts) == 1:
        return parts[0].capitalize(), "", dit_name

    surname_idx = len(parts) - 1
    if len(parts) >= 3:
        two_word = f"{parts[-3]} {parts[-2]}".lower()
        if two_word in COMPOUND_SURNAME_PREFIXES_2:
            surname_idx = len(parts) - 3
        elif (parts[-2].lower().rstrip('.') in COMPOUND_SURNAME_PREFIXES_1
              or parts[-2].lower() in COMPOUND_SURNAME_PREFIXES_1):
            surname_idx = len(parts) - 2
    elif len(parts) == 2:
        if parts[0].lower().rstrip('.') in COMPOUND_SURNAME_PREFIXES_1:
            return "", " ".join(w.capitalize() for w in text.split()), dit_name

    given_parts = parts[:surname_idx]
    surname_parts = parts[surname_idx:]

    given = " ".join(w.capitalize() for w in given_parts)
    s_formatted = []
    for p in surname_parts:
        low = p.lower()
        if low in ("st", "st."):
            s_formatted.append("St.")
        elif low in ("ste", "ste."):
            s_formatted.append("Ste.")
        else:
            s_formatted.append(p.capitalize())
    surname = " ".join(s_formatted)

    if dit_name and not re.search(r'\s+\bdit\b\s+', surname, flags=re.I):
        surname = f"{surname} dit {dit_name}"

    return given, surname, dit_name


def fix_participant_name(p: Dict[str, Any], expected_surname: str = "") -> bool:
    """Repairs participant's std_given and std_surname, moving compound prefixes properly."""
    given = (p.get("std_given") or "").strip()
    surname = (p.get("std_surname") or "").strip()
    dit_name = (p.get("dit_name") or "").strip()

    if not given and not surname:
        return False

    combined = f"{given} {surname}".strip()
    new_given, new_surname, new_dit = parse_single_name(combined, expected_surname=expected_surname)

    if dit_name and not new_dit:
        new_dit = dit_name
        if not re.search(r'\s+\bdit\b\s+', new_surname, flags=re.I):
            new_surname = f"{new_surname} dit {new_dit}".strip()

    modified = False
    if new_given != given:
        p["std_given"] = new_given
        modified = True
    if new_surname != surname:
        p["std_surname"] = new_surname
        modified = True
    if new_dit and p.get("dit_name") != new_dit:
        p["dit_name"] = new_dit
        modified = True

    return modified


def fix_all_participant_names_in_record(record: Dict[str, Any]) -> bool:
    """Repairs participant names across the entire record."""
    participants = record.get("participants", [])
    if not participants:
        return False

    primary = participants[0]
    primary_surname = (primary.get("std_surname") or "").strip()

    modified = False
    if fix_participant_name(primary):
        modified = True
        primary_surname = (primary.get("std_surname") or "").strip()

    for p in participants[1:]:
        role = p.get("role_semantic") or str(p.get("role_number"))
        exp = primary_surname if role in ("father", "6") else ""
        if fix_participant_name(p, expected_surname=exp):
            modified = True

    return modified


def build_composite_record_number(tf: Dict[str, Any], _pid: str = "") -> str:
    """Builds standard composite key: [Claim Number]-[Allotment Number]-[Scrip Number]."""
    claim = (tf.get("claim_number") or "").strip() or "0"
    allotment = (tf.get("allotment_number") or "").strip() or "0"
    scrip = (tf.get("scrip_number") or "").strip() or "0"
    return f"{claim}-{allotment}-{scrip}"


def resolve_maiden_name_for_record(record: Dict[str, Any], row_nee: str = "") -> bool:
    """Resolves maiden surname if married surname is currently in std_surname."""
    participants = record.get("participants", [])
    if not participants:
        return False

    primary = participants[0]
    father = next((p for p in participants
                   if p.get("role_semantic") == "father" or str(p.get("role_number")) == "6"), None)
    spouse = next((p for p in participants
                   if p.get("role_semantic") == "spouse" or str(p.get("role_number")) == "2"), None)

    f_surname = (father.get("std_surname") or "").strip() if isinstance(father, dict) else ""
    c_surname = (primary.get("std_surname") or "").strip()
    c_given = (primary.get("std_given") or "").strip()
    nee = (row_nee or "").strip().title()

    maiden_surname = ""
    if f_surname and f_surname.lower() not in ("[illegible]", "unknown", ""):
        maiden_surname = f_surname
    elif nee and nee.lower() not in ("[illegible]", "unknown", ""):
        maiden_surname = nee

    if not maiden_surname:
        return False

    title_lower = (record.get("lac_catalog_title") or record.get("lac_catalog_title_live") or "").lower()
    is_female = (
        primary.get("sex") == "F"
        or (isinstance(spouse, dict) and spouse.get("sex") == "M")
        or ("wife of" in title_lower)
        or ("widow of" in title_lower)
        or ("husband:" in title_lower)
    )

    modified = False
    if is_female:
        if primary.get("sex") != "F":
            primary["sex"] = "F"
            modified = True
        if isinstance(spouse, dict) and not spouse.get("sex"):
            spouse["sex"] = "M"
            modified = True

        if c_surname and c_surname.lower() != maiden_surname.lower():
            alt_names = primary.setdefault("alternate_names", [])
            married_full = f"{c_given} {c_surname}".strip()
            if married_full and not any(
                (a.get("value") or "").strip().lower() == married_full.lower() for a in alt_names
            ):
                alt_names.append({"value": married_full})
            primary["std_surname"] = maiden_surname
            modified = True
        elif not c_surname:
            primary["std_surname"] = maiden_surname
            modified = True

    return modified


def resolve_dataset_maiden_names(data: Dict[str, Any]) -> int:
    """Resolves married/maiden surnames across an entire dataset."""
    modified_count = 0
    for sheet in data.get("sheets", []):
        for record in sheet.get("records", []):
            if record.get("lac_catalog_title"):
                record["lac_catalog_title"] = fix_mojibake(record["lac_catalog_title"])
            if record.get("lac_catalog_title_live"):
                record["lac_catalog_title_live"] = fix_mojibake(record["lac_catalog_title_live"])

            for p in record.get("participants", []):
                for k in ("std_given", "std_surname", "race", "birth_place", "death_place"):
                    if p.get(k):
                        p[k] = fix_mojibake(p[k])

            fix_all_participant_names_in_record(record)
            if resolve_maiden_name_for_record(record):
                modified_count += 1

    return modified_count


CITATION_PATTERNS = {
    "claim_number": re.compile(r"claim no\.?:?\s*([^;]+)", re.I),
    "affidavit_number": re.compile(r"aff(?:idavit|dt)?\.?\s*no\.?:?\s*([^;]+)", re.I),
    "allotment_number": re.compile(r"allotment\s*no\.?:?\s*([^;]+)", re.I),
    "scrip_number": re.compile(r"scrip no\.?:?\s*([^;]+)", re.I),
    "grant_number": re.compile(r"grant no\.?:?\s*([^;]+)", re.I),
    "patent_number": re.compile(r"patent no\.?:?\s*([^;]+)", re.I),
    "case_number": re.compile(r"case no\.?:?\s*([^;]+)", re.I),
    "scrip_amount": re.compile(r"amount:?\s*([^;]+)", re.I),
    "scrip_issue_date": re.compile(r"date of issue:?\s*([^;]+)", re.I),
    "issue_date": re.compile(r"date of issue:?\s*([^;]+)", re.I),
    "application_date": re.compile(r"date of application:?\s*([^;]+)", re.I),
}


def extract_citation_fields(citation: str) -> Dict[str, str]:
    """Extracts structured scrip fields from citation string."""
    fields = {}
    if not citation:
        return fields
    citation = fix_mojibake(citation)
    for key, pattern in CITATION_PATTERNS.items():
        m = pattern.search(citation)
        if m:
            val = m.group(1).strip().rstrip(".")
            if val:
                fields[key] = val
    if "scrip_issue_date" in fields and "issue_date" not in fields:
        fields["issue_date"] = fields["scrip_issue_date"]
    elif "issue_date" in fields and "scrip_issue_date" not in fields:
        fields["scrip_issue_date"] = fields["issue_date"]
    return fields


# ==============================================================================
# SCRIP ENRICHMENT & PARTITIONING (folded from Commissioner / DEV)
# ==============================================================================

def resolve_pid_from_filename(file_name: str) -> Optional[str]:
    """Returns the PID embedded in a locally-chosen filename, or None."""
    if not file_name:
        return None
    match = re.search(r"_(\d+)\.pdf$", file_name, re.IGNORECASE)
    return match.group(1) if match else None


def resolve_pid_for_sheet_or_record(sheet: Dict[str, Any], record: Dict[str, Any]) -> Optional[str]:
    """Resolves the PID for a record from record.lac_pid, document_metadata.pid, or file_name."""
    if record.get("lac_pid"):
        return str(record["lac_pid"]).strip()
    meta = sheet.get("document_metadata") or {}
    if meta.get("pid"):
        return str(meta["pid"]).strip()
    file_name = meta.get("file_name") or (record.get("document_metadata") or {}).get("file_name", "")
    return resolve_pid_from_filename(file_name)


UNKNOWN_COLLECTION_LABEL = "Unclassified (no rg_series_code or inferable volume yet)"


def classify_sheet_collection(sheet: Dict[str, Any]) -> Tuple[Optional[str], str, Optional[str], str]:
    """Determines collection for a sheet."""
    records = sheet.get("records", [])
    if records:
        record = records[0]
        series_code = (record.get("type_specific_fields") or {}).get("rg_series_code")
        res = voyageur_lac.collection_for_series_code(series_code)
        if res:
            return res

    meta = sheet.get("document_metadata", {})
    res = voyageur_lac.collection_for_volume(meta.get("volume"), meta.get("volume_range"))
    if res:
        return res

    return None, UNKNOWN_COLLECTION_LABEL, None, "unclassified"


def partition_json_by_collection(data: Dict[str, Any], output_dir: Path) -> Dict[str, Path]:
    """Partitions a master Scrip dataset into separate JSON files grouped by official LAC collection."""
    output_dir.mkdir(parents=True, exist_ok=True)
    buckets: Dict[str, Dict[str, Any]] = {}
    for sheet in data.get("sheets", []):
        code, title, finding_aid, _status = classify_sheet_collection(sheet)
        key = code or "unclassified"
        if key not in buckets:
            buckets[key] = {
                "series_code": code,
                "title": title,
                "finding_aid": finding_aid,
                "sheets": [],
            }
        buckets[key]["sheets"].append(sheet)

    written_files = {}
    for key, info in buckets.items():
        clean_title = re.sub(r'[\s,:-]+', '_', info['title'].strip())
        filename = f"{key}_{clean_title}.json" if key != "unclassified" else "unclassified.json"
        out_file = output_dir / filename
        dataset = {
            "record_type_name": data.get("record_type_name", "Scrip"),
            "collection_title": info["title"],
            "finding_aid": info["finding_aid"],
            "rg_series_code": info["series_code"],
            "sheets": info["sheets"],
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(dataset, f, indent=2, ensure_ascii=False)
        written_files[key] = out_file

    return written_files


_SCRIP_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:to|-|–)\s*(\d+)\s*$", re.IGNORECASE)


def expand_scrip_number_range(scrip_number: Optional[str]) -> List[str]:
    """Expands a range like '2234 to 2241' into individual numbers."""
    if not scrip_number:
        return []
    match = _SCRIP_RANGE_RE.match(scrip_number.strip())
    if not match:
        return [scrip_number]
    low, high = int(match.group(1)), int(match.group(2))
    if high < low or high - low > 200:
        return [scrip_number]
    return [str(n) for n in range(low, high + 1)]


def build_claim_search_queries(record: Dict[str, Any]) -> List[str]:
    """Builds search queries to find a claim's related documents from LAC."""
    fields = record.get("type_specific_fields", {})
    claim_number = fields.get("claim_number")
    affidavit_number = fields.get("affidavit_number")
    scrip_numbers = expand_scrip_number_range(fields.get("scrip_number"))

    primary = claim_number or affidavit_number
    if primary and scrip_numbers:
        return [f"claim: {primary} Scrip: {n}" for n in scrip_numbers]
    if primary:
        return [f"claim: {primary}"]
    if scrip_numbers:
        return [f"Scrip: {n}" for n in scrip_numbers]

    reasons = []  # noqa: F841
    file_name = (record.get("document_metadata") or {}).get("file_name", "")
    e_number_match = re.search(r"(e\d{6,})", file_name, re.IGNORECASE)
    if e_number_match:
        return [e_number_match.group(1)]

    return []


def build_claim_search_query(record: Dict[str, Any]) -> Optional[str]:
    queries = build_claim_search_queries(record)
    return queries[0] if queries else None


def cross_check_claim_record(record: Dict[str, Any], cookies: Dict[str, str], media_dir: str
                             ) -> Dict[str, Any]:
    """Single-claim cross-check: resolves this Scrip record's own PID, searches for related documents,
    and downloads everything found into record['source_documents']."""
    reasons = []  # noqa: F841
    file_name = (record.get("document_metadata") or {}).get("file_name", "")
    own_pid = resolve_pid_from_filename(file_name)

    if own_pid:
        record["lac_pid"] = own_pid
        try:
            own_bundle = voyageur_lac.download_pid_bundle(
                own_pid, media_dir, document_type_override=record.get("document_type")
            )
            record["lac_catalog_title"] = fix_mojibake(own_bundle["lac_catalog_title"])
            type_fields = record.setdefault("type_specific_fields", {})
            if own_bundle.get("reel_numbers"):
                type_fields["reel_numbers"] = ", ".join(own_bundle["reel_numbers"])
            if own_bundle.get("series_code"):
                type_fields["rg_series_code"] = own_bundle["series_code"]

            parsed = extract_citation_fields(record["lac_catalog_title"])
            for k, v in parsed.items():
                if not type_fields.get(k):
                    type_fields[k] = v
            resolve_maiden_name_for_record(record)
        except lac_client.LacCallError as e:
            reasons.append(f"Paleographer: failed to fetch own PID {own_pid}: {e}")

    queries = build_claim_search_queries(record)
    if not queries:
        reasons.append(
            "Paleographer: no claim_number/affidavit_number/scrip_number/e-number available to search LAC with")
        if reasons:
            existing = record.get("review_reason")
            record["review_reason"] = "; ".join([existing] + reasons) if existing else "; ".join(reasons)
        return record

    all_found_pids = set()
    for query in queries:
        try:
            all_found_pids.update(lac_client.search(query, cookies))
        except lac_client.LacSearchAuthError as e:
            reasons.append(f"Paleographer: search cookie expired/invalid: {e}")
            break
        except lac_client.LacCallError as e:
            reasons.append(f"Paleographer: search failed for {query!r}: {e}")

    related_pids = sorted(p for p in all_found_pids if p != own_pid)
    source_documents = record.setdefault("source_documents", [])
    for related_pid in related_pids:
        try:
            bundle = voyageur_lac.download_pid_bundle(related_pid, media_dir)
            source_documents.extend(bundle["source_documents"])
        except lac_client.LacCallError as e:
            reasons.append(
                f"Paleographer: failed to fetch related PID {related_pid}: {e}")

    if reasons:
        existing = record.get("review_reason")
        record["review_reason"] = "; ".join([existing] + reasons) if existing else "; ".join(reasons)
    return record


def enrich_record_from_lac_metadata(sheet: Dict[str, Any], record: Dict[str, Any],
                                    metadata: "lac_client.RecordMetadata") -> None:
    """Applies live LAC catalog metadata to a sheet and record."""
    clean_title = fix_mojibake(metadata.title)
    record["lac_catalog_title_live"] = clean_title
    meta = sheet.setdefault("document_metadata", {})
    if metadata.reel_numbers:
        meta["reel_numbers"] = metadata.reel_numbers

    type_fields = record.setdefault("type_specific_fields", {})
    if metadata.series_code and not type_fields.get("rg_series_code"):
        type_fields["rg_series_code"] = metadata.series_code
    if metadata.reel_numbers and not type_fields.get("reel_numbers"):
        type_fields["reel_numbers"] = ", ".join(metadata.reel_numbers)

    parsed = extract_citation_fields(clean_title)
    for k, v in parsed.items():
        if not type_fields.get(k):
            type_fields[k] = v

    if not record.get("record_number") or record.get("record_number") == "0-0-0":
        record["record_number"] = build_composite_record_number(type_fields, record.get("lac_pid", ""))

    fix_all_participant_names_in_record(record)
    resolve_maiden_name_for_record(record)


def enrich_json_data(data: Dict[str, Any], checkpoint_path: Optional[str] = None,
                     delay_seconds: float = 0.4, limit: Optional[int] = None,
                     checkpoint_every: int = 50) -> Dict[str, Any]:
    """Iterates through all records in a JSON dataset, fetching live LAC metadata for each PID."""
    checkpoint = voyageur_lac.load_checkpoint(checkpoint_path) if checkpoint_path else {
        "done_pids": [], "failed_pids": {}
    }
    done = set(checkpoint.get("done_pids", []))
    failed = checkpoint.get("failed_pids", {})

    sheets = data.get("sheets", [])
    processed = 0
    since_checkpoint = 0

    for sheet in sheets:
        if limit is not None and processed >= limit:
            break
        records = sheet.get("records", [])
        if not records:
            continue
        for record in records:
            pid = resolve_pid_for_sheet_or_record(sheet, record)
            if not pid:
                continue
            if pid in done:
                continue

            try:
                metadata = lac_client.get_record_metadata(pid)
                enrich_record_from_lac_metadata(sheet, record, metadata)
                done.add(pid)
                failed.pop(pid, None)
                processed += 1
                since_checkpoint += 1
            except lac_client.LacCallError as e:
                failed[pid] = str(e)
                processed += 1

            if checkpoint_path and since_checkpoint >= checkpoint_every:
                checkpoint["done_pids"] = sorted(done)
                checkpoint["failed_pids"] = failed
                voyageur_lac.save_checkpoint(checkpoint_path, checkpoint)
                since_checkpoint = 0

            if delay_seconds > 0:
                time.sleep(delay_seconds)

    if checkpoint_path:
        checkpoint["done_pids"] = sorted(done)
        checkpoint["failed_pids"] = failed
        voyageur_lac.save_checkpoint(checkpoint_path, checkpoint)

    return data


def resolve_json_input(json_file: str, json_dir: str) -> Path:
    """Resolves the active JSON dataset path."""
    if os.path.isabs(json_file):
        return Path(json_file)
    p = Path(json_file)
    if p.is_file():
        return p
    return Path(json_dir) / json_file


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Paleographer: AI-driven extraction & document enrichment.")
    parser.add_argument("mode", choices=["enrich", "crosscheck", "partition", "resolve-names"],
                        help="Operating mode")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="Path to JSON dataset (for enrich, crosscheck, partition, resolve-names)")
    parser.add_argument("--delay", type=float, default=0.4, help="Delay in seconds between requests (for enrich)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of records to process (for enrich)")
    parser.add_argument("--output-dir", default=None, help="Output directory for partitioned datasets")
    parser.add_argument("--cookie-file", default=voyageur_lac.COOKIE_FILE,
                        help="Path to browser cookies file for LAC search (for crosscheck)")
    args, _ = parser.parse_known_args()

    if args.mode == "crosscheck":
        target = resolve_json_input(args.json_path or os.getenv("SCRIP_MASTER_DB_NAME") or "scrip_records.json",
                                    os.getenv("JSON_DIR", str(Path(__file__).resolve().parent.parent / "JSON")))
        print(f"Cross-checking claims in dataset: {target}...")
        try:
            cookies = voyageur_lac.load_cookies(args.cookie_file)
        except (FileNotFoundError, ValueError) as e:
            print(f"[FATAL ERROR] {e} Search LAC once in a real browser, then paste its Cookie header "
                  f"into that file.")
            return
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        for sheet in data.get("sheets", []):
            for record in sheet.get("records", []):
                cross_check_claim_record(record, cookies, voyageur_lac.MEDIA_DIR)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Cross-check complete: {target}")
        return

    # noinspection DuplicatedCode
    if args.mode == "enrich":
        target = resolve_json_input(args.json_path or os.getenv("SCRIP_MASTER_DB_NAME") or "scrip_records.json",
                                    os.getenv("JSON_DIR", str(Path(__file__).resolve().parent.parent / "JSON")))
        print(f"Enriching dataset: {target}...")
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        enrich_json_data(data, delay_seconds=args.delay, limit=args.limit)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Enrichment complete: {target}")
        return

    if args.mode == "partition":
        target = resolve_json_input(args.json_path or os.getenv("SCRIP_MASTER_DB_NAME") or "scrip_records.json",
                                    os.getenv("JSON_DIR", str(Path(__file__).resolve().parent.parent / "JSON")))
        out_dir = Path(args.output_dir) if args.output_dir else target.parent / "partitioned"
        print(f"Partitioning dataset {target} into {out_dir}...")
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        written = partition_json_by_collection(data, out_dir)
        print(f"Partitioned into {len(written)} collections in {out_dir}")
        for k, p in written.items():
            print(f" - {k}: {p.name}")
        return

    # noinspection DuplicatedCode
    if args.mode == "resolve-names":
        target = resolve_json_input(args.json_path or os.getenv("SCRIP_MASTER_DB_NAME") or "scrip_records.json",
                                    os.getenv("JSON_DIR", str(Path(__file__).resolve().parent.parent / "JSON")))
        print(f"Resolving names in dataset: {target}...")
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = resolve_dataset_maiden_names(data)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Resolved maiden/dit names for {count} records in {target}")
        return


if __name__ == "__main__":
    main()
