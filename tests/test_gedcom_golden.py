"""TEST-1: Golden-file GEDCOM regression framework.

Runs Archivist's builders over checked-in JSON fixtures and compares every produced
.ged against a checked-in golden snapshot, after masking the lines that legitimately
change between runs (today's date/time, the copyright year, absolute media paths).

Conventions
-----------
tests/fixtures/archivist/<name>.census.json   -> Census.run_census_flavor(data)
tests/fixtures/archivist/<name>.scrip.json    -> General.run_general_flavor(data, Scrip.ScripProfile())
tests/fixtures/archivist/<name>.general.json  -> General.run_general_flavor(data, General.GeneralProfile())
tests/golden/archivist/<name>.ged             -> the normalized snapshot for that case

Workflow
--------
1. Drop a new fixture JSON in place.
2. Run:  ANT_UPDATE_GOLDEN=1 python -m pytest tests/test_gedcom_golden.py
3. Review the generated snapshot with git diff, then commit fixture + snapshot.

The suite is intentionally quiet while no fixtures exist (the parametrization simply
has no cases); the framework's own machinery is covered by the self-tests below so
this file always contributes runnable tests.

Archivist/Utils.py freezes its configuration into module constants at import time, so
the environment is prepared inside a session fixture BEFORE the first import - do not
move these imports to module level.
"""

import difflib
import importlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures" / "archivist"
GOLDEN_DIR = TESTS_DIR / "golden" / "archivist"

UPDATE_GOLDEN = os.environ.get("ANT_UPDATE_GOLDEN", "").lower() not in ("", "0", "false")

_FLAVOR_BY_SUFFIX = {
    ".census.json": "census",
    ".scrip.json": "scrip",
    ".general.json": "general",
}


# ---------------------------------------------------------------------------
# Volatile-line normalization
# ---------------------------------------------------------------------------

_MONTHS = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
# Utils.CURRENT_DATE / General's gedcom_date: "05 SEP 2026" at level 1 (HEAD + tasks).
_RUN_DATE_RE = re.compile(
    r"^(1 (?:_L)?DATE) (?:0[1-9]|[12]\d|3[01]) " + _MONTHS + r" (?:19|20)\d{2}$", re.M)
_RUN_TIME_RE = re.compile(r"^2 TIME \d{2}:\d{2}:\d{2}$", re.M)
# General's HEAD copyright carries the current year; Census's is a fixed start year.
_COPYRIGHT_YEARS_RE = re.compile(r"^(1 COPR Copyright \(c\) )\d{4}-\d{4}", re.M)
# Media/object FILE lines embed machine-specific absolute directories; keep the basename.
_FILE_LINE_RE = re.compile(r"^(\d+ FILE ).*[/\\]", re.M)


def normalize_gedcom(text: str) -> str:
    """Masks run-volatile content so two runs of one fixture are byte-comparable.

    Deliberately narrow: anchored, level-aware patterns that can only match the
    volatile emissions themselves - historical event dates (level 2, or level 1 with
    EST/BEF-style prefixes or pre-1900 years) pass through untouched.
    """
    text = _RUN_DATE_RE.sub(r"\1 <RUN_DATE>", text)
    text = _RUN_TIME_RE.sub("2 TIME <RUN_TIME>", text)
    text = _COPYRIGHT_YEARS_RE.sub(r"\1<YEARS>", text)
    text = _FILE_LINE_RE.sub(r"\1<DIR>/", text)
    return text.replace("\r\n", "\n").strip() + "\n"


# ---------------------------------------------------------------------------
# Session-wide Archivist environment
# ---------------------------------------------------------------------------

# Census.run_census_flavor and General.run_general_flavor both build a fresh
# per-call RunConfig dataclass internally (ARCH-3) - neither mutates module
# globals anymore, so no cross-fixture reset is needed here.


