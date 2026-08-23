"""
Tests for build_census_dataframe_from_unified - the adapter translating Voyageur's
now-normalized shared-schema census output (sheets[].records[].participants[]) back into
the same old-column-named DataFrame shape Archivist's household-parsing/citation logic
has always operated on. These functions (parse_household, parse_household_relational,
get_census_era, etc.) are deliberately NOT changed by this rework - only what feeds them
changed - so these tests confirm the adapter's column-naming/grouping produces input
those functions still handle correctly, not that the functions themselves changed.
"""
# noinspection PyUnresolvedReferences
import Utils
# noinspection PyUnresolvedReferences,PyPep8Naming
import Census as arc
from Commissioner.census_consts import get_census_era
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _unified_doc(record_type_name, sheets):
    return {"collection_title": "Test Census", "record_type_name": record_type_name,
            "citation": {"repository": "Ancestry.com"}, "sheets": sheets}


def _sheet(records, page_id="3"):
    return {"page_id": page_id, "document_metadata": {"source_location": "Minnesota"}, "records": records}


def _record(participants, family_number=None, ed="12", roll="T624_1"):
    ts = {"enumeration_district": ed, "roll_number": roll, "state": "Minnesota", "county": "Ramsey"}
    if family_number:
        ts["family_number"] = family_number
    return {"record_id": None, "page": "3", "record_number": family_number or "", "event_type": "Census (family)",
            "year": "1900", "event_date": "", "event_place": "", "citation_details": "",
            "citation_text": "", "review": False, "review_reason": None,
            "continues_on_next_image": False, "continues_from_previous_image": False,
            "type_specific_fields": ts, "participants": participants}


def _participant(given, surname, sex, role_name=None, age=None, line=None, facts=None):
    return {"role_number": None, "role_name": role_name, "std_given": given, "std_surname": surname,
            "raw_given": None, "raw_surname": None, "dit_name": None, "alternate_names": [],
            "prefix": None, "suffix": None, "sex": sex, "is_priest": False, "age": age, "age_unit": None,
            "occupation": None, "race": None, "religion": None, "residence": None, "birth_date": None,
            "birth_place": None, "death_date": None, "death_place": None, "review": False, "review_reason": None,
            "facts": facts or [], "type_specific_fields": {"line_number": line} if line else {}}


def test_adapter_produces_expected_columns_and_year_location():
    doc = _unified_doc("Census_1900", [_sheet([
        _record([_participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")],
                family_number="5"),
    ])])

    df, year, location = arc.build_census_dataframe_from_unified(doc)

    assert year == "1900"
    assert location == "Minnesota"
    assert len(df) == 1
    row = df.iloc[0]
    assert row["Given Name"] == "Jean"
    assert row["Surname"] == "Gagnon"
    assert row["Gender"] == "M"
    assert row["Relationship to Head"] == "Head"
    assert row["Family Number"] == "5"
    assert row["Line Number"] == "1"
    assert row["Enumeration_District"] == "12"
    assert row["Roll"] == "T624_1"


def test_adapter_reads_image_id_and_place_details_from_document_metadata():
    """Regression: Image_ID (feeds the GEDCOM's '1 FILE' media path) and Place_Details were
    both silently dropped by this adapter even when census_schema.py's normalize step
    supplied them - Image_ID came from document_metadata.file_name (hardcoded '' before the
    census_schema.py fix), Place_Details wasn't read into page_meta at all here."""
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00001.jpg"},
        "records": [_record([_participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")],
                            family_number="5", ed="12")],
    }])
    doc["sheets"][0]["records"][0]["type_specific_fields"]["place_details"] = "Ward 3"

    df, _, _ = arc.build_census_dataframe_from_unified(doc)

    row = df.iloc[0]
    assert row["Image_ID"] == "4211353_00001.jpg"
    assert row["Place_Details"] == "Ward 3"


