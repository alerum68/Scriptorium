"""
End-to-end simulation of Paleographer.py's full pipeline: file discovery -> content-part
building -> prompt/schema assembly -> (fake) AI Assistant call -> post-processing -> master DB
write. No real API call is ever made - google.genai.Client is replaced with a fake that
returns a canned, schema-shaped response - but every other piece (the real Parish.pmt/
Scrip.pmt prompt files, the real FactTypes.json vocabulary, engine.py's schema merging,
postprocess.py's derivation logic, Paleographer.py's own orchestration) runs for real.

This exists because engine.py and postprocess.py already have thorough unit tests for
their own pieces in isolation, but nothing previously exercised Paleographer.py itself or
verified the pieces actually work together end-to-end for either real record type.
"""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

# noinspection PyUnresolvedReferences
import agy_engine
# noinspection PyUnresolvedReferences
import engine


class FakeCaches:
    @staticmethod
    def create(**_kwargs):
        return SimpleNamespace(name="fake-cache-1")

    @staticmethod
    def delete(**_kwargs):
        pass


class FakeModels:
    """fake_page_data is either one dict (returned for every call, the common case) or a
    list of dicts (consumed one per call, in order - for simulating a multi-file run where
    each image needs its own distinct response, e.g. page-continuation tests)."""

    def __init__(self, fake_page_data):
        self.fake_page_data = fake_page_data
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=500,
                                cached_content_token_count=0, thoughts_token_count=0)
        if isinstance(self.fake_page_data, list):
            response_data = self.fake_page_data[len(self.calls) - 1]
        else:
            response_data = self.fake_page_data
        return SimpleNamespace(
            text=json.dumps(response_data),
            usage_metadata=usage,
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
        )


class FakeClient:
    """Stands in for genai.Client so process_one_file_sync's real logic (retry loop,
    JSON parsing, cost tracking, context caching) runs unchanged against a canned
    response instead of a real network call."""
    last_instance = None
    pending_page_data = None

    def __init__(self, *_args, **_kwargs):
        self.caches = FakeCaches()
        self.models = FakeModels(FakeClient.pending_page_data)
        FakeClient.last_instance = self


# Record types' own prefixed .env keys (Parish.pmt/Scrip.pmt's field_remap tables). Other
# test modules (e.g. Archivist's) import Archivist.py with real, unmocked load_dotenv calls,
# which set these for real in the process environment (override=False still sets a key
# that's otherwise unset) and never clean up after themselves - so a real dev machine's own
# Paleographer/.env values (CHURCH_IMAGE_DIR='Parish', etc.) can leak into this test's
# os.environ when the full suite runs in one process. Clearing them here keeps this test
# isolated regardless of what ran earlier in the same pytest session.
_PREFIXED_KEYS_TO_CLEAR = (
    "CHURCH_IMAGE_DIR", "CHURCH_MASTER_DB_NAME", "CHURCH_GEDCOM_NAME", "CHURCH_CALL_NUMBER",
    "CHURCH_COLLECTION_URL", "CHURCH_COLLECTION_NAME", "CHURCH_REPOSITORY", "CHURCH_REPOSITORY_LOC",
    "SCRIP_IMAGE_DIR", "SCRIP_MASTER_DB_NAME", "SCRIP_COLLECTION_NAME",
)


def _media_type_name(env_overrides):
    """TYPE_CFG.name equals the active .pmt file's stem (engine.parse_type_config sets
    name=pmt_path.stem), and Extract.py scans <GENEALOGY_DIR>/Media/<TYPE_CFG.name> for new
    images (SOURCE_DIR). Parish.pmt -> "Parish", Scrip.pmt -> "Scrip" - the fixtures must
    stage their images under the same subfolder the active record type resolves to."""
    record_type = env_overrides.get("PALEOGRAPHER_RECORD_TYPE") or "Parish.pmt"
    return Path(record_type).stem


