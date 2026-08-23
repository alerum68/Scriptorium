"""
RootsMagic Historical County Fixer.

Updates a RootsMagic database by checking each event's geocoded location
against historical shapefiles (like the Newberry Atlas) and forking the
place record to reflect the correct historical county or territory as of
that date, while preserving the original FamilySearch/Ancestry tracking
IDs and coordinates.
"""

import calendar
import datetime
import os
import re
import shutil
import sqlite3
import sys
import warnings
from pathlib import Path
from typing import List, Optional

import geopandas as gpd
from shapely.geometry import Point
from tqdm import tqdm

# Commissioner lives in a sibling tool folder, not an installed package - add the repo
# root to sys.path so its shared env loader can be imported when this file is run
# directly (python Gazetteer/Gazetteer.py); a no-op inside the frozen app bundle.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from Commissioner.envkit import load_tool_env  # noqa: E402
from Commissioner.sqlite_helpers import register_rmnocase  # noqa: E402

# Global settings come from the project root's .env; this tool's own settings come from
# its own subfolder's .env, so Gazetteer stays runnable standalone. Tool .env overrides
# global .env (root first, tool second, both override=True) - see Commissioner/envkit.py.
load_tool_env(Path(__file__).resolve().parent)


# ==========================================
# CONFIGURATION
# ==========================================
PROGRAM_DIR = os.getenv("PROGRAM_DIR", str(Path(__file__).resolve().parent.parent))
GENEALOGY_DIR = os.getenv("GENEALOGY_DIR", "")

_rm_db = os.getenv("GAZETTEER_RM_DATABASE", "Roots Magic 11/Your Tree.rmtree")
RM_DATABASE = _rm_db if os.path.isabs(_rm_db) else os.path.join(GENEALOGY_DIR, _rm_db)

# These ship alongside the Gazetteer tool at fixed, known-good locations - not
# user-configurable settings, so they are plain constants rather than os.getenv() reads.
# PROGRAM_DIR already IS the app's own install root (see Antiquarian.py's _run_subprocess),
# so these paths sit directly under it - matching the .gitignore patterns for the same
# folders (/Gazetteer/US_AtlasHCB_Counties/, /Gazetteer/CA_UNICEN_Counties/*).
SHAPEFILE_PATH = os.path.join(
    PROGRAM_DIR,
    "Gazetteer/US_AtlasHCB_Counties/US_HistCounties_Shapefile/US_HistCounties.shp")

# Optional: if the folder isn't present, Gazetteer simply runs US-only, exactly as it did
# before this existed (one shapefile per census year - see that folder's own
# LICENSE_AND_ATTRIBUTION.txt).
CA_SHAPEFILE_DIR = os.path.join(PROGRAM_DIR, "Gazetteer/CA_UNICEN_Counties")

DEBUG_MODE = False
CREATE_BACKUP = True


# ==========================================
# GLOBALS & COMPILED REGEX
# ==========================================
# Global variables for tracking progress across functions without UI bounce
_progress_bar: Optional[tqdm] = None
_updated_count: int = 0

# RootsMagic specific date string formats
DATE_PATTERN_D = re.compile(r'^D.([+-])(\d{4})(\d{2})(\d{2})')
DATE_PATTERN_T_FULL = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
DATE_PATTERN_T_YEAR = re.compile(r'(\d{4})')

# A comprehensive set of states/territories to prevent them from being
# accidentally parsed as local cities.
# noinspection SpellCheckingInspection
US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
    "dakota territory", "minnesota territory", "illinois territory",
    "indiana territory", "michigan territory", "wisconsin territory",
    "iowa territory", "missouri territory", "northwest territory",
    "oregon territory", "washington territory", "utah territory",
    "new mexico territory", "nebraska territory", "kansas territory",
    "colorado territory", "nevada territory", "idaho territory",
    "arizona territory", "montana territory", "wyoming territory",
    "hawaii territory", "alaska territory", "indian territory",
    "united states", "united states of america", "usa", "u.s.a.", "us", "u.s.",
    "canada", "uk", "united kingdom", "england", "france", "germany", "ireland",
    "scotland", "mexico"
}

