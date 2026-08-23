"""
Tests for Archivist's semantic-driven role/family-linking logic - the generalization
that replaced hardcoded role_number digit checks (role "2" is always "Father", role "4"
is always the marriage bride, ...) with a small fixed role_semantic vocabulary
(primary/spouse/child/father/mother/father_in_law/mother_in_law) read directly off each
participant, the same way for any record type.
"""

import os

# noinspection PyUnresolvedReferences
import Utils
# noinspection PyUnresolvedReferences
import Scrip
# noinspection PyUnresolvedReferences
import General
# noinspection PyUnresolvedReferences
import Census
import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_generate_uid_same_person_same_page_matches():
    """Baseline: identical record_id/role/page must always collide onto one UID."""
    rec = {"page": "page_001", "record_id": "SCRIP-5473", "participants": [{"role_number": "1"}]}
    part = rec["participants"][0]
    assert General.generate_uid(rec, part, "1") == General.generate_uid(dict(rec), part, "1")


def test_generate_uid_same_record_id_different_page_still_matches():
    """Regression: confirmed live, two JSON records for the same real person on different
    pages used to hash to different UIDs (page was part of the hash input) - two separate
    INDI blocks for one person. page must no longer affect the UID at all when record_id
    (or lac_pid) is the same."""
    part = {"role_number": "1"}
    rec_page_1 = {"page": "page_001", "record_id": "SCRIP-5473", "participants": [part]}
    rec_page_2 = {"page": "page_002", "record_id": "SCRIP-5473", "participants": [part]}

    assert General.generate_uid(rec_page_1, part, "1") == General.generate_uid(rec_page_2, part, "1")


def test_generate_uid_prefers_lac_pid_over_record_id():
    """Once Commissioner attaches lac_pid, it must take priority over record_id (LAC's own
    authoritative identifier vs. an OCR'd record_number) - proven here by two records with
    DIFFERENT record_ids but the SAME lac_pid still colliding onto one UID."""
    part = {"role_number": "1"}
    rec_a = {"page": "page_001", "record_id": "SCRIP-5473", "lac_pid": "1502188", "participants": [part]}
    rec_b = {"page": "page_002", "record_id": "SCRIP-9999", "lac_pid": "1502188", "participants": [part]}

    assert General.generate_uid(rec_a, part, "1") == General.generate_uid(rec_b, part, "1")


def test_get_dynamic_source_id_omits_prefix_for_scrip():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        assert General.get_dynamic_source_id("3") == "@S003@"
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_get_dynamic_source_id_keeps_prefix_by_default_for_parish():
    General.set_active_profile(General.GeneralProfile())
    orig = General.GENERAL_CONFIG.get('register_source_id')
    try:
        General.GENERAL_CONFIG['register_source_id'] = '1042'
        assert General.get_dynamic_source_id("3") == "@S1042003@"
    finally:
        if orig is not None:
            General.GENERAL_CONFIG['register_source_id'] = orig
        else:
            General.GENERAL_CONFIG.pop('register_source_id', None)


def test_run_general_flavor_sets_profile_from_record_type():
    import Archivist
    profile = Archivist.resolve_profile("Scrip")
    assert isinstance(profile, Scrip.ScripProfile)
    profile = Archivist.resolve_profile("Parish")
    assert isinstance(profile, General.GeneralProfile)


def test_generate_uid_different_record_id_still_differs_without_lac_pid():
    """Sanity check the fix didn't accidentally collapse everything - genuinely different
    claims (no lac_pid on either) must still get different UIDs."""
    part = {"role_number": "1"}
    rec_a = {"page": "page_001", "record_id": "SCRIP-5473", "participants": [part]}
    rec_b = {"page": "page_001", "record_id": "SCRIP-9999", "participants": [part]}

    assert General.generate_uid(rec_a, part, "1") != General.generate_uid(rec_b, part, "1")


def test_generate_media_uid_for_path_deterministic_and_path_keyed():
    a = General.generate_media_uid_for_path("C:/Media/Commissioner/1502188/e011355548.pdf")
    b = General.generate_media_uid_for_path("C:/Media/Commissioner/1502188/e011355548.pdf")
    c = General.generate_media_uid_for_path("C:/Media/Commissioner/1502188/e011355549.pdf")
    assert a == b
    assert a != c
    assert a.startswith("M")


def test_build_general_citation_page_line_uses_claim_affdt_when_present():
    """RootsMagic/FTM auto-name citations from this PAGE line - "Record EVEN-1964" (an
    internal id_prefix+number) made the Citations list unbrowsable in real testing. Use
    the claim's own real reference numbers instead when known."""
    rec = {"page": "3", "record_id": "EVEN-1964", "year": "1901",
           "type_specific_fields": {"claim_number": "1964", "affidavit_number": "850"},
           "citation_text": "text", "citation_details": "text"}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")
    assert "3 PAGE Claim 1964; Affdt 850, Page 3" in blocks[0]
    assert "Record EVEN-1964" not in blocks[0]


def test_build_general_citation_page_line_falls_back_without_claim_affdt():
    rec = {"page": "3", "record_id": "B-1", "year": "1876",
           "citation_text": "text", "citation_details": "text"}
    part = {"std_given": "Jean", "std_surname": "Gagnon", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "BIRT", "1", "M0000000001")
    assert "3 PAGE Page 3, Record B-1" in blocks[0]


def test_build_general_citation_single_block_without_source_documents():
    """Backward compatibility: every record type that never populates source_documents
    (everything except Parish/Scrip claims that went through the merge step) must get
    exactly the same single-block behavior as before."""
    rec = {"page": "1", "record_id": "B-1", "year": "1876",
           "citation_text": "orig text", "citation_details": "eng text"}
    part = {"std_given": "Jean", "std_surname": "Gagnon", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "BIRT", "1", "M0000000001")
    assert isinstance(blocks, list)
    assert len(blocks) == 1
    assert "orig text" in blocks[0]
    assert "eng text" in blocks[0]


def test_build_general_citation_collapses_identical_original_and_translation():
    """Per the user: most Scrip affidavits are already in English, so
    citation_text and citation_details end up identical - showing both the
    "English Translation:"/"Original Transcription:" headers in that case just duplicates
    the same text twice. Only one plain text block, no header labels, when they match."""
    rec = {"page": "1", "record_id": "B-1", "year": "1876",
           "citation_text": "I, Roger Letendre, do solemnly swear...",
           "citation_details": "I, Roger Letendre, do solemnly swear..."}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

    assert len(blocks) == 1
    assert blocks[0].count("I, Roger Letendre, do solemnly swear") == 1
    assert "English Translation:" not in blocks[0]
    assert "Original Transcription:" not in blocks[0]
    assert "4 TEXT I, Roger Letendre" in blocks[0]


def test_build_general_citation_normalization_is_whitespace_only_not_fuzzy():
    """Word order actually differing (the "1964" moved) is a genuine content difference,
    not just reflow - must NOT collapse, proving the normalization added for whitespace
    reflow doesn't also hide a real mismatch via fuzzy/reordering matching."""
    rec = {"page": "1", "record_id": "B-1", "year": "1876",
           "citation_text": "1964\nForm A. (2).\nNORTH-WEST HALFBREED CLAIMS COMMISSION.",
           "citation_details": "Form A. (2). NORTH-WEST HALFBREED CLAIMS COMMISSION. 1964"}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

    assert len(blocks) == 1
    assert "Form A. (2)." in blocks[0]
    assert "NORTH-WEST HALFBREED CLAIMS COMMISSION" in blocks[0]


