import re
from typing import Dict, List, Optional, Tuple

import General
import Utils

# RootsMagic custom Source Templates (Simplified Citations and Metis Scrip .rmst files).
# Detail=False fields populate the master SOUR record; Detail=True fields populate citations.
_SIMPLIFIED_CITATION_TEMPLATES: Dict[int, Dict[str, object]] = {
    10006: {
        'name': "!Simplified Citations Master Template",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Master Template",
        'source_description': "Master Citation Template",
        'master_fields': ['PrimaryCreator', 'Department', 'Author', 'Role', 'Date',
                          'BookTitle', 'Subtitle', 'Title', 'SourceDescription',
                          'Person', 'Publisher', 'PublishLocation'],
        'detail_fields': ['Page', 'SourceDetailPerson', 'Location', 'CensusED',
                          'HouseholdID', 'Repository', 'URL', 'Accessed', 'RefNumber',
                          'PersonalID'],
    },
    10008: {
        'name': "!Simple Citations: Census Records",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Census Records",
        'source_description': "Census Records and Population Schedules",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription',
                          'Person', 'Publisher', 'PublishLocation'],
        'detail_fields': ['Page', 'SourceDetailPerson', 'Location', 'CensusED',
                          'HouseholdID', 'Repository', 'URL', 'Accessed', 'RefNumber',
                          'PersonalID'],
    },
    10009: {
        'name': "!Simple Citations: Non-traditional",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Non-traditional (Church / Parish / Vital / Cemetery)",
        'source_description': "Church Registers, Vital Records, and Archives",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription', 'Person'],
        'detail_fields': ['Page', 'SourceDetailPerson', 'Location', 'Repository',
                          'URL', 'Accessed', 'RefNumber', 'PersonalID'],
    },
    10010: {
        'name': "!Simple Citations: Traditional",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Traditional (Books / Newspapers / Periodicals)",
        'source_description': "Published Books, Articles, and Periodicals",
        'master_fields': ['Author', 'Role', 'Date', 'BookTitle', 'Subtitle', 'Title',
                          'Publisher', 'PublishLocation'],
        'detail_fields': ['Page', 'SourceDetailPerson', 'Location', 'Repository',
                          'URL', 'Accessed', 'RefNumber', 'PersonalID'],
    },
    20001: {
        'name': "!Simple Citations: Métis Scrip (Manitoba, 1870–1876)",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Métis Scrip: Manitoba (1870–1876)",
        'source_description': "Manitoba Métis Scrip Applications",
        'website_collection': "Manitoba Métis scrip applications",
        'primary_creator': "Department of the Interior",
        'department': "Manitoba Scrip Commission",
        'commission': "Manitoba Scrip Commission",
        'collection': "Department of the Interior fonds, RG 15, Series D-II-8-a",
        'date_range': [(1870, 1876)],
        'date_range_str': "1870–1876",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription', 'Collection', 'Repository'],
        'detail_fields': ['ClaimantName', 'AffidavitNumber', 'Parish', 'ScripType',
                          'Volume', 'Microfilm', 'URL', 'Accessed', 'RefNumber'],
    },
    20002: {
        'name': "!Simple Citations: Métis Scrip (North-West, 1885 & 1900-1901)",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Métis Scrip: North-West (1885 & 1900-1901)",
        'source_description': "North-West Territories Métis Scrip Applications",
        'website_collection': "North-West Territories Métis scrip applications",
        'primary_creator': "Department of the Interior",
        'department': "North-West Half-Breed Claims Commission",
        'commission': "North-West Half-Breed Claims Commission",
        'collection': "Department of the Interior fonds, RG 15, Series D-II-8-c",
        'date_range': [(1885, 1885), (1900, 1901)],
        'date_range_str': "1885 & 1900-1901",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription', 'Collection', 'Repository'],
        'detail_fields': ['ClaimantName', 'ClaimNumber', 'ScripNumber', 'IssueDate',
                          'Location', 'Volume', 'Microfilm', 'URL', 'Accessed', 'RefNumber'],
    },
    20003: {
        'name': "!Simple Citations: Métis Scrip (Treaty 8, 1899-1908)",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Métis Scrip: Treaty 8 (1899-1908)",
        'source_description': "Treaty No. 8 Métis Scrip Applications",
        'website_collection': "Treaty No. 8 Métis scrip applications",
        'primary_creator': "Department of the Interior",
        'department': "Treaty No. 8 Scrip Commission",
        'commission': "Treaty No. 8 Scrip Commission",
        'collection': "Department of the Interior fonds, RG 15, Series D-II-8-i",
        'date_range': [(1899, 1908)],
        'date_range_str': "1899–1908",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription', 'Collection', 'Repository'],
        'detail_fields': ['ClaimantName', 'ClaimNumber', 'ScripAmount', 'ScripNoteNumber',
                          'DeliveryDate', 'DeliveryPlace', 'Volume', 'URL', 'Accessed', 'RefNumber'],
    },
    20004: {
        'name': "!Simple Citations: Métis Scrip (Certificates & Payments)",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Métis Scrip: Certificate",
        'source_description': "Métis Scrip Certificates and Payments",
        'website_collection': "Métis scrip certificates and payments",
        'primary_creator': "Department of the Interior",
        'department': "Scrip Commission",
        'commission': "Scrip Commission",
        'collection': "Department of the Interior fonds, RG 15, Series D-II-8-e/f/j",
        'date_range': [(1870, 1906)],
        'date_range_str': "1870–1906",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription', 'Collection', 'Repository'],
        'detail_fields': ['ClaimantName', 'ScripType', 'CertificateNumber', 'Amount',
                          'IssueDate', 'Volume', 'Microfilm', 'URL', 'Accessed', 'RefNumber'],
    },
    20005: {
        'name': "!Simple Citations: Dominion Land Grants & Patents",
        'category': "Simplified Citations for Genealogical Sources",
        'label': "Land Records: Dominion Land Grant Patent",
        'source_description': "Dominion Lands Patents",
        'website_collection': "Dominion Lands patents",
        'primary_creator': "Department of the Interior",
        'department': "Dominion Lands Branch",
        'commission': "",
        'collection': "Dominion Land Grants, RG 15",
        'date_range': [(1870, 1930)],
        'date_range_str': "1870–1930",
        'master_fields': ['PrimaryCreator', 'Department', 'Date', 'SourceDescription', 'Collection', 'Repository'],
        'detail_fields': ['GranteeName', 'OriginalClaimant', 'LandDescription', 'IssueDate',
                          'Liber', 'Folio', 'Microfilm', 'URL', 'Accessed', 'RefNumber'],
    },
}
_SCRIP_TEMPLATES: Dict[int, Dict[str, object]] = {
    k: v for k, v in _SIMPLIFIED_CITATION_TEMPLATES.items() if k in (20001, 20002, 20003, 20004, 20005)
}