# The UNI-CEN Canadian boundaries are discrete per-census-year snapshots (no
# continuous START_DATE/END_DATE range exists for Canada - see
# CA_UNICEN_Counties/LICENSE_AND_ATTRIBUTION.txt for why), so an event's own date is
# matched to whichever of these years is chronologically closest, rather than an
# exact range lookup.
CA_CENSUS_YEARS = [1851, 1861, 1871, 1881, 1891, 1901, 1911, 1921]

# geosid's own first two letters are a province/territory code, with no separate
# name column - confirmed against real data that this is historically accurate as
# stored (e.g. 1861/1891 code today's Alberta/Saskatchewan under "NT", since neither
# became a province until 1905), so no extra era-aware remapping is needed here.
CA_PROVINCE_NAMES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
    "YT": "Yukon", "NU": "Nunavut",
}

# Newfoundland did not join Confederation until 1949 - every year CA_CENSUS_YEARS
# covers predates that, so an "NL" match's country is its own, never "Canada".
CA_COUNTRY_OVERRIDES = {"NL": "Newfoundland"}


# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def update_ui() -> None:
    """Update the fixed progress bar postfix without causing horizontal bounce."""
    if _progress_bar is not None:
        _progress_bar.set_postfix({'Fixed': _updated_count}, refresh=False)


def debug_print(message: str) -> None:
    """Print debug messages cleanly on a new line above the static progress bar."""
    if not DEBUG_MODE:
        return

    if _progress_bar is not None:
        _progress_bar.write(f"   -> {message}")
    else:
        print(f"   -> {message}")


def parse_rm_date(date_str: str) -> Optional[str]:
    """Parse RootsMagic proprietary date strings into standard YYYY-MM-DD format."""
    if not date_str or date_str == '.':
        return None

    year, month, day = 0, 1, 1

    # D-format: Typically D.[+/-][YYYY][MM][DD]
    if date_str.startswith('D'):
        m_match = DATE_PATTERN_D.match(date_str)
        if not m_match:
            return None
        sign, y_str, mo_str, d_str = m_match.groups()
        if sign == '-':  # Skip BC dates for historical county tracking
            return None
        year, month, day = int(y_str), int(mo_str), int(d_str)

    # T-format: Typically textual or ISO-like T[YYYY]-[MM]-[DD]
    elif date_str.startswith('T'):
        m_full = DATE_PATTERN_T_FULL.search(date_str)
        if m_full:
            year, month, day = (
                int(m_full.group(1)),
                int(m_full.group(2)),
                int(m_full.group(3))
            )
        else:
            m_year = DATE_PATTERN_T_YEAR.search(date_str)
            if not m_year:
                return None
            year = int(m_year.group(1))
    else:
        return None

    if year == 0:
        return None

    # Sanitize month and day to prevent calendar errors
    month = max(1, min(month, 12))
    day = max(1, day)

    # Clamp the day to the maximum allowed days for the specific month/year
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        day = last_day

    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_local_parts(place_name: str) -> str:
    """Extract the local city/town from a place name string."""
    if not place_name:
        return ""

    parts = [p.strip() for p in place_name.split(',')]

    # Short places (e.g., "Minnesota, USA" or "United States")
    if len(parts) <= 2:
        if parts[0].lower() in US_STATES:
            return ""
        return parts[0]

    # Standard RM Place (City, County, State, Country) or larger
    if len(parts) >= 4:
        # Strip the last 3 elements to isolate the local entity.
        return ", ".join(parts[:-3])

    # 3-part place (e.g., City, State, Country)
    if len(parts) == 3:
        return parts[0]

    return parts[0]