def test_build_general_citation_collapses_whitespace_only_reflow():
    """The actual confirmed-live case: same words, same order, only newlines vs. spaces
    differ - this one must collapse."""
    rec = {"page": "1", "record_id": "B-1", "year": "1876",
           "citation_text": "Form A. (2).\nNORTH-WEST HALFBREED CLAIMS COMMISSION.\nBefore me.",
           "citation_details": "Form A. (2). NORTH-WEST HALFBREED CLAIMS COMMISSION. Before me."}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

    assert len(blocks) == 1
    assert "Citation Details:" not in blocks[0]
    assert "Citation Text:" not in blocks[0]
    assert "NORTH-WEST HALFBREED CLAIMS COMMISSION" in blocks[0]


def test_build_general_citation_collapses_when_only_one_side_populated():
    rec = {"page": "1", "record_id": "B-1", "year": "1876",
           "citation_text": "", "citation_details": "Only a translation exists here."}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

    assert len(blocks) == 1
    assert "Only a translation exists here." in blocks[0]
    assert "Citation Details:" not in blocks[0]
    assert "Citation Text:" not in blocks[0]


def test_build_general_citation_keeps_both_blocks_for_a_genuine_translation():
    """The other direction: a real French-original document with a distinct English
    translation must keep both labeled sections - nothing to collapse there."""
    rec = {"page": "1", "record_id": "B-1", "year": "1876",
           "citation_text": "Je soussigné jure solennellement...",
           "citation_details": "I, the undersigned, do solemnly swear..."}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

    assert len(blocks) == 1
    assert "Je soussigné" in blocks[0]
    assert "I, the undersigned" in blocks[0]
    assert "-- " not in blocks[0]  # no document_type suffix when there's nothing to distinguish


def test_build_general_citation_shows_configured_header_labels_when_set():
    """CITATION_DETAIL/CITATION_TEXT are blank by default (no label line) but a user who
    configures them must still see their chosen label prefixed - the "header present" branch
    of General.citation_text_block, uncovered by every other test in this file (which all run
    against the blank-default GENERAL_CONFIG)."""
    orig_config = dict(General.GENERAL_CONFIG)
    try:
        General.GENERAL_CONFIG['citation_detail'] = "Citation Details:"
        General.GENERAL_CONFIG['citation_text'] = "Citation Text:"
        rec = {"page": "1", "record_id": "B-1", "year": "1876",
               "citation_text": "Je soussigné jure solennellement...",
               "citation_details": "I, the undersigned, do solemnly swear..."}
        part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
        blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

        assert len(blocks) == 1
        assert "Citation Details:" in blocks[0]
        assert "Citation Text:" in blocks[0]
        assert "Je soussigné" in blocks[0]
        assert "I, the undersigned" in blocks[0]
    finally:
        General.GENERAL_CONFIG.clear()
        General.GENERAL_CONFIG.update(orig_config)


def test_build_general_citation_one_block_per_source_document():
    rec = {"page": "1", "record_id": "SCRIP-5473", "year": "1880", "source_documents": [
        {"document_type": "Witness Affidavit", "page": "page_001",
         "citation_text": "witness orig", "citation_details": "witness eng"},
        {"document_type": "Claimant's Own Affidavit", "page": "page_002",
         "citation_text": "claimant orig", "citation_details": "claimant eng"},
    ]}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")

    assert len(blocks) == 2
    assert "-- Witness Affidavit" in blocks[0]
    assert "Page page_001" in blocks[0]
    assert "witness orig" in blocks[0] and "witness eng" in blocks[0]
    assert "claimant orig" not in blocks[0]

    assert "-- Claimant's Own Affidavit" in blocks[1]
    assert "Page page_002" in blocks[1]
    assert "claimant orig" in blocks[1] and "claimant eng" in blocks[1]
    assert "witness orig" not in blocks[1]

    # record_id (shared across all merged documents) stays constant across both blocks
    assert "Record SCRIP-5473" in blocks[0]
    assert "Record SCRIP-5473" in blocks[1]


def test_build_general_citation_media_only_entry_uses_its_own_path_derived_uid():
    """A Commissioner-downloaded certificate has no transcription, only a media_path -
    its citation block must reference an OBJE built from generate_media_uid_for_path on
    THAT path, not the shared media_uid passed in for the sheet's own scanned image."""
    media_path = "C:/Media/Commissioner/1502999/e099999999.pdf"
    rec = {"page": "1", "record_id": "SCRIP-5473", "year": "1880", "source_documents": [
        {"document_type": "Scrip Certificate", "media_path": media_path},
    ]}
    part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M_SHEET_UID")

    expected_uid = General.generate_media_uid_for_path(media_path)
    assert f"3 OBJE @{expected_uid}@" in blocks[0]
    assert "M_SHEET_UID" not in blocks[0]
    assert "-- Scrip Certificate" in blocks[0]


def test_build_gedcom_from_general_creates_separate_obje_for_commissioner_media():
    media_path = "C:/Media/Commissioner/1502999/e099999999.pdf"
    data = {
        "collection_title": "Test Scrip Collection", "record_type_name": "Scrip",
        "sheets": [{
            "document_metadata": {"file_name": "BAC-LAC_fonandcol_1502188.pdf", "pages": "1", "file_type": "pdf"},
            "records": [{
                "event_type": "Scrip", "page": "1", "record_id": "SCRIP-5473", "year": "1880",
                "type_specific_fields": {}, "source_documents": [
                    {"document_type": "Scrip Certificate", "media_path": media_path},
                ],
                "participants": [make_participant("primary", given="Roger", surname="Letendre")],
            }],
        }],
    }
    ged = General.build_gedcom_from_general(data, "RM")

    expected_uid = General.generate_media_uid_for_path(media_path)
    assert f"0 @{expected_uid}@ OBJE" in ged
    assert f"1 FILE {media_path}" in ged
    assert "2 TITL Scrip Certificate" in ged
    assert f"3 OBJE @{expected_uid}@" in ged  # the citation references the same UID


def test_generate_media_uid_for_lac_asset_uses_the_e_number_directly():
    """Confirmed live in review: an opaque hashed media UID makes a real GEDCOM harder to
    cross-reference by hand against LAC - the e-number's digits are already a stable,
    unique identifier, so they should be used directly rather than hashed. The leading
    "e" is stripped (not kept) - per the user, FTM import breaks on a non-numeric media
    object ID, so everything after the fixed "M" prefix must stay digits-only."""
    assert General.generate_media_uid_for_lac_asset("e011359206") == "M011359206"


def test_build_general_citation_prefers_lac_asset_id_over_hashed_path():
    """The realistic Commissioner shape: a source_documents entry always carries both
    media_path AND lac_asset_id - the e-number must win over hashing the path."""
    media_path = "C:/Media/Commissioner/1503710/e011359206.pdf"
    rec = {"page": "1", "record_id": "SCRIP-5473", "year": "1880", "source_documents": [
        {"document_type": "Scrip Certificate", "media_path": media_path, "lac_asset_id": "e011359206"},
    ]}
    part = {"std_given": "Margaret", "std_surname": "Sabiston", "role_number": "1"}
    blocks = General.build_general_citation(rec, part, "EVEN", "1", "M_SHEET_UID")

    assert "3 OBJE @M011359206@" in blocks[0]
    hashed_uid = General.generate_media_uid_for_path(media_path)
    assert f"@{hashed_uid}@" not in blocks[0]


