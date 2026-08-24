"""Tests for LAC.py's Commissioner-scaffold building blocks: MASTER_DB path resolution
(mirroring Paleographer.py's own resolve_setting), load/save, and scaffold-sheet
deduplicated append. See the Voyageur-Parish-Scrip-scaffold design spec."""
from pathlib import Path
import json
import os
import queue
import threading

import pytest

import LAC
import lac_client


class _FakeProcess:
    """Synchronous stand-in for multiprocessing.Process - runs the worker entrypoint
    in-process instead of spawning a real subprocess, so tests can drive real SUCCESS/
    START/FAIL messages through the controller's queue-consuming logic without the
    pickling/spawn complications (or real concurrency) of an actual child process.
    is_alive() is False by construction: start() already ran the worker to completion
    synchronously before returning, so there's never a moment this fake is still "running"."""
    def __init__(self, target=None, args=()):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)

    def join(self, timeout=None):
        pass

    def terminate(self):
        pass

    def is_alive(self):
        return False


def test_resolve_generic_setting_prefers_prefixed_key(monkeypatch):
    monkeypatch.setenv("CHURCH_MASTER_DB_NAME", "parish_register.json")
    monkeypatch.delenv("MASTER_DB_NAME", raising=False)
    assert LAC.resolve_generic_setting("Parish", "MASTER_DB_NAME") == "parish_register.json"


def test_resolve_generic_setting_falls_back_to_generic_key(monkeypatch):
    monkeypatch.delenv("CHURCH_MASTER_DB_NAME", raising=False)
    monkeypatch.setenv("MASTER_DB_NAME", "fallback.json")
    assert LAC.resolve_generic_setting("Parish", "MASTER_DB_NAME") == "fallback.json"


def test_resolve_master_db_path_matches_paleographer_convention(monkeypatch, tmp_path):
    monkeypatch.setenv("CHURCH_MASTER_DB_NAME", "parish_register.json")
    monkeypatch.setenv("JSON_DIR", "JSON")
    path = LAC.resolve_master_db_path("Parish", str(tmp_path))
    assert path == str(tmp_path / "JSON" / "parish_register.json")


def test_resolve_master_db_path_raises_on_empty_master_db_name(monkeypatch, tmp_path):
    monkeypatch.delenv("CHURCH_MASTER_DB_NAME", raising=False)
    monkeypatch.delenv("MASTER_DB_NAME", raising=False)
    with pytest.raises(RuntimeError, match="MASTER_DB_NAME"):
        LAC.resolve_master_db_path("Parish", str(tmp_path))


def test_load_master_db_returns_default_shape_when_missing(tmp_path):
    master_db_path = str(tmp_path / "does_not_exist.json")
    data = LAC.load_master_db(master_db_path, "Test Collection", "Parish")
    assert data == {
        "collection_title": "Test Collection", "record_type_name": "Parish", "sheets": [],
        "total_spent": 0.0, "total_pages_processed": 0, "pending_batch_jobs": [],
    }


def test_save_and_load_master_db_round_trip(tmp_path):
    master_db_path = str(tmp_path / "JSON" / "parish_register.json")
    data = {"collection_title": "Test", "record_type_name": "Parish", "sheets": [{"page_id": "p1"}]}
    LAC.save_master_db(master_db_path, data)
    assert LAC.load_master_db(master_db_path, "Test", "Parish") == data


def test_save_master_db_leaves_existing_file_untouched_on_write_failure(tmp_path, monkeypatch):
    master_db_path = str(tmp_path / "parish_register.json")
    LAC.save_master_db(master_db_path, {"sheets": ["original"]})

    def fail_replace(_self, _target):
        raise OSError("disk full")

    monkeypatch.setattr(LAC.Path, "replace", fail_replace)

    with pytest.raises(OSError):
        LAC.save_master_db(master_db_path, {"sheets": ["new-data-that-should-not-land"]})

    with open(master_db_path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"sheets": ["original"]}
    assert not (tmp_path / "parish_register.tmp").exists()


