# noinspection PyUnresolvedReferences
import Scrip
# noinspection PyUnresolvedReferences
import General
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GENERAL = General.GeneralProfile()
SCRIP = Scrip.ScripProfile()

REC = {"page": "1", "record_id": "SCRIP-5473", "year": "1880",
       "type_specific_fields": {"claim_number": "3126", "affidavit_number": "5473",
                                 "scrip_number": "12761"}}
PART = {"std_given": "Roger", "std_surname": "Letendre", "role_semantic": "primary"}


def test_citation_title_diverges_between_profiles():
    general_titl = GENERAL.citation_title(REC, PART, "EVEN", "1880", None)
    scrip_titl = SCRIP.citation_title(REC, PART, "EVEN", "1880", None)
    assert general_titl != scrip_titl
    assert "Claim: 3126" in scrip_titl
    assert "Letendre, Roger, EVEN, 1880" in general_titl


def test_citation_proof_status_scrip_always_proven():
    assert SCRIP.citation_proof_status("possible") == "proven"
    assert GENERAL.citation_proof_status("possible") == "possible"


def test_citation_uses_source_documents_only_for_general():
    assert GENERAL.citation_uses_source_documents(REC) is True
    assert SCRIP.citation_uses_source_documents(REC) is False


def test_repository_defaults_diverge():
    assert GENERAL.repository_defaults() == ("FamilySearch.org", "Granite Mountain, UT")
    assert SCRIP.repository_defaults() == ("Library and Archives Canada", "Ottawa, ON")


def test_default_gedcom_output_name_only_set_for_scrip():
    assert GENERAL.default_gedcom_output_name() is None
    assert SCRIP.default_gedcom_output_name() == "Scrip.ged"


def test_dynamic_source_id_scrip_has_no_register_prefix():
    cfg = General.GeneralRunConfig(register_source_id='1042')
    assert GENERAL.dynamic_source_id("3", cfg) == "@S1042003@"
    assert SCRIP.dynamic_source_id("3", cfg) == "@S003@"