_SCRIP_YEAR_RE = re.compile(r"\b(1[89]\d{2})\b")


def _scrip_record_year(rec: dict) -> Optional[int]:
    """Extracts a 4-digit year from record metadata or date fields."""
    tf = rec.get('type_specific_fields') or {}
    for source in (rec.get('year'), tf.get('application_date'), tf.get('issue_date')):
        match = _SCRIP_YEAR_RE.search(Utils.clean_val(source))
        if match:
            return int(match.group(1))
    return None


def select_scrip_template_id(commission_reference: str, document_type: str,
                             series_code: Optional[str] = None, year: Optional[int] = None) -> Optional[int]:
    """Maps a Scrip source document to one of the 5 custom RootsMagic templates (20001-20005)."""
    doc_l = (document_type or "").lower()
    if "certificate" in doc_l or "receipt" in doc_l:
        return 20004
    if "land grant" in doc_l or "patent" in doc_l:
        return 20005

    series_l = (series_code or "").lower()
    if series_l:
        if "d-ii-8-a" in series_l:
            return 20001
        if "d-ii-8-c" in series_l:
            return 20002
        if "d-ii-8-i" in series_l:
            return 20003
        if any(s in series_l for s in ("d-ii-8-e", "d-ii-8-f", "d-ii-8-j")):
            return 20004

    ref_l = (commission_reference or "").lower()
    if "treaty" in ref_l and ("8" in ref_l or "eight" in ref_l):
        return 20003
    if "manitoba" in ref_l:
        return 20001
    if "north-west" in ref_l or "north west" in ref_l or "half-breed" in ref_l or "half breed" in ref_l:
        return 20002

    if year:
        for tid, tpl in _SCRIP_TEMPLATES.items():
            if any(lo <= year <= hi for lo, hi in tpl['date_range']):
                return tid
    return None


