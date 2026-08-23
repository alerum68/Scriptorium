"""
Shared Paleographer Engine.

Houses every piece of machinery used to extract structured genealogical data
from historical documents that has zero record-type-specific logic: prompt/
schema resolution from a record type's .pmt file, image/PDF content routing,
AI Assistant API calls (synchronous and batch), retry/backoff, context caching, and
cost tracking. Adding a new record type never touches this file; it only
ever requires a new .pmt file in prompts/.
"""

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from string import Template
from textwrap import dedent
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import pdfplumber
from PIL import Image
# noinspection PyUnresolvedReferences
from google import genai
# noinspection PyUnresolvedReferences
from google.genai import types

# PDFix lives in its own sibling tool folder, not an installed package - add the repo
# root to sys.path so it can be imported the same way every other same-folder import in
# this codebase already relies on Python's automatic sys.path[0] behavior.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from PDFix.PDFix import optimize_pdf, COMPRESSION_PARAMS  # noqa: E402
from AntiquarianMCP import agy_client  # noqa: E402
from Commissioner.models import FACT_DEFINITIONS  # noqa: E402
from Commissioner.record_registry import (  # noqa: E402
    load_pmt_parts,
    prompt_search_dirs,
    resolve_prompt_path as comm_resolve_prompt_path,
)

DEFAULT_TYPE = "Parish.pmt"

# A PDF with more pages than this routes through the Batch API instead of the
# synchronous path; a single-page image never crosses it.
BATCH_PAGE_THRESHOLD = 5

FRONT_MATTER_DELIM = "---"

UNIVERSAL_PROMPT_SUFFIX = dedent("""
    UNIVERSAL OUTPUT RULES (apply regardless of record type):
    - Output must match the provided JSON schema exactly. No extra fields. Use null when data isn't
      explicitly present.
    - raw_* fields preserve the exact original reading (diacritics, spelling, everything). std_* fields are
      your best linguistic standardization; formatting, diacritic-stripping, and code/ID derivation are
      handled downstream, not by you.
    - Set "review": true and give a short plain-English "review_reason" (under 15 words) whenever any part
      of a record or participant is uncertain, guessed, illegible, or otherwise needs a human to double-check
      it. Otherwise "review": false and "review_reason": null.
    - Illegible text: attempt reading at least 3 times. Use "[illegible]" if genuinely unreadable. NEVER
      guess silently.
    - Strikethroughs: insert "[struck through: <best reading>]" inline in the transcription.
    - PAGE CONTINUITY: each image is processed independently, with no memory of any other image -
      except when a "CONTINUATION FROM PREVIOUS IMAGE" context block is explicitly given to you below,
      which is the one case where you DO have information from the previous image. If your OWN last
      record on THIS image is cut off at the bottom of the page (no natural ending, mid-sentence, no
      closing/signature), set that record's "continues_on_next_image": true - do not guess how it
      would have ended. If you were given a "CONTINUATION FROM PREVIOUS IMAGE" block and the TOP of
      this image is what completes it, output ONE merged record combining the given content with what
      you read here (reusing its record_number/year), set "continues_from_previous_image": true on it,
      and do not also output a second, separate record for the same entry. If that context was given
      but nothing on this image continues it, ignore it entirely - do not force a merge.
""").strip()


class DailyQuotaExhausted(Exception):
    """Raised when AI Assistant reports the daily quota is exhausted. The whole run should stop, not retry."""


@dataclass
class TypeConfig:
    """A record type's resolved configuration: structured front-matter data plus the prose
    prompt body, plus event_types loaded from the toolbox-wide FactTypes.json rather than
    the .pmt file itself (see load_event_types)."""
    name: str
    event_types: Dict[str, Dict[str, str]]
    roles: Dict[str, Dict[str, Optional[str]]]
    defaults: Dict[str, Dict[str, str]]
    extra_fields: Dict[str, List[Dict[str, str]]]
    metadata_fields: Dict[str, str]
    field_remap: Dict[str, str]
    batch_page_threshold: int
    role_validation: str
    prose: str


