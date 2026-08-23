"""
Extract: record-type-generic document extraction for Paleographer.
"""

import agy_engine
import engine
import json
import math
import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
# noinspection PyUnresolvedReferences
from google import genai

# Reconfigure stdout to utf-8 to prevent crashing on emoji/checkmarks in debug mode.
# noinspection DuplicatedCode
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add repo root to sys.path to allow absolute imports for AntiquarianMCP.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from AntiquarianMCP import agy_client  # noqa: E402

from Commissioner import normalization  # noqa: E402
from Commissioner.envkit import load_tool_env  # noqa: E402


# Load global settings from project root .env and local settings from subfolder .env.
# Tool .env overrides global .env (root first, tool second, both override=True) - see
# Commissioner/envkit.py. ROOT_ENV stays defined: print_cost_line() re-reads it mid-run
# to pick up a budget edited while extraction is in progress.
ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"
load_tool_env(Path(__file__).resolve().parent)


# ==============================================================================
# POSTPROCESS
# ==============================================================================
def strip_diacritics(text: Optional[str]) -> Optional[str]:
    """Strips diacritics, keeping only plain ASCII characters."""
    if text is None:
        return None
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def derive_role_numbers(record: Dict[str, Any], roles_table: Dict[str, Dict[str, Optional[str]]]) -> None:
    """Sets each participant's role_number from their plain-word role_name."""
    for participant in record.get("participants", []):
        raw_role_name = participant.get("role_name")
        if raw_role_name:
            participant["role_name"] = normalization.capitalize_text_string(raw_role_name)
        if participant.get("role_number"):
            continue
        role_number = normalization.derive_role_number(raw_role_name or "", roles_table)
        if role_number is not None:
            participant["role_number"] = role_number


def derive_role_semantics(record: Dict[str, Any], roles_table: Dict[str, Dict[str, Optional[str]]]) -> None:
    """Sets each participant's role_semantic from their already-resolved role_number."""
    for participant in record.get("participants", []):
        role_number = participant.get("role_number")
        semantic = normalization.derive_role_semantic(role_number, roles_table)
        if semantic:
            participant["role_semantic"] = semantic


def _find_role_number(roles_table: Dict[str, Dict[str, Optional[str]]], semantic: str) -> Optional[str]:
    for role_number, role in roles_table.items():
        if role.get("semantic") == semantic:
            return role_number
    return None


def derive_suffixes(record: Dict[str, Any], roles_table: Dict[str, Dict[str, Optional[str]]]) -> None:
    """Sets 'Jr'/'Sr' on a primary participant and their father when their standardized
    names match exactly."""
    primary_role = _find_role_number(roles_table, "primary")
    father_role = _find_role_number(roles_table, "father")
    if primary_role is None or father_role is None:
        return

    participants = record.get("participants", [])
    primary: Optional[Dict[str, Any]] = next(
        (p for p in participants if p.get("role_number") == primary_role), None)
    father: Optional[Dict[str, Any]] = next(
        (p for p in participants if p.get("role_number") == father_role), None)
    if not primary or not father:
        return

    if (primary.get("std_given") and primary.get("std_surname")
            and primary["std_given"] == father.get("std_given")
            and primary["std_surname"] == father.get("std_surname")):
        primary["suffix"] = "Jr"
        father["suffix"] = "Sr"


def _participant_key(participant: Dict[str, Any]) -> tuple:
    return (
        (participant.get("std_given") or "").strip().lower(),
        (participant.get("std_surname") or "").strip().lower(),
    )


def _label_for(record: Dict[str, Any]) -> str:
    """Best available label for a record's own source document."""
    document_type = (record.get("type_specific_fields") or {}).get("document_type")
    if document_type:
        return normalization.capitalize_text_string(document_type)
    page = record.get("page")
    page_str = str(page).strip() if page is not None else ""
    return f"Page {page_str}" if page_str else "Untitled section"


def _source_document_entry(record: Dict[str, Any]) -> Dict[str, Any]:
    """One record's own text, snapshotted as a source_documents list entry."""
    return {
        "document_type": _label_for(record),
        "page": record.get("page"),
        "citation_text": record.get("citation_text"),
        "citation_details": record.get("citation_details"),
    }