# noinspection DuplicatedCode
def test_build_gedcom_from_general_uses_lac_asset_id_for_obje_when_present():
    media_path = "C:/Media/Commissioner/1503710/e011359206.pdf"
    data = {
        "collection_title": "Test Scrip Collection", "record_type_name": "Scrip",
        "sheets": [{
            "document_metadata": {"file_name": "e011359206.pdf", "pages": "1", "file_type": "pdf"},
            "records": [{
                "event_type": "Scrip", "page": "1", "record_id": "SCRIP-5473", "year": "1880",
                "type_specific_fields": {}, "source_documents": [
                    {"document_type": "Scrip Certificate", "media_path": media_path, "lac_asset_id": "e011359206"},
                ],
                "participants": [make_participant("primary", given="Margaret", surname="Sabiston")],
            }],
        }],
    }
    ged = General.build_gedcom_from_general(data, "RM")

    assert "0 @M011359206@ OBJE" in ged
    assert f"1 FILE {media_path}" in ged
    assert "3 OBJE @M011359206@" in ged


def test_build_individual_scrip_desc_line_groups_claim_affidavit_scrip_together():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SCRIP-5473", "event_place": "Winnipeg",
               "type_specific_fields": {
                   "claim_number": "3126", "affidavit_number": "5473",
                   "scrip_number": "12761", "scrip_amount": "$160", "claim_basis": "Half-breed Head",
               },
               "participants": [make_participant("primary", given="Roger", surname="Letendre")]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)

        assert "1 EVEN Claim: 3126; Affidavit #: 5473; Scrip #: 12761 ($160); Claim Basis: Half-breed Head" in joined
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_scrip_desc_line_without_claim_or_affidavit_still_shows_scrip_number():
    """No claim_number/affidavit_number present (e.g. an older extraction) - the fact's own
    value line still shows whatever Scrip-specific fields it does have."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SCRIP-1", "event_place": "Winnipeg",
               "type_specific_fields": {"scrip_number": "12761", "scrip_amount": "$160"},
               "participants": [make_participant("primary", given="Roger", surname="Letendre")]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)

        assert "1 EVEN Scrip #: 12761 ($160)" in joined
        assert "Claim:" not in joined and "Affidavit #:" not in joined
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_scrip_desc_line_excludes_document_type_with_claim():
    """document_type labels ONE physical document (e.g. "Witness Affidavit"), not the
    whole merged claim - showing it in the fact's own value line was misleading (per the
    user, the note appeared to cite only the witness affidavit). Still available per
    citation via _TITL's own "-- {document_type}" suffix, just not here."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SCRIP-5473", "event_place": "Winnipeg",
               "type_specific_fields": {
                   "claim_number": "3126", "affidavit_number": "5473",
                   "scrip_number": "12761", "document_type": "Witness Affidavit",
               },
               "participants": [make_participant("primary", given="Roger", surname="Letendre")]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)

        assert "Document Type" not in joined
        even_line = next(line for line in lines if line.startswith("1 EVEN"))
        assert "Witness Affidavit" not in even_line
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_scrip_desc_line_excludes_document_type_without_claim():
    """Same exclusion in the no-claim/affidavit path."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SCRIP-1", "event_place": "Winnipeg",
               "type_specific_fields": {"scrip_number": "12761", "document_type": "Register Entry"},
               "participants": [make_participant("primary", given="Roger", surname="Letendre")]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)

        assert "Document Type" not in joined
        assert "Register Entry" not in joined
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_parse_household_forms_second_family_unit_for_unrelated_boarder_household():
    """Regression: a family/dwelling-number group can legitimately contain two unrelated
    families (e.g. a boarder sharing a dwelling with an unrelated nuclear family) - real
    1860 census data ('Depar' family sharing a dwelling with boarder 'Hougton'). Previously,
    a plausible spouse pair (matching surname, opposite sex, valid age gap) that didn't
    also fit as a child of the group's first-listed unit was silently discarded instead of
    forming its own unit - every member of the second family fell through to 'unrelated'
    one at a time. parse_household must now recognize the second family as its own unit,
    with subsequent same-surname children correctly attaching to it."""
    rows = [
        {"Given Name": "Joseph", "Surname": "Hougton", "Gender": "Male", "Age": "45"},
        {"Given Name": "James", "Surname": "Depar", "Gender": "Male", "Age": "40"},
        {"Given Name": "Vide", "Surname": "Depar", "Gender": "Female", "Age": "34"},
        {"Given Name": "Lucretia", "Surname": "Depar", "Gender": "Female", "Age": "12"},
    ]
    group = pd.DataFrame(rows)
    units, unrelated, flags = Census.parse_household(group, Census.CensusRunConfig())

    assert len(unrelated) == 0
    assert len(units) == 2
    depar_unit = next(u for u in units
                      if (u["husband"] is not None and Utils.clean_val(u["husband"].get("Surname")) == "Depar"))
    assert Utils.clean_val(depar_unit["husband"].get("Given Name")) == "James"
    assert Utils.clean_val(depar_unit["wife"].get("Given Name")) == "Vide"
    assert [Utils.clean_val(c.get("Given Name")) for c in depar_unit["children"]] == ["Lucretia"]


def make_participant(role_semantic=None, role_name="", sex="M", given="Jean", surname="Gagnon",
                     is_priest=False, age=""):
    return {
        "role_semantic": role_semantic, "role_name": role_name, "role_number": "0",
        "sex": sex, "std_given": given, "std_surname": surname, "is_priest": is_priest, "age": age,
    }


def test_get_event_gedcom_tag_person_and_family_buckets():
    assert Utils.get_event_gedcom_tag("Baptism") == "BAPM"
    assert Utils.get_event_gedcom_tag("Christen") == "CHR"
    assert Utils.get_event_gedcom_tag("Burial") == "BURI"
    assert Utils.get_event_gedcom_tag("Marriage") == "MARR"


def test_get_event_gedcom_tag_unknown_falls_back_to_even():
    assert Utils.get_event_gedcom_tag("Some Future Fact Type") == "EVEN"


def test_is_family_event_true_only_for_family_bucket():
    assert Utils.is_family_event("Marriage") is True
    assert Utils.is_family_event("Baptism") is False
    assert Utils.is_family_event("Scrip") is False


def test_get_by_semantic_and_get_all_by_semantic():
    rec = {"participants": [
        make_participant("primary"), make_participant("father"), make_participant("mother"),
    ]}
    assert General.get_by_semantic(rec, "primary") is rec["participants"][0]
    assert General.get_by_semantic(rec, "spouse") is None
    assert General.get_all_by_semantic(rec, ("father", "mother")) == rec["participants"][1:]


def test_assign_spouses_by_sex():
    a = make_participant(sex="F")
    b = make_participant(sex="M")
    husb, wife = General.assign_spouses_by_sex(a, b)
    assert husb is b and wife is a


def test_assign_spouses_by_sex_single_parent_defaults_by_own_sex():
    mother = make_participant(sex="F")
    husb, wife = General.assign_spouses_by_sex(mother, None)
    assert husb is None and wife is mother


def test_resolve_family_links_baptism_shape_no_spouse_or_children():
    """Primary is purely a child in this record: their parents keep the unsuffixed FAM id,
    exactly as before child/spouse/in-law roles existed."""
    rec = {"participants": [make_participant("primary"), make_participant("father")]}
    links = General.resolve_family_links(rec)
    assert links["primary_forms_own_family"] is False
    assert links["primary_parents_suffix"] == ""


def test_resolve_family_links_marriage_shape_with_spouse():
    rec = {"participants": [make_participant("primary"), make_participant("spouse")]}
    links = General.resolve_family_links(rec)
    assert links["primary_forms_own_family"] is True
    assert links["main_suffix"] == ""
    assert links["primary_parents_suffix"] == "G"
    assert links["spouse_parents_suffix"] == "B"


def test_build_individual_primary_with_no_parents_still_gets_famc():
    """Regression: build_family always creates a FAM for a primary who is purely a child
    in this record, even with neither parent present (matching this record type's GEDCOM
    output from before family/child/in-law roles existed - see resolve_family_links).
    build_individual's own FAMC line has to point at that same FAM unconditionally too, not
    only when has_parents is true - caught by diffing against pre-refactor output on a
    burial with no parents recorded, where this FAMC line went missing entirely."""
    rec = {"event_type": "Burial", "page": "12", "record_id": "S-2", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
    ]}
    primary = rec["participants"][0]
    lines, _, _, _ = General.build_individual("I1", rec, primary, "12", "M0000000001", "27 JUL 2026", False, "RM")
    joined = "\n".join(lines)
    assert "1 FAMC @F" in joined
    fams = General.build_family(rec, "12", "M0000000001", "RM")
    assert len(fams) == 1
    famc_id = joined.split("1 FAMC @F")[1].split("@")[0]
    assert famc_id in fams[0]


def test_build_individual_fsftid_gets_companion_fs_tree_weblink():
    """Regression: only the record/citation-level FamilySearch ark URL was ever emitted -
    the person's own FS Tree profile page never had a link of its own, so it couldn't
    compete with the Ancestry link already present. _FSFTID must now be accompanied by a
    weblink to https://www.familysearch.org/tree/person/details/{fsftid}, RM/FTM-flavored
    the same way every other weblink already is (weblink_lines)."""
    rec = {"event_type": "Burial", "page": "12", "record_id": "S-2", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
    ]}
    primary = dict(rec["participants"][0], type_specific_fields={"fsftid": "LZXY-ABC"})
    rec["participants"][0] = primary  # type: ignore

    rm_lines, _, _, _ = General.build_individual("I1", rec, primary, "12", "M0000000001", "27 JUL 2026", False, "RM")
    joined_rm = "\n".join(rm_lines)
    assert "1 _FSFTID LZXY-ABC" in joined_rm
    assert "1 _WEBTAG" in joined_rm
    assert "2 URL https://www.familysearch.org/tree/person/details/LZXY-ABC" in joined_rm

    ftm_lines, _, _, _ = General.build_individual("I1", rec, primary, "12", "M0000000001", "27 JUL 2026", False, "FTM")
    joined_ftm = "\n".join(ftm_lines)
    assert "1 _LINK https://www.familysearch.org/tree/person/details/LZXY-ABC" in joined_ftm


def test_build_individual_apid_is_individual_level_not_nested_in_citation(monkeypatch):
    """_APID is an individual-level GEDCOM tag (FTM/RM both expect it directly on the INDI
    record, same as _FSFTID above), never nested inside a per-fact SOUR citation -
    build_individual must emit exactly one '1 _APID ...' line, and build_general_citation
    must never emit a '3 _APID' (the old, incorrect citation-nested placement)."""
    monkeypatch.setattr(Utils, "APID_DB", "2442")
    rec = {"event_type": "Burial", "page": "12", "record_id": "S-2", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
    ]}
    primary = dict(rec["participants"][0], type_specific_fields={"apid": "105307051"})
    rec["participants"][0] = primary  # type: ignore

    lines, _, _, _ = General.build_individual("I1", rec, primary, "12", "M0000000001", "27 JUL 2026", False, "RM")
    apid_lines = [ln for ln in lines if "_APID" in ln]
    assert apid_lines == ["1 _APID 1,2442::105307051"], apid_lines


# noinspection DuplicatedCode
def test_build_family_baptism_shape_single_famc_no_suffix():
    rec = {"event_type": "Baptism", "page": "1", "record_id": "B-1", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
        make_participant("father", given="Pierre", surname="Ledoux"),
        make_participant("mother", given="Marie", surname="Roy"),
    ]}
    fams = General.build_family(rec, "1", "M0000000001", "RM")
    assert len(fams) == 1
    assert "1 HUSB" in fams[0] and "1 WIFE" in fams[0] and "1 CHIL" in fams[0]
    assert "@F" in fams[0].splitlines()[0] and not fams[0].splitlines()[0].split("@")[1].endswith(("G", "B"))


def test_build_family_marriage_shape_three_families_with_suffixes():
    rec = {"event_type": "Marriage", "page": "5", "record_id": "M-1", "event_date": "1850-01-01",
           "participants": [
               make_participant("primary", given="Jean", surname="Gagnon"),
               make_participant("spouse", given="Marie", surname="Boucher", sex="F"),
               make_participant("father", given="Pierre", surname="Gagnon"),
               make_participant("mother", given="Anne", surname="Cyr", sex="F"),
               make_participant("father_in_law", given="Louis", surname="Boucher"),
               make_participant("mother_in_law", given="Rose", surname="Dubois", sex="F"),
           ]}
    fams = General.build_family(rec, "5", "M0000000001", "RM")
    assert len(fams) == 3
    main, g_fam, b_fam = fams
    assert "1 MARR" in main
    assert g_fam.splitlines()[0].split("@")[1].endswith("G")
    assert b_fam.splitlines()[0].split("@")[1].endswith("B")


def test_build_family_burial_with_surviving_spouse_forms_fams_without_marr_event():
    """A burial naming the deceased's surviving spouse is real marriage evidence and should
    link as a real family - previously dropped entirely (role '4' outside a marriage record
    had no FAMC/FAMS handling at all). Burial is a person-level fact, so no MARR event is
    attached to this FAM - that event already lives on the deceased's own BURI record."""
    rec = {"event_type": "Burial", "page": "9", "record_id": "S-1", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
        make_participant("spouse", given="Marie", surname="Roy", sex="F"),
    ]}
    fams = General.build_family(rec, "9", "M0000000001", "RM")
    assert len(fams) == 1
    assert "1 HUSB" in fams[0] and "1 WIFE" in fams[0]
    assert "MARR" not in fams[0]