# noinspection DuplicatedCode
def _import_paleographer_fresh(monkeypatch, tmp_path, env_overrides, fake_page_data, argv=None,
                               image_filenames=("TestFile_00001.jpg",)):
    program_dir = tmp_path / "program"
    json_dir_name = "JSON"
    media_dir = program_dir / "Media" / _media_type_name(env_overrides)
    (program_dir / json_dir_name).mkdir(parents=True)
    media_dir.mkdir(parents=True)

    for name in image_filenames:
        Image.new("RGB", (10, 10), color="white").save(media_dir / name)

    for key in _PREFIXED_KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)

    env = {
        "PROGRAM_DIR": str(program_dir),
        "GENEALOGY_DIR": str(program_dir),
        # Importing engine.py at module top pulls in PDFix/PDFix.py, whose real
        # load_dotenv(override=True) call loads the root .env into the process env -
        # including an absolute MEDIA_DIR ('//ALIENWARE/...'). Extract.py reads
        # MEDIA_DIR verbatim (GENEALOGY_DIR / MEDIA_DIR / TYPE_CFG.name), so pin it to
        # the relative "Media" the fixture stages images under, or SOURCE_DIR would
        # point at the real (empty-for-tests) network media folder instead.
        "MEDIA_DIR": "Media",
        "JSON_DIR": json_dir_name,
        "MASTER_DB_NAME": "master.json",
        "MODEL_NAME": "AI Assistant-test-model",
        "AI_API_KEY": "fake-key-not-used",
        # This whole fixture is specifically for exercising the api engine's FakeClient
        # mock - EXTRACTION_ENGINE's own default is now "agy", so this must be pinned
        # explicitly or every test here would try to make real agy calls instead.
        "EXTRACTION_ENGINE": "api",
        "API_BUDGET": "5.00",
        "COST_PER_1M_INPUT": "0.075",
        "COST_PER_1M_OUTPUT": "0.30",
        "CACHE_DISCOUNT_MULTIPLIER": "0.10",
        "VOLUME_TITLE": "Test Volume",
        "VOLUME_NUM": "1",
    }
    env.update(env_overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Paleographer.py's own load_dotenv(..., override=True) calls would otherwise
    # overwrite the env set above with whatever's in the real project .env files.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    # DEBUG_FILE reads sys.argv[1] at import time - pytest's own argv would otherwise be
    # misread as a debug filename and divert the whole run down the debug-mode path.
    monkeypatch.setattr(sys, "argv", argv or ["Paleographer.py"])

    FakeClient.pending_page_data = fake_page_data
    monkeypatch.setattr("google.genai.Client", FakeClient)

    sys.modules.pop("Extract", None)
    module = importlib.import_module("Extract")
    return module, program_dir / json_dir_name / "master.json"


PARISH_FAKE_RESPONSE = {
    "collection_title": "Test Baptisms",
    "sheets": [{
        "page_id": "p1",
        "document_metadata": {},
        "records": [{
            "record_id": None,
            "page": "12",
            "record_number": "45",
            "event_type": "Baptism",
            "year": "1850",
            "event_date": "December 12, 1850",
            "event_place": None,
            "citation_details": "On December 12, 1850, I baptized Jean, son of Jean Gagne and Marie Notre-Dame.",
            "citation_text": "Le 12 decembre 1850, j'ai baptise Jean, fils de Jean Gagne et Marie Notre-Dame.",
            "review": False,
            "review_reason": None,
            "type_specific_fields": {},
            "participants": [
                {
                    "role_number": None, "role_name": "Primary",
                    "std_given": "Jean", "std_surname": "Gagné",
                    "raw_given": "Jean", "raw_surname": "Gagné",
                    "dit_name": None, "prefix": None, "suffix": None,
                    "sex": "M", "is_priest": False, "age": None,
                    "occupation": None, "race": None, "religion": None, "residence": None,
                    "birth_date": "December 10, 1850", "birth_place": None,
                    "death_date": None, "death_place": None,
                    "review": False, "review_reason": None, "type_specific_fields": {},
                },
                {
                    "role_number": None, "role_name": "Father",
                    "std_given": "Jean", "std_surname": "Gagné",
                    "raw_given": "Jean", "raw_surname": "Gagné",
                    "dit_name": None, "prefix": None, "suffix": None,
                    "sex": "M", "is_priest": False, "age": "30",
                    "occupation": "Farmer", "race": None, "religion": None, "residence": None,
                    "birth_date": None, "birth_place": None, "death_date": None, "death_place": None,
                    "review": False, "review_reason": None, "type_specific_fields": {},
                },
                {
                    "role_number": None, "role_name": "Mother",
                    "std_given": "Marie", "std_surname": "Nôtre-Dame",
                    "raw_given": "Marie", "raw_surname": "Nôtre-Dame",
                    "dit_name": None, "prefix": None, "suffix": None,
                    "sex": "F", "is_priest": False, "age": "28",
                    "occupation": None, "race": None, "religion": None, "residence": None,
                    "birth_date": None, "birth_place": None, "death_date": None, "death_place": None,
                    "review": False, "review_reason": None, "type_specific_fields": {},
                },
                {
                    "role_number": None, "role_name": "Officiant",
                    "std_given": "Alexandre", "std_surname": "Tache",
                    "raw_given": "Alexandre", "raw_surname": "Tache",
                    "dit_name": None, "prefix": None, "suffix": None,
                    "sex": "M", "is_priest": True, "age": None,
                    "occupation": None, "race": None, "religion": None, "residence": None,
                    "birth_date": None, "birth_place": None, "death_date": None, "death_place": None,
                    "review": False, "review_reason": None, "type_specific_fields": {},
                },
            ],
        }],
    }],
}


def test_parish_pipeline_end_to_end(tmp_path, monkeypatch):
    module, master_db_path = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=PARISH_FAKE_RESPONSE,
    )

    module.main()

    assert master_db_path.exists(), "main() should have written the master DB"
    saved = json.loads(master_db_path.read_text(encoding="utf-8"))

    assert saved["total_pages_processed"] == 1
    assert saved["total_spent"] > 0

    sheet = saved["sheets"][0]
    assert sheet["document_metadata"]["file_name"] == "TestFile_00001.jpg"
    assert sheet["document_metadata"]["file_type"] == "JPEG"
    assert sheet["document_metadata"]["volume"] == "1"

    record = sheet["records"][0]
    # Derived from event_type via the real FactTypes.json vocabulary (Baptism -> BAPM).
    assert record["record_id"] == "BAPM-45"
    # LLM's English-language date reading converted to ISO by normalization.parse_date_to_iso_format.
    assert record["event_date"] == "1850-12-12"
    # Parish.pmt's record-level default (event_place was left null by the fake response).
    assert record["event_place"] == "Testville, TS, USA"

    primary, father, mother, officiant = record["participants"]
    assert primary["role_number"] == "1"
    assert father["role_number"] == "2"
    assert mother["role_number"] == "3"
    # New "Officiant" role (added so is_priest has somewhere to attach to) resolves to its
    # own role_number - proving Parish.pmt's roles table and derive_role_numbers agree.
    assert officiant["role_number"] == "9"
    assert officiant["is_priest"] is True
    assert primary["is_priest"] is False

    # Diacritics stripped from std_* fields only - raw_* must stay untouched.
    assert primary["std_surname"] == "Gagne"
    assert primary["raw_surname"] == "Gagné"
    assert mother["std_surname"] == "Notre-Dame"

    assert primary["birth_date"] == "1850-12-10"

    # Primary and Father share the same standardized name -> Jr/Sr suffix derivation.
    assert primary["suffix"] == "Jr"
    assert father["suffix"] == "Sr"

    # Parish.pmt's participant-level default, applied since the fake response left it null.
    assert primary["religion"] == "Roman Catholic"
    assert father["religion"] == "Roman Catholic"
    assert mother["religion"] == "Roman Catholic"