@dataclass
class CostConfig:
    cost_per_1m_in: float
    cost_per_1m_out: float
    cache_discount_multiplier: float


@dataclass
class CallCost:
    in_tokens: int
    out_tokens: int
    cached_tokens: int
    thoughts_tokens: int
    call_cost: float


# ==========================================
# TYPE CONFIG RESOLUTION
# ==========================================
def _substitute_env(value: Any) -> Any:
    """Recursively substitutes ${VAR_NAME} placeholders from the environment."""
    if isinstance(value, str):
        return Template(value).safe_substitute(os.environ)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


_prompt_search_dirs = prompt_search_dirs


def resolve_prompt_path(requested_name: str) -> Path:
    """Finds the .pmt file for the requested record type (case-insensitive, extension
    optional), falling back to DEFAULT_TYPE if not found."""
    return comm_resolve_prompt_path(requested_name, default_type=DEFAULT_TYPE)


def load_event_types() -> Dict[str, Dict[str, str]]:
    """Loads the toolbox-wide fact/event vocabulary from Commissioner.models.FACT_DEFINITIONS.
    Person and family fact buckets are flattened into one lookup keyed by name.
    id_prefix is derived from each fact's gedcom_tag for record_id construction (e.g. "BAPM-14")."""
    return {
        fd.name: {"id_prefix": f"{fd.gedcom_tag}-"}
        for fd in FACT_DEFINITIONS
    }


def parse_type_config(pmt_path: Path) -> TypeConfig:
    """Parses a .pmt file's YAML front matter and prose body. The front matter carries
    per-type role vocabulary, defaults, schema extensions, metadata field templates, and
    a field_remap table (which of this record type's own prefixed settings-tab keys map
    to which generic runtime env var - e.g. CHURCH_GEDCOM_NAME -> GEDCOM_OUTPUT_NAME);
    IMAGE_DIR is deliberately NOT part of this table - Archivist/General.py auto-resolves
    it to Media/<this .pmt's own name>, matching this module's own SOURCE_DIR convention
    below, with no per-type override; event/fact
    vocabulary comes from the shared FactTypes.json instead (see load_event_types), since
    it's RootsMagic's own vocabulary rather than something that varies by document type.
    The prose body is the free-form system-instruction text handed to the LLM.

    field_remap exists so Paleographer.py (and, independently, Archivist.py) can resolve
    their own generic runtime settings (IMAGE_DIR, MASTER_DB_NAME, CALL_NUMBER, etc.) from
    whichever of this record type's own prefixed .env keys is actually set, without
    Antiquarian.py's GUI layer needing to know what a record type even is - each script
    reads this table itself, from its own .env, and stays runnable standalone."""
    front_matter, prose = load_pmt_parts(pmt_path)
    front_matter = _substitute_env(front_matter)

    return TypeConfig(
        name=pmt_path.stem,
        event_types=load_event_types(),
        roles=front_matter.get("roles", {}),
        defaults=front_matter.get("defaults", {}),
        extra_fields=front_matter.get("extra_fields", {}),
        metadata_fields=front_matter.get("metadata_fields", {}),
        field_remap=front_matter.get("field_remap", {}),
        batch_page_threshold=int(front_matter.get("batch_page_threshold", BATCH_PAGE_THRESHOLD)),
        role_validation=front_matter.get("role_validation", "closed"),
        prose=prose.strip(),
    )


# ==========================================
# SCHEMA MERGING
# ==========================================
def build_merged_schema(core_schema: Dict[str, Any],
                        extra_fields: Dict[str, List[Dict[str, str]]]) -> Dict[str, Any]:
    """Deep-copies the universal core schema and injects a record type's extra fields
    into the open `type_specific_fields` slot at record and participant level, producing
    a fully concrete schema for AI Assistant's response_schema."""
    merged = copy.deepcopy(core_schema)

    def inject(container: Dict[str, Any], fields: List[Dict[str, str]]) -> None:
        properties = container.setdefault("properties", {})
        for f in fields:
            if f.get("type") == "enum":
                properties[f["name"]] = {"type": "string", "enum": f.get("choices", []), "nullable": True}
            elif f.get("type") == "dict":
                properties[f["name"]] = {"type": "object", "nullable": True}
            else:
                properties[f["name"]] = {"type": f.get("type", "string"), "nullable": True}

    record_props = merged["properties"]["sheets"]["items"]["properties"]["records"]["items"]["properties"]
    inject(record_props["type_specific_fields"], extra_fields.get("record", []))

    participant_props = record_props["participants"]["items"]["properties"]
    inject(participant_props["type_specific_fields"], extra_fields.get("participant", []))

    return merged


