"""
AgyCli: safe, synchronous Google AGY CLI (agy) invocation.

A generic, domain-agnostic library any Antiquarian tool can use to call agy - the
subscription-covered CLI backend for AI Assistant, distinct from the metered google-genai API
key path. Knows nothing about Parish.pmt, schemas, or genealogy; Paleographer's own
agy_engine.py builds on top of this.

Despite living in a folder named to mirror the separate, general-purpose
ai-research-mcp tool (C:\\Users\\Jason Cole\\Documents\\PyCharm\\tools\\mcp\\
ai-research-mcp - untouched, still used for ad hoc AI Assistant research queries
across any project), this is a plain importable Python module, not an MCP server:
Paleographer runs as a standalone subprocess with no MCP client present at runtime, so a
real MCP server would be unreachable. The safety mechanics below (resolve binary via
shutil.which, fresh scratch dir per call, stdin=DEVNULL, hard timeout with a full
Windows process-tree kill) mirror that other project's own proven lib.py, reimplemented
synchronously (subprocess, not asyncio) to match how Paleographer actually calls in.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CLI_BIN = "agy"
DEFAULT_TIMEOUT_SECONDS = 240
KILL_WAIT_SECONDS = 10

# check_or_prompt_auth's own default - deliberately generous since it may be waiting on
# a human completing a Google OAuth flow, not a model response.
AUTH_TIMEOUT_SECONDS = 600


class AgyCallError(Exception):
    """Spawn failure, timeout, non-zero exit, or malformed/empty stdout. Callers should
    treat this as the base error type this module ever raises - agy_engine.py's retry
    wrapper catches this specifically."""


class AgyBinaryNotFoundError(AgyCallError):
    """The agy binary itself couldn't be resolved via shutil.which. Distinct from
    AgyCallError so callers (e.g. agy_engine.run_with_agy_retries) can fail fast
    instead of retrying something that will never succeed."""


@dataclass
class AgyUsage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cache_read_tokens: int
    total_tokens: int


@dataclass
class AgyStructuredResult:
    structured_output: Dict[str, Any]
    usage: AgyUsage


# ==========================================
# BINARY RESOLUTION
# ==========================================
def resolve_binary(cli_bin: str = DEFAULT_CLI_BIN) -> str:
    """Resolves cli_bin via shutil.which rather than relying on the OS to find it at
    exec time - on Windows a Node/npm-installed CLI is often a .cmd/.bat shim that some
    subprocess-spawning paths fail to locate by bare name even though it works fine
    typed directly into a shell. Resolving explicitly here means a missing/misnamed
    binary fails with a clear message pointing at an override, instead of a confusing
    low-level spawn error."""
    resolved = shutil.which(cli_bin)
    if not resolved:
        raise AgyBinaryNotFoundError(
            f"Could not find '{cli_bin}' on PATH. If the AGY CLI is installed "
            f"under a different name or location, pass cli_bin= with the correct "
            f"binary name or absolute path."
        )
    return str(Path(resolved).resolve())


# ==========================================
# PROCESS LIFECYCLE
# ==========================================
def _kill_process_tree(proc: subprocess.Popen, wait_seconds: int = KILL_WAIT_SECONDS) -> None:
    """Kills proc and, on Windows, its whole descendant tree - not just the immediate
    child - then waits (with a bounded fallback) for it to actually die.

    This matters because if the CLI being run is (or spawns) a batch file - e.g.
    agy.cmd, or any wrapper script - the process actually launched is cmd.exe, which
    runs the real work as ITS OWN child. A plain proc.kill() only terminates that
    immediate cmd.exe; the grandchild survives as an orphan holding a duplicated handle
    to our stdout/stderr pipes (ordinary Windows handle inheritance, not something we
    opted into). With that grandchild still alive, a plain proc.wait() can hang
    indefinitely even though the direct child is already dead - the exact failure mode
    ai-research-mcp's own lib.py was built to close via taskkill /T /F, ported
    here synchronously."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass  # fall through to proc.kill() below regardless
    try:
        proc.kill()
    except ProcessLookupError:
        pass  # already gone (e.g. taskkill above got it first)

    try:
        proc.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        # Done everything reasonable (taskkill /T /F plus proc.kill()). If the OS still
        # won't report it as dead within wait_seconds, give up waiting rather than hang
        # the whole call forever - the caller already has a clear timeout error to raise.
        pass