SCRIP_FAKE_RESPONSE = {
    "collection_title": "Test Scrip",
    "sheets": [{
        "page_id": "p1",
        "document_metadata": {},
        "records": [{
            "record_id": None,
            "page": "1",
            "record_number": "7",
            "event_type": "Residence (family)",
            "year": "1876",
            "event_date": "March 3, 1876",
            "event_place": "Manitoba",
            "citation_details": "Scrip application of Louis Riel.",
            "citation_text": "Scrip application of Louis Riel.",
            "review": False,
            "review_reason": None,
            "type_specific_fields": {
                "scrip_number": "1234", "scrip_amount": "$160",
                "claim_basis": "Half-breed", "application_date": "March 3, 1876",
            },
            "participants": [
                {
                    "role_number": None, "role_name": "Claimant",
                    "std_given": "Louis", "std_surname": "Riel",
                    "raw_given": "Louis", "raw_surname": "Riel",
                    "dit_name": None, "prefix": None, "suffix": None,
                    "sex": "M", "is_priest": False, "age": "31",
                    "occupation": None, "race": None, "religion": None, "residence": None,
                    "birth_date": None, "birth_place": None, "death_date": None, "death_place": None,
                    "review": False, "review_reason": None,
                    "type_specific_fields": {"marital_status": "Married", "relationship_to_claimant": "Self"},
                },
            ],
        }],
    }],
}


