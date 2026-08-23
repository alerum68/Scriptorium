import datetime
import hashlib
import os
import re
import xml.etree.ElementTree as etree
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union

import Utils

GENERAL_CONFIG = {
    'volume_num': '',
    'register_source_id': '1',
    'register_name': '',
    'parish_name': 'Parish Name',
    'parish_name_short': 'Parish',
    'parish_location': 'City, State',
    'volume_title': '',
    'date_range_str': '',
    'diocese': '',
    'collection_url': '',
    'collection_name': '',
    'parish_file_name': 'Parish_Export',
    'default_location': '',
    'citation_detail': '',
    'citation_text': '',
    'role_clergy': 'Priest',
    'role_default_witness': 'Witness',
    'clergy_honorific': 'Father',
}

CALL_NUMBER = ""
COLLECTION_URL = ""
COLLECTION_NAME = ""
REPOSITORY = ""
REPOSITORY_LOC = ""
# Default image directory (overridden per-record-type in apply_collection_metadata)
IMAGE_DIR = Utils.safe_path(Utils.GENEALOGY_DIR, os.getenv("MEDIA_DIR", "Media"))


class Profile(Protocol):
    def dynamic_source_id(self, vol_digits: str, rec: Optional[dict] = None) -> str: ...
    def participant_uid(self, identity: str, role: str, occ: int) -> Optional[str]: ...
    def family_uid(self, identity: str) -> Optional[str]: ...
    def citation_title(self, rec: dict, part: dict, tag_name: str, year: str,
                       document_type: Optional[str]) -> str: ...

    def citation_page(self, rec: dict, part: dict, page: str) -> str: ...
    def citation_template_id(self, rec: dict, vol: str) -> Optional[int]: ...
    def citation_proof_status(self, computed_status: str, rec: Optional[dict] = None) -> str: ...
    def citation_quality_fields(self, rec: dict, part: dict, target_software: str) -> List[str]: ...
    def citation_detail_fields(self, rec: dict, part: dict, page: str, vol: str,
                               target_software: str) -> List[str]: ...

    def citation_text_block(self, rec: dict, part: dict, raw_orig: str, raw_trans: str) -> List[str]: ...
    def citation_uses_source_documents(self, rec: dict) -> bool: ...
    def primary_fact_date(self, rec: dict, is_primary: bool) -> str: ...

    def build_primary_event_lines(self, rec: dict, part: dict, event_tag: str, witnesses: List[dict],
                                  vol: str, media_uid: str, target_software: str, resi: str,
                                  alt_names: list, scrip_fact_date: str, raw_event_date: str,
                                  age: str) -> List[str]: ...

    def volume_source_detail_fields(self, v_clause: str) -> List[str]: ...
    def media_caption(self, sheet: dict, vol: str, pages: str) -> str: ...
    def resolve_source_templates(self, json_data: dict, target_software: str) -> List[str]: ...
    def repository_defaults(self) -> Tuple[str, str]: ...
    def default_gedcom_output_name(self) -> Optional[str]: ...


def build_generic_primary_event_lines(rec: dict, part: dict, event_tag: str, witnesses: List[dict],
                                      vol: str, media_uid: str, target_software: str,
                                      alt_names: list, raw_event_date: str, age: str) -> List[str]:
    """Builds the primary-event GEDCOM block for non-Scrip events."""
    event_type = Utils.clean_val(rec.get('event_type'))
    lines = []
    event_value = ""
    if event_tag == 'EVEN':
        extra_fields = rec.get('type_specific_fields') or {}
        value_parts = [f"{k.replace('_', ' ').title()}: {Utils.clean_val(v)}"
                       for k, v in extra_fields.items() if k != 'document_type' and Utils.clean_val(v)]
        event_value = f" {'; '.join(value_parts)}" if value_parts else ""
    lines.append(f"1 {event_tag}{event_value}")
    if event_tag == 'EVEN':
        lines.append(f"2 TYPE {Utils.capitalize_text_string(event_type)}")
    if raw_event_date:
        lines.append(f"2 DATE {Utils.format_gedcom_date(raw_event_date)}")
    lines.append(f"2 PLAC {Utils.clean_place(rec.get('event_place')) or GENERAL_CONFIG['default_location']}")
    if age:
        lines.append(f"2 AGE {age}")
    if alt_names:
        alt_values = ", ".join(Utils.clean_val(a.get('value')) for a in alt_names)
        lines.append(f"2 NOTE Margin note suggests alternate spelling: {alt_values}")
    lines.extend(build_witness_links(rec, witnesses, vol, target_software))
    lines.extend(build_general_citation(rec, part, event_tag, vol, media_uid,
                                        Utils.get_proof_status(raw_event_date), target_software))
    return lines





