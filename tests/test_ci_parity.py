"""TEST-6: CI wiring parity.

Two artifact pairs must never drift apart silently:

1. Commissioner/FactTypes.json <-> Commissioner/models.FACT_DEFINITIONS.
   Archivist/Utils.py builds its runtime FACT_TYPES lookup from the JSON, while
   models.py validates extracted AI output against the pydantic list. A fact present
   in one but not the other either emits GEDCOM the validator rejects, or validates
   facts the emitter cannot render.

2. Paleographer/schema.json <-> Commissioner.models.
   schema.json is the JSON Schema handed to the extraction model; the pydantic models
   validate what comes back. Field/enum drift between the two corrupts either the
   prompt or the parsed payload.
"""

import json
import sys
import typing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("pydantic")

from pydantic import ValidationError  # noqa: E402

from Commissioner import models  # noqa: E402


def _read_json(rel_path: str):
    return json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8"))


# ----------------------------------------------------------- FactTypes <-> models

def test_facttypes_json_matches_fact_definitions():
    raw = _read_json("Commissioner/FactTypes.json")
    assert set(raw) == {"person", "family"}, (
        f"unexpected scopes in FactTypes.json: {sorted(raw)}")

    modeled = {"person": {}, "family": {}}
    for fd in models.FACT_DEFINITIONS:
        modeled[fd.scope.value][fd.name] = fd

    for scope in ("person", "family"):
        json_names = set(raw[scope])
        model_names = set(modeled[scope])
        assert json_names == model_names, (
            f"{scope} scope drift - JSON-only: {sorted(json_names - model_names)}; "
            f"models-only: {sorted(model_names - json_names)}"
        )
        for name, entry in raw[scope].items():
            fd = modeled[scope][name]
            for attr in ("gedcom_tag", "use_value", "use_date", "use_place", "custom"):
                assert entry[attr] == getattr(fd, attr), f"{scope}.{name}.{attr} drifted"
            assert str(entry["code"]) == fd.code, f"{scope}.{name}.code drifted"


def test_fact_validator_accepts_known_and_rejects_unknown():
    models.Fact(fact_type="Birth")  # must not raise
    with pytest.raises(ValidationError) as excinfo:
        models.Fact(fact_type="Definitely Not A Real Fact")
    assert "FACT_DEFINITIONS" in str(excinfo.value)


# ------------------------------------------------------------ schema.json <-> models

SCHEMA_REL = "Paleographer/schema.json"

_SHEETS_ITEMS = ("properties", "sheets", "items")
_RECORDS_ITEMS = _SHEETS_ITEMS + ("properties", "records", "items")
_PARTICIPANTS_ITEMS = _RECORDS_ITEMS + ("properties", "participants", "items")

# (label, model class, path from the schema root to the property dict describing it,
#  model fields allowed to be absent from the schema - the AI-facing schema is a
#  deliberate subset where noted, e.g. Collection's internal-only fields.)
SCHEMA_MODEL_MAP = [
    ("Collection", models.Collection, (),
     frozenset({"record_type_name", "collection_metadata"})),
    ("Sheet", models.Sheet, _SHEETS_ITEMS, frozenset()),
    ("DocumentMetadata", models.DocumentMetadata,
     _SHEETS_ITEMS + ("properties", "document_metadata"), frozenset()),
    ("Record", models.Record, _RECORDS_ITEMS, frozenset()),
    ("Participant", models.Participant, _PARTICIPANTS_ITEMS, frozenset()),
    ("AlternateName", models.AlternateName,
     _PARTICIPANTS_ITEMS + ("properties", "alternate_names", "items"), frozenset()),
    ("Fact", models.Fact,
     _PARTICIPANTS_ITEMS + ("properties", "facts", "items"), frozenset()),
]


def _resolve(schema, path):
    node = schema
    for key in path:
        node = node[key]
    return node


def _required_fields(cls):
    return {name for name, field in cls.model_fields.items() if field.is_required()}


@pytest.mark.parametrize("label,model_cls,path,allowed_missing",
                         SCHEMA_MODEL_MAP,
                         ids=[entry[0] for entry in SCHEMA_MODEL_MAP])
def test_schema_properties_match_model_fields(label, model_cls, path, allowed_missing):
    schema = _read_json(SCHEMA_REL)
    node = _resolve(schema, path)
    schema_props = set(node.get("properties", {}))
    model_fields = set(model_cls.model_fields)

    missing_in_schema = model_fields - schema_props
    unexpected = missing_in_schema - set(allowed_missing)
    assert not unexpected, (
        f"{label}: model field(s) {sorted(unexpected)} missing from schema.json"
    )

    unknown_in_schema = schema_props - model_fields
    assert not unknown_in_schema, (
        f"{label}: schema.json propert(y/ies) {sorted(unknown_in_schema)} "
        f"have no matching field on {label}"
    )

    # Where the schema declares required, it must agree with the model's required set.
    if "required" in node:
        assert set(node["required"]) == _required_fields(model_cls), (
            f"{label}: schema.json 'required' disagrees with the model's required fields"
        )


def _literal_args(annotation):
    ann = annotation
    if typing.get_origin(ann) is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if len(args) == 1:
            ann = args[0]
    if typing.get_origin(ann) is typing.Literal:
        return list(typing.get_args(ann))
    return None


def test_participant_enums_match_schema_enums():
    schema = _read_json(SCHEMA_REL)
    props = _resolve(schema, _PARTICIPANTS_ITEMS)["properties"]
    for field_name in ("sex", "age_unit"):
        expected = _literal_args(models.Participant.model_fields[field_name].annotation)
        assert expected is not None, (
            f"Participant.{field_name} is no longer a Literal on the model")
        actual = props[field_name].get("enum")
        assert actual == expected, (
            f"Participant.{field_name} enum drifted: schema={actual} models={expected}"
        )