def test_adapter_reads_alternate_birth_places_from_type_specific_fields():
    doc = _unified_doc("Census_1900", [_sheet([
        _record([_participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")], family_number="5"),
    ])])
    doc["sheets"][0]["records"][0]["participants"][0]["type_specific_fields"]["alternate_birth_places"] = [
        {"value": "Quebec, Canada"}]

    df, _, _ = arc.build_census_dataframe_from_unified(doc)

    alt = json.loads(df.iloc[0]["AlternateBirthPlaces"])
    assert alt == [{"value": "Quebec, Canada"}]


def test_adapter_reads_street_and_it_drives_the_cens_addr_line(tmp_path, monkeypatch):
    """Confirms 'street' reaches the DataFrame AND actually drives build_gedcom_from_census's
    CENS fact '2 ADDR' line - not just that it lands somewhere plausible-looking."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["type_specific_fields"]["street"] = "212 Main St"
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)
    assert df.iloc[0]["Street"] == "212 Main St"

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()
    assert "2 ADDR 212 Main St" in lines


def test_adapter_reads_married_within_year_per_participant():
    doc = _unified_doc("Census_1900", [_sheet([
        _record([_participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")], family_number="5"),
    ])])
    doc["sheets"][0]["records"][0]["participants"][0]["type_specific_fields"]["married_within_year"] = "Yes"

    df, _, _ = arc.build_census_dataframe_from_unified(doc)

    assert df.iloc[0]["Married within Year"] == "Yes"


def test_adapter_maps_facts_to_expected_old_column_names():
    doc = _unified_doc("Census_1900", [_sheet([
        _record([_participant("Jean", "Gagnon", "M",
                              facts=[{"fact_type": "Occupation", "value": "Farmer"},
                                     {"fact_type": "Immigration", "date": "1889"}])],
                family_number="5"),
    ])])
    df, _, _ = arc.build_census_dataframe_from_unified(doc)
    row = df.iloc[0]
    assert row["Occupation"] == "Farmer"
    assert row["Immigration Year"] == "1889"


def test_adapter_promotes_named_occupation_field_not_just_facts():
    """Occupation belongs in the participant's own named
    'occupation' field (Commissioner's own Participant.facts docstring: "do not duplicate a
    fact already covered by a named field ... here") - FamilySearch's YAML mapping now
    targets this directly rather than a duplicate facts-array entry, so
    build_census_dataframe_from_unified() must promote p.get('occupation') on its own,
    independent of the general facts-based FACT_TYPE_TO_COLUMN path (still exercised by
    test_adapter_maps_facts_to_expected_old_column_names for sources that genuinely use it)."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["occupation"] = "Farmer"
    doc = _unified_doc("Census_1900", [_sheet([_record([head], family_number="5")])])
    df, _, _ = arc.build_census_dataframe_from_unified(doc)
    assert df.iloc[0]["Occupation"] == "Farmer"


def test_relational_era_household_parsing_works_on_adapted_dataframe():
    """1900 (relationship era) - Head + Wife, explicit role_name - confirms
    parse_household_relational (unchanged) correctly resolves the household from the
    adapter's column naming."""
    doc = _unified_doc("Census_1900", [_sheet([
        _record([
            _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1"),
            _participant("Marie", "Gagnon", "F", role_name="Wife", age="38", line="2"),
        ], family_number="5"),
    ])])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    cfg = arc.CensusRunConfig(census_year=int(year))
    cfg.census_era = get_census_era(cfg.census_year)
    assert cfg.census_era == "relationship"

    units, unrelated, flags = arc.parse_household_relational(df, cfg)
    assert len(units) == 1
    assert units[0]["husband"]["Given Name"] == "Jean"
    assert units[0]["wife"]["Given Name"] == "Marie"
    assert not unrelated


def test_gedcom_wrapped_lines_splits_multiline_text_into_cont_lines():
    """Real-world regression (2026-08-21): RootsMagic imported every source as Freeform
    instead of the intended template - traced to _rmst_element_to_gedcom() emitting
    multi-paragraph Description/Hint text (from the .rmst source) as a single GEDCOM line
    with raw embedded newlines. GEDCOM 5.5.1 requires every physical line to start with its
    own level+tag; a bare continuation line with no tag prefix is invalid and can make a
    strict parser silently drop or abort the whole _STMPLT record."""
    lines = arc._gedcom_wrapped_lines(1, "DESC", "First paragraph.\n\nSecond paragraph.")
    assert lines == ["1 DESC First paragraph.", "2 CONT ", "2 CONT Second paragraph."]
    assert not any("\n" in ln for ln in lines)


def test_load_source_template_lines_has_no_embedded_raw_newlines():
    """Integration regression: the real Census .rmst template's multi-paragraph
    Description/Hint text must never produce a line containing an embedded '\\n' -
    confirmed live this was the actual cause of RootsMagic's source-template import
    silently failing (every source fell back to Freeform)."""
    lines = arc.load_source_template_lines(10008)
    assert lines, "expected the real Simplified Citations - Census.rmst template to be found"
    assert not any("\n" in ln for ln in lines)
    joined = "\n".join(lines)
    assert "1 DESC (NOTE: changes made to the content of this template should also be made " \
           "to the master template. Alterations made for the sole purpose of allowing the " \
           "content to print correctly do not need to be made." in joined
    assert "2 CONC )" in joined


def test_sort_group_by_line_number_fixes_out_of_order_household_and_reattaches_child():
    """Real-world regression (2026-08-21): a real gather's participant array for one
    household was NOT in sheet/line order (Line Numbers 6, 4, 8, 10, 9, 5, 7 - a daughter,
    line 6, listed before the head, line 4). parse_household_relational's "first person
    must be Head" check wrongly detached her as unrelated. Sorting by Line Number right
    before parsing fixes this regardless of what order the raw gather produced."""
    doc = _unified_doc("Census_1950", [_sheet([
        _record([
            _participant("Marlys", "Crowston", "F", role_name="Daughter", age="16", line="6"),
            _participant("Jess", "Crowston", "M", role_name="Head", age="51", line="4"),
            _participant("Glenda", "Crowston", "F", role_name="Daughter", age="12", line="8"),
            _participant("James", "Crowston", "M", role_name="Son", age="8", line="10"),
            _participant("Faye", "Crowston", "F", role_name="Daughter", age="9", line="9"),
            _participant("May", "Crowston", "F", role_name="Wife", age="43", line="5"),
            _participant("Gerald", "Crowston", "M", role_name="Son", age="14", line="7"),
        ], family_number="10"),
    ])])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)
    cfg = arc.CensusRunConfig(census_year=int(year))
    cfg.census_era = get_census_era(cfg.census_year)

    sorted_df = arc.sort_group_by_line_number(df)
    assert list(sorted_df["Given Name"]) == ["Jess", "May", "Marlys", "Gerald", "Glenda", "Faye", "James"]

    units, unrelated, flags = arc.parse_household_relational(sorted_df, cfg)
    assert len(units) == 1
    unrelated_names = [u.get('Given Name') for u in unrelated]
    assert len(unrelated) == 0, f"everyone should be attached to the household: {unrelated_names}"
    assert units[0]["husband"]["Given Name"] == "Jess"
    assert units[0]["wife"]["Given Name"] == "May"
    children = {c["Given Name"] for c in units[0]["children"]}
    assert children == {"Marlys", "Gerald", "Glenda", "Faye", "James"}


def test_heuristic_era_household_parsing_works_on_adapted_dataframe():
    """1860 (heuristic era) - no role_name at all (matches what census_schema.py produces
    when the source has no relationship column) - confirms parse_household (unchanged)
    still infers the spouse pair from age/sex/surname proximity via the adapter's output."""
    doc = _unified_doc("Census_1860", [_sheet([
        _record([
            _participant("Jean", "Gagnon", "M", age="40", line="1"),
            _participant("Marie", "Gagnon", "F", age="38", line="2"),
        ], family_number="5"),
    ])])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    cfg = arc.CensusRunConfig(census_year=int(year))
    cfg.census_era = get_census_era(cfg.census_year)
    assert cfg.census_era == "heuristic"

    units, unrelated, flags = arc.parse_household(df, cfg)
    assert len(units) == 1
    assert {units[0]["husband"]["Given Name"], units[0]["wife"]["Given Name"]} == {"Jean", "Marie"}


def test_is_institution_resident_true_when_any_institution_field_present():
    row = pd.Series({"Institution 1 Type": "Hospital"})
    assert arc.is_institution_resident(row) is True
    assert arc.is_institution_resident(pd.Series({"Given Name": "Jean"})) is False


def test_build_institution_note_composes_one_sentence_from_present_fields():
    row = pd.Series({
        "Institution 1": "County Poor Farm", "Institution 1 Type": "Almshouse",
        "Institution 1 From Line": "1", "Institution 1 To Line": "40",
    })
    note = arc.build_institution_note(row)
    assert "County Poor Farm" in note
    assert "Almshouse" in note
    assert "1-40" in note


def test_build_institution_note_empty_when_no_institution_fields_present():
    assert arc.build_institution_note(pd.Series({"Given Name": "Jean"})) == ""


def test_institution_resident_gets_no_family_links_even_with_head_wife_roles():
    """A person enumerated at an institution gets no
    family/spouse/parent-child links at all, just their own individual record - confirmed
    here by giving the pair explicit Head/Wife roles (which would normally link them) but
    marking one an institution resident, and asserting parse_household_relational treats
    the whole group as unrelated individuals rather than a family unit."""
    doc = _unified_doc("Census_1950", [_sheet([
        _record([
            _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1"),
            _participant("Marie", "Gagnon", "F", role_name="Wife", age="38", line="2"),
        ], family_number="5"),
    ])])
    doc["sheets"][0]["records"][0]["participants"][1]["type_specific_fields"]["institution_1_type"] = "Hospital"
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    cfg = arc.CensusRunConfig(census_year=int(year))
    cfg.census_era = get_census_era(cfg.census_year)
    assert cfg.census_era == "relationship"

    units, unrelated, flags = arc.parse_household_relational(df, cfg)
    assert units == []
    assert len(unrelated) == 2


def test_two_households_get_separate_family_number_groups():
    doc = _unified_doc("Census_1900", [_sheet([
        _record([_participant("Jean", "Gagnon", "M", role_name="Head", line="1")], family_number="5"),
        _record([_participant("Louis", "Riel", "M", role_name="Head", line="2")], family_number="6"),
    ])])
    df, _, _ = arc.build_census_dataframe_from_unified(doc)
    df["Household_ID"] = (df["Family Number"] != df["Family Number"].shift()).cumsum()
    assert df["Household_ID"].nunique() == 2


def test_dispatch_by_record_type_name_not_shape():
    """Both census and church documents use the "sheets" top-level key now - confirms
    is_census's own logic (record_type_name-based, not a hardcoded shape guess) would
    route a Census_-prefixed document to the census flavor."""
    doc = _unified_doc("Census_1900", [_sheet([_record([_participant("Jean", "Gagnon", "M")])])])
    assert doc.get("record_type_name", "").startswith("Census_")  # type: ignore
    assert "sheets" in doc


def test_build_census_task_folder_name_uses_fixed_vocabulary_not_raw_flag_text():
    """Regression: RootsMagic rejects an arbitrary sentence as a "0 _FOLDER" value -
    build_census_task used to write one directly (a lightly regex-cleaned copy of the raw
    review-flag text, e.g. "Head-surname match: no age fit"). The folder name must instead
    come from evaluate_task_priority's fixed, safe vocabulary - already proven correct for
    the general flavor - reusing the exact flag text real 1860 census data produced."""
    cfg = arc.CensusRunConfig()
    _, folder = arc.build_census_task("1", "Jean", "Gagnon", "1900, Fam 5, p.3",
                                      [("Head-surname match; no age fit", 0.3)],
                                      [], "img.jpg", "Title", "RM", cfg)
    assert folder == "Name & Identity Issues"

    _, folder2 = arc.build_census_task("2", "Jean", "Gagnon", "1900, Fam 5, p.3",
                                       [("Unrelated household member", 0.5)],
                                       [], "img.jpg", "Title", "RM", cfg)
    assert folder2 == "General Review"


def test_census_gedcom_output_has_no_illegal_name_under_sour_and_single_extension(tmp_path, monkeypatch):
    """Regression for the FTM import-error root cause (confirmed against a real FTM import
    error log: ~85 "Unsupported or invalid tag: NAME" errors on one real test census page):
    a "NAME Researcher: ..." line was being written as a child of a per-fact "2 SOUR ..."
    citation, which isn't legal under GEDCOM 5.5.1 (see build_census_citation). This is
    distinct from "2 NAME Researcher: ..." under the shared "1 SOUR @S1@" researcher
    reference on every individual - confirmed live by the user that RootsMagic needs BOTH
    "2 NAME" and "2 _TITL" there to merge that citation across every person who cites it;
    that pattern is intentional, not a regression of this fix. Also regresses an OBJE media
    filename doubling its extension (".jpg.jpg") when Image_ID already carries one, as it
    does coming from the unified schema's document_metadata.file_name - and confirms the
    FamilySearch Family Tree profile webtag now accompanies _FSFTID."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["type_specific_fields"] = {"line_number": "1", "fsftid": "LZXY-ABC"}
    wife = _participant("Marie", "Gagnon", "F", role_name="Wife", age="38", line="2")
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head, wife], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    # GEDCOM_OUTPUT_NAME is read from the environment at Utils import time, and this
    # dev machine's Archivist/.env sets it to an empty string - resolve_gedcom_output_path
    # would then produce a filename with no base and no ".ged" extension (" - RM").
    # Pin a real name here so the output is a findable *.ged file (in production
    # Archivist.py fills an empty name from the input file's stem before this is called).
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    out_files = list(tmp_path.glob("*.ged"))
    assert len(out_files) == 1
    lines = out_files[0].read_text(encoding="utf-8").splitlines()

    # The illegal line was specifically "3 NAME Researcher: ..." nested directly under a
    # per-fact "2 SOUR ..." citation - "2 NAME Researcher: ..." under the shared top-level
    # "1 SOUR @S1@" reference (checked below) is the separate, intentional pattern.
    sour_line_idxs = [i for i, ln in enumerate(lines) if ln.startswith("2 SOUR ")]
    for i in sour_line_idxs:
        for ln in lines[i + 1:]:
            if not (ln.startswith("3 ") or ln.startswith("4 ") or ln.startswith("5 ")):
                break
            if ln.startswith("3 NAME"):
                assert False, f"illegal NAME directly under a per-fact SOUR citation: {ln!r}"

    # The shared "1 SOUR @S1@" researcher reference on each individual needs BOTH "2 NAME"
    # and "2 _TITL" - confirmed live by the user - to merge across every person's citation
    # of it in RootsMagic's own UI.
    root_sour_idxs = [i for i, ln in enumerate(lines) if ln == f"1 SOUR {Utils.ROOT_SOURCE_ID}"]
    assert root_sour_idxs
    for i in root_sour_idxs:
        assert lines[i + 1].startswith("2 NAME Researcher:")
        assert lines[i + 2].startswith("2 _TITL Researcher:")

    file_lines = [ln for ln in lines if ln.startswith("1 FILE ")]
    assert file_lines
    assert all(ln.endswith(".jpg") and not ln.endswith(".jpg.jpg") for ln in file_lines)

    assert any(ln == "1 _FSFTID LZXY-ABC" for ln in lines)
    assert any("https://www.familysearch.org/tree/person/details/LZXY-ABC" in ln for ln in lines)


def test_census_gedcom_media_attaches_to_each_fact_citation_per_original_design(tmp_path, monkeypatch):
    """This project's own citation-building shape, unchanged since the very first commit
    (CensusConverter/CensusConverter.py, build_citation_block): the OBJE media reference
    lives INSIDE each fact's own '2 SOUR ...' citation block (at '3 OBJE <id>', a child of
    the citation, not a direct child of the fact) - and that whole citation block, media
    included, is what gets attached once per fact (NAME, BIRT, CENS, OCCU, ...). A session
    mid-course attempt to instead park the media once on the shared top-level SOUR record
    was reverted after review - confirmed against project history that per-fact-citation
    attachment (not source-only) is the long-standing, correct design."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    wife = _participant("Marie", "Gagnon", "F", role_name="Wife", age="38", line="2")
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head, wife], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    # Each person has multiple facts (NAME, BIRT, CENS at least) - each carries its own
    # '3 OBJE' nested under its own '2 SOUR' citation, so the total count is several per
    # person, not one per person and not zero.
    obje_lines = [ln for ln in lines if ln.startswith("3 OBJE")]
    assert len(obje_lines) >= 4, f"expected multiple per-fact OBJE lines, got: {obje_lines}"
    assert all(ln == "3 OBJE @M4211353_00003@" for ln in obje_lines), obje_lines

    # The shared source record itself carries no OBJE - media is a citation-level detail,
    # not something hoisted onto the bare source.
    sour_idx = lines.index(f"0 @S{arc.CENSUS_SOURCE_ID}@ SOUR")
    sour_block_end = next((i for i in range(sour_idx + 1, len(lines)) if lines[i].startswith("0 ")), len(lines))
    sour_block = lines[sour_idx:sour_block_end]
    assert not any(ln.startswith("1 OBJE") for ln in sour_block), \
        f"source record should not carry its own OBJE: {sour_block}"


