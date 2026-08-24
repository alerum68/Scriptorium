# Architecture Contracts

This document covers Antiquarian's cross-cutting runtime contracts - the load-bearing
behaviors that aren't obvious from any single file, previously documented only in
scattered inline comments across the codebase. For a module-by-module map of what
lives where, see `docs/developer/architecture-overview.md` instead; this file is about
*how the pieces actually run together*, not what each one is.

---

## 1. `.env` tiering and precedence

Every tool loads its environment via `Commissioner/envkit.py`'s `load_tool_env(tool_dir)`
(BUG-8), which replaced each tool's own inconsistent pair of `load_dotenv()` calls.

It loads two files, in this order, both at `override=False` (the default every caller
in this codebase actually uses - none pass `override=True`):

1. `<tool_dir>/.env` - the tool's own subfolder `.env` (e.g. `Paleographer/.env`)
2. `<repo root>/.env` - the global `.env`

`load_dotenv(override=False)` only fills in a key that isn't **already** set in
`os.environ`. Net precedence, highest first:

1. **Already-set environment variables** - inherited from the shell, a CI runner's
   exports, or ARCH-4's curated per-tool env that `Antiquarian.py._run_subprocess`
   passes to a launched subprocess (see §3 below).
2. **Tool `.env`** (loads first, so it wins any conflict with the global file).
3. **Global `.env`** - only fills gaps neither of the above set.

This means a value already present in the process environment always wins over both
`.env` files - `.env` files are for *filling in defaults*, not overriding a caller's
explicit environment. If a future caller genuinely needs `.env` values to win over an
inherited variable, it must pass `override=True` explicitly; no current caller does.

---

## 2. `PROGRAM_DIR` vs `APP_DIR`: the frozen-build distinction

Two different "where is this installed" values exist, and conflating them silently
breaks portable installs:

- **`APP_DIR`** (`Antiquarian.py` only) - the app's *actual* install location.
  `Path(__file__).resolve().parent` lands inside PyInstaller's `_internal` bundle
  directory when frozen, not next to the `.exe` - so anything that needs "where the
  app itself lives" (detecting a portable install's `.portable` marker file, finding
  the bundled `Prompts` folder next to the `.exe`) must resolve via
  `Path(sys.executable).resolve().parent` when frozen instead. `BASE_DIR` is the
  `__file__`-relative dev-mode equivalent; `APP_DIR` falls back to `BASE_DIR` when not
  frozen (`getattr(sys, "frozen", False)` is false).
