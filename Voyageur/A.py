import json
import os
import re
import sys
import urllib.parse
import uuid
from pathlib import Path
import census_schema

# Commissioner lives in a sibling tool folder, not an installed package - add the repo
# root to sys.path so its shared env loader can be imported when this sub-script is run
# directly (python Voyageur/A.py); a no-op inside the frozen app bundle.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from Commissioner.envkit import load_tool_env  # noqa: E402

from _gather_helpers import (  # noqa: E402
    cleanup_checkpoint_files,
    extract_census_image_routing_fields,
    find_orphaned_gather_runs,
    launch_gather_browser,
    move_downloaded_images,
    move_with_retry,
    print_incomplete_pages_warning,
    resolve_census_image_dir,
    wait_for_final_json_event,
    write_archivist_json_file,
)


# ==========================================
# URL PARSING
# ==========================================
def parse_ancestry_url(url: str):
    """Extract APID (dbid) and Start Record ID from Ancestry URLs."""
    view_match = re.search(r'view/(\d+):(\d+)', url)
    if view_match:
        return view_match.group(2), view_match.group(1)

    col_match = re.search(r'collections/(\d+)', url)
    pid_match = re.search(r'[?&]pId=(\d+)', url, re.IGNORECASE)
    if col_match and pid_match:
        return col_match.group(1), pid_match.group(1)

    # Newer imageviewer URLs drop pId entirely and put the image number straight in the
    # path instead: .../collections/<dbid>/images/<imageNum>-<slug>.
    img_match = re.search(r'collections/(\d+)/images/(\d+)', url)
    if img_match:
        return img_match.group(1), img_match.group(2)

    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if 'dbid' in qs and 'h' in qs:
        return qs['dbid'][0], qs['h'][0]

    return None, None


def normalize_ancestry_census_gather(raw_gather: dict, dbid: str = "") -> dict:
    """Translates a raw Ancestry census gather into the shared record schema, deriving
    collection_title/record_type_name from the gather's own census_year/location - the
    exact translation main() applies at gather time, pulled out here so it's testable
    without a browser session. Only ever used as a last-resort fallback title -
    census_schema.normalize_census_pages() prefers each page's own real
    citation.collection_name (Ancestry's own scraped citation text) over this whenever
    one was actually captured.

    country is read back from the raw gather's own first page (Voyageur.js's
    ancestryCountryFromState() is the only place that determines it) rather than
    re-derived or hardcoded here, plugged directly into the label - never a fixed
    US-or-Canada choice, so any country this project ever gathers is handled the same
    way with no country list to maintain. Confirmed live (2026-08-15 Canadian gather,
    dbId 1578, Ontario) that hardcoding "US Federal Census" unconditionally mislabeled
    every Canadian record all the way through to the generated GEDCOM's citation text."""
    census_year_raw = raw_gather.get("census_year", "")
    first_page_country = next(
        (p.get("country") for p in raw_gather.get("pages", []) if p.get("country")), "USA")
    collection_title = f"{census_year_raw} {first_page_country} Census - {raw_gather.get('location', '')}".strip(" -")
    normalized = census_schema.normalize_and_validate_census(
        raw_gather, "ancestry_census", collection_title, f"Census_{census_year_raw}")
    if dbid:
        for page in normalized.get("pages", []):
            page["apid_db"] = dbid
    return normalized