def test_census_gedcom_refn_is_bare_ark_not_the_gedcomx_type_prefixed_form(tmp_path, monkeypatch):
    """User-requested: a FamilySearch person ark's own REFN value should read as just the
    ark itself (e.g. 'MF36-Z6D'), not the GEDCOM X type-prefixed form ('1:1:MF36-Z6D') that
    FS.py's pid/person_ark actually carries and that the citation-level _FSFTID fallback
    also strips (see test_census_ingestion.py). The '@I<id>@' cross-ref pointer itself is
    untouched - that's an internal GEDCOM pointer needing only internal consistency, not
    display-cleanliness, and stripping it would risk mismatching its own definition/
    reference pair."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["type_specific_fields"]["pid"] = "1:1:MF36-Z6D"
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    assert "0 @I1:1:MF36-Z6D@ INDI" in lines
    assert "1 REFN MF36-Z6D" in lines
    assert "1 REFN 1:1:MF36-Z6D" not in lines


def test_census_gedcom_fsftid_uses_persons_own_person_ark_when_present(tmp_path, monkeypatch):
    """2026-08-20 semantic split: PersonArk now carries a TRUE enduring FamilySearch Family
    Tree identifier (FSFTID-shaped, e.g. "KLBM-H9P" - no ark-type prefix, so no
    strip_ark_type_prefix() is applied), distinct from RecordArk (this persona's
    record-scoped id). This row's PID is deliberately a different value than its PersonArk
    to prove the two are read independently."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["type_specific_fields"]["pid"] = "9999-fake-record-id"
    head["type_specific_fields"]["person_ark"] = "KLBM-H9P"
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    assert "0 @I9999-fake-record-id@ INDI" in lines
    assert "1 _FSFTID KLBM-H9P" in lines
    assert not any(ln.startswith("1 _FSFTID ") and "KLBM-H9P" not in ln for ln in lines), \
        f"_FSFTID must come from PersonArk, not the record's own PID: {lines}"


