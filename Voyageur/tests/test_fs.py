"""Tests for FS.py's build_universal_json() document_metadata fix and Commissioner
validation wiring - see the Voyageur-Parish-Scrip-scaffold design spec."""
import json
import FS


def test_parse_citation_matches_without_the_optional_nara_clause():
    """Real-world regression (2026-08-21): Voyageur.js's own
    fsBuildCitationTextFromApiResponse() only appends the trailing "; citing NARA microfilm
    publication ... (repo_loc: repo_name, n.d.)." clause when the image-level
    EXT_FILM_NBR/EXT_REPOSITORY_NAME fields are both present - a real gather missing those
    two fields produced a citation_text ending in a bare period right after browse_path.
    The old CITATION_RE required that clause unconditionally, so the whole match failed and
    collection_name/repository/browse_path all came back blank even though citation_text
    itself was non-empty - confirmed live this is why every citation field except
    collection_url was blank for a real 1950 Pembina, ND gather."""
    text = ('"United States Census, 1950: Pembina. Census 1950," database with images, '
            'FamilySearch (https://x : 21 August 2026), North Dakota > Pembina > image 3 of 14.')
    result = FS.parse_citation(text)
    assert result["collection_name"] == "United States Census, 1950: Pembina. Census 1950"
    assert result["repository"] == "FamilySearch"
    assert result["browse_path"] == "North Dakota > Pembina > image 3 of 14"
    assert result["publisher"] == ""
    assert result["pub_loc"] == ""


def test_parse_citation_still_matches_with_the_optional_nara_clause():
    text = ('"United States Census, 1950: Pembina. Census 1950," database with images, '
            'FamilySearch (https://x : 21 August 2026), North Dakota > Pembina > image 3 of 14; '
            'citing NARA microfilm publication T627 (Washington D.C.: The U.S. National Archives '
            'and Records Administration (NARA), n.d.).')
    result = FS.parse_citation(text)
    assert result["collection_name"] == "United States Census, 1950: Pembina. Census 1950"
    assert result["repository"] == "FamilySearch"
    assert result["browse_path"] == "North Dakota > Pembina > image 3 of 14"


def test_sanitize_item_id_filename_replaces_unsafe_characters():
    assert FS.sanitize_item_id_filename("abc 123/def") == "abc_123_def.jpg"


def test_sanitize_item_id_filename_preserves_safe_characters():
    assert FS.sanitize_item_id_filename("abc-123_DEF") == "abc-123_DEF.jpg"


def test_pid_from_identifier_is_deterministic_numeric_and_ten_digits():
    """'pid' must be a clean, GEDCOM-friendly numeric
    xref/REFN, not an ark-shaped string with colons and hyphens, and must stay stable across
    repeated gathers of the same record/person - hashlib (not Python's own randomized hash())."""
    pid = FS.pid_from_identifier("1:1:MF36-Z6D")
    assert pid.isdigit()
    assert len(pid) == 10
    assert FS.pid_from_identifier("1:1:MF36-Z6D") == pid
    assert FS.pid_from_identifier("1:1:MF36-Z6E") != pid


def test_normalize_familysearch_gather_url_strips_tracking_params():
    """User-reported: a URL copied from a FamilySearch search result carries wc/cc/i
    tracking params that aren't part of the ark's own identity and can send the viewer to
    the wrong page - only the ark path (collection id + image ark) and a clean
    view=index&lang=en query belong in the launch URL."""
    messy = ("https://www.familysearch.org/ark:/61903/3:1:33SQ-GRCN-QMV"
             "?wc=QZF7-X1H%3A648803501%2C649973301%2C649993301%2C1589282428%26cc%3D1810731"
             "&cc=1810731&lang=en&i=0")
    assert (FS.normalize_familysearch_gather_url(messy)
            == "https://www.familysearch.org/ark:/61903/3:1:33SQ-GRCN-QMV?view=index&lang=en")


def test_normalize_familysearch_gather_url_already_canonical_is_unchanged():
    canonical = "https://www.familysearch.org/ark:/61903/3:1:3QHN-PQHW-1YYN?view=index&lang=en"
    assert FS.normalize_familysearch_gather_url(canonical) == canonical