def test_scrip_pipeline_end_to_end(tmp_path, monkeypatch):
    module, master_db_path = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Scrip.pmt",
            "SCRIP_COLLECTION_NAME": "Test Scrip Collection", "SCRIP_DISTRICT": "Test District",
        },
        fake_page_data=SCRIP_FAKE_RESPONSE,
    )

    module.main()

    saved = json.loads(master_db_path.read_text(encoding="utf-8"))
    record = saved["sheets"][0]["records"][0]

    # Scrip.pmt's extra_fields (record + participant level) must survive the schema
    # merge/round-trip intact, proving build_merged_schema's injected fields actually
    # thread through Paleographer.py's real pipeline rather than only in isolation.
    assert record["type_specific_fields"]["scrip_number"] == "1234"
    assert record["type_specific_fields"]["scrip_amount"] == "$160"
    assert record["type_specific_fields"]["claim_basis"] == "Half-breed"

    claimant = record["participants"][0]
    assert claimant["role_number"] == "1"
    assert claimant["type_specific_fields"]["marital_status"] == "Married"
    assert claimant["type_specific_fields"]["relationship_to_claimant"] == "Self"


def test_build_merged_schema_maps_dict_field_type_to_json_schema_object(tmp_path, monkeypatch):
    """Paleographer.py's own copy of build_merged_schema/inject() (duplicated from
    engine.py) must also translate a `dict` field type to JSON-Schema's `object` - Fix 1
    of the final-review fix brief. Uses a synthetic core schema, same as engine.py's own
    unit test, rather than exercising the full pipeline."""
    module, _ = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=PARISH_FAKE_RESPONSE,
    )

    # noinspection DuplicatedCode
    core = {
        "properties": {
            "sheets": {"items": {"properties": {"records": {"items": {"properties": {
                "type_specific_fields": {"type": "object", "properties": {}},
                "participants": {"items": {"properties": {
                    "type_specific_fields": {"type": "object", "properties": {}}
                }}}
            }}}}}}
        }
    }
    extra_fields = {"participant": [{"name": "unmapped", "type": "dict"}]}

    merged = engine.build_merged_schema(core, extra_fields)

    rec_props = merged["properties"]["sheets"]["items"]["properties"]["records"]["items"]["properties"]
    part_props = rec_props["participants"]["items"]["properties"]
    assert part_props["type_specific_fields"]["properties"]["unmapped"] == {"type": "object", "nullable": True}


def test_build_vocabulary_summary_closed_mode_matches_parish_pmt(tmp_path, monkeypatch):
    """Regression test for Parish.pmt's existing closed-mode phrasing - must stay
    byte-for-byte unchanged from before Fix 3."""
    module, _ = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=PARISH_FAKE_RESPONSE,
    )

    cfg = engine.parse_type_config(engine.resolve_prompt_path("Parish.pmt"))
    assert cfg.role_validation == "closed"

    summary = engine.build_vocabulary_summary(cfg)

    assert "role_name (choose exactly one per participant - where a role's own meaning" in summary
    assert "use one of these when it applies" not in summary