def _recover_orphaned_runs(downloads_dir: Path, current_run_id: str, json_target_dir: Path,
                           genealogy_dir: str) -> None:
    """Recovers gather output left behind by a previous run that had no watcher present to
    collect it (e.g. a gather started via Voyageur.js's own manual "Start Auto-Batch" button
    instead of through this script) - moves the JSON and images into their normal project
    locations. The Ancestry-specific header-normalization/apid_db-tagging pass is skipped
    for recovered runs: it needs the originating record's dbid, parsed from A_URL, which
    isn't reliably known for a run this script didn't itself launch. An incomplete stale run
    (checkpoints only, no final JSON - the browser gather itself never finished) is reported,
    not guessed at."""
    orphans = find_orphaned_gather_runs(downloads_dir, "TMP_A_", current_run_id)
    if not orphans:
        return

    for run_id, group in orphans.items():
        if group["final"] is None:
            names = ", ".join(p.name for p in group["checkpoints"] + group["images"])
            print(f"[WARN] Found an incomplete stale gather (run {run_id}) with no final JSON - "
                  f"left in place for manual review: {names}")
            continue

        final_name = group["final"].name[len(f"TMP_A_{run_id}_"):]
        recovered_json = json_target_dir / final_name
        # Deliberately hardcoded, not the run's own GATHER_ON_COLLISION setting: recovered data
        # is best-effort, so defensively skip an existing file rather than overwrite it.
        try:
            json_status = move_with_retry(group["final"], recovered_json, on_collision="skip")
        except OSError as e:
            print(f"[WARN] Could not recover run {run_id}'s final JSON ({group['final'].name}): {e}")
            continue
        if json_status == "skipped":
            print(f"[System] Recovered run {run_id}: {final_name} already exists in Project folder, "
                  f"discarding the stale copy (images below are still recovered).")

        # Best-effort only, matching this function's own header-normalization-skipped
        # philosophy (see docstring) - opportunistically read routing fields back from
        # whatever JSON shape actually landed, falling back to empty/generic defaults
        # rather than guessing when the file isn't in the expected shape. The routing-field
        # extraction stays inside this same try: a recovered file can parse as valid JSON
        # yet have an unexpected shape (e.g. a non-dict sheet/record), which must fall back
        # the same as a read/parse failure, not crash main() before the gather even starts.
        census_year, country, location_folder = "", "", ""
        try:
            with open(recovered_json, "r", encoding="utf-8") as f:
                recovered_data = json.load(f)
            census_year, country, location_folder, _ = \
                extract_census_image_routing_fields(recovered_data)
        except (OSError, json.JSONDecodeError, AttributeError, IndexError, TypeError):
            pass
        img_target_dir = resolve_census_image_dir(
            "Census", genealogy_dir, census_year, country, location_folder)

        img_moved, img_skipped, img_failed = move_downloaded_images(
            downloads_dir, f"TMP_A_{run_id}_Images_", 0, img_target_dir, on_collision="skip")
        print(f"[System] Recovered stale run {run_id}: moved {final_name} and {img_moved} image(s) "
              f"to Project folders. NOTE: header normalization was skipped for this recovered run - "
              f"verify column names before relying on it.")


