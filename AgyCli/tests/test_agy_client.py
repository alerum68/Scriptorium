"""
Tests for agy_client.py against fake_bins/ stand-ins - no live agy binary or network
access. FAKE_AGY_MODE (read by fake_agy.bat) is set per-test via monkeypatch.setenv.
"""

import time
from pathlib import Path

import pytest

import agy_client

FAKE_BINS_DIR = Path(__file__).resolve().parent / "fake_bins"
FAKE_AGY = str(FAKE_BINS_DIR / "fake_agy.bat")
FAKE_AGY_HANG = str(FAKE_BINS_DIR / "fake_agy_hang.bat")

SCHEMA = {"type": "object", "properties": {"sheets": {"type": "array"}}}


def test_resolve_binary_finds_fake_agy():
    resolved = agy_client.resolve_binary(FAKE_AGY)
    assert resolved.lower().endswith("fake_agy.bat")


def test_resolve_binary_raises_clear_error_when_missing():
    with pytest.raises(agy_client.AgyCallError, match="Could not find"):
        agy_client.resolve_binary("definitely_not_a_real_binary_xyz")


def test_call_agy_structured_happy_path(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_AGY_MODE", raising=False)
    result = agy_client.call_agy_structured(
        workspace_dir=tmp_path, model="gemini-3.1-pro-high", prompt="test prompt",
        schema=SCHEMA, cli_bin=FAKE_AGY, timeout_seconds=30,
    )
    assert result.structured_output == {"sheets": [{"page_id": "1", "records": []}]}
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 20
    assert result.usage.thinking_tokens == 5
    assert result.usage.cache_read_tokens == 3
    assert result.usage.total_tokens == 128
    # schema.json should have been written into the workspace dir
    assert (tmp_path / "schema.json").exists()


def test_call_agy_structured_raises_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "fail")
    with pytest.raises(agy_client.AgyCallError, match="fake failure stderr text"):
        agy_client.call_agy_structured(
            workspace_dir=tmp_path, model="gemini-3.1-pro-high", prompt="test prompt",
            schema=SCHEMA, cli_bin=FAKE_AGY, timeout_seconds=30,
        )


def test_call_agy_structured_raises_when_status_success_but_no_structured_output(tmp_path, monkeypatch):
    """Reproduces the real, confirmed-live bug: agy can report status SUCCESS with an
    empty response and no structured_output key at all. Must not be treated as success."""
    monkeypatch.setenv("FAKE_AGY_MODE", "empty_success")
    with pytest.raises(agy_client.AgyCallError, match="no structured_output"):
        agy_client.call_agy_structured(
            workspace_dir=tmp_path, model="gemini-3.1-pro-high", prompt="test prompt",
            schema=SCHEMA, cli_bin=FAKE_AGY, timeout_seconds=30,
        )


def test_call_agy_structured_raises_on_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "bad_json")
    with pytest.raises(agy_client.AgyCallError, match="not valid JSON"):
        agy_client.call_agy_structured(
            workspace_dir=tmp_path, model="gemini-3.1-pro-high", prompt="test prompt",
            schema=SCHEMA, cli_bin=FAKE_AGY, timeout_seconds=30,
        )


def test_call_agy_structured_timeout_recovers_from_hung_process(tmp_path, monkeypatch):
    """The critical Windows-specific regression test: fake_agy_hang.bat spawns `ping`
    as cmd.exe's own child. A plain proc.kill() would only kill cmd.exe, orphaning the
    ping process holding our stdout/stderr pipe handles, which can make a plain
    proc.wait() hang forever even though the direct child is dead. This proves
    _kill_process_tree's taskkill /T /F actually recovers within a bounded time,
    instead of hanging for the ping's full ~120s duration."""
    monkeypatch.delenv("FAKE_AGY_MODE", raising=False)
    start = time.monotonic()
    with pytest.raises(agy_client.AgyCallError, match="timed out"):
        agy_client.call_agy_structured(
            workspace_dir=tmp_path, model="gemini-3.1-pro-high", prompt="test prompt",
            schema=SCHEMA, cli_bin=FAKE_AGY_HANG, timeout_seconds=2,
        )
    elapsed = time.monotonic() - start
    # Should recover well within the ping's ~120s duration - generous bound to absorb
    # CI/dev-machine variance in how long taskkill itself takes.
    assert elapsed < 30, f"took {elapsed:.1f}s to recover from a hung process - kill-tree logic may not be working"


def test_check_or_prompt_auth_returns_true_on_success(monkeypatch):
    monkeypatch.delenv("FAKE_AGY_MODE", raising=False)
    assert agy_client.check_or_prompt_auth("gemini-3.1-pro-high", cli_bin=FAKE_AGY) is True


def test_check_or_prompt_auth_returns_false_on_failure(monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "fail")
    assert agy_client.check_or_prompt_auth("gemini-3.1-pro-high", cli_bin=FAKE_AGY) is False


# ==========================================
# QUOTA & RATE LIMIT PARSING TESTS
# ==========================================
def test_is_quota_or_rate_limit():
    assert agy_client.is_quota_or_rate_limit("ResourceExhausted: 429 Resource has been exhausted (e.g. check quota).")
    assert agy_client.is_quota_or_rate_limit(
        "Rate limit exceeded for model gemini-3.1-pro-high. Please try again later."
    )
    assert agy_client.is_quota_or_rate_limit("Quota limit reached: reset at 2026-08-02T14:00:00Z")
    assert agy_client.is_quota_or_rate_limit("Too many requests (429)")
    assert not agy_client.is_quota_or_rate_limit("File not found: test.pdf")
    assert not agy_client.is_quota_or_rate_limit("Syntax error on line 5")


def test_parse_quota_reset_wait_seconds():
    # Relative durations in seconds / minutes
    assert agy_client.parse_quota_reset_wait_seconds("Quota exceeded, please retry in 45 seconds") == 45
    assert agy_client.parse_quota_reset_wait_seconds("Rate limit reached. Retry after 2 minutes.") == 120
    assert agy_client.parse_quota_reset_wait_seconds("Please wait 1 hour 15 minutes before retry.") == 4500
    assert agy_client.parse_quota_reset_wait_seconds("retry after 90s") == 90

    # Clock time: e.g. "reset at 14:30:00"
    wait = agy_client.parse_quota_reset_wait_seconds("Quota resets at 23:59:59")
    assert wait is not None and wait > 0

    # Default fallback when no specific time found in rate limit message
    default_wait = agy_client.parse_quota_reset_wait_seconds("429 Too Many Requests: Rate limit exceeded.")
    assert default_wait == 60

    # Non-quota message returns None
    assert agy_client.parse_quota_reset_wait_seconds("Unknown server error 500") is None
