# Ox Alpha Audit: Phase 1 (Correctness & Hardening)

| Task | Status | Description |
| --- | --- | --- |
| 1. BUG-1: `ScripTools` review_reason | Pending | Fix `record.setdefault("review_reason", []).append(...)` to be a string, matching the schema. |
| 2. BUG-2: `PDFix` temp-file regex | Pending | Fix `.temp_optimized.pdf` guard to match the actual `.temp_opt_` prefix. |
| 3. BUG-4 & ARCH-1: PyInstaller Runner Shim | Pending | Implement `tool_runner.py` shim and add `hiddenimports` in `build.py` to fix frozen `.exe` tool launches. |
| 4. BUG-5: `Extract.py` MODEL_NAME | Pending | Gate `MODEL_ID` requirement on `EXTRACTION_ENGINE == "api"`. |
| 5. BUG-6: `census_schema` review message | Pending | Reword stale review reason for unmapped columns. |
| 6. BUG-7: Master DB Atomic Writes | Pending | Route `Extract.save_master_db` to use a `.tmp` file and atomic replace to prevent corruption. |
| 7. BUG-8: `.env` Override Precedence | Pending | Standardize on tool `.env` overriding global `.env`. |
| 8. ROB-1: Quota waits consume retries | Pending | Track quota pauses separately from failures in `agy_engine.py`. |
| 9. ROB-2: Tampermonkey hang | Pending | Add 30-minute timeout to `wait_for_final_json_event`. |
| 10. ROB-7: `batch_set_env` atomic rewrite | Pending | Use `.tmp` + `os.replace` for `.env` rewriting. |
