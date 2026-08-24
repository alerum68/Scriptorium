"""
Tests for agy_engine.py. agy_client.call_agy_structured is monkeypatched throughout -
these tests are about agy_engine's own scratch-dir staging, retry policy, and cost
adaptation, not the subprocess layer (already covered in
AgyCli/tests/test_agy_client.py). rasterize_pdf_to_images is tested against a
real, tiny, locally-generated PDF (via PyMuPDF, already a project dependency) since
that logic is cheap to exercise for real rather than mock.
"""

from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image

# noinspection PyUnresolvedReferences
import agy_engine
from AgyCli import agy_client

SCHEMA = {"type": "object", "properties": {}}


def _make_result(structured_output=None):
    return agy_client.AgyStructuredResult(
        structured_output=structured_output or {"sheets": []},
        usage=agy_client.AgyUsage(input_tokens=100, output_tokens=20, thinking_tokens=5,
                                  cache_read_tokens=3, total_tokens=128),
    )


def _make_image(color="white", size=(50, 50)):
    return Image.new("RGB", size, color)


# ==========================================
# call_agy_extract
# ==========================================
def test_call_agy_extract_stages_single_image_and_cleans_up(monkeypatch):
    captured = {}

    def fake_call_agy_structured(*, workspace_dir, model, prompt, **_kwargs):
        captured["workspace_dir"] = Path(workspace_dir)
        captured["staged_files"] = sorted(p.name for p in Path(workspace_dir).iterdir())
        captured["prompt"] = prompt
        captured["model"] = model
        return _make_result()

    monkeypatch.setattr(agy_client, "call_agy_structured", fake_call_agy_structured)

    result = agy_engine.call_agy_extract([_make_image()], SCHEMA, "base prompt", model="gemini-3.1-pro-high")

    assert result.structured_output == {"sheets": []}
    assert captured["staged_files"] == ["page_001.jpg"]
    assert "page_001.jpg" in captured["prompt"]
    assert captured["model"] == "gemini-3.1-pro-high"
    # scratch dir must be cleaned up after the call
    assert not captured["workspace_dir"].exists()


def test_call_agy_extract_stages_multiple_images_in_order(monkeypatch):
    captured = {}

    def fake_call_agy_structured(*, workspace_dir, prompt, **_kwargs):
        captured["staged_files"] = sorted(p.name for p in Path(workspace_dir).iterdir())
        captured["prompt"] = prompt
        return _make_result()

    monkeypatch.setattr(agy_client, "call_agy_structured", fake_call_agy_structured)

    images = [_make_image("white"), _make_image("black"), _make_image("gray")]
    agy_engine.call_agy_extract(images, SCHEMA, "base prompt")

    assert captured["staged_files"] == ["page_001.jpg", "page_002.jpg", "page_003.jpg"]
    assert "page_001.jpg, page_002.jpg, page_003.jpg" in captured["prompt"]


def test_call_agy_extract_cleans_up_scratch_dir_even_on_failure(monkeypatch):
    workspace_holder = {}

    def fake_call_agy_structured(*, workspace_dir, **_kwargs):
        workspace_holder["dir"] = Path(workspace_dir)
        raise agy_client.AgyCallError("boom")

    monkeypatch.setattr(agy_client, "call_agy_structured", fake_call_agy_structured)

    with pytest.raises(agy_client.AgyCallError):
        agy_engine.call_agy_extract([_make_image()], SCHEMA, "base prompt")

    assert not workspace_holder["dir"].exists()


def test_call_agy_extract_requires_at_least_one_image():
    with pytest.raises(ValueError):
        agy_engine.call_agy_extract([], SCHEMA, "base prompt")


# ==========================================
# run_with_agy_retries
# ==========================================
def test_run_with_agy_retries_succeeds_first_try():
    calls = []

    def call_fn():
        calls.append(1)
        return "ok"

    assert agy_engine.run_with_agy_retries(call_fn) == "ok"
    assert len(calls) == 1


def test_run_with_agy_retries_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: None)
    attempts = {"count": 0}

    def call_fn():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise agy_client.AgyCallError("transient failure")
        return "recovered"

    assert agy_engine.run_with_agy_retries(call_fn, max_retries=5) == "recovered"
    assert attempts["count"] == 3


def test_run_with_agy_retries_raises_runtime_error_after_exhausting(monkeypatch):
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: None)

    def call_fn():
        raise agy_client.AgyCallError("always fails")

    with pytest.raises(RuntimeError, match="agy call failed after 3 attempts"):
        agy_engine.run_with_agy_retries(call_fn, max_retries=3)


