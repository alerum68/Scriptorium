"""Tests for census_schema.py's field-map normalization across all three census eras."""
import census_schema


def _page(people, **overrides):
    page = {
        "page_number": 1, "image_id": "img1", "country": "USA", "state": "Minnesota",
        "county": "Ramsey", "city": "St. Paul", "place_details": "", "enumeration_district": "12",
        "film_number": "", "roll_number": "T624_1", "apid_db": "", "collection_id": "",
        "collection_name": "", "collection_url": "",
        "repository": "Ancestry.com", "repository_loc": "", "publisher": "", "pub_loc": "", "people": people,
    }
    page.update(overrides)
    return page


def test_normalize_and_validate_census_accepts_valid_normalized_output(capsys):
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Relationship to Head": "Head", "Family Number": "5"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_and_validate_census(raw, "ancestry_census", "1900 US Census", "Census_1900")
    assert doc["collection_title"] == "1900 US Census"
    captured = capsys.readouterr()
    assert "[WARN]" not in captured.out


def test_citation_carries_collection_id_from_page_for_source_id_resolution():
    """FS.py's build_census_json attaches the FamilySearch catalog collection number (the
    'CC' from ?cc=<id> in the collection URL) as page['collection_id'] so Archivist's
    Census.py can use it as CENSUS_SOURCE_ID instead of an auto-assigned registry id -
    this dropped silently when the shared citation dict below didn't copy it over."""
    raw = {
        "census_year": "1860", "location": "Dakota Territory",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1"},
        ], collection_id="1473181")],
    }
    doc = census_schema.normalize_census_pages(raw, "familysearch_census", "1860 US Census", "Census_1860")

    assert doc["citation"]["collection_id"] == "1473181"


def test_citation_prefers_parsed_collection_name_and_url_over_raw_document_title():
    """FS.py's citation parser (parse_citation) produces a clean collection name/url from
    FamilySearch's own generated citation prose - build_census_json now carries those onto
    page['collection_name']/['collection_url']. This must win over the collection_title
    param passed in here, which is the raw document.title (often 'Title; ark URL' shaped
    on FamilySearch collection pages) - using it directly was the source of a corrupted
    citation.collection_name in production."""
    raw = {
        "census_year": "1860", "location": "Dakota Territory",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1"},
        ], collection_name="United States, Census, 1860",
           collection_url="https://familysearch.org/ark:/61903/3:1:33S7-9YBJ-9PFZ")],
    }
    doc = census_schema.normalize_census_pages(
        raw, "familysearch_census",
        "United States, Census, 1860; https://familysearch.org/ark:/61903/3:1:33S7-9YBJ-9PFZ?cc=1473181",
        "Census_1860")

    assert doc["citation"]["collection_name"] == "United States, Census, 1860"
    assert doc["citation"]["collection_url"] == "https://familysearch.org/ark:/61903/3:1:33S7-9YBJ-9PFZ"


def test_document_metadata_file_name_sanitizes_familysearch_ark_image_id():
    """Regression: document_metadata.file_name was hardcoded to '' - Archivist's
    build_census_dataframe_from_unified reads exactly this field into the DataFrame's
    Image_ID column (Census.py:1403), which then feeds the GEDCOM's '1 FILE' media path.
    With it empty, every census GEDCOM (both sources) silently pointed OBJE records at a
    filename that doesn't exist on disk (confirmed live: '_00001.jpg' instead of the real
    saved '3_1_33S7-9YBJ-9PD7.jpg'/'4211353_00001.jpg') - RootsMagic/FTM would show every
    census image as missing. FS's raw image_id is an unsanitized ark ('3:1:33S7-9YBJ-9PD7')
    and must be sanitized the same way FS.py's own sanitize_item_id_filename does for the
    non-census path, to match what's actually on disk."""
    raw = {
        "census_year": "1860", "location": "Dakota Territory",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1"},
        ], image_id="3:1:33S7-9YBJ-9PD7")],
    }
    doc = census_schema.normalize_census_pages(raw, "familysearch_census", "1860 US Census", "Census_1860")

    doc_meta = doc["sheets"][0]["document_metadata"]
    assert doc_meta["file_name"] == "3_1_33S7-9YBJ-9PD7.jpg"
    assert doc_meta["file_type"] == "jpg"


def test_document_metadata_file_name_passes_through_already_clean_ancestry_image_id():
    raw = {
        "census_year": "1860", "location": "Dakota Territory",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1"},
        ], image_id="4211353_00001")],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1860 US Census", "Census_1860")

    assert doc["sheets"][0]["document_metadata"]["file_name"] == "4211353_00001.jpg"