def resolve_scrip_template_id(rec: dict) -> Optional[int]:
    """Resolves the template ID for a single Scrip record."""
    tf = rec.get('type_specific_fields') or {}
    doc_type = tf.get('document_type')
    if not doc_type:
        for doc in rec.get('source_documents') or []:
            doc_type = doc.get('document_type')
            if doc_type:
                break
    return select_scrip_template_id(
        tf.get('commission_reference'), doc_type, tf.get('rg_series_code'), _scrip_record_year(rec)
    )


def _scrip_template_field_value(field_name: str, rec: dict, part: dict, vol: str) -> str:
    """Resolves a Detail=True field value for a Métis Scrip template citation."""
    tf = rec.get('type_specific_fields') or {}
    name = f"{Utils.clean_val(part.get('std_given'))} {Utils.clean_val(part.get('std_surname'))}".strip()

    if field_name in ('ClaimantName', 'GranteeName'):
        return name
    if field_name == 'OriginalClaimant':
        return Utils.clean_val(tf.get('original_claimant'))
    if field_name == 'AffidavitNumber':
        return Utils.clean_val(tf.get('affidavit_number'))
    if field_name == 'ClaimNumber':
        return Utils.clean_val(tf.get('claim_number'))
    if field_name in ('AllotmentNumber', 'Allotment'):
        return Utils.clean_val(tf.get('allotment_number'))
    if field_name in ('ScripNumber', 'ScripNoteNumber', 'CertificateNumber'):
        return Utils.clean_val(tf.get('scrip_number'))
    if field_name in ('ScripAmount', 'Amount'):
        return Utils.clean_val(tf.get('scrip_amount'))
    if field_name == 'ScripType':
        return Utils.clean_val(tf.get('claim_basis'))
    if field_name == 'IssueDate':
        return Utils.clean_val(tf.get('issue_date')) or Utils.clean_val(tf.get('application_date'))
    if field_name == 'DeliveryDate':
        return Utils.clean_val(tf.get('delivery_date'))
    if field_name == 'DeliveryPlace':
        return Utils.clean_val(tf.get('delivery_place'))
    if field_name == 'Microfilm':
        return Utils.clean_val(tf.get('reel_numbers'))
    if field_name in ('Parish', 'Location'):
        return Utils.clean_val(part.get('residence')) or Utils.clean_val(part.get('birth_place'))
    if field_name == 'LandDescription':
        return Utils.clean_val(tf.get('land_description'))
    if field_name == 'Liber':
        return Utils.clean_val(tf.get('liber'))
    if field_name == 'Folio':
        return Utils.clean_val(tf.get('folio'))
    if field_name == 'Volume':
        return Utils.clean_val(vol)
    if field_name == 'URL':
        pid = Utils.clean_val(rec.get('lac_pid'))
        return (f"https://recherche-collection-search.bac-lac.gc.ca/eng/Home/Record"
                f"?app=fonandcol&IdNumber={pid}") if pid else ""
    if field_name == 'Accessed':
        return Utils.clean_val(tf.get('accessed'))
    if field_name == 'RefNumber':
        pid = Utils.clean_val(rec.get('lac_pid'))
        return f"Item ID {pid}" if pid else ""
    return ""