# ==========================================
# PROMPT BUILDING
# ==========================================
def optimize_image(image_path: str, max_dimension: int = 2048) -> Image.Image:
    """Downscale an image before sending it to the API, to cut token costs.

    2048px keeps 19th-century cursive legible while limiting tile costs."""
    img = Image.open(image_path)
    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return img


def build_vocabulary_summary(type_cfg: TypeConfig) -> str:
    """Auto-generates the valid event_type/role_name vocabulary lines - event_type from
    the shared FactTypes.json (every record type draws from the same RootsMagic fact
    vocabulary), role_name from this type's own front-matter roles table - so a .pmt
    file's prose never needs to hand-write (and risk drifting from) either vocabulary.
    A role entry's optional 'context' string is appended in parens when present, for a
    role whose real-world meaning shifts by event type (e.g. Parish.pmt's "Primary" role
    means the child for a baptism but the deceased for a burial) - without it, the model
    only ever sees a bare role name with no way to know which meaning applies."""
    event_type_names = ", ".join(sorted(type_cfg.event_types.keys()))

    role_context_by_name: Dict[str, Optional[str]] = {}
    for r in type_cfg.roles.values():
        name = r.get("name")
        if name and name not in role_context_by_name:
            role_context_by_name[name] = r.get("context")
    role_lines = [f"{name} ({context})" if context else name
                  for name, context in sorted(role_context_by_name.items())]
    role_names = ", ".join(role_lines)

    if type_cfg.role_validation == "open":
        # Census.pmt's open mode: the 9-name list is not exhaustive - a role outside it
        # (e.g. Boarder, Roomer) must be recorded verbatim as an association, never
        # coerced into one of the family roles (see the sub-project 2 design spec).
        role_name_line = dedent(f"""\
            - role_name: use one of these when it applies - {role_names} - and record the
              source's own relationship term verbatim when none of these fit (e.g. Boarder,
              Servant, Roomer, Grandson). Only the listed names carry a family relationship;
              anything else is recorded as-is and treated as an association, not a family link.""")
    else:
        role_name_line = dedent(f"""\
            - role_name (choose exactly one per participant - where a role's own meaning
              depends on the event type, that's noted in parentheses): {role_names}""")

    return dedent(f"""
        VALID VOCABULARY FOR THIS RECORD TYPE:
        - event_type (choose exactly one): {event_type_names}
    """).strip() + "\n" + role_name_line


def get_cached_system_instruction(type_cfg: TypeConfig) -> str:
    """The static system-instruction ruleset for this record type: its own prose body,
    the auto-generated event/role vocabulary summary, and the universal rules shared by
    every type."""
    return f"{type_cfg.prose}\n\n{build_vocabulary_summary(type_cfg)}\n\n{UNIVERSAL_PROMPT_SUFFIX}"


def get_dynamic_prompt(type_cfg: TypeConfig, file_metadata: Dict[str, str]) -> str:
    """Per-file metadata block appended to the cached prompt: the type's own
    project-level fields (from its .pmt front matter, e.g. parish name/location) plus
    this specific file's runtime fields (filename, page)."""
    combined = {**type_cfg.metadata_fields, **file_metadata}
    lines = "\n".join(f"{k}: {v}," for k, v in combined.items())
    return f"\nMetadata Context:\n{lines}\n"