def _merge_record_into(base: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """Merges `incoming` into `base` in place."""
    source_documents = base.setdefault("source_documents", [])
    if not source_documents:
        source_documents.append(_source_document_entry(base))
    source_documents.append(_source_document_entry(incoming))

    base_fields = base.setdefault("type_specific_fields", {})
    for key, value in (incoming.get("type_specific_fields") or {}).items():
        if key == "document_type":
            continue
        if value and not base_fields.get(key):
            base_fields[key] = value

    if incoming.get("review"):
        base["review"] = True
        reasons = [r for r in (base.get("review_reason"), incoming.get("review_reason")) if r]
        base["review_reason"] = "; ".join(reasons) if reasons else base.get("review_reason")

    base_participants = base.setdefault("participants", [])
    by_key = {_participant_key(p): p for p in base_participants if _participant_key(p) != ("", "")}
    for participant in incoming.get("participants", []):
        key = _participant_key(participant)
        existing = by_key.get(key) if key != ("", "") else None
        if existing is None or not isinstance(existing, dict):
            base_participants.append(participant)
            continue
        for field, value in participant.items():
            if field == "type_specific_fields":
                continue
            if value and not existing.get(field):
                existing[field] = value
        existing_fields: Dict[str, Any] = existing.setdefault("type_specific_fields", {})
        if isinstance(existing_fields, dict):
            for tk, tv in (participant.get("type_specific_fields") or {}).items():
                if tv and not existing_fields.get(tk):
                    existing_fields[tk] = tv


def merge_same_claim_records(sheets: List[Dict[str, Any]]) -> None:
    """Merges records that share the same derived record_id within one extraction result's
    sheets into a single record."""
    seen: Dict[str, Dict[str, Any]] = {}
    for sheet in sheets:
        records = sheet.get("records", [])
        kept: List[Dict[str, Any]] = []
        for record in records:
            record_id = record.get("record_id")
            if record_id and record_id in seen:
                _merge_record_into(seen[record_id], record)
            else:
                if record_id:
                    seen[record_id] = record
                kept.append(record)
        sheet["records"] = kept


def apply_defaults(target: Dict[str, Any], defaults_table: Dict[str, str]) -> None:
    """Fills only null/empty fields on target from defaults_table."""
    for key, value in defaults_table.items():
        if not target.get(key):
            target[key] = value


# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Selects the backend for AI extraction ("agy" for CLI, "api" for SDK).
EXTRACTION_ENGINE: str = (os.getenv("EXTRACTION_ENGINE", "agy") or "agy").strip().lower()
if EXTRACTION_ENGINE not in ("api", "agy"):
    raise RuntimeError(f"Unknown EXTRACTION_ENGINE '{EXTRACTION_ENGINE}' - expected 'api' or 'agy'.")

# Initialize API client only if using the SDK backend.
client = genai.Client(api_key=os.getenv("AI_API_KEY")) if EXTRACTION_ENGINE == "api" else None

# ==========================================
# CONFIGURATION
# ==========================================
RECORD_TYPE_NAME = os.getenv("PALEOGRAPHER_RECORD_TYPE", "")
COLLECTION_TITLE = os.getenv("VOLUME_TITLE") or ""
VOLUME_NUM = os.getenv("VOLUME_NUM", "")

TYPE_CFG = engine.parse_type_config(engine.resolve_prompt_path(RECORD_TYPE_NAME))


def resolve_setting(generic_key: str, default: str = "") -> str:
    """Resolves a generic runtime setting via the active record type's own field_remap
    table, falling back to reading generic_key directly."""
    for prefixed_key, target in TYPE_CFG.field_remap.items():
        if target == generic_key:
            val = os.getenv(prefixed_key, "")
            if val:
                return val
    return os.getenv(generic_key, default)


API_BUDGET: float = float(os.getenv("API_BUDGET", "5.00"))
COST_CFG = engine.CostConfig(
    cost_per_1m_in=float(os.getenv("COST_PER_1M_INPUT", "0.075")),
    cost_per_1m_out=float(os.getenv("COST_PER_1M_OUTPUT", "0.30")),
    cache_discount_multiplier=float(os.getenv("CACHE_DISCOUNT_MULTIPLIER", "0.10")),
)

PROGRAM_DIR: Path = Path(os.getenv("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent)))
GENEALOGY_DIR: Path = Path(os.getenv("GENEALOGY_DIR", ""))
_MASTER_DB_NAME = resolve_setting("MASTER_DB_NAME")
if not _MASTER_DB_NAME:
    _MASTER_DB_NAME = f"{TYPE_CFG.name}.json"
MASTER_DB: str = str(PROGRAM_DIR / os.getenv("JSON_DIR", "") / _MASTER_DB_NAME)
MEDIA_DIR: Path = GENEALOGY_DIR / (os.getenv("MEDIA_DIR") or "Media")
SOURCE_DIR: str = str(MEDIA_DIR / TYPE_CFG.name)

MODEL_ID: str = os.getenv("MODEL_NAME") or ""
if EXTRACTION_ENGINE == "api" and not MODEL_ID:
    raise RuntimeError("MODEL_NAME is not set. Check your .env configuration.")
DEBUG_FILE: Union[str, None] = sys.argv[1] if (
    len(sys.argv) > 1
    and sys.argv[1] not in ("extract", "enrich", "crosscheck", "partition", "resolve-names")
    and not sys.argv[1].startswith("-")
) else None

# Agy-specific settings.
AGY_MODEL_ID: str = os.getenv("AGY_MODEL_NAME") or agy_engine.DEFAULT_MODEL
AGY_CLI_BIN: str = os.getenv("AGY_CLI_BIN") or agy_client.DEFAULT_CLI_BIN
AGY_TIMEOUT_SECONDS: int = 240

SOURCE_SUFFIXES = engine.IMAGE_SUFFIXES + (".pdf",)

with open(Path(__file__).resolve().parent / "schema.json", "r", encoding="utf-8") as _schema_file:
    CORE_SCHEMA: Dict[str, Any] = json.load(_schema_file)

SCHEMA: Dict[str, Any] = engine.build_merged_schema(CORE_SCHEMA, TYPE_CFG.extra_fields)


# ==============================================================================
# RECORD POST-PROCESSING
# ==============================================================================
def finalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Applies every generic mechanical post-processing step to one extracted record."""
    derive_role_numbers(record, TYPE_CFG.roles)
    derive_role_semantics(record, TYPE_CFG.roles)
    normalization.derive_record_identity(record, TYPE_CFG.event_types)
    derive_suffixes(record, TYPE_CFG.roles)
    apply_defaults(record, TYPE_CFG.defaults.get("record", {}))

    if record.get("event_date"):
        record["event_date"] = normalization.parse_date_to_iso_format(record["event_date"])

    for participant in record.get("participants", []):
        participant["std_given"] = strip_diacritics(participant.get("std_given"))
        participant["std_surname"] = strip_diacritics(participant.get("std_surname"))
        if participant.get("birth_date"):
            participant["birth_date"] = normalization.parse_date_to_iso_format(participant["birth_date"])
        if participant.get("death_date"):
            participant["death_date"] = normalization.parse_date_to_iso_format(participant["death_date"])
        apply_defaults(participant, TYPE_CFG.defaults.get("participant", {}))

    return record


def finalize_page_data(page_data: Dict[str, Any]) -> Dict[str, Any]:
    for sheet in page_data.get("sheets", []):
        for record in sheet.get("records", []):
            finalize_record(record)
    # After every record has its record_id set, fold together any that share one.
    merge_same_claim_records(page_data.get("sheets", []))
    return page_data


def tag_document_metadata(page_data: Dict[str, Any], file_name: str, file_ext: str) -> None:
    for sheet in page_data.get("sheets", []):
        metadata = sheet.setdefault("document_metadata", {})
        metadata["file_name"] = file_name
        metadata["file_type"] = file_ext
        metadata["volume"] = VOLUME_NUM


# ==============================================================================
# MASTER DB HELPERS
# ==============================================================================
def load_master_db() -> Dict[str, Any]:
    if os.path.exists(MASTER_DB):
        with open(MASTER_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("record_type_name", TYPE_CFG.name)
        return data
    return {"collection_title": COLLECTION_TITLE, "record_type_name": TYPE_CFG.name, "sheets": [],
            "total_spent": 0.0, "total_pages_processed": 0, "pending_batch_jobs": []}


def save_master_db(master_data: Dict[str, Any]) -> None:
    updates = {
        "CITATION_TEXT": resolve_setting("CITATION_TEXT"),
        "CITATION_DETAIL": resolve_setting("CITATION_DETAIL"),
        "CALL_NUMBER": resolve_setting("CALL_NUMBER"),
        "REPOSITORY": resolve_setting("REPOSITORY"),
        "REPOSITORY_LOC": resolve_setting("REPOSITORY_LOC"),
        "COLLECTION_URL": resolve_setting("COLLECTION_URL"),
        "COLLECTION_NAME": resolve_setting("COLLECTION_NAME"),
        "PUBLISHER": resolve_setting("PUBLISHER"),
        "PUB_LOC": resolve_setting("PUB_LOC"),
        "REGISTER_NAME": resolve_setting("REGISTER_NAME"),
        "REGISTER_SOURCE_ID": resolve_setting("REGISTER_SOURCE_ID"),
        "VOLUME_TITLE": resolve_setting("VOLUME_TITLE"),
        "VOLUME_NUM": resolve_setting("VOLUME_NUM"),
    }
    non_empty_updates = {k: v for k, v in updates.items() if v}
    if non_empty_updates:
        master_data.setdefault("collection_metadata", {}).update(non_empty_updates)

    try:
        from Commissioner.record_registry import validate_collection_softly
        validate_collection_softly(master_data, master_data.get("record_type_name", TYPE_CFG.name), COLLECTION_TITLE)
    except Exception as e:
        print(f"[WARN] Commissioner validation failed for {COLLECTION_TITLE!r}: {e}")

    os.makedirs(os.path.dirname(MASTER_DB), exist_ok=True)
    master_db_tmp = MASTER_DB + ".tmp"
    with open(master_db_tmp, "w", encoding="utf-8") as f:
        json.dump(master_data, f, indent=2, ensure_ascii=False)
    os.replace(master_db_tmp, MASTER_DB)


def _sheet_is_placeholder(sheet: Dict[str, Any]) -> bool:
    """Returns True if the sheet is an unprocessed scaffold missing participant data."""
    return not any(record.get("participants") for record in sheet.get("records", []))


def get_processed_files(master_data: Dict[str, Any]) -> set:
    processed = set()
    for sheet in master_data.get("sheets", []):
        metadata = sheet.get("document_metadata", {})
        if not isinstance(metadata, dict) or "file_name" not in metadata:
            continue
        if not _sheet_is_placeholder(sheet):
            processed.add(metadata["file_name"])
    return processed


def get_placeholder_for_file(master_data: Dict[str, Any], filename: str) -> Optional[Dict[str, Any]]:
    base = os.path.basename(filename)
    for sheet in master_data.get("sheets", []):
        db_filename = sheet.get("document_metadata", {}).get("file_name")
        if db_filename == filename or db_filename == base:
            if _sheet_is_placeholder(sheet):
                return sheet
    return None


def _merge_type_specific_fields(existing_fields: Dict[str, Any], incoming_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Merges incoming into existing, keeping existing's value for any key it has already set."""
    merged = dict(existing_fields)
    for key, value in incoming_fields.items():
        if value and not merged.get(key):
            merged[key] = value
    return merged


def merge_sheets(master_data: Dict[str, Any], new_sheets: List[Dict[str, Any]]) -> None:
    master_sheets = master_data.get("sheets")
    if not isinstance(master_sheets, list):
        master_data["sheets"] = new_sheets
        return

    by_file_name = {
        sheet.get("document_metadata", {}).get("file_name"): idx
        for idx, sheet in enumerate(master_sheets)
    }

    for new_sheet in new_sheets:
        file_name = new_sheet.get("document_metadata", {}).get("file_name")
        existing_idx = by_file_name.get(file_name) if file_name is not None else None
        if existing_idx is not None and _sheet_is_placeholder(master_sheets[existing_idx]):
            placeholder = master_sheets[existing_idx]
            ph_records = placeholder.get("records", [])
            new_records = new_sheet.get("records", [])
            for ph_rec, new_rec in zip(ph_records, new_records):
                new_rec["type_specific_fields"] = _merge_type_specific_fields(
                    ph_rec.get("type_specific_fields", {}),
                    new_rec.get("type_specific_fields", {}),
                )
            master_sheets[existing_idx] = new_sheet
            continue
        master_sheets.append(new_sheet)


def record_cost(master_data: Dict[str, Any], usage_metadata: Any) -> engine.CallCost:
    if isinstance(usage_metadata, agy_client.AgyUsage):
        cost = agy_engine.adapt_agy_usage_to_call_cost(usage_metadata)
    else:
        cost = engine.compute_call_cost(usage_metadata, COST_CFG)
    master_data["total_spent"] = float(master_data.get("total_spent", 0.0)) + cost.call_cost
    master_data["total_pages_processed"] = int(master_data.get("total_pages_processed", 0)) + 1
    return cost


def print_cost_line(master_data: Dict[str, Any], cost: engine.CallCost) -> None:
    total_tokens = cost.cached_tokens + cost.in_tokens + cost.out_tokens + cost.thoughts_tokens
    print(f" DONE! | Cost: ${cost.call_cost:.4f}")
    print(f"      Tokens -> Cached: {cost.cached_tokens} | Input: {cost.in_tokens} | "
          f"Output: {cost.out_tokens} | Thinking: {cost.thoughts_tokens} = Total: {total_tokens}")

    if EXTRACTION_ENGINE == "agy":
        print("      Budget -> N/A (subscription backend, no per-call cost)")
        return

    total_spent = master_data["total_spent"]
    total_pages = master_data["total_pages_processed"]
    avg_cost = total_spent / total_pages if total_pages else 0.0

    load_dotenv(ROOT_ENV, override=True)
    live_budget = float(os.getenv("API_BUDGET", str(API_BUDGET)))
    remaining_budget = max(0.0, live_budget - total_spent)
    estimated_pages_left = math.floor(remaining_budget / avg_cost) if avg_cost > 0 else 0
    print(f"      Budget -> Total Spent: ${total_spent:.4f} | Est Pages Left: ~{estimated_pages_left}")


# ==============================================================================
# FILE CLASSIFICATION
# ==============================================================================
def list_source_files() -> List[str]:
    files = []
    for root, _, filenames in os.walk(SOURCE_DIR):
        for f in filenames:
            if f.lower().endswith(SOURCE_SUFFIXES):
                rel_path = os.path.relpath(os.path.join(root, f), SOURCE_DIR)
                files.append(rel_path.replace("\\", "/"))
    return sorted(files)


def is_batch_eligible(file_path: Path) -> bool:
    """Returns True if a PDF exceeds the batch page threshold. Images are never eligible."""
    if file_path.suffix.lower() != ".pdf":
        return False
    return engine.get_pdf_page_count(file_path) > TYPE_CFG.batch_page_threshold


# ==============================================================================
# SYNCHRONOUS PROCESSING
# ==============================================================================
def build_gen_config_kwargs(active_cache_name: Optional[str]) -> Dict[str, Any]:
    if DEBUG_FILE:
        return engine.build_debug_generation_config()
    kwargs: Dict[str, Any] = dict(response_mime_type="application/json", response_schema=SCHEMA)
    if active_cache_name:
        kwargs["cached_content"] = active_cache_name
    return kwargs


def process_one_file_sync(filename: str, active_cache_name: Optional[str],
                          pending_continuation: Optional[Dict[str, Any]] = None,
                          placeholder_sheet: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Processes one file synchronously. Returns a dict with page_data/usage_metadata on
    success, or None on failure. Raises DailyQuotaExhausted, which the caller must handle
    by stopping the run."""
    file_path = Path(SOURCE_DIR) / filename
    file_base = file_path.stem
    file_ext = file_path.suffix.upper().replace(".", "")
    if file_ext == "JPG":
        file_ext = "JPEG"
    pages_str = file_base.split("_")[-1]

    needs_review = False
    if placeholder_sheet:
        records = placeholder_sheet.get("records", [])
        if records:
            tsf = records[0].get("type_specific_fields", {})
            needs_review = tsf.get("needs_llm_structured_review", False)

    file_metadata = {
        "File": file_base,
        "Pages": pages_str,
        "needs_llm_structured_review": needs_review
    }
    dynamic_prompt = engine.get_dynamic_prompt(TYPE_CFG, file_metadata)

    dynamic_prompt += engine.build_continuation_context(pending_continuation)

    if EXTRACTION_ENGINE == "agy":
        images = (agy_engine.rasterize_pdf_to_images(file_path) if file_path.suffix.lower() == ".pdf"
                  else [engine.optimize_image(str(file_path))])
        full_prompt = engine.get_cached_system_instruction(TYPE_CFG) + "\n\n" + dynamic_prompt

        def call_fn() -> Any:
            result = agy_engine.call_agy_extract_chunked(images, SCHEMA, full_prompt, model=AGY_MODEL_ID,
                                                         cli_bin=AGY_CLI_BIN, timeout_seconds=AGY_TIMEOUT_SECONDS)
            return result.structured_output, result.usage

        try:
            page_data, usage_metadata = agy_engine.run_with_agy_retries(call_fn)
        except RuntimeError as e:
            print(f"\n[FAILED] {filename}: {e}")
            return None
    else:
        _, content_part = engine.build_content_part_for_file(client, file_path)

        if DEBUG_FILE:
            prompt = engine.get_cached_system_instruction(TYPE_CFG) + "\n\n" + dynamic_prompt
            prompt += engine.debug_schema_suffix(SCHEMA)
        else:
            prompt = dynamic_prompt

        contents = [prompt, content_part]

        def call_fn() -> Any:
            if client is None:
                raise RuntimeError("API client is not initialized")
            response = client.models.generate_content(
                model=MODEL_ID, contents=contents,
                config=genai.types.GenerateContentConfig(**build_gen_config_kwargs(active_cache_name)),
            )

            if DEBUG_FILE:
                parts = (response.candidates[0].content.parts or []) if response.candidates and \
                    response.candidates[0].content else []
                thought_parts = [p.text for p in parts if getattr(p, "thought", False) and p.text]
                if thought_parts:
                    print("\n\n--- MODEL THINKING ---")
                    print("\n".join(thought_parts))
                    print("--- END THINKING ---\n")

            raw_text = engine.strip_markdown_fences((response.text or "").strip())
            return json.loads(raw_text), response.usage_metadata

        try:
            page_data, usage_metadata = engine.run_with_retries(call_fn)
        except RuntimeError as e:
            print(f"\n[FAILED] {filename}: {e}")
            return None

    page_data = finalize_page_data(page_data)
    tag_document_metadata(page_data, filename, file_ext)

    return {"page_data": page_data, "usage_metadata": usage_metadata}


# noinspection DuplicatedCode
def pop_trailing_cutoff_record(sheets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pops and returns the last record if it continues onto the next image."""
    if not sheets:
        return None
    last_sheet = sheets[-1]
    records = last_sheet.get("records", [])
    if not records or not records[-1].get("continues_on_next_image"):
        return None
    record = records.pop()
    record["_pending_sheet_info"] = {"page_id": last_sheet.get("page_id"),
                                     "document_metadata": last_sheet.get("document_metadata")}
    return record


def consumed_leading_continuation(sheets: List[Dict[str, Any]]) -> bool:
    """True if the first record of the first sheet merged the pending continuation context."""
    if not sheets:
        return False
    first_records = sheets[0].get("records", [])
    return bool(first_records and first_records[0].get("continues_from_previous_image"))


def reattach_leftover_record(sheets: List[Dict[str, Any]], leftover: Dict[str, Any]) -> None:
    """Reattaches an uncontinued pending record as its own one-record sheet."""
    sheet_info = leftover.pop("_pending_sheet_info", {}) or {}
    sheets.insert(0, {"page_id": sheet_info.get("page_id", ""),
                      "document_metadata": sheet_info.get("document_metadata", {}), "records": [leftover]})


def run_synchronous_batch(files: List[str], master_data: Dict[str, Any]) -> None:
    total_files = len(files)
    active_cache_name = None
    pending_continuation: Optional[Dict[str, Any]] = None

    if not DEBUG_FILE and files:
        print(f"Found {total_files} file(s) to process synchronously.")
        if EXTRACTION_ENGINE == "api":
            print("Creating Context Cache for System Instructions to reduce costs...")
            active_cache_name = engine.create_context_cache(
                client, MODEL_ID, engine.get_cached_system_instruction(TYPE_CFG))
            if active_cache_name:
                print(f"Cache created successfully: {active_cache_name}\n")

    try:
        for index, filename in enumerate(files, start=1):
            print(f"[{index}/{total_files}] Processing {filename} with {MODEL_ID}...", end="", flush=True)
            try:
                placeholder = get_placeholder_for_file(master_data, filename)
                result = process_one_file_sync(filename, active_cache_name,
                                               pending_continuation, placeholder_sheet=placeholder)
            except engine.DailyQuotaExhausted:
                print("\n\n[FATAL ERROR] Daily Quota Exhausted.")
                print("Progress saved. Exiting script to prevent infinite crashing.")
                if not DEBUG_FILE and pending_continuation is not None:
                    reattach_leftover_record(master_data.setdefault("sheets", []), pending_continuation)
                    save_master_db(master_data)
                return
            except Exception as e:
                print(f" LOCAL ERROR! Details: {e}")
                continue

            if result is None:
                continue

            cost = record_cost(master_data, result["usage_metadata"])
            sheets = result["page_data"].get("sheets", [])

            if DEBUG_FILE:
                print("--- EXTRACTED JSON (not saved to master DB) ---")
                print(json.dumps(result["page_data"], indent=2, ensure_ascii=False))
                print_cost_line(master_data, cost)
            else:
                if pending_continuation is not None and not consumed_leading_continuation(sheets):
                    reattach_leftover_record(sheets, pending_continuation)
                pending_continuation = pop_trailing_cutoff_record(sheets)
                merge_sheets(master_data, sheets)
                save_master_db(master_data)
                print_cost_line(master_data, cost)

        if not DEBUG_FILE and pending_continuation is not None:
            reattach_leftover_record(master_data.setdefault("sheets", []), pending_continuation)
            save_master_db(master_data)
    finally:
        if active_cache_name:
            engine.delete_context_cache(client, active_cache_name)
            print(f"\nDeleted context cache: {active_cache_name}")


# ==============================================================================
# BATCH PROCESSING (large multi-page documents)
# ==============================================================================
def run_batch_mode(files: List[str], master_data: Dict[str, Any]) -> None:
    """Submits new batch jobs and retrieves completed ones via AI Assistant's Batch API."""
    pending_jobs = master_data.setdefault("pending_batch_jobs", [])

    if pending_jobs:
        completed, still_pending = engine.check_batch_jobs(client, pending_jobs)
        master_data["pending_batch_jobs"] = still_pending

        for entry in completed:
            print(f"Retrieving results for completed batch job {entry['job_name']}...")
            for source_file, response in engine.retrieve_batch_results(client, entry["job_name"]):
                try:
                    raw_text = engine.strip_markdown_fences((response.text or "").strip())
                    page_data = json.loads(raw_text)
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"   [!] Could not parse batch result for {source_file}: {e}")
                    continue

                page_data = finalize_page_data(page_data)
                file_ext = Path(source_file).suffix.upper().replace(".", "")
                tag_document_metadata(page_data, source_file, file_ext)
                merge_sheets(master_data, page_data.get("sheets", []))
                cost = record_cost(master_data, response.usage_metadata)
                print(f"Merged {source_file}: cost ${cost.call_cost:.4f}")

            save_master_db(master_data)

    if not files:
        if not master_data.get("pending_batch_jobs"):
            print("No new or pending batch files.")
        return

    print(f"Submitting {len(files)} file(s) as a new batch job...")
    system_instruction = engine.get_cached_system_instruction(TYPE_CFG)
    requests = []
    for filename in files:
        file_path = Path(SOURCE_DIR) / filename
        _, content_part = engine.build_content_part_for_file(client, file_path)
        file_metadata = {"File": file_path.stem, "Pages": str(engine.get_pdf_page_count(file_path))}
        dynamic_prompt = engine.get_dynamic_prompt(TYPE_CFG, file_metadata)

        placeholder = get_placeholder_for_file(master_data, filename)
        if placeholder:
            records = placeholder.get("records", [])
            if records:
                tsf = records[0].get("type_specific_fields", {})
                needs_review = tsf.get("needs_llm_structured_review", False)
                if needs_review:
                    dynamic_prompt += "\n\nCRITICAL INSTRUCTION: The structured data table in this document was either empty or missing. You MUST carefully read the unstructured summary paragraph/narrative and attempt to extract all structured vital dates (birth, death) and employment rows (positions, posts, dates) from it, mapping them into the structured JSON fields to the best of your ability."  # noqa: E501
                else:
                    dynamic_prompt += "\n\nCRITICAL INSTRUCTION: The structured data table in this document was successfully parsed by a previous system. DO NOT attempt to extract structured vital dates or employment rows from the image. Focus ONLY on transcribing the unstructured summary narrative exactly as written. Leave the structured data fields empty, as they will be preserved from the previous pass."  # noqa: E501

        prompt = system_instruction + "\n\n" + dynamic_prompt
        gen_config_kwargs: Dict[str, Any] = dict(response_mime_type="application/json", response_schema=SCHEMA)
        requests.append(engine.build_batch_request(MODEL_ID, [prompt, content_part], filename,
                                                   gen_config_kwargs))

    job_name = engine.submit_batch_job(client, MODEL_ID, requests)
    master_data.setdefault("pending_batch_jobs", []).append(
        {"job_name": job_name, "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"), "file_names": files})
    save_master_db(master_data)
    print(f"Submitted batch job '{job_name}' for {len(files)} file(s). Check back later; "
          "re-run this same step to retrieve results once AI Assistant finishes.")


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    if EXTRACTION_ENGINE == "agy":
        print("Verifying AGY CLI authentication...")
        if not agy_client.check_or_prompt_auth(AGY_MODEL_ID, cli_bin=AGY_CLI_BIN):
            print("[FATAL ERROR] Could not authenticate with agy. Run the 'Test Agy "
                  "Connection' action in Antiquarian's Global Settings, or `agy` "
                  "directly, to sign in, then try again.")
            return
        print("Authenticated.\n")

    master_data = load_master_db()
    all_files = list_source_files()

    if DEBUG_FILE:
        if DEBUG_FILE not in all_files:
            print(f"[DEBUG MODE] '{DEBUG_FILE}' not found in {SOURCE_DIR}. Aborting.")
            return
        print(f"[DEBUG MODE] Processing ONLY '{DEBUG_FILE}' with thinking enabled. "
              f"Nothing will be saved to {MASTER_DB}.\n")
        run_synchronous_batch([DEBUG_FILE], master_data)
        return

    processed_files = get_processed_files(master_data)
    already_in_batch = {fn for entry in master_data.get("pending_batch_jobs", [])
                        for fn in entry.get("file_names", [])}
    pending_files = []
    for f in all_files:
        base = os.path.basename(f)
        if f not in processed_files and base not in processed_files:
            if f not in already_in_batch and base not in already_in_batch:
                pending_files.append(f)

    if EXTRACTION_ENGINE == "agy":
        if pending_files:
            run_synchronous_batch(pending_files, master_data)
        else:
            print("No new files to process.")
        return

    sync_files: List[str] = []
    batch_files: List[str] = []
    for filename in pending_files:
        file_path = Path(SOURCE_DIR) / filename
        (batch_files if is_batch_eligible(file_path) else sync_files).append(filename)

    if batch_files or master_data.get("pending_batch_jobs"):
        run_batch_mode(batch_files, master_data)

    if sync_files:
        run_synchronous_batch(sync_files, master_data)
    elif not batch_files and not master_data.get("pending_batch_jobs"):
        print("No new files to process.")


if __name__ == "__main__":
    main()