def test_build_family_scrip_shape_claimant_spouse_and_children_share_one_family():
    rec = {"event_type": "Scrip", "page": "2", "record_id": "SC-1", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
        make_participant("spouse", given="Marie", surname="Roy", sex="F"),
        make_participant("child", given="Louis", surname="Ledoux"),
        make_participant("child", given="Rose", surname="Ledoux", sex="F"),
    ]}
    fams = General.build_family(rec, "2", "M0000000001", "RM")
    assert len(fams) == 1
    assert fams[0].count("1 CHIL") == 2


def test_build_individual_famc_and_fams_tags_use_semantic_not_digits():
    rec = {"event_type": "Marriage", "page": "5", "record_id": "M-1", "event_date": "1850-01-01",
           "participants": [
               make_participant("primary", given="Jean", surname="Gagnon"),
               make_participant("spouse", given="Marie", surname="Boucher", sex="F"),
               make_participant("father", given="Pierre", surname="Gagnon"),
           ]}
    primary = rec["participants"][0]
    lines, _, _, _ = General.build_individual("I1", rec, primary, "5", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)
    assert "1 FAMS @F" in joined
    assert "1 FAMC @F" in joined and joined.count("@F") >= 2


def test_build_custom_fact_lines_renders_even_type_and_citation():
    rec = {"page": "1", "record_id": "B-1"}
    part = make_participant("primary")
    lines = General.build_custom_fact_lines("Race", "Metis", rec, part, "1", "M0000000001", "RM")
    assert lines[0] == "1 EVEN Metis"
    assert lines[1] == "2 TYPE Race"
    assert "2 SOUR" in "\n".join(lines)