def test_type_specific_fields_carries_place_details_through():
    """Regression: Ancestry's page-level place_details (leftover browsePath segments beyond
    state/county/city/ED, e.g. a specific street/ward/precinct) was silently dropped by the
    unified-schema path - present in page but never copied into type_specific_fields."""
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1"},
        ], place_details="Ward 3")],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    record = doc["sheets"][0]["records"][0]
    assert record["type_specific_fields"]["place_details"] == "Ward 3"


def test_participant_carries_alternate_birth_places_through():
    """Regression: Ancestry's per-person alternate_birth_places (real data - readPersonAlternates()
    in Voyageur.js) was silently dropped - _normalize_participant's initial dict set
    alternate_names at the top level (correctly read by Census.py's row['AlternateNames'])
    but never set alternate_birth_places anywhere, even though Census.py's unified-path
    adapter reads it from type_specific_fields (pts.get('alternate_birth_places', [])) -
    build_alternate_birth_lines() silently never fired for any unified-path gather."""
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1",
             "alternate_birth_places": [{"value": "Quebec, Canada"}]},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    participant = doc["sheets"][0]["records"][0]["participants"][0]
    assert participant["type_specific_fields"]["alternate_birth_places"] == [{"value": "Quebec, Canada"}]


def test_married_within_year_is_a_per_participant_field_not_dropped_as_a_record_field():
    """Regression: 'Married within Year' was categorized as a record_field in
    ancestry_census.yaml, which only handles household/dwelling-grouping keys
    (_household_key only reads family_number/dwelling_number targets) - its own value was
    never actually copied anywhere, just marked 'consumed' so it wouldn't get flagged as
    unmapped. Census.py's build_gedcom_from_census reads it PER PERSON (row.get('Married
    within Year') on the head/wife rows specifically) to decide whether to emit a '1 MARR'
    fact - it needed to be a participant_fields target, not a record_fields one."""
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Married within Year": "Yes"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    participant = doc["sheets"][0]["records"][0]["participants"][0]
    assert participant["type_specific_fields"]["married_within_year"] == "Yes"
    assert not participant["review"], participant.get("review_reason")


def test_ancestry_birth_month_and_marital_status_are_mapped_not_unmapped():
    """Regression for the two new participant_fields entries this session's Ancestry
    index-panel-data investigation surfaced (1880's SelfBirthMonth, 1880/1920's
    SelfMaritalStatus) - confirms they land in type_specific_fields and do NOT trigger
    the unmapped-column review flag."""
    raw = {
        "census_year": "1880", "location": "Dakota Territory",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Birth Month": "March", "Marital Status": "Married"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1880 US Census", "Census_1880")

    participant = doc["sheets"][0]["records"][0]["participants"][0]
    assert participant["type_specific_fields"]["birth_month"] == "March"
    assert participant["type_specific_fields"]["marital_status"] == "Married"
    assert not participant["review"], participant.get("review_reason")