class GeneralProfile:
    @staticmethod
    def dynamic_source_id(vol_digits: str, _rec: Optional[dict] = None) -> str:
        if GENERAL_CONFIG.get("platform_source_id"):
            return f"@S{GENERAL_CONFIG['platform_source_id']}@"

        base_id = re.sub(r'\D', '', f"{GENERAL_CONFIG.get('register_source_id', '1')}")
        if base_id.endswith('001') and len(base_id) > 1:
            base_id = base_id[:-3]
        return f"@S{base_id or '1'}{vol_digits.zfill(3)}@"

    @staticmethod
    def participant_uid(_identity: str, _role: str, _occ: int) -> Optional[str]:
        return None

    @staticmethod
    def family_uid(_identity: str) -> Optional[str]:
        return None

    @staticmethod
    def citation_title(_rec: dict, part: dict, tag_name: str, year: str,
                       document_type: Optional[str]) -> str:
        std_g = Utils.clean_val(part.get('std_given'))
        std_s = Utils.clean_val(part.get('std_surname'))
        titl = f"3 _TITL {std_s}, {std_g}, {tag_name}, {year}"
        if document_type:
            titl += f" -- {Utils.capitalize_text_string(document_type)}"
        return titl

    # noinspection DuplicatedCode
    @staticmethod
    def citation_page(rec: dict, _part: dict, page: str) -> str:
        rec_id = Utils.clean_val(rec.get('record_id')) or 'Unknown'
        type_fields = rec.get('type_specific_fields') or {}
        claim_num = Utils.clean_val(type_fields.get('claim_number'))
        affdt_num = Utils.clean_val(type_fields.get('affidavit_number'))
        ref_bits = [b for b in (f"Claim {claim_num}" if claim_num else "",
                                f"Affdt {affdt_num}" if affdt_num else "") if b]
        if ref_bits:
            return f"3 PAGE {'; '.join(ref_bits)}, Page {page}"
        return f"3 PAGE Page {page}, Record {rec_id}"

    @staticmethod
    def citation_template_id(_rec: dict, _vol: str) -> Optional[int]:
        return None

    @staticmethod
    def citation_proof_status(computed_status: str, rec: Optional[dict] = None) -> str:
        _ = rec
        return computed_status

    @staticmethod
    def citation_quality_fields(rec: dict, _part: dict, target_software: str) -> List[str]:
        rec_id = Utils.clean_val(rec.get('record_id')) or 'Unknown'
        refn = Utils.clean_val(rec.get('record_number')) or rec_id
        if target_software == "RM":
            return [
                f"3 REFN {refn}",
                "3 QUAY 3", "3 _QUAL", "4 _SOUR O", "4 _INFO P",
                "4 _EVID D"
            ]
        return [f"3 REFN {refn}", "3 QUAY 3"]

    # noinspection DuplicatedCode
    @staticmethod
    def citation_detail_fields(rec: dict, part: dict, page: str, vol: str,
                               target_software: str) -> List[str]:
        _ = vol
        if target_software != "RM":
            return []
        std_g = Utils.clean_val(part.get('std_given'))
        std_s = Utils.clean_val(part.get('std_surname'))
        rec_id = Utils.clean_val(rec.get('record_id')) or 'Unknown'
        type_fields = rec.get('type_specific_fields') or {}
        claim_num = Utils.clean_val(type_fields.get('claim_number'))
        affdt_num = Utils.clean_val(type_fields.get('affidavit_number'))
        ref_bits = [b for b in (f"Claim {claim_num}" if claim_num else "",
                                f"Affdt {affdt_num}" if affdt_num else "") if b]
        person_name = f"{std_g} {std_s}".strip()
        parish_loc = Utils.clean_val(GENERAL_CONFIG.get('parish_location'))
        ref_num_str = ('; '.join(ref_bits) if ref_bits
                       else (f"Record {rec.get('record_number') or rec_id}"
                             if rec_id and rec_id != 'Unknown' else ""))
        parish_detail_fields = [
            ("Page", f"Page {page}" if page and page != 'X' else ""),
            ("SourceDetailPerson", person_name),
            ("Location", parish_loc),
            ("Repository", Utils.clean_val(REPOSITORY)),
            ("URL", Utils.clean_val(COLLECTION_URL)),
            ("Accessed", ""),
            ("RefNumber", ref_num_str),
        ]
        # Bare FIELD tags render Free Form; RM needs them under _TMPLT. No TID here -
        # that's on the master SOUR record only. RM's <...> omission logic only treats
        # a field as blank when it's declared with an empty VALUE, not when it's
        # missing from the list entirely - so every field is always declared.
        field_lines = []
        for f_name, f_val in parish_detail_fields:
            field_lines.extend(["4 FIELD", f"5 NAME {f_name}", f"5 VALUE {f_val}"])
        return ["3 _TMPLT"] + field_lines

    # noinspection DuplicatedCode
    @staticmethod
    def citation_text_block(_rec: dict, _part: dict, raw_orig: str, raw_trans: str) -> List[str]:
        orig_val = Utils.clean_val(raw_orig)
        trans_val = Utils.clean_val(raw_trans)
        norm_orig = re.sub(r'\s+', ' ', orig_val).strip().lower() if orig_val else orig_val
        norm_trans = re.sub(r'\s+', ' ', trans_val).strip().lower() if trans_val else trans_val
        same_text = orig_val and trans_val and norm_orig == norm_trans
        lines = []
        if same_text or not (orig_val and trans_val):
            single_text = Utils.wrap_text(trans_val or orig_val, '4 TEXT')
            if single_text:
                lines.append(single_text)
        else:
            citation_detail_header = GENERAL_CONFIG.get('citation_detail', '')
            if citation_detail_header:
                lines.append(f"4 TEXT {citation_detail_header}")
                trans_text = Utils.wrap_text(trans_val, '5 CONT')
            else:
                trans_text = Utils.wrap_text(trans_val, '4 TEXT')
            if trans_text:
                lines.append(trans_text)

            citation_text_header = GENERAL_CONFIG.get('citation_text', '')
            if citation_text_header:
                lines.append(f"3 NOTE {citation_text_header}")
                orig_text = Utils.wrap_text(orig_val, '4 CONT')
            else:
                orig_text = Utils.wrap_text(orig_val, '3 NOTE')
            if orig_text:
                lines.append(orig_text)
        return lines

    @staticmethod
    def citation_uses_source_documents(_rec: dict) -> bool:
        return True

    @staticmethod
    def primary_fact_date(_rec: dict, _is_primary: bool) -> str:
        return ""

    @staticmethod
    def build_primary_event_lines(rec: dict, part: dict, event_tag: str, witnesses: List[dict],
                                  vol: str, media_uid: str, target_software: str, _resi: str,
                                  alt_names: list, _scrip_fact_date: str, raw_event_date: str,
                                  age: str) -> List[str]:
        return build_generic_primary_event_lines(rec, part, event_tag, witnesses, vol, media_uid,
                                                 target_software, alt_names, raw_event_date, age)

    # noinspection DuplicatedCode
    @staticmethod
    def volume_source_detail_fields(v_clause: str) -> List[str]:
        tid = 10009
        primary_creator = Utils.clean_val(GENERAL_CONFIG.get('parish_name'))
        dept = Utils.clean_val(GENERAL_CONFIG.get('diocese')) or Utils.clean_val(GENERAL_CONFIG.get('parish_location'))
        source_desc = f"{GENERAL_CONFIG.get('register_name', '')}{v_clause}".strip()
        date_str = Utils.clean_val(GENERAL_CONFIG.get('date_range_str'))
        # RM's <...> omission logic only treats a field as blank when it's declared
        # with an empty VALUE, not when it's missing from the list entirely - so
        # every master field the template defines is always declared.
        lines = ["1 _TMPLT", f"2 TID {tid}"]
        lines.extend(["2 FIELD", "3 NAME PrimaryCreator", f"3 VALUE {primary_creator}"])
        lines.extend(["2 FIELD", "3 NAME Department", f"3 VALUE {dept}"])
        lines.extend(["2 FIELD", "3 NAME Date", f"3 VALUE {date_str}"])
        lines.extend(["2 FIELD", "3 NAME SourceDescription", f"3 VALUE {source_desc}"])
        lines.extend(["2 FIELD", "3 NAME Person", "3 VALUE"])
        lines.extend(["2 FIELD", "3 NAME Repository", f"3 VALUE {REPOSITORY}"])
        lines.extend(["2 FIELD", "3 NAME PublishLocation", f"3 VALUE {REPOSITORY_LOC}"])
        return lines

    @staticmethod
    def media_caption(_sheet: dict, vol: str, pages: str) -> str:
        return f"{GENERAL_CONFIG['parish_name_short']} - Vol {vol or 'Unknown'} - Page {pages or 'X'}"

    @staticmethod
    def resolve_source_templates(_json_data: dict, target_software: str) -> List[str]:
        if target_software == "RM":
            return get_source_templates({10009})
        return []

    @staticmethod
    def repository_defaults() -> Tuple[str, str]:
        return "FamilySearch.org", "Granite Mountain, UT"

    @staticmethod
    def default_gedcom_output_name() -> Optional[str]:
        return None


_ACTIVE_PROFILE: Profile = GeneralProfile()


def set_active_profile(profile: Profile) -> None:
    global _ACTIVE_PROFILE
    _ACTIVE_PROFILE = profile


FAMILY_SEMANTICS = ('primary', 'spouse', 'child', 'father', 'mother', 'father_in_law', 'mother_in_law')


def extract_volume(sheet: dict) -> str:
    """Aggressively extracts volume from metadata or sheet root, falling back to global CONFIG."""
    meta = sheet.get('document_metadata', {})
    if isinstance(meta, dict):
        for k, v in meta.items():
            if k.lower() == 'volume' and Utils.clean_val(v):
                return Utils.clean_val(v)

    for k, v in sheet.items():
        if k.lower() == 'volume' and Utils.clean_val(v):
            return Utils.clean_val(v)

    return Utils.clean_val(GENERAL_CONFIG.get('volume_num'))


def get_dynamic_source_id(vol_val: str, rec: Optional[dict] = None) -> str:
    vol_digits = re.sub(r'\D', '', f"{vol_val or '1'}") or '1'
    return _ACTIVE_PROFILE.dynamic_source_id(vol_digits, rec)


def get_by_semantic(rec: dict, semantic: str) -> Optional[dict]:
    """Finds the first participant whose role_semantic matches."""
    for p in rec.get('participants', []):
        if p.get('role_semantic') == semantic:
            return p
    return None


def get_all_by_semantic(rec: dict, semantics: Union[str, Tuple[str, ...]]) -> List[dict]:
    """Like get_by_semantic, but returns every match."""
    wanted = (semantics,) if isinstance(semantics, str) else semantics
    return [p for p in rec.get('participants', []) if p.get('role_semantic') in wanted]