def test_build_custom_fact_lines_empty_value_returns_nothing():
    rec = {"page": "1", "record_id": "B-1"}
    part = make_participant("primary")
    assert General.build_custom_fact_lines("Race", "", rec, part, "1", "M0000000001", "RM") == []


def test_build_individual_race_uses_generic_custom_fact_not_bare_race_tag():
    rec = {"event_type": "Baptism", "page": "1", "record_id": "B-1", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
    ]}
    primary = rec["participants"][0]
    primary["race"] = "Metis"  # type: ignore
    lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)
    assert "_RACE" not in joined
    assert "1 EVEN Metis" in joined and "2 TYPE Race" in joined


def test_build_individual_scrip_event_gets_type_line_and_value_from_extra_fields():
    """Scrip's own event_type resolves to gedcom_tag 'EVEN' - it's built as a dedicated
    "Scrip" custom fact (FactTypes.json code 10004), needing a '2 TYPE Scrip' line for
    RootsMagic to recognize it, with its own Date/Place/Desc all filled in."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SC-1", "event_place": "Winnipeg",
               "type_specific_fields": {"scrip_number": "1234", "scrip_amount": "$160"},
               "participants": [make_participant("primary", given="Baptiste", surname="Ledoux")]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)
        assert "2 TYPE Scrip" in joined
        assert "1 EVEN Scrip #: 1234 ($160)" in joined
        assert "2 PLAC Winnipeg" in joined
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_scrip_fact_gets_document_year_as_date_and_media_attached():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SC-1", "year": "1901",
               "type_specific_fields": {"scrip_number": "1234"},
               "participants": [make_participant("primary", given="Baptiste", surname="Ledoux")]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)
        assert "2 DATE 1901" in joined
        assert "3 OBJE @M0000000001@" in joined
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_scrip_race_fact_gets_no_document_year_date():
    """Per the user: everything but Race should carry the document year as its DATE -
    Race describes an ongoing characteristic, not something dated to one document."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SC-1", "year": "1901",
               "type_specific_fields": {}, "participants": [make_participant("primary")]}
        primary = rec["participants"][0]
        primary["race"] = "Metis"  # type: ignore
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
        joined = "\n".join(lines)
        race_block = joined.split("2 TYPE Race")[1].split("2 SOUR")[0]
        assert "2 DATE" not in race_block
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_scrip_witness_associations_exclude_nuclear_family():
    """Only true witnesses (no family-position role_semantic) become witness
    Associations - spouse/parent/child participants must not appear here even though the
    old filter (just excluding 'primary') used to sweep them in too. Uses FTM output,
    where witnesses render as plain names in a NOTE line - RM's own _SHAR form only ever
    carries a UID + role text, not a name, so it can't distinguish this directly."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"event_type": "Scrip", "page": "1", "record_id": "SC-1",
               "type_specific_fields": {"scrip_number": "1"},
               "participants": [
                   make_participant("primary", given="Baptiste", surname="Ledoux"),
                   make_participant("spouse", given="Marie", surname="Ledoux"),
                   make_participant("child", given="Jean", surname="Ledoux"),
                   make_participant(None, role_name="Witness", given="Louis", surname="Riel"),
               ]}
        primary = rec["participants"][0]
        lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "FTM")
        witness_note = next(line for line in lines if line.startswith("2 NOTE Witnesses:"))
        assert "Louis" in witness_note and "Riel" in witness_note
        assert "Marie" not in witness_note
        assert "Jean" not in witness_note
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_gedcom_from_general_excludes_commissioner_from_indis():
    """Participants with role_semantic 'commissioner' are captured in JSON for archival reference
    and LAC lookup, but must never be emitted as INDI records or witness associations in GEDCOM."""
    data = {
        "metadata": {"source_name": "Test Scrip", "volume": "1324"},
        "sheets": [{
            "sheet_metadata": {"file_name": "test.jpg"},
            "records": [{
                "event_type": "Scrip", "record_number": "101", "record_id": "SC-101", "page": "1",
                "type_specific_fields": {"claim_number": "101"},
                "participants": [
                    make_participant("primary", given="Pierre", surname="Falcon"),
                    make_participant("commissioner", role_name="Commissioner", given="Roger", surname="Goulet"),
                    make_participant(None, role_name="Witness", given="Louis", surname="Riel"),
                ],
            }],
        }],
    }
    ged = General.build_gedcom_from_general(data, "RM")
    # Pierre Falcon (claimant) and Louis Riel (witness) get INDIs; Roger Goulet (commissioner) must NOT
    assert "1 NAME Pierre /Falcon/" in ged
    assert "1 NAME Louis /Riel/" in ged
    assert "Roger" not in ged
    assert "Goulet" not in ged


def test_build_individual_baptism_event_gets_no_type_line():
    """A standard GEDCOM-tagged event (Baptism -> BAPM) must not gain a '2 TYPE' line or
    any value text on its '1 BAPM' line - that's exclusively for the 'EVEN' fallback case."""
    rec = {"event_type": "Baptism", "page": "1", "record_id": "B-1", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
    ]}
    primary = rec["participants"][0]
    lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)
    assert "1 BAPM" in joined
    assert "2 TYPE Baptism" not in joined


def test_build_individual_alternate_name_renders_as_proposed_name_fact_with_event_note():
    """A margin note offering a different spelling (a later annotator's aid, not the
    priest's own entry - see Parish.pmt's MARGIN ANNOTATIONS rule) becomes its own
    'proposed' NAME fact, same convention as census's crowdsourced alternate readings,
    plus a NOTE on the primary's own event so it's visible right where the event is."""
    rec = {"event_type": "Baptism", "page": "1", "record_id": "B-1", "participants": [
        make_participant("primary", given="Baptiste", surname="Ledoux"),
    ]}
    primary = rec["participants"][0]
    primary["alternate_names"] = [{"value": "Baptiste Ladoux"}]
    lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)
    assert "1 NAME Baptiste /Ladoux/" in joined
    assert "2 _PROOF proposed" in joined
    assert "2 NOTE Margin note suggests alternate spelling: Baptiste Ladoux" in joined


