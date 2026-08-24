import argparse
import json
import multiprocessing as mp
import os
import queue
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Commissioner lives in a sibling tool folder, not an installed package - add the repo root
# to sys.path so it can be imported by absolute path, matching census_schema.py's own
# precedent for cross-package imports (Voyageur/census_schema.py:28-35).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Commissioner.envkit import load_tool_env  # noqa: E402

load_tool_env(Path(__file__).resolve().parent)

try:
    import lac_client
except ImportError:
    from Voyageur import lac_client

try:
    from _gather_helpers import atomic_write_bytes
except ImportError:
    from Voyageur._gather_helpers import atomic_write_bytes


try:
    from Archivist.Utils import safe_path  # noqa: E402
except (ImportError, AttributeError):
    import importlib.util
    _u_spec = importlib.util.spec_from_file_location("Archivist_Utils", _REPO_ROOT / "Archivist" / "Utils.py")
    _u_mod = importlib.util.module_from_spec(_u_spec)
    _u_spec.loader.exec_module(_u_mod)
    safe_path = _u_mod.safe_path
from Commissioner.jsonio import (  # noqa: E402
    atomic_write_json,
    load_checkpoint as jsonio_load_checkpoint,
    save_checkpoint as jsonio_save_checkpoint,
)
from Commissioner.record_registry import resolve_generic_setting  # noqa: E402

# ==========================================
# PATH & CONFIG SETUP
# ==========================================
_safe_path = safe_path