def get_role_name(part: dict) -> str:
    """The display/citation name for a participant's role."""
    if part.get('is_priest'):
        return GENERAL_CONFIG['role_clergy']
    return Utils.capitalize_text_string(part.get('role_name')) or GENERAL_CONFIG['role_default_witness']


def resolve_family_links(rec: dict) -> Dict[str, Any]:
    """Works out this record's family structure purely from which family-position role semantics are present."""
    primary_forms_own_family = bool(get_by_semantic(rec, 'spouse') or get_all_by_semantic(rec, 'child'))
    return {
        'primary_forms_own_family': primary_forms_own_family,
        'main_suffix': "",
        'primary_parents_suffix': "G" if primary_forms_own_family else "",
        'spouse_parents_suffix': "B",
    }


def assign_spouses_by_sex(a: Optional[dict], b: Optional[dict]) -> Tuple[Optional[dict], Optional[dict]]:
    """Returns (husband, wife) for a couple, using each person's own sex field."""
    if a is not None and Utils.clean_val(a.get('sex'))[:1].upper() == 'F' and (
            b is None or Utils.clean_val(b.get('sex'))[:1].upper() != 'F'):
        return b, a
    return a, b


# noinspection DuplicatedCode
def evaluate_task_priority(task_note: str) -> tuple:
    """Evaluates task notes for keywords to assign a priority, color code, and dynamic folder name."""
    task_note_lower = f"{task_note}".lower()

    rules = [
        (["conflict", "error", "mismatch", "discrepancy", "inconsistent", "contradict", "wrong"],
            1, "1", "Data Conflicts & Errors"),
        (["illegible", "unreadable", "hard to read", "faded", "damaged", "torn", "blot", "margin", "ink"],
            1, "1", "Legibility & Record Condition"),
        (["surname", "given name", "blank name", "no name", "unknown name", "alias", "dit name", "spelling",
          "identity"],
            1, "1", "Name & Identity Issues"),
        (["parent", "father", "mother", "sponsor", "godparent", "witness", "spouse", "bride", "groom", "relationship",
          "unknown parents"],
            2, "2", "Relationship & Participant Issues"),
        (["translate", "translation", "latin", "french", "language"],
            2, "2", "Translation Needed"),
        (["date", "year", "month", "day", "invalid", "chronology", "sequence", "estimated", "calculated", "age",
          "born"],
            3, "3", "Date & Chronology Issues"),
    ]

    for keywords, priority, color, folder in rules:
        if any(kw in task_note_lower for kw in keywords):
            return priority, color, folder

    return 3, Utils.REVIEW_COLOR, "General Review"


def generate_uid(rec: dict, part: dict, vol: str) -> str:
    src_id = re.sub(r'\D', '', get_dynamic_source_id(vol))

    link_id = Utils.clean_val((part.get('type_specific_fields') or {}).get('link_id'))
    if link_id:
        numeric_id = int(hashlib.md5(f"link_{link_id}".encode('utf-8')).hexdigest(), 16) % (10 ** 10)
        return f"{src_id}{numeric_id:010d}"

    if part.get('is_priest'):
        std_g = Utils.clean_val(part.get('std_given')).strip().lower()
        std_s = Utils.clean_val(part.get('std_surname')).strip().lower()
        std_name = f"{std_g} {std_s}".strip()
        numeric_id = int(hashlib.md5(f"clergy_{std_name}".encode('utf-8')).hexdigest(), 16) % (10 ** 10)
        return f"CLR{numeric_id:010d}"

    pid = Utils.clean_val(rec.get('lac_pid'))
    rec_id = Utils.clean_val(rec.get('record_id'))
    identity = pid or rec_id
    role = f"{part.get('role_number', '0')}"

    participants = rec.get('participants', [])
    same_role = [i for i, p in enumerate(participants) if f"{p.get('role_number', '0')}" == role]
    pos = next((i for i, p in enumerate(participants) if p is part), None)
    occ = same_role.index(pos) if pos in same_role else 0

    override = _ACTIVE_PROFILE.participant_uid(identity, role, occ)
    if override:
        return override

    unique_string = f"indi_{vol}_{identity}_{role}_{occ}"
    numeric_id = int(hashlib.md5(unique_string.encode('utf-8')).hexdigest(), 16) % (10 ** 10)
    return f"{src_id}{numeric_id:010d}"


def generate_media_uid(meta: dict, vol: str) -> str:
    """Generates a deterministic 10-digit numerical UID for a media file."""
    file_name = Utils.clean_val(meta.get('file_name'))
    image_dir = Utils.clean_val(IMAGE_DIR)

    if file_name:
        if image_dir:
            dir_base = os.path.basename(os.path.normpath(image_dir))
            unique_string = f"{dir_base}\\{file_name}"
        else:
            unique_string = file_name
    else:
        pages = Utils.clean_val(meta.get('pages'))
        unique_string = f"vol_{vol}_pages_{pages}"

    numeric_id = int(hashlib.md5(unique_string.encode('utf-8')).hexdigest(), 16) % (10 ** 10)
    return f"M{numeric_id:010d}"


def generate_media_uid_for_path(file_path: str) -> str:
    """Generates a deterministic media UID from a file path."""
    numeric_id = int(hashlib.md5(Utils.clean_val(file_path).encode('utf-8')).hexdigest(), 16) % (10 ** 10)
    return f"M{numeric_id:010d}"


def generate_media_uid_for_lac_asset(asset_id: str) -> str:
    """Generates a media UID directly from an LAC asset identifier."""
    digits = re.sub(r'\D', '', Utils.clean_val(asset_id))
    return f"M{digits}"


# noinspection DuplicatedCode
def _gedcom_wrapped_lines(level: int, tag: str, text: str, max_len: int = 200) -> List[str]:
    """CONT on each literal newline (paragraph break); CONC within a paragraph past
    max_len - GEDCOM 5.5.1 caps a physical line at 255 bytes."""
    lines: List[str] = []
    first = True
    for para in text.split("\n"):
        chunks = [para[i:i + max_len] for i in range(0, len(para), max_len)] or [""]
        for i, chunk in enumerate(chunks):
            if first:
                lines.append(f"{level} {tag} {chunk}")
                first = False
            elif i == 0:
                lines.append(f"{level + 1} CONT {chunk}")
            else:
                lines.append(f"{level + 1} CONC {chunk}")
    return lines


# noinspection DuplicatedCode
def _rmst_element_to_gedcom(elem: etree.Element) -> List[str]:
    """Emits _STMPLT/FOOTNOTE/BIBLIO/DISPLAY/ISDETAIL/LONGHINT - RM's real tag
    vocabulary, not the _SRCTEMPLATE/FOOT/BIBL/DISP/DETL/LHNT names RM doesn't
    recognize."""
    tid = elem.get("Id", "")
    name = (elem.findtext("Name") or "").strip()
    desc = (elem.findtext("Description") or "").strip()
    cat = (elem.findtext("Category") or "Simplified Citations for Genealogical Sources").strip()
    foot = (elem.findtext("Footnote") or "").strip()
    short = (elem.findtext("ShortFootnote") or "").strip()
    bibl = (elem.findtext("Bibliography") or "").strip()

    lines = ["0 _STMPLT", f"1 TID {tid}"]
    if name:
        lines.append(f"1 NAME {name}")
    if desc:
        lines.extend(_gedcom_wrapped_lines(1, "DESC", desc))
    if cat:
        lines.append(f"1 CAT {cat}")
    if foot:
        lines.extend(_gedcom_wrapped_lines(1, "FOOTNOTE", foot))
    if short:
        lines.extend(_gedcom_wrapped_lines(1, "SHORT", short))
    if bibl:
        lines.extend(_gedcom_wrapped_lines(1, "BIBLIO", bibl))

    for fld in elem.findall("Field"):
        f_type = (fld.findtext("Type") or "Text").strip().upper()
        f_name = (fld.findtext("Name") or "").strip()
        f_disp = (fld.findtext("Display") or "").strip()
        f_hint = (fld.findtext("Hint") or "").strip()
        f_detl = "Y" if (fld.findtext("Detail") or "False").strip().lower() in ("true", "1", "y") else "N"
        f_lhnt = (fld.findtext("LongHint") or "").strip()

        lines.append("1 FIELD")
        if f_name:
            lines.append(f"2 NAME {f_name}")
        if f_disp:
            lines.extend(_gedcom_wrapped_lines(2, "DISPLAY", f_disp))
        if f_hint:
            lines.extend(_gedcom_wrapped_lines(2, "HINT", f_hint))
        if f_lhnt:
            lines.extend(_gedcom_wrapped_lines(2, "LONGHINT", f_lhnt))
        lines.append(f"2 TYPE {f_type}")
        lines.append(f"2 ISDETAIL {f_detl}")
    return lines


