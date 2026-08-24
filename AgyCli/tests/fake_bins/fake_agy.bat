@echo off
rem Fake stand-in for agy.exe used by test_agy_client.py - never touches the real
rem network/CLI. Branches on FAKE_AGY_MODE (set by the test via monkeypatch.setenv)
rem since parsing agy's real --add-dir/--model/etc. flags in batch would be painful and
rem these tests are about agy_client.py's own subprocess/parsing logic, not agy's.

if "%FAKE_AGY_MODE%"=="fail" (
    echo fake failure stderr text 1>&2
    exit /b 1
)

if "%FAKE_AGY_MODE%"=="empty_success" (
    rem Reproduces the real, confirmed-live bug: status SUCCESS, empty response, no
    rem structured_output key at all - agy_client.call_agy_structured must catch this.
    echo {"conversation_id": "fake", "status": "SUCCESS", "response": "", "usage": {"input_tokens": 10, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 10}}
    exit /b 0
)

if "%FAKE_AGY_MODE%"=="bad_json" (
    echo this is not valid json
    exit /b 0
)

echo {"conversation_id": "fake", "status": "SUCCESS", "response": "{}", "structured_output": {"sheets": [{"page_id": "1", "records": []}]}, "usage": {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 5, "cache_read_tokens": 3, "total_tokens": 128}}
exit /b 0