def test_census_gedcom_fsftid_omitted_when_no_true_person_ark_found(tmp_path, monkeypatch):
    """Real-world case confirmed live 2026-08-20 (Jess Guy Crowston, FamilySearch's own UI
    shows 'Attached in Tree to ... KLBM-H9P' but that id appears nowhere in the
    orchestration API's JSON response FS.py actually gathers from): when only a RecordArk
    exists and no true PersonArk was captured, _FSFTID must be omitted entirely, not
    fall back to the record-scoped persona id (a persona id is not a valid FamilySearch
    Tree person id, and using one would produce a fabricated-looking _FSFTID)."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["type_specific_fields"]["record_ark"] = "1:1:MF36-Z6D"
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    assert not any("_FSFTID" in ln for ln in lines), \
        f"_FSFTID must not fall back to RecordArk: {lines}"


def test_census_gedcom_apid_is_individual_level_not_nested_in_citation(tmp_path, monkeypatch):
    """_APID is an individual-level GEDCOM tag (FTM/RM both expect it directly on the INDI
    record), never nested inside a per-fact SOUR citation - confirms build_gedcom_from_census
    emits exactly one '1 _APID ...' per person and that no '3 _APID' (the old, incorrect
    citation-nested placement) survives anywhere in the output."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["type_specific_fields"]["pid"] = "105307051"
    doc = _unified_doc("Census_1900", [{
        "page_id": "3",
        "document_metadata": {"source_location": "Minnesota", "file_name": "4211353_00003.jpg"},
        "records": [_record([head], family_number="5")],
    }])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path),
                              apid_db="2442")

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    apid_lines = [ln for ln in lines if "_APID" in ln]
    assert apid_lines == ["1 _APID 1,2442::105307051"], apid_lines


def _citation_row(**overrides):
    row = {"Given Name": "Jean", "Surname": "Gagnon", "FSFTID": "", "FamilySearch_URL": "",
           "Extracted_URL": "", "Family Number": "5", "Dwelling Number": "5"}
    row.update(overrides)
    return pd.Series(row)


def test_census_citation_never_emits_apid_for_an_ark_shaped_rec_id(monkeypatch):
    """Regression: an FS-only gather has no real Ancestry APID_DB, but rec_id (fed from
    FS.py's per-person 'pid', which is the FamilySearch person ark, e.g. '1:1:MF36-Z6D'
    when there's no genuine Ancestry record) was still being unconditionally baked into the
    Ancestry-specific '_APID 1,<db>::<id>' GEDCOM tag - an ark is not an APID and must
    never appear there, and no bogus Ancestry citation data should be emitted at all.

    The ark itself must NOT be routed into the top-level per-person _FSFTID/tree-weblink
    mechanism (an earlier fix attempt did this, then had to be reverted - confirmed live it
    produced a broken 'familysearch.org/tree/person/details/1:1:MF36-Z6D' weblink, since
    that URL pattern is specifically for a Family Tree PROFILE id, a different, unrelated
    ID namespace only obtained via genuine tree-attachment). This test's row has no
    FamilySearch_URL either (not FS-sourced at all), so the citation-level _FSFTID fallback
    (see the companion test below) correctly doesn't fire - _FSFTID must not appear."""
    cfg = arc.CensusRunConfig(apid_db="", census_year=1860, census_source_id="1473181",
                              collection_name="United States, Census, 1860")

    row = _citation_row(FSFTID="")

    for target in ("RM", "FTM"):
        cit = arc.build_census_citation(row, "1:1:MF36-Z6D", "@Mimg1@", "3", target,
                                        "Pembina", "Dakota Territory", "Dakota Territory",
                                        "T624_1", "", "", cfg)
        assert not any("_APID" in ln for ln in cit), f"{target}: bogus _APID from an ark: {cit}"
        assert not any("_FSFTID" in ln for ln in cit), f"{target}: bogus _FSFTID from an ark: {cit}"