def test_build_universal_json_sets_real_document_metadata_from_item_id():
    raw = {"collection_title": "Test Parish Register"}
    items_raw = [{"item_id": "abc 123/def", "rows": [], "citation_text": ""}]
    result = FS.build_universal_json(raw, items_raw, {}, "church")

    metadata = result["sheets"][0]["document_metadata"]
    assert metadata["file_name"] == "abc_123_def.jpg"
    assert metadata["file_type"] == "jpg"


def test_build_universal_json_empty_item_id_yields_empty_file_name():
    raw = {"collection_title": "Test"}
    items_raw = [{"item_id": "", "rows": []}]
    result = FS.build_universal_json(raw, items_raw, {}, "church")

    assert result["sheets"][0]["document_metadata"]["file_name"] == ""


def test_normalize_familysearch_census_gather_derives_record_type():
    raw_census = {
        "census_year": "1900",
        "pages": [{
            "page_number": 3, "state": "Ohio", "county": "Lucas", "city": "", "country": "USA",
            "repository": "FamilySearch",
            "people": [
                {"columns": {"Given Name": "Marie", "Surname": "Boucher", "Gender": "F",
                             "Age": "35", "Relationship to Head": "Head", "Family Number": "2"},
                 "pid": "p2"},
            ],
        }],
    }

    normalized = FS.normalize_familysearch_census_gather(raw_census, "1900 US Census - Ohio")

    assert normalized["record_type_name"] == "Census_1900"
    assert normalized["collection_title"] == "1900 US Census - Ohio"
    assert len(normalized["sheets"]) == 1


def test_build_census_json_accepts_household_view_row_shape():
    """Locks in this design's core claim: the household-view scraper (replacing the old
    Image Index table scraper) can produce rows in this exact shape with zero FS.py/Census.py
    changes - see docs/superpowers/specs/2026-08-07-familysearch-household-view-gather-design.md.
    Column keys and values mirror what a real View Name panel showed live (Joseph Rolette
    household, 1850 Minnesota census): Given Name/Surname split from Essential Information,
    Gender from "Sex: M", Relationship to Head from Household Details ("Spouse"/"Child"),
    Family Number synthesized per household section (not from FamilySearch, which has no
    such field)."""
    raw = {"collection_title": "Minnesota, 1850 federal census : population schedules"}
    items_raw = [{
        "item_id": "3:1:S3HY-67NL-ZP",
        "citation_text": '"Minnesota, 1850 federal census," database with images, FamilySearch '
        '(https://familysearch.org : 3 August 2026), Kittson > image 39; '
        "NARA microfilm publication.",
        "rows": [
            {"columns": {"Given Name": "Joseph", "Surname": "Rolette", "Gender": "M", "Age": "35",
                         "Relationship to Head": "Head", "Family Number": "1"},
             "record_ark": "1:1:MZ2Z-WM4", "person_ark": "9CJG-851"},
            {"columns": {"Given Name": "Angelic", "Surname": "Rolette", "Gender": "F", "Age": "30",
                         "Relationship to Head": "Spouse", "Family Number": "1"},
             "record_ark": "1:1:MZ2Z-WM5", "person_ark": ""},
            {"columns": {"Given Name": "Joseph", "Surname": "Rolette", "Gender": "M", "Age": "9",
                         "Relationship to Head": "Child", "Family Number": "1"},
             "record_ark": "1:1:MZ2Z-WM6", "person_ark": ""},
            {"columns": {"Given Name": "George", "Surname": "Monison", "Gender": "M", "Age": "22",
                         "Relationship to Head": "No Relation", "Family Number": "1"},
             "record_ark": "1:1:MZ2Z-WM7", "person_ark": ""},
            {"columns": {"Given Name": "J Baptiste", "Surname": "Cardinal", "Gender": "M", "Age": "40",
                         "Family Number": "2"},
             "record_ark": "1:1:MZ2Z-XX1", "person_ark": ""},
        ],
    }]

    result = FS.build_census_json(raw, items_raw, {})

    people = result["pages"][0]["people"]
    assert len(people) == 5
    # 'pid' is a deterministic, numbers-only, 10-digit hash (2026-08-21 user-directed
    # design) that prefers person_ark (the true, enduring Family Tree profile id) when a
    # genuine attachment is on file - the same real person attached across multiple
    # records/census years then collapses to the same pid, usable later to merge them -
    # falling back to record_ark otherwise. person_ark is still preserved separately too
    # (the raw value, not the hash). familysearch_url still points at record_ark, since
    # that's this specific historical record's own citation link.
    assert people[0]["pid"] == FS.pid_from_identifier("9CJG-851")
    assert people[0]["pid"].isdigit() and len(people[0]["pid"]) == 10
    assert people[0]["record_ark"] == "1:1:MZ2Z-WM4"
    assert people[0]["person_ark"] == "9CJG-851"
    assert people[0]["familysearch_url"] == "https://www.familysearch.org/ark:/61903/1:1:MZ2Z-WM4"
    assert people[0]["columns"]["Relationship to Head"] == "Head"
    # Everyone else has no person_ark on file, so pid falls back to record_ark unchanged.
    assert people[1]["pid"] == FS.pid_from_identifier("1:1:MZ2Z-WM5")
    assert people[1]["person_ark"] == ""
    # J Baptiste Cardinal's household has no relationship data at all (the bare-"Primary"
    # case confirmed live) - the column must simply be absent, not fabricated as empty string.
    assert "Relationship to Head" not in people[4]["columns"]