# ==========================================
# MAIN EXECUTION
# ==========================================
def main() -> Path:
    print("========================================")
    print(" Voyageur (A) - Ancestry Gather Automation")
    print("========================================")

    # Global settings come from the project root's .env; this tool's own settings come
    # from its own subfolder's .env, so this sub-script stays runnable standalone. Tool
    # .env overrides global .env (root first, tool second, both override=True) - see
    # Commissioner/envkit.py.
    load_tool_env(Path(__file__).resolve().parent)

    program_dir = os.getenv("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent))
    genealogy_dir = os.getenv("GENEALOGY_DIR", "")
    url = os.getenv("GATHER_URL", "").strip()
    json_dir = os.getenv("JSON_DIR", "JSON")
    on_collision = os.getenv("GATHER_ON_COLLISION", "overwrite").strip().lower()
    # Matches Antiquarian.py's own default ("Census", resolved against
    # MEDIA_DIR by the GUI before this ever runs).
    base_img_setting = "Census"

    if not url:
        print("[ERROR] Please enter an Ancestry URL in the Toolbox settings first.")
        sys.exit(1)

    dbid, start_id = parse_ancestry_url(url)
    if not dbid or not start_id:
        if "ancestry.com" not in url.lower():
            print("[ERROR] The Gather URL doesn't look like an Ancestry link. "
                  "'Ancestry' is selected as the source in the Voyageur settings, but the "
                  "URL in the Gather URL field points elsewhere - check that field and the "
                  "source dropdown agree.")
        else:
            print("[ERROR] Could not parse database ID (dbid) or record ID (h) from the URL.")
        sys.exit(1)

    print(f"[System] Extracted -> DBID: {dbid} | Start ID: {start_id}")

    # Voyageur.js downloads via plain <a download> rather than GM_download (see CHANGELOG -
    # GM_download's permission grant proved unreliable). Chrome replaces "/" in a download
    # attribute with "_" instead of creating subfolders, so these land flat in the Downloads
    # root with a "TMP_A_"/"TMP_A_Images_" filename prefix instead of a real subfolder -
    # that prefix is also what lets this scan pick its own files out from whatever else
    # happens to be in the Downloads root.
    downloads_dir = Path.home() / "Downloads"
    json_target_dir = Path(program_dir) / json_dir if program_dir else Path(json_dir)
    json_target_dir.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex[:8]
    json_prefix = f"TMP_A_{run_id}_"
    image_prefix = f"TMP_A_{run_id}_Images_"

    # Recover anything a previous run left stranded before this run's own files - which use
    # a fresh, guaranteed-unique run_id - are even downloaded. See the Downloads Handling
    # Redesign spec (docs/superpowers/specs/2026-08-13-voyageur-downloads-handling-design.md).
    _recover_orphaned_runs(downloads_dir, run_id, json_target_dir, genealogy_dir)

    start_time = launch_gather_browser(url, run_id)

    json_file = wait_for_final_json_event(downloads_dir, json_prefix, "Final JSON")

    print("\n[System] Processing extracted files...")

    final_json = json_target_dir / json_file.name[len(json_prefix):]
    json_status = move_with_retry(json_file, final_json, on_collision=on_collision)
    cleanup_checkpoint_files(downloads_dir, json_prefix, start_time)

    # Normalize at gather time: translate Ancestry's own raw column header text into the
    # shared record schema's field names via the declarative field map, so Archivist never
    # has to guess among several possible header spellings downstream. Overwrites the same
    # file in place - Archivist still just reads whatever JSON_FILE points to. Skipped when
    # json_status == "skipped": that means an existing final_json was kept as-is (collision
    # policy), and it was already normalized whenever it was first produced - leaving it
    # untouched is what "skip" is supposed to mean.
    # Real country for the image-folder path below (see resolve_census_image_dir()) - read
    # from the gather itself, never hardcoded. Only available when this run actually
    # normalized the data (json_status == "moved"); a "skipped" run kept an existing file
    # as-is and never re-read it, so country comes back blank instead - a rare edge case,
    # since a fresh gather almost always produces "moved".
    if json_status == "moved":
        with open(final_json, "r", encoding="utf-8") as f:
            raw_gather = json.load(f)
        print_incomplete_pages_warning(raw_gather.get("incomplete_pages", []), "page(s)")
        normalized = normalize_ancestry_census_gather(raw_gather, dbid)

        from _gather_helpers import build_detailed_census_filename
        clean_name = build_detailed_census_filename(raw_gather.get("census_year", ""), normalized, "Ancestry")
        if clean_name:
            new_final_json = final_json.with_name(clean_name)
            if new_final_json != final_json:
                final_json.replace(new_final_json)
                final_json = new_final_json

        with open(final_json, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
    else:
        try:
            with open(final_json, "r", encoding="utf-8") as f:
                normalized = json.load(f)
        except (OSError, json.JSONDecodeError):
            normalized = {}

    # Persist this as the JSON_FILE setting immediately, before anything below (the image
    # move) can fail - so even if that fails, Archivist's "Generate GEDCOM" (the manual/retry
    # button) still targets the exact file that was just produced, instead of whatever
    # JSON_FILE happened to be set to before this run started. JSON_FILE is read by
    # Archivist (see its own ARCHIVIST_VARS entry in Antiquarian.py), so it's written to
    # Archivist's own .env, not this script's own subfolder one - confirmed live this was
    # the actual bug behind Archivist immediately failing with FileNotFoundError right
    # after a real gather succeeded: this used to write to Voyageur/.env, a file Archivist
    # never reads at all.
    write_archivist_json_file(final_json.name)

    census_year, country, location_folder, _ = \
        extract_census_image_routing_fields(normalized)

    # CENSUS_IMAGE_DIR is a subfolder *of the Base Media Directory* (MEDIA_DIR), not of
    # PROGRAM_DIR directly. Resolved here independently (rather than relying on
    # Antiquarian.py's GUI to pre-resolve it into an absolute path before launching this
    # as a subprocess) so this script produces correct output standalone, with nothing
    # else open. An already-absolute CENSUS_IMAGE_DIR (whether GUI-resolved or set
    # directly by the user) is used as-is, never re-nested.
    img_target_dir = resolve_census_image_dir(
        base_img_setting, genealogy_dir, census_year, country, location_folder)

    img_moved, img_skipped, img_failed = move_downloaded_images(
        downloads_dir, image_prefix, start_time, img_target_dir, on_collision=on_collision)
    json_note = "moved" if json_status == "moved" else "kept existing (skipped)"
    print(f"[System] JSON {json_note}; moved {img_moved} image(s)"
          f"{f', skipped {len(img_skipped)}' if img_skipped else ''}"
          f"{f', {len(img_failed)} FAILED' if img_failed else ''} to Project folders.")
    print(f"[System] Gather complete. Run Archivist's \"Generate GEDCOM\" when you're ready to "
          f"build the GEDCOM ({final_json.name}).")

    # Gather's job stops here - it stages the JSON (already normalized into the shared
    # sheets[].records[].participants[] schema above) and images and hands off, rather
    # than launching GEDCOM generation itself.
    return final_json


if __name__ == "__main__":
    main()