_BUILTIN_SOURCE_TEMPLATES: Dict[int, List[str]] = {}


def load_source_template_lines(template_id: int) -> List[str]:
    """Finds and parses the given template ID from any .rmst files in Archivist or DEV,
    or falls back to the embedded _BUILTIN_SOURCE_TEMPLATES."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    archivist_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_dirs = [
        os.path.join(archivist_dir, "Source Templates"),
        os.path.join(base_dir, "DEV", "Source Templates"),
        os.path.join(base_dir, "DEV"),
    ]
    for cdir in candidate_dirs:
        if os.path.isdir(cdir):
            for fname in os.listdir(cdir):
                if fname.endswith(".rmst"):
                    fpath = os.path.join(cdir, fname)
                    try:
                        tree = etree.parse(fpath)
                        root = tree.getroot()
                        elem = root.find(f".//Template[@Id='{template_id}']")
                        if elem is not None:
                            return _rmst_element_to_gedcom(elem)
                    except (etree.ParseError, OSError):
                        continue
    return _BUILTIN_SOURCE_TEMPLATES.get(template_id, [])


def get_source_templates(template_ids_used: set) -> List[str]:
    """Generates 0 _STMPLT GEDCOM blocks for all referenced template IDs."""
    lines = []
    for tid in sorted(template_ids_used):
        t_lines = load_source_template_lines(tid)
        if t_lines:
            lines.extend(t_lines)
    return lines


def generate_fam_uid(rec: dict, vol: str) -> str:
    pid = Utils.clean_val(rec.get('lac_pid'))
    rec_id = Utils.clean_val(rec.get('record_id'))
    identity = pid or rec_id

    override = _ACTIVE_PROFILE.family_uid(identity)
    if override:
        return override

    src_id = re.sub(r'\D', '', get_dynamic_source_id(vol, rec))
    page_str = Utils.clean_val(rec.get('page'))
    unique_string = f"fam_{vol}_{page_str}_{identity}"
    numeric_id = int(hashlib.md5(unique_string.encode('utf-8')).hexdigest(), 16) % (10 ** 10)
    return f"{src_id}{numeric_id:010d}"


def _build_citation_block(rec: dict, part: dict, tag_name: str, vol: str, media_uid: str,
                          proof_status: str, target_software: str, document_type: Optional[str] = None,
                          page: Optional[str] = None, citation_text: Optional[str] = None,
                          citation_details: Optional[str] = None,
                          doc_media_uid: Optional[str] = None) -> str:
    page = Utils.clean_val(page if page is not None else rec.get('page')) or 'X'
    year = Utils.clean_val(rec.get('year')) or 'Unknown'

    titl = _ACTIVE_PROFILE.citation_title(rec, part, tag_name, year, document_type)
    page_line = _ACTIVE_PROFILE.citation_page(rec, part, page)

    template_id = _ACTIVE_PROFILE.citation_template_id(rec, vol)
    sour_id = f"@S{template_id}@" if template_id else get_dynamic_source_id(vol, rec)

    computed_status = proof_status
    try:
        proof_status = _ACTIVE_PROFILE.citation_proof_status(computed_status, rec=rec)
    except TypeError:
        proof_status = _ACTIVE_PROFILE.citation_proof_status(computed_status)

    block = [
        f"2 _PROOF {proof_status}",
        f"2 SOUR {sour_id}",
        titl,
        page_line,
    ]
    block.extend(_ACTIVE_PROFILE.citation_detail_fields(rec, part, page, vol, target_software))
    block.append("3 DATA")

    raw_orig = citation_text if citation_text is not None else rec.get('citation_text')
    raw_trans = citation_details if citation_details is not None else rec.get('citation_details')
    block.extend(_ACTIVE_PROFILE.citation_text_block(rec, part, raw_orig, raw_trans))

    rec_id = Utils.clean_val(rec.get('record_id')) or 'Unknown'
    if hasattr(_ACTIVE_PROFILE, "citation_quality_fields"):
        block.extend(_ACTIVE_PROFILE.citation_quality_fields(rec, part, target_software))
    else:
        refn = Utils.clean_val(rec.get('record_number')) or rec_id
        if target_software == "RM":
            block.extend([
                f"3 REFN {refn}",
                "3 QUAY 3", "3 _QUAL", "4 _SOUR O", "4 _INFO P",
                "4 _EVID D"
            ])
        else:
            block.extend([f"3 REFN {refn}", "3 QUAY 3"])

    person_ark = Utils.clean_val((part.get('type_specific_fields') or {}).get('person_ark'))
    if person_ark:
        record_url = f"https://www.familysearch.org/ark:/61903/1:1:{person_ark}"
        if target_software == "RM":
            block.extend(["3 _WEBTAG", "4 NAME FamilySearch Record", f"4 URL {record_url}"])
        else:
            block.extend([f"3 _LINK {record_url}", f"3 NOTE {record_url}"])

    lac_pid = Utils.clean_val(rec.get('lac_pid'))
    if lac_pid:
        lac_url = f"https://recherche-collection-search.bac-lac.gc.ca/eng/Home/Record?app=fonandcol&IdNumber={lac_pid}"
        if target_software == "RM":
            block.extend(["3 _WEBTAG", "4 NAME LAC Digital Record", f"4 URL {lac_url}"])
        else:
            block.extend([f"3 _LINK {lac_url}", f"3 NOTE {lac_url}"])

    pdf_url = Utils.clean_val((rec.get('type_specific_fields') or {}).get('pdf_url') or rec.get('pdf_url'))
    if pdf_url:
        if target_software == "RM":
            block.extend(["3 _WEBTAG", "4 NAME HBCA Biographical Sheet PDF", f"4 URL {pdf_url}"])
        else:
            block.extend([f"3 _LINK {pdf_url}", f"3 NOTE {pdf_url}"])

    keystone_urls = (rec.get('type_specific_fields') or {}).get('keystone_urls') or []
    for k_url in keystone_urls:
        if k_url:
            if target_software == "RM":
                block.extend(["3 _WEBTAG", "4 NAME Archives of Manitoba Keystone Record", f"4 URL {k_url}"])
            else:
                block.extend([f"3 _LINK {k_url}", f"3 NOTE {k_url}"])

    effective_media_uid = doc_media_uid or media_uid
    if effective_media_uid:
        block.append(f"3 OBJE @{effective_media_uid}@")

    return "\n".join(block)


def build_general_citation(rec: dict, part: dict, tag_name: str, vol: str, media_uid: str,
                           proof_status: str = "proven", target_software: str = "RM") -> List[str]:
    """Constructs the source citation blocks for an event."""
    if not _ACTIVE_PROFILE.citation_uses_source_documents(rec):
        return [_build_citation_block(rec, part, tag_name, vol, media_uid, proof_status, target_software)]

    source_documents = rec.get('source_documents') or []
    if not source_documents:
        return [_build_citation_block(rec, part, tag_name, vol, media_uid, proof_status, target_software)]

    blocks = []
    for doc in source_documents:
        asset_id = doc.get('lac_asset_id')
        media_path = doc.get('media_path')
        if asset_id:
            doc_media_uid = generate_media_uid_for_lac_asset(asset_id)
        elif media_path:
            doc_media_uid = generate_media_uid_for_path(media_path)
        else:
            doc_media_uid = media_uid
        blocks.append(_build_citation_block(
            rec, part, tag_name, vol, media_uid, proof_status, target_software,
            document_type=doc.get('document_type'), page=doc.get('page'),
            citation_text=doc.get('citation_text'),
            citation_details=doc.get('citation_details'),
            doc_media_uid=doc_media_uid,
        ))
    return blocks


def build_custom_fact_lines(fact_name: str, value: str, rec: dict, part: dict, vol: str, media_uid: str,
                            target_software: str, date: str = "", place: str = "") -> List[str]:
    """Renders a RootsMagic custom fact as an EVEN/TYPE block based on FactTypes.json config."""
    entry = Utils.FACT_TYPES.get('person', {}).get(fact_name, {})
    val = Utils.clean_val(value) if entry.get('use_value', True) else ""
    fact_date = Utils.format_gedcom_date(date) if entry.get('use_date') and date else ""
    fact_place = Utils.clean_place(place) if entry.get('use_place') and place else ""

    if not (val or fact_date or fact_place):
        return []

    lines = [f"1 EVEN {val}".rstrip(), f"2 TYPE {fact_name}"]
    if fact_date:
        lines.append(f"2 DATE {fact_date}")
    if fact_place:
        lines.append(f"2 PLAC {fact_place}")
    lines.extend(build_general_citation(rec, part, fact_name, vol, media_uid, target_software=target_software))
    return lines


def build_witness_links(rec: dict, witnesses: List[dict], vol: str, target_software: str) -> List[str]:
    """Builds witness association links (_SHAR for RM, NOTE summary for others)."""
    if not witnesses:
        return []
    if target_software == "RM":
        lines = []
        for p in witnesses:
            lines.append(f"2 _SHAR @I{generate_uid(rec, p, vol)}@\n3 ROLE {get_role_name(p)}")
        return lines

    parts = []
    for p in witnesses:
        name = f"{Utils.clean_val(p.get('std_given'))} {Utils.clean_val(p.get('std_surname'))}".strip()
        role = get_role_name(p)
        parts.append(f"{name} ({role})" if name else role)
    return [f"2 NOTE Witnesses: {', '.join(parts)}"]


def build_individual(uid: str, rec: dict, part: dict, vol: str, media_uid: str, gedcom_date: str,
                     any_part_review: bool, target_software: str) -> tuple:
    """Builds a single individual's INDI block, along with their Task block if flagged for review."""
    event_type = Utils.clean_val(rec.get('event_type'))
    event_tag = Utils.get_event_gedcom_tag(event_type)
    is_family_evt = Utils.is_family_event(event_type)
    is_bap_or_chr = event_tag in ("BAPM", "CHR")
    semantic = part.get('role_semantic')
    is_primary = semantic == 'primary'
    scrip_fact_date = _ACTIVE_PROFILE.primary_fact_date(rec, is_primary)
    is_parent = semantic in ('father', 'mother', 'father_in_law', 'mother_in_law')
    is_deceased = bool(part.get('deceased')) or bool(part.get('is_deceased'))

    std_g = Utils.clean_val(part.get('std_given'))
    std_s = Utils.clean_val(part.get('std_surname'))
    dit_name = re.sub(r'(?i)^dit\s+', '', Utils.clean_val(part.get('dit_name'))).strip()

    sex = Utils.clean_val(part.get('sex'))[:1]
    race = Utils.capitalize_text_string(part.get('race'))
    religion = Utils.capitalize_text_string(part.get('religion'))
    occu = Utils.capitalize_text_string(part.get('occupation'))
    resi = Utils.clean_place(part.get('residence'))

    age = Utils.clean_val(part.get('age'))
    raw_event_date = Utils.clean_val(rec.get('event_date'))
    raw_b = Utils.clean_val(part.get('birth_date'))
    raw_d = Utils.clean_val(part.get('death_date'))

    if not raw_b and age and raw_event_date:
        raw_b = Utils.estimate_birth_from_age(raw_event_date, age)

    if raw_event_date:
        if is_bap_or_chr and is_primary:
            raw_b = raw_b or f"BEF {raw_event_date}"
        elif (not is_bap_or_chr and not is_family_evt and is_primary) or (is_parent and is_deceased):
            raw_d = raw_d or f"BEF {raw_event_date}"

        if not is_bap_or_chr and raw_b and not f"{raw_b}".upper().startswith("CAL "):
            if (f"{raw_event_date}"[:4] in raw_b and any(
                    m in f"{raw_b}".upper() for m in ["BEF", "ABT", "EST", "CAL"])):
                raw_b = ""

    b_date = Utils.format_gedcom_date(raw_b)
    d_date = Utils.format_gedcom_date(raw_d)
    b_place = Utils.clean_place(part.get('birth_place'))
    d_place = Utils.clean_place(part.get('death_place'))

    indi = [f"0 @I{uid}@ INDI"]
    if target_software == "RM":
        indi.append(f"1 _UID {uid}")
    indi.append(f"1 REFN {uid}")

    fsftid = Utils.clean_val((part.get('type_specific_fields') or {}).get('fsftid'))
    if fsftid:
        indi.append(f"1 _FSFTID {fsftid}")
        indi.extend(Utils.weblink_lines(f"https://www.familysearch.org/tree/person/details/{fsftid}",
                                        "FamilySearch Family Tree", target_software))

    apid_record_id = Utils.clean_val((part.get('type_specific_fields') or {}).get('apid'))
    if apid_record_id and Utils.APID_DB:
        indi.append(f"1 _APID 1,{Utils.APID_DB}::{apid_record_id}")

    task_block, needs_review, folder_name = None, False, None

    needs_primary_review = rec.get('review', False) and not any_part_review and is_primary
    if part.get('review', False) or needs_primary_review:
        needs_review = True
        reasons = [r for r in [Utils.clean_val(part.get('review_reason')),
                               Utils.clean_val(rec.get('review_reason'))] if r]
        task_note = "; ".join(reasons) if reasons else "Review requested during extraction."

        priority, color_code, folder_name = evaluate_task_priority(task_note)

        if target_software == "RM":
            indi.extend([f"1 _COLOR {color_code}", f"1 _TASK @T{uid}@"])

            citation_blocks = build_general_citation(rec, part, f"Review {event_tag}", vol, media_uid,
                                                     target_software=target_software)
            raw_cit = [line for block in citation_blocks for line in block.split('\n')]

            task_citation = Utils.dedent_citation_lines(raw_cit, skip_at_level=(2, "_PROOF"))

            weblink = Utils.weblink_lines(Utils.clean_val(GENERAL_CONFIG.get('collection_url')),
                                          GENERAL_CONFIG.get('collection_name', 'Collection Link'), target_software)

            task_lines = [
                f"0 @T{uid}@ _TASK", f"1 DESC {std_s or '[No Surname]'}, {std_g or '[No Given Name]'} "
                f"({Utils.clean_val(rec.get('record_id')) or 'Unknown'}): {task_note}", f"1 REFN {uid}",
                f"1 _LINK @I{uid}@", "1 TYPE 2", f"1 DATE {gedcom_date}",
                f"1 _LDATE {gedcom_date}", f"1 NOTE {task_note}", "1 STAT NEW", f"1 PRTY {priority}",
                f"1 _COLOR {color_code}"
            ]

            task_lines.extend(weblink)
            task_lines.extend(task_citation)
            if media_uid:
                task_lines.extend([f"1 OBJE @{media_uid}@"])
            task_block = "\n".join(task_lines)
        else:
            folder_name = None

    std_s_base = std_s
    if dit_name:
        if re.search(r'\s+dit\s+', std_s, flags=re.IGNORECASE):
            std_s_base = re.split(r'\s+dit\s+', std_s, flags=re.IGNORECASE)[0]
        else:
            std_s_base = std_s.replace(dit_name, "")
    std_s_base = re.sub(r'(?i)\s+dit\b', '', std_s_base).strip()

    indi.append(f"1 NAME {std_g} /{std_s_base}/")
    if Utils.clean_val(part.get('prefix')):
        indi.append(f"2 NPFX {Utils.clean_val(part.get('prefix'))}")
    elif part.get('is_priest'):
        indi.append(f"2 NPFX {GENERAL_CONFIG['clergy_honorific']}")

    if Utils.clean_val(part.get('suffix')):
        indi.append(f"2 NSFX {Utils.clean_val(part.get('suffix'))}")

    indi.extend([f"1 SOUR {Utils.ROOT_SOURCE_ID}", f"2 NAME Researcher: {Utils.RESEARCHER}",
                f"2 _TITL Researcher: {Utils.RESEARCHER}"])

    if dit_name:
        indi.extend([f"1 NAME {std_g} /{std_s_base} dit {dit_name}/", f"1 NAME {std_g} /{dit_name}/"])
        indi.extend(build_custom_fact_lines('dit Name', dit_name, rec, part, vol, media_uid, target_software,
                                            date=scrip_fact_date))

    alt_names = [n for n in (part.get('alternate_names') or []) if Utils.clean_val(n.get('value'))]
    for alt in alt_names:
        alt_giv, alt_sur = Utils.split_full_name(Utils.clean_val(alt.get('value')))
        indi.append(f"1 NAME {alt_giv} /{alt_sur}/")
        indi.extend(build_general_citation(rec, part, 'Alternate Name', vol, media_uid, "proposed", target_software))

    if sex:
        indi.append(f"1 SEX {sex}")
    if race:
        indi.extend(build_custom_fact_lines('Race', race, rec, part, vol, media_uid, target_software))
    if religion:
        reli_lines = [f"1 RELI {religion}"]
        if scrip_fact_date:
            reli_lines.append(f"2 DATE {scrip_fact_date}")
        indi.append("\n".join(reli_lines))
        indi.extend(build_general_citation(rec, part, "RELI", vol, media_uid, target_software=target_software))

    final_occu = GENERAL_CONFIG['role_clergy'] if part.get('is_priest') else occu
    if final_occu:
        occu_lines = [f"1 OCCU {final_occu}"]
        if scrip_fact_date:
            occu_lines.append(f"2 DATE {scrip_fact_date}")
        indi.append("\n".join(occu_lines))
        indi.extend(build_general_citation(rec, part, "OCCU", vol, media_uid, target_software=target_software))

    address = Utils.clean_val(part.get('address'))
    if resi or address:
        resi_lines = ["1 RESI"]
        if scrip_fact_date:
            resi_lines.append(f"2 DATE {scrip_fact_date}")
        if resi:
            resi_lines.append(f"2 PLAC {resi}")
        if address:
            resi_lines.append(f"3 PLAS {address}")
        indi.append("\n".join(resi_lines))
        indi.extend(build_general_citation(rec, part, "RESI", vol, media_uid, target_software=target_software))

    for fact in part.get('facts', []) or []:
        fact_type = Utils.clean_val(fact.get('fact_type', ''))
        if not fact_type:
            continue
        indi.extend(build_custom_fact_lines(fact_type, fact.get('value', ''), rec, part, vol, media_uid,
                                            target_software, date=fact.get('date', ''), place=fact.get('place', '')))

    if b_date or b_place:
        indi.append("1 BIRT")
        if b_date:
            indi.append(f"2 DATE {b_date}")
        if b_place:
            indi.append(f"2 PLAC {b_place}")
        indi.extend(build_general_citation(rec, part, "BIRT", vol, media_uid, Utils.get_proof_status(b_date),
                                           target_software))

    if d_date or d_place:
        indi.append("1 DEAT")
        if d_date:
            indi.append(f"2 DATE {d_date}")
        if d_place:
            indi.append(f"2 PLAC {d_place}")
        if age:
            indi.append(f"2 AGE {age}")
        indi.extend(build_general_citation(rec, part, "DEAT", vol, media_uid, Utils.get_proof_status(d_date),
                                           target_software))

    if is_primary and not is_family_evt:
        witnesses = [p for p in rec.get('participants', [])
                     if p.get('role_semantic') not in FAMILY_SEMANTICS and p.get('role_semantic') != 'commissioner']

        indi.extend(_ACTIVE_PROFILE.build_primary_event_lines(
            rec, part, event_tag, witnesses, vol, media_uid, target_software,
            resi, alt_names, scrip_fact_date, raw_event_date, age))

    if get_by_semantic(rec, 'primary'):
        f_uid = generate_fam_uid(rec, vol)
        links = resolve_family_links(rec)
        has_parents = bool(get_all_by_semantic(rec, ('father', 'mother')))
        has_in_laws = bool(get_all_by_semantic(rec, ('father_in_law', 'mother_in_law')))

        if semantic == 'primary':
            if links['primary_forms_own_family']:
                indi.append(f"1 FAMS @F{f_uid}{links['main_suffix']}@")
                if has_parents:
                    indi.append(f"1 FAMC @F{f_uid}{links['primary_parents_suffix']}@")
            else:
                indi.append(f"1 FAMC @F{f_uid}{links['primary_parents_suffix']}@")
        elif semantic == 'spouse':
            indi.append(f"1 FAMS @F{f_uid}{links['main_suffix']}@")
            if has_in_laws:
                indi.append(f"1 FAMC @F{f_uid}{links['spouse_parents_suffix']}@")
        elif semantic in ('father', 'mother'):
            indi.append(f"1 FAMS @F{f_uid}{links['primary_parents_suffix']}@")
        elif semantic in ('father_in_law', 'mother_in_law'):
            indi.append(f"1 FAMS @F{f_uid}{links['spouse_parents_suffix']}@")
        elif semantic == 'child':
            indi.append(f"1 FAMC @F{f_uid}{links['main_suffix']}@")

    return [line for line in indi if line], task_block, needs_review, folder_name