def test_run_with_agy_retries_fails_fast_on_missing_binary(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    attempts = {"count": 0}

    def call_fn():
        attempts["count"] += 1
        raise agy_client.AgyBinaryNotFoundError("agy not on PATH")

    with pytest.raises(RuntimeError, match="agy not on PATH"):
        agy_engine.run_with_agy_retries(call_fn, max_retries=5)

    assert attempts["count"] == 1, "should not retry a missing binary"
    assert sleep_calls == [], "should not sleep/backoff for a missing binary"


# ==========================================
# adapt_agy_usage_to_call_cost
# ==========================================
def test_adapt_agy_usage_to_call_cost_maps_fields_and_zeroes_cost():
    usage = agy_client.AgyUsage(input_tokens=111, output_tokens=22, thinking_tokens=33,
                                cache_read_tokens=44, total_tokens=210)
    cost = agy_engine.adapt_agy_usage_to_call_cost(usage)
    assert cost.in_tokens == 111
    assert cost.out_tokens == 22
    assert cost.thoughts_tokens == 33
    assert cost.cached_tokens == 44
    assert cost.call_cost == 0.0


# ==========================================
# rasterize_pdf_to_images (real pdfplumber/PyMuPDF, no mocking)
# ==========================================
def test_rasterize_pdf_to_images_returns_one_image_per_page(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Test page {i + 1}")
    doc.save(str(pdf_path))
    doc.close()

    images = agy_engine.rasterize_pdf_to_images(pdf_path)

    assert len(images) == 3
    for img in images:
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        assert max(img.size) <= 2048


def test_rasterize_pdf_to_images_respects_max_dimension(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page(width=1000, height=1000)
    page.insert_text((72, 72), "Test")
    doc.save(str(pdf_path))
    doc.close()

    images = agy_engine.rasterize_pdf_to_images(pdf_path, max_dimension=300, resolution=200)

    assert len(images) == 1
    assert max(images[0].size) <= 300


# ==========================================
# call_agy_extract_chunked
# ==========================================
def _sheet(page_id, records):
    return {"page_id": page_id, "document_metadata": {}, "records": records}


def _record(number, continues_on_next_image=False, continues_from_previous_image=False):
    return {"record_number": number, "continues_on_next_image": continues_on_next_image,
            "continues_from_previous_image": continues_from_previous_image}


def test_call_agy_extract_chunked_delegates_when_under_chunk_size(monkeypatch):
    calls = []

    def fake_call_agy_extract(imgs, _schema=None, _prompt_text=None, **_kwargs):
        calls.append(len(imgs))
        return _make_result({"collection_title": "T", "sheets": [_sheet("1", [_record("1")])]})

    monkeypatch.setattr(agy_engine, "call_agy_extract", fake_call_agy_extract)

    images = [_make_image() for _ in range(5)]
    result = agy_engine.call_agy_extract_chunked(images, SCHEMA, "prompt", chunk_size=10)

    assert calls == [5], "5 images with chunk_size=10 should be a single non-chunked call"
    assert result.structured_output["sheets"][0]["page_id"] == "1"


def test_call_agy_extract_chunked_splits_and_calls_consolidation(monkeypatch):
    extract_calls = []
    consolidate_calls = []

    def fake_call_agy_extract(imgs, _schema=None, _prompt_text=None, **_kwargs):
        extract_calls.append(len(imgs))
        chunk_num = len(extract_calls)
        return _make_result({"collection_title": "Test Collection",
                             "sheets": [_sheet(f"chunk{chunk_num}", [_record(str(chunk_num))])]})

    def fake_consolidate(all_sheets, collection_title, _schema=None, _model=None, _cli_bin=None, _timeout_seconds=None):
        consolidate_calls.append({"num_sheets": len(all_sheets), "collection_title": collection_title})
        return _make_result({"collection_title": collection_title, "sheets": all_sheets})

    monkeypatch.setattr(agy_engine, "call_agy_extract", fake_call_agy_extract)
    monkeypatch.setattr(agy_engine, "_consolidate_chunked_result", fake_consolidate)
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: None)

    images = [_make_image() for _ in range(25)]  # 3 chunks of 10, 10, 5 at chunk_size=10
    result = agy_engine.call_agy_extract_chunked(images, SCHEMA, "prompt", chunk_size=10)

    assert extract_calls == [10, 10, 5]
    assert len(consolidate_calls) == 1
    assert consolidate_calls[0]["num_sheets"] == 3
    assert consolidate_calls[0]["collection_title"] == "Test Collection"
    # usage summed across all 3 extraction calls + the 1 consolidation call
    assert result.usage.input_tokens == 100 * 4
    assert result.usage.total_tokens == 128 * 4


def test_call_agy_extract_chunked_threads_continuation_across_chunks(monkeypatch):
    """A record cut off at the end of chunk 1 that chunk 2 claims to continue must end
    up as ONE record (chunk 2's, since that's the completed version), not two."""
    chunk_responses = [
        {"collection_title": "T", "sheets": [_sheet("c1", [
            _record("44", continues_on_next_image=True),
        ])]},
        {"collection_title": "T", "sheets": [_sheet("c2", [
            _record("44", continues_from_previous_image=True),
            _record("45"),
        ])]},
    ]

    # noinspection DuplicatedCode
    def fake_call_agy_extract(_imgs, _schema=None, _prompt_text=None, **_kwargs):
        return _make_result(chunk_responses.pop(0))

    def fake_consolidate(all_sheets, collection_title, _schema=None, _model=None, _cli_bin=None, _timeout_seconds=None):
        return _make_result({"collection_title": collection_title, "sheets": all_sheets})

    monkeypatch.setattr(agy_engine, "call_agy_extract", fake_call_agy_extract)
    monkeypatch.setattr(agy_engine, "_consolidate_chunked_result", fake_consolidate)
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: None)

    images = [_make_image() for _ in range(15)]  # 2 chunks at chunk_size=10
    result = agy_engine.call_agy_extract_chunked(images, SCHEMA, "prompt", chunk_size=10)

    all_records = [r for s in result.structured_output["sheets"] for r in s["records"]]
    record_numbers = sorted(r["record_number"] for r in all_records)
    assert record_numbers == ["44", "45"], "record 44 must appear once (merged), not twice"


def test_call_agy_extract_chunked_falls_back_when_consolidation_fails(monkeypatch):
    """Confirmed live: consolidation can fail after retries even when every individual
    chunk succeeded (reasoning over the whole combined result at once hits the same
    scale limit chunking exists to avoid). That must never discard the real,
    successfully-extracted per-chunk data - it must fall back to the unconsolidated
    result instead of raising."""
    chunk_responses = [
        {"collection_title": "T", "sheets": [_sheet("c1", [_record("1")])]},
        {"collection_title": "T", "sheets": [_sheet("c2", [_record("2")])]},
    ]

    # noinspection DuplicatedCode
    def fake_call_agy_extract(_images, _schema=None, _prompt_text=None, **_kwargs):
        return _make_result(chunk_responses.pop(0))

    def fake_consolidate(_all_sheets, _collection_title, _schema=None, _model=None,
                         _cli_bin=None, _timeout_seconds=None):
        raise agy_client.AgyCallError("status SUCCESS but no structured_output")

    monkeypatch.setattr(agy_engine, "call_agy_extract", fake_call_agy_extract)
    monkeypatch.setattr(agy_engine, "_consolidate_chunked_result", fake_consolidate)
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: None)

    images = [_make_image() for _ in range(15)]
    result = agy_engine.call_agy_extract_chunked(images, SCHEMA, "prompt", chunk_size=10)

    all_records = [r for s in result.structured_output["sheets"] for r in s["records"]]
    record_numbers = sorted(r["record_number"] for r in all_records)
    assert record_numbers == ["1", "2"], "both chunks' real data must survive a consolidation failure"