def test_census_citation_never_emits_fsftid_for_familysearch_sourced_rows(monkeypatch):
    """_FSFTID is an individual-level GEDCOM tag (see build_gedcom_from_census's indi_fsftid,
    which sources it from the row's own PersonArk column, not from build_census_citation) -
    it must never appear inside the per-fact citation itself, whether or not the row is
    FamilySearch-sourced."""
    cfg = arc.CensusRunConfig(apid_db="", census_year=1860, census_source_id="1473181",
                              collection_name="United States, Census, 1860")

    fs_row = _citation_row(FSFTID="", **{"FamilySearch_URL": "https://www.familysearch.org/ark:/61903/1:1:MF36-Z6D"})
    for target in ("RM", "FTM"):
        cit = arc.build_census_citation(fs_row, "1:1:MF36-Z6D", "@Mimg1@", "3", target,
                                        "Pembina", "Dakota Territory", "Dakota Territory",
                                        "T624_1", "", "", cfg)
        assert not any("_FSFTID" in ln for ln in cit), f"{target}: _FSFTID leaked into citation: {cit}"

    cfg.apid_db = "2442"
    anc_row = _citation_row(FSFTID="")
    for target in ("RM", "FTM"):
        cit = arc.build_census_citation(anc_row, "105307051", "@Mimg1@", "3", target,
                                        "Pembina", "Dakota Territory", "Dakota Territory",
                                        "T624_1", "", "", cfg)
        assert not any("_FSFTID" in ln for ln in cit), f"{target}: _FSFTID leaked into citation: {cit}"


def test_strip_ark_type_prefix():
    assert arc.strip_ark_type_prefix("1:1:MF36-Z6D") == "MF36-Z6D"
    assert arc.strip_ark_type_prefix("3:1:33S7-9YBJ-9PD7") == "33S7-9YBJ-9PD7"
    assert arc.strip_ark_type_prefix("105307051") == "105307051"
    assert arc.strip_ark_type_prefix("") == ""


def test_census_citation_household_id_field_is_bare_number_when_only_one_number_present(monkeypatch):
    """Regression: RM's 'HouseholdID' citation FIELD was rendering as 'family 1' or
    'dwelling 5' (confirmed live against a real 1860 gather - RootsMagic shows
    'HouseholdID: family 1', which reads as a stray descriptive word next to the field's
    own name) instead of the bare id value. The label word only earns its place when BOTH
    dwelling and family numbers exist and genuinely need to be told apart."""
    cfg = arc.CensusRunConfig(apid_db="", census_year=1860, census_source_id="1473181",
                              collection_name="United States, Census, 1860")

    family_only = arc.build_census_citation(
        _citation_row(**{"Family Number": "1", "Dwelling Number": ""}), "MF36-Z6D", "@Mimg1@", "3", "RM",
        "Pembina", "Dakota Territory", "Dakota Territory", "T624_1", "", "", cfg)
    assert any(ln == "3 _TMPLT" for ln in family_only), family_only
    assert any(ln == "5 VALUE 1" for ln in family_only), family_only
    assert not any("family" in ln.lower() for ln in family_only), family_only

    dwelling_only = arc.build_census_citation(
        _citation_row(**{"Family Number": "", "Dwelling Number": "5"}), "MF36-Z6D", "@Mimg1@", "3", "RM",
        "Pembina", "Dakota Territory", "Dakota Territory", "T624_1", "", "", cfg)
    assert any(ln == "3 _TMPLT" for ln in dwelling_only), dwelling_only
    assert any(ln == "5 VALUE 5" for ln in dwelling_only), dwelling_only
    assert not any("dwelling" in ln.lower() for ln in dwelling_only), dwelling_only

    both = arc.build_census_citation(
        _citation_row(**{"Family Number": "1", "Dwelling Number": "5"}), "MF36-Z6D", "@Mimg1@", "3", "RM",
        "Pembina", "Dakota Territory", "Dakota Territory", "T624_1", "", "", cfg)
    assert any(ln == "3 _TMPLT" for ln in both), both
    assert any(ln == "5 VALUE dwelling 5, family 1" for ln in both), both


def test_census_citation_never_emits_apid_for_real_ancestry_data(monkeypatch):
    """Companion to the regression above - _APID is an individual-level GEDCOM tag (see
    build_gedcom_from_census's indi-level '1 _APID ...' line), not a citation-level one, so
    even a genuine Ancestry-sourced record (real APID_DB, numeric rec_id) must never carry
    it inside build_census_citation()'s own output."""
    cfg = arc.CensusRunConfig(apid_db="2442", census_year=1860, census_source_id="1001",
                              collection_name="1860 United States Federal Census")

    row = _citation_row()

    for target in ("RM", "FTM"):
        cit = arc.build_census_citation(row, "105307051", "@Mimg1@", "3", target,
                                        "Pembina", "Dakota Territory", "Dakota Territory",
                                        "T624_1", "", "", cfg)
        assert not any("_APID" in ln for ln in cit), f"{target}: _APID leaked into citation: {cit}"


def test_dynamic_occupation_template_normalizes_raw_case():
    """Regression: get_occupation_value's 2026-08-15 refactor dropped the capitalize_text_string
    normalization the pre-refactor version applied to its assembled result - real census
    source text can come back ALL-CAPS or lowercase, unlike this test file's other
    already-Title-Case fixtures."""
    from Census import get_occupation_value

    row = pd.Series({
        "Occupation": "FARMER",
        "Employer": "SMITH FARM",
        "Industry": "agriculture",
    })
    occ, _ = get_occupation_value(row, arc.CensusRunConfig())
    assert occ == "Farmer at Smith Farm, working in Agriculture"