def build_family(rec: dict, vol: str, media_uid: str, target_software: str) -> list:
    """Builds FAM records for primary's own family and for primary's and spouse's parents."""
    primary = get_by_semantic(rec, 'primary')
    if not primary:
        return []

    spouse = get_by_semantic(rec, 'spouse')
    children = get_all_by_semantic(rec, 'child')
    links = resolve_family_links(rec)
    fam_uid = generate_fam_uid(rec, vol)
    event_type = Utils.clean_val(rec.get('event_type'))

    fams = []

    def add_parent_family(p_a: Optional[dict], p_b: Optional[dict], suffix: str, ch: dict) -> None:
        if not (p_a or p_b):
            return
        p_husb, p_wife = assign_spouses_by_sex(p_a, p_b)
        p_fam = [f"0 @F{fam_uid}{suffix}@ FAM"]
        if p_husb:
            p_fam.append(f"1 HUSB @I{generate_uid(rec, p_husb, vol)}@")
        if p_wife:
            p_fam.append(f"1 WIFE @I{generate_uid(rec, p_wife, vol)}@")
        p_fam.append(f"1 CHIL @I{generate_uid(rec, ch, vol)}@")
        fams.append("\n".join(p_fam))

    parents = get_all_by_semantic(rec, ('father', 'mother'))
    parent_a, parent_b = (parents + [None, None])[:2]

    if links['primary_forms_own_family']:
        husb, wife = assign_spouses_by_sex(primary, spouse)
        main_fam = [f"0 @F{fam_uid}{links['main_suffix']}@ FAM"]
        if husb:
            main_fam.append(f"1 HUSB @I{generate_uid(rec, husb, vol)}@")
        if wife:
            main_fam.append(f"1 WIFE @I{generate_uid(rec, wife, vol)}@")
        for child in children:
            main_fam.append(f"1 CHIL @I{generate_uid(rec, child, vol)}@")

        if Utils.is_family_event(event_type):
            event_tag = Utils.get_event_gedcom_tag(event_type)
            event_date = Utils.format_gedcom_date(Utils.clean_val(rec.get('event_date')))
            main_fam.extend([f"1 {event_tag}", f"2 DATE {event_date}" if event_date else "",
                             f"2 PLAC {Utils.clean_place(rec.get('event_place')) or GENERAL_CONFIG['default_location']}"])  # noqa: E501

            if Utils.clean_val(primary.get('age')):
                slot = "HUSB" if husb is primary else "WIFE"
                main_fam.append(f"2 {slot}\n3 AGE {Utils.clean_val(primary.get('age'))}")
            if spouse and Utils.clean_val(spouse.get('age')):
                slot = "WIFE" if wife is spouse else "HUSB"
                main_fam.append(f"2 {slot}\n3 AGE {Utils.clean_val(spouse.get('age'))}")

            for person in (primary, spouse):
                if person is None:
                    continue
                alt_names = [n for n in (person.get('alternate_names') or []) if Utils.clean_val(n.get('value'))]
                if alt_names:
                    alt_values = ", ".join(Utils.clean_val(a.get('value')) for a in alt_names)
                    who = Utils.clean_val(person.get('std_given'))
                    main_fam.append(f"2 NOTE Margin note suggests alternate spelling for {who}: {alt_values}")

            witnesses = [p for p in rec.get('participants', [])
                         if p.get('role_semantic') not in ('primary', 'spouse')
                         and p.get('role_semantic') != 'commissioner']
            main_fam.extend(build_witness_links(rec, witnesses, vol, target_software))
            main_fam.extend(build_general_citation(rec, primary, event_tag, vol, media_uid,
                                                   Utils.get_proof_status(event_date), target_software))

        fams.append("\n".join([line for line in main_fam if line]))

        add_parent_family(parent_a, parent_b, links['primary_parents_suffix'], primary)
        if spouse:
            in_laws = get_all_by_semantic(rec, ('father_in_law', 'mother_in_law'))
            in_law_a, in_law_b = (in_laws + [None, None])[:2]
            add_parent_family(in_law_a, in_law_b, links['spouse_parents_suffix'], spouse)
    else:
        husb, wife = assign_spouses_by_sex(parent_a, parent_b)
        fam_tag = [f"0 @F{fam_uid}{links['primary_parents_suffix']}@ FAM"]
        if husb:
            fam_tag.append(f"1 HUSB @I{generate_uid(rec, husb, vol)}@")
        if wife:
            fam_tag.append(f"1 WIFE @I{generate_uid(rec, wife, vol)}@")
        fam_tag.append(f"1 CHIL @I{generate_uid(rec, primary, vol)}@")
        fams.append("\n".join(fam_tag))

    return fams


