import json
from types import SimpleNamespace

import pytest

# noinspection PyUnresolvedReferences
import engine
# noinspection PyUnresolvedReferences
from google.genai import errors


def make_client_error(code: int, message: str) -> errors.ClientError:
    return errors.ClientError(code, {"error": {"message": message, "status": message.split(":")[0]}})


# ==========================================
# parse_type_config
# ==========================================
# event_types always comes from Commissioner.models.FACT_DEFINITIONS (see
# engine.load_event_types), not from a .pmt's own front matter or any file these tests
# control - so every case below asserts against the real, full FACT_DEFINITIONS vocabulary.


def test_parse_type_config_reads_front_matter_and_substitutes_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PARISH_NAME", "St. Test Parish")
    pmt_path = tmp_path / "TestType.pmt"
    pmt_path.write_text(
        "---\n"
        "roles:\n"
        "  \"1\": {name: \"Primary\", semantic: primary}\n"
        "defaults:\n"
        "  record: {event_place: \"Testville\"}\n"
        "metadata_fields:\n"
        "  Church: \"${TEST_PARISH_NAME}\"\n"
        "---\n\n"
        "You are an expert genealogist.\n",
        encoding="utf-8",
    )

    cfg = engine.parse_type_config(pmt_path)

    assert cfg.name == "TestType"
    assert cfg.event_types["Baptism"] == {"id_prefix": "BAPM-"}
    assert cfg.event_types["Marriage"] == {"id_prefix": "MARR-"}
    assert len(cfg.event_types) == len(engine.FACT_DEFINITIONS)
    assert cfg.roles == {"1": {"name": "Primary", "semantic": "primary"}}
    assert cfg.defaults == {"record": {"event_place": "Testville"}}
    assert cfg.metadata_fields == {"Church": "St. Test Parish"}
    assert cfg.prose.strip() == "You are an expert genealogist."


def test_parse_type_config_defaults_batch_threshold(tmp_path):
    pmt_path = tmp_path / "Minimal.pmt"
    pmt_path.write_text("---\nroles: {}\n---\nSome prose.", encoding="utf-8")

    cfg = engine.parse_type_config(pmt_path)

    assert cfg.batch_page_threshold == engine.BATCH_PAGE_THRESHOLD


def test_parse_type_config_defaults_role_validation_to_closed(tmp_path):
    """Matches Commissioner.record_registry's own default (role_validation_mode =
    front_matter.get("role_validation", "closed")) - a .pmt with no role_validation key
    at all must resolve to closed, not open."""
    pmt_path = tmp_path / "Minimal.pmt"
    pmt_path.write_text("---\nroles: {}\n---\nSome prose.", encoding="utf-8")

    cfg = engine.parse_type_config(pmt_path)

    assert cfg.role_validation == "closed"


def test_parse_type_config_reads_explicit_role_validation(tmp_path):
    pmt_path = tmp_path / "Open.pmt"
    pmt_path.write_text("---\nroles: {}\nrole_validation: open\n---\nSome prose.", encoding="utf-8")

    cfg = engine.parse_type_config(pmt_path)

    assert cfg.role_validation == "open"


def test_parse_type_config_handles_no_front_matter(tmp_path):
    pmt_path = tmp_path / "NoFrontMatter.pmt"
    pmt_path.write_text("Just prose, no front matter at all.", encoding="utf-8")

    cfg = engine.parse_type_config(pmt_path)

    # event_types is sourced from FACT_DEFINITIONS regardless of front matter,
    # so it's populated here even though this .pmt has no front matter of its own.
    assert cfg.event_types["Baptism"] == {"id_prefix": "BAPM-"}
    assert cfg.event_types["Marriage"] == {"id_prefix": "MARR-"}
    assert len(cfg.event_types) == len(engine.FACT_DEFINITIONS)
    assert cfg.prose == "Just prose, no front matter at all."


def test_build_vocabulary_summary_lists_sorted_values(tmp_path):
    pmt_path = tmp_path / "Vocab.pmt"
    pmt_path.write_text(
        "---\n"
        "roles: {\"1\": {name: \"Primary\"}, \"2\": {name: \"Father\"}}\n"
        "---\nPrompt body.",
        encoding="utf-8",
    )
    cfg = engine.parse_type_config(pmt_path)

    summary = engine.build_vocabulary_summary(cfg)

    assert "Baptism" in summary and "Marriage" in summary
    assert "Father, Primary" in summary