def get_scrip_citation_fields(template_id: int, rec: dict, part: dict, vol: str) -> List[str]:
    """Builds the citation detail FIELD/VALUE lines for a RootsMagic citation.
    Bare FIELD tags render Free Form; RM needs them under _TMPLT. No TID here -
    that's on the master SOUR record only. RM's <...> omission logic only treats a
    field as blank when it's declared with an empty VALUE, not when it's missing
    from the list entirely - so every field is always declared."""
    lines = []
    for field_name in _SCRIP_TEMPLATES[template_id]['detail_fields']:
        value = _scrip_template_field_value(field_name, rec, part, vol)
        lines.extend(["4 FIELD", f"5 NAME {field_name}", f"5 VALUE {value}"])
    return ["3 _TMPLT"] + lines


def get_scrip_template_sources(template_ids_used: set, target_software: str, cfg: "General.GeneralRunConfig") -> list:
    """Builds master SOUR records for each referenced Métis Scrip template."""
    lines = []
    repository = Utils.clean_val(cfg.repository) or "Library and Archives Canada, Ottawa, Ontario"
    for tid in sorted(template_ids_used):
        tpl = _SCRIP_TEMPLATES.get(tid)
        if not tpl:
            continue
        s_id = f"@S{tid}@"
        display_name = (Utils.clean_val(tpl.get('source_description'))
                        or Utils.clean_val(tpl.get('website_collection'))
                        or str(tpl.get('label', '')))
        commission = Utils.clean_val(tpl.get('department') or tpl.get('commission'))
        collection = Utils.clean_val(tpl.get('collection'))
        primary_creator = Utils.clean_val(tpl.get('primary_creator', 'Department of the Interior'))
        date_str = Utils.clean_val(tpl.get('date_range_str'))
        source_desc = Utils.clean_val(tpl.get('source_description')) or display_name
        bibl = ". ".join(b for b in (repository, collection, commission) if b) + "."

        if target_software == "RM":
            block = [
                f"0 {s_id} SOUR",
                f"1 ABBR {display_name}",
                f"1 REFN {tid}",
                f"1 TITL {display_name}",
                f"1 _BIBL {bibl}",
                "1 _TMPLT",
                f"2 TID {tid}",
            ]
            # noinspection DuplicatedCode
            # RM's <...> omission logic only treats a field as blank when it's
            # declared with an empty VALUE, not when it's missing from the list
            # entirely - so every master field the template defines is always declared.
            block.extend(["2 FIELD", "3 NAME PrimaryCreator", f"3 VALUE {primary_creator}"])
            block.extend(["2 FIELD", "3 NAME Department", f"3 VALUE {commission}"])
            block.extend(["2 FIELD", "3 NAME Date", f"3 VALUE {date_str}"])
            block.extend(["2 FIELD", "3 NAME SourceDescription", f"3 VALUE {source_desc}"])
            block.extend(["2 FIELD", "3 NAME Collection", f"3 VALUE {collection}"])
            block.extend(["2 FIELD", "3 NAME Repository", f"3 VALUE {repository}"])
            block.extend(Utils.weblink_lines(cfg.collection_url, cfg.collection_name, "RM"))
        else:
            block = [
                f"0 {s_id} SOUR",
                f"1 TITL {display_name}",
                f"1 AUTH {repository}",
                f"1 PUBL {bibl}",
                f"1 REFN {tid}",
            ]
            block.extend(Utils.weblink_lines(cfg.collection_url, cfg.collection_name, "FTM"))
        lines.extend(block)
    return lines


