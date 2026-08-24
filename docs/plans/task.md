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
| 14. BUG-3: `Census.IMAGE_DIR` double-nesting | Done | Was in the original audit's Phase 1 scope but dropped when Phase 1 landed (not in this table until now). `run_census_flavor` re-nested `IMAGE_DIR` from its own current value each call, folding the path in on itself on a second in-process invocation. Fixed: nest from a pristine `_IMAGE_DIR_BASE` captured at import time, never from the mutated global. |

# Ox Alpha Audit: Phase 2 (Safety Net)

| Task | Status | Description |
| --- | --- | --- |
| 1. TEST-1: Golden GEDCOM regression harness | Done | `tests/test_gedcom_golden.py` resets Archivist's mutable module globals between cases and masks only volatile lines (run date/time, copyright year, file paths). The `legacy_1950` JSON fixture and its baseline `.ged` have been committed. |
| 2. TEST-3: Router smoke test | Done | `tests/test_router_smoke.py`, AST-based (no `import Antiquarian.py`/`Voyageur.py`, avoids GUI deps). Guards the ARCH-1 revert (`runpy.run_module` + `--module`). |
| 3. TEST-6: FactTypes/schema.json CI parity checks | Done | `tests/test_ci_parity.py` — `FactTypes.json` &harr; `models.FACT_DEFINITIONS` and `schema.json` &harr; `models` drift checks. The JS harness (`Voyageur/tests/js/`) has now been wired into `.github/workflows/test.yml` as well. |
| 4. Hiddenimports coverage test | Done | `tests/test_hiddenimports_coverage.py`, pulled forward per the Phase 1 addendum. |
| 5. CI wiring: run the test suite on push/PR | Done | Added `.github/workflows/test.yml` (windows-latest, mirrors `build.yml`'s proven dependency install). Previously no workflow ran `pytest` at all — `lint.yml` only runs bare `pycodestyle`, not this repo's own flake8 gate. |
| 6. Fix dead `review_reason` assertions in `test_crosscheck.py` | Done | Leftover from Phase 1's BUG-1 fix (list &rarr; string); two tests still asserted `any(... for r in result["review_reason"])`, which iterates characters of a string and can never pass. |
| 7. flake8 gate cleanup | Done | Fixed 4 pre-existing violations blocking `test_code_quality_flake8` (`Antiquarian.py` E303, `Archivist/General.py` + `Voyageur/FS.py` E302, `Voyageur/A.py` E402 missing noqa) plus a new E501/E131 in the golden test file itself. Added `flake8` to `requirements.txt` — the gate test shells out to it but it was never installed anywhere, so it could never run.

# Ox Alpha Audit: Phase 3 (Shared Core Deduplication)

| Task | Status | Description |
| --- | --- | --- |
| DUP-1 to DUP-4: Text Utils | Done | Deduplicated string cleaners, whitespace normalizers, and snake_case converters into Commissioner/textutils.py. |
| DUP-5: Normalizers | Done | Deduplicated 
ormalize_enum and 
ormalize_sex_code into Commissioner/normalization.py. |
| DUP-6: get_census_era | Done | Extracted standalone get_census_era into Commissioner/census_consts.py. |
| DUP-7 to DUP-12: Core Deduplication | Done | Centralized safe_path, .env loading, .pmt parsing, prompt directory tiers, load_event_types, and atomic JSON io. |
| DUP-13 to DUP-18: IO and String Utils | Done | Centralized generic setting resolution, RMNOCASE collation, Windows file-lock retry IO, and name/filename sanitization. |

# Ox Alpha Audit: Phase 4 (Architecture)

| Task | Status | Description |
| --- | --- | --- |
| 1. ARCH-1 (revised scope) | Done | Self-relaunch router is correct as-is (do NOT reintroduce a `tool_runner.py` spawned by path - that broke frozen builds in Phase 1 batch 3 and was reverted). `build.py`'s hidden-import list exhaustiveness was already verified post-Phase-1 (commit `9adaab3`). Added comments to `Voyageur.py`/`Paleographer.py`'s dispatchers documenting their (intentionally opposite) argv-surgery behavior; no behavior change. |
| 2. ARCH-2: Declarative `execute_script` args | Done | Replaced the `if script_key == ... elif mode == ...` CLI-assembly chain with a module-level `ARG_SPECS` table: `(script_key, mode) -> callable(string_vars, debug_file_var) -> list[str]`. Verified byte-for-byte argv parity with the old chain for every real combo. |
| 3. ARCH-4: Curated per-tool env | Done | Added `SCRIPT_ENV_SUBFOLDERS` (script_key &rarr; owning `ENV_TARGETS` subfolder) and `_curated_env_vars()`; a launched child process now gets `GLOBAL_VARS` plus only its own tool's fields, not every GUI StringVar. The three nested-dir overrides (`REGISTRAR_RM_DATABASE`, `GAZETTEER_RM_DATABASE`, `PDFIX_TARGET_DIR`) are filtered the same way. Verified zero cross-tool leakage against the real schema. |
| 4. ARCH-5: Complete FactTypes migration | Done | `engine.py`/`FS.py`'s `load_event_types()` already derived from `FACT_DEFINITIONS`. Switched `Utils.FACT_TYPES` from reading `FactTypes.json` off disk to `Commissioner.fact_registry.export_fact_types_json()`; removed the dead `FACT_TYPES_PATH` constants and the test fixture built around monkeypatching one of them. `FactTypes.json` remains only as a generated/reference export, still guarded by `test_ci_parity.py`. |
| 5. ARCH-6: `record_registry` frozen-build path resolution | Done | Added `_pmt_files_by_priority()`, merging `.pmt` files across the tiered search dirs `prompt_search_dirs()` already defines; wired `_build_registry()` and `get_field_remap()` through it instead of the hardcoded, source-tree-relative `PMT_DIR`. |
| 6. ARCH-7: Lazy `Commissioner` registry build | Done | Replaced eager module-level `_REGISTRY = _build_registry()` with an `lru_cache`d `_get_registry()` getter; builds on first access, not at import time. |

# Ox Alpha Audit: Phase 5 (De-globalization)

| Task | Status | Description |
| --- | --- | --- |
| 1. ARCH-3: Eliminate global mutable state in Archivist pipeline | Done | `Census.py`: `run_census_flavor` no longer mutates module globals - a `CensusRunConfig` dataclass is built once per call and threaded explicitly. `General.py`: `GENERAL_CONFIG`/`CALL_NUMBER`/`COLLECTION_URL`/`COLLECTION_NAME`/`REPOSITORY`/`REPOSITORY_LOC`/`IMAGE_DIR`/`_ACTIVE_PROFILE` all eliminated - a `GeneralRunConfig` dataclass (carrying the former config fields plus the active `Profile` instance itself) is built once per `run_general_flavor` call and threaded explicitly through every builder/citation/household function, the `Profile` Protocol, and both implementations (`ScripProfile` in Scrip.py, `HBCAProfile` in HBCA.py) across module boundaries. Golden GEDCOM output confirmed byte-identical for both flavors; full suite (634 tests), flake8, and pycodestyle all green. Split `build_gedcom_from_census`/`build_individual` into smaller testable functions afterward (deferred - not required for de-globalization). |

# Ox Alpha Audit: Phase 6 (Performance & Polish)

| Task | Status | Description |
| --- | --- | --- |
| 1. PERF-2: `LAC.download_images` batch flush | Done | `save_master_db` now flushes every 10 canvases plus a guaranteed final flush, instead of re-serializing the whole master DB after every single canvas - mirrors `download_volume_assets_multiworker`'s existing batching rationale. New regression test asserts the call count, not just the end state. |
| 2. PERF-3: `Extract.save_master_db` flush cadence | Won't fix (for now) | The task's own note already said "fine at small scale, adopt flush-every-N if batch sizes grow" - confirmed no active problem (each save is preceded by a slow LLM call, unlike LAC's fast HTTP fetches where the O(N) re-serialize cost actually dominates). No code change; revisit if batch sizes grow. |
| 3. PERF-4: `Registrar` Pass 2 O(N×M) comparison | Done | Candidates are now prefiltered by shared name-token initial letter (via a bucketed index) before scoring with `fuzz.token_set_ratio`, instead of scoring every unknown-age record against every record in the tree. Safe under `FUZZY_THRESHOLD_STRICT` (95): a match that strict requires near-identical tokens, which virtually always share an initial letter. New test asserts a disjoint-name pair is never even scored. |
| 4. PERF-5: `Voyageur.js` DOM observer scope | Deferred | Would require guessing live DOM container selectors on authenticated third-party sites (Ancestry.com, FamilySearch.org) with no way to verify against the real, logged-in page in this environment. The task itself labels this "Opportunistic" - not worth risking breakage of live gather logic on an unverified guess. Left as-is. |
| 5. HYG-1: Machine-specific paths in `.mcp.json`/`opencode.json` | Done | Already satisfied: the only tracked config (`.air/mcp.json`) has no paths, and `opencode.json` (which does carry machine-specific settings) is already gitignored (`.gitignore` line 21) rather than committed. No change needed. |
| 6. HYG-2: Extract icon/help-text blobs from `Antiquarian.py` | Done | Moved the two base64 app-icon PNGs and the per-tab `help_texts` dict to `Commissioner/assets/app_icons.py` and `Commissioner/assets/help_content.py` respectively - byte-for-byte verified against the originals via AST comparison before deleting them. Cut `Antiquarian.py` from 2201 to 2035 lines. |
| 7. HYG-3: Dead code sweep | Done | Removed `Voyageur.js`'s `lastPageSignature` (assigned twice, read nowhere). Confirmed `Antiquarian.py`'s `switch_tab`/`_load_tool_schema` aliases are deliberate public test accessors (see `docs/plans/2026-08-13-pycharm-inspection-fixes.md`), actively used by 2+ test files each - kept, not dead code. Repo-wide sweep for commented-out code / TODO / FIXME / XXX markers found none. |
| 8. HYG-4: Rename `AntiquarianMCP/` | Done | Renamed to `AgyCli/` via `git mv` (history preserved) and updated every import site, `build.py`'s hidden-import entry, `Antiquarian.py`'s `AGY_TEST_SCRIPT` path, `tests/test_code_quality.py`, and the living `docs/developer/development-workflow.md`. The old name required its own README section explaining "despite the name, this isn't an MCP server" - a naming smell the package's own docs already flagged; that section now reads as history instead of a live disclaimer. Dated historical planning docs under `docs/plans/`/`docs/superpowers/` left untouched as point-in-time records. |
| 9. HYG-5: Pin dependencies | Done | `requirements.in` is now the source of truth for direct versions; `requirements.txt` is a `pip-tools`-generated lockfile pinning every transitive dependency too. Generating it surfaced a real latent conflict: the old `flake8==7.1.1` pin required `pycodestyle<2.13.0`, incompatible with the pinned `pycodestyle==2.14.0` - bumped to `flake8==7.3.0` (confirmed as what was actually installed and working, meaning the old pin was silently unenforced). Split `playwright`/`websocket-client` into `requirements-optional.in`/`.txt` - both are lazily imported only by Voyageur's browser-automation paths and already degrade gracefully or raise a clear install hint when absent. |
| 10. HYG-6: Write `ARCHITECTURE.md` | Done | New root-level `ARCHITECTURE.md` documents `.env` tiering/precedence, the frozen-build `PROGRAM_DIR`/`APP_DIR` distinction, the `runpy` self-relaunch router (ARCH-1) and its `build.py` hiddenimports contract, `.pmt` prompt search tiers, and the Voyageur-gathers/Archivist-decodes division of labor. Cross-linked from `docs/developer/architecture-overview.md` (which stays the module-map companion). Verifying the `.env` section against actual behavior surfaced a real bug: `Commissioner/envkit.py`'s docstring claimed `override=True` and inherited-env-loses precedence; every real caller uses the actual default `override=False`, so inherited environment variables actually win over both `.env` files - the opposite of what was documented. Fixed the docstring to match verified behavior. |
