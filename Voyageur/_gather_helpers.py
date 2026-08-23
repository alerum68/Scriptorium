"""
Shared gather helpers for Voyageur's provider scripts (A.py, FS.py). Chrome (or antivirus
scanning it) can still hold a freshly-downloaded file open for a brief moment after it
appears in the folder listing, so an immediate shutil.move/unlink can lose to a transient
PermissionError/WinError 32 on Windows - these retry helpers ride out that window instead
of letting a gather crash with the file left stranded. The remaining functions here are the
Tampermonkey-download-wait/image-move/image-dir-resolution/Archivist-write-back logic that
used to be duplicated between A.py and FS.py's own main().
"""

import os
import re
import sys
import threading
import time
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Optional

from dotenv import set_key
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Commissioner.winio import move_with_retry  # noqa: E402


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Writes data to path via a temp file + atomic rename, so a crash/interruption
    mid-write never leaves a truncated file sitting at the final path - a caller that
    skips re-downloading an already-existing file would otherwise treat that truncated
    file as complete forever. On any failure the temp file is removed and the original
    exception re-raised; the final path is never touched unless the write fully
    succeeded."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def cleanup_checkpoint_files(downloads_dir: Path, prefix: str, start_time: float) -> None:
    """Deletes this run's own leftover periodic checkpoint downloads (see
    downloadCheckpointJson in Voyageur.js) now that the final combined JSON has already
    been moved/written out - they're superseded and, unlike the final JSON, nothing else
    ever cleans them up, so a long gather would otherwise leave several of them sitting in
    the Downloads folder permanently. Best-effort: a checkpoint that can't be deleted (still
    briefly locked, already gone) is left in place rather than raising."""
    for p in downloads_dir.iterdir():
        if (p.is_file() and p.suffix.lower() == '.json' and p.name.startswith(prefix)
                and '[checkpoint' in p.name and p.stat().st_mtime >= start_time):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def find_orphaned_gather_runs(downloads_dir: Path, source_prefix: str,
                              current_run_id: str) -> dict:
    """Returns the leftover TMP_<source>_<runId>_* files in downloads_dir grouped by
    their embedded run ID, excluding the run that is still in progress. Each run's
    entry is {'final': Path | None, 'checkpoints': [Path, ...], 'images': [Path, ...]}:
    a run_id with 'final' set is a complete, recoverable gather; one with only
    checkpoints/images never produced its final JSON and is reported as-is - no final
    is ever guessed or synthesized. Only regular files are considered, and only those
    whose name starts with source_prefix (the source's own fixed prefix, e.g.
    'TMP_A_' or 'TMP_FS_'); the run ID is the text between source_prefix and the next
    '_' (run IDs are plain hex so they never contain one themselves). A .jpg belongs in
    'images'; a .json belongs in 'final' (the run's own final JSON - whichever .json is
    not a checkpoint; at most one per run in practice); a .json whose remainder contains
    the literal 'checkpoint' substring (e.g. ..._checkpoint_<N>.json) belongs in
    'checkpoints'."""
    runs: dict = {}
    for p in downloads_dir.iterdir():
        if not p.is_file() or not p.name.startswith(source_prefix):
            continue
        rest = p.name[len(source_prefix):]
        if '_' not in rest:
            continue
        run_id, remainder = rest.split('_', 1)
        if run_id == current_run_id:
            continue
        entry = runs.setdefault(run_id, {'final': None, 'checkpoints': [], 'images': []})
        if p.suffix.lower() == '.jpg':
            entry['images'].append(p)
        elif p.suffix.lower() == '.json':
            if 'checkpoint' in remainder:
                entry['checkpoints'].append(p)
            else:
                entry['final'] = p
    return runs


def build_detailed_census_filename(year: str, normalized_data: dict, provider: str) -> Optional[str]:
    """Builds a '(Year) Census - (Country) - (State) - (County) - (City, township, ED) - (Provider).json'
    filename matching the specific request."""
    prov_abbr = "ANC" if provider.lower() == "ancestry" else (
        "FS" if provider.lower() in ("familysearch", "fs") else provider)

    for sheet in normalized_data.get("sheets", []):
        for record in sheet.get("records", []):
            fields = record.get("type_specific_fields", {}) or {}
            parts = [f"Census-{year}" if year else "Census"]
            if fields.get("country"):
                parts.append(fields["country"])
            if fields.get("state"):
                parts.append(fields["state"])
            if fields.get("county"):
                parts.append(fields["county"])

            if fields.get("city"):
                parts.append(fields["city"])
            if fields.get("enumeration_district"):
                parts.append(fields["enumeration_district"])

            if prov_abbr:
                parts.append(prov_abbr)

            if len(parts) > 1:
                safe = "-".join(parts)
                safe = re.sub(r'[/\\?%*:|"<>]', "-", safe).strip()
                return f"{safe}.json"
    return None