# ==========================================
# Scrip custom RootsMagic source templates (Metis Scrip.rmst, Ids 20001-20005)
# ==========================================
def test_select_scrip_template_id_manitoba_from_commission_reference():
    assert Scrip.select_scrip_template_id("Affidavit under Manitoba Act, 33 Vic. Cap 3", "Witness Affidavit") == 20001


def test_select_scrip_template_id_north_west_from_commission_reference():
    ref = "Form A - North-West Half-Breed Claims Commission under Order in Council of 30th March, 1885"
    assert Scrip.select_scrip_template_id(ref, "Claimant's Own Affidavit") == 20002


def test_select_scrip_template_id_treaty_8_from_commission_reference():
    assert Scrip.select_scrip_template_id("Form C - Treaty No. 8", "Witness Affidavit") == 20003


def test_select_scrip_template_id_certificate_from_document_type_regardless_of_commission():
    """document_type wins over commission_reference - a Certificate is a structurally
    different document from an affidavit no matter which commission issued it."""
    assert Scrip.select_scrip_template_id("Manitoba Act, 33 Vic. Cap 3", "Scrip Certificate") == 20004


def test_select_scrip_template_id_prefers_series_code_over_commission_reference_text():
    """series_code is LAC's own catalog classification (Commissioner-fetched) - more
    authoritative than the printed commission_reference text, so it wins when present,
    even when commission_reference text alone would have matched a different template."""
    assert Scrip.select_scrip_template_id("some unrelated header text", "Witness Affidavit",
                                          series_code="RG15-D-II-8-c") == 20002


def test_select_scrip_template_id_returns_none_when_unrecognized():
    """No guessing - an unresolved record must fall back to the plain freeform source,
    not get mis-templated."""
    assert Scrip.select_scrip_template_id("", "") is None
    assert Scrip.select_scrip_template_id("some unrelated header text", "Register Entry") is None


def test_scrip_template_field_value_microfilm_from_commissioner_reel_numbers():
    rec = {"type_specific_fields": {"reel_numbers": "C-14929, C-14930"}}
    part = {}
    assert Scrip._scrip_template_field_value("Microfilm", rec, part, "1320") == "C-14929, C-14930"


def test_scrip_template_field_value_issue_date_prefers_dedicated_field_over_application_date():
    rec = {"type_specific_fields": {"issue_date": "5 May 1886", "application_date": "1 Jan 1886"}}
    assert Scrip._scrip_template_field_value("IssueDate", rec, {}, "1320") == "5 May 1886"


def test_scrip_template_field_value_issue_date_falls_back_to_application_date():
    rec = {"type_specific_fields": {"application_date": "1 Jan 1886"}}
    assert Scrip._scrip_template_field_value("IssueDate", rec, {}, "1320") == "1 Jan 1886"


def test_scrip_template_field_value_treaty_8_delivery_fields():
    rec = {"type_specific_fields": {"delivery_date": "3 Aug 1900", "delivery_place": "Fort Vermilion"}}
    assert Scrip._scrip_template_field_value("DeliveryDate", rec, {}, "1") == "3 Aug 1900"
    assert Scrip._scrip_template_field_value("DeliveryPlace", rec, {}, "1") == "Fort Vermilion"


def test_scrip_template_field_value_land_grant_fields_are_a_known_gap():
    """No claimant affidavit states these - they belong to a later, separately-cataloged
    Dominion Lands Office patent record Commissioner doesn't fetch yet."""
    rec = {"type_specific_fields": {}}
    for field in ("OriginalClaimant", "LandDescription", "Liber", "Folio"):
        assert Scrip._scrip_template_field_value(field, rec, {}, "1") == ""


def test_get_scrip_citation_fields_declares_empty_values_blank_not_omitted():
    rec = {"type_specific_fields": {"affidavit_number": "5473"}, "lac_pid": ""}
    part = make_participant("primary", given="Roger", surname="Letendre")
    lines = Scrip.get_scrip_citation_fields(20001, rec, part, "1320")
    joined = "\n".join(lines)
    assert "3 _TMPLT" in joined
    assert "5 NAME AffidavitNumber" in joined and "5 VALUE 5473" in joined
    assert "5 NAME ClaimantName" in joined and "5 VALUE Roger Letendre" in joined
    # Microfilm/URL were never set on this record - RM only omits a <...> Footnote
    # clause when its field is declared with an empty VALUE, not when the field is
    # missing entirely, so both must still be declared here, just blank.
    assert "5 NAME Microfilm\n5 VALUE " in joined
    assert "5 NAME URL\n5 VALUE " in joined


def test_build_general_citation_scrip_cites_the_matching_template_source_with_field_block():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"page": "1", "record_id": "SC-1", "record_number": "5473", "lac_pid": "1506170",
               "type_specific_fields": {"commission_reference": "Affidavit under Manitoba Act, 33 Vic. Cap 3",
                                        "affidavit_number": "5473"}}
        part = make_participant("primary", given="William", surname="Anderson")
        blocks = General.build_general_citation(rec, part, "CENS", "1324", "M0000000001", target_software="RM")
        joined = blocks[0]
        assert "2 SOUR @S20001@" in joined
        assert "3 _TMPLT" in joined
        assert "5 NAME AffidavitNumber" in joined and "5 VALUE 5473" in joined
        assert "LAC Digital Record" in joined
        assert (
            "https://recherche-collection-search.bac-lac.gc.ca/eng/Home/Record?app=fonandcol&IdNumber=1506170"
            in joined
        )
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_general_citation_scrip_forces_proven_even_when_date_is_estimated():
    """Every Scrip fact is read straight off a sworn primary source - get_proof_status'
    generic BEF/ABT/EST downgrade (still correct for Parish/Census) must not apply here."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"page": "1", "record_id": "SC-1", "type_specific_fields": {}}
        part = make_participant("primary")
        blocks = General.build_general_citation(rec, part, "BIRT", "1324", "M0000000001",
                                                proof_status="proposed", target_software="RM")
        assert "2 _PROOF proven" in blocks[0]
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_general_citation_scrip_falls_back_to_freeform_when_template_unresolved():
    """An unrecognized commission_reference must not get mis-templated - it cites the
    existing per-volume freeform source instead, same @S{vol}@ id as before this change."""
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"page": "1", "record_id": "SC-1", "type_specific_fields": {}}
        part = make_participant("primary")
        blocks = General.build_general_citation(rec, part, "CENS", "1324", "M0000000001", target_software="RM")
        assert "2 SOUR @S1324@" in blocks[0]
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_get_volume_sources_omits_church_template_for_scrip():
    General.set_active_profile(Scrip.ScripProfile())
    orig_config = dict(General.GENERAL_CONFIG)
    try:
        General.GENERAL_CONFIG['parish_name'] = "Library and Archives Canada"
        General.GENERAL_CONFIG['register_name'] = "Scrip Records"
        General.GENERAL_CONFIG['volume_title'] = "Scrip Records"
        General.GENERAL_CONFIG['parish_location'] = "Ottawa, ON"
        lines = General.get_volume_sources({"1324"}, "RM")
        joined = "\n".join(lines)
        assert "TID 355" not in joined
        assert "Church_Author" not in joined
    finally:
        General.GENERAL_CONFIG.clear()
        General.GENERAL_CONFIG.update(orig_config)
        General.set_active_profile(General.GeneralProfile())


def test_get_volume_sources_keeps_church_template_for_parish():
    General.set_active_profile(General.GeneralProfile())
    orig_config = dict(General.GENERAL_CONFIG)
    try:
        General.GENERAL_CONFIG['parish_name'] = "St. Boniface"
        General.GENERAL_CONFIG['register_name'] = "Baptisms"
        General.GENERAL_CONFIG['volume_title'] = "Baptisms"
        General.GENERAL_CONFIG['parish_location'] = "Manitoba"
        lines = General.get_volume_sources({"1"}, "RM")
        joined = "\n".join(lines)
        assert "TID 10009" in joined
    finally:
        General.GENERAL_CONFIG.clear()
        General.GENERAL_CONFIG.update(orig_config)
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_researcher_citation_has_both_name_and_titl():
    """RootsMagic needs both '2 NAME' and '2 _TITL' under the shared @S1@ researcher
    citation to merge it across multiple people's citations in its own UI - confirmed
    live by the user; _TITL alone displays but doesn't merge."""
    rec = {"event_type": "Baptism", "page": "1", "record_id": "B-1",
           "participants": [make_participant("primary", given="Baptiste", surname="Ledoux")]}
    primary = rec["participants"][0]
    lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)
    assert "2 NAME Researcher:" in joined
    assert "2 _TITL Researcher:" in joined