def test_ancestry_religion_and_nationality_are_mapped_facts_not_unmapped():
    """Regression for this plan's Task 2 - Religion and Nationality are real,
    pre-existing FactTypes.json fact types (confirmed via Task 2 Step 1's live check),
    common on Canadian census years and previously absent from ancestry_census.yaml
    entirely. Confirms both land as facts, not flagged for manual review."""
    raw = {
        "census_year": "1871", "location": "Nova Scotia, Canada",
        "pages": [_page([
            {
                "columns": {
                    "Given Name": "Donald", "Surname": "MacDonald", "Gender": "M",
                    "Age": "75", "Religion": "C Of Scotland", "Nationality": "Scotch",
                },
                "pid": "p1",
            },
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1871 Canada Census", "Census_1871")

    participant = doc["sheets"][0]["records"][0]["participants"][0]
    fact_types = {f["fact_type"] for f in participant["facts"]}
    assert "Religion" in fact_types
    assert "Nationality" in fact_types
    assert not participant["review"], participant.get("review_reason")


def test_street_address_is_mapped_and_house_number_is_not_double_claimed():
    """Regression: 'Street'/'Street Address'/'Address' were unmapped in
    ancestry_census.yaml's participant_fields - Census.py's build_gedcom_from_census
    already reads row['Street'] to build a CENS fact's '2 ADDR' line, but the raw value
    never reached it. Deliberately NOT mapping 'House Number' to the same target here -
    that header is already claimed by record_fields as a dwelling_number alias (a
    different census concept, the sequential dwelling-visited count), so it must not also
    resolve to a street value."""
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Street": "212 Main St", "House Number": "14"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    participant = doc["sheets"][0]["records"][0]["participants"][0]
    assert participant["type_specific_fields"]["street"] == "212 Main St"
    assert doc["sheets"][0]["records"][0]["record_number"] == "14"
    assert "unmapped" not in participant["type_specific_fields"]


def test_citation_falls_back_to_raw_collection_title_when_no_parsed_name():
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    assert doc["citation"]["collection_name"] == "1900 US Census"


def test_group_household_prefers_household_id_over_column_based_key():
    """Ancestry's index-panel-data API supplies a real, stable household_id per person
    (Task 1/2/3 of docs/superpowers/plans/2026-08-15-ancestry-index-panel-extraction.md) -
    when present, it must win over the existing Family/Dwelling Number column-based
    inference, since it's a direct signal from Ancestry itself rather than a guess from
    column text that can vary or be absent by census year. Two people share a
    household_id but have DIFFERENT Family Number column values (simulating a data
    inconsistency) to prove household_id, not the column, decides the grouping."""
    raw = {
        "census_year": "1920", "location": "North Dakota",
        "pages": [_page([
            {"columns": {"Given Name": "Mary", "Surname": "Darylus", "Gender": "F", "Age": "67",
                         "Family Number": "1"}, "pid": "p1", "household_id": "79215820"},
            {"columns": {"Given Name": "Helen", "Surname": "Darylus", "Gender": "F", "Age": "42",
                         "Family Number": "2"}, "pid": "p2", "household_id": "79215820"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    records = doc["sheets"][0]["records"]
    assert len(records) == 1, f"expected both people grouped into one household by household_id, got: {records}"
    assert len(records[0]["participants"]) == 2


def test_group_household_falls_back_to_column_based_key_when_household_id_absent():
    """The DOM-table-scraping fallback path (Task 3) never sets household_id - confirms
    the existing column-based grouping still works completely unchanged when it's
    absent, matching every pre-existing test in this file."""
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                         "Family Number": "5"}, "pid": "p1"},
            {"columns": {"Given Name": "Marie", "Surname": "Gagnon", "Gender": "F", "Age": "38",
                         "Family Number": "5"}, "pid": "p2"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    records = doc["sheets"][0]["records"]
    assert len(records) == 1
    assert records[0]["record_number"] == "5"


def test_relationship_era_groups_household_and_maps_role_name():
    raw = {
        "census_year": "1900",
        "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Relationship to Head": "Head", "Family Number": "5"}, "pid": "p1"},
            {"columns": {"Given Name": "Marie", "Surname": "Gagnon", "Gender": "F", "Age": "38",
                         "Relationship to Head": "Wife", "Family Number": "5"}, "pid": "p2"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")

    assert doc["record_type_name"] == "Census_1900"
    records = doc["sheets"][0]["records"]
    assert len(records) == 1
    record = records[0]
    assert record["event_type"] == "Census (family)"
    assert record["type_specific_fields"]["family_number"] == "5"
    assert len(record["participants"]) == 2

    head = next(p for p in record["participants"] if p["role_name"] == "Head")
    assert head["std_given"] == "Jean"
    assert head["std_surname"] == "Gagnon"
    assert head["sex"] == "M"
    assert head["age"] == "40"
    assert head["review"] is False

    wife = next(p for p in record["participants"] if p["role_name"] == "Wife")
    assert wife["std_given"] == "Marie"


def test_heuristic_era_groups_by_family_number_with_no_relationship_column():
    raw = {
        "census_year": "1860", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Family Number": "5"}, "pid": "p1"},
            {"columns": {"Given Name": "Marie", "Surname": "Gagnon", "Gender": "F", "Age": "38",
                         "Family Number": "5"}, "pid": "p2"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1860 US Census", "Census_1860")

    records = doc["sheets"][0]["records"]
    assert len(records) == 1
    assert len(records[0]["participants"]) == 2
    # No relationship column present at all this era - role_name stays unset, Archivist's
    # own heuristic parser resolves family position from age/sex/surname, not this step.
    assert all(p["role_name"] is None for p in records[0]["participants"])


def test_pre1850_era_has_no_family_number_and_no_grouping():
    raw = {
        "census_year": "1820", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1820 US Census", "Census_1820")

    records = doc["sheets"][0]["records"]
    assert len(records) == 1
    assert len(records[0]["participants"]) == 1
    assert "family_number" not in records[0]["type_specific_fields"]


def test_two_unrelated_households_on_one_page_stay_separate():
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M",
                        "Relationship to Head": "Head", "Family Number": "5"}, "pid": "p1"},
            {"columns": {"Given Name": "Louis", "Surname": "Riel", "Gender": "M",
                         "Relationship to Head": "Head", "Family Number": "6"}, "pid": "p2"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")
    records = doc["sheets"][0]["records"]
    assert len(records) == 2
    assert {r["type_specific_fields"]["family_number"] for r in records} == {"5", "6"}


def test_unmapped_column_is_preserved_but_no_longer_flags_for_review():
    """2026-08-21: an unmapped column no longer triggers review - with raw-passthrough
    extraction, most people legitimately have several unmapped fields (metadata, office-use
    codes, etc.) as the normal, expected case, not an anomaly. The data must still be fully
    preserved (never dropped), just without flagging every participant/record for review
    over routine, expected gaps."""
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M",
                        "Relationship to Head": "Head", "Family Number": "5",
                         "Some Brand New Column Ancestry Just Added": "mystery value"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")
    participant = doc["sheets"][0]["records"][0]["participants"][0]

    assert participant["review"] is False
    assert "Some Brand New Column Ancestry Just Added" in participant["type_specific_fields"]["unmapped"]
    unmapped = participant["type_specific_fields"]["unmapped"]
    assert unmapped["Some Brand New Column Ancestry Just Added"] == "mystery value"
    assert doc["sheets"][0]["records"][0]["review"] is False


def test_recognized_extra_columns_become_typed_facts_not_flagged_for_review():
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M",
                        "Relationship to Head": "Head", "Family Number": "5",
                         "Occupation": "Farmer", "Immigration Year": "1889"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1900 US Census", "Census_1900")
    participant = doc["sheets"][0]["records"][0]["participants"][0]

    assert participant["review"] is False
    fact_types = {f["fact_type"] for f in participant["facts"]}
    assert fact_types == {"Occupation", "Immigration"}
    occupation_fact = next(f for f in participant["facts"] if f["fact_type"] == "Occupation")
    assert occupation_fact["value"] == "Farmer"


def test_familysearch_census_field_map_loads_and_normalizes():
    raw = {
        "census_year": "1900", "location": "Manitoba",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M",
                        "Relationship to Head": "Head", "Family Number": "5"}, "pid": "p1",
             "fsftid": "ABCD-123"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "familysearch_census", "1900 Canada Census", "Census_1900")
    participant = doc["sheets"][0]["records"][0]["participants"][0]
    assert participant["std_given"] == "Jean"
    assert participant["type_specific_fields"]["fsftid"] == "ABCD-123"


def test_familysearch_occupation_maps_to_named_field_not_a_duplicate_fact():
    """Commissioner's own Participant.facts docstring
    says "do not duplicate a fact already covered by a named field (occupation, birth_date,
    etc.) here" - FamilySearch's raw OCCUPATION/Occupation fields must land in the
    participant's own named 'occupation' field, not also produce a facts-array entry."""
    raw = {
        "census_year": "1950", "location": "North Dakota",
        "pages": [_page([
            {"columns": {"Given Name": "Jess", "Surname": "Crowston", "Gender": "M",
                         "Relationship to Head": "Head", "Family Number": "5",
                         "OCCUPATION": "Farmer"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_census_pages(raw, "familysearch_census", "1950 US Census", "Census_1950")
    participant = doc["sheets"][0]["records"][0]["participants"][0]
    assert participant["occupation"] == "Farmer"
    assert not any(f.get("fact_type") == "Occupation" for f in participant["facts"])


def test_normalize_and_validate_census_returns_normalized_doc():
    raw = {
        "census_year": "1900", "location": "Minnesota",
        "pages": [_page([
            {"columns": {"Given Name": "Jean", "Surname": "Gagnon", "Gender": "M", "Age": "40",
                        "Relationship to Head": "Head", "Family Number": "5"}, "pid": "p1"},
        ])],
    }
    doc = census_schema.normalize_and_validate_census(raw, "ancestry_census", "1900 US Census", "Census_1900")

    assert doc["record_type_name"] == "Census_1900"
    assert doc["collection_title"] == "1900 US Census"
    assert len(doc["sheets"]) == 1


def test_complex_field_integration_standard_mappings():
    raw = {
        "census_year": "1940", "location": "USA",
        "pages": [_page([{"columns": {
            "Income": "500",
            "Tribe": "Cherokee",
            "EducationCost": "50",
            "CauseOfDeath": "Fever",
            "CannotRead1": "Yes",
            "Given Name": "John",
            "Surname": "Doe",
        }, "pid": "p1"}])],
    }
    doc = census_schema.normalize_census_pages(raw, "ancestry_census", "1940 Census", "C1940")
    facts = doc["sheets"][0]["records"][0]["participants"][0]["facts"]
    fact_types = [f["fact_type"] for f in facts]

    assert "Property" in fact_types
    assert "Nationality" in fact_types
    assert "Education" in fact_types
    assert "Death" in fact_types
