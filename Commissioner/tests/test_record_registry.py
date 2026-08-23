import pytest
from pydantic import ValidationError

import Commissioner.record_registry as record_registry
from Commissioner.record_registry import (
    InvalidRoleError,
    UnknownDocumentTypeError,
    UnknownFieldTypeError,
    _build_registry,
    get_document_types,
    get_valid_roles,
    parse_collection,
    validate_participant_extra_fields,
    validate_record_extra_fields,
    validate_role_name,
    validate_collection_softly,
)


def test_discovers_both_real_pmt_files():
    doc_types = get_document_types()
    assert "Parish" in doc_types
    assert "Scrip" in doc_types


def test_parish_has_no_extra_fields():
    record_extra = validate_record_extra_fields("Parish", {})
    assert record_extra.model_dump() == {}
    participant_extra = validate_participant_extra_fields("Parish", {})
    assert participant_extra.model_dump() == {}


def test_scrip_record_extra_fields_validate_and_coerce_types():
    extra = validate_record_extra_fields(
        "Scrip",
        {
            "claim_number": "5473",
            "scrip_amount": "160",
            "scrip_type": "Cash",
        },
    )
    assert extra.claim_number == "5473"  # type: ignore
    assert extra.scrip_amount == "160"  # type: ignore
    assert extra.scrip_type == "Cash"  # type: ignore


def test_scrip_record_extra_rejects_invalid_enum_choice():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="Land"):
        validate_record_extra_fields("Scrip", {"scrip_type": "Currency"})


def test_scrip_participant_extra_fields():
    extra = validate_participant_extra_fields(
        "Scrip", {"marital_status": "Married", "race_or_origin": "Metis"}
    )
    assert extra.marital_status == "Married"  # type: ignore
    assert extra.race_or_origin == "Metis"  # type: ignore


def test_unknown_document_type_raises():
    with pytest.raises(UnknownDocumentTypeError, match="NotARecordType"):
        validate_record_extra_fields("NotARecordType", {})


def test_valid_roles_differ_by_document_type():
    parish_roles = get_valid_roles("Parish")
    scrip_roles = get_valid_roles("Scrip")
    assert "Officiant" in parish_roles
    assert "Claimant" in scrip_roles
    assert "Claimant" not in parish_roles


def test_discovers_census_pmt_file():
    assert "Census" in get_document_types()


def test_census_record_extra_fields_validate():
    extra = validate_record_extra_fields(
        "Census",
        {
            "family_number": "12",
            "enumeration_district": "0042",
            "state": "Minnesota",
        },
    )
    assert extra.family_number == "12"  # type: ignore
    assert extra.enumeration_district == "0042"  # type: ignore
    assert extra.state == "Minnesota"  # type: ignore


def test_census_participant_extra_fields_validate():
    extra = validate_participant_extra_fields(
        "Census", {"line_number": "7", "pid": "MXHY-ABC"}
    )
    assert extra.line_number == "7"  # type: ignore
    assert extra.pid == "MXHY-ABC"  # type: ignore


def test_census_record_extra_fields_include_place_details():
    """Regression: place_details is set by census_schema.normalize_census_pages() (one
    of the loc_key fields carried through from every page) but was never declared here,
    so any record carrying it would fail Commissioner validation with extra_forbidden."""
    extra = validate_record_extra_fields("Census", {"place_details": "Rural Route 2"})
    assert extra.place_details == "Rural Route 2"  # type: ignore


def test_census_participant_extra_fields_include_all_ancestry_fs_mapped_keys():
    """Regression: confirmed live (2026-08-15 Canadian gather) that marital_status alone
    tripped a real Commissioner extra_forbidden error, even though ancestry_census.yaml
    has mapped it since the original Ancestry index-panel-data plan's Task 4. Auditing
    every participant_fields type_specific_fields.* target across both
    ancestry_census.yaml and familysearch_census.yaml (plus census_schema.py's own
    always-added passthrough keys) surfaced four more of the same gap: street,
    married_within_year, birth_month, and alternate_birth_places."""
    extra = validate_participant_extra_fields(
        "Census",
        {
            "street": "Main Street",
            "married_within_year": "Yes",
            "birth_month": "March",
            "marital_status": "Widowed",
            "alternate_birth_places": ["Ontario", "Canada"],
        },
    )
    assert extra.street == "Main Street"  # type: ignore
    assert extra.married_within_year == "Yes"  # type: ignore
    assert extra.birth_month == "March"  # type: ignore
    assert extra.marital_status == "Widowed"  # type: ignore
    assert extra.alternate_birth_places == ["Ontario", "Canada"]  # type: ignore


