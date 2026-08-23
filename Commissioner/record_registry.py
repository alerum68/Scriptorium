import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Type, Union

import yaml
from pydantic import BaseModel, ConfigDict, create_model

from Commissioner.models import Collection

PMT_DIR = Path(__file__).resolve().parent.parent / "Paleographer" / "prompts"

_PRIMITIVE_TYPE_MAP: Dict[str, type] = {
    "string": str,
    "int": int,
    "float": float,
    "bool": bool,
    "date": date,
    "dict": Dict[str, Any],
    "list": List[Any],
}


class UnknownDocumentTypeError(Exception):
    pass


class UnknownFieldTypeError(Exception):
    pass


class InvalidRoleError(Exception):
    pass


class _DocumentTypeSchema:
    def __init__(
        self,
        record_extra_model: Type[BaseModel],
        participant_extra_model: Type[BaseModel],
        valid_roles: FrozenSet[str],
        role_validation_mode: str,
    ):
        self.record_extra_model = record_extra_model
        self.participant_extra_model = participant_extra_model
        self.valid_roles = valid_roles
        self.role_validation_mode = role_validation_mode


def load_pmt_front_matter(path: Union[str, Path]) -> dict:
    """Reads a .pmt file's YAML front matter. Returns {} if missing, unparseable, or no front matter."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        return {}
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


_load_pmt_front_matter = load_pmt_front_matter


def load_pmt_parts(path: Union[str, Path]) -> Tuple[dict, str]:
    """Splits a .pmt file into its YAML front-matter dict and prose body."""
    p = Path(path)
    if not p.is_file():
        return {}, ""
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return {}, ""
    stripped = raw.lstrip()
    if stripped.startswith("---"):
        parts = stripped.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                fm = {}
            return fm, parts[2]
    return {}, raw


def prompt_search_dirs(
    genealogy_dir: Optional[str] = None,
    prompts_dir: Optional[str] = None,
    program_dir: Optional[str] = None,
) -> List[Path]:
    """.pmt search path, highest priority first:
    1. GENEALOGY_DIR / PROMPTS_DIR (user overrides)
    2. PROGRAM_DIR / Prompts (bundled defaults)
    3. Paleographer/prompts (dev-mode checkout fallback)
    """
    dirs = []
    g_dir = os.getenv("GENEALOGY_DIR", "").strip() if genealogy_dir is None else str(genealogy_dir).strip()
    p_subdir = (os.getenv("PROMPTS_DIR") or "Prompts").strip() if prompts_dir is None else str(prompts_dir).strip()
    if not p_subdir:
        p_subdir = "Prompts"
    if g_dir:
        dirs.append(Path(g_dir) / p_subdir)

    pr_dir = os.getenv("PROGRAM_DIR", "").strip() if program_dir is None else str(program_dir).strip()
    if pr_dir:
        dirs.append(Path(pr_dir) / "Prompts")

    dev_prompts = Path(__file__).resolve().parent.parent / "Paleographer" / "prompts"
    dirs.append(dev_prompts)
    return dirs


def resolve_prompt_path(
    requested_name: str,
    search_dirs: Optional[List[Path]] = None,
    default_type: str = "Parish.pmt",
) -> Path:
    """Finds the .pmt file for the requested record type (case-insensitive, extension
    optional), falling back to default_type if not found."""
    requested = (requested_name or "").strip() or default_type
    if not requested.lower().endswith(".pmt"):
        requested += ".pmt"

    dirs = search_dirs if search_dirs is not None else prompt_search_dirs()
    available: Dict[str, Path] = {}
    for p_dir in reversed(dirs):
        if p_dir.is_dir():
            for p in p_dir.glob("*.pmt"):
                available[p.name.lower()] = p

    match = available.get(requested.lower())
    if match:
        return match

    fallback = available.get(default_type.lower())
    if fallback:
        return fallback

    searched = ", ".join(str(d) for d in dirs)
    raise FileNotFoundError(
        f"Could not find record type '{requested}' or fallback '{default_type}' in any of: {searched}"
    )


def _field_type_for(document_type: str, field: dict) -> Any:
    type_name = field["type"]
    if type_name == "enum":
        from typing import Literal

        choices = tuple(field["choices"])
        return Literal.__getitem__(choices)
    if type_name not in _PRIMITIVE_TYPE_MAP:
        raise UnknownFieldTypeError(
            f"{document_type}: unrecognized field type {type_name!r} for field {field['name']!r}"
        )
    return _PRIMITIVE_TYPE_MAP[type_name]


def _build_extra_model(model_name: str, document_type: str, fields: List[dict]) -> Type[BaseModel]:
    """Builds the validation-only model for a document type's extra fields."""
    field_definitions = {
        field["name"]: (Optional.__getitem__(_field_type_for(document_type, field)), None)
        for field in fields
    }
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **field_definitions)


