"""
Paleographer-specific glue for the agy (AGY CLI) extraction backend.

Domain-specific counterpart to AntiquarianMCP/agy_client.py's generic, headless "safely
call agy" primitives: this module knows about Paleographer's own scratch-dir staging,
PDF rasterization, retry policy, and usage/cost adaptation, but delegates the actual
subprocess call to agy_client. Never talks to Google's genai SDK - engine.py's
genai-specific machinery (run_with_retries, compute_call_cost, context caching) is not
reused here, since agy's failure surface (subprocess exit code/stderr) and usage shape
are both fundamentally different from the genai SDK's.
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pdfplumber
from PIL import Image

import engine

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from AntiquarianMCP import agy_client  # noqa: E402

# Paleographer's own default - passed explicitly to agy_client on every call, never
# left implicit. Confirmed live that agy's own default (when --model is omitted) is a
# flash-tier model with materially worse OCR quality than this pinned pro-tier model,
# and that shorthand values like "pro"/"flash" are not valid --model values at all -
# only exact IDs from `agy models` work.
DEFAULT_MODEL = "gemini-3.1-pro-high"

DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_SECONDS = 5.0


# ==========================================
# PDF RASTERIZATION
# ==========================================
def rasterize_pdf_to_images(pdf_path: Path, max_dimension: int = 2048,
                            resolution: int = 200) -> List[Image.Image]:
    """One PIL Image per page, via pdfplumber's page.to_image() (pdfplumber is already
    a dependency of engine.py - no new library needed), each then downscaled the same
    way engine.optimize_image downscales a direct image file.

    Used instead of trusting agy's own native PDF file-reading via --add-dir: confirmed
    live that native PDF reading works for light description queries but is unreliable
    for a full schema-constrained extraction on a real multi-page case file - agy's own
    agent tried an internal tool call (most likely for PDF page rendering) that headless
    mode auto-denied, silently returning status SUCCESS with no structured_output at
    all. Rasterizing locally and staging plain images sidesteps that entirely, reusing
    the same mechanism already proven reliable for direct image input."""
    images: List[Image.Image] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_image = page.to_image(resolution=resolution)
            if page_image.original is None:
                continue
            img = page_image.original.convert("RGB")
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            images.append(img)
    return images


# ==========================================
# EXTRACTION CALL
# ==========================================
def call_agy_extract(images: List[Image.Image], schema: Dict[str, Any], prompt_text: str,
                     model: str = DEFAULT_MODEL,
                     cli_bin: str = agy_client.DEFAULT_CLI_BIN,
                     timeout_seconds: int = agy_client.DEFAULT_TIMEOUT_SECONDS
                     ) -> agy_client.AgyStructuredResult:
    """Provisions a fresh scratch dir, saves each image in `images` into it
    (page_001.jpg, page_002.jpg, ... - a single-image call is just a length-1 list),
    calls agy_client.call_agy_structured with a prompt that names every staged file,
    and always cleans the scratch dir up in a finally block regardless of outcome.

    For a PDF input, `images` comes from rasterize_pdf_to_images; for a direct image
    file, `images` is a length-1 list from engine.optimize_image."""
    if not images:
        raise ValueError("call_agy_extract requires at least one image")

    scratch_dir = Path(tempfile.mkdtemp(prefix="paleographer_agy_"))
    try:
        filenames = []
        for index, img in enumerate(images, start=1):
            filename = f"page_{index:03d}.jpg"
            img.convert("RGB").save(scratch_dir / filename, "JPEG", quality=90)
            filenames.append(filename)

        if len(filenames) == 1:
            file_instruction = f"The file to process is staged in your workspace directory as: {filenames[0]}."
        else:
            file_instruction = (
                f"The files to process are staged in your workspace directory, in page "
                f"order, as: {', '.join(filenames)}."
            )
        full_prompt = (
            f"{prompt_text}\n\n{file_instruction} Open/view "
            f"{'it' if len(filenames) == 1 else 'them, in order,'} and produce the JSON "
            f"output now, matching the schema exactly."
        )

        return agy_client.call_agy_structured(
            workspace_dir=scratch_dir, model=model, prompt=full_prompt, schema=schema,
            cli_bin=cli_bin, timeout_seconds=timeout_seconds,
        )
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


# ==========================================
# CHUNKED EXTRACTION (large multi-page documents)
# ==========================================
# Confirmed live: a single agy call staging all 38 pages of a real multi-page case file
# failed 3/3 times with the same "status SUCCESS, no structured_output" failure that's
# only intermittent at small scale (1-3 images) - a real reliability limit on how many
# images one call can reliably hold, not just bad luck. Chunking sidesteps it by reusing
# the exact mechanism already proven reliable at small scale, repeated across sections.
DEFAULT_CHUNK_SIZE = 10


# noinspection DuplicatedCode
def _pop_trailing_cutoff_record(sheets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Same logic as Paleographer.py's own pop_trailing_cutoff_record, reimplemented
    here rather than imported to avoid a circular import (Paleographer.py imports this
    module, not the other way around) - see that function's docstring for the full
    rationale. Operates on one chunk's sheets instead of one file's."""
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


