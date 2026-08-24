# noinspection PyUnresolvedReferences
import Scrip
# noinspection PyUnresolvedReferences
import General
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_scrip_profile_dynamic_source_id_has_no_register_prefix():
    profile = Scrip.ScripProfile()
    assert profile.dynamic_source_id("3", General.GeneralRunConfig()) == "@S003@"


def test_scrip_profile_participant_uid_uses_identity_directly_for_primary():
    profile = Scrip.ScripProfile()
    assert profile.participant_uid("SCRIP-5473", "0", 0) == "SCRIP-5473"


def test_scrip_profile_repository_defaults_to_lac():
    profile = Scrip.ScripProfile()
    assert profile.repository_defaults() == ("Library and Archives Canada", "Ottawa, ON")