def test_call_agy_extract_chunked_saves_leftover_when_last_chunk_ends_mid_record(monkeypatch):
    """If the very last chunk's last record is cut off with no further section to check
    against, it must still be saved, not silently dropped."""
    chunk_responses = [
        {"collection_title": "T", "sheets": [_sheet("c1", [_record("1")])]},
        {"collection_title": "T", "sheets": [_sheet("c2", [
            _record("2", continues_on_next_image=True),
        ])]},
    ]

    # noinspection DuplicatedCode
    def fake_call_agy_extract(_images, _schema=None, _prompt_text=None, **_kwargs):
        return _make_result(chunk_responses.pop(0))

    def fake_consolidate(all_sheets, collection_title, _schema=None, _model=None, _cli_bin=None, _timeout_seconds=None):
        return _make_result({"collection_title": collection_title, "sheets": all_sheets})

    monkeypatch.setattr(agy_engine, "call_agy_extract", fake_call_agy_extract)
    monkeypatch.setattr(agy_engine, "_consolidate_chunked_result", fake_consolidate)
    monkeypatch.setattr(agy_engine.time, "sleep", lambda seconds: None)

    images = [_make_image() for _ in range(15)]
    result = agy_engine.call_agy_extract_chunked(images, SCHEMA, "prompt", chunk_size=10)

    all_records = [r for s in result.structured_output["sheets"] for r in s["records"]]
    record_numbers = sorted(r["record_number"] for r in all_records)
    assert record_numbers == ["1", "2"], "record 2 must still be saved even though cut off with nothing after it"