def test_build_vocabulary_summary_appends_context_when_present(tmp_path):
    pmt_path = tmp_path / "Vocab.pmt"
    pmt_path.write_text(
        "---\n"
        "roles:\n"
        "  \"1\": {name: \"Primary\", context: \"Baptism: the child. Burial: the deceased.\"}\n"
        "  \"2\": {name: \"Father\"}\n"
        "---\nPrompt body.",
        encoding="utf-8",
    )
    cfg = engine.parse_type_config(pmt_path)

    summary = engine.build_vocabulary_summary(cfg)

    assert "Primary (Baptism: the child. Burial: the deceased.)" in summary
    # A role with no context stays a bare name, no empty parens.
    assert "Father," in summary and "Father (" not in summary


def test_build_vocabulary_summary_closed_mode_uses_choose_exactly_one_phrasing(tmp_path):
    """Regression test for existing Parish/Scrip behavior - role_validation: closed (or
    unset) must keep the original 'choose exactly one' phrasing unchanged."""
    pmt_path = tmp_path / "Closed.pmt"
    pmt_path.write_text(
        "---\n"
        "role_validation: closed\n"
        "roles: {\"1\": {name: \"Primary\"}, \"2\": {name: \"Father\"}}\n"
        "---\nPrompt body.",
        encoding="utf-8",
    )
    cfg = engine.parse_type_config(pmt_path)

    summary = engine.build_vocabulary_summary(cfg)

    assert ("- role_name (choose exactly one per participant - where a role's own meaning\n"
            "  depends on the event type, that's noted in parentheses): Father, Primary") in summary
    assert "use one of these when it applies" not in summary


def test_build_vocabulary_summary_open_mode_uses_escape_hatch_phrasing(tmp_path):
    """Census.pmt's open mode (Fix 3): the model must be told the role list isn't
    exhaustive and that anything else is recorded verbatim as an association, never
    coerced into one of the listed family roles."""
    pmt_path = tmp_path / "Open.pmt"
    pmt_path.write_text(
        "---\n"
        "role_validation: open\n"
        "roles: {\"1\": {name: \"Head\"}, \"2\": {name: \"Wife\"}}\n"
        "---\nPrompt body.",
        encoding="utf-8",
    )
    cfg = engine.parse_type_config(pmt_path)

    summary = engine.build_vocabulary_summary(cfg)

    assert ("- role_name: use one of these when it applies - Head, Wife - and record the\n"
            "  source's own relationship term verbatim when none of these fit (e.g. Boarder,\n"
            "  Servant, Roomer, Grandson). Only the listed names carry a family relationship;\n"
            "  anything else is recorded as-is and treated as an association, not a family link.") in summary
    assert "choose exactly one per participant" not in summary


def test_load_event_types_flattens_person_and_family_buckets():
    result = engine.load_event_types()

    assert result["Baptism"] == {"id_prefix": "BAPM-"}
    assert result["Marriage"] == {"id_prefix": "MARR-"}
    assert len(result) == len(engine.FACT_DEFINITIONS)


# ==========================================
# build_merged_schema
# ==========================================
# noinspection DuplicatedCode
def test_build_merged_schema_injects_extra_fields_without_mutating_core():
    core = {
        "properties": {
            "sheets": {"items": {"properties": {"records": {"items": {"properties": {
                "type_specific_fields": {"type": "object", "properties": {}},
                "participants": {"items": {"properties": {
                    "type_specific_fields": {"type": "object", "properties": {}}
                }}}
            }}}}}}
        }
    }
    extra_fields = {"record": [{"name": "scrip_number", "type": "string"}],
                    "participant": [{"name": "marital_status", "type": "string"}]}

    merged = engine.build_merged_schema(core, extra_fields)

    rec_props = merged["properties"]["sheets"]["items"]["properties"]["records"]["items"]["properties"]
    assert rec_props["type_specific_fields"]["properties"] == {
        "scrip_number": {"type": "string", "nullable": True}}
    part_props = rec_props["participants"]["items"]["properties"]
    assert part_props["type_specific_fields"]["properties"] == {
        "marital_status": {"type": "string", "nullable": True}}

    # The original core schema must be untouched (deep copy, not aliasing).
    original_rec_props = core["properties"]["sheets"]["items"]["properties"]["records"]["items"]["properties"]
    assert original_rec_props["type_specific_fields"]["properties"] == {}