def _run_agy(args: List[str], cwd: Path, cli_bin: str, timeout_seconds: int,
             interactive: bool = False) -> subprocess.CompletedProcess:
    """Runs [resolve_binary(cli_bin)] + args. Non-interactive (the default): stdin is
    DEVNULL (never inherited - no auto-approve/skip-permissions flag exists or should be
    used, so if agy ever needs to ask for approval, it has no terminal to ask through;
    that inability is the actual safety boundary, not a documented promise) and stdout/
    stderr are captured. Interactive (check_or_prompt_auth only): stdin/stdout/stderr are
    all inherited from the parent process, so a printed sign-in URL or browser-launch
    behavior is visible to whoever is running Antiquarian.

    Raises AgyCallError on spawn OSError or on timeout (kills the process tree via
    _kill_process_tree first)."""
    resolved_bin = resolve_binary(cli_bin)
    stdio_kwargs: Dict[str, Any] = (
        {} if interactive else
        {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    )
    try:
        proc = subprocess.Popen([resolved_bin] + args, cwd=str(cwd), text=True,
                                encoding="utf-8", errors="replace", **stdio_kwargs)
    except OSError as e:
        raise AgyCallError(f"Failed to launch '{resolved_bin}': {e}") from e

    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        raise AgyCallError(
            f"agy call timed out after {timeout_seconds}s. In non-interactive mode this "
            f"has no attached terminal, so it cannot answer an approval prompt even if "
            f"one came up - a stall here means the safety boundary held, not that "
            f"something broke."
        )

    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


# ==========================================
# STEADY-STATE EXTRACTION CALL (headless, always)
# ==========================================
def call_agy_structured(*, workspace_dir: Path, model: str, prompt: str,
                        schema: Dict[str, Any], cli_bin: str = DEFAULT_CLI_BIN,
                        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> AgyStructuredResult:
    """One synchronous, fully headless agy call. workspace_dir must already contain
    whatever file(s) `prompt` references (an already-staged image, etc.) - this function
    only adds schema.json to it and invokes:
        agy --add-dir <workspace_dir> --model <model> --output-format json
            --json-schema <workspace_dir>/schema.json -p <prompt>
    `model` has no default - every caller must decide explicitly, since agy's own
    default is a flash-tier model with materially worse OCR quality than a pinned
    pro-tier model (confirmed live).

    Parses stdout as agy's wrapper JSON and returns structured_output/usage verbatim -
    NEVER parses the "response" field (it may have trailing junk after the JSON;
    structured_output is already the clean parsed dict).

    Raises AgyCallError on spawn failure, timeout, non-zero exit (message includes
    stderr), or missing/empty structured_output. That last case is NOT redundant with
    checking exit code/"status": confirmed live that agy can report top-level
    "status": "SUCCESS" with an empty "response" and no "structured_output" key at all
    (observed when its own agent tried an internal tool call that headless mode
    auto-denied, having no terminal to prompt through) - "status" alone is never a
    trustworthy success signal here.

    Caller owns workspace_dir's lifecycle (create/cleanup) - this function only writes
    schema.json into it and reads/deletes nothing else."""
    schema_path = workspace_dir / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    args = [
        "--add-dir", str(workspace_dir),
        "--model", model,
        "--output-format", "json",
        "--json-schema", str(schema_path),
        "-p", prompt,
    ]
    result = _run_agy(args, cwd=workspace_dir, cli_bin=cli_bin, timeout_seconds=timeout_seconds)

    if result.returncode != 0:
        raise AgyCallError(
            f"agy exited with code {result.returncode}: {(result.stderr or '').strip() or '(no stderr output)'}"
        )

    try:
        wrapper = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AgyCallError(f"agy stdout was not valid JSON: {e}") from e

    structured_output = wrapper.get("structured_output")
    if not structured_output:
        raise AgyCallError(
            f"agy reported status={wrapper.get('status')!r} but returned no "
            f"structured_output (empty response - likely an internal tool call was "
            f"blocked in headless mode; see call_agy_structured's docstring)."
        )

    usage_raw = wrapper.get("usage") or {}
    usage = AgyUsage(
        input_tokens=usage_raw.get("input_tokens", 0),
        output_tokens=usage_raw.get("output_tokens", 0),
        thinking_tokens=usage_raw.get("thinking_tokens", 0),
        cache_read_tokens=usage_raw.get("cache_read_tokens", 0),
        total_tokens=usage_raw.get("total_tokens", 0),
    )
    return AgyStructuredResult(structured_output=structured_output, usage=usage)


# ==========================================
# AUTH BOOTSTRAP (the only interactive path)
# ==========================================
def check_or_prompt_auth(model: str, cli_bin: str = DEFAULT_CLI_BIN,
                         timeout_seconds: int = AUTH_TIMEOUT_SECONDS) -> bool:
    """Runs a minimal `agy --model <model> -p "reply OK"` call WITHOUT stdin=DEVNULL
    (stdio inherited from the parent, so any printed sign-in URL/instructions are
    visible) and with a generous timeout (default 10 minutes) rather than the short
    extraction timeout, since this may be waiting on a human completing a Google OAuth
    flow, not a model response. Returns True once the call exits 0. This is the ONLY
    function in this module that doesn't force headless/non-interactive behavior -
    call_agy_structured never changes, and stays safe to run unattended once this has
    been called successfully once.

    IMPLEMENTATION NOTE: this machine's agy install was already authenticated during
    development, so the actual first-run/unauthenticated behavior (does agy print a
    URL, auto-launch a system browser itself as its own OS-level child process
    independent of this function's stdio, or something else; what an auth-required
    failure actually looks like) was not observed directly and this function cannot
    yet distinguish "needs auth" from any other failure - it simply reports whether the
    call succeeded. Revisit this once the real unauthenticated behavior has been
    observed (see plan's verification step for check_or_prompt_auth)."""
    result = _run_agy(["--model", model, "-p", "reply OK"], cwd=Path.cwd(),
                      cli_bin=cli_bin, timeout_seconds=timeout_seconds, interactive=True)
    return result.returncode == 0


# ==========================================
# QUOTA & RATE LIMIT HANDLING
# ==========================================
QUOTA_RATE_LIMIT_KEYWORDS = [
    "429",
    "resource_exhausted",
    "resourceexhausted",
    "rate limit",
    "quota",
    "too many requests",
    "please wait",
    "before retry",
    "retry after",
    "retry in",
    "resets at",
    "reset at",
]


def is_quota_or_rate_limit(error_msg: str) -> bool:
    """Returns True if the given error string indicates an API rate limit or quota exhaustion."""
    if not error_msg:
        return False
    lower = error_msg.lower()
    return any(kw in lower for kw in QUOTA_RATE_LIMIT_KEYWORDS)


def parse_quota_reset_wait_seconds(error_msg: str) -> Optional[float]:
    """Parses relative duration or reset timestamp from a quota/rate limit error message.
    Returns wait seconds (float or int), or 60 as default if it's a quota error without specific time.
    Returns None if error_msg is not a quota/rate limit error."""
    if not is_quota_or_rate_limit(error_msg):
        return None

    # Hours
    hr_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b', error_msg, flags=re.I)
    # Minutes
    min_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b', error_msg, flags=re.I)
    # Seconds
    sec_match = re.search(r'(?:in|after|retry|wait)?\s*(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b', error_msg, flags=re.I)

    total_seconds = 0.0
    matched_duration = False

    if hr_match:
        total_seconds += float(hr_match.group(1)) * 3600
        matched_duration = True
    if min_match:
        total_seconds += float(min_match.group(1)) * 60
        matched_duration = True
    if sec_match:
        total_seconds += float(sec_match.group(1))
        matched_duration = True

    if matched_duration and total_seconds > 0:
        return int(total_seconds) if total_seconds.is_integer() else total_seconds

    # Check for ISO timestamp: e.g. "2026-08-02T14:00:00Z"
    iso_match = re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', error_msg)
    if iso_match:
        try:
            iso_str = iso_match.group(0)
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            reset_dt = datetime.fromisoformat(iso_str)
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            diff = (reset_dt - now).total_seconds()
            return max(int(diff), 1)
        except Exception:
            pass

    # Check for clock time: e.g. "resets at 23:59:59" or "at 14:30:00"
    clock_match = re.search(r'\b(?:at|reset[s]?\s+at)\s+(\d{1,2}):(\d{2})(?::(\d{2}))?', error_msg, flags=re.I)
    if clock_match:
        try:
            ch, cm, cs = int(clock_match.group(1)), int(clock_match.group(2)), int(clock_match.group(3) or 0)
            now = datetime.now()
            target = now.replace(hour=ch, minute=cm, second=cs, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            diff = (target - now).total_seconds()
            return max(int(diff), 1)
        except Exception:
            pass

    # Default fallback when rate limit detected but no duration string parsed
    return 60


def pause_for_quota_reset(wait_seconds: float, reason: str = "") -> None:
    """Pauses execution cleanly when a quota or rate limit is hit, informing the user
    and resuming automatically when the wait duration expires."""
    wait_int = int(wait_seconds)
    print(f"\n   [PAUSE] Quota/rate limit encountered ({reason or 'waiting for reset'}). "
          f"Pausing execution for {wait_int}s...", flush=True)
    time.sleep(wait_seconds)
    print("   [RESUME] Quota reset period elapsed. Resuming execution at current position.\n", flush=True)
