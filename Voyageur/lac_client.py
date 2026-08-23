"""
Generic HTTP client for Library and Archives Canada's Collection Search
(recherche-collection-search.bac-lac.gc.ca) - no genealogy domain knowledge, just the
four LAC endpoints needed for harvesting and downloading:

- get_record_metadata: the catalog record page for a known Item ID (PID).
- get_manifest: the IIIF Presentation API v3 manifest listing an item's digital objects.
- download_asset: the actual file bytes for one digital object (image or PDF).
- search: find item(s) by claim/scrip number when the PID isn't already known.

The site is Cloudflare-protected. record/manifest/download all pass through cleanly with
a plain `cloudscraper` session (confirmed live - no special cookies needed). `search`
specifically sits behind a *second*, stronger layer requiring a real `cf_clearance`
cookie that only a real browser solving an interactive challenge can produce - confirmed
live that cloudscraper's challenge solvers (both its default and Node.js interpreters)
cannot get past it on their own, but replaying a real browser's full cookie jar +
matching headers does work. There is no way around this: `search` always needs a
cookie jar sourced from an actual browser session (see parse_cookie_header), refreshed
periodically by the user, not something this module can obtain on its own.
"""

import json
import re
import webbrowser
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import quote

import cloudscraper
import requests
from bs4 import BeautifulSoup

RECORD_HOST = "https://recherche-collection-search.bac-lac.gc.ca"
MANIFEST_HOST = "https://digitalmanifest.bac-lac.gc.ca"
ASSET_HOST = "https://central.bac-lac.gc.ca"

# Heritage Canadiana (heritage.canadiana.ca) independently mirrors LAC's microfilm reels
# via a CRKN digitization partnership. Confirmed live: the reel view page and its IIIF
# Image API v2 backend carry NO Cloudflare challenge at all (plain requests, no
# cloudscraper needed) - a genuine second bulk-retrieval path that never touches LAC's
# protected search endpoint. There is no JSON presentation manifest (confirmed live: the
# obvious /manifest and /presentation/.../manifest URL shapes both 404/fail) - a reel's
# full ordered page list has to be scraped from the view page's embedded IIIF image
# references instead (confirmed live: a single GET of the view page returns all 720 of a
# real reel's page images inline, no pagination needed).
CANADIANA_VIEW_HOST = "https://heritage.canadiana.ca"
CANADIANA_IMAGE_HOST = "https://image-uab.canadiana.ca"

# Changed to a tuple: (connect_timeout, read_timeout) to prevent hanging connections
DEFAULT_TIMEOUT_SECONDS = (5, 15)
TimeoutType = Union[int, float, Tuple[int, int], Tuple[float, float]]

# Confirmed live: manifest URLs are shaped {MANIFEST_HOST}/DigitalManifest/{source_code}/{PID}.
# source_code=1 is the "fonandcol" reference system - confirmed live against a real item.
# Not yet confirmed whether other reference systems (if LAC ever exposes them) use a
# different source_code; fonandcol is the only one needed so far.
FONANDCOL_SOURCE_CODE = 1

# Between-request pacing - this is a government site, not a target to hammer. Applied by
# callers between records, not enforced inside this module itself,
# since a single lookup here is already just one or two requests.
POLITE_DELAY_SECONDS = 1.0


class LacCallError(Exception):
    """Base error for any failed LAC call - a non-2xx response, a malformed body, or a
    network failure. Callers' retry logic catches this specifically."""


class LacSearchAuthError(LacCallError):
    """The search endpoint rejected the request because the supplied cookie jar is
    missing, invalid, or expired (confirmed live: an expired/missing cf_clearance
    returns a page titled "Forbidden: Request denied" or a Cloudflare "Just a moment..."
    challenge, not a clean error status). Distinct from LacCallError so callers can
    surface a specific "refresh your cookies" message instead of a generic failure."""


@dataclass
class RecordMetadata:
    pid: str
    title: str
    digital_object_count: Optional[int]
    reel_numbers: List[str]  # e.g. ["C-14932"] - LAC's own microfilm reel identifier(s),
    # directly usable with Heritage Canadiana's lac_reel_c{number} convention. A register/
    # index item can span many reels (confirmed live); an individual claim record has
    # exactly one, repeated per physical copy - deduped here either way.
    series_code: Optional[str]  # e.g. "RG15-D-II-8-a" - the exact RG sub-series, identifying
    # which of the several historical scrip commissions this record belongs to. A complex
    # item can carry more than one code concatenated with no separator (confirmed live,
    # e.g. "RG15-D-II-8-a-iRG15-D-II-8-a-ii") - stored as the raw text; callers needing the
    # individual codes split out should regex on their own known-code prefixes.