def test_load_master_db_falls_back_to_default_shape_on_malformed_json(tmp_path, capsys):
    master_db_path = tmp_path / "parish_register.json"
    master_db_path.write_text("{not valid json", encoding="utf-8")

    data = LAC.load_master_db(str(master_db_path), "Test Collection", "Parish")

    assert data == {
        "collection_title": "Test Collection", "record_type_name": "Parish", "sheets": [],
        "total_spent": 0.0, "total_pages_processed": 0, "pending_batch_jobs": [],
    }
    assert "[WARN]" in capsys.readouterr().out


def test_save_and_load_checkpoint_round_trip(tmp_path):
    checkpoint_path = str(tmp_path / "checkpoint.json")
    data = {"pids": ["pid1"], "downloaded_pids": ["pid1"], "failed_pids": {}}
    LAC.save_checkpoint(checkpoint_path, data)
    assert LAC.load_checkpoint(checkpoint_path) == data


def test_save_checkpoint_leaves_existing_file_untouched_on_write_failure(tmp_path, monkeypatch):
    checkpoint_path = str(tmp_path / "checkpoint.json")
    LAC.save_checkpoint(checkpoint_path, {"pids": ["original"]})

    def fail_replace(_self, _target):
        raise OSError("disk full")

    monkeypatch.setattr(LAC.Path, "replace", fail_replace)

    with pytest.raises(OSError):
        LAC.save_checkpoint(checkpoint_path, {"pids": ["new-data-that-should-not-land"]})

    with open(checkpoint_path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"pids": ["original"]}
    assert not (tmp_path / "checkpoint.tmp").exists()


def test_load_checkpoint_falls_back_to_default_shape_on_malformed_json(tmp_path, capsys):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text("{not valid json", encoding="utf-8")

    data = LAC.load_checkpoint(str(checkpoint_path))

    assert data == {"pids": [], "downloaded_pids": [], "failed_pids": {}}
    assert "[WARN]" in capsys.readouterr().out


def test_append_scaffold_sheets_adds_new_sheets():
    master_data = {"sheets": []}
    new_sheets = [
        {"page_id": "p1", "document_metadata": {"file_name": "abc.jpg"}, "records": []},
        {"page_id": "p2", "document_metadata": {"file_name": "def.jpg"}, "records": []},
    ]
    LAC.append_scaffold_sheets(master_data, new_sheets)
    assert len(master_data["sheets"]) == 2


def test_append_scaffold_sheets_dedups_by_file_name():
    existing_sheet = {"page_id": "p1", "document_metadata": {"file_name": "abc.jpg"}, "records": []}
    master_data = {"sheets": [existing_sheet]}
    duplicate_sheet = {"page_id": "p1-retry", "document_metadata": {"file_name": "abc.jpg"}, "records": []}
    LAC.append_scaffold_sheets(master_data, [duplicate_sheet])
    assert master_data["sheets"] == [existing_sheet]


def test_resolve_record_type_maps_parish_and_scrip():
    assert LAC._resolve_record_type("parish") == "Parish"
    assert LAC._resolve_record_type("scrip") == "Scrip"


def test_resolve_record_type_exits_on_empty(capsys):
    with pytest.raises(SystemExit):
        LAC._resolve_record_type("")
    assert "[ERROR]" in capsys.readouterr().out


def test_download_volume_assets_writes_one_scaffold_sheet_per_asset(monkeypatch, tmp_path):
    def fake_download_pid_bundle(pid, _media_dir):
        return {
            "pid": pid, "lac_catalog_title": "Test", "reel_numbers": [], "series_code": "RG15-D-II-8-b",
            "source_documents": [
                {"document_type": "Affidavit", "media_path": str(tmp_path / pid / "asset1.jpg"),
                 "lac_pid": pid, "lac_asset_id": "asset1", "source": "LAC"},
            ],
        }
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")

    result = LAC.download_volume_assets(["pid1"], str(tmp_path), checkpoint_path,
                                        master_db_path, "Scrip", "Test Collection")

    assert result["downloaded_pids"] == ["pid1"]
    master_data = LAC.load_master_db(master_db_path, "Test Collection", "Scrip")
    assert len(master_data["sheets"]) == 1
    assert master_data["sheets"][0]["document_metadata"]["file_name"] == "asset1.jpg"
    assert master_data["sheets"][0]["records"][0]["participants"] == []