def test_build_vocabulary_summary_open_mode_matches_census_pmt(tmp_path, monkeypatch):
    """Census.pmt declares role_validation: open (Task 2) - Paleographer.py's own prompt
    generator must produce the escape-hatch phrasing, not force-fit a boarder/lodger into
    one of the 9 family roles (Fix 3, the most important finding)."""
    module, _ = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=PARISH_FAKE_RESPONSE,
    )

    cfg = engine.parse_type_config(engine.resolve_prompt_path("Census.pmt"))
    assert cfg.role_validation == "open"

    summary = engine.build_vocabulary_summary(cfg)

    assert "role_name: use one of these when it applies" in summary
    assert "treated as an association, not a family link." in summary
    assert "choose exactly one per participant" not in summary


def test_debug_mode_does_not_write_master_db(tmp_path, monkeypatch):
    """DEBUG_FILE mode (the Toolbox's 'Run Analysis (API)' with a specific file picked)
    must print the extracted JSON without ever touching the master DB on disk."""
    module, master_db_path = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=PARISH_FAKE_RESPONSE,
        argv=["Paleographer.py", "TestFile_00001.jpg"],
    )

    module.main()

    assert not master_db_path.exists(), "DEBUG_FILE mode must never write the master DB"


def _minimal_record(**overrides):
    base = {
        "record_id": None, "page": "1", "record_number": "1",
        "event_type": "Baptism", "year": "1850", "event_date": "December 12, 1850", "event_place": None,
        "citation_details": "On December 12, 1850, I baptized Jean.",
        "citation_text": "Le 12 decembre 1850, j'ai baptise Jean.",
        "review": False, "review_reason": None, "continues_on_next_image": False,
        "continues_from_previous_image": False, "type_specific_fields": {},
        "participants": [{
            "role_number": None, "role_name": "Primary", "std_given": "Jean", "std_surname": "Gagne",
            "raw_given": "Jean", "raw_surname": "Gagne", "dit_name": None, "prefix": None, "suffix": None,
            "sex": "M", "is_priest": False, "age": None, "occupation": None, "race": None, "religion": None,
            "residence": None, "birth_date": None, "birth_place": None, "death_date": None, "death_place": None,
            "review": False, "review_reason": None, "type_specific_fields": {},
        }],
    }
    base.update(overrides)
    return base


def test_page_continuation_merges_across_images_without_duplication(tmp_path, monkeypatch):
    """A record cut off at the bottom of one image and completed at the top of the next
    must end up as ONE record in the master DB, not two - see UNIVERSAL_PROMPT_SUFFIX's
    PAGE CONTINUITY rule, engine.build_continuation_context, and Paleographer.py's
    pop_trailing_cutoff_record/consumed_leading_continuation/reattach_leftover_record."""
    page_1_response = {
        "collection_title": "Test Baptisms",
        "sheets": [{"page_id": "p1", "document_metadata": {}, "records": [
            _minimal_record(record_number="44", citation_text="Le 12 decembre, j'ai baptise",
                            continues_on_next_image=True, review=True, review_reason="Cut off at page end"),
        ]}],
    }
    page_2_response = {
        "collection_title": "Test Baptisms",
        "sheets": [{"page_id": "p2", "document_metadata": {}, "records": [
            _minimal_record(record_number="44",
                            citation_text="Le 12 decembre, j'ai baptise Jean, fils de...",
                            continues_from_previous_image=True),
            _minimal_record(record_number="45"),
        ]}],
    }

    # noinspection DuplicatedCode
    module, master_db_path = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=[page_1_response, page_2_response],
        image_filenames=("TestFile_00001.jpg", "TestFile_00002.jpg"),
    )

    module.main()

    saved = json.loads(master_db_path.read_text(encoding="utf-8"))
    all_records = [r for sheet in saved["sheets"] for r in sheet["records"]]

    assert len(all_records) == 2, f"expected 2 records total (one merged, one new), got {len(all_records)}"
    record_44 = next(r for r in all_records if r["record_number"] == "44")
    assert record_44["citation_text"] == "Le 12 decembre, j'ai baptise Jean, fils de..."
    assert record_44["continues_from_previous_image"] is True