def test_format_gedcom_date_handles_iso_and_natural_text():
    assert Utils.format_gedcom_date("1850-12-12") == "12 DEC 1850"
    assert Utils.format_gedcom_date("1850-06") == "JUN 1850"
    assert Utils.format_gedcom_date("1850") == "1850"
    assert Utils.format_gedcom_date("December 12, 1850") == "12 DEC 1850"
    assert Utils.format_gedcom_date("12 December 1850") == "12 DEC 1850"
    assert Utils.format_gedcom_date("15 Jun 1875") == "15 JUN 1875"
    assert Utils.format_gedcom_date("June 1875") == "JUN 1875"
    assert Utils.format_gedcom_date("ABT 1850-12-12") == "ABT 12 DEC 1850"
    assert Utils.format_gedcom_date("BEF December 12, 1850") == "BEF 12 DEC 1850"
    assert Utils.format_gedcom_date("BET 1850 AND 1855") == "BET 1850 AND 1855"
    assert Utils.format_gedcom_date("") == ""


def test_build_general_citation_scrip_emits_commissioners_review_note():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {
            "page": "1", "record_id": "SCRIP-5473", "year": "1885",
            "citation_text": "Verbatim French / English affidavit text...",
            "citation_details": "Commissioner's Review: Roger Letendre claims as Half-breed head of family.",
            "type_specific_fields": {"claim_number": "5473"}
        }
        part = {"std_given": "Roger", "std_surname": "Letendre", "role_number": "1"}
        blocks = General.build_general_citation(rec, part, "EVEN", "1", "M0000000001")
        assert len(blocks) == 1
        assert "4 TEXT Verbatim French / English affidavit text" in blocks[0]
        assert "3 NOTE Commissioner's Review:" in blocks[0]
        assert "Roger Letendre claims as Half-breed head of family" in blocks[0]
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_apply_collection_metadata_auto_resolves_image_dir(monkeypatch):
    """IMAGE_DIR has no user-facing override (not even via env var) - it's always
    auto-resolved to Media/<record type>, matching Paleographer/Extract.py's own
    SOURCE_DIR convention for where that type's images were actually extracted from. A
    stray HBCA_IMAGE_DIR env var (e.g. left over from a legacy .env) must NOT change it."""
    monkeypatch.setenv("HBCA_IMAGE_DIR", "SomeOtherPath")
    orig_image_dir = General.IMAGE_DIR
    try:
        General.apply_collection_metadata({"record_type_name": "HBCA"})
        assert General.IMAGE_DIR == Utils.safe_path(Utils.GENEALOGY_DIR, os.getenv("MEDIA_DIR", "Media"), "HBCA")

        General.apply_collection_metadata({"record_type_name": "Parish"})
        assert General.IMAGE_DIR == Utils.safe_path(Utils.GENEALOGY_DIR, os.getenv("MEDIA_DIR", "Media"), "Parish")

        General.apply_collection_metadata({"record_type_name": "Scrip"})
        assert General.IMAGE_DIR == Utils.safe_path(Utils.GENEALOGY_DIR, os.getenv("MEDIA_DIR", "Media"), "Scrip")
    finally:
        General.IMAGE_DIR = orig_image_dir


def test_run_general_flavor_scrip_defaults_to_scrip_ged(monkeypatch):
    monkeypatch.setattr(Utils, "resolve_gedcom_output_targets", lambda: [])
    monkeypatch.delenv("GEDCOM_OUTPUT_NAME", raising=False)
    monkeypatch.delenv("SCRIP_GEDCOM_NAME", raising=False)
    orig_output_name = Utils.GEDCOM_OUTPUT_NAME
    orig_repo = General.REPOSITORY
    orig_repo_loc = General.REPOSITORY_LOC
    orig_config = dict(General.GENERAL_CONFIG)
    try:
        Utils.GEDCOM_OUTPUT_NAME = "Family_Register.ged"
        General.run_general_flavor({"record_type_name": "Scrip", "sheets": []}, Scrip.ScripProfile())
        assert Utils.GEDCOM_OUTPUT_NAME == "Scrip.ged"
    finally:
        Utils.GEDCOM_OUTPUT_NAME = orig_output_name
        General.REPOSITORY = orig_repo
        General.REPOSITORY_LOC = orig_repo_loc
        General.GENERAL_CONFIG.clear()
        General.GENERAL_CONFIG.update(orig_config)
        General.set_active_profile(General.GeneralProfile())


def test_load_source_template_lines_from_rmst():
    lines = General.load_source_template_lines(20001)
    joined = "\n".join(lines)
    assert "0 _STMPLT" in joined
    assert "1 TID 20001" in joined
    assert "1 NAME !Simple Citations: Métis Scrip (Manitoba, 1870–1876)" in joined
    assert "1 CAT Simplified Citations for Genealogical Sources" in joined
    assert "1 FOOTNOTE" in joined
    assert "1 SHORT" in joined
    assert "1 BIBLIO" in joined
    assert "1 FIELD\n2 NAME ClaimantName" in joined


def test_get_scrip_template_sources_simplified_citations_fields():
    sources = Scrip.get_scrip_template_sources({20001}, "RM")
    joined = "\n".join(sources)
    assert "0 @S20001@ SOUR" in joined
    assert "2 TID 20001" in joined
    assert "3 NAME PrimaryCreator\n3 VALUE Department of the Interior" in joined
    assert "3 NAME Department\n3 VALUE Manitoba Scrip Commission" in joined
    assert "3 NAME Date\n3 VALUE 1870–1876" in joined
    assert "3 NAME SourceDescription\n3 VALUE Manitoba Métis Scrip Applications" in joined