def test_build_census_json_pages_use_each_items_own_location_not_the_first_ones():
    """Regression: Voyageur.js's buildFsItemData() computes each item's own state/county/
    city/country/enumeration_district directly (from the Image-Index API response or the
    scraped citation - see fsBuildItemData image-index/names branches), but
    build_census_json() previously derived location_info once from only the FIRST item's
    citation_text and applied it to every page, silently discarding the second (and every
    later) item's own, potentially different, location fields."""
    items_raw = [
        {
            "item_id": "item-1", "citation_text": "",
            "state": "Minnesota", "county": "Ramsey", "city": "St Paul",
            "country": "USA", "enumeration_district": "45",
            "rows": [],
        },
        {
            "item_id": "item-2", "citation_text": "",
            "state": "North Dakota", "county": "Pembina", "city": "Walhalla",
            "country": "USA", "enumeration_district": "12",
            "rows": [],
        },
    ]
    result = FS.build_census_json({"collection_title": "1900 Census"}, items_raw, {})

    assert result["pages"][0]["state"] == "Minnesota"
    assert result["pages"][0]["county"] == "Ramsey"
    assert result["pages"][0]["enumeration_district"] == "45"
    assert result["pages"][1]["state"] == "North Dakota"
    assert result["pages"][1]["county"] == "Pembina"
    assert result["pages"][1]["enumeration_district"] == "12"


def test_build_census_json_page_falls_back_to_citation_location_when_items_own_is_blank():
    """When an item's own state/county/city/country/enumeration_district came back empty
    (e.g. buildFsItemData()'s citation-text regex didn't match), the page still falls back
    to the shared first-citation-derived location_info, rather than losing the location
    entirely."""
    items_raw = [{
        "item_id": "item-1",
        "citation_text": '"1900 Census," database with images, FamilySearch '
        '(https://familysearch.org : 3 August 2026), Minnesota > Ramsey > St Paul; '
        "some publisher, some place.",
        "rows": [],
    }]
    result = FS.build_census_json({"collection_title": "1900 Census"}, items_raw, {})

    assert result["pages"][0]["state"] == "Minnesota"
    assert result["pages"][0]["county"] == "Ramsey"
    assert result["pages"][0]["city"] == "St Paul"


def test_build_census_json_prefers_scraped_collection_id_over_citation_cc_param():
    """Regression: Voyageur.js's scrapeFsCollectionInfo() reads the real FamilySearch
    numeric collection id from the "Information" tab's "Historical Record Collection" link
    (there is no `cc=`-style query param in FamilySearch's own generated citation text under
    this architecture - see parse_citation()'s own note) - this per-item value must win over
    whatever parse_citation() derived from citation_text, same convention as the location
    fields above."""
    items_raw = [{
        "item_id": "item-1", "citation_text": "",
        "collection_id": "4464515", "collection_name": "United States, Census, 1950",
        "rows": [],
    }]
    result = FS.build_census_json({"collection_title": "1950 Census"}, items_raw, {})

    assert result["pages"][0]["collection_id"] == "4464515"
    assert result["pages"][0]["collection_name"] == "United States, Census, 1950"