@pytest.fixture(scope="session")
def archivist(tmp_path_factory):
    """Imports the Archivist modules once, with the environment pointed at a throwaway
    Genealogy directory, and hands back a namespace carrying the modules plus pristine
    copies of their mutable module state (both flavors keep run-to-run state in module
    globals; each test resets from these snapshots so cases stay independent)."""
    genealogy = tmp_path_factory.mktemp("genealogy")
    out_dir = genealogy / "GEDCOM"
    out_dir.mkdir(parents=True, exist_ok=True)

    env_keys = ("GENEALOGY_DIR", "GEDCOM_OUTPUT_PATH", "GEDCOM_OUTPUT_NAME",
                "GEDCOM_OUTPUT_MODE", "MEDIA_DIR", "RM_DIR", "FTM_DIR",
                "RESEARCHER_NAME", "ORG_NAME",
                "SUBM_ADDRESS", "MGS_GROUP_URL", "ANCESTRY_GROUP_URL")
    saved_env = {k: os.environ.get(k) for k in env_keys}
    os.environ["GENEALOGY_DIR"] = str(genealogy)
    os.environ["GEDCOM_OUTPUT_PATH"] = str(out_dir)
    os.environ["GEDCOM_OUTPUT_NAME"] = "golden.ged"
    os.environ["GEDCOM_OUTPUT_MODE"] = "Both"
    for k in ("MEDIA_DIR", "RM_DIR", "FTM_DIR", "RESEARCHER_NAME", "ORG_NAME",
              "SUBM_ADDRESS", "MGS_GROUP_URL", "ANCESTRY_GROUP_URL"):
        os.environ[k] = ""

    archivist_dir = REPO_ROOT / "Archivist"
    for entry in (str(REPO_ROOT), str(archivist_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    try:
        # Flat imports, matching how the tools themselves import their siblings.
        import Utils      # Archivist/Utils.py
        # Utils freezes SUBM_ADDRESS/MGS_GROUP_URL/ANCESTRY_GROUP_URL etc. into module
        # constants at import time. If another test file (e.g. Archivist/tests/*, which
        # sorts before this file and pollutes os.environ at conftest collection time)
        # already imported Utils earlier in the session, the import above is a no-op
        # cache hit and the stale constants survive untouched. Force a reload so they're
        # always recomputed from the environment this fixture just set.
        importlib.reload(Utils)
        import Census
        import General
        import Scrip

        # resolve_source_id() persists a registry next to the real Archivist package;
        # redirect it so test runs never write into the repo tree.
        Utils.SOURCE_ID_REGISTRY_PATH = (
            tmp_path_factory.mktemp("source-id-registry") / "source_id_registry.json")

        yield SimpleNamespace(
            Utils=Utils, Census=Census, General=General, Scrip=Scrip,
            out_dir=out_dir,
        )
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _reset_builder_state(archivist_ns):
    """Resets the only run-to-run state that still lives outside a RunConfig: Utils'
    own module-level output settings."""
    archivist_ns.Utils.GEDCOM_OUTPUT_NAME = "golden.ged"
    archivist_ns.Utils.RESEARCHER = ""
    archivist_ns.Utils.ORG_NAME = ""


# ---------------------------------------------------------------------------
# The golden comparison itself
# ---------------------------------------------------------------------------

def _discover_fixtures():
    if not FIXTURES_DIR.is_dir():
        return []
    cases = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        for suffix, flavor in _FLAVOR_BY_SUFFIX.items():
            if path.name.endswith(suffix):
                cases.append((path, flavor))
                break
    return cases


_CASES = _discover_fixtures()


@pytest.mark.parametrize(
    "fixture_path,flavor",
    _CASES,
    ids=[p.name[: -(len(flavor) + len(".json") + 1)] for p, flavor in _CASES],
)
def test_gedcom_matches_golden(fixture_path, flavor, archivist, tmp_path):
    _reset_builder_state(archivist)

    out_dir = tmp_path / "GEDCOM"
    out_dir.mkdir()
    archivist.Utils.GEDCOM_OUTPUT_PATH = str(out_dir)

    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    if flavor == "census":
        archivist.Census.run_census_flavor(data)
    elif flavor == "scrip":
        archivist.General.run_general_flavor(data, archivist.Scrip.ScripProfile())
    elif flavor == "general":
        archivist.General.run_general_flavor(data, archivist.General.GeneralProfile())
    else:  # pragma: no cover - guarded by _discover_fixtures
        pytest.fail(f"unknown flavor {flavor!r}")

    produced = {}
    for ged_path in sorted(out_dir.glob("*.ged")):
        produced[ged_path.name] = normalize_gedcom(ged_path.read_text(encoding="utf-8"))
    assert produced, f"builder produced no .ged files in {out_dir}"

    actual = "".join(
        f"=== {name} ===\n{body}\n" for name, body in sorted(produced.items()))

    case_name = fixture_path.name[: -(len(flavor) + len(".json") + 1)]
    golden = GOLDEN_DIR / f"{case_name}.ged"

    if UPDATE_GOLDEN:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        pytest.skip(f"golden snapshot (re)written: {golden}")

    assert golden.is_file(), (
        f"no golden snapshot for {fixture_path.name}. Generate one with "
        f"ANT_UPDATE_GOLDEN=1, review tests/golden/archivist/{golden.name} "
        f"with git diff, and commit it."
    )
    expected = golden.read_text(encoding="utf-8")
    if expected != actual:
        pytest.fail(
            f"GEDCOM output for {fixture_path.name} drifted from its golden snapshot.\n"
            + _diff_snippet(expected, actual)
            + "\nIf this change is intentional, re-run with ANT_UPDATE_GOLDEN=1 "
              "and commit the updated snapshot."
        )


def _diff_snippet(expected: str, actual: str, max_lines: int = 60) -> str:
    diff = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile="golden", tofile="actual", lineterm="", n=3))
    snippet = "\n".join(diff[:max_lines])
    if len(diff) > max_lines:
        snippet += f"\n... ({len(diff) - max_lines} more diff lines)"
    return snippet or "(outputs differ but produced no line diff - encoding change?)"