def test_generate_uid_scrip_uses_pid_or_record_id_directly():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        rec = {"page": "1", "record_id": "SC-100", "lac_pid": "1502188", "participants": [
            {"role_number": "0", "std_given": "Jean", "std_surname": "Riel"},
            {"role_number": "1", "std_given": "Marie", "std_surname": "Lafreniere"}
        ]}
        # Primary participant role 0 -> returns PID directly
        primary_uid = General.generate_uid(rec, rec["participants"][0], "1")
        assert primary_uid == "1502188"

        # Secondary participant role 1 -> returns PID_role
        spouse_uid = General.generate_uid(rec, rec["participants"][1], "1")
        assert spouse_uid == "1502188_1"
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_gedcom_from_general_emits_srctemplates_for_rm():
    General.set_active_profile(Scrip.ScripProfile())
    try:
        json_data = {
            "record_type_name": "Scrip",
            "sheets": [
                {
                    "volume_identifier": "1324",
                    "records": [
                        {
                            "page": "1",
                            "record_id": "SC-1",
                            "lac_pid": "1506170",
                            "event_type": "Affidavit",
                            "event_date": "1875",
                            "type_specific_fields": {
                                "commission_reference": "Affidavit under Manitoba Act, 33 Vic. Cap 3",
                                "affidavit_number": "5473"
                            },
                            "participants": [
                                {
                                    "role_number": "0",
                                    "role_semantic": "primary",
                                    "std_given": "William",
                                    "std_surname": "Anderson",
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        ged_text = General.build_gedcom_from_general(json_data, target_software="RM")
        assert "0 _STMPLT" in ged_text
        assert "1 NAME !Simple Citations: Métis Scrip (Manitoba, 1870–1876)" in ged_text
        assert "0 @S20001@ SOUR" in ged_text
        assert "0 @I1506170@ INDI" in ged_text
    finally:
        General.set_active_profile(General.GeneralProfile())


def test_build_individual_renders_generic_facts_via_fact_types():
    rec = {"event_type": "Baptism", "page": "1", "record_id": "REC-1", "event_place": "Quebec",
           "participants": [make_participant("primary", given="Jean", surname="Gagnon")]}
    primary = rec["participants"][0]  # type: ignore
    primary["facts"] = [{"fact_type": "Occupation", "value": "Farmer"}]  # type: ignore

    lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)

    assert "1 EVEN Farmer" in joined
    assert "2 TYPE Occupation" in joined


def test_build_individual_skips_unknown_fact_type_gracefully():
    rec = {"event_type": "Baptism", "page": "1", "record_id": "REC-2", "event_place": "Quebec",
           "participants": [make_participant("primary", given="Marie", surname="Boucher")]}
    primary = rec["participants"][0]  # type: ignore
    primary["facts"] = [{"fact_type": "", "value": "irrelevant"}]  # type: ignore

    lines, _, _, _ = General.build_individual("I1", rec, primary, "1", "M0000000001", "26 JUL 2026", False, "RM")
    joined = "\n".join(lines)

    assert "irrelevant" not in joined


def test_gedcom_wrapped_lines_cont_on_paragraph_break():
    lines = Census._gedcom_wrapped_lines(1, "DESC", "First paragraph.\n\nSecond paragraph.")
    assert lines[0] == "1 DESC First paragraph."
    assert lines[1] == "2 CONT "
    assert lines[2] == "2 CONT Second paragraph."


def test_gedcom_wrapped_lines_conc_on_long_line():
    long_text = "A" * 250
    lines = Census._gedcom_wrapped_lines(1, "FOOTNOTE", long_text, max_len=200)
    assert lines[0] == f"1 FOOTNOTE {'A' * 200}"
    assert lines[1] == f"2 CONC {'A' * 50}"


def test_rmst_element_to_gedcom_uses_stmplt_tag_vocabulary():
    import lxml.etree as etree
    xml = etree.fromstring("""
    <Template Id="99999">
      <Name>!Test Template</Name>
      <Description>A description.</Description>
      <Category>Test Category</Category>
      <Footnote>Footnote text.</Footnote>
      <ShortFootnote>Short text.</ShortFootnote>
      <Bibliography>Bibliography text.</Bibliography>
      <Field>
        <Type>Text</Type>
        <Name>TestField</Name>
        <Display>Test Field</Display>
        <Hint>a hint</Hint>
        <Detail>True</Detail>
        <LongHint>a long hint</LongHint>
      </Field>
    </Template>
    """)
    lines = Census._rmst_element_to_gedcom(xml)
    joined = "\n".join(lines)
    assert "0 _STMPLT" in joined
    assert "_SRCTEMPLATE" not in joined
    assert "1 TID 99999" in joined
    assert "1 NAME !Test Template" in joined
    assert "1 FOOTNOTE Footnote text." in joined
    assert "1 BIBLIO Bibliography text." in joined
    assert "2 DISPLAY Test Field" in joined
    assert "2 LONGHINT a long hint" in joined
    assert "2 TYPE TEXT" in joined
    assert "2 ISDETAIL Y" in joined
    assert "FOOT " not in joined
    assert "BIBL " not in joined
    assert "DISP " not in joined
    assert "DETL " not in joined
    assert "LHNT " not in joined


def test_general_profile_citation_detail_fields_wraps_in_tmplt():
    rec = {"record_id": "REC-1", "type_specific_fields": {}}
    part = {"std_given": "Marie", "std_surname": "Gagnon"}
    General.GENERAL_CONFIG["parish_location"] = "St. Boniface, Manitoba"
    lines = General.GeneralProfile.citation_detail_fields(rec, part, "12", "3", "RM")
    assert lines[0] == "3 _TMPLT"
    assert "4 FIELD" in lines
    assert any(ln == "5 NAME SourceDetailPerson" for ln in lines)
    assert any(ln == "5 VALUE Marie Gagnon" for ln in lines)
    assert not any(ln.startswith("4 TID") for ln in lines)


def test_all_template_names_start_with_bang():
    for tid, tpl in Scrip._SIMPLIFIED_CITATION_TEMPLATES.items():
        assert tpl['name'].startswith('!'), f"TID {tid} name {tpl['name']!r} missing ! prefix"


def test_findagrave_entry_removed():
    assert 10001 not in Scrip._SIMPLIFIED_CITATION_TEMPLATES


def test_non_traditional_master_fields_match_rmst():
    tpl = Scrip._SIMPLIFIED_CITATION_TEMPLATES[10009]
    assert 'Publisher' not in tpl['master_fields']
    assert 'PublishLocation' not in tpl['master_fields']
    assert 'PersonalID' in tpl['detail_fields']


def test_census_detail_fields_include_personal_id():
    assert 'PersonalID' in Scrip._SIMPLIFIED_CITATION_TEMPLATES[10008]['detail_fields']


def test_traditional_master_fields_include_title():
    tpl = Scrip._SIMPLIFIED_CITATION_TEMPLATES[10010]
    assert 'Title' in tpl['master_fields']
    assert 'PersonalID' in tpl['detail_fields']


def test_master_template_fields_match_rmst():
    tpl = Scrip._SIMPLIFIED_CITATION_TEMPLATES[10006]
    for f in ('Author', 'Role', 'BookTitle', 'Subtitle', 'Title'):
        assert f in tpl['master_fields'], f"missing {f}"
    assert 'PersonalID' in tpl['detail_fields']


def test_resolve_scrip_template_id_checks_source_documents_document_type():
    rec = {
        "type_specific_fields": {},
        "source_documents": [{"document_type": "Scrip Certificate"}],
    }
    assert Scrip.resolve_scrip_template_id(rec) == 20004