def test_census_roles_are_restricted_to_family_relationships():
    roles = get_valid_roles("Census")
    assert roles == {
        "Head", "Wife", "Husband", "Son", "Daughter",
        "Father", "Mother", "Father-In-Law", "Mother-In-Law",
    }


def test_census_role_validation_is_open():
    validate_role_name("Census", "Boarder")
    validate_role_name("Census", "Roomer")
    validate_role_name("Census", "Coordinator")


def test_validate_role_name_accepts_known_role():
    validate_role_name("Scrip", "Claimant")
    validate_role_name("Scrip", None)


def test_validate_role_name_rejects_unknown_role():
    with pytest.raises(InvalidRoleError, match="Coordinator"):
        validate_role_name("Scrip", "Coordinator")


def test_undeclared_extra_field_key_is_rejected_not_ignored():
    """extra='forbid': since the validated model is never written back over the caller's
    dict, an undeclared key must fail loudly rather than be silently ignored."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="not_a_real_field"):
        validate_record_extra_fields("Scrip", {"not_a_real_field": "x"})

    with pytest.raises(ValidationError, match="not_a_real_field"):
        validate_participant_extra_fields("Scrip", {"not_a_real_field": "x"})


UNKNOWN_TYPE_PMT = """---
roles:
  "1": {name: "Claimant"}
extra_fields:
  record:
    - {name: bogus_field, type: nonsense}
---
Fixture prompt body.
"""


def test_unknown_field_type_token_raises_at_load_time(tmp_path):
    (tmp_path / "Fixture.pmt").write_text(UNKNOWN_TYPE_PMT, encoding="utf-8")
    with pytest.raises(UnknownFieldTypeError, match="nonsense"):
        _build_registry(tmp_path)


def test_build_registry_accepts_a_valid_fixture_dir(tmp_path):
    """Control for the test above: same fixture with a supported type token loads fine,
    so the failure there is the type token and not the fixture format."""
    (tmp_path / "Fixture.pmt").write_text(
        UNKNOWN_TYPE_PMT.replace("type: nonsense", "type: string"), encoding="utf-8"
    )
    registry = _build_registry(tmp_path)
    assert set(registry) == {"Fixture"}
    assert registry["Fixture"].valid_roles == frozenset({"Claimant"})


OPEN_ROLE_PMT = """---
roles:
  "1": {name: "Head", semantic: primary}
role_validation: open
extra_fields:
  participant:
    - {name: notes, type: dict}