def test_download_volume_assets_skips_already_downloaded_pid(monkeypatch, tmp_path):
    calls = []

    def fake_download_pid_bundle(pid, _media_dir):
        calls.append(pid)
        return {"source_documents": []}
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    LAC.save_checkpoint(checkpoint_path, {"pids": ["pid1"], "downloaded_pids": ["pid1"], "failed_pids": {}})
    master_db_path = str(tmp_path / "scrip_records.json")

    LAC.download_volume_assets(["pid1"], str(tmp_path), checkpoint_path, master_db_path, "Scrip", "Test")

    assert calls == []


def test_download_volume_assets_records_failure_without_writing_scaffold(monkeypatch, tmp_path):
    def fake_download_pid_bundle(_pid, _media_dir):
        raise lac_client.LacCallError("boom")
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")

    result = LAC.download_volume_assets(["pid1"], str(tmp_path), checkpoint_path, master_db_path, "Scrip", "Test")

    assert result["failed_pids"] == {"pid1": "boom"}
    assert not os.path.exists(master_db_path)


def test_download_volume_assets_persists_source_documents_in_checkpoint(monkeypatch, tmp_path):
    def fake_download_pid_bundle(pid, _media_dir):
        return {
            "source_documents": [
                {"media_path": str(tmp_path / pid / "asset1.jpg"), "lac_asset_id": "asset1"},
            ],
        }
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")

    LAC.download_volume_assets(["pid1"], str(tmp_path), checkpoint_path, master_db_path, "Scrip", "Test")

    checkpoint = LAC.load_checkpoint(checkpoint_path)
    assert checkpoint["pid_documents"]["pid1"][0]["lac_asset_id"] == "asset1"


def test_download_volume_assets_reseeds_scaffold_for_already_downloaded_pid_without_refetch(monkeypatch, tmp_path):
    checkpoint_path = str(tmp_path / "checkpoint.json")
    LAC.save_checkpoint(checkpoint_path, {
        "pids": ["pid1"], "downloaded_pids": ["pid1"], "failed_pids": {},
        "pid_documents": {"pid1": [{"media_path": str(tmp_path / "pid1" / "asset1.jpg"), "lac_asset_id": "asset1"}]},
    })

    def fail_if_called(_pid, _media_dir):
        raise AssertionError("should not re-fetch an already-downloaded pid")
    monkeypatch.setattr(LAC, "download_pid_bundle", fail_if_called)

    master_db_path = str(tmp_path / "scrip_records.json")  # simulates a MASTER_DB reset - file doesn't exist yet

    LAC.download_volume_assets(["pid1"], str(tmp_path), checkpoint_path, master_db_path, "Scrip", "Test")

    master_data = LAC.load_master_db(master_db_path, "Test", "Scrip")
    assert len(master_data["sheets"]) == 1
    assert master_data["sheets"][0]["document_metadata"]["file_name"] == "asset1.jpg"


def test_download_volume_assets_multiworker_no_op_when_all_downloaded(tmp_path):
    checkpoint_path = str(tmp_path / "checkpoint.json")
    LAC.save_checkpoint(checkpoint_path, {"pids": ["pid1"], "downloaded_pids": ["pid1"], "failed_pids": {}})
    master_db_path = str(tmp_path / "scrip_records.json")

    result = LAC.download_volume_assets_multiworker(["pid1"], str(tmp_path), checkpoint_path,
                                                    master_db_path, "Scrip", "Test", max_workers=2)

    assert result["downloaded_pids"] == ["pid1"]


def test_download_volume_assets_multiworker_reseeds_scaffold_for_already_downloaded_pid(tmp_path):
    checkpoint_path = str(tmp_path / "checkpoint.json")
    LAC.save_checkpoint(checkpoint_path, {
        "pids": ["pid1"], "downloaded_pids": ["pid1"], "failed_pids": {},
        "pid_documents": {"pid1": [{"media_path": str(tmp_path / "pid1" / "asset1.jpg"), "lac_asset_id": "asset1"}]},
    })
    master_db_path = str(tmp_path / "scrip_records.json")

    result = LAC.download_volume_assets_multiworker(["pid1"], str(tmp_path), checkpoint_path,
                                                    master_db_path, "Scrip", "Test", max_workers=2)

    assert result["downloaded_pids"] == ["pid1"]
    master_data = LAC.load_master_db(master_db_path, "Test", "Scrip")
    assert len(master_data["sheets"]) == 1