class ScripProfile:
    @staticmethod
    def dynamic_source_id(vol_digits: str, cfg: "General.GeneralRunConfig", rec: Optional[dict] = None) -> str:
        if cfg.platform_source_id:
            return f"@S{cfg.platform_source_id}@"
        if rec:
            mikan = Utils.clean_val((rec.get('type_specific_fields') or {}).get('collection_mikan'))
            if mikan:
                return f"@S{mikan}@"
        return f"@S{vol_digits.zfill(3)}@"

    @staticmethod
    def participant_uid(identity: str, role: str, occ: int) -> Optional[str]:
        if not identity:
            return None
        if role == '0' and occ == 0:
            return identity
        return f"{identity}_{role}_{occ}" if occ > 0 else f"{identity}_{role}"

    @staticmethod
    def family_uid(identity: str) -> Optional[str]:
        if not identity:
            return None
        return f"FAM_{identity}"

    @staticmethod
    def citation_title(rec: dict, part: dict, _tag_name: str, _year: str,
                       _document_type: Optional[str]) -> str:
        std_g = Utils.clean_val(part.get('std_given'))
        std_s = Utils.clean_val(part.get('std_surname'))
        type_fields = rec.get('type_specific_fields') or {}
        claim_num = Utils.clean_val(type_fields.get('claim_number'))
        affdt_num = Utils.clean_val(type_fields.get('affidavit_number'))
        scrip_num = Utils.clean_val(type_fields.get('scrip_number'))
        ref_bits = [b for b in (f"Claim: {claim_num}" if claim_num else "",
                                f"Affidavit: {affdt_num}" if affdt_num else "",
                                f"Scrip: {scrip_num}" if scrip_num else "") if b]
        role_label = "Personal" if part.get('role_semantic') == 'primary' else "Witness"
        return f"3 _TITL {std_s}, {std_g}: {'; '.join(ref_bits)} [{role_label} (\"{std_g}\", {std_s})]"

    # noinspection DuplicatedCode
    @staticmethod
    def citation_page(rec: dict, _part: dict, _page: str) -> str:
        rec_id = Utils.clean_val(rec.get('record_id')) or 'Unknown'
        type_fields = rec.get('type_specific_fields') or {}
        claim_num = Utils.clean_val(type_fields.get('claim_number'))
        affdt_num = Utils.clean_val(type_fields.get('affidavit_number'))
        ref_bits = [b for b in (f"Claim {claim_num}" if claim_num else "",
                                f"Affdt {affdt_num}" if affdt_num else "") if b]
        return f"3 PAGE {'; '.join(ref_bits)}" if ref_bits else f"3 PAGE Record {rec_id}"

    @staticmethod
    def citation_template_id(rec: dict, _vol: str) -> Optional[int]:
        return resolve_scrip_template_id(rec)

    @staticmethod
    def citation_proof_status(_computed_status: str) -> str:
        return "proven"

    @staticmethod
    def citation_detail_fields(rec: dict, part: dict, page: str, vol: str,
                               target_software: str, _cfg: "General.GeneralRunConfig") -> List[str]:
        _ = page
        if target_software != "RM":
            return []
        template_id = resolve_scrip_template_id(rec)
        if not template_id:
            return []
        return get_scrip_citation_fields(template_id, rec, part, vol)

    @staticmethod
    def citation_text_block(rec: dict, _part: dict, raw_orig: str, raw_trans: str,
                            _cfg: "General.GeneralRunConfig") -> List[str]:
        type_fields = rec.get('type_specific_fields') or {}
        lines = []
        orig_val = Utils.clean_val(raw_orig)
        text = Utils.wrap_text(orig_val, '4 TEXT')
        if text:
            lines.append(text)
        review_val = Utils.clean_val(raw_trans if raw_trans is not None else type_fields.get('package_summary'))
        if review_val:
            lines.append("3 NOTE Commissioner's Review:")
            review_text = Utils.wrap_text(review_val, '4 CONT')
            if review_text:
                lines.append(review_text)
        return lines

    @staticmethod
    def citation_uses_source_documents(_rec: dict) -> bool:
        return False

    @staticmethod
    def primary_fact_date(rec: dict, is_primary: bool) -> str:
        year = _scrip_record_year(rec)
        return str(year) if is_primary and year else ""

    @staticmethod
    def build_primary_event_lines(rec: dict, part: dict, event_tag: str, witnesses: List[dict],
                                  vol: str, media_uid: str, target_software: str, resi: str,
                                  alt_names: list, scrip_fact_date: str, raw_event_date: str,
                                  age: str, cfg: "General.GeneralRunConfig") -> List[str]:
        if event_tag != 'EVEN':
            return General.build_generic_primary_event_lines(
                rec, part, event_tag, witnesses, vol, media_uid, target_software,
                alt_names, raw_event_date, age, cfg)

        extra_fields = rec.get('type_specific_fields') or {}
        claim_number = Utils.clean_val(extra_fields.get('claim_number'))
        affidavit_number = Utils.clean_val(extra_fields.get('affidavit_number'))
        scrip_number = Utils.clean_val(extra_fields.get('scrip_number'))
        scrip_amount = Utils.clean_val(extra_fields.get('scrip_amount'))
        scrip_part = f"Scrip #: {scrip_number}" if scrip_number else ""
        if scrip_part and scrip_amount:
            scrip_part += f" ({scrip_amount})"
        value_parts = [p for p in (
            f"Claim: {claim_number}" if claim_number else "",
            f"Affidavit #: {affidavit_number}" if affidavit_number else "",
            scrip_part,
        ) if p]
        consumed = ('claim_number', 'affidavit_number', 'scrip_number', 'scrip_amount', 'document_type',
                    'commission_reference', 'rg_series_code', 'reel_numbers', 'application_date',
                    'issue_date', 'delivery_date', 'delivery_place', 'package_summary')
        value_parts.extend(f"{k.replace('_', ' ').title()}: {Utils.clean_val(v)}"
                           for k, v in extra_fields.items() if k not in consumed and Utils.clean_val(v))
        scrip_value = "; ".join(value_parts)
        scrip_place = Utils.clean_place(rec.get('event_place')) or resi or cfg.default_location

        lines = General.build_custom_fact_lines('Scrip', scrip_value, rec, part, vol, media_uid,
                                                target_software, cfg, date=scrip_fact_date, place=scrip_place)
        if alt_names:
            alt_values = ", ".join(Utils.clean_val(a.get('value')) for a in alt_names)
            lines.append(f"2 NOTE Margin note suggests alternate spelling: {alt_values}")
        lines.extend(General.build_witness_links(rec, witnesses, vol, target_software, cfg))
        return lines

    @staticmethod
    def volume_source_detail_fields(_v_clause: str, _cfg: "General.GeneralRunConfig") -> List[str]:
        return []

    @staticmethod
    def media_caption(sheet: dict, vol: str, pages: str, _cfg: "General.GeneralRunConfig") -> str:
        first_rec = next(iter(sheet.get('records', [])), {})
        primary = General.get_by_semantic(first_rec, 'primary') or {}
        scrip_tf = first_rec.get('type_specific_fields') or {}
        media_std_g = Utils.clean_val(primary.get('std_given'))
        media_std_s = Utils.clean_val(primary.get('std_surname'))
        media_ref_bits = [b for b in (
            f"Claim: {Utils.clean_val(scrip_tf.get('claim_number'))}" if scrip_tf.get('claim_number') else "",
            f"Affidavit: {Utils.clean_val(scrip_tf.get('affidavit_number'))}" if scrip_tf.get(
                'affidavit_number') else "",
            f"Scrip: {Utils.clean_val(scrip_tf.get('scrip_number'))}" if scrip_tf.get('scrip_number') else "",
        ) if b]
        if media_std_s or media_std_g:
            return f"{media_std_s}, {media_std_g}: {'; '.join(media_ref_bits)}"
        return f"Scrip Records - Vol {vol or 'Unknown'} - Page {pages or 'X'}"

    @staticmethod
    def resolve_source_templates(json_data: dict, target_software: str,
                                 cfg: "General.GeneralRunConfig") -> List[str]:
        template_ids_used = set()
        for sheet in json_data.get('sheets', []):
            for rec in sheet.get('records', []):
                tid = resolve_scrip_template_id(rec)
                if tid:
                    template_ids_used.add(tid)
        if not template_ids_used:
            return []
        lines = get_scrip_template_sources(template_ids_used, target_software, cfg)
        if target_software == "RM":
            lines = lines + General.get_source_templates(template_ids_used)
        return lines

    @staticmethod
    def repository_defaults() -> Tuple[str, str]:
        return "Library and Archives Canada", "Ottawa, ON"

    @staticmethod
    def default_gedcom_output_name() -> Optional[str]:
        return "Scrip.ged"