def build_gather_launch_url(url: str, run_id: str) -> str:
    """Appends the auto-start flag and this run's own unique ID to the gather URL so
    Voyageur.js can read both from window.location.href: mgs_auto=1 starts the batch
    automatically, mgs_run=<id> is embedded into every filename this run downloads so two
    runs (even of the identical record) never produce colliding Downloads filenames - see
    the Voyageur Downloads Handling Redesign spec."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}mgs_auto=1&mgs_run={run_id}"


def launch_gather_browser(url: str, run_id: str) -> float:
    start_time = time.time()
    auto_url = build_gather_launch_url(url, run_id)
    print("[System] Launching browser...")
    webbrowser.open(auto_url)
    print("\n[System] Waiting for Tampermonkey downloads (Auto-Batch will start automatically)...")
    return start_time


def _block_until_ready(ready: threading.Event) -> None:
    ready.wait()


def wait_for_final_json_event(downloads_dir: Path, json_prefix: str, label: str) -> Path:
    """State-based replacement for the old time.sleep(1) polling loop: blocks on a real
    filesystem "file created" event for this run's own final JSON instead of waking up
    once a second to re-scan the directory. No timeout, matching the polling loop's own
    previous behavior (it never gave up on its own either) - just event-driven instead of
    poll-driven, per project direction (prefer state-based waiting over timers wherever a
    real event exists)."""
    ready = threading.Event()
    found: dict = {}

    def matches(p: Path) -> bool:
        return (p.suffix.lower() == '.json' and p.name.startswith(json_prefix)
                and '[checkpoint' not in p.name)

    class _FinalJsonHandler(FileSystemEventHandler):
        def _set_found_if_match(self, path: str) -> None:
            p = Path(path)
            if matches(p):
                found['path'] = p
                ready.set()

        def on_created(self, event):
            if event.is_directory:
                return
            self._set_found_if_match(event.src_path)

        def on_moved(self, event):
            if event.is_directory:
                return
            # Chrome stages downloads to a .crdownload file (which only fires on_created
            # for the staging name, never the final name) and then renames it to the
            # final filename via a filesystem move - so the final file's arrival is only
            # ever observed here, against dest_path (the new location), not src_path.
            self._set_found_if_match(event.dest_path)

    observer = Observer()
    observer.schedule(_FinalJsonHandler(), str(downloads_dir), recursive=False)
    observer.start()
    try:
        # A file matching this run's own final-JSON pattern may already exist by the time
        # the observer is attached (e.g. a very fast gather) - its on_created event would
        # never fire since it already happened, so check directly before waiting.
        existing = [p for p in downloads_dir.iterdir() if p.is_file() and matches(p)]
        if existing:
            found['path'] = max(existing, key=lambda p: p.stat().st_mtime)
            ready.set()

        _block_until_ready(ready)
    except KeyboardInterrupt:
        print("\n[System] Operation cancelled by user.")
        sys.exit(0)
    finally:
        observer.stop()
        observer.join()

    print(f"[System] Detected {label}: {found['path'].name}")
    return found['path']


def move_downloaded_images(downloads_dir: Path, image_prefix: str, start_time: float,
                           img_target_dir: Path, on_collision: str = "overwrite") -> tuple:
    """Moves every downloaded image to img_target_dir. A move that fails (e.g. a transient
    Windows file lock outlasting move_with_retry's own retries) is not fatal - it's retried
    once more, in a second pass over just the failures, after the rest of the batch has had
    a chance to finish and any lock has had a moment longer to clear. Returns
    (moved_count, skipped_names, failed_names)."""
    image_candidates = [
        p for p in downloads_dir.iterdir()
        if p.is_file() and p.suffix.lower() == '.jpg'
        and p.name.startswith(image_prefix) and p.stat().st_mtime >= start_time
    ]

    moved = []
    skipped = []
    failed = {}  # prefixed source name -> (file_path, final name) for files still pending

    def attempt(candidate_path: Path) -> None:
        final_name = candidate_path.name[len(image_prefix):]
        final_img = img_target_dir / final_name
        status = move_with_retry(candidate_path, final_img, on_collision=on_collision)
        (moved if status == "moved" else skipped).append(final_name)

    for file_path in image_candidates:
        # noinspection broad-exception
        try:
            attempt(file_path)
        except Exception as e:
            print(f"[ERROR] Could not move image {file_path.name}: {e}")
            failed[file_path.name] = file_path

    if failed:
        print(f"[System] Retrying {len(failed)} failed image move(s) once...")
        time.sleep(1.0)
        for name, file_path in list(failed.items()):
            if not file_path.exists():
                continue
            # noinspection broad-exception
            try:
                attempt(file_path)
                del failed[name]
            except Exception as e:
                print(f"[ERROR] Retry failed for {name}: {e}")

    final_failed_names = [name[len(image_prefix):] for name in failed]
    if final_failed_names:
        print(f"[WARN] {len(final_failed_names)} image(s) could not be moved after retry: "
              f"{', '.join(final_failed_names)}")

    return len(moved), skipped, final_failed_names


def _mode_or_first(values: list) -> str:
    """Most common non-blank value, falling back to the first non-blank one on a tie
    (Counter.most_common's own tie-break: insertion order) - never the empty string as long
    as at least one real value exists."""
    counts = Counter(v for v in values if v)
    return counts.most_common(1)[0][0] if counts else ""


def extract_census_image_routing_fields(final_data: dict) -> tuple[str, str, str, str]:
    """Extracts (census_year, country, location_folder, collection_name) from a normalised
    gather dict (output of normalize_*_census_gather). Used by both FS.py and A.py to
    route images without re-parsing the filename.

    State/county/city are each the most common non-blank
    value across ALL records, not just the first one - confirmed live this was a real bug,
    not a hypothetical: a real 1950 Pembina, ND gather had its very first record's own
    'state' field read "Advance" (a citation-parsing artifact for that one record) while the
    true majority of records correctly said "North Dakota" - routing every image by the
    first record alone sent them all into a one-off "Advance" folder that didn't match what
    Archivist/Census.py's own get_json_fallback() (already mode-based) put in the GEDCOM,
    producing a FILE reference to a folder the images were never actually saved in. Same
    fix as that function's own reasoning, applied consistently here too.

    location_folder is built as 'state - county - city - ED' (each segment omitted when
    blank) matching the ' - ' split resolve_census_image_dir uses internally. Nests
    one level past city, down to the enumeration district - a
    single city/township can span several EDs, so ED is the level that actually
    disambiguates one image set from another within it."""
    census_year = ""
    countries: list = []
    states: list = []
    counties: list = []
    cities: list = []
    eds: list = []
    for sheet in final_data.get("sheets", []):
        for record in sheet.get("records", []) or []:
            record = record or {}
            if not census_year:
                # normalize_census_pages() stores year on the record itself (a sibling of
                # type_specific_fields), not inside type_specific_fields - see census_schema.py.
                census_year = record.get("year", "") or ""
            fields = record.get("type_specific_fields", {}) or {}
            countries.append(fields.get("country", ""))
            states.append(fields.get("state", ""))
            counties.append(fields.get("county", ""))
            cities.append(fields.get("city", ""))
            eds.append(fields.get("enumeration_district", ""))

    country = _mode_or_first(countries)
    location_parts = [_mode_or_first(states), _mode_or_first(counties), _mode_or_first(cities),
                      _mode_or_first(eds)]
    location_folder = " - ".join(p for p in location_parts if p)
    collection_name = final_data.get("citation", {}).get("collection_name", "") or ""
    return census_year, country, location_folder, collection_name


def resolve_census_image_dir(base_img_setting: str, genealogy_dir: str,
                             year: str, country: str, location_folder: str) -> Path:
    """Resolves the image target directory for a census gather.

    Path structure: <base_img_dir>/<country>/<year>/<location_parts...>, matching the
    project's Media\\Census\\Country\\Year\\State\\County\\City\\ convention (see
    docs/plans/2026-08-17-media-directory-structure.md) - no separate collection-name
    wrapper folder; country and year are each skipped when blank, then location_folder is
    split on ' - ' to form the remaining leaf segments.
    """
    if os.path.isabs(base_img_setting):
        base_img_dir = Path(base_img_setting)
    else:
        media_setting = os.getenv("MEDIA_DIR", "Media")
        base_media_dir = Path(media_setting) if os.path.isabs(media_setting) else (
            Path(genealogy_dir) / media_setting if genealogy_dir else Path(media_setting))
        base_img_dir = base_media_dir / base_img_setting

    parts = []
    if country:
        parts.append(country)
    if year:
        parts.append(year)

    location_parts = [p.strip() for p in location_folder.split(' - ') if p.strip()]
    parts.extend(location_parts)

    img_target_dir = base_img_dir.joinpath(*parts) if parts else base_img_dir
    img_target_dir.mkdir(parents=True, exist_ok=True)
    return img_target_dir


def write_archivist_json_file(final_json_name: str) -> None:
    set_key(str(Path(__file__).resolve().parent.parent / "Archivist" / ".env"), "JSON_FILE", final_json_name)


def print_incomplete_pages_warning(entries: list, label: str) -> None:
    if not entries:
        return
    border = "!" * 70
    print(f"\n{border}")
    print(f"[WARNING] {len(entries)} {label} incomplete - no index data received:")
    for entry in entries:
        page_number = entry.get("page_number")
        identifier = entry.get("image_id") or entry.get("item_id", "")
        if page_number is not None:
            print(f"  - page {page_number} (image {identifier})")
        else:
            print(f"  - item {identifier}")
    print(f"{border}\n")