def test_download_volume_assets_multiworker_writes_scaffold_sheet_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(LAC.mp, "Process", _FakeProcess)

    def fake_download_pid_bundle(pid, _media_dir):
        return {
            "source_documents": [
                {"media_path": str(tmp_path / pid / "asset1.jpg"), "lac_asset_id": "asset1"},
            ],
        }
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")

    result = LAC.download_volume_assets_multiworker(["pid1"], str(tmp_path), checkpoint_path,
                                                    master_db_path, "Scrip", "Test", max_workers=1)

    assert result["downloaded_pids"] == ["pid1"]
    master_data = LAC.load_master_db(master_db_path, "Test", "Scrip")
    assert len(master_data["sheets"]) == 1
    assert master_data["sheets"][0]["document_metadata"]["file_name"] == "asset1.jpg"


def test_process_worker_messages_updates_to_latest_message_per_worker():
    """Regression test for the exact race #18 found: if a worker's SUCCESS(A) and
    START(B) both land in the same drain batch, active_workers must reflect B (the
    worker's actual current task) afterward, not stall on A's now-irrelevant state -
    otherwise a watchdog check right after could time out the worker over a task (A)
    it already finished, while the task it's ACTUALLY running (B) stays invisible and,
    if the watchdog fires, gets killed with no requeue and no failure record."""
    active_workers = {0: {"process": None, "pid": None, "start_time": 0}}
    downloaded = set()
    failed = {}
    pid_documents = {}
    task_queue = queue.Queue()
    rate_lock = threading.Lock()
    current_delay = type("FakeManagerValue", (), {"value": 0.3})()
    appended = []

    stale_start_time = 1000.0
    fresh_start_time = 5000.0
    messages = [
        ("START", 0, "pidA", stale_start_time),
        ("SUCCESS", 0, "pidA", {"source_documents": []}),
        ("START", 0, "pidB", fresh_start_time),
    ]

    delta, changed = LAC._process_worker_messages(
        messages, active_workers, downloaded, failed, pid_documents,
        task_queue, rate_lock, current_delay, appended.append)

    assert active_workers[0]["pid"] == "pidB"
    assert active_workers[0]["start_time"] == fresh_start_time
    assert downloaded == {"pidA"}
    assert delta == 1
    assert changed is True
    assert appended == [[]]


def test_process_worker_messages_requeues_on_403_and_backs_off():
    active_workers = {0: {"process": None, "pid": "pidA", "start_time": 100.0}}
    downloaded = set()
    failed = {}
    pid_documents = {}
    task_queue = queue.Queue()
    rate_lock = threading.Lock()
    current_delay = type("FakeManagerValue", (), {"value": 0.3})()

    delta, changed = LAC._process_worker_messages(
        [("403_ERROR", 0, "pidA", "HTTP 403")], active_workers, downloaded, failed,
        pid_documents, task_queue, rate_lock, current_delay, lambda docs: None)

    assert active_workers[0]["pid"] is None
    assert task_queue.get_nowait() == "pidA"
    assert current_delay.value == pytest.approx(0.6)
    assert delta == 0
    assert changed is False


