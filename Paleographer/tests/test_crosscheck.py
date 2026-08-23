"""
Unit tests for Paleographer.py's cross_check_claim_record - wired into the crosscheck CLI
mode by this same cleanup pass (see Paleographer.py main()'s "crosscheck" dispatch branch).
No real network call is ever made: lac_client.search and voyageur_lac.download_pid_bundle
are monkeypatched on the imported module's own attributes.
"""
import importlib
import sys

import pytest


# noinspection DuplicatedCode
@pytest.fixture
def paleographer_module(monkeypatch, tmp_path):
    program_dir = tmp_path / "program"
    (program_dir / "Parish").mkdir(parents=True)
    (program_dir / "JSON").mkdir(parents=True)

    env = {
        "PROGRAM_DIR": str(program_dir),
        "JSON_DIR": "JSON",
        "PALEOGRAPHER_RECORD_TYPE": "Parish",
        "MODEL_NAME": "AI Assistant-test-model",
        "AI_API_KEY": "fake-key-not-used",
        "API_BUDGET": "5.00",
        "COST_PER_1M_INPUT": "0.075",
        "COST_PER_1M_OUTPUT": "0.30",
        "CACHE_DISCOUNT_MULTIPLIER": "0.10",
        "VOLUME_TITLE": "Test Volume",
        "VOLUME_NUM": "1",
        "EXTRACTION_ENGINE": "api",
        "CHURCH_IMAGE_DIR": "Parish",
        "CHURCH_MASTER_DB_NAME": "parish_register.json",
    }
    for key in ("IMAGE_DIR", "MASTER_DB_NAME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["Paleographer.py"])
    monkeypatch.setattr("google.genai.Client", lambda *a, **k: object())

    sys.modules.pop("ScripTools", None)
    return importlib.import_module("ScripTools")


def _record(file_name="", **type_fields):
    claim_number = type_fields.pop("claim_number", "1234")
    record = {
        "document_metadata": {"file_name": file_name},
        "type_specific_fields": {"claim_number": claim_number, **type_fields},
        "participants": [],
    }
    return record


def test_own_pid_resolution_succeeds_and_merges(paleographer_module, monkeypatch):
    module = paleographer_module

    def fake_download(pid, _media_dir, document_type_override=None):
        _ = document_type_override
        assert pid == "1502188"
        return {
            "pid": pid,
            "lac_catalog_title": "Test Claim Title",
            "reel_numbers": ["C-1234"],
            "series_code": "RG15-D-II-8-a",
            "source_documents": [],
        }

    monkeypatch.setattr(module.voyageur_lac, "download_pid_bundle", fake_download)
    monkeypatch.setattr(module.lac_client, "search", lambda _query, _cookies: [])

    record = _record(file_name="BAC-LAC_fonandcol_1502188.pdf")
    result = module.cross_check_claim_record(record, {"cookie": "value"}, "media")

    assert result["lac_pid"] == "1502188"
    assert result["lac_catalog_title"] == "Test Claim Title"
    assert result["type_specific_fields"]["reel_numbers"] == "C-1234"
    assert result["type_specific_fields"]["rg_series_code"] == "RG15-D-II-8-a"
    assert "review_reason" not in result


def test_own_pid_resolution_fails_appends_review_reason(paleographer_module, monkeypatch):
    module = paleographer_module

    def fake_download(pid, _media_dir, document_type_override=None):
        _ = document_type_override
        raise module.lac_client.LacCallError(f"404 for {pid}")

    monkeypatch.setattr(module.voyageur_lac, "download_pid_bundle", fake_download)
    monkeypatch.setattr(module.lac_client, "search", lambda _query, _cookies: [])

    record = _record(file_name="BAC-LAC_fonandcol_1502188.pdf")
    result = module.cross_check_claim_record(record, {"cookie": "value"}, "media")

    assert result["lac_pid"] == "1502188"
    assert "failed to fetch own PID 1502188" in result["review_reason"]


def test_related_pid_search_finds_results_and_appends_source_documents(paleographer_module, monkeypatch):
    module = paleographer_module

    related_entry = {"document_type": "Affidavit", "media_path": "media/999/asset1.pdf",
                     "lac_pid": "999", "lac_asset_id": "asset1", "source": "LAC"}

    def fake_download(pid, _media_dir, document_type_override=None):
        _ = document_type_override
        assert pid == "999"
        return {"pid": pid, "lac_catalog_title": "Related", "reel_numbers": [],
                "series_code": None, "source_documents": [related_entry]}

    monkeypatch.setattr(module.voyageur_lac, "download_pid_bundle", fake_download)
    monkeypatch.setattr(module.lac_client, "search", lambda _query, _cookies: ["999"])

    record = _record(file_name="")  # no own PID - file_name doesn't match the PID convention
    result = module.cross_check_claim_record(record, {"cookie": "value"}, "media")

    assert result["source_documents"] == [related_entry]
    assert "review_reason" not in result


def test_search_auth_error_breaks_loop_with_review_reason(paleographer_module, monkeypatch):
    module = paleographer_module
    call_count = {"n": 0}

    def fake_search(_query, _cookies):
        call_count["n"] += 1
        raise module.lac_client.LacSearchAuthError("cookie expired")

    monkeypatch.setattr(module.lac_client, "search", fake_search)

    record = _record(file_name="", scrip_number="1 to 2")  # two queries if the loop doesn't break
    result = module.cross_check_claim_record(record, {"cookie": "value"}, "media")

    assert call_count["n"] == 1
    assert "search cookie expired/invalid" in result["review_reason"]