@dataclass
class DigitalObject:
    asset_id: str
    label: str
    media_format: str  # e.g. "image/jpeg", "application/pdf"

    @property
    def op(self) -> str:
        """The `op` query value download_asset needs - "pdf" for a PDF asset, "img" for
        anything else (confirmed live: image assets use op=img, the combined-PDF asset
        uses op=pdf)."""
        return "pdf" if "pdf" in self.media_format.lower() else "img"


def _get_scraper() -> "cloudscraper.CloudScraper":
    """A fresh cloudscraper session per call is deliberate, not an oversight - these are
    infrequent, one-off lookups (not a tight loop needing connection reuse), and a fresh
    session avoids any risk of stale challenge-solve state leaking between unrelated
    calls. `search` is the one function that takes an explicit, separately-sourced
    cookie jar instead, since cloudscraper's own challenge-solving can't reach that
    endpoint at all (see module docstring)."""
    return cloudscraper.create_scraper()


# ==========================================
# RECORD METADATA
# ==========================================
def get_record_metadata(pid: str, timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS
                        ) -> RecordMetadata:
    """Fetches the catalog record page for a known PID (Item ID) and pulls its title -
    confirmed live to carry a rich descriptive summary (names, birth year, parents,
    claim #, scrip #, date of issue, amount, digital-object count) that can be more
    accurate than an AI-OCR'd reading of the source document itself (confirmed live:
    LAC's own catalog had the correct birth year and scrip number where extraction from
    the scanned document had misread both). Callers should treat this as a cross-check
    source, not blindly overwrite extracted data with it.

    Raises LacCallError on a non-200 response or if no <title> is found."""
    url = f"{RECORD_HOST}/eng/Home/Record?app=fonandcol&IdNumber={pid}"
    scraper = _get_scraper()
    try:
        resp = scraper.get(url, timeout=timeout_seconds)
    except Exception as e:
        raise LacCallError(f"Failed to fetch record metadata for PID {pid}: {e}") from e

    if resp.status_code != 200:
        raise LacCallError(f"Record page for PID {pid} returned status {resp.status_code}")

    soup = BeautifulSoup(resp.content, "lxml")
    title_tag = soup.title
    if not title_tag or not title_tag.get_text(strip=True):
        raise LacCallError(f"Record page for PID {pid} had no <title> - unexpected response shape")
    title = title_tag.get_text(strip=True)

    count_match = re.search(r"\((\d+)\s+digital object", title, re.IGNORECASE)
    digital_object_count = int(count_match.group(1)) if count_match else None

    # Confirmed live: these two fields' element IDs embed the PID itself
    # (...containernotefonandcol{pid}, ...recordcontrolnumbercode{N}textfonandcol{pid}),
    # and the control-number field's numeric code segment (151 in one real example) isn't
    # fixed either - matched by prefix/regex, never a hardcoded full ID string.
    container_el = soup.find(id=re.compile(r"^jq-container-body-recordmediaphysicalmanifestationcontainernote"))
    reel_numbers = sorted(set(re.findall(r"[A-Z]-\d+", container_el.get_text()))) if container_el else []

    control_el = soup.find(id=re.compile(r"^jq-container-body-recordcontrolnumbercode\d+text"))
    series_code = control_el.get_text(strip=True) if control_el else None

    return RecordMetadata(pid=pid, title=title, digital_object_count=digital_object_count,
                          reel_numbers=reel_numbers, series_code=series_code)