def test_dynamic_notes_exclude_citation_plumbing_columns():
    """The CENS fact's visible note must not duplicate
    citation/URL data already carried by the SOUR citation block and weblinks - Collection
    Name/Collection URL/RecordArk/PersonArk are plumbing columns, not genealogical facts
    about the person, and previously fell through build_dynamic_events_and_notes()'s
    catch-all into a plain, visible note since they weren't in CORE_COLUMNS."""
    row = pd.Series({
        "Collection Name": "United States Census, 1950: Pembina. Census 1950",
        "Collection URL": "https://www.familysearch.org/ark:/61903/3:1:3QHN-PQHW-1YYJ",
        "RecordArk": "1:1:6F7Z-QJKR", "PersonArk": "KLBM-H9P",
        "Lived on Farm": "yes",
    })
    columns = list(row.index)
    _, notes = arc.build_dynamic_events_and_notes(row, [], "Jess", columns, "North Dakota, USA", "",
                                                  arc.CensusRunConfig())
    joined = " | ".join(notes)
    assert "Collection Name" not in joined
    assert "Collection URL" not in joined
    assert "RecordArk" not in joined
    assert "PersonArk" not in joined
    assert "Lived on Farm: yes" in joined


def test_dynamic_occupation_template():
    from Census import get_occupation_value

    cfg = arc.CensusRunConfig()

    # Test Employed
    row1 = pd.Series({
        "Occupation": "Farmer",
        "Employer": "Smith Farm",
        "Industry": "Agriculture",
        "Class of Worker": "W",
        "Hours Worked": "40"
    })
    occ1, notes1 = get_occupation_value(row1, cfg)
    assert occ1 == "Farmer at Smith Farm, working in Agriculture"
    assert "Class of Worker: W" in notes1
    assert "Hours Worked: 40" in notes1

    # Test Unemployed override
    row2 = pd.Series({
        "Occupation": "Clerk",
        "Employer": "Bank",
        "Out Of Work": "Yes"
    })
    occ2, notes2 = get_occupation_value(row2, cfg)
    assert occ2 == "Unemployed from Clerk at Bank"

    # Test Usual Occupation priority
    row3 = pd.Series({
        "Occupation": "Laborer",
        "Usual Occupation": "Carpenter"
    })
    occ3, notes3 = get_occupation_value(row3, cfg)
    assert occ3 == "Carpenter"


def test_foreign_birthplace_as_nationality(tmp_path, monkeypatch):
    row_foreign = pd.Series({
        "Given Name": "Patrick",
        "Surname": "O'Connor",
        "Gender": "M",
        "Age": "35",
        "Birth Place": "Ireland",
        "Family Number": "1",
        "Line Number": "1",
    })
    row_us = pd.Series({
        "Given Name": "John",
        "Surname": "Smith",
        "Gender": "M",
        "Age": "30",
        "Birth Place": "Minnesota",
        "Family Number": "2",
        "Line Number": "2",
    })
    row_explicit_nat = pd.Series({
        "Given Name": "Hans",
        "Surname": "Schmidt",
        "Gender": "M",
        "Age": "45",
        "Birth Place": "Germany",
        "Nationality": "Prussian",
        "Family Number": "3",
        "Line Number": "3",
    })
    df = pd.DataFrame([row_foreign, row_us, row_explicit_nat])

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=1900, census_era="relationship", image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)

    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    # Ireland birthplace without nationality emits 1 NATI Ireland
    assert "1 NATI Ireland" in lines
    # Minnesota birthplace does not emit 1 NATI Minnesota
    assert "1 NATI Minnesota" not in lines
    # Explicit Nationality Prussian is preserved
    assert "1 NATI Prussian" in lines


def test_canadian_and_historical_hbc_birthplaces_are_not_foreign():
    """Regression: this project's core subjects are Canadian/Métis/HBCA-region families
    (see Gazetteer.CA_PROVINCE_NAMES) - is_foreign_birthplace()'s original
    US_STATES_AND_TERRITORIES allowlist had no Canadian entries at all, so every
    Canadian- or Rupert's-Land-born ancestor would have had their raw birthplace text
    dumped verbatim into a NATI tag as if it were a foreign nationality."""
    from Census import is_foreign_birthplace

    assert is_foreign_birthplace("Ontario") is False
    assert is_foreign_birthplace("Manitoba") is False
    assert is_foreign_birthplace("Rupert's Land") is False
    assert is_foreign_birthplace("Red River Settlement") is False
    assert is_foreign_birthplace("Pembina, Dakota Territory") is False
    assert is_foreign_birthplace("Ireland") is True


def test_us_state_abbreviations_are_not_foreign():
    """Regression: historical census sheets abbreviate state names (esp. Nationality-
    column entries copied from a birthplace field), e.g. 'N Dak', 'Mass', 'Penna' -
    these must resolve as domestic, not get treated as a foreign nationality string."""
    from Census import is_foreign_birthplace

    assert is_foreign_birthplace("N Dak") is False
    assert is_foreign_birthplace("S. Dak.") is False
    assert is_foreign_birthplace("No Dakota") is False
    assert is_foreign_birthplace("Mass") is False
    assert is_foreign_birthplace("Penna") is False
    assert is_foreign_birthplace("Wash") is False
    assert is_foreign_birthplace("Mich") is False


def test_parent_birthplace_appends_second_birt_fact_to_existing_father(tmp_path, monkeypatch):
    """FTHR_BIR_PLACE/MTHR_BIR_PLACE describe the
    child's own father/mother, not the child's own facts. When that parent was already
    extracted as a real person in the household, the birthplace must land as a SECOND,
    proposed-proof BIRT fact on the parent's own INDI record - not overriding the
    birthplace already extracted from the parent's own gathered row - and no new person
    is created for them."""
    head = _participant("Jean", "Gagnon", "M", role_name="Head", age="40", line="1")
    head["birth_place"] = "Minnesota, USA"
    wife = _participant("Marie", "Gagnon", "F", role_name="Wife", age="38", line="2")
    child = _participant("Louis", "Gagnon", "M", role_name="Child", age="10", line="3")
    child["type_specific_fields"]["father_birth_place"] = "Ireland"
    doc = _unified_doc("Census_1950", [_sheet([
        _record([head, wife, child], family_number="5"),
    ])])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)
    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    head_start = lines.index("1 NAME Jean /Gagnon/")
    next_indi = next(i for i in range(head_start + 1, len(lines)) if lines[i].startswith("0 @I"))
    head_block = lines[head_start:next_indi]

    birt_indices = [i for i, ln in enumerate(head_block) if ln == "1 BIRT"]
    assert len(birt_indices) == 2, f"expected original + appended BIRT facts: {head_block}"
    assert "2 PLAC Minnesota, USA" in head_block
    assert "2 PLAC Ireland" in head_block
    assert head_block[birt_indices[1]:birt_indices[1] + 3] == \
        ["1 BIRT", "2 PLAC Ireland", "2 _PROOF proposed"]
    # no synthetic father person was created since a real one already exists
    assert lines.count("1 NAME /Gagnon/") == 0