# ---------------------------------------------------------------------------
# Framework self-tests (always run, even with zero fixtures)
# ---------------------------------------------------------------------------

def test_normalizer_masks_volatile_lines_only():
    sample = "\n".join([
        "0 HEAD",
        "1 DATE 05 SEP 2026",
        "2 TIME 13:04:05",
        "1 COPR Copyright (c) 2018-2026 Example Society. All rights reserved.",
        "1 COPR Copyright 2018",
        "1 FILE C:/Users/researcher/Genealogy/Media/Census/1950/img_00001.jpg",
        "1 _LDATE 05 SEP 2026",
        "1 DATE EST 1850",
        "2 DATE 12 JUN 1850",
        "2 DATE DEC 1871",
    ])
    out = normalize_gedcom(sample)
    assert "1 DATE <RUN_DATE>\n2 TIME <RUN_TIME>" in out
    assert "1 COPR Copyright (c) <YEARS> Example Society. All rights reserved." in out
    assert "1 COPR Copyright 2018" in out                       # fixed-year form untouched
    assert "1 FILE <DIR>/img_00001.jpg" in out                  # dir masked, basename kept
    assert "1 _LDATE <RUN_DATE>" in out
    assert "1 DATE EST 1850" in out                             # prefixed event date survives
    assert "2 DATE 12 JUN 1850" in out                          # level-2 event date survives
    assert "2 DATE DEC 1871" in out                             # month-precision date survives


def test_fixture_and_golden_directories_exist():
    assert FIXTURES_DIR.is_dir(), "tests/fixtures/archivist/ is missing"
    assert GOLDEN_DIR.is_dir(), "tests/golden/archivist/ is missing"