# ==========================================
# MANIFEST (digital object list)
# ==========================================
def get_manifest(pid: str, source_code: int = FONANDCOL_SOURCE_CODE,
                 timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS) -> List[DigitalObject]:
    """Fetches the IIIF Presentation API v3 manifest for a known PID and returns its
    digital objects - confirmed live against a real 2-document item: one entry per page
    image plus one for the combined PDF, each with its own `e0XXXXXXX` asset ID
    (confirmed distinct from the PID - one item can have several assets)."""
    url = f"{MANIFEST_HOST}/DigitalManifest/{source_code}/{pid}"
    scraper = _get_scraper()
    try:
        resp = scraper.get(url, timeout=timeout_seconds)
    except Exception as e:
        raise LacCallError(f"Failed to fetch manifest for PID {pid}: {e}") from e

    if resp.status_code != 200:
        raise LacCallError(f"Manifest for PID {pid} returned status {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as e:
        raise LacCallError(f"Manifest for PID {pid} was not valid JSON: {e}") from e

    objects: List[DigitalObject] = []
    for canvas in data.get("items", []):
        label_parts = (canvas.get("label") or {}).get("en") or []
        label = label_parts[0] if label_parts else ""
        for annotation_page in canvas.get("items", []):
            for annotation in annotation_page.get("items", []):
                body = annotation.get("body") or {}
                asset_url = body.get("id") or ""
                media_format = body.get("format") or ""
                asset_match = re.search(r"[?&]id=(e\d+)", asset_url)
                if asset_match:
                    objects.append(DigitalObject(asset_id=asset_match.group(1), label=label,
                                                 media_format=media_format))
    return objects


# ==========================================
# ASSET DOWNLOAD
# ==========================================
def download_asset(asset_id: str, op: str, timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """Downloads one digital object's actual file bytes. `op` is "pdf" or "img" - see
    DigitalObject.op for how to derive it from a manifest entry's media_format."""
    url = f"{ASSET_HOST}/.item/?id={asset_id}&app=fonandcol&op={op}"
    scraper = _get_scraper()
    try:
        resp = scraper.get(url, timeout=timeout_seconds)
    except Exception as e:
        raise LacCallError(f"Failed to download asset {asset_id}: {e}") from e

    if resp.status_code != 200:
        raise LacCallError(f"Asset {asset_id} download returned status {resp.status_code}")
    if not resp.content:
        raise LacCallError(f"Asset {asset_id} download returned an empty body")

    return resp.content


# ==========================================
# HERITAGE CANADIANA (reel mirror, no Cloudflare gate)
# ==========================================
_CANADIANA_IMAGE_REF_RE = re.compile(r"iiif/2/([^/\"]+)/info\.json")


def get_canadiana_reel_pages(reel_id: str, timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS
                             ) -> List[str]:
    """Fetches a Canadiana reel's view page (reel_id like "lac_reel_c14950" - drop the
    "oocihm." prefix, added here) and returns the ordered list of IIIF image identifiers
    for every page on that reel."""
    url = f"{CANADIANA_VIEW_HOST}/view/oocihm.{reel_id}"
    try:
        resp = requests.get(url, timeout=timeout_seconds)
    except Exception as e:
        raise LacCallError(f"Failed to fetch Canadiana reel {reel_id}: {e}") from e

    if resp.status_code != 200:
        raise LacCallError(f"Canadiana reel {reel_id} returned status {resp.status_code}")

    image_ids = _CANADIANA_IMAGE_REF_RE.findall(resp.text)
    if not image_ids:
        raise LacCallError(f"Canadiana reel {reel_id} page had no recognizable image references")

    return list(dict.fromkeys(image_ids))


def download_canadiana_page(image_id: str, size: str = "full",
                            timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """Downloads one page's full-resolution image bytes via Canadiana's IIIF Image API
    v2 backend."""
    url = f"{CANADIANA_IMAGE_HOST}/iiif/2/{image_id}/{size}/full/0/default.jpg"
    try:
        resp = requests.get(url, timeout=timeout_seconds)
    except Exception as e:
        raise LacCallError(f"Failed to download Canadiana page {image_id}: {e}") from e

    if resp.status_code != 200:
        raise LacCallError(f"Canadiana page {image_id} download returned status {resp.status_code}")
    if not resp.content:
        raise LacCallError(f"Canadiana page {image_id} download returned an empty body")

    return resp.content


# ==========================================
# SEARCH (requires a real browser's cookie jar)
# ==========================================
def parse_cookie_header(raw_cookie_header: str) -> Dict[str, str]:
    """Parses a raw `Cookie:` header string (e.g. pasted from DevTools > Network > Copy
    as cURL) into a dictionary requests/cloudscraper can use. Strips leading 'Cookie: '
    if present, tolerates trailing semicolons and whitespace around names/values."""
    cookies: Dict[str, str] = {}
    cleaned = re.sub(r"^cookie:\s*", "", raw_cookie_header.strip(), flags=re.IGNORECASE)
    for part in cleaned.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


_SEARCH_HEADERS = {
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
               "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "upgrade-insecure-requests": "1",
}


def _do_search_request(url: str, cookies: Dict[str, str], timeout_seconds: TimeoutType,
                       description: str) -> List[str]:
    """Shared plumbing for search() and search_volume() - runs the request with
    _SEARCH_HEADERS and the supplied cookie jar, checks for Cloudflare challenge/auth
    rejection, and extracts all IdNumber (PID) values from the results page.

    Raises LacSearchAuthError on any auth failure, LacCallError on network/HTTP errors."""
    try:
        resp = requests.get(url, headers=_SEARCH_HEADERS, cookies=cookies, timeout=timeout_seconds)
    except Exception as e:
        raise LacCallError(f"Search request failed for {description}: {e}") from e

    soup = BeautifulSoup(resp.content, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""

    if resp.status_code != 200 or "forbidden" in title.lower() or "just a moment" in title.lower():
        raise LacSearchAuthError(
            f"Search for {description} was rejected (status {resp.status_code}, title "
            f"{title!r}) - the supplied cookie jar is likely missing or expired."
        )

    return sorted(set(re.findall(r"IdNumber=(\d+)", resp.text)))


def search(query: str, cookies: Dict[str, str],
           timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS) -> List[str]:
    """Searches LAC Collection Search by free-text query (e.g. a claim number, affidavit
    number, scrip number, or e-number) and returns the list of matching Item IDs (PIDs).

    Requires a valid browser cookie jar with an unexpired cf_clearance cookie - see
    module docstring. Raises LacSearchAuthError on Cloudflare rejection, LacCallError
    on network or response errors."""
    url = f"{RECORD_HOST}/eng/Home/Result?ST=STAD&q_type_1=q&q_1={quote(query)}&"
    return _do_search_request(url, cookies, timeout_seconds, description=repr(query))


DEFAULT_CDP_PORT = 9222


def load_cookies_from_cdp(port: int = DEFAULT_CDP_PORT, domain_url: str = f"{RECORD_HOST}/",
                          timeout_seconds: TimeoutType = (5, 10)) -> Dict[str, str]:
    """Reads live session cookies straight out of a Chrome/Edge instance running with
    --remote-debugging-port={port} via Chrome DevTools Protocol (Network.getCookies).

    The user launches Chrome/Edge once with that flag, navigates to LAC, and solves a
    search there; this function fetches the resulting cookie jar over localhost
    websocket without requiring any manual DevTools copy-pasting.

    Raises LacCallError if no browser is reachable on that port or if the websocket
    connection fails; raises LacSearchAuthError if the browser tab has no cookies for
    domain_url (i.e. the user hasn't visited LAC in that browser window yet)."""
    try:
        import websocket
    except ImportError as e:
        raise LacCallError(
            "The websocket-client package is required for CDP cookie reading "
            "(pip install websocket-client)."
        ) from e

    try:
        targets = requests.get(f"http://localhost:{port}/json", timeout=timeout_seconds).json()
    except Exception as e:
        raise LacCallError(
            f"Could not reach a debuggable browser on port {port}: {e}. Launch Chrome or "
            f"Edge with --remote-debugging-port={port} first, then search LAC in that window."
        ) from e

    target = next((t for t in targets if t.get("type") == "page"), None)
    if not target or not target.get("webSocketDebuggerUrl"):
        raise LacCallError(f"No open browser tab found on debug port {port}.")

    ws_timeout = timeout_seconds[1] if isinstance(timeout_seconds, tuple) else timeout_seconds
    ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=ws_timeout)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.getCookies", "params": {"urls": [domain_url]}}))
        response = json.loads(ws.recv())
    finally:
        ws.close()

    cookies = {c["name"]: c["value"] for c in response.get("result", {}).get("cookies", [])}
    if not cookies:
        raise LacSearchAuthError(
            f"No cookies found for {domain_url} on debug port {port} - search LAC in that "
            f"browser window first (or the session there has already expired)."
        )
    return cookies


def open_search_browser_for_refresh(query: str = "") -> None:
    """Opens the LAC search page in the user's real default browser so they can solve
    the Cloudflare challenge interactively and refresh their session. If a query is
    provided, opens directly to that search; otherwise opens Advanced Search."""
    url = f"{RECORD_HOST}/eng/Home/SearchAdvanced"
    if query:
        url = f"{RECORD_HOST}/eng/Home/Result?ST=STAD&q_type_1=q&q_1={quote(query)}&"
    webbrowser.open(url)


def search_volume(vol: str, cookies: Dict[str, str], archival_number: str = "RG15",
                  timeout_seconds: TimeoutType = DEFAULT_TIMEOUT_SECONDS) -> List[str]:
    """Harvests every PID filed under one volume/box number (and archival series prefix,
    e.g. RG15) via LAC's Advanced Search fields.

    Confirmed live: VolumeBoxNumber search on RG15 returns all matching items in one page
    (num=100) - a single search call retrieves the entire volume's PID list.

    Requires a valid browser cookie jar. Raises LacSearchAuthError on Cloudflare
    rejection, LacCallError on network or response errors."""
    query_string = (
        f"ST=STAD&DataSource=Archives|FonAndCol"
        f"&SearchIn_1=ArchivalNumber&SearchInText_1={quote(archival_number)}&Operator_1=AND"
        f"&SearchIn_2=VolumeBoxNumber&SearchInText_2={quote(str(vol))}&Operator_2=AND"
        f"&DataSourceSel=Archives&start=0&num=100&"
    )
    url = f"{RECORD_HOST}/eng/Home/Result?{query_string}"
    return _do_search_request(url, cookies, timeout_seconds, description=f"volume {vol}")