def build_continuation_context(pending_record: Optional[Dict[str, Any]]) -> str:
    """Formats the previous image's cut-off last record (see UNIVERSAL_PROMPT_SUFFIX's
    PAGE CONTINUITY rule) as extra prompt context for the next image - or returns "" when
    there is nothing pending, so callers can always append this unconditionally. Only the
    fields useful for recognizing/completing a continuation are included, not the whole
    record verbatim (id/code fields are downstream-derived and meaningless as context)."""
    if not pending_record:
        return ""
    participants_summary = [
        {"role_name": p.get("role_name"), "std_given": p.get("std_given"), "std_surname": p.get("std_surname")}
        for p in pending_record.get("participants", [])
    ]
    return dedent(f"""

        CONTINUATION FROM PREVIOUS IMAGE:
        The record below was cut off at the bottom of the previous image and may continue
        at the top of THIS one. If it does, merge it into ONE complete record reusing its
        record_number/year, and set that record's continues_from_previous_image: true. If
        nothing on this image continues it, ignore this block entirely.

        record_number: {pending_record.get("record_number") or ""}
        year: {pending_record.get("year") or ""}
        event_type: {pending_record.get("event_type") or ""}
        transcription so far: {pending_record.get("citation_text") or ""}
        translation so far: {pending_record.get("citation_details") or ""}
        participants captured so far: {json.dumps(participants_summary, ensure_ascii=False)}
    """)


# ==========================================
# CONTENT-TRANSPORT ROUTING
# ==========================================
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tiff", ".tif")


def has_usable_text_layer(pdf_path: Union[str, Path], sample_pages: int = 3,
                          min_alpha_ratio: float = 0.5, min_chars: int = 40) -> bool:
    """Probes a PDF's first few pages for a genuine, non-garbage text layer, to decide
    whether its content can be extracted locally (pdfplumber) or must go to AI Assistant as a
    native document (a scanned/handwritten source has no real text layer at all)."""
    # noinspection broad-exception
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            sampled = "".join((page.extract_text() or "") for page in pdf.pages[:sample_pages])
    except Exception:
        return False

    if len(sampled) < min_chars:
        return False

    alpha_count = sum(1 for c in sampled if c.isalpha())
    return (alpha_count / len(sampled)) >= min_alpha_ratio


def get_pdf_page_count(pdf_path: Union[str, Path]) -> int:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return len(pdf.pages)


def optimize_pdf_for_upload(file_path: Path, compression_level: int = 2) -> Path:
    """Runs PDFix's lossless structural optimization (garbage-collection + stream deflate)
    against a throwaway temp copy before uploading to AI Assistant, to cut upload size/cost -
    mirrors optimize_image()'s downscaling for images, but structural rather than pixel-
    based (embedded image DPI is untouched, so transcription quality is unaffected at any
    level). NEVER mutates the researcher's original source PDF: optimize_pdf() itself does
    an in-place move onto whatever path it's given, so that move is aimed at the temp
    copy, not file_path. Falls back to returning file_path unchanged if anything goes
    wrong, so a failed optimization never blocks the actual upload."""
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".pdf", prefix="pdfix_upload_")
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        shutil.copy2(file_path, tmp_path)
        params = COMPRESSION_PARAMS.get(compression_level, COMPRESSION_PARAMS[2])
        optimize_pdf(str(tmp_path), params)
        return tmp_path
    # noinspection PyBroadException
    except Exception:
        tmp_path.unlink(missing_ok=True)
        return file_path