def get_source_root(target_software: str) -> list:
    """Generates the static organization-level source record."""
    return [
        f"0 {Utils.ROOT_SOURCE_ID} SOUR",
        f"1 TITL {Utils.ORG_NAME}", "1 AUTH",
        f"1 PUBL Researcher: {Utils.RESEARCHER}."
    ] + Utils.weblink_lines(Utils.MGS_GROUP_URL, "Facebook Group", target_software) + Utils.weblink_lines(
        Utils.ANCESTRY_GROUP_URL, "Ancestry Group", target_software)


def get_volume_sources(volumes_used: set, target_software: str) -> list:
    """Generates register-specific source records dynamically based on volumes used."""
    loc_full = GENERAL_CONFIG['parish_location']
    sour_lines = []

    for vol in sorted(list(volumes_used)):
        s_id = get_dynamic_source_id(vol)
        v_clause = f", Volume {vol}" if vol else ""
        v_title = f"{GENERAL_CONFIG['volume_title']}{v_clause}"

        if target_software == "RM":
            block = [f"0 {s_id} SOUR",
                     f"1 ABBR {GENERAL_CONFIG['parish_name']} {v_title}",
                     f"1 REFN {s_id.replace('@S', '').replace('@', '')}",
                     f"1 TITL {GENERAL_CONFIG['parish_name']}, , {GENERAL_CONFIG['register_name']}{v_clause} ; "
                     f"{v_title}, {GENERAL_CONFIG['parish_name']}, {loc_full}.",
                     f"1 _SUBQ {GENERAL_CONFIG['parish_name']} - {loc_full}.",
                     f"1 _BIBL {GENERAL_CONFIG['parish_name']}. {v_title}. "
                     f"{GENERAL_CONFIG['parish_name']}, {loc_full}."]

            block.extend(_ACTIVE_PROFILE.volume_source_detail_fields(v_clause))
            block.extend(Utils.weblink_lines(COLLECTION_URL, COLLECTION_NAME, "RM"))
        else:
            block = [f"0 {s_id} SOUR",
                     f"1 TITL {GENERAL_CONFIG['parish_name']}, {v_title}",
                     f"1 AUTH {GENERAL_CONFIG['parish_name']}",
                     f"1 PUBL {REPOSITORY_LOC}: {REPOSITORY}", f"1 REFN {s_id.replace('@S', '').replace('@', '')}"]
            block.extend(Utils.weblink_lines(COLLECTION_URL, COLLECTION_NAME, "FTM"))

        sour_lines.extend(block)

    return sour_lines