def _consumed_leading_continuation(sheets: List[Dict[str, Any]]) -> bool:
    """See Paleographer.py's consumed_leading_continuation - same logic, chunk-scoped."""
    if not sheets:
        return False
    first_records = sheets[0].get("records", [])
    return bool(first_records and first_records[0].get("continues_from_previous_image"))


def _reattach_leftover_record(sheets: List[Dict[str, Any]], leftover: Dict[str, Any]) -> None:
    """See Paleographer.py's reattach_leftover_record - same logic, chunk-scoped."""
    sheet_info = leftover.pop("_pending_sheet_info", {}) or {}
    sheets.insert(0, {"page_id": sheet_info.get("page_id", ""),
                      "document_metadata": sheet_info.get("document_metadata", {}), "records": [leftover]})


def _consolidate_chunked_result(all_sheets: List[Dict[str, Any]], collection_title: Optional[str],
                                schema: Dict[str, Any], model: str, cli_bin: str,
                                timeout_seconds: int) -> agy_client.AgyStructuredResult:
    """Final pass over the FULL concatenated result from every chunk, looking for
    cross-chunk duplicates or inconsistencies a section boundary could introduce (e.g.
    the same application split oddly, or genuinely re-appearing near a boundary) -
    something the model can only catch by seeing the whole document's extracted data
    together, which no single chunk call ever does.

    Deliberately text-only: works from the already-extracted JSON, not the original
    images again (much cheaper/faster than a real extraction call, and avoids agy's
    own image-viewing tool-use entirely for this pass). The JSON is staged as a file in
    the workspace rather than embedded directly in the prompt string, since a large
    multi-page document's full extracted JSON can exceed a safe command-line argument
    length - the same --add-dir file-staging mechanism already proven reliable for
    images works just as well for a plain text/JSON file."""
    scratch_dir = Path(tempfile.mkdtemp(prefix="paleographer_agy_consolidate_"))
    try:
        records_path = scratch_dir / "extracted_records.json"
        records_path.write_text(
            json.dumps({"collection_title": collection_title, "sheets": all_sheets}, ensure_ascii=False),
            encoding="utf-8",
        )
        prompt = (
            "The file extracted_records.json in your workspace directory contains records "
            "already extracted from a multi-page document, processed in sequential "
            "page-order sections due to its length. Read it, then review the FULL set for "
            "consistency:\n"
            "- If any two records actually describe the SAME application (e.g. duplicated "
            "across a section boundary), merge them into one record, keeping the most "
            "complete data from either.\n"
            "- Do not invent, drop, or renumber any distinct record that is genuinely "
            "separate - only merge genuine duplicates.\n"
            "- Preserve every field exactly as given except where merging duplicates "
            "requires combining two records' data.\n\n"
            "Output the final, reconciled JSON now, matching the schema exactly."
        )
        return agy_client.call_agy_structured(
            workspace_dir=scratch_dir, model=model, prompt=prompt, schema=schema,
            cli_bin=cli_bin, timeout_seconds=timeout_seconds,
        )
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def call_agy_extract_chunked(images: List[Image.Image], schema: Dict[str, Any], prompt_text: str,
                             model: str = DEFAULT_MODEL,
                             cli_bin: str = agy_client.DEFAULT_CLI_BIN,
                             timeout_seconds: int = agy_client.DEFAULT_TIMEOUT_SECONDS,
                             chunk_size: int = DEFAULT_CHUNK_SIZE) -> agy_client.AgyStructuredResult:
    """For documents with more pages than one agy call can reliably hold: splits
    `images` into chunk_size-page groups, processes each sequentially with page
    continuity threaded between chunks (engine.build_continuation_context - the exact
    same mechanism Paleographer.py already uses between separate files, applied here
    between sections of one file), then attempts one final consolidation pass over the
    full concatenated result to catch cross-chunk duplicates. If consolidation itself
    fails after retries (confirmed live: it can, even when every individual chunk
    succeeded - reasoning over the whole combined result at once hits the same
    scale-related reliability limit chunking was built to avoid), falls back to
    returning the unconsolidated-but-fully-extracted, continuity-stitched result rather
    than raising and discarding every real, successfully-extracted page. Returns a
    single AgyStructuredResult in the same shape a non-chunked call would have produced
    either way (usage summed across every call actually made, including a failed
    consolidation attempt's own token spend).

    len(images) <= chunk_size is just call_agy_extract - no chunking/consolidation
    overhead for anything that already fits in one call."""
    if len(images) <= chunk_size:
        return call_agy_extract(images, schema, prompt_text, model=model, cli_bin=cli_bin,
                                timeout_seconds=timeout_seconds)

    chunks = [images[i:i + chunk_size] for i in range(0, len(images), chunk_size)]
    all_sheets: List[Dict[str, Any]] = []
    pending_continuation: Optional[Dict[str, Any]] = None
    collection_title: Optional[str] = None
    usage_totals = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
                    "cache_read_tokens": 0, "total_tokens": 0}

    def _add_usage(usage: agy_client.AgyUsage) -> None:
        usage_totals["input_tokens"] += usage.input_tokens
        usage_totals["output_tokens"] += usage.output_tokens
        usage_totals["thinking_tokens"] += usage.thinking_tokens
        usage_totals["cache_read_tokens"] += usage.cache_read_tokens
        usage_totals["total_tokens"] += usage.total_tokens

    for chunk_index, chunk_images in enumerate(chunks, start=1):
        print(f"   [agy] section {chunk_index}/{len(chunks)} ({len(chunk_images)} page(s))...",
              end="", flush=True)
        chunk_prompt = (
            f"{prompt_text}\n\nNOTE: This is section {chunk_index} of {len(chunks)} of a "
            f"longer multi-page document, processed in sequential page-order sections "
            f"due to its length. Apply the same PAGE CONTINUITY rules across this section "
            f"boundary as you would between any two consecutive images."
        )
        chunk_prompt += engine.build_continuation_context(pending_continuation)

        target_imgs = chunk_images
        target_prm = chunk_prompt

        def call_fn() -> agy_client.AgyStructuredResult:
            return call_agy_extract(target_imgs, schema, target_prm, model=model, cli_bin=cli_bin,
                                    timeout_seconds=timeout_seconds)

        chunk_result = run_with_agy_retries(call_fn)
        print(" done.", flush=True)
        _add_usage(chunk_result.usage)
        if collection_title is None:
            collection_title = chunk_result.structured_output.get("collection_title")

        chunk_sheets = chunk_result.structured_output.get("sheets", [])
        if pending_continuation is not None and not _consumed_leading_continuation(chunk_sheets):
            _reattach_leftover_record(chunk_sheets, pending_continuation)
        pending_continuation = _pop_trailing_cutoff_record(chunk_sheets)
        all_sheets.extend(chunk_sheets)

    if pending_continuation is not None:
        # The very last chunk's last record was cut off with no further section to
        # check against - save it rather than silently drop it, same as
        # Paleographer.py's own end-of-run handling.
        _reattach_leftover_record(all_sheets, pending_continuation)

    unconsolidated_output = {"collection_title": collection_title, "sheets": all_sheets}

    # The consolidation pass is a nice-to-have cross-chunk sanity check, not something
    # that may throw away every real, successfully-extracted page if IT happens to be
    # the one call that hits agy's scale-related reliability limit (confirmed live: on
    # a real 38-page document, all 38 individual extraction calls succeeded but the
    # single consolidation call - reasoning over the full combined result at once - hit
    # the same "status SUCCESS, no structured_output" failure 5/5 times). Never let a
    # failure here discard already-successful extraction work.
    print(f"   [agy] consolidating {len(chunks)} section(s)...", end="", flush=True)

    def consolidation_call_fn() -> agy_client.AgyStructuredResult:
        return _consolidate_chunked_result(all_sheets, collection_title, schema, model,
                                           cli_bin, timeout_seconds)

    try:
        final_result = run_with_agy_retries(consolidation_call_fn)
    except RuntimeError as e:
        print(f" FAILED ({e}) - using unconsolidated per-section result instead.", flush=True)
        return agy_client.AgyStructuredResult(
            structured_output=unconsolidated_output,
            usage=agy_client.AgyUsage(**usage_totals),
        )

    print(" done.", flush=True)
    _add_usage(final_result.usage)

    return agy_client.AgyStructuredResult(
        structured_output=final_result.structured_output,
        usage=agy_client.AgyUsage(**usage_totals),
    )


