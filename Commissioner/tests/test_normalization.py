from Commissioner import normalization

ROLES = {
    "1": {"name": "Primary", "semantic": "primary"},
    "2": {"name": "Father", "semantic": "father"},
    "7": {"name": "Godfather/Witness 1"},
}

EVENT_TYPES = {
    "Baptism": {"code": "7", "id_prefix": "BAPM-"},
}


def test_derive_role_semantic_matches_by_role_number():
    assert normalization.derive_role_semantic("1", ROLES) == "primary"
    assert normalization.derive_role_semantic("2", ROLES) == "father"


def test_derive_role_semantic_none_for_role_without_semantic():
    assert normalization.derive_role_semantic("7", ROLES) is None


def test_derive_role_semantic_none_for_missing_role_number():
    assert normalization.derive_role_semantic(None, ROLES) is None


def test_derive_record_identity_default_does_not_set_type_code():
    record = {"event_type": "Baptism", "record_number": "45"}
    normalization.derive_record_identity(record, EVENT_TYPES)
    assert record["record_id"] == "BAPM-45"
    assert "record_type_code" not in record


def test_derive_record_identity_set_type_code_true_sets_it():
    record = {"event_type": "Baptism", "record_number": "45"}
    normalization.derive_record_identity(record, EVENT_TYPES, set_type_code=True)
    assert record["record_id"] == "BAPM-45"
    assert record["record_type_code"] == "7"


def test_capitalize_text_string_preserves_known_acronym():
    assert normalization.capitalize_text_string("hbc trading post") == "HBC Trading Post"
    assert normalization.capitalize_text_string(None) == ""
    assert normalization.capitalize_text_string("null") == ""
    assert normalization.capitalize_text_string("") == ""


def test_parse_date_to_iso_format_full_date():
    assert normalization.parse_date_to_iso_format("December 12, 1850") == "1850-12-12"


def test_derive_role_number():
    assert normalization.derive_role_number("Primary", ROLES) == "1"
    assert normalization.derive_role_number("Father", ROLES) == "2"
    assert normalization.derive_role_number("Unknown", ROLES) is None


def test_normalize_sex_code():
    assert normalization.normalize_sex_code("Male") == "M"
    assert normalization.normalize_sex_code("female") == "F"
    assert normalization.normalize_sex_code("M") == "M"
    assert normalization.normalize_sex_code("F") == "F"
    assert normalization.normalize_sex_code("unknown") == ""
    assert normalization.normalize_sex_code("unknown", default="U") == "U"
    assert normalization.normalize_sex_code(None) == ""


def test_normalize_enum():
    assert normalization.normalize_enum("yes", ["yes", "no"]) == "yes"
    assert normalization.normalize_enum("YES", ["yes", "no"]) == "yes"
    assert normalization.normalize_enum("maybe", ["yes", "no"], default="no") == "no"
    assert normalization.normalize_enum("MALE", {"male": "M", "female": "F"}) == "M"
    assert normalization.normalize_enum(None, ["yes", "no"]) is None
