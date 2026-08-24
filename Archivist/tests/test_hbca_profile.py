import Archivist as arch_dispatcher
# noinspection PyUnresolvedReferences,PyPep8Naming
import HBCA as HBCA
# noinspection PyUnresolvedReferences
import General
import sys
from pathlib import Path

archivist_dir = Path(__file__).resolve().parents[1]
if str(archivist_dir) not in sys.path:
    sys.path.insert(0, str(archivist_dir))


HBCA_SAMPLE_REC = {
    "page": "1",
    "record_id": "HBCA-ADAMS-GEORGE",
    "year": "1864",
    "event_place": "York Factory",
    "is_derivative": True,
    "type_specific_fields": {
        "employee_name": "George Adams",
        "parish_of_origin": "St. Andrews",
        "birth_date_extracted": "ca. 1796",
        "death_date_extracted": "1864",
        "hbca_references": ["B.239/g/1-4", "A.32/21"],
        "pdf_url": "https://www.gov.mb.ca/chc/archives/hbca/biographical/a/adams_george.pdf",
        "keystone_urls": ["https://pam.minisisinc.com/scripts/mwimain.dll/144/PAM_AUTHORITY/AUTH_DESC_HOLD_EXP/KEY%201234"],  # noqa: E501
    },
    "participants": [
        {
            "role": "0",
            "role_semantic": "primary",
            "std_given": "George",
            "std_surname": "Adams",
            "birth_date": "ABT 1796",
            "birth_place": "St. Andrews",
            "death_date": "1864",
            "death_place": "Red River Settlement",
        }
    ],
}

HBCA_SAMPLE_SHEET = {
    "document_metadata": {
        "media_path": "HBCA/adams_george.pdf",
        "pages": "1",
        "pdf_url": "https://www.gov.mb.ca/chc/archives/hbca/biographical/a/adams_george.pdf",
    },
    "records": [HBCA_SAMPLE_REC],
}

HBCA_SAMPLE_DATA = {
    "record_type_name": "HBCA",
    "sheets": [HBCA_SAMPLE_SHEET],
}


def test_hbca_profile_dynamic_source_id_and_template_id():
    profile = HBCA.HBCAProfile()
    cfg = General.GeneralRunConfig(profile=profile)
    assert profile.dynamic_source_id("1", cfg) == "@S10009@"
    assert profile.citation_template_id(HBCA_SAMPLE_REC, "1") == 10009


def test_hbca_profile_proof_status_proposed_for_bio_sheet():
    profile = HBCA.HBCAProfile()
    # Derivative bio sheet facts are always proposed
    assert profile.citation_proof_status("proven", rec=HBCA_SAMPLE_REC) == "proposed"
    assert profile.citation_proof_status("proposed", rec=HBCA_SAMPLE_REC) == "proposed"

    # Actual found primary source citation is proven
    primary_rec = dict(HBCA_SAMPLE_REC, is_derivative=False, is_primary_source=True)
    assert profile.citation_proof_status("proven", rec=primary_rec) == "proven"


def test_hbca_profile_source_quality_reduced_for_bio_sheet():
    profile = HBCA.HBCAProfile()
    part = HBCA_SAMPLE_REC["participants"][0]  # type: ignore

    # Derivative bio sheet: QUAY 1, _SOUR D, _INFO S, _EVID I
    qual_lines = profile.citation_quality_fields(HBCA_SAMPLE_REC, part, "RM")
    assert any("3 QUAY 1" in line for line in qual_lines)
    assert any("4 _SOUR D" in line for line in qual_lines)
    assert any("4 _INFO S" in line for line in qual_lines)
    assert any("4 _EVID I" in line for line in qual_lines)

    # Primary archival citation: QUAY 3, _SOUR O, _INFO P, _EVID D
    primary_rec = dict(HBCA_SAMPLE_REC, is_derivative=False, is_primary_source=True)
    primary_qual = profile.citation_quality_fields(primary_rec, part, "RM")
    assert any("3 QUAY 3" in line for line in primary_qual)
    assert any("4 _SOUR O" in line for line in primary_qual)
    assert any("4 _INFO P" in line for line in primary_qual)
    assert any("4 _EVID D" in line for line in primary_qual)


def test_hbca_profile_citation_detail_fields_rm():
    profile = HBCA.HBCAProfile()
    part = HBCA_SAMPLE_REC["participants"][0]  # type: ignore
    cfg = General.GeneralRunConfig(profile=profile)
    fields = profile.citation_detail_fields(HBCA_SAMPLE_REC, part, "1", "1", "RM", cfg)
    fields_str = "\n".join(fields)

    assert "NAME Page" in fields_str
    assert "NAME SourceDetailPerson" in fields_str
    assert "VALUE George Adams" in fields_str
    assert "NAME Repository" in fields_str
    assert "Hudson's Bay Company Archives" in fields_str
    assert "NAME URL" in fields_str
    assert "adams_george.pdf" in fields_str
    assert "NAME RefNumber" in fields_str
    assert "B.239/g/1-4" in fields_str