def _build_registry(pmt_dir: Path = PMT_DIR) -> Dict[str, _DocumentTypeSchema]:
    registry: Dict[str, _DocumentTypeSchema] = {}
    for pmt_path in sorted(pmt_dir.glob("*.pmt")):
        document_type = pmt_path.stem
        front_matter = _load_pmt_front_matter(pmt_path)

        extra_fields = front_matter.get("extra_fields") or {}
        record_fields = extra_fields.get("record", [])
        participant_fields = extra_fields.get("participant", [])

        record_extra_model = _build_extra_model(f"{document_type}RecordExtra", document_type, record_fields)
        participant_extra_model = _build_extra_model(
            f"{document_type}ParticipantExtra", document_type, participant_fields
        )

        roles = front_matter.get("roles") or {}
        valid_roles = frozenset(role["name"] for role in roles.values())

        role_validation_mode = front_matter.get("role_validation", "closed")

        registry[document_type] = _DocumentTypeSchema(
            record_extra_model, participant_extra_model, valid_roles, role_validation_mode
        )
    return registry


_REGISTRY: Dict[str, _DocumentTypeSchema] = _build_registry()


def _get_schema(document_type: str) -> _DocumentTypeSchema:
    try:
        return _REGISTRY[document_type]
    except KeyError:
        raise UnknownDocumentTypeError(
            f"Unknown document_type {document_type!r}; no matching .pmt file found"
        ) from None


def get_document_types() -> List[str]:
    return list(_REGISTRY.keys())


def get_valid_roles(document_type: str) -> FrozenSet[str]:
    return _get_schema(document_type).valid_roles


def validate_record_extra_fields(document_type: str, raw: dict) -> BaseModel:
    return _get_schema(document_type).record_extra_model(**raw)


def validate_participant_extra_fields(document_type: str, raw: dict) -> BaseModel:
    return _get_schema(document_type).participant_extra_model(**raw)


def validate_role_name(document_type: str, role_name: Optional[str]) -> None:
    if role_name is None:
        return
    schema = _get_schema(document_type)
    if schema.role_validation_mode == "open":
        return
    if role_name not in schema.valid_roles:
        raise InvalidRoleError(
            f"{role_name!r} is not a valid role for document_type {document_type!r} "
            f"(valid roles: {sorted(schema.valid_roles)})"
        )


def parse_collection(raw_json: dict, document_type: str) -> Collection:
    _get_schema(document_type)  # raises UnknownDocumentTypeError early if unrecognized

    collection = Collection.model_validate(raw_json)

    for sheet in collection.sheets:
        for record in sheet.records:
            # Validation only - the caller's dict is deliberately left untouched. Replacing
            # it with the model's dump would inject None for every declared-but-absent field
            # and drop anything the document type doesn't declare.
            validate_record_extra_fields(document_type, record.type_specific_fields)

            for participant in record.participants:
                validate_participant_extra_fields(document_type, participant.type_specific_fields)
                validate_role_name(document_type, participant.role_name)

    return collection


def validate_collection_softly(data: dict, document_type: str, label: str) -> None:
    """Runs parse_collection() as a visibility check; validation failures are logged and swallowed."""
    try:
        parse_collection(data, document_type)
    except Exception as e:
        print(f"[WARN] Commissioner validation failed for {label!r}: {e}")


def build_empty_sheet(file_name: str, file_type: str, page_id: Optional[str] = None) -> dict:
    """Builds a placeholder sheet dict to mark an image for AI processing."""
    return {
        "page_id": page_id if page_id is not None else file_name,
        "document_metadata": {
            "file_name": file_name,
            "file_type": file_type,
            "volume": None,
            "pages": None,
            "source_name": None,
            "source_location": None,
        },
        "records": [{
            "record_id": None,
            "page": None,
            "record_number": None,
            "event_type": None,
            "year": None,
            "event_date": None,
            "event_place": None,
            "citation_details": None,
            "citation_text": None,
            "review": False,
            "review_reason": None,
            "continues_on_next_image": False,
            "continues_from_previous_image": False,
            "type_specific_fields": {},
            "participants": [],
        }],
    }


def get_field_remap(document_type: str) -> Dict[str, str]:
    """Returns document_type's own .pmt front matter field_remap table."""
    _get_schema(document_type)  # raises UnknownDocumentTypeError early if unrecognized
    front_matter = _load_pmt_front_matter(PMT_DIR / f"{document_type}.pmt")
    return front_matter.get("field_remap") or {}