- **`PROGRAM_DIR`** (every other tool - `Utils.py`, `Gazetteer.py`, `Extract.py`,
  `PDFix.py`, `Registrar.py`, `Commissioner/record_registry.py`) - an environment
  variable each tool reads via `os.getenv("PROGRAM_DIR", <dev-checkout fallback>)`.
  It is **never** a GUI-configurable setting; `Antiquarian.py._run_subprocess` sets it
  in the launched subprocess's environment as `run_env.setdefault('PROGRAM_DIR',
  str(APP_DIR))` - i.e. `PROGRAM_DIR` *is* `APP_DIR`, just handed down to child
  processes that can't compute `sys.executable`'s own frozen-vs-dev distinction
  themselves (they aren't the frozen entry point; the router is).

In short: `APP_DIR` is computed once, in the one process that actually knows whether
it's frozen. `PROGRAM_DIR` is that same value, propagated to every tool subprocess as
an environment variable so they don't need to duplicate the frozen-detection logic.

---

## 3. The `runpy` self-relaunch contract (ARCH-1)

PyInstaller freezes `Antiquarian.py` into a single `.exe` with no separate `.py` files
on disk for the other tools - so launching a tool by spawning `python
Voyageur/Voyageur.py` as a subprocess (a real file path) cannot work in a frozen
build; that path simply doesn't exist. A prior attempt at a `tool_runner.py` spawned
by path broke frozen builds this way and was reverted (Phase 1) - do not reintroduce
that pattern.

The actual contract, in `Antiquarian.py`:

1. **Building the relaunch command** (`_run_subprocess`): the target script's path
   (e.g. `Archivist/Archivist.py`) is converted to a dotted module name
   (`Archivist.Archivist`) relative to `BASE_DIR`. The subprocess command becomes
   `[sys.executable, __file__, "--module", module_name] + args` - i.e. **the same
   entry point relaunches itself** with a `--module` flag, rather than pointing at a
   different file. This works identically in dev (`sys.executable` = `python.exe`)
   and frozen (`sys.executable` = `Antiquarian.exe`) builds, since neither path
   depends on a loose `.py` file existing on disk.
2. **Dispatching on relaunch** (`if __name__ == "__main__":` at the bottom of
   `Antiquarian.py`): when invoked with `--module <name>`, it rewrites `sys.argv` to
   look like a normal direct invocation of that module and calls
   `runpy.run_module(args.module, run_name="__main__")` to execute it in-process,
   exactly as if `python -m <name>` had been run directly.
3. **`build.py`'s `hiddenimports`**: because step 2 loads modules dynamically via
   `runpy` rather than through a static `import` PyInstaller's analyzer can see,
   every module ever reachable via `--module` must be declared explicitly as a
   `--hidden-import` in `build.py`, or a frozen build will fail to find it at
   runtime even though it works fine from source. This list is the single point of
   truth for "which modules the router can relaunch into" - `tests/test_router_smoke.py`
   and `tests/test_hiddenimports_coverage.py` both guard it.

`Voyageur.py`/`Paleographer.py`'s own dispatchers additionally do their own
`sys.path` surgery (adding their own module directory) to work around the router
leaving `cwd` at the project root rather than the tool's own subfolder - see the
comments at the top of each for their (intentionally opposite) argv handling.

---

## 4. Prompt (`.pmt`) search tiers

`Commissioner/record_registry.py`'s `prompt_search_dirs()` resolves `.pmt` document-type
definitions from three tiers, highest priority first:

1. **`GENEALOGY_DIR / PROMPTS_DIR`** - user overrides, e.g. a user's own custom record
   type dropped into their genealogy folder.
2. **`PROGRAM_DIR / Prompts`** - the app's bundled defaults (what `build.py` actually
   ships next to the `.exe` in a frozen install).
3. **`Paleographer/prompts`** - the dev-mode checkout fallback, so running from source
   works without any environment setup.

`resolve_prompt_path()` and `_pmt_files_by_priority()` both merge across all three
tiers by walking them in *reverse* (lowest priority first) and letting a later
(higher-priority) tier's file overwrite an earlier one in a `{filename: path}` dict -
so a user's `GENEALOGY_DIR` copy of `Parish.pmt` silently shadows the bundled one
without deleting it, and `Commissioner.record_registry._build_registry()`'s
lazily-built document-type registry (ARCH-7) reflects the same merged view.

---

## 5. Division of labor: Voyageur gathers, Archivist decodes

Voyageur's job stops at **capturing raw data**, not interpreting it. When it scrapes
a census index table or downloads a parish register image, whatever code the source
site used (a numeric occupation code, a state abbreviation, a nationality
abbreviation) is written into the Master DB JSON exactly as the site presented it -
Voyageur does not decode, normalize, or otherwise transform those codes.

Archivist is the *only* place that decoding happens, and only at GEDCOM-build time.
`Commissioner/census_codes.py` (consumed by `Archivist/Census.py`) is explicit about
why: "decoding happens at GEDCOM-build time, never at gather time, so the JSON stays a
raw capture and a dictionary fix never requires re-gathering." Concretely: if a
year-specific code dictionary in `Commissioner/census_<year>_codes.json` turns out to
be wrong or incomplete, fixing it and re-running Archivist against the same
already-gathered JSON is enough - there is no need to re-scrape the source site.

This is also why Archivist's `run_general_flavor`/`run_census_flavor` (ARCH-3) build a
fresh `RunConfig` per call and never mutate module globals: decoding is meant to be
safely re-run, repeatedly, against the same static input, and gather-time global state
leaking into that process would undermine the whole point of the split.