def clean_shapefile_name(name: str) -> str:
    """Clean historical names extracted from the shapefile dataset."""
    if not name:
        return ""

    name = name.title()

    replacements = {
        "Terr.": "Territory",
        "Unorg.": "Unorganized",
        "Fed.": "Federal",
        "Bdry.": "Boundary",
        "Nca": "NCA",
        "Dist.": "District",
        " De ": " de "
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    return name.strip()


def create_reverse_place(place_name: str) -> str:
    """Reverse the comma-separated parts of a place name."""
    if not place_name:
        return ""
    parts = [part.strip() for part in place_name.split(',')]
    parts.reverse()
    return ", ".join(parts)


def clean_canadian_name(name: str) -> str:
    """Cleans a UNI-CEN geoname value. The source dbf uses a literal "?" where a
    bilingual English/French name should show a "/" separator (e.g. confirmed real
    data: "Brant, South?Sud") - a data-quality artifact in the source file itself,
    not something introduced by reading it here."""
    if not name:
        return ""
    return name.replace("?", "/").strip()


def load_canadian_shapefiles() -> dict:
    """Loads each UNI-CEN Census Division snapshot year into its own GeoDataFrame,
    keyed by census year. Missing entirely (folder not downloaded) is not an error -
    Gazetteer just runs US-only, as it always has."""
    shapefiles = {}
    if not os.path.isdir(CA_SHAPEFILE_DIR):
        return shapefiles
    for year in CA_CENSUS_YEARS:
        path = os.path.join(CA_SHAPEFILE_DIR, f"cd_{year}.shp")
        if not os.path.exists(path):
            continue
        try:
            shapefiles[year] = gpd.read_file(path, encoding="cp1252").to_crs("EPSG:4326")
        except Exception as e:
            print(f"[WARN] Failed to load Canadian {year} boundaries: {e}")
    return shapefiles


def nearest_canadian_census_year(target_date: str) -> Optional[int]:
    """Finds the loaded UNI-CEN census year closest to an event's own date - the
    best available precision, since these are decade snapshots with no continuous
    change log to interpolate from (see CA_UNICEN_Counties/LICENSE_AND_ATTRIBUTION.txt)."""
    try:
        target_year = int(str(target_date)[:4])
    except (TypeError, ValueError):
        return None
    return min(CA_CENSUS_YEARS, key=lambda y: abs(y - target_year))


def build_us_place_name(current_name: str, matched_row) -> str:
    """Builds a standardized US place name from a matched Newberry Atlas polygon -
    the exact naming logic already in use, extracted unchanged so a Canadian match
    can share the same call site in main()'s event loop."""
    local_city = extract_local_parts(current_name)
    county_val = clean_shapefile_name(matched_row['NAME'])
    state_val = clean_shapefile_name(matched_row['STATE_TERR'])

    lower_county = county_val.lower()
    pseudo_keywords = [
        'territory', 'unorganized', 'nca', 'de facto', 'new pur',
        'boundary', 'ext', 'dist', 'district', 'tract', 'reserve'
    ]

    if lower_county == state_val.lower():
        final_county = ""
    elif any(kw in lower_county for kw in pseudo_keywords):
        final_county = county_val
    else:
        # noinspection SpellCheckingInspection
        if state_val.lower() == "louisiana":
            final_county = f"{county_val} Parish"
        else:
            final_county = f"{county_val} County"

    components = []
    if local_city:
        components.append(local_city)
    if final_county:
        components.append(final_county)
    if state_val:
        components.append(state_val)
    components.append("USA")

    return ", ".join(components)


def build_ca_place_name(current_name: str, matched_row) -> str:
    """Builds a standardized Canadian place name from a matched UNI-CEN Census
    Division polygon. Unlike the US side, geoname is used as-is with no synthetic
    "County" suffix - Canadian CD names don't follow one uniform convention (some
    provinces do use "County" in the name itself, most don't), so inventing one
    would misrepresent real names like "Yale & Cariboo" or "Comox-Atlin"."""
    local_city = extract_local_parts(current_name)
    cd_val = clean_canadian_name(matched_row['geoname'])
    province_code = str(matched_row['geosid'])[:2]
    province_val = CA_PROVINCE_NAMES.get(province_code, province_code)
    country_val = CA_COUNTRY_OVERRIDES.get(province_code, "Canada")

    components = []
    if local_city:
        components.append(local_city)
    if cd_val and cd_val.lower() != province_val.lower():
        components.append(cd_val)
    if province_val:
        components.append(province_val)
    components.append(country_val)

    return ", ".join(components)


# ==========================================
# DATABASE OPERATIONS
# ==========================================
def _clone_place_row(
    cursor: sqlite3.Cursor,
    original_place_id: int,
    columns: List[str],
    overrides: dict
) -> int:
    """Insert a new PlaceTable row cloned from original_place_id, with the given
    column values overridden (all other columns, like coordinates and UUIDs,
    carried over unchanged)."""
    cursor.execute("SELECT * FROM PlaceTable WHERE PlaceID = ?", (original_place_id,))
    original_data = cursor.fetchone()

    insert_cols = []
    insert_vals = []
    placeholders = []

    for col, val in zip(columns, original_data):
        if col == 'PlaceID':
            continue
        insert_cols.append(col)
        insert_vals.append(overrides.get(col, val))
        placeholders.append('?')

    insert_sql = (
        f"INSERT INTO PlaceTable ({', '.join(insert_cols)}) "
        f"VALUES ({', '.join(placeholders)})"
    )
    cursor.execute(insert_sql, insert_vals)

    return cursor.lastrowid


def clone_historical_place(
    cursor: sqlite3.Cursor,
    original_place_id: int,
    new_place_name: str,
    columns: List[str]
) -> int:
    """Clone a place record, maintaining coordinates and UUIDs, with a new name."""
    cursor.execute("SELECT PlaceID FROM PlaceTable WHERE Name = ?", (new_place_name,))
    result = cursor.fetchone()
    if result:
        debug_print(f"Place '{new_place_name}' exists (ID: {result[0]}). Reusing.")
        return result[0]

    new_reverse_name = create_reverse_place(new_place_name)
    return _clone_place_row(cursor, original_place_id, columns,
                            {'Name': new_place_name, 'Reverse': new_reverse_name})


def get_or_create_place_detail(
    cursor: sqlite3.Cursor,
    new_place_id: int,
    detail_name: str,
    original_site_id: int,
    columns: List[str]
) -> int:
    """Clone a place detail (like a hospital or church) to the new place."""
    if not detail_name:
        return 0

    cursor.execute("""
        SELECT PlaceID FROM PlaceTable
        WHERE MasterID = ? AND Name = ? AND PlaceType = 2
    """, (new_place_id, detail_name))

    result = cursor.fetchone()
    if result:
        return result[0]

    return _clone_place_row(cursor, original_site_id, columns, {'MasterID': new_place_id})


# ==========================================
# MAIN EXECUTION
# ==========================================
def main() -> None:
    """Main execution loop for the historical county fixer."""
    print("Loading Newberry Historical Shapefiles...")
    try:
        gdf = gpd.read_file(SHAPEFILE_PATH).to_crs("EPSG:4326")
        print("Shapefiles loaded.\n")
    except Exception as e:
        print(f"Failed to load shapefiles: {e}")
        return

    ca_shapefiles = load_canadian_shapefiles()
    if ca_shapefiles:
        print(f"Loaded Canadian boundaries for {len(ca_shapefiles)} census year(s).\n")
    else:
        print(f"No Canadian boundary data found - running US-only (expected at: {CA_SHAPEFILE_DIR}).\n")

    if not os.path.exists(RM_DATABASE):
        print(f"Error: Database file not found at {RM_DATABASE}")
        return

    if CREATE_BACKUP:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{RM_DATABASE}.{timestamp}.bak"
        shutil.copy2(RM_DATABASE, backup_path)
        print(f"Backup created at: {backup_path}")

    print(f"Connecting to Database: {RM_DATABASE}")
    conn = sqlite3.connect(RM_DATABASE)
    register_rmnocase(conn)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(PlaceTable)")
    place_table_columns = [row[1] for row in cursor.fetchall()]

    query = """
        SELECT e.EventID, e.Date, p.PlaceID, p.Name, p.Latitude, p.Longitude,
               e.SiteID, pd.Name AS DetailName
        FROM EventTable e
        JOIN PlaceTable p ON e.PlaceID = p.PlaceID
        LEFT JOIN PlaceTable pd ON e.SiteID = pd.PlaceID
        WHERE p.Latitude != 0 AND p.Longitude != 0
        AND p.Latitude IS NOT NULL AND p.Longitude IS NOT NULL
    """
    cursor.execute(query)
    events = cursor.fetchall()

    if os.name == 'nt':
        os.system('')

    print(f"Evaluating {len(events)} geocoded timeline markers...\n")
    print("-" * 50)

    global _progress_bar, _updated_count
    _updated_count = 0

    custom_format = (
        "{desc}: {percentage:3.0f}% |{bar:35}| "
        "{n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}"
    )

    with tqdm(
        events, desc="Mapping", bar_format=custom_format, colour="green",
        dynamic_ncols=True, mininterval=0.2
    ) as bar:
        _progress_bar = bar
        for event in bar:
            event_id, raw_date, place_id, current_name, lat, lon, site_id, detail_name = event

            target_date = parse_rm_date(raw_date)
            if not target_date:
                debug_print(f"[{current_name}] No usable date")
                continue

            try:
                active_polygons = gdf[
                    (gdf['START_DATE'] <= target_date) &
                    (gdf['END_DATE'] >= target_date)
                ]
            except Exception as date_error:
                debug_print(f"[{current_name}] Bad date compare ({date_error})")
                continue

            real_lat = lat / 1e7
            real_lon = lon / 1e7
            target_point = Point(real_lon, real_lat)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                matched_mask = active_polygons.geometry.contains(target_point)
                matched = active_polygons[matched_mask]

            is_canadian_match = False
            if matched.empty and ca_shapefiles:
                ca_year = nearest_canadian_census_year(target_date)
                ca_gdf = ca_shapefiles.get(ca_year)
                if ca_gdf is not None:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        ca_mask = ca_gdf.geometry.contains(target_point)
                        ca_matched = ca_gdf[ca_mask]
                    if not ca_matched.empty:
                        matched = ca_matched
                        is_canadian_match = True

            if matched.empty:
                debug_print(
                    f"[{current_name}] No historical polygon matched for {target_date}"
                )
                continue

            if is_canadian_match:
                new_place_name = build_ca_place_name(current_name, matched.iloc[0])
            else:
                new_place_name = build_us_place_name(current_name, matched.iloc[0])

            if new_place_name != current_name:
                new_place_id = clone_historical_place(cursor, place_id, new_place_name, place_table_columns)

                if site_id and site_id > 0 and detail_name:
                    new_site_id = get_or_create_place_detail(
                        cursor, new_place_id, detail_name, site_id, place_table_columns)
                else:
                    new_site_id = site_id

                cursor.execute("""
                    UPDATE EventTable
                    SET PlaceID = ?, SiteID = ?
                    WHERE EventID = ?
                """, (new_place_id, new_site_id, event_id))

                _updated_count += 1
                update_ui()

                bar.write(f"\033[92m[FORKED]\033[0m Event {event_id} [{target_date}] | "
                          f"\033[91m{current_name}\033[0m -> \033[92m{new_place_name}\033[0m")
            else:
                debug_print(f"[{current_name}] Name already matches.")

    _progress_bar = None
    conn.commit()
    conn.close()

    print("-" * 50)
    print(f"\nCompleted! Adjusted {_updated_count} display values without losing FamilySearch data fields.")


if __name__ == "__main__":
    main()