def build_content_part_for_file(client: genai.Client, file_path: Path) -> Tuple[str, Any]:
    """Classifies a source file and returns (mode, content_part), where mode is one of
    "image", "pdf_native", or "pdf_text", and content_part is what should be appended to
    the `contents` list alongside the prompt (a PIL Image, an uploaded AI Assistant File
    reference, or extracted text)."""
    suffix = file_path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image", optimize_image(str(file_path))

    if suffix == ".pdf":
        if has_usable_text_layer(file_path):
            with pdfplumber.open(str(file_path)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return "pdf_text", text

        compression_level = int(os.getenv("PALEOGRAPHER_PDF_COMPRESSION_LEVEL", "2"))
        optimized_path = optimize_pdf_for_upload(file_path, compression_level)
        try:
            uploaded = client.files.upload(file=str(optimized_path))
        finally:
            if optimized_path != file_path:
                optimized_path.unlink(missing_ok=True)
        return "pdf_native", uploaded

    raise ValueError(f"Unsupported file type: {file_path}")


# ==========================================
# COST TRACKING
# ==========================================
def compute_call_cost(usage_metadata: Any, cost_cfg: CostConfig) -> CallCost:
    """Computes the dollar cost of one API call from its usage_metadata, given
    manually-configured per-token pricing (no live pricing API call)."""
    if usage_metadata is None:
        return CallCost(0, 0, 0, 0, 0.0)

    in_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
    out_tokens = getattr(usage_metadata, "candidates_token_count", 0) or 0
    cached_tokens = getattr(usage_metadata, "cached_content_token_count", 0) or 0
    thoughts_tokens = getattr(usage_metadata, "thoughts_token_count", 0) or 0

    cache_rate = cost_cfg.cost_per_1m_in * cost_cfg.cache_discount_multiplier
    cost_cached = (cached_tokens / 1_000_000) * cache_rate
    cost_in = (in_tokens / 1_000_000) * cost_cfg.cost_per_1m_in
    cost_out = ((out_tokens + thoughts_tokens) / 1_000_000) * cost_cfg.cost_per_1m_out

    return CallCost(in_tokens, out_tokens, cached_tokens, thoughts_tokens, cost_cached + cost_in + cost_out)


# ==========================================
# SYNCHRONOUS RETRY LOOP
# ==========================================
def run_with_retries(call_fn: Callable[[], Any], max_retries: int = 10, max_json_retries: int = 3) -> Any:
    """Calls call_fn() until it succeeds, retrying on transient errors: malformed JSON
    (up to max_json_retries), rate limits and server errors (backoff, up to max_retries).
    Raises DailyQuotaExhausted immediately on a daily-quota error, since the whole run
    should stop rather than retry. Raises RuntimeError if retries are exhausted."""
    attempts = 0
    json_attempts = 0

    while attempts < max_retries:
        try:
            return call_fn()
        except json.JSONDecodeError as e:
            json_attempts += 1
            if json_attempts >= max_json_retries:
                raise RuntimeError(
                    f"Malformed JSON {max_json_retries}x in a row (likely a systemic issue, not transient): {e}"
                ) from e
            print(f"   [!] Malformed JSON generated. Retrying... ({e})", end="", flush=True)
            time.sleep(2)
            attempts += 1
        except genai.errors.ClientError as api_error:
            error_msg = str(api_error)
            if "PerDay" in error_msg:
                raise DailyQuotaExhausted(error_msg) from api_error
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or agy_client.is_quota_or_rate_limit(error_msg):
                match = re.search(r"retry in (\d+\.?\d*)s", error_msg)
                if match:
                    wait_time = float(match.group(1)) + 1.5
                else:
                    wait_time = agy_client.parse_quota_reset_wait_seconds(error_msg)
                    if wait_time is None:
                        wait_time = 35.0 * float(2 ** attempts)
                agy_client.pause_for_quota_reset(wait_time, reason=f"Rate limit hit ({error_msg[:60]})")
                attempts += 1
            elif "500" in error_msg or "503" in error_msg or "504" in error_msg:
                print(f"   [!] Google Server Error ({error_msg[:30]}). Sleeping 5s...", end="", flush=True)
                time.sleep(5)
                attempts += 1
            else:
                raise

    raise RuntimeError(f"Exhausted all {max_retries} retries")


# ==========================================
# BATCH API (for large multi-page documents)
# ==========================================
def build_batch_request(model_id: str, contents: list, source_file: str,
                        gen_config_kwargs: Dict[str, Any]) -> types.InlinedRequest:
    """Builds one AI Assistant batch request, tagging it with the source filename so the
    result can be matched back to its file once the job completes."""
    return types.InlinedRequest(
        model=model_id, contents=contents, metadata={"source_file": source_file},
        config=genai.types.GenerateContentConfig(**gen_config_kwargs),
    )


def submit_batch_job(client: genai.Client, model_id: str, requests: List[types.InlinedRequest],
                     display_name: str = "paleographer-batch") -> str:
    """Submits a list of pre-built requests as one AI Assistant batch job. Returns the job name
    to persist for a later check_batch_jobs()/retrieve_batch_results() call."""
    job = client.batches.create(model=model_id, src=requests,
                                config=types.CreateBatchJobConfig(display_name=display_name))
    if not job.name:
        raise RuntimeError("AI Assistant created a batch job but returned no job name.")
    return job.name


def check_batch_jobs(client: genai.Client, pending: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Checks every pending batch job entry ({"job_name": ..., ...}) against AI Assistant.
    Returns (completed_entries, still_pending_entries). A failed, canceled, or expired
    job is surfaced via print and dropped rather than retried automatically or looped
    silently."""
    completed, still_pending = [], []
    for entry in pending:
        job = client.batches.get(name=entry["job_name"])
        if job.state == types.JobState.JOB_STATE_SUCCEEDED:
            completed.append(entry)
        elif job.state in (types.JobState.JOB_STATE_FAILED, types.JobState.JOB_STATE_CANCELLED,
                           types.JobState.JOB_STATE_EXPIRED):
            print(f"   [!] Batch job {entry['job_name']} ended in state {job.state}; "
                  "not retried automatically, resubmit manually if needed.")
        else:
            still_pending.append(entry)
    return completed, still_pending


def retrieve_batch_results(client: genai.Client, job_name: str) -> List[Tuple[str, Any]]:
    """Fetches a completed batch job's results. Returns a list of (source_file,
    GenerateContentResponse) pairs, one per original request that didn't error out."""
    job = client.batches.get(name=job_name)
    results: List[Tuple[str, Any]] = []
    dest = job.dest
    if dest is None or not dest.inlined_responses:
        return results
    for r in dest.inlined_responses:
        source_file = (r.metadata or {}).get("source_file", "")
        if r.error:
            print(f"   [!] Batch item for {source_file} failed: {r.error}")
            continue
        results.append((source_file, r.response))
    return results


# ==========================================
# CONTEXT CACHING
# ==========================================
def create_context_cache(client: genai.Client, model_id: str, system_instruction: str,
                         ttl_seconds: int = 86400) -> Optional[str]:
    """Uploads the system instruction as a AI Assistant Context Cache, for a cost discount on
    repeated calls. Returns None (and prints a warning) on failure, so callers can
    proceed without caching rather than crash."""
    try:
        cache = client.caches.create(
            model=model_id,
            config=genai.types.CreateCachedContentConfig(system_instruction=system_instruction,
                                                         ttl=f"{ttl_seconds}s"),
        )
        return cache.name
    except Exception as e:
        print(f"Warning: Failed to create cache. Proceeding without it. Error: {e}")
        return None


def delete_context_cache(client: genai.Client, cache_name: str) -> None:
    try:
        client.caches.delete(name=cache_name)
    except Exception as e:
        print(f"Warning: Failed to delete cache {cache_name} (it will expire on its own via TTL). Error: {e}")


# ==========================================
# DEBUG MODE HELPERS
# ==========================================
def strip_markdown_fences(text: str) -> str:
    """Removes ```json ... ``` (or bare ```) code fences some models add despite
    instructions not to."""
    backticks = "`" * 3
    if text.startswith(backticks):
        pattern = r"^" + backticks + r"(?:json)?\s*|\s*" + backticks + r"$"
        return re.sub(pattern, "", text.strip())
    return text


def debug_schema_suffix(schema: Dict[str, Any]) -> str:
    """Debug mode can't combine thinking_config with response_schema/cached_content, so
    the schema is instead appended to the prompt as text and parsed defensively via
    strip_Markdown_fences() afterward."""
    return ("\n\nOUTPUT FORMAT: Respond with ONLY raw JSON matching this schema, no markdown code "
            f"fences, no commentary before or after:\n{json.dumps(schema)}")


def build_debug_generation_config() -> Dict[str, Any]:
    return dict(thinking_config=genai.types.ThinkingConfig(include_thoughts=True))