def test_page_continuation_saves_leftover_when_nothing_continues_it(tmp_path, monkeypatch):
    """If the next image's response doesn't claim to continue the pending record, it must
    still be saved (exactly as flagged as before this feature existed), not silently
    dropped."""
    page_1_response = {
        "collection_title": "Test Baptisms",
        "sheets": [{"page_id": "p1", "document_metadata": {}, "records": [
            _minimal_record(record_number="44", continues_on_next_image=True,
                            review=True, review_reason="Cut off at page end"),
        ]}],
    }
    page_2_response = {
        "collection_title": "Test Baptisms",
        "sheets": [{"page_id": "p2", "document_metadata": {}, "records": [
            _minimal_record(record_number="45"),
        ]}],
    }

    # noinspection DuplicatedCode
    module, master_db_path = _import_paleographer_fresh(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_page_data=[page_1_response, page_2_response],
        image_filenames=("TestFile_00001.jpg", "TestFile_00002.jpg"),
    )

    module.main()

    saved = json.loads(master_db_path.read_text(encoding="utf-8"))
    record_numbers = sorted(r["record_number"] for sheet in saved["sheets"] for r in sheet["records"])
    assert record_numbers == ["44", "45"], "the cut-off record must still be saved, not dropped"


# ==========================================
# AGY ENGINE PIPELINE (EXTRACTION_ENGINE=agy)
# ==========================================
# agy_engine.call_agy_extract/rasterize_pdf_to_images are monkeypatched here - the
# subprocess layer itself is already covered by AgyCli/tests/test_agy_client.py
# and Paleographer/tests/test_agy_engine.py. This is about Paleographer.py's own
# orchestration (file classification, finalize_page_data, tag_document_metadata,
# save_master_db, cost accounting) working correctly for the agy engine, for both an
# image and a PDF input, exactly as it's already proven for the api engine above.
# noinspection DuplicatedCode
def _import_paleographer_fresh_agy(monkeypatch, tmp_path, env_overrides, fake_structured_output,
                                   image_filenames=("TestFile_00001.jpg",), pdf_filenames=()):
    program_dir = tmp_path / "program"
    json_dir_name = "JSON"
    media_dir = program_dir / "Media" / _media_type_name(env_overrides)
    (program_dir / json_dir_name).mkdir(parents=True)
    media_dir.mkdir(parents=True)

    for name in image_filenames:
        Image.new("RGB", (10, 10), color="white").save(media_dir / name)
    for name in pdf_filenames:
        # Content is irrelevant - rasterize_pdf_to_images is monkeypatched below, so
        # this is never actually opened/parsed; only its existence/extension matters
        # for list_source_files() to pick it up.
        (media_dir / name).write_bytes(b"%PDF-1.4 fake")

    for key in _PREFIXED_KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)

    env = {
        "PROGRAM_DIR": str(program_dir),
        "GENEALOGY_DIR": str(program_dir),
        # Same MEDIA_DIR pinning as the api fixture - see _import_paleographer_fresh.
        "MEDIA_DIR": "Media",
        "JSON_DIR": json_dir_name,
        "MASTER_DB_NAME": "master.json",
        "MODEL_NAME": "AI Assistant-test-model",
        "AI_API_KEY": "fake-key-not-used",
        "EXTRACTION_ENGINE": "agy",
        "AGY_MODEL_NAME": "gemini-3.1-pro-high",
        "API_BUDGET": "5.00",
        "COST_PER_1M_INPUT": "0.075",
        "COST_PER_1M_OUTPUT": "0.30",
        "CACHE_DISCOUNT_MULTIPLIER": "0.10",
        "VOLUME_TITLE": "Test Volume",
        "VOLUME_NUM": "1",
    }
    env.update(env_overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["Paleographer.py"])

    sys.modules.pop("Extract", None)
    module = importlib.import_module("Extract")

    from AgyCli import agy_client

    # main()'s startup auth check - never make a real subprocess call in a unit test.
    monkeypatch.setattr(agy_client, "check_or_prompt_auth", lambda *a, **k: True)

    # rasterize_pdf_to_images would otherwise really try to open the fake PDF bytes
    # above via pdfplumber - returning a single blank image is enough for this
    # orchestration-level test (rasterization itself is unit-tested separately).
    # Extract.py imports agy_engine as a real sibling module and calls it as
    # agy_engine.rasterize_pdf_to_images(...)/agy_engine.call_agy_extract_chunked(...),
    # so the fakes are patched directly on agy_engine, not on the Extract module.
    monkeypatch.setattr(agy_engine, "rasterize_pdf_to_images",
                        lambda _pdf_path, **_k: [Image.new("RGB", (10, 10), color="white")])

    call_log = []

    def fake_call_agy_extract(images, _schema, _prompt_text, **_kwargs):
        call_log.append({"num_images": len(images)})
        # A real agy call always returns a freshly-parsed dict (json.loads(stdout)) -
        # deep-copy here so tag_document_metadata's in-place mutation on one call can
        # never leak into another call's result the way a shared reference would.
        return agy_client.AgyStructuredResult(
            structured_output=json.loads(json.dumps(fake_structured_output)),
            usage=agy_client.AgyUsage(input_tokens=1000, output_tokens=500, thinking_tokens=100,
                                      cache_read_tokens=0, total_tokens=1600),
        )

    monkeypatch.setattr(agy_engine, "call_agy_extract_chunked", fake_call_agy_extract)

    return module, program_dir / json_dir_name / "master.json", call_log