def build_gedcom_from_general(json_data: dict, target_software: str) -> str:
    """Main builder orchestrating the conversion from the loaded JSON to a raw GEDCOM string."""
    now = datetime.datetime.now()
    gedcom_date = now.strftime('%d %b %Y').upper()

    ged = [
        "0 HEAD",
        f"1 FILE {GENERAL_CONFIG['parish_file_name']}.ged",
        f"1 SOUR {Utils.SOFTWARE_NAME}",
        f"2 VERS {Utils.SOFTWARE_VERS}",
        f"2 NAME {Utils.SOFTWARE_NAME}",
        f"2 CORP {Utils.SOFTWARE_NAME}, Inc.",
        f"1 DEST {Utils.SOFTWARE_NAME}",
        f"1 DATE {gedcom_date}",
        f"2 TIME {now.strftime('%H:%M:%S')}",
        "1 SUBM @SUBM1@", f"1 COPR Copyright (c) {Utils.COPYRIGHT_START}-{now.year} {Utils.ORG_NAME}. All rights reserved.",  # noqa: E501
        f"1 NOTE {Utils.GEDCOM_NOTE}",
        f"2 CONC {Utils.GEDCOM_CONC}",
        "1 GEDC",
        "2 VERS 5.5.1",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
        "0 @SUBM1@ SUBM",
        f"1 NAME {Utils.RESEARCHER}",
        f"2 ADDR {Utils.SUBM_ADDRESS}",
        f"1 NOTE {Utils.ORG_NAME}"
    ]

    fam_recs, task_recs, media_recs = [], [], []
    printed_indis, printed_media, vols_used = set(), set(), set()
    folder_tasks: Dict[str, List[str]] = {}
    review_count = 0

    for sheet in json_data.get('sheets', []):
        meta = sheet.get('document_metadata', {}) or {}
        vol = extract_volume(sheet)
        vols_used.add(vol)

        media_uid = generate_media_uid(meta, vol)
        file_name = Utils.clean_val(meta.get('file_name'))

        media_title = _ACTIVE_PROFILE.media_caption(sheet, vol, Utils.clean_val(meta.get('pages')))

        if media_uid not in printed_media:
            file_path = Utils.safe_path(IMAGE_DIR, file_name)
            form_type = Utils.clean_val(meta.get('file_type', 'jpg')).lower()
            if form_type == 'jpeg':
                form_type = 'jpg'

            media_block = [f"0 @{media_uid}@ OBJE", f"1 FILE {file_path}",
                           f"2 FORM {form_type}",
                           f"2 TITL {media_title}"]
            media_recs.append("\n".join(media_block))
            printed_media.add(media_uid)

        for rec in sheet.get('records', []):
            any_review = any(p.get('review') for p in rec.get('participants', []))

            for doc in rec.get('source_documents') or []:
                doc_media_path = doc.get('media_path')
                if not doc_media_path:
                    continue
                doc_asset_id = doc.get('lac_asset_id')
                doc_media_uid = (generate_media_uid_for_lac_asset(doc_asset_id) if doc_asset_id
                                 else generate_media_uid_for_path(doc_media_path))
                if doc_media_uid in printed_media:
                    continue
                doc_type_title = Utils.capitalize_text_string(doc.get('document_type')) or 'LAC Digital Object'
                doc_media_block = [f"0 @{doc_media_uid}@ OBJE", f"1 FILE {doc_media_path}",
                                   "2 FORM " + ("pdf" if doc_media_path.lower().endswith(".pdf") else "jpg"),
                                   f"2 TITL {doc_type_title}"]
                media_recs.append("\n".join(doc_media_block))
                printed_media.add(doc_media_uid)

            for part in rec.get('participants', []):
                if part.get('role_semantic') == 'commissioner':
                    continue
                uid = generate_uid(rec, part, vol)
                if uid in printed_indis:
                    continue
                printed_indis.add(uid)

                indi_block, t_block, flagged, folder = build_individual(uid, rec, part, vol, media_uid, gedcom_date,
                                                                        any_review, target_software)

                ged.extend(indi_block)
                if t_block and folder:
                    task_recs.append(t_block)
                    folder_tasks.setdefault(folder, []).append(f"1 _TASK @T{uid}@")
                if flagged:
                    review_count += 1

            fam_recs.extend(build_family(rec, vol, media_uid, target_software))

    ged.extend(fam_recs)
    ged.extend(task_recs)

    for folder, tasks in folder_tasks.items():
        ged.append(f"0 _FOLDER {folder}")
        ged.extend(tasks)

    ged.extend(media_recs)
    ged.extend(get_source_root(target_software))
    ged.extend(get_volume_sources(vols_used, target_software))
    ged.extend(_ACTIVE_PROFILE.resolve_source_templates(json_data, target_software))

    ged.append("0 TRLR")

    if review_count > 0:
        print(f"Flagged {review_count} individual(s) with priority-based Tasks and Folders.")

    return "\n".join(ged)


