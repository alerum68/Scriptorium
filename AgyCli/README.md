# AgyCli

A generic, reusable, **synchronous** library for safely calling Google's AGY
CLI (`agy`) from any Antiquarian tool. Right now Paleographer's `agy_engine.py` is the
only consumer, but `agy_client.py` itself knows nothing about Parish.pmt, schemas, or
genealogy - any future Antiquarian tool that wants to call `agy` can import it directly.

## Not an MCP server

This is a **plain importable Python package, not an MCP server** - it was
previously named `AntiquarianMCP`, which implied otherwise (see below) and was
renamed to `AgyCli` for that reason. Paleographer runs as a standalone
subprocess launched by Antiquarian.py's GUI with no MCP client present at
runtime, so a real MCP server would be unreachable from it.

## Relationship to ai-research-mcp

There's a separate, unrelated tool at
`C:\Users\Jason Cole\Documents\PyCharm\tools\mcp\ai-research-mcp` - a real MCP
server exposing a `research` tool for AI Assistant's own ad hoc research queries across
any project. It is **not touched or replaced by this folder** and stays registered
independently. Both it and this package depend on the same underlying `agy` CLI
install/authentication, which is why the setup steps below are documented once, here,
for both.

## Files

- `agy_client.py` - the library. `call_agy_structured(...)` is the steady-state,
  fully headless extraction call (never interactive, hard timeout, full Windows
  process-tree kill on timeout). `check_or_prompt_auth(...)` is the one deliberately
  interactive function, used only for first-time sign-in.
- `test_agy_connection.py` - standalone script backing Antiquarian's "Test Agy
  Connection" button (Global Settings, API & Processing section).
- `tests/` - unit tests against `tests/fake_bins/*.bat` stand-ins, no live `agy` or
  network access needed.

## Setup

1. **Confirm `agy` is installed and on PATH.** This machine already has it at
   `%LOCALAPPDATA%\agy\bin\agy.exe`. Check with:
   ```
   agy --help
   ```
2. **Authenticate.** Use Antiquarian's **Test Agy Connection** button (Global Settings,
   next to the Extraction Engine selector), or run `AgyCli/test_agy_connection.py`
   directly. This is the one deliberately-interactive step - if `agy` isn't already
   signed in, it may need to open a browser for Google sign-in.

   **Known gap:** the exact first-run/unauthenticated behavior has not been directly
   observed during this feature's development (this machine's `agy` install was already
   authenticated throughout). No `login`/`auth` subcommand appears in `agy --help`, so
   sign-in is presumed to trigger automatically on first use - possibly via `agy`
   launching a system browser itself as its own OS-level process, independent of
   whatever stdio `check_or_prompt_auth`'s subprocess call uses. If you hit this for the
   first time on a fresh machine, update this section with what you actually observed.
3. **Verify Pro-tier access.** Once authenticated, confirm a pro-tier model actually
   works:
   ```
   agy --model gemini-3.1-pro-high -p "reply OK"
   ```
   If this errors about subscription/tier, the signed-in Google account doesn't have
   Pro access.
4. **Valid model IDs.** Run `agy models` to see the full list. Only exact IDs from that
   list work with `--model` - confirmed live that shorthand values like `pro` or
   `flash` are **not** valid (`agy --model pro` errors with "model pro is not
   recognized"). Paleographer always passes the exact ID `gemini-3.1-pro-high`
   explicitly on every call, never relying on `agy`'s own default - confirmed live that
   with no `--model` flag, `agy` defaults to a flash-tier model with noticeably worse
   OCR quality.
5. **In Antiquarian**, set "Extraction Engine" (Global Settings) to "AGY CLI"
   (the default) and confirm "Agy Model Name" reads `gemini-3.1-pro-high`.

## Known behavior worth knowing about

- **`agy` can be intermittently flaky, even for calls that normally succeed.** During
  development, the exact same single-image call succeeded on one run and failed (empty
  `structured_output`, `status: "SUCCESS"` - see below) on an immediately-following
  retry of the identical call. `agy_engine.run_with_agy_retries` exists specifically to
  absorb this - a single failure is not necessarily a real problem, retry before
  concluding something is broken.
- **`status: "SUCCESS"` does not always mean real output was produced.** Confirmed live
  (both on a native-PDF-reading attempt and on a plain single-image call): `agy` can
  report `"status": "SUCCESS"` with an empty `"response"` and no `"structured_output"`
  key at all, apparently when its own internal agent tries a tool call that headless
  mode auto-denies (no terminal to prompt through) and it gives up silently rather than
  surfacing that as a failure status. `agy_client.call_agy_structured` checks for a
  present, non-empty `structured_output` explicitly - never trust `status` alone.
- **PDFs are rasterized locally, not read natively by `agy`.** Native PDF reading via
  `--add-dir` genuinely works for light queries ("how many pages does this have"), but
  was confirmed unreliable for a full schema-constrained multi-page extraction on a
  real 38-page case file (same empty-`structured_output` failure mode above). Instead,
  `agy_engine.rasterize_pdf_to_images` converts each page to an image locally (via
  `pdfplumber`, already a project dependency) and stages all of them in one `--add-dir`
  call - the same mechanism already proven reliable for direct image input. This was
  verified live to correctly return one "sheet" per staged page in a single call.
- **No async batch API equivalent exists** for `agy`, unlike the direct AI Assistant API
  path. Every file - image or PDF, any page count - goes through the same synchronous
  call. Large multi-page PDFs should use a generous `AGY_TIMEOUT_SECONDS`.
