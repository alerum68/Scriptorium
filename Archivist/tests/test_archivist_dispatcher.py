import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

# noinspection PyUnresolvedReferences
from capture_golden_gedcom import PARISH_FIXTURE, SCRIP_FIXTURE  # noqa: E402
# noinspection PyUnresolvedReferences
import Utils  # noqa: E402
# noinspection PyUnresolvedReferences
import General  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


# noinspection DuplicatedCode
def _regenerate(fixture: dict, target_software: str, profile) -> str:
    cfg = General.GeneralRunConfig(
        profile=profile,
        parish_name='', parish_name_short='', parish_location='', parish_file_name='',
        image_dir=Utils.safe_path("C:/Users/Jason Cole/Documents/Genealogy", "Census"),
    )
    raw = General.build_gedcom_from_general(fixture, target_software, cfg)
    return re.sub(r"1 DATE .*\r?\n2 TIME .*", "1 DATE 07 AUG 2026\n2 TIME 14:11:32", raw)


def test_scrip_rm_matches_golden():
    # noinspection PyUnresolvedReferences
    import Scrip
    actual = _regenerate(SCRIP_FIXTURE, "RM", Scrip.ScripProfile())
    expected = (GOLDEN_DIR / "scrip_rm.ged").read_text(encoding="utf-8")
    assert actual == expected


def test_scrip_ftm_matches_golden():
    # noinspection PyUnresolvedReferences
    import Scrip
    actual = _regenerate(SCRIP_FIXTURE, "FTM", Scrip.ScripProfile())
    expected = (GOLDEN_DIR / "scrip_ftm.ged").read_text(encoding="utf-8")
    assert actual == expected


def test_parish_rm_matches_golden():
    actual = _regenerate(PARISH_FIXTURE, "RM", General.GeneralProfile())
    expected = (GOLDEN_DIR / "parish_rm.ged").read_text(encoding="utf-8")
    assert actual == expected


def test_parish_ftm_matches_golden():
    actual = _regenerate(PARISH_FIXTURE, "FTM", General.GeneralProfile())
    expected = (GOLDEN_DIR / "parish_ftm.ged").read_text(encoding="utf-8")
    assert actual == expected