# noinspection DuplicatedCode
def test_build_merged_schema_maps_dict_field_type_to_json_schema_object():
    """Census.pmt declares `unmapped: {type: dict}` - "dict" is not a valid JSON-Schema
    type (the correct token is "object"), so inject() must translate it rather than pass
    the raw front-matter token straight through (Fix 1)."""
    core = {
        "properties": {
            "sheets": {"items": {"properties": {"records": {"items": {"properties": {
                "type_specific_fields": {"type": "object", "properties": {}},
                "participants": {"items": {"properties": {
                    "type_specific_fields": {"type": "object", "properties": {}}
                }}}
            }}}}}}
        }
    }
    extra_fields = {"participant": [{"name": "unmapped", "type": "dict"}]}

    merged = engine.build_merged_schema(core, extra_fields)

    rec_props = merged["properties"]["sheets"]["items"]["properties"]["records"]["items"]["properties"]
    part_props = rec_props["participants"]["items"]["properties"]
    assert part_props["type_specific_fields"]["properties"]["unmapped"] == {"type": "object", "nullable": True}


# ==========================================
# compute_call_cost
# ==========================================
def test_compute_call_cost_applies_cache_discount():
    usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=200,
                            cached_content_token_count=500, thoughts_token_count=100)
    cost_cfg = engine.CostConfig(cost_per_1m_in=2.0, cost_per_1m_out=12.0, cache_discount_multiplier=0.10)

    result = engine.compute_call_cost(usage, cost_cfg)

    expected_cached = (500 / 1_000_000) * (2.0 * 0.10)
    expected_in = (1000 / 1_000_000) * 2.0
    expected_out = (300 / 1_000_000) * 12.0
    assert result.call_cost == pytest.approx(expected_cached + expected_in + expected_out)
    assert result.in_tokens == 1000
    assert result.cached_tokens == 500


def test_compute_call_cost_handles_none_usage():
    cost_cfg = engine.CostConfig(cost_per_1m_in=2.0, cost_per_1m_out=12.0, cache_discount_multiplier=0.10)
    result = engine.compute_call_cost(None, cost_cfg)
    assert result.call_cost == 0.0
    assert result.in_tokens == 0


# ==========================================
# run_with_retries
# ==========================================
def test_run_with_retries_succeeds_first_try():
    assert engine.run_with_retries(lambda: "ok") == "ok"


def test_run_with_retries_retries_json_decode_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def call_fn():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise json.JSONDecodeError("bad json", "doc", 0)
        return "recovered"

    assert engine.run_with_retries(call_fn) == "recovered"
    assert attempts["count"] == 2


def test_run_with_retries_gives_up_after_max_json_retries(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)

    def call_fn():
        raise json.JSONDecodeError("bad json", "doc", 0)

    with pytest.raises(RuntimeError, match="Malformed JSON"):
        engine.run_with_retries(call_fn, max_json_retries=2)