def test_download_volume_assets_multiworker_batches_writes_and_flushes_before_returning(monkeypatch, tmp_path):
    monkeypatch.setattr(LAC.mp, "Process", _FakeProcess)

    def fake_download_pid_bundle(pid, _media_dir):
        return {"source_documents": [
            {"media_path": str(tmp_path / pid / f"{pid}_asset1.jpg"), "lac_asset_id": "asset1"},
        ]}
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    save_master_db_calls = []
    real_save_master_db = LAC.save_master_db

    def counting_save_master_db(path, data):
        save_master_db_calls.append(1)
        real_save_master_db(path, data)
    monkeypatch.setattr(LAC, "save_master_db", counting_save_master_db)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")
    pids = [f"pid{i}" for i in range(5)]

    result = LAC.download_volume_assets_multiworker(pids, str(tmp_path), checkpoint_path,
                                                    master_db_path, "Scrip", "Test", max_workers=1)

    # With _FakeProcess, one worker synchronously drains all 5 tasks before the
    # controller loop even starts, so the whole backlog lands in one drain batch -
    # save_master_db should be called far fewer times than once per PID.
    assert len(save_master_db_calls) < 5
    assert sorted(result["downloaded_pids"]) == sorted(pids)
    master_data = LAC.load_master_db(master_db_path, "Test", "Scrip")
    assert len(master_data["sheets"]) == 5


def test_download_volume_assets_multiworker_flushes_partial_progress_on_crash(monkeypatch, tmp_path):
    """Caught by /code-review: batching removed the old code's per-event flush, which was
    the only thing guaranteeing a crash mid-harvest didn't lose already-downloaded PIDs'
    checkpoint/master_db state. With _FakeProcess draining every task synchronously before
    the controller loop even starts, all 5 PIDs' SUCCESS messages land in one drain batch -
    forcing append_scaffold_sheets to raise on the 3rd one simulates an unexpected crash
    partway through processing that batch. The first 2 PIDs' in-memory state was already
    mutated before the raise; a bare "while ... finally: flush" must still persist it."""
    monkeypatch.setattr(LAC.mp, "Process", _FakeProcess)

    def fake_download_pid_bundle(pid, _media_dir):
        return {"source_documents": [
            {"media_path": str(tmp_path / pid / f"{pid}_asset1.jpg"), "lac_asset_id": "asset1"},
        ]}
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    real_append_scaffold_sheets = LAC.append_scaffold_sheets
    call_count = {"n": 0}

    def crash_on_third_call(m_data, new_sheets):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated crash mid-harvest")
        real_append_scaffold_sheets(m_data, new_sheets)
    monkeypatch.setattr(LAC, "append_scaffold_sheets", crash_on_third_call)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")
    pids = [f"pid{i}" for i in range(5)]

    with pytest.raises(RuntimeError, match="simulated crash mid-harvest"):
        LAC.download_volume_assets_multiworker(pids, str(tmp_path), checkpoint_path,
                                               master_db_path, "Scrip", "Test", max_workers=1)

    # Message handling marks a pid "downloaded" before appending its scaffold sheet, so
    # pid2 (the one whose append call raised) is already in downloaded_pids even though
    # its sheet never made it into master_data - a real, pre-existing gap between the two,
    # not introduced by this fix. What this fix guarantees is that none of it is lost: all
    # 3 completed-or-in-flight pids are flushed to the checkpoint, and pid2's source
    # documents are captured in pid_documents too (pid_documents[pid] is also set before
    # the crash-prone append call) - both are exactly what the reseed loop at the top of a
    # future run needs to self-heal pid2's missing sheet without re-downloading anything.
    checkpoint = LAC.load_checkpoint(checkpoint_path)
    assert sorted(checkpoint["downloaded_pids"]) == ["pid0", "pid1", "pid2"]
    assert "pid2" in checkpoint["pid_documents"]
    master_data = LAC.load_master_db(master_db_path, "Test", "Scrip")
    assert len(master_data["sheets"]) == 2

    # Resume with a non-crashing fake: pid2's sheet must self-heal via the reseed loop,
    # and pid3/pid4 (never reached before the crash) must still get downloaded.
    monkeypatch.setattr(LAC, "append_scaffold_sheets", real_append_scaffold_sheets)
    result = LAC.download_volume_assets_multiworker(pids, str(tmp_path), checkpoint_path,
                                                    master_db_path, "Scrip", "Test", max_workers=1)
    assert sorted(result["downloaded_pids"]) == sorted(pids)
    master_data = LAC.load_master_db(master_db_path, "Test", "Scrip")
    assert len(master_data["sheets"]) == 5