# ==========================================
# RETRY POLICY
# ==========================================
def run_with_agy_retries(call_fn: Callable[[], Any], max_retries: int = DEFAULT_MAX_RETRIES,
                         backoff_seconds: float = DEFAULT_BACKOFF_SECONDS) -> Any:
    """Retries agy_client.AgyCallError with linear backoff, and automatically pauses
    execution when quota or rate limit reset times are encountered, restarting gracefully
    at the exact same spot once the reset time arrives. Fails fast on AgyBinaryNotFoundError."""
    last_error: Optional[Exception] = None
    failure_count = 0
    quota_pause_count = 0
    while failure_count < max_retries:
        try:
            return call_fn()
        except agy_client.AgyBinaryNotFoundError as e:
            raise RuntimeError(str(e)) from e
        except agy_client.AgyCallError as e:
            last_error = e
            err_msg = str(e)
            if agy_client.is_quota_or_rate_limit(err_msg):
                quota_pause_count += 1
                wait_time = agy_client.parse_quota_reset_wait_seconds(err_msg)
                if wait_time is not None and wait_time > 0:
                    agy_client.pause_for_quota_reset(wait_time, reason=f"agy quota limit hit: {err_msg[:80]}")
                    continue
                else:
                    pause_wait = 30.0 * float(2 ** (quota_pause_count - 1))
                    agy_client.pause_for_quota_reset(pause_wait, reason=f"agy rate limit hit: {err_msg[:80]}")
                    continue

            failure_count += 1
            if failure_count < max_retries:
                wait = backoff_seconds * failure_count
                print(f"   [!] agy call failed (attempt {failure_count}/{max_retries}): {e} "
                      f"Retrying in {wait:.0f}s...", flush=True)
                time.sleep(wait)
    err_str = str(last_error) if last_error is not None else "unknown error"
    raise RuntimeError(f"agy call failed after {max_retries} attempts: {err_str}") from last_error


# ==========================================
# COST ADAPTATION
# ==========================================
def adapt_agy_usage_to_call_cost(usage: agy_client.AgyUsage) -> "engine.CallCost":
    """Maps agy's usage shape onto engine.CallCost so Paleographer.py's existing
    record_cost/print_cost_line machinery can treat both engines uniformly.
    call_cost is always 0.0 - subscription-covered, not routed through
    engine.compute_call_cost/CostConfig at all, since there's no metered per-token
    price to configure for this path."""
    return engine.CallCost(
        in_tokens=usage.input_tokens,
        out_tokens=usage.output_tokens,
        cached_tokens=usage.cache_read_tokens,
        thoughts_tokens=usage.thinking_tokens,
        call_cost=0.0,
    )