def test_hbca_profile_repository_and_gedcom_output_defaults():
    profile = HBCA.HBCAProfile()
    repo_name, repo_loc = profile.repository_defaults()
    assert "Hudson's Bay Company Archives" in repo_name
    assert "Manitoba" in repo_name
    assert profile.default_gedcom_output_name() == "MasterDB_HBCA.ged"


# noinspection PyUnresolvedReferences
def test_hbca_archivist_dispatcher_resolves_hbca_profile():
    # noinspection PyUnresolvedReferences
    profile = arch_dispatcher.resolve_profile("HBCA")
    assert isinstance(profile, HBCA.HBCAProfile)

    # noinspection PyUnresolvedReferences
    profile_bio = arch_dispatcher.resolve_profile("HBCA_Bio")
    assert isinstance(profile_bio, HBCA.HBCAProfile)


def test_hbca_resolve_source_templates():
    profile = HBCA.HBCAProfile()
    cfg = General.GeneralRunConfig(profile=profile)
    sources = profile.resolve_source_templates(HBCA_SAMPLE_DATA, "RM", cfg)
    sources_str = "\n".join(sources)

    assert "0 @S10009@ SOUR" in sources_str
    assert "Hudson's Bay Company" in sources_str
    assert "Biographical Sheets" in sources_str
    assert "0 _STMPLT" in sources_str
    assert "1 TID 10009" in sources_str


def test_hbca_build_gedcom_end_to_end():
    profile = HBCA.HBCAProfile()
    cfg = General.GeneralRunConfig(profile=profile)
    gedcom_text = General.build_gedcom_from_general(HBCA_SAMPLE_DATA, "RM", cfg)

    assert "0 HEAD" in gedcom_text
    assert "0 @S10009@ SOUR" in gedcom_text
    assert "2 _PROOF proposed" in gedcom_text
    assert "3 QUAY 1" in gedcom_text
    assert "1 NAME George /Adams/" in gedcom_text
    assert "1 BIRT" in gedcom_text
    assert "1 DEAT" in gedcom_text
    assert "0 TRLR" in gedcom_text


def test_citation_detail_fields_include_keystone_metadata_when_present():
    profile = HBCA.HBCAProfile()
    rec = {
        "type_specific_fields": {
            "employee_name": "Charles Adams",
            "hbca_references": ["B.239/k/3"],
            "keystone_records": {
                "B.239/k/3": {
                    "metadata": {
                        "item_description": "Northern Department minutes of council",
                        "date": "1851-1870",
                        "microfilm_no": "1M814",
                    },
                    "record_urls": [
                        "https://pam.minisisinc.com/scripts/mwimain.dll/144/LISTINGS_IMAGES/LISTINGS_DET_IMAGES/SISN%205154?sessionsearch"  # noqa: E501
                    ],
                },
            },
        },
    }
    part = {"std_given": "Charles", "std_surname": "Adams"}
    cfg = General.GeneralRunConfig(profile=profile)
    lines = profile.citation_detail_fields(rec, part, page="adams_charles.pdf", vol="", target_software="RM",
                                           cfg=cfg)
    joined = "\n".join(lines)
    assert "1M814" in joined
    assert "SISN%205154" in joined


def test_citation_detail_fields_degrade_gracefully_without_keystone_records():
    profile = HBCA.HBCAProfile()
    rec = {"type_specific_fields": {"employee_name": "Charles Adams", "hbca_references": ["B.239/k/3"]}}
    part = {"std_given": "Charles", "std_surname": "Adams"}
    cfg = General.GeneralRunConfig(profile=profile)
    lines = profile.citation_detail_fields(rec, part, page="adams_charles.pdf", vol="", target_software="RM",
                                           cfg=cfg)
    assert any("B.239/k/3" in line for line in lines)


def test_hbca_citation_detail_fields_wraps_in_tmplt():
    rec = {"event_place": "Red River", "type_specific_fields": {
        "employee_name": "John Smith", "hbca_references": ["A.32/1"],
    }}
    part = {"std_given": "John", "std_surname": "Smith"}
    lines = HBCA.HBCAProfile.citation_detail_fields(rec, part, "1", "1", "RM", General.GeneralRunConfig())
    assert lines[0] == "3 _TMPLT"
    assert "4 FIELD" in lines
    assert any(ln == "5 NAME SourceDetailPerson" for ln in lines)
    assert not any(ln.startswith("4 TID") for ln in lines)