def test_download_volume_assets_multiworker_joins_still_alive_workers_on_exit(monkeypatch, tmp_path):
    join_calls = []

    class LingeringProcess(_FakeProcess):
        def __init__(self, target=None, args=()):
            super().__init__(target, args)
            self._alive = True

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            join_calls.append(timeout)
            self._alive = False

    monkeypatch.setattr(LAC.mp, "Process", LingeringProcess)

    def fake_download_pid_bundle(pid, _media_dir):
        return {"source_documents": [{"media_path": str(tmp_path / pid / "asset1.jpg"), "lac_asset_id": "asset1"}]}
    monkeypatch.setattr(LAC, "download_pid_bundle", fake_download_pid_bundle)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")

    LAC.download_volume_assets_multiworker(["pid1"], str(tmp_path), checkpoint_path,
                                           master_db_path, "Scrip", "Test", max_workers=1)

    assert len(join_calls) >= 1


def test_retrieve_volume_threads_master_db_params_to_sequential_path(monkeypatch, tmp_path):
    monkeypatch.setattr(LAC, "retrieve_volume_pids",
                        lambda vol, cookies, cp_path, archival_number: ["pid1"])
    monkeypatch.setattr(LAC, "download_pid_bundle", lambda _pid, _media_dir: {
        "source_documents": [{"media_path": str(tmp_path / "asset1.jpg"), "lac_asset_id": "asset1"}],
    })
    checkpoint_path = str(tmp_path / "checkpoint.json")
    master_db_path = str(tmp_path / "scrip_records.json")

    LAC.retrieve_volume("1325", {}, str(tmp_path), checkpoint_path, master_db_path, "Scrip", "Test Collection")

    master_data = LAC.load_master_db(master_db_path, "Test Collection", "Scrip")
    assert len(master_data["sheets"]) == 1


def test_download_images_writes_scaffold_sheet_per_canvas(monkeypatch, tmp_path):
    manifest_data = {
        "sequences": [{"canvases": [
            {"images": [{"resource": {"@id": "https://example.com/img1.jpg"}}]},
        ]}],
    }

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, timeout=None):
            _ = (url, timeout)
            return FakeResponse()

    monkeypatch.setattr(LAC.requests, "Session", lambda: FakeSession())

    out_dir = str(tmp_path / "images")
    os.makedirs(out_dir, exist_ok=True)
    master_db_path = str(tmp_path / "parish_register.json")

    LAC.download_images(manifest_data, out_dir, "roll1", master_db_path, "Parish", "Test Collection")

    master_data = LAC.load_master_db(master_db_path, "Test Collection", "Parish")
    assert len(master_data["sheets"]) == 1
    assert master_data["sheets"][0]["document_metadata"]["file_name"] == "roll1_0001.jpg"
    assert master_data["sheets"][0]["page_id"] == "roll1_0001"


def test_download_images_batches_master_db_flushes(monkeypatch, tmp_path):
    """PERF-2: save_master_db must not be called once per canvas - for a 720-page reel
    that re-serializes the entire (growing) master_data dict 720 times. It should flush
    every FLUSH_EVERY_N canvases plus once more at the end for any remainder."""
    manifest_data = {
        "sequences": [{"canvases": [
            {"images": [{"resource": {"@id": f"https://example.com/img{i}.jpg"}}]}
            for i in range(1, 13)
        ]}],
    }

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, timeout=None):
            _ = (url, timeout)
            return FakeResponse()

    monkeypatch.setattr(LAC.requests, "Session", lambda: FakeSession())

    out_dir = str(tmp_path / "images")
    os.makedirs(out_dir, exist_ok=True)
    master_db_path = str(tmp_path / "parish_register.json")

    real_save = LAC.save_master_db
    call_count = 0

    def counting_save(path, data):
        nonlocal call_count
        call_count += 1
        real_save(path, data)

    monkeypatch.setattr(LAC, "save_master_db", counting_save)

    LAC.download_images(manifest_data, out_dir, "roll1", master_db_path, "Parish", "Test Collection")

    assert call_count == 2

    master_data = LAC.load_master_db(master_db_path, "Test Collection", "Parish")
    assert len(master_data["sheets"]) == 12