def apply_collection_metadata(data: dict) -> None:
    global CALL_NUMBER, COLLECTION_URL, COLLECTION_NAME, REPOSITORY, REPOSITORY_LOC, IMAGE_DIR
    cm = data.get("collection_metadata", {})
    if cm:
        CALL_NUMBER = cm.get("CALL_NUMBER", CALL_NUMBER)
        COLLECTION_URL = cm.get("COLLECTION_URL", COLLECTION_URL)
        COLLECTION_NAME = cm.get("COLLECTION_NAME", COLLECTION_NAME)
        REPOSITORY = cm.get("REPOSITORY", REPOSITORY)
        REPOSITORY_LOC = cm.get("REPOSITORY_LOC", REPOSITORY_LOC)

        GENERAL_CONFIG['citation_text'] = cm.get("CITATION_TEXT", GENERAL_CONFIG['citation_text'])
        GENERAL_CONFIG['citation_detail'] = cm.get("CITATION_DETAIL", GENERAL_CONFIG['citation_detail'])
        GENERAL_CONFIG['register_name'] = cm.get("REGISTER_NAME", GENERAL_CONFIG['register_name'])
        GENERAL_CONFIG['register_source_id'] = cm.get("REGISTER_SOURCE_ID", GENERAL_CONFIG['register_source_id'])
        GENERAL_CONFIG['volume_title'] = cm.get("VOLUME_TITLE", GENERAL_CONFIG['volume_title'])
        GENERAL_CONFIG['volume_num'] = cm.get("VOLUME_NUM", GENERAL_CONFIG['volume_num'])

    record_type_name = data.get("record_type_name", "")
    if record_type_name:
        IMAGE_DIR = Utils.safe_path(Utils.GENEALOGY_DIR, os.getenv("MEDIA_DIR", "Media"), record_type_name)


def apply_extracted_parish_name(data: dict) -> None:
    for sheet in data.get("sheets", []):
        source_name = (sheet.get("document_metadata") or {}).get("source_name")
        source_val = str(source_name) if source_name is not None else ""
        if source_val.strip():
            GENERAL_CONFIG["parish_name"] = source_val.strip()
            return

    if not GENERAL_CONFIG.get('parish_name') and data.get('record_type_name') == 'Scrip':
        collection = Utils.clean_val(COLLECTION_NAME) or Utils.clean_val(
            data.get('collection_title')) or 'Scrip Records'
        GENERAL_CONFIG['parish_name'] = Utils.clean_val(REPOSITORY) or 'Library and Archives Canada'
        GENERAL_CONFIG['register_name'] = collection
        GENERAL_CONFIG['volume_title'] = collection
        GENERAL_CONFIG['parish_location'] = Utils.clean_val(REPOSITORY_LOC) or 'Ottawa, ON'


def apply_resolved_source_id(data: dict) -> None:
    explicit = str(GENERAL_CONFIG.get("register_source_id", "")).strip()
    if explicit and explicit != "1":
        return

    citation = data.get("citation") or {}
    cc = citation.get("collection_id")
    cc_val = str(cc) if cc is not None else ""
    if cc_val.strip():
        GENERAL_CONFIG["register_source_id"] = cc_val.strip()
        GENERAL_CONFIG["platform_source_id"] = cc_val.strip()
        return

    apid = Utils.APID_DB or citation.get("apid_db")
    apid_val = str(apid) if apid is not None else ""
    if apid_val.strip():
        GENERAL_CONFIG["register_source_id"] = apid_val.strip()
        GENERAL_CONFIG["platform_source_id"] = apid_val.strip()
        return

    record_type_name = data.get("record_type_name") or "Church"
    collection_name = GENERAL_CONFIG.get("parish_name", "")
    GENERAL_CONFIG["register_source_id"] = str(Utils.resolve_source_id(record_type_name, collection_name))


def run_general_flavor(data: dict, profile: Profile) -> None:
    set_active_profile(profile)
    global REPOSITORY, REPOSITORY_LOC
    apply_collection_metadata(data)

    default_repo, default_repo_loc = profile.repository_defaults()
    REPOSITORY = REPOSITORY or default_repo
    REPOSITORY_LOC = REPOSITORY_LOC or default_repo_loc

    default_output_name = profile.default_gedcom_output_name()
    if (default_output_name and Utils.GEDCOM_OUTPUT_NAME == "Family_Register.ged"
            and not os.getenv("GEDCOM_OUTPUT_NAME", "").strip()):
        Utils.GEDCOM_OUTPUT_NAME = default_output_name

    apply_extracted_parish_name(data)
    apply_resolved_source_id(data)

    for software in Utils.resolve_gedcom_output_targets():
        gedcom_text = build_gedcom_from_general(data, software)
        final_path = Utils.resolve_gedcom_output_path(software)
        final_path.write_text(gedcom_text, encoding="utf-8")
        print(f"Successfully generated {final_path}")