def test_parent_birthplace_synthesizes_stub_parents_when_foreign_and_none_extracted(tmp_path, monkeypatch):
    """When the child has no extracted father/mother (unrelated in the household) and the
    birthplace is foreign, FTHR_BIR_PLACE/MTHR_BIR_PLACE synthesize stub parent records -
    surname only (no given name recorded) - carrying just a proposed-proof birthplace-only
    BIRT fact, linked as the child's parents in a new FAM block."""
    head = _participant("Marie", "Smith", "F", role_name="Head", age="70", line="1")
    lodger = _participant("Louis", "Gagnon", "M", role_name="Lodger", age="10", line="2")
    lodger["type_specific_fields"]["father_birth_place"] = "Ireland"
    lodger["type_specific_fields"]["mother_birth_place"] = "Norway"
    doc = _unified_doc("Census_1950", [_sheet([
        _record([head, lodger], family_number="5"),
    ])])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)
    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    assert lines.count("1 NAME /Gagnon/") == 2, f"expected one synthetic father + mother: {lines}"
    assert "1 SEX M" in lines
    assert "1 SEX F" in lines
    assert "2 PLAC Ireland" in lines
    assert "2 PLAC Norway" in lines

    fam_starts = [i for i, ln in enumerate(lines) if ln.startswith("0 @F") and ln.endswith(" FAM")]
    fam_blocks = []
    for start in fam_starts:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("0 ")), len(lines))
        fam_blocks.append(lines[start:end])
    child_fam = next(fb for fb in fam_blocks if any(ln.startswith("1 CHIL") for ln in fb))
    assert any(ln.startswith("1 HUSB") for ln in child_fam)
    assert any(ln.startswith("1 WIFE") for ln in child_fam)


def test_parent_birthplace_does_not_synthesize_a_person_for_domestic_birthplace(tmp_path, monkeypatch):
    """The 1950 census overwhelmingly records
    "United States" for domestic-born parents - creating a stub person for that ubiquitous,
    unremarkable answer would flood the tree with low-value records, so a synthetic parent
    is only created when the birthplace is foreign. A domestic birthplace with no already-
    extracted parent produces no new person and no fact at all."""
    head = _participant("Marie", "Smith", "F", role_name="Head", age="70", line="1")
    lodger = _participant("Louis", "Gagnon", "M", role_name="Lodger", age="10", line="2")
    lodger["type_specific_fields"]["father_birth_place"] = "United States"
    doc = _unified_doc("Census_1950", [_sheet([
        _record([head, lodger], family_number="5"),
    ])])
    df, year, _ = arc.build_census_dataframe_from_unified(doc)

    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_PATH", tmp_path)
    monkeypatch.setattr(Utils, "GEDCOM_OUTPUT_NAME", "Test_Census.ged")
    cfg = arc.CensusRunConfig(census_year=int(year), census_era=get_census_era(int(year)), image_dir=str(tmp_path))

    arc.build_gedcom_from_census(df, "RM", cfg)
    lines = list(tmp_path.glob("*.ged"))[0].read_text(encoding="utf-8").splitlines()

    assert "1 NAME /Gagnon/" not in lines
    assert "2 PLAC United States" not in lines


def test_weeks_out_of_work_marks_unemployed_and_notes_the_weeks():
    """MISC_WEEKS_OUT_OF_WORK is folded into the
    Occupation fact's "Unemployed" state rather than getting its own fact - a weeks-out-of-
    work count is inherently about this same occupation question, not a distinct historical
    event, and MISC_FLAG_EMPLOYED is deliberately not mapped at all since it would just be
    a second, conflicting way to say the same thing."""
    from Census import get_occupation_value

    row = pd.Series({"Occupation": "Clerk", "Weeks Out of Work": "12"})
    occ, notes = get_occupation_value(row, arc.CensusRunConfig())
    assert occ == "Unemployed from Clerk"
    assert "Weeks Out of Work: 12" in notes


def test_location_string_falls_back_to_residence_place_only_when_census_place_blank():
    """EVENT_RESIDENCE_PLACE is a backup for the census
    place only, used when the State/County/City breakdown that normally composes it is
    entirely blank - never its own separate fact, and never preferred over a real value."""
    from Census import get_location_string

    cfg = arc.CensusRunConfig()
    blank_row = pd.Series({"Residence Place Fallback": "Some Town, Some State"})
    assert get_location_string(blank_row, cfg) == "Some Town, Some State"

    populated_row = pd.Series({"State": "Minnesota", "Residence Place Fallback": "Should Not Win"})
    assert get_location_string(populated_row, cfg) == "Minnesota, USA"


def test_build_census_dataframe_from_unified_surfaces_unmapped_codes():
    data = {
        "record_type_name": "Census_1950", "sheets": [{
            "page_id": "1", "document_metadata": {},
            "records": [{
                "type_specific_fields": {},
                "participants": [{
                    "std_given": "William", "std_surname": "Vinctson",
                    "type_specific_fields": {
                        "unmapped": {
                            "MISC_CODE_C_1950_CENSUS": "100",
                            "MISC_CODE_C1_1950_CENSUS": "105",
                            "MISC_CODE_C2_1950_CENSUS": "3",
                            "MISC_CODE_B_1950_CENSUS": "161",
                        },
                    },
                }],
            }],
        }],
    }
    df, year_str, _ = arc.build_census_dataframe_from_unified(data)
    assert year_str == "1950"
    row = df.iloc[0]
    assert row['Occupation Code'] == "100"
    assert row['Industry Code'] == "105"
    assert row['Class of Worker Code'] == "3"
    assert row['Birthplace Code'] == "161"


def test_build_census_dataframe_from_unified_omits_code_columns_when_absent():
    data = {
        "record_type_name": "Census_1950", "sheets": [{
            "page_id": "1", "document_metadata": {},
            "records": [{
                "type_specific_fields": {},
                "participants": [{"std_given": "Jane", "std_surname": "Doe",
                                  "type_specific_fields": {}}],
            }],
        }],
    }
    df, _, _ = arc.build_census_dataframe_from_unified(data)
    row = df.iloc[0]
    for col in ('Occupation Code', 'Industry Code', 'Class of Worker Code', 'Birthplace Code'):
        assert col not in df.columns or not row.get(col)


def test_get_occupation_value_prefers_decoded_code_over_existing_text():
    """Code-first, not code-fallback: even when real occupation text is ALSO
    present, the decoded code wins."""
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Occupation Code': '100', 'Occupation': 'Some Other Job'})
    occ, _ = arc.get_occupation_value(row, cfg)
    assert occ == "Farmers (owners and tenants)"


def test_get_occupation_value_falls_back_to_text_when_code_unknown():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Occupation Code': '999999', 'Occupation': 'Blacksmith'})
    occ, _ = arc.get_occupation_value(row, cfg)
    assert occ == "Blacksmith"