def test_build_census_json_page_falls_back_to_citation_collection_id_when_items_own_is_blank():
    """When Voyageur.js couldn't find/scrape the Information-tab collection link (e.g. it
    never appeared in time), the page still falls back to whatever parse_citation() found
    in citation_text, rather than losing collection_id entirely."""
    items_raw = [{
        "item_id": "item-1",
        "citation_text": '"1900 Census," database with images, FamilySearch '
        '(https://familysearch.org?cc=1803986 : 3 August 2026), Minnesota > Ramsey > St Paul; '
        "some publisher, some place.",
        "rows": [],
    }]
    result = FS.build_census_json({"collection_title": "1900 Census"}, items_raw, {})

    assert result["pages"][0]["collection_id"] == "1803986"


def test_convert_raw_gather_to_final_routes_census_collections_through_census_path():
    raw = {
        "collection_title": "United States, Census, 1880",
        "items": [
            {
                "item_id": "abc",
                "citation_text": (
                    '"1880 Census," database with images, FamilySearch '
                    '(https://familysearch.org/x : accessed 1 Jan 2026), Alabama > Autauga > '
                    'image 1 of 1; citing NARA microfilm publication T9 (Washington D.C.: '
                    'National Archives and Records Administration, n.d.).'
                ),
                "rows": [{"columns": {"Name": "John Smith"}, "record_ark": "ARK1"}],
            }
        ],
    }

    final_data, clean_name = FS.convert_raw_gather_to_final(raw)

    assert "sheets" in final_data
    assert clean_name is None or clean_name.endswith(".json")


def test_convert_raw_gather_to_final_routes_non_census_collections_through_universal_path():
    raw = {
        "collection_title": "Quebec, Catholic Parish Registers",
        "items": [{"item_id": "abc", "citation_text": "", "rows": []}],
    }

    final_data, clean_name = FS.convert_raw_gather_to_final(raw)

    assert "sheets" in final_data
    assert clean_name is None


MINIMAL_FINAL_DATA = {
    "citation": {"collection_name": "United States Census, 1880"},
    "sheets": [{
        "records": [{
            "year": "1880",
            "type_specific_fields": {
                "country": "USA",
                "state": "North Dakota",
                "county": "Pembina",
                "city": "Walhalla",
                "enumeration_district": "",
            }
        }]
    }]
}


def test_fs_main_image_routing_uses_live_data_not_filename():
    """census_year and location_folder come from final_data, never from stem.split."""
    from _gather_helpers import extract_census_image_routing_fields
    year, country, loc_folder, coll_name = extract_census_image_routing_fields(MINIMAL_FINAL_DATA)
    assert year == "1880"
    assert country == "USA"
    assert loc_folder == "North Dakota - Pembina - Walhalla"
    assert coll_name == "United States Census, 1880"


def test_fs_location_folder_skips_empty_fields():
    """Empty city is omitted from location_folder, no trailing ' - '."""
    data = json.loads(json.dumps(MINIMAL_FINAL_DATA))
    data["sheets"][0]["records"][0]["type_specific_fields"]["city"] = ""
    from _gather_helpers import extract_census_image_routing_fields
    _, _, loc_folder, _ = extract_census_image_routing_fields(data)
    assert loc_folder == "North Dakota - Pembina"
    assert not loc_folder.endswith(" - ")


def test_fs_location_folder_nests_past_city_down_to_enumeration_district():
    """Nests one level past city, down to the
    enumeration district - a single city/township can span several EDs, so ED is the level
    that actually disambiguates one image set from another within it."""
    data = json.loads(json.dumps(MINIMAL_FINAL_DATA))
    data["sheets"][0]["records"][0]["type_specific_fields"]["enumeration_district"] = "12"
    from _gather_helpers import extract_census_image_routing_fields
    _, _, loc_folder, _ = extract_census_image_routing_fields(data)
    assert loc_folder == "North Dakota - Pembina - Walhalla - 12"