def test_run_with_retries_backs_off_on_rate_limit_then_succeeds(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(engine.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    attempts = {"count": 0}

    def call_fn():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise make_client_error(429, "RESOURCE_EXHAUSTED: retry in 3.0s")
        return "recovered"

    assert engine.run_with_retries(call_fn) == "recovered"
    assert sleep_calls == [pytest.approx(4.5)]  # 3.0s from the message + 1.5s fixed pad


def test_run_with_retries_raises_daily_quota_exhausted_immediately(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)

    def call_fn():
        raise make_client_error(429, "PerDay quota exceeded")

    with pytest.raises(engine.DailyQuotaExhausted):
        engine.run_with_retries(call_fn)


def test_run_with_retries_reraises_unrecognized_client_error(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)

    def call_fn():
        raise make_client_error(400, "INVALID_ARGUMENT: bad request")

    with pytest.raises(errors.ClientError):
        engine.run_with_retries(call_fn)


# ==========================================
# Batch API (mocked client)
# ==========================================
class FakeBatches:
    def __init__(self):
        self.created_with = None
        self.jobs = {}

    def create(self, *, model, src, config):
        self.created_with = {"model": model, "src": src, "config": config}
        name = "batches/fake-job-1"
        self.jobs[name] = SimpleNamespace(name=name)
        return self.jobs[name]

    def get(self, *, name):
        return self.jobs[name]


class FakeClient:
    def __init__(self):
        self.batches = FakeBatches()


def test_submit_batch_job_returns_job_name():
    client = FakeClient()
    job_name = engine.submit_batch_job(client, "AI Assistant-3.1-pro-preview", requests=["fake-request"])
    assert job_name == "batches/fake-job-1"
    assert client.batches.created_with["model"] == "AI Assistant-3.1-pro-preview"


def test_check_batch_jobs_splits_completed_and_pending():
    # noinspection PyUnresolvedReferences
    from google.genai import types

    client = FakeClient()
    client.batches.jobs["batches/done"] = SimpleNamespace(state=types.JobState.JOB_STATE_SUCCEEDED)
    client.batches.jobs["batches/still-running"] = SimpleNamespace(state=types.JobState.JOB_STATE_RUNNING)
    client.batches.jobs["batches/failed"] = SimpleNamespace(state=types.JobState.JOB_STATE_FAILED)

    pending = [{"job_name": "batches/done"}, {"job_name": "batches/still-running"},
               {"job_name": "batches/failed"}]
    completed, still_pending = engine.check_batch_jobs(client, pending)

    assert completed == [{"job_name": "batches/done"}]
    assert still_pending == [{"job_name": "batches/still-running"}]
    # The failed job is dropped entirely: not completed, not retried.


def test_retrieve_batch_results_pairs_source_file_with_response():
    client = FakeClient()
    good_response = SimpleNamespace(text='{"sheets": []}')
    client.batches.jobs["batches/done"] = SimpleNamespace(
        dest=SimpleNamespace(inlined_responses=[
            SimpleNamespace(metadata={"source_file": "page1.jpg"}, error=None, response=good_response),
            SimpleNamespace(metadata={"source_file": "page2.jpg"}, error="boom", response=None),
        ])
    )

    results = engine.retrieve_batch_results(client, "batches/done")

    assert len(results) == 1
    source_file, response = results[0]
    assert source_file == "page1.jpg"
    assert response is good_response


def test_retrieve_batch_results_handles_empty_destination():
    client = FakeClient()
    client.batches.jobs["batches/empty"] = SimpleNamespace(dest=None)
    assert engine.retrieve_batch_results(client, "batches/empty") == []


# ==========================================
# has_usable_text_layer
# ==========================================
def test_has_usable_text_layer_false_for_missing_file(tmp_path):
    assert engine.has_usable_text_layer(tmp_path / "does-not-exist.pdf") is False


# ==========================================
# optimize_pdf_for_upload
# ==========================================
def _make_minimal_pdf(path):
    import fitz
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()


def test_optimize_pdf_for_upload_never_mutates_original(tmp_path):
    original = tmp_path / "source.pdf"
    _make_minimal_pdf(original)
    original_bytes = original.read_bytes()

    result_path = engine.optimize_pdf_for_upload(original)

    assert original.read_bytes() == original_bytes
    if result_path != original:
        result_path.unlink()


def test_optimize_pdf_for_upload_returns_a_cleaned_up_temp_copy(tmp_path):
    original = tmp_path / "source.pdf"
    _make_minimal_pdf(original)

    result_path = engine.optimize_pdf_for_upload(original)

    assert result_path != original
    assert result_path.exists()
    result_path.unlink()


def test_optimize_pdf_for_upload_falls_back_to_original_on_error(tmp_path, monkeypatch):
    original = tmp_path / "source.pdf"
    _make_minimal_pdf(original)

    def boom(*_args, **_kwargs):
        raise RuntimeError("optimize failed")

    monkeypatch.setattr(engine, "optimize_pdf", boom)

    result_path = engine.optimize_pdf_for_upload(original)

    assert result_path == original


# ==========================================
# build_continuation_context
# ==========================================
def test_build_continuation_context_empty_when_nothing_pending():
    assert engine.build_continuation_context(None) == ""


def test_build_continuation_context_includes_key_fields():
    pending = {
        "record_number": "44", "year": "1850", "event_type": "Baptism",
        "citation_text": "Le 12 decembre, j'ai baptise",
        "citation_details": "On December 12, I baptized",
        "participants": [{"role_name": "Primary", "std_given": "Jean", "std_surname": "Gagne"}],
    }
    text = engine.build_continuation_context(pending)
    assert "CONTINUATION FROM PREVIOUS IMAGE" in text
    assert "record_number: 44" in text
    assert "year: 1850" in text
    assert "Le 12 decembre, j'ai baptise" in text
    assert "Jean" in text and "Gagne" in text