PROGRAM_DIR = os.environ.get("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent)).strip()
GENEALOGY_DIR = os.environ.get("GENEALOGY_DIR", "").strip()
MEDIA_DIR = _safe_path(GENEALOGY_DIR, os.environ.get("MEDIA_DIR", "Media").strip())
CHECKPOINT_DIR = _safe_path(PROGRAM_DIR, "Working/LAC")
COOKIE_FILE = _safe_path(PROGRAM_DIR, "Working/LAC/lac_cookies.txt")
DEFAULT_ARCHIVAL_NUMBER = "RG15"
CDP_PORT = 9222


def resolve_master_db_path(document_type: str, program_dir: str) -> str:
    """Resolves the absolute path to Paleographer's own MASTER_DB for document_type,
    matching Paleographer.py's own MASTER_DB derivation (PROGRAM_DIR / JSON_DIR /
    MASTER_DB_NAME) exactly, so scaffold sheets Voyageur writes land in the same file
    Paleographer itself reads/writes."""
    master_db_name = resolve_generic_setting(document_type, "MASTER_DB_NAME")
    if not master_db_name:
        raise RuntimeError(
            f"MASTER_DB_NAME resolved to an empty value for document_type {document_type!r} "
            f"(check the active record type's own MASTER_DB_NAME setting, e.g. "
            f"CHURCH_MASTER_DB_NAME for Parish).")
    json_dir = os.environ.get("JSON_DIR", "")
    return str(Path(program_dir) / json_dir / master_db_name)


def load_master_db(master_db_path: str, collection_title: str, record_type_name: str) -> Dict[str, Any]:
    default = {
        "collection_title": collection_title, "record_type_name": record_type_name, "sheets": [],
        "total_spent": 0.0, "total_pages_processed": 0, "pending_batch_jobs": [],
    }
    if os.path.exists(master_db_path):
        try:
            with open(master_db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to read MASTER_DB {master_db_path}: {e}")
            return default
    return default


def save_master_db(master_db_path: str, master_data: Dict[str, Any]) -> None:
    atomic_write_json(master_db_path, master_data, indent=2, ensure_ascii=False)


def append_scaffold_sheets(master_data: Dict[str, Any], new_sheets: List[Dict[str, Any]]) -> None:
    """Appends Voyageur-built placeholder sheets into master_data["sheets"], deduplicating
    by document_metadata.file_name against sheets already present. Guards a crash-and-resume
    scenario: download_pid_bundle skips the actual file download when it already exists on
    disk, but is not itself responsible for skipping the caller's own scaffold-sheet append,
    so a resumed run that re-touches an already-checkpointed PID must not write a duplicate
    placeholder for the same file_name."""
    master_sheets = master_data.setdefault("sheets", [])
    existing_file_names = {sheet.get("document_metadata", {}).get("file_name") for sheet in master_sheets}
    for sheet in new_sheets:
        file_name = sheet.get("document_metadata", {}).get("file_name")
        if file_name is not None and file_name in existing_file_names:
            continue
        master_sheets.append(sheet)
        existing_file_names.add(file_name)


RECORD_TYPE_ARG_TO_DOCUMENT_TYPE = {"parish": "Parish", "scrip": "Scrip"}


def _resolve_record_type(record_type_arg: str) -> str:
    document_type = RECORD_TYPE_ARG_TO_DOCUMENT_TYPE.get(record_type_arg)
    if document_type is None:
        print("[ERROR] --record-type is required (parish or scrip) - or set LAC_RECORD_TYPE in .env.")
        sys.exit(1)
    return document_type


def load_cookies(cookie_file: str = COOKIE_FILE, cdp_port: int = CDP_PORT) -> Dict[str, str]:
    """Loads search cookies from a debuggable browser or a cookie file."""
    try:
        return lac_client.load_cookies_from_cdp(port=cdp_port)
    except lac_client.LacCallError:
        pass

    path = Path(cookie_file)
    if not path.is_file():
        raise FileNotFoundError(f"No cookie file at {path}.")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"Cookie file {path} is empty.")
    return lac_client.parse_cookie_header(raw)


# ==========================================
# CANADIANA IIIF MANIFEST & DOWNLOAD
# ==========================================
def parse_url(raw_url: str) -> Tuple[str, str]:
    """Sanitizes the user's pasted URL into a proper IIIF manifest API call."""
    print(f"[Info] Target URL: {raw_url}")
    base_id_match = re.search(r'(oocihm\.lac_reel_[a-zA-Z0-9]+)', raw_url, re.IGNORECASE)
    if not base_id_match:
        print("[Error] Could not find a valid Canadiana identifier (oocihm.lac_reel...) in the URL.")
        sys.exit(1)

    base_id = base_id_match.group(1)
    roll_match = re.search(r'lac_reel_([a-zA-Z0-9]+)', base_id, re.IGNORECASE)
    roll_num = roll_match.group(1) if roll_match else "Unknown_Roll"
    print(f"[Info] Extracted Roll Number: {roll_num}")
    manifest_url = f"https://heritage.canadiana.ca/iiif/{base_id}/manifest"
    return roll_num, manifest_url


def setup_directories(program_dir: str, media_dir: str, roll_num: str) -> str:
    """Constructs the final output path relative to the user's media directory."""
    if os.path.isabs(media_dir):
        base_media = media_dir
    else:
        base_media = os.path.join(program_dir, media_dir)
    out_dir = os.path.join(base_media, "LAC", roll_num).replace("\\", "/")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def download_manifest(manifest_url: str) -> Dict[str, Any]:
    """Fetches the IIIF structural blueprint for the film roll."""
    print("[Info] Downloading manifest file...")
    try:
        response = requests.get(manifest_url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Error] Failed to fetch or parse manifest: {e}")
        sys.exit(1)


def download_images(manifest_data: Dict[str, Any], out_dir: str, roll_num: str,
                    master_db_path: str, document_type: str, collection_title: str) -> Dict[str, str]:
    """Loops through the manifest canvases, downloads max-resolution files, and seeds
    Paleographer's own MASTER_DB with one Commissioner-shaped scaffold sheet per canvas -
    for both a freshly downloaded image and one already on disk from a prior run, so a
    MASTER_DB reset/first-time run still ends up fully seeded. See the
    Voyageur-Parish-Scrip-scaffold design spec. Returns a {canvas number: error} dict of
    any canvases that failed to download - a failure is tracked and reported, not just
    printed and forgotten, mirroring download_volume_assets's failed_pids.

    save_master_db is flushed every FLUSH_EVERY_N canvases rather than after each one -
    for a 720-page reel, writing the entire (progressively larger) master_data dict to
    disk on every single canvas is the same re-serialize-the-whole-file cost that
    download_volume_assets_multiworker's docstring calls out. A guaranteed final flush
    after the loop means nothing is lost even if the last batch is smaller than N."""
    from Commissioner.record_registry import build_empty_sheet, validate_collection_softly

    FLUSH_EVERY_N = 10

    if "sequences" in manifest_data and manifest_data["sequences"]:
        canvases = manifest_data["sequences"][0].get("canvases", [])
    elif "items" in manifest_data:
        canvases = manifest_data.get("items", [])
    else:
        print("[Error] No valid sequences or items found in the manifest.")
        print(f"[Debug] Manifest Keys returned: {list(manifest_data.keys())}")
        sys.exit(1)

    total = len(canvases)
    if total == 0:
        print("[Error] No images found in the manifest.")
        sys.exit(1)

    print(f"[Info] Found {total} images to download.")
    session = requests.Session()
    master_data = load_master_db(master_db_path, collection_title, document_type)
    failed: Dict[str, str] = {}
    unflushed = 0

    for i, canvas in enumerate(canvases, 1):
        try:
            img_id = ""
            if "images" in canvas:
                images = canvas.get("images", [])
                if images:
                    resource = images[0].get("resource", {})
                    img_id = resource.get("@id", "")
            elif "items" in canvas:
                items = canvas.get("items", [])
                if items:
                    annotations = items[0].get("items", [])
                    if annotations:
                        body = annotations[0].get("body", {})
                        if isinstance(body, dict):
                            img_id = body.get("id", "")
                        elif isinstance(body, list) and body:
                            img_id = body[0].get("id", "")

            if not img_id:
                print(f"\n[Warning] Could not extract image URL from canvas {i}")
                failed[str(i)] = "No image URL in canvas"
                continue

            filename = f"{roll_num}_{i:04d}.jpg"
            filepath = os.path.join(out_dir, filename)
            page_id = f"{roll_num}_{i:04d}"

            print(f"\rDownloading [{i}/{total}]...", end="", flush=True)

            on_collision = os.getenv("GATHER_ON_COLLISION", "skip").strip().lower()
            if on_collision == "skip" and os.path.exists(filepath):
                pass
            else:
                img_resp = session.get(img_id, timeout=20)
                img_resp.raise_for_status()
                atomic_write_bytes(Path(filepath), img_resp.content)

            new_sheet = build_empty_sheet(filename, "jpg", page_id=page_id)
            append_scaffold_sheets(master_data, [new_sheet])
            validate_collection_softly(master_data, document_type, collection_title)
            unflushed += 1
            if unflushed >= FLUSH_EVERY_N:
                save_master_db(master_db_path, master_data)
                unflushed = 0

        except Exception as e:
            print(f"\n[Warning] Failed to download image {i}: {e}")
            failed[str(i)] = str(e)

    if unflushed:
        save_master_db(master_db_path, master_data)

    if failed:
        print(f"\n[Warning] {len(failed)} image(s) failed to download: {', '.join(sorted(failed))}")
        print(f"\n\n[System] LAC Download for {roll_num} completed with {len(failed)} failure(s).")
    else:
        print(f"\n\n[System] LAC Download for {roll_num} completed successfully!")
    return failed


# ==========================================
# VOLUME HARVEST & PID BUNDLE DOWNLOADS
# ==========================================
def _document_type_for_asset(asset: "lac_client.DigitalObject") -> str:
    return asset.label or "LAC Digital Object"


def download_pid_bundle(pid: str, media_dir: str,
                        document_type_override: Optional[str] = None) -> Dict[str, Any]:
    """Fetches record metadata + manifest for a PID, downloads every digital object to
    media_dir/{pid}/{asset_id}.{ext}, and returns bundle dict."""
    metadata = lac_client.get_record_metadata(pid)
    assets = lac_client.get_manifest(pid)

    pid_dir = Path(media_dir) / pid
    pid_dir.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    on_collision = os.getenv("GATHER_ON_COLLISION", "skip").strip().lower()

    for asset in assets:
        ext = "pdf" if asset.op == "pdf" else "jpg"
        file_path = pid_dir / f"{asset.asset_id}.{ext}"
        if on_collision == "skip" and file_path.exists():
            pass
        else:
            data = lac_client.download_asset(asset.asset_id, asset.op)
            atomic_write_bytes(file_path, data)

        entries.append({
            "document_type": document_type_override or _document_type_for_asset(asset),
            "media_path": str(file_path),
            "lac_pid": pid,
            "lac_asset_id": asset.asset_id,
            "source": "LAC",
        })

    return {
        "pid": pid,
        "lac_catalog_title": metadata.title,
        "reel_numbers": metadata.reel_numbers,
        "series_code": metadata.series_code,
        "source_documents": entries,
    }


# ==========================================
# COLLECTION CLASSIFICATION
# ==========================================
COLLECTIONS = [
    ("RG15-D-II-8-a", "Affidavits, 1870-1885", "Finding Aid 15-19", 1319, 1324),
    ("RG15-D-II-8-b", "Applications, 1885", "Finding Aid 15-20", 1325, 1330),
    ("RG15-D-II-8-c", "Applications, 1886-1906", "Finding Aid 15-21", 1331, 1372),
]


def collection_for_series_code(code: Optional[str]) -> Optional[Tuple[str, str, str, str]]:
    if not code:
        return None
    for series_code, title, finding_aid, _lo, _hi in COLLECTIONS:
        if code.startswith(series_code):
            return series_code, title, finding_aid, "confirmed"
    return None


def collection_for_volume(volume: Any, volume_range: Any) -> Optional[Tuple[str, str, str, str]]:
    def in_range(v):
        try:
            v_int = int(v)
            for series_code, title, finding_aid, lo, hi in COLLECTIONS:
                if lo <= v_int <= hi:
                    return series_code, title, finding_aid, "inferred"
        except (ValueError, TypeError):
            pass
        return None

    if volume and str(volume).isdigit():
        res = in_range(volume)
        if res:
            return res
    if volume_range:
        parts = str(volume_range).split("-")
        if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
            lo_res = in_range(parts[0].strip())
            hi_res = in_range(parts[1].strip())
            if lo_res and hi_res and lo_res[0] == hi_res[0]:
                return lo_res
    return None


def load_checkpoint(checkpoint_path: str) -> Dict[str, Any]:
    default = {"pids": [], "downloaded_pids": [], "failed_pids": {}}
    return jsonio_load_checkpoint(checkpoint_path, default=default)


def save_checkpoint(checkpoint_path: str, data: Dict[str, Any]) -> None:
    jsonio_save_checkpoint(checkpoint_path, data, indent=2, ensure_ascii=False)


def retrieve_volume_pids(vol: str, cookies: Dict[str, str], checkpoint_path: str,
                         archival_number: str = DEFAULT_ARCHIVAL_NUMBER) -> List[str]:
    """Discovers all PIDs in an archival volume using LAC search endpoint."""
    checkpoint = load_checkpoint(checkpoint_path)
    if checkpoint.get("pids"):
        return checkpoint["pids"]

    pids = lac_client.search_volume(vol, cookies, archival_number=archival_number)
    checkpoint["pids"] = pids
    checkpoint["volume"] = vol
    save_checkpoint(checkpoint_path, checkpoint)
    return pids


def download_volume_assets(pids: List[str], media_dir: str, checkpoint_path: str,
                           master_db_path: str, document_type: str, collection_title: str) -> Dict[str, Any]:
    """Sequential bulk download for a list of PIDs with checkpointing. Also seeds
    Paleographer's own MASTER_DB with one Commissioner-shaped scaffold sheet per
    downloaded asset, incrementally - see the Voyageur-Parish-Scrip-scaffold design spec.
    Each PID's source_documents are persisted into the checkpoint so a MASTER_DB
    reset can re-seed scaffolds for already-downloaded PIDs without re-fetching."""
    from Commissioner.record_registry import build_empty_sheet, validate_collection_softly

    checkpoint = load_checkpoint(checkpoint_path)
    downloaded = set(checkpoint.get("downloaded_pids", []))
    failed = checkpoint.get("failed_pids", {})
    pid_documents = checkpoint.get("pid_documents", {})
    master_data = load_master_db(master_db_path, collection_title, document_type)

    def write_scaffold(src_docs):
        new_sheets = [
            build_empty_sheet(Path(entry["media_path"]).name,
                              Path(entry["media_path"]).suffix.lstrip("."),
                              page_id=entry.get("lac_asset_id"))
            for entry in src_docs
        ]
        append_scaffold_sheets(master_data, new_sheets)
        validate_collection_softly(master_data, document_type, collection_title)
        save_master_db(master_db_path, master_data)

    for pid in pids:
        if pid in downloaded:
            write_scaffold(pid_documents.get(pid, []))
            continue
        try:
            bundle = download_pid_bundle(pid, media_dir)
            downloaded.add(pid)
            failed.pop(pid, None)
            source_documents = bundle.get("source_documents", [])
            pid_documents[pid] = source_documents
            write_scaffold(source_documents)
        except lac_client.LacCallError as e:
            failed[pid] = str(e)

        checkpoint["downloaded_pids"] = sorted(downloaded)
        checkpoint["failed_pids"] = failed
        checkpoint["pid_documents"] = pid_documents
        save_checkpoint(checkpoint_path, checkpoint)

    return checkpoint


def _worker_download_loop(worker_id: int, task_queue: mp.Queue, result_queue: mp.Queue,
                          rate_lock: Any, last_req_time: Any, current_delay: Any,
                          media_dir: str) -> None:
    """Worker process for concurrent asset downloads with rate limiting."""
    while True:
        try:
            pid = task_queue.get_nowait()
        except queue.Empty:
            break

        result_queue.put(("START", worker_id, pid, time.time()))
        try:
            with rate_lock:
                now = time.time()
                elapsed = now - last_req_time.value
                delay_val = current_delay.value
                if elapsed < delay_val:
                    time.sleep(delay_val - elapsed)
                last_req_time.value = time.time()

            bundle = download_pid_bundle(pid, media_dir)
            result_queue.put(("SUCCESS", worker_id, pid, bundle))
        except Exception as e:
            err_str = str(e)
            if "403" in err_str:
                result_queue.put(("403_ERROR", worker_id, pid, err_str))
            else:
                result_queue.put(("FAIL", worker_id, pid, err_str))


def _process_worker_messages(messages: List[tuple], active_workers: Dict[int, Dict[str, Any]],
                             downloaded: set, failed: Dict[str, Any], pid_documents: Dict[str, Any],
                             task_queue: Any, rate_lock: Any, current_delay: Any,
                             append_scaffold: Any) -> Tuple[int, bool]:
    """Applies one already-drained batch of result_queue messages to controller state, in
    order. Kept pure (no queue draining, no time.time() calls, no disk writes) so the
    property that matters for watchdog correctness - active_workers ends up reflecting the
    LATEST message for a worker, never an intermediate one - is directly testable without
    real or fake worker processes. This is the actual fix for the race #18 found: the old
    code processed one message per loop iteration with a watchdog check before every fetch,
    so if a worker sent SUCCESS(A) then immediately grabbed and started B, a controller that
    fell behind draining (e.g. slow disk I/O) could still see worker.pid == A with A's OLD
    start_time when the watchdog ran - timing the worker out over a task (A) it had already
    finished, while B (what it was ACTUALLY doing) was silently killed with no requeue and
    no failure record. Processing a full drained batch here, in order, before the caller's
    watchdog check ever runs, means active_workers can never be staler than "the last
    message this batch contained" - which is as fresh as draining can make it.
    Returns (processed_delta, has_unflushed_changes)."""
    processed_delta = 0
    has_unflushed_changes = False
    for msg in messages:
        msg_type, wid, pid = msg[0], msg[1], msg[2]
        if msg_type == "START":
            active_workers[wid]["pid"] = pid
            active_workers[wid]["start_time"] = msg[3]
        elif msg_type == "SUCCESS":
            active_workers[wid]["pid"] = None
            downloaded.add(pid)
            failed.pop(pid, None)
            processed_delta += 1
            bundle = msg[3]
            source_documents = bundle.get("source_documents", [])
            pid_documents[pid] = source_documents
            append_scaffold(source_documents)
            has_unflushed_changes = True
            print(f"\rDownloaded PID {pid}", end="", flush=True)
        elif msg_type == "403_ERROR":
            active_workers[wid]["pid"] = None
            with rate_lock:
                current_delay.value = min(current_delay.value * 2.0, 5.0)
            task_queue.put(pid)
        elif msg_type == "FAIL":
            active_workers[wid]["pid"] = None
            failed[pid] = msg[3]
            processed_delta += 1
            has_unflushed_changes = True
    return processed_delta, has_unflushed_changes


def download_volume_assets_multiworker(pids: List[str], media_dir: str, checkpoint_path: str,
                                       master_db_path: str, document_type: str, collection_title: str,
                                       max_workers: int = 4, base_delay: float = 0.3,
                                       timeout_seconds: int = 45) -> Dict[str, Any]:
    """Concurrent multi-worker PID downloading with watchdog timeout. Scaffold-sheet writes
    happen only in this controller loop (never inside a worker subprocess) after a SUCCESS
    message, mirroring the existing single-writer checkpoint pattern - see the
    Voyageur-Parish-Scrip-scaffold design spec. Disk writes (save_master_db/save_checkpoint)
    are batched rather than done on every single success: save_master_db re-serializes the
    ENTIRE master_data dict every call, so writing once per PID makes each write - and thus
    each pass through the drain loop below - progressively more expensive as a long harvest
    accumulates sheets, which is exactly the condition that let the watchdog race happen in
    the first place. Batching keeps the drain loop cheap regardless of harvest size, and a
    guaranteed final flush before returning means nothing is lost."""
    from Commissioner.record_registry import build_empty_sheet, validate_collection_softly

    checkpoint = load_checkpoint(checkpoint_path)
    downloaded = set(checkpoint.get("downloaded_pids", []))
    failed = checkpoint.get("failed_pids", {})
    master_data = load_master_db(master_db_path, collection_title, document_type)

    def append_scaffold_in_memory(source_documents):
        new_sheets = [
            build_empty_sheet(Path(entry["media_path"]).name,
                              Path(entry["media_path"]).suffix.lstrip("."),
                              page_id=entry.get("lac_asset_id"))
            for entry in source_documents
        ]
        append_scaffold_sheets(master_data, new_sheets)
        validate_collection_softly(master_data, document_type, collection_title)

    pid_documents = checkpoint.get("pid_documents", {})
    reseeded_any = False
    for pid in pids:
        if pid in downloaded:
            append_scaffold_in_memory(pid_documents.get(pid, []))
            reseeded_any = True
    if reseeded_any:
        save_master_db(master_db_path, master_data)

    pids_to_process = [p for p in pids if p not in downloaded]
    if not pids_to_process:
        return checkpoint

    print(f"[LAC Multi-Worker] Processing {len(pids_to_process)} PIDs with {max_workers} workers...")

    manager = mp.Manager()
    rate_lock = manager.Lock()
    last_req_time = manager.Value('d', 0.0)
    current_delay = manager.Value('d', base_delay)

    task_queue = manager.Queue()
    result_queue = manager.Queue()

    for pid in pids_to_process:
        task_queue.put(pid)

    active_workers = {i: {"process": None, "pid": None, "start_time": 0} for i in range(max_workers)}

    def start_worker(worker_id):
        p = mp.Process(target=_worker_download_loop,
                       args=(worker_id, task_queue, result_queue, rate_lock, last_req_time, current_delay, media_dir))
        p.start()
        active_workers[worker_id]["process"] = p
        active_workers[worker_id]["pid"] = None

    for wid in range(max_workers):
        start_worker(wid)

    processed_count = 0
    total_target = len(pids_to_process)

    FLUSH_EVERY_N = 10
    FLUSH_INTERVAL_SECONDS = 5.0
    has_unflushed_changes = False
    unflushed_since_last = 0
    last_flush_time = time.time()

    def flush_pending():
        nonlocal has_unflushed_changes, unflushed_since_last, last_flush_time
        checkpoint["downloaded_pids"] = sorted(downloaded)
        checkpoint["failed_pids"] = failed
        checkpoint["pid_documents"] = pid_documents
        save_checkpoint(checkpoint_path, checkpoint)
        save_master_db(master_db_path, master_data)
        has_unflushed_changes = False
        unflushed_since_last = 0
        last_flush_time = time.time()

    try:
        while processed_count < total_target:
            # Block briefly for at least one message (avoids busy-spinning when the queue
            # is empty), then drain everything else already available before the watchdog
            # check below runs - see _process_worker_messages' docstring for why this
            # ordering is what actually closes the race.
            try:
                first_msg = result_queue.get(timeout=0.2)
            except queue.Empty:
                first_msg = None

            if first_msg is not None:
                messages = [first_msg]
                while True:
                    try:
                        messages.append(result_queue.get_nowait())
                    except queue.Empty:
                        break

                delta, changed = _process_worker_messages(
                    messages, active_workers, downloaded, failed, pid_documents,
                    task_queue, rate_lock, current_delay, append_scaffold_in_memory)
                processed_count += delta
                if changed:
                    has_unflushed_changes = True
                    unflushed_since_last += delta

                if has_unflushed_changes and (
                    unflushed_since_last >= FLUSH_EVERY_N
                    or time.time() - last_flush_time >= FLUSH_INTERVAL_SECONDS
                    or processed_count >= total_target
                ):
                    flush_pending()
                if delta:
                    print(f" [{processed_count}/{total_target}]", end="", flush=True)

            now = time.time()
            for wid, worker in active_workers.items():
                hung_pid = worker["pid"]
                if hung_pid and (now - worker["start_time"] > timeout_seconds):
                    print(f"\n[Watchdog] Worker {wid} hung on PID {hung_pid}. Restarting worker...")
                    if worker["process"] is not None:
                        worker["process"].terminate()
                        worker["process"].join()
                    task_queue.put(hung_pid)
                    start_worker(wid)
    finally:
        # Unconditional, not gated on has_unflushed_changes: if the loop above was
        # interrupted by an exception or Ctrl-C partway through a batch,
        # _process_worker_messages may have already mutated downloaded/master_data
        # in-place for some messages before raising, without ever reaching the line that
        # sets has_unflushed_changes - flushing unconditionally here is the only way to
        # guarantee whatever progress happened before an abnormal exit is never lost.
        # flush_pending() is idempotent, so a redundant call on normal completion costs
        # nothing but a rewrite of already-correct data.
        flush_pending()

        # task_queue should be empty by now on a normal exit, so every worker should
        # already be exiting on its own - but give any still-alive worker a moment to
        # finish cleanly, then force it, rather than leaving an orphaned subprocess
        # running after this function returns or raises.
        for worker in active_workers.values():
            process = worker["process"]
            if process is not None and process.is_alive():
                process.join(timeout=5.0)
                if process.is_alive():
                    process.terminate()
                    process.join()

    print("\n[LAC Multi-Worker] Download run completed.")
    return checkpoint


def retrieve_volume(vol: str, cookies: Dict[str, str], media_dir: str, checkpoint_path: str,
                    master_db_path: str, document_type: str, collection_title: str,
                    archival_number: str = DEFAULT_ARCHIVAL_NUMBER, max_workers: int = 1) -> Dict[str, Any]:
    """High-level volume retrieval: gathers PIDs and downloads all associated assets."""
    pids = retrieve_volume_pids(vol, cookies, checkpoint_path, archival_number=archival_number)
    if max_workers > 1:
        return download_volume_assets_multiworker(pids, media_dir, checkpoint_path, master_db_path,
                                                  document_type, collection_title, max_workers=max_workers)
    return download_volume_assets(pids, media_dir, checkpoint_path, master_db_path, document_type, collection_title)


# ==========================================
# MAIN EXECUTION
# ==========================================
def _run_volume(args: argparse.Namespace) -> None:
    print(f"[System] Starting LAC Volume retrieval for Volume {args.volume}...")
    document_type = _resolve_record_type(args.record_type)
    master_db_path = resolve_master_db_path(document_type, PROGRAM_DIR)
    collection_title = f"LAC Volume {args.volume}"

    try:
        cookies = load_cookies(args.cookie_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"[FATAL ERROR] {e} Search LAC once in a real browser, then paste its Cookie header "
              f"into that file. Opening search browser...")
        lac_client.open_search_browser_for_refresh()
        return

    checkpoint_path = str(Path(CHECKPOINT_DIR) / f"volume_{args.volume}.json")
    try:
        result = retrieve_volume(args.volume, cookies, args.media_dir, checkpoint_path,
                                 master_db_path, document_type, collection_title,
                                 archival_number=args.archival_number, max_workers=args.workers)
    except lac_client.LacSearchAuthError as e:
        print(f"[FATAL ERROR] {e} Opening the search page now.")
        lac_client.open_search_browser_for_refresh()
        return

    print(f"[System] Harvested volume {args.volume}: {len(result.get('pids', []))} PID(s), "
          f"{len(result.get('downloaded_pids', []))} downloaded, "
          f"{len(result.get('failed_pids', {}))} failed.")


def _run_reel(args: argparse.Namespace) -> None:
    if not args.url:
        print("[Error] --url is required for the reel subcommand.")
        sys.exit(1)

    document_type = _resolve_record_type(args.record_type)
    program_dir = os.environ.get("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent)).strip()
    master_db_path = resolve_master_db_path(document_type, PROGRAM_DIR)

    roll, manifest = parse_url(args.url)
    collection_title = f"LAC Reel {roll}"
    output_directory = setup_directories(program_dir, args.media_dir, roll)
    manifest_json = download_manifest(manifest)
    download_images(manifest_json, output_directory, roll, master_db_path, document_type, collection_title)


def main() -> None:
    parser = argparse.ArgumentParser(description="Voyageur LAC Gatherer: Canadiana IIIF and LAC Volume Harvester")
    subparsers = parser.add_subparsers(dest="command", required=True)

    volume_parser = subparsers.add_parser("volume", help="Harvest an LAC archival volume by number.")
    volume_parser.add_argument("--volume", default="",
                               help="LAC Volume number to harvest (e.g., 1325).")
    volume_parser.add_argument("--archival-number", default=DEFAULT_ARCHIVAL_NUMBER,
                               help="Archival series number (default: RG15).")
    volume_parser.add_argument("--cookie-file", default=COOKIE_FILE,
                               help="Path to browser cookies file for LAC search.")
    volume_parser.add_argument("--media-dir", default=MEDIA_DIR,
                               help="Base output media directory.")
    volume_parser.add_argument("--workers", type=int, default=8,
                               help="Number of concurrent workers for volume downloading (default 1).")
    volume_parser.add_argument("--record-type", default="",
                               help="Commissioner record type this volume harvest is for: parish or scrip.")
    volume_parser.set_defaults(func=_run_volume)

    reel_parser = subparsers.add_parser("reel", help="Download a Canadiana IIIF reel by URL.")
    reel_parser.add_argument(
        "--url", default=os.environ.get("GATHER_URL", ""),
        help="Canadiana IIIF URL (e.g., https://heritage.canadiana.ca/view/oocihm.lac_reel_c2170).")
    reel_parser.add_argument("--media-dir", default=MEDIA_DIR,
                             help="Base output media directory.")
    reel_parser.add_argument("--record-type", default="",
                             help="Commissioner record type this reel harvest is for: parish or scrip.")
    reel_parser.set_defaults(func=_run_reel)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
