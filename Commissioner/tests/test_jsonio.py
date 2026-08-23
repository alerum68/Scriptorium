"""Tests for Commissioner/jsonio.py (atomic JSON write and checkpoint loading/saving)."""

import json
from pathlib import Path

import pytest

from Commissioner.jsonio import atomic_write_json, load_checkpoint, save_checkpoint


def test_atomic_write_json_writes_content_and_cleans_tmp(tmp_path: Path):
    target = tmp_path / "data.json"
    data = {"key": "value", "count": 42}
    atomic_write_json(target, data)

    assert target.exists()
    assert not (tmp_path / "data.json.tmp").exists()
    with open(target, "r", encoding="utf-8") as f:
        assert json.load(f) == data


def test_atomic_write_json_leaves_original_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "data.json"
    target.write_text('{"initial": true}', encoding="utf-8")

    def broken_dump(*args, **kwargs):
        raise IOError("Disk write failed simulated")

    monkeypatch.setattr(json, "dump", broken_dump)

    with pytest.raises(IOError, match="Disk write failed simulated"):
        atomic_write_json(target, {"initial": False})

    assert json.loads(target.read_text(encoding="utf-8")) == {"initial": True}
    assert not (tmp_path / "data.json.tmp").exists()


def test_load_checkpoint_missing_returns_default(tmp_path: Path):
    target = tmp_path / "nonexistent.json"
    assert load_checkpoint(target, default={"default": 1}) == {"default": 1}
    assert load_checkpoint(target) is None


def test_load_checkpoint_malformed_returns_default(tmp_path: Path, capsys):
    target = tmp_path / "corrupted.json"
    target.write_text("{not valid json", encoding="utf-8")
    assert load_checkpoint(target, default={"fallback": True}) == {"fallback": True}


def test_save_and_load_checkpoint_roundtrip(tmp_path: Path):
    target = tmp_path / "sub" / "cp.json"
    payload = {"pids": ["pid1", "pid2"], "done": True}
    save_checkpoint(target, payload)

    loaded = load_checkpoint(target)
    assert loaded == payload