def test_agy_engine_pipeline_processes_image_and_pdf(tmp_path, monkeypatch):
    module, master_db_path, call_log = _import_paleographer_fresh_agy(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Parish.pmt",
            "PARISH_NAME": "St. Test Parish", "PARISH_CITY": "Testville", "PARISH_STATE": "TS",
            "DEFAULT_EVENT_LOCATION": "Testville, TS, USA",
        },
        fake_structured_output=PARISH_FAKE_RESPONSE,
        image_filenames=("TestFile_00001.jpg",),
        pdf_filenames=("TestFile_00002.pdf",),
    )

    assert module.EXTRACTION_ENGINE == "agy"
    assert module.client is None, "the agy engine must never construct a genai.Client"

    module.main()

    assert len(call_log) == 2, "both the image and the PDF should have gone through call_agy_extract"

    saved = json.loads(master_db_path.read_text(encoding="utf-8"))
    file_names = sorted(sheet["document_metadata"]["file_name"] for sheet in saved["sheets"])
    assert file_names == ["TestFile_00001.jpg", "TestFile_00002.pdf"]

    # Real postprocessing (finalize_page_data/tag_document_metadata) must have run for
    # both files, same as the api engine.
    for sheet in saved["sheets"]:
        assert sheet["document_metadata"]["volume"] == "1"
        record = sheet["records"][0]
        assert record["participants"][0]["std_surname"] == "Gagne", "diacritic-stripping postprocessing must have run"

    # Subscription-covered - must never accumulate a dollar cost.
    assert saved["total_spent"] == 0.0
    assert saved["total_pages_processed"] == 2


def test_agy_engine_stages_multiple_rasterized_pages_for_pdf(tmp_path, monkeypatch):
    """A PDF should be rasterized to multiple images (mocked here as 3) and all of them
    passed to call_agy_extract in one call, not split into separate per-page calls."""
    module, master_db_path, call_log = _import_paleographer_fresh_agy(
        monkeypatch, tmp_path,
        env_overrides={
            "PALEOGRAPHER_RECORD_TYPE": "Scrip.pmt",
            "SCRIP_COLLECTION_NAME": "Test Scrip Collection", "SCRIP_DISTRICT": "Test District",
        },
        fake_structured_output=SCRIP_FAKE_RESPONSE,
        image_filenames=(),
        pdf_filenames=("CaseFile_001.pdf",),
    )

    monkeypatch.setattr(agy_engine, "rasterize_pdf_to_images",
                        lambda pdf_path, **k: [Image.new("RGB", (10, 10), color=c) for c in ("white", "gray", "black")])

    module.main()

    assert len(call_log) == 1
    assert call_log[0]["num_images"] == 3