---
Fixture prompt body.
"""


def test_open_role_validation_mode_is_read_from_front_matter(tmp_path):
    (tmp_path / "OpenFixture.pmt").write_text(OPEN_ROLE_PMT, encoding="utf-8")
    registry = _build_registry(tmp_path)
    assert registry["OpenFixture"].role_validation_mode == "open"


def test_closed_is_the_default_role_validation_mode_when_key_absent(tmp_path):
    (tmp_path / "Fixture.pmt").write_text(
        UNKNOWN_TYPE_PMT.replace("type: nonsense", "type: string"), encoding="utf-8"
    )
    registry = _build_registry(tmp_path)
    assert registry["Fixture"].role_validation_mode == "closed"


def test_validate_role_name_is_a_noop_for_open_mode_document_types(tmp_path, monkeypatch):
    (tmp_path / "OpenFixture.pmt").write_text(OPEN_ROLE_PMT, encoding="utf-8")
    fixture_registry = _build_registry(tmp_path)
    monkeypatch.setattr(record_registry, "_REGISTRY", fixture_registry)

    validate_role_name("OpenFixture", "TotallyUnknownRole")
    validate_role_name("OpenFixture", "Head")


def test_validate_role_name_still_rejects_unknown_role_for_closed_mode_document_types(
    tmp_path, monkeypatch
):
    (tmp_path / "Fixture.pmt").write_text(
        UNKNOWN_TYPE_PMT.replace("type: nonsense", "type: string"), encoding="utf-8"
    )
    fixture_registry = _build_registry(tmp_path)
    monkeypatch.setattr(record_registry, "_REGISTRY", fixture_registry)

    with pytest.raises(InvalidRoleError, match="Coordinator"):
        validate_role_name("Fixture", "Coordinator")


def test_dict_field_type_accepts_a_nested_dict_value(tmp_path):
    (tmp_path / "OpenFixture.pmt").write_text(OPEN_ROLE_PMT, encoding="utf-8")
    registry = _build_registry(tmp_path)
    extra = registry["OpenFixture"].participant_extra_model(
        notes={"Race": "W", "Column_9": "Yes"}
    )
    assert extra.notes == {"Race": "W", "Column_9": "Yes"}


SAMPLE_SCRIP_PAYLOAD = {
    "collection_title": "Test Scrip Collection",
    "sheets": [
        {
            "page_id": "page_001",
            "document_metadata": {"source_location": "Manitoba"},
            "records": [
                {
                    "page": "page_001",
                    "record_number": "5473-0-0",
                    "event_type": "Scrip",
                    "review": False,
                    "continues_on_next_image": False,
                    "continues_from_previous_image": False,
                    "type_specific_fields": {
                        "claim_number": "5473",
                        "scrip_amount": "160",
                        "scrip_type": "Cash",
                    },
                    "participants": [
                        {
                            "role_name": "Claimant",
                            "std_given": "Jean",
                            "std_surname": "Gagnon",
                            "is_priest": False,
                            "sex": "M",
                            "review": False,
                            "type_specific_fields": {
                                "marital_status": "Married",
                                "race_or_origin": "Metis",
                            },
                        }
                    ],
                }
            ],
        }
    ],
}


def test_parse_collection_validates_scrip_payload_end_to_end():
    collection = parse_collection(SAMPLE_SCRIP_PAYLOAD, document_type="Scrip")
    record = collection.sheets[0].records[0]
    assert record.type_specific_fields["scrip_type"] == "Cash"
    participant = record.participants[0]
    assert participant.type_specific_fields["race_or_origin"] == "Metis"


def test_parse_collection_rejects_bad_extra_field_type():
    bad_payload = {
        **SAMPLE_SCRIP_PAYLOAD,
        "sheets": [
            {
                **SAMPLE_SCRIP_PAYLOAD["sheets"][0],
                "records": [
                    {
                        **SAMPLE_SCRIP_PAYLOAD["sheets"][0]["records"][0],
                        "type_specific_fields": {"scrip_type": "Currency"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError, match="Land"):
        parse_collection(bad_payload, document_type="Scrip")


def test_validate_collection_softly_accepts_a_valid_collection_and_prints_nothing(capsys):
    validate_collection_softly(SAMPLE_SCRIP_PAYLOAD, "Scrip", "Test Scrip Collection")

    captured = capsys.readouterr()
    assert "[WARN]" not in captured.out


def test_validate_collection_softly_logs_and_does_not_raise_on_bad_shape(capsys):
    bad_payload = {"collection_title": "Bad Collection", "sheets": [{"records": "not-a-list"}]}

    validate_collection_softly(bad_payload, "Scrip", "Bad Collection")

    captured = capsys.readouterr()
    assert "[WARN] Commissioner validation failed for 'Bad Collection'" in captured.out


def test_validate_collection_softly_logs_and_does_not_raise_on_unknown_document_type(capsys):
    validate_collection_softly({"collection_title": "X", "sheets": []}, "NotARecordType", "X Collection")

    captured = capsys.readouterr()
    assert "[WARN] Commissioner validation failed for 'X Collection'" in captured.out
    assert "NotARecordType" in captured.out


def test_parse_collection_leaves_type_specific_fields_exactly_as_given():
    """parse_collection validates type_specific_fields; it must never rewrite them.
    Overwriting with the validation model's dump would inject None for every
    declared-but-absent field - silent data corruption of the caller's payload."""
    payload = {
        **SAMPLE_SCRIP_PAYLOAD,
        "sheets": [
            {
                **SAMPLE_SCRIP_PAYLOAD["sheets"][0],
                "records": [
                    {
                        **SAMPLE_SCRIP_PAYLOAD["sheets"][0]["records"][0],
                        # Only 2 of Scrip's 16 declared record extra fields.
                        "type_specific_fields": {"claim_number": "5473", "scrip_type": "Land"},
                        "participants": [
                            {
                                **SAMPLE_SCRIP_PAYLOAD["sheets"][0]["records"][0]["participants"][0],
                                "type_specific_fields": {"treaty_band": "St. Peter's"},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    collection = parse_collection(payload, document_type="Scrip")
    record = collection.sheets[0].records[0]
    assert record.type_specific_fields == {"claim_number": "5473", "scrip_type": "Land"}
    assert record.participants[0].type_specific_fields == {"treaty_band": "St. Peter's"}


def test_parse_collection_preserves_empty_type_specific_fields_for_parish():
    """Parish.pmt declares zero extra_fields, so an overwrite-with-dump would have been
    invisible here - but every Parish record's dict must still survive untouched."""
    payload = {
        "collection_title": "Test Parish Collection",
        "sheets": [
            {
                "page_id": "page_001",
                "records": [
                    {
                        "page": "page_001",
                        "record_number": "1",
                        "event_type": "Baptism",
                        "type_specific_fields": {},
                        "participants": [
                            {
                                "role_name": "Primary",
                                "std_given": "Jean",
                                "is_priest": False,
                                "sex": "M",
                                "type_specific_fields": {},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    collection = parse_collection(payload, document_type="Parish")
    record = collection.sheets[0].records[0]
    assert record.type_specific_fields == {}
    assert record.participants[0].type_specific_fields == {}


def test_parse_collection_rejects_undeclared_type_specific_field_key():
    payload = {
        **SAMPLE_SCRIP_PAYLOAD,
        "sheets": [
            {
                **SAMPLE_SCRIP_PAYLOAD["sheets"][0],
                "records": [
                    {
                        **SAMPLE_SCRIP_PAYLOAD["sheets"][0]["records"][0],
                        "type_specific_fields": {"claim_number": "5473", "undeclared_key": "x"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError, match="undeclared_key"):
        parse_collection(payload, document_type="Scrip")


def test_parse_collection_rejects_invalid_role_for_document_type():
    bad_payload = {
        **SAMPLE_SCRIP_PAYLOAD,
        "sheets": [
            {
                **SAMPLE_SCRIP_PAYLOAD["sheets"][0],
                "records": [
                    {
                        **SAMPLE_SCRIP_PAYLOAD["sheets"][0]["records"][0],
                        "participants": [
                            {
                                **SAMPLE_SCRIP_PAYLOAD["sheets"][0]["records"][0]["participants"][0],
                                "role_name": "Coordinator",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with pytest.raises(InvalidRoleError, match="Coordinator"):
        parse_collection(bad_payload, document_type="Scrip")


SAMPLE_CENSUS_PAYLOAD = {
    "collection_title": "Test 1900 Census Collection",
    "sheets": [
        {
            "page_id": "12",
            "document_metadata": {
                "source_name": "United States Census (Population Schedule)",
                "source_location": "Minnesota, USA",
            },
            "records": [
                {
                    "page": "12",
                    "record_number": "4",
                    "event_type": "Census (family)",
                    "year": "1900",
                    "event_place": "Township of Example, Example County, Minnesota",
                    "citation_text": "",
                    "citation_details": "",
                    "review": False,
                    "continues_on_next_image": False,
                    "continues_from_previous_image": False,
                    "type_specific_fields": {
                        "family_number": "4",
                        "enumeration_district": "0042",
                        "state": "Minnesota",
                        "county": "Example County",
                    },
                    "participants": [
                        {
                            "role_name": "Head",
                            "std_given": "Baptiste",
                            "std_surname": "Gagnon",
                            "is_priest": False,
                            "sex": "M",
                            "age": "42",
                            "review": False,
                            "type_specific_fields": {"line_number": "17", "pid": "MXHY-ABC"},
                        },
                        {
                            "role_name": "Wife",
                            "std_given": "Marie",
                            "std_surname": "Gagnon",
                            "is_priest": False,
                            "sex": "F",
                            "age": "39",
                            "review": False,
                            "type_specific_fields": {"line_number": "18", "pid": "MXHY-ABD"},
                        },
                        {
                            "role_name": "Son",
                            "std_given": "Louis",
                            "std_surname": "Gagnon",
                            "is_priest": False,
                            "sex": "M",
                            "age": "12",
                            "review": False,
                            "type_specific_fields": {"line_number": "19", "pid": "MXHY-ABE"},
                        },
                    ],
                }
            ],
        }
    ],
}


def test_parse_collection_validates_census_payload_end_to_end():
    collection = parse_collection(SAMPLE_CENSUS_PAYLOAD, document_type="Census")
    record = collection.sheets[0].records[0]
    assert record.type_specific_fields["family_number"] == "4"
    participants = record.participants
    assert participants[0].role_name == "Head"
    assert participants[0].type_specific_fields["pid"] == "MXHY-ABC"
    assert participants[1].role_name == "Wife"
    assert participants[2].role_name == "Son"


def test_parse_collection_accepts_any_role_for_census():
    payload = {
        **SAMPLE_CENSUS_PAYLOAD,
        "sheets": [
            {
                **SAMPLE_CENSUS_PAYLOAD["sheets"][0],
                "records": [
                    {
                        **SAMPLE_CENSUS_PAYLOAD["sheets"][0]["records"][0],
                        "participants": [
                            {
                                **SAMPLE_CENSUS_PAYLOAD["sheets"][0]["records"][0]["participants"][0],
                                "role_name": "Boarder",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    collection = parse_collection(payload, document_type="Census")
    assert collection.sheets[0].records[0].participants[0].role_name == "Boarder"


def test_parse_collection_validates_census_unmapped_dict_field():
    payload = {
        **SAMPLE_CENSUS_PAYLOAD,
        "sheets": [
            {
                **SAMPLE_CENSUS_PAYLOAD["sheets"][0],
                "records": [
                    {
                        **SAMPLE_CENSUS_PAYLOAD["sheets"][0]["records"][0],
                        "participants": [
                            {
                                **SAMPLE_CENSUS_PAYLOAD["sheets"][0]["records"][0]["participants"][0],
                                "type_specific_fields": {
                                    "line_number": "17",
                                    "pid": "MXHY-ABC",
                                    "unmapped": {"Race": "W", "Column_9": "Yes"},
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    collection = parse_collection(payload, document_type="Census")
    participant = collection.sheets[0].records[0].participants[0]
    assert participant.type_specific_fields["unmapped"] == {"Race": "W", "Column_9": "Yes"}


def test_build_empty_sheet_shape():
    sheet = record_registry.build_empty_sheet("abc123.jpg", "jpg")
    assert sheet["page_id"] == "abc123.jpg"
    assert sheet["document_metadata"] == {
        "file_name": "abc123.jpg", "file_type": "jpg", "volume": None,
        "pages": None, "source_name": None, "source_location": None,
    }
    assert len(sheet["records"]) == 1
    assert sheet["records"][0]["participants"] == []
    assert sheet["records"][0]["event_type"] is None


def test_build_empty_sheet_explicit_page_id():
    sheet = record_registry.build_empty_sheet("abc123.jpg", "jpg", page_id="p1")
    assert sheet["page_id"] == "p1"


def test_build_empty_sheet_defaults_page_id_to_file_name():
    sheet = record_registry.build_empty_sheet("abc123.jpg", "jpg")
    assert sheet["page_id"] == "abc123.jpg"


def test_build_empty_sheet_round_trips_through_sheet_validation():
    from Commissioner.models import Sheet
    sheet = Sheet.model_validate(record_registry.build_empty_sheet("abc123.jpg", "jpg"))
    assert sheet.document_metadata.file_name == "abc123.jpg"
    assert sheet.records[0].participants == []


def test_build_empty_sheet_validates_against_commissioner_schema():
    collection = {
        "collection_title": "Test",
        "sheets": [record_registry.build_empty_sheet("abc123.jpg", "jpg")],
    }
    result = parse_collection(collection, "Parish")
    assert result.sheets[0].records[0].participants == []


def test_get_field_remap_parish():
    remap = record_registry.get_field_remap("Parish")
    assert remap["CHURCH_MASTER_DB_NAME"] == "MASTER_DB_NAME"
    # IMAGE_DIR has no user-facing override at all (not env var, not field_remap) - it's
    # always auto-resolved by Archivist/General.py to Media/<record type>, matching
    # Paleographer/Extract.py's own SOURCE_DIR convention. To change it, edit this .pmt
    # file's own name (i.e. rename it), not a setting.
    assert "CHURCH_IMAGE_DIR" not in remap


def test_get_field_remap_scrip():
    remap = record_registry.get_field_remap("Scrip")
    assert remap["SCRIP_MASTER_DB_NAME"] == "MASTER_DB_NAME"
    # See test_get_field_remap_parish's comment - same auto-resolved-only IMAGE_DIR.
    assert "SCRIP_IMAGE_DIR" not in remap


def test_get_field_remap_unknown_document_type_raises():
    with pytest.raises(UnknownDocumentTypeError, match="NotARecordType"):
        record_registry.get_field_remap("NotARecordType")


def test_resolve_generic_setting_prefers_prefixed(monkeypatch):
    monkeypatch.setenv("CHURCH_MASTER_DB_NAME", "parish_register.json")
    monkeypatch.delenv("MASTER_DB_NAME", raising=False)
    assert record_registry.resolve_generic_setting("Parish", "MASTER_DB_NAME") == "parish_register.json"


def test_resolve_generic_setting_falls_back_to_generic(monkeypatch):
    monkeypatch.delenv("CHURCH_MASTER_DB_NAME", raising=False)
    monkeypatch.setenv("MASTER_DB_NAME", "fallback.json")
    assert record_registry.resolve_generic_setting("Parish", "MASTER_DB_NAME") == "fallback.json"


def test_resolve_generic_setting_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("CHURCH_MASTER_DB_NAME", raising=False)
    monkeypatch.delenv("MASTER_DB_NAME", raising=False)
    assert record_registry.resolve_generic_setting("Parish", "MASTER_DB_NAME", "default.json") == "default.json"