def test_download_images_tracks_failure_and_continues_with_remaining_canvases(monkeypatch, tmp_path):
    manifest_data = {
        "sequences": [{"canvases": [
            {"images": [{"resource": {"@id": "https://example.com/bad.jpg"}}]},
            {"images": [{"resource": {"@id": "https://example.com/good.jpg"}}]},
        ]}],
    }

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, timeout=None):
            _ = timeout
            if "bad" in url:
                raise ConnectionError("network down")
            return FakeResponse()

    monkeypatch.setattr(LAC.requests, "Session", lambda: FakeSession())

    out_dir = str(tmp_path / "images")
    os.makedirs(out_dir, exist_ok=True)
    master_db_path = str(tmp_path / "parish_register.json")

    failed = LAC.download_images(manifest_data, out_dir, "roll1", master_db_path, "Parish", "Test Collection")

    assert failed == {"1": "network down"}
    master_data = LAC.load_master_db(master_db_path, "Test Collection", "Parish")
    assert len(master_data["sheets"]) == 1
    assert master_data["sheets"][0]["document_metadata"]["file_name"] == "roll1_0002.jpg"


def test_download_images_does_not_claim_success_when_some_canvases_failed(monkeypatch, tmp_path, capsys):
    """Caught by /code-review: the completion message printed unconditionally, right after
    the failure warning, misleadingly claiming "completed successfully!" even when the
    failed dict this same diff added already had the data to know better."""
    manifest_data = {
        "sequences": [{"canvases": [
            {"images": [{"resource": {"@id": "https://example.com/bad.jpg"}}]},
        ]}],
    }

    class FakeSession:
        def get(self, url, timeout=None):
            _ = (url, timeout)
            raise ConnectionError("network down")

    monkeypatch.setattr(LAC.requests, "Session", lambda: FakeSession())

    out_dir = str(tmp_path / "images")
    os.makedirs(out_dir, exist_ok=True)
    master_db_path = str(tmp_path / "parish_register.json")

    LAC.download_images(manifest_data, out_dir, "roll1", master_db_path, "Parish", "Test Collection")

    out = capsys.readouterr().out
    assert "completed successfully" not in out


def test_download_images_leaves_no_truncated_file_when_write_fails(monkeypatch, tmp_path):
    manifest_data = {
        "sequences": [{"canvases": [
            {"images": [{"resource": {"@id": "https://example.com/img1.jpg"}}]},
        ]}],
    }

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, timeout=None):
            _ = (url, timeout)
            return FakeResponse()

    monkeypatch.setattr(LAC.requests, "Session", lambda: FakeSession())

    def fail_replace(_self, _target):
        raise OSError("disk full")

    monkeypatch.setattr(LAC.Path, "replace", fail_replace)

    out_dir = str(tmp_path / "images")
    os.makedirs(out_dir, exist_ok=True)
    master_db_path = str(tmp_path / "parish_register.json")

    failed = LAC.download_images(manifest_data, out_dir, "roll1", master_db_path, "Parish", "Test Collection")

    assert "1" in failed
    assert not (Path(out_dir) / "roll1_0001.jpg").exists()


def test_download_images_dedups_scaffold_when_image_already_on_disk(monkeypatch, tmp_path):
    manifest_data = {
        "sequences": [{"canvases": [
            {"images": [{"resource": {"@id": "https://example.com/img1.jpg"}}]},
        ]}],
    }
    out_dir = str(tmp_path / "images")
    os.makedirs(out_dir, exist_ok=True)
    (Path(out_dir) / "roll1_0001.jpg").write_bytes(b"already-downloaded")

    # Voyageur/.env sets GATHER_ON_COLLISION=overwrite which would leak into this test;
    # clear it so the default ('skip') applies and the dedup check can fire.
    monkeypatch.delenv("GATHER_ON_COLLISION", raising=False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("should not re-download an existing image")
    monkeypatch.setattr(LAC.requests, "Session", lambda: type("FakeSession", (), {"get": fail_if_called})())

    master_db_path = str(tmp_path / "parish_register.json")

    LAC.download_images(manifest_data, out_dir, "roll1", master_db_path, "Parish", "Test Collection")

    master_data = LAC.load_master_db(master_db_path, "Test Collection", "Parish")
    assert len(master_data["sheets"]) == 1