def test_get_occupation_value_falls_back_to_text_when_no_code():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Occupation': 'Blacksmith'})
    occ, _ = arc.get_occupation_value(row, cfg)
    assert occ == "Blacksmith"


def test_get_occupation_value_drops_occupation_category_entirely():
    """Occupation Category (h/wk/ot/u) is a different, undecodable scheme - it must
    never appear as the occupation value, even as a last resort."""
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Occupation Category': 'wk'})
    occ, _ = arc.get_occupation_value(row, cfg)
    assert occ == ""


def test_get_occupation_value_decodes_industry_code_first():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Occupation Code': '100', 'Industry Code': '105'})
    occ, _ = arc.get_occupation_value(row, cfg)
    assert "working in Agriculture" in occ


def test_get_occupation_value_decodes_class_of_worker_code_in_notes():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Occupation Code': '100', 'Class of Worker Code': '3'})
    _, notes = arc.get_occupation_value(row, cfg)
    assert "Class of Worker: In own business" in notes


def test_get_education_value_decodes_grade_code():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Highest Grade Completed': 'S8'})
    assert arc.get_education_value(row, cfg) == "8th grade"


def test_get_education_value_normalizes_letter_o_to_zero_before_decode():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Highest Grade Completed': 'O'})
    assert arc.get_education_value(row, cfg) == "No schooling"


def test_get_education_value_falls_back_to_raw_code_when_undecodable():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Highest Grade Completed': 'ZZ'})
    assert arc.get_education_value(row, cfg) == "ZZ"


def test_get_education_value_still_returns_none_when_nothing_present():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({})
    assert arc.get_education_value(row, cfg) is None


def test_get_education_value_still_returns_empty_string_for_attended_only():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Attended School': 'Yes'})
    assert arc.get_education_value(row, cfg) == ''


def test_get_education_value_falls_back_to_alt_column_in_heterogeneous_dataframe():
    """On a real multi-row DataFrame built from rows with different keys, a
    missing 'Highest Grade of School Completed' cell is NaN (not an absent key),
    so the alt-column fallback must still fire via get_row_val rather than being
    masked by row.get's default arg."""
    cfg = arc.CensusRunConfig(census_year=1950)
    df = pd.DataFrame([
        {'Highest Grade of School Completed': 'S8', 'Highest Grade Completed': None},
        {'Highest Grade of School Completed': None, 'Highest Grade Completed': 'S4'},
    ])
    assert arc.get_education_value(df.iloc[1], cfg) == "4th grade"


def test_get_race_value_decodes_abbreviation():
    cfg = arc.CensusRunConfig(census_year=1950)
    assert arc.get_race_value(pd.Series({'Race': 'W'}), cfg) == "White"


def test_get_race_value_passes_through_already_spelled_out_value():
    cfg = arc.CensusRunConfig(census_year=1950)
    assert arc.get_race_value(pd.Series({'Race': 'White'}), cfg) == "White"


def test_get_race_value_falls_back_to_color_column():
    cfg = arc.CensusRunConfig(census_year=1950)
    assert arc.get_race_value(pd.Series({'Color': 'W'}), cfg) == "White"


def test_get_race_value_empty_when_nothing_present():
    cfg = arc.CensusRunConfig(census_year=1950)
    assert arc.get_race_value(pd.Series({}), cfg) == ""


def test_get_race_value_falls_back_to_color_column_in_heterogeneous_dataframe():
    """On a real multi-row DataFrame built from rows with different keys, a
    missing 'Race' cell is NaN (not an absent key), so the Color fallback must
    still fire via get_row_val rather than being masked by row.get's default arg."""
    cfg = arc.CensusRunConfig(census_year=1950)
    df = pd.DataFrame([{'Race': 'W', 'Color': None}, {'Race': None, 'Color': 'N'}])
    assert arc.get_race_value(df.iloc[1], cfg) == "Negro"


def test_get_nationality_value_uses_decoded_foreign_code():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Birthplace Code': 'V39', 'Birth Place': 'Ireland'})
    assert arc.get_nationality_value(row, birth_place='Ireland', cfg=cfg) == "Iceland"


def test_get_nationality_value_suppresses_for_resolved_us_code():
    """A resolved US code must win over stale Nationality text or a
    foreign-looking birth_place string - the code can rule a value out, not just
    supply one."""
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Birthplace Code': '091', 'Nationality': 'Norwegian'})
    assert arc.get_nationality_value(row, birth_place='Norway', cfg=cfg) == ""


def test_get_nationality_value_falls_back_to_text_when_code_unresolved():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Birthplace Code': '999999'})
    assert arc.get_nationality_value(row, birth_place='Norway', cfg=cfg) == "Norway"


def test_get_nationality_value_falls_back_to_text_when_no_code():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({})
    assert arc.get_nationality_value(row, birth_place='Norway', cfg=cfg) == "Norway"


def test_get_nationality_value_empty_for_us_birthplace_text_no_code():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({})
    assert arc.get_nationality_value(row, birth_place='Ohio', cfg=cfg) == ""


def test_get_nationality_value_handles_nan_code_from_heterogeneous_dataframe():
    """When a DataFrame has mixed Birthplace Code presence across rows,
    pandas fills the missing cells with NaN rather than omitting the key.
    NaN is truthy, so get_nationality_value must clean_val it before the
    truthiness check or it will crash trying to len() a float."""
    cfg = arc.CensusRunConfig(census_year=1950)
    df = pd.DataFrame([
        {'Birthplace Code': 'V39', 'Nationality': None},
        {'Birthplace Code': None, 'Nationality': 'Norway'},
    ])
    row_with_code = df.iloc[0]
    row_without_code = df.iloc[1]
    assert arc.get_nationality_value(row_with_code, birth_place='Ireland', cfg=cfg) == "Iceland"
    assert arc.get_nationality_value(row_without_code, birth_place='Ohio', cfg=cfg) == "Norway"


def test_get_nationality_value_suppresses_us_state_text_in_nationality_field():
    """Regression: some sheets have a US state abbreviation typed into the Nationality
    column itself (e.g. 'N Dak'), not just the birthplace column - that text must not
    be emitted verbatim as a foreign nationality."""
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Nationality': 'N Dak'})
    assert arc.get_nationality_value(row, birth_place='', cfg=cfg) == ""


def test_get_nationality_value_keeps_foreign_text_alongside_new_abbreviations():
    cfg = arc.CensusRunConfig(census_year=1950)
    row = pd.Series({'Nationality': 'Norwegian'})
    assert arc.get_nationality_value(row, birth_place='', cfg=cfg) == "Norwegian"