def test_fs_image_routing_uses_majority_state_not_just_first_record():
    """Real-world regression (2026-08-21): a real 1950 Pembina, ND gather had its very
    first record's own 'state' field read "Advance" (a citation-parsing artifact for that
    one record) while the true majority of records correctly said "North Dakota" - routing
    every image by the first record alone sent them all into a one-off "Advance" folder
    that didn't match Archivist/Census.py's own get_json_fallback() (already mode-based),
    producing a GEDCOM FILE reference to a folder the images were never actually saved in."""
    from _gather_helpers import extract_census_image_routing_fields
    records = [{"year": "1950", "type_specific_fields": {"country": "USA", "state": "Advance",
                                                         "county": "", "city": ""}}]
    records += [{"year": "1950", "type_specific_fields": {"country": "USA", "state": "North Dakota",
                                                          "county": "Pembina", "city": ""}}] * 3
    data = {"citation": {"collection_name": "United States Census, 1950"},
            "sheets": [{"records": records}]}
    _, _, loc_folder, _ = extract_census_image_routing_fields(data)
    assert loc_folder == "North Dakota - Pembina"


def test_fs_normalize_then_extract_routing_fields_round_trips():
    """Integration regression: extract_census_image_routing_fields() must read whatever
    shape normalize_familysearch_census_gather() actually produces, not a hand-built
    fixture - census_year previously lived only in test fixtures' type_specific_fields, a
    shape the real normalizer never produces (it stores year as the record's own top-level
    "year" key - see census_schema.py), which let census_year come back "" for every real
    FamilySearch gather while the hand-built-fixture tests kept passing."""
    from _gather_helpers import extract_census_image_routing_fields
    raw_census = {
        "census_year": "1900",
        "pages": [{
            "page_number": 3, "state": "Ohio", "county": "Lucas", "city": "", "country": "USA",
            "repository": "FamilySearch",
            "people": [
                {"columns": {"Given Name": "Marie", "Surname": "Boucher", "Gender": "F",
                             "Age": "35", "Relationship to Head": "Head", "Family Number": "2"},
                 "pid": "p2"},
            ],
        }],
    }
    normalized = FS.normalize_familysearch_census_gather(raw_census, "1900 US Census - Ohio")
    year, country, loc_folder, _ = extract_census_image_routing_fields(normalized)
    assert year == "1900"
    assert country == "USA"
    assert loc_folder == "Ohio - Lucas"


def test_recover_orphaned_runs_quarantines_corrupt_raw_json_instead_of_crashing(tmp_path):
    """Real-world regression (2026-08-21): a stale TMP_FS_ raw JSON left behind by an
    interrupted prior gather can have a corrupt byte spliced into it (confirmed live - not
    a bug in this pipeline's own JSON.stringify-based serialization). Before this fix,
    _recover_orphaned_runs's bare json.loads() crashed the ENTIRE current gather on
    startup - and since find_orphaned_gather_runs re-scans by filename pattern with no
    memory of a prior failure, the exact same file would crash every subsequent run too.
    The corrupt file is moved into a "Failed" subfolder of the JSON
    output directory (out of Downloads entirely) so recovery can report it and move on,
    not loop forever."""
    downloads_dir = tmp_path / "Downloads"
    downloads_dir.mkdir()
    json_target_dir = tmp_path / "Project"
    json_target_dir.mkdir()

    stale = downloads_dir / "TMP_FS_deadbeef_FS - Some Census.json"
    stale.write_text('{"items": [{"foo": "",f\n"bar": "baz"}]}', encoding="utf-8")

    FS._recover_orphaned_runs(downloads_dir, "current_run_id", json_target_dir, str(tmp_path), "skip")

    assert not stale.exists()
    quarantined = json_target_dir / "Failed" / "TMP_FS_deadbeef_FS - Some Census.json"
    assert quarantined.exists()
    assert quarantined.read_text(encoding="utf-8") == '{"items": [{"foo": "",f\n"bar": "baz"}]}'
    assert list(json_target_dir.iterdir()) == [json_target_dir / "Failed"]
