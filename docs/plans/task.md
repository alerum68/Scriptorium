# Ox Alpha Audit: Phase 1 (Correctness & Hardening)

| Task | Status | Description |
| --- | --- | --- |
| 1. BUG-1: `ScripTools` review_reason | Done | Fix `record.setdefault("review_reason", []).append(...)` to be a string, matching the schema. |
| 2. BUG-2: `PDFix` temp-file regex | Done | Fix `.temp_optimized.pdf` guard to match the actual `.temp_opt_` prefix. |
| 3. BUG-4 & ARCH-1: PyInstaller Runner Shim | Done | Self-relaunch via `sys.executable`/`--module`/`runpy.run_module` (frozen exe can't run an arbitrary script path); declare all `runpy`-only entry-point modules as `hiddenimports` in `build.py`. |
| 4. BUG-5: `Extract.py` MODEL_NAME | Done | Gate `MODEL_ID` requirement on `EXTRACTION_ENGINE == "api"`. |
| 5. BUG-6: `census_schema` review message | Done | Reword stale review reason for unmapped columns. |
| 6. BUG-7: Master DB Atomic Writes | Done | Route `Extract.save_master_db` to use a `.tmp` file and atomic replace to prevent corruption. |
| 7. BUG-8: `.env` Override Precedence | Done | Standardize on tool `.env` overriding global `.env`. |
| 8. ROB-1: Quota waits consume retries | Done | Track quota pauses separately from failures in `agy_engine.py`. |
| 9. ROB-2: Tampermonkey hang | Done | Add 30-minute timeout to `wait_for_final_json_event`. |
| 10. ROB-7: `batch_set_env` atomic rewrite | Done | Use `.tmp` + `os.replace` for `.env` rewriting. |
| 11. ROB-10: User-Agent headers | Done | Restore valid Chrome UA instead of IP placeholder. |
| 12. ROB-11: Dead annotations/code | Done | Remove dead session/aliases, fix annotations. |
| 13. PERF-1: stdout read chunking | Done | Read subprocess stdout in 256-byte chunks instead of 1. |

# Ox Alpha Audit: Phase 2 (Safety Net)

| Task | Status | Description |
| --- | --- | --- |
| 1. TEST-1: Golden GEDCOM regression harness | Done (unpopulated) | `tests/test_gedcom_golden.py` resets Archivist's mutable module globals between cases and masks only volatile lines (run date/time, copyright year, file paths). No fixtures committed yet (`tests/fixtures/archivist/` is empty) — zero actual GEDCOM regression coverage until fixtures are added. |
| 2. TEST-3: Router smoke test | Done | `tests/test_router_smoke.py`, AST-based (no `import Antiquarian.py`/`Voyageur.py`, avoids GUI deps). Guards the ARCH-1 revert (`runpy.run_module` + `--module`). |
| 3. TEST-6: FactTypes/schema.json CI parity checks | Done | `tests/test_ci_parity.py` — `FactTypes.json` &harr; `models.FACT_DEFINITIONS` and `schema.json` &harr; `models` drift checks. JS harness (`Voyageur/tests/js/`) predates this phase and is still not wired into CI. |
| 4. Hiddenimports coverage test | Done | `tests/test_hiddenimports_coverage.py`, pulled forward per the Phase 1 addendum. |
| 5. CI wiring: run the test suite on push/PR | Done | Added `.github/workflows/test.yml` (windows-latest, mirrors `build.yml`'s proven dependency install). Previously no workflow ran `pytest` at all — `lint.yml` only runs bare `pycodestyle`, not this repo's own flake8 gate. |
| 6. Fix dead `review_reason` assertions in `test_crosscheck.py` | Done | Leftover from Phase 1's BUG-1 fix (list &rarr; string); two tests still asserted `any(... for r in result["review_reason"])`, which iterates characters of a string and can never pass. |
| 7. flake8 gate cleanup | Done | Fixed 4 pre-existing violations blocking `test_code_quality_flake8` (`Antiquarian.py` E303, `Archivist/General.py` + `Voyageur/FS.py` E302, `Voyageur/A.py` E402 missing noqa) plus a new E501/E131 in the golden test file itself. Added `flake8` to `requirements.txt` — the gate test shells out to it but it was never installed anywhere, so it could never run.
