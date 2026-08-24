"""
HBCA Archivist Profile.

Maps Hudson's Bay Company Archives (HBCA) Biographical Sheets to RootsMagic/GEDCOM
using Simplified Citations Template 10009 (* Simple Citations: Non-traditional).

Key genealogical rules:
1. Facts extracted from Biographical Sheets are derivative and marked with:
   - Proof Status: "proposed"
   - Source Quality: QUAY 1, _SOUR D (derivative), _INFO S (secondary), _EVID I (indirect)
2. Primary archival records linked later receive:
   - Proof Status: "proven"
   - Source Quality: QUAY 3, _SOUR O (original), _INFO P (primary), _EVID D (direct)
3. Single Master Source (@S10009@) shared across all HBCA records in the run.
"""

import datetime
from pathlib import Path
import re
from typing import List, Optional, Tuple

import General
import Utils

HBCA_TEMPLATE_ID = 10009


class HBCAProfile:
    @staticmethod
    def dynamic_source_id(_vol_digits: str, cfg: "General.GeneralRunConfig", rec: Optional[dict] = None) -> str:
        if cfg.platform_source_id:
            return f"@S{cfg.platform_source_id}@"
        if rec:
            refd = Utils.clean_val((rec.get('type_specific_fields') or {}).get('refd'))
            if refd:
                return f"@S{refd}@"
        return f"@S{HBCA_TEMPLATE_ID}@"

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
        else:
            titl += " -- HBCA Biographical Sheet"
        return titl

    @staticmethod
    def citation_page(rec: dict, part: dict, page: str) -> str:
        tf = rec.get('type_specific_fields') or {}
        emp_name = Utils.clean_val(tf.get('employee_name'))
        if emp_name:
            return f"3 PAGE {emp_name}"
        std_g = Utils.clean_val(part.get('std_given'))
        std_s = Utils.clean_val(part.get('std_surname'))
        if std_g or std_s:
            return f"3 PAGE {std_s}, {std_g}".strip(", ")
        return f"3 PAGE Page {page}"

    @staticmethod
    def citation_template_id(_rec: dict, _vol: str) -> Optional[int]:
        return HBCA_TEMPLATE_ID

    @staticmethod
    def citation_proof_status(_computed_status: str, rec: Optional[dict] = None) -> str:
        if rec and (rec.get("is_primary_source") or not rec.get("is_derivative", True)):
            return "proven"
        return "proposed"

    @staticmethod
    def citation_quality_fields(rec: dict, _part: dict, target_software: str) -> List[str]:
        refn = Utils.clean_val(rec.get('record_number')) or Utils.clean_val(rec.get('record_id')) or 'HBCA'
        is_primary = bool(rec.get('is_primary_source') or not rec.get('is_derivative', True))

        if is_primary:
            if target_software == "RM":
                return [
                    f"3 REFN {refn}",
                    "3 QUAY 3",
                    "3 _QUAL",
                    "4 _SOUR O",
                    "4 _INFO P",
                    "4 _EVID D",
                ]
            return [f"3 REFN {refn}", "3 QUAY 3"]
        else:
            if target_software == "RM":
                return [
                    f"3 REFN {refn}",
                    "3 QUAY 1",
                    "3 _QUAL",
                    "4 _SOUR D",
                    "4 _INFO S",
                    "4 _EVID I",
                ]
            return [f"3 REFN {refn}", "3 QUAY 1"]

    @staticmethod
    def citation_detail_fields(rec: dict, part: dict, page: str, vol: str,
                               target_software: str, cfg: "General.GeneralRunConfig") -> List[str]:
        _ = vol
        if target_software != "RM":
            return []

        tf = rec.get('type_specific_fields') or {}
        std_g = Utils.clean_val(part.get('std_given'))
        std_s = Utils.clean_val(part.get('std_surname'))
        person_name = f"{std_g} {std_s}".strip()
        emp_name = Utils.clean_val(tf.get('employee_name')) or person_name

        b_yr = Utils.clean_val(tf.get('birth_date_extracted') or part.get('birth_date'))
        d_yr = Utils.clean_val(tf.get('death_date_extracted') or part.get('death_date'))
        date_desc = ""
        if b_yr and d_yr:
            date_desc = f"b. {b_yr}-d. {d_yr}"
        elif b_yr:
            date_desc = f"b. {b_yr}"
        elif d_yr:
            date_desc = f"d. {d_yr}"

        page_val = f"{emp_name} ({date_desc})" if date_desc else emp_name or f"Page {page}"
        location_val = Utils.clean_val(
            rec.get('event_place')
            or tf.get('parish_of_origin')
            or cfg.parish_location
            or "Hudson's Bay Company Territories"
        )
        repo_val = "Hudson's Bay Company Archives, Archives of Manitoba, Winnipeg, MB"
        url_val = Utils.clean_val(
            tf.get('pdf_url')
            or rec.get('pdf_url')
            or "https://www.gov.mb.ca/chc/archives/hbca/biographical/index.html"
        )
        accessed_val = Utils.clean_val(
            tf.get('accessed') or datetime.date.today().strftime('%d %b %Y')
        )

        hbca_refs = tf.get('hbca_references') or []
        if isinstance(hbca_refs, list) and hbca_refs:
            ref_val = f"HBCA: {'; '.join(hbca_refs)}"
        elif isinstance(hbca_refs, str) and hbca_refs:
            ref_val = f"HBCA: {hbca_refs}"
        else:
            ref_val = f"Search File: {emp_name}" if emp_name else ""

        keystone_records = tf.get('keystone_records') or {}
        microfilm_nos = []
        archival_urls = []
        for code in (hbca_refs if isinstance(hbca_refs, list) else [hbca_refs]):
            entry = keystone_records.get(code)
            if not isinstance(entry, dict):
                continue
            meta = entry.get('metadata') or {}
            if meta.get('microfilm_no'):
                microfilm_nos.append(meta['microfilm_no'])
            archival_urls.extend(entry.get('record_urls') or [])

        if microfilm_nos:
            ref_val = f"{ref_val} (Microfilm {', '.join(dict.fromkeys(microfilm_nos))})"

        detail_fields = [
            ("Page", page_val),
            ("SourceDetailPerson", person_name),
            ("Location", location_val),
            ("Repository", repo_val),
            ("URL", url_val),
            ("Accessed", accessed_val),
            ("RefNumber", ref_val),
        ]

        if archival_urls:
            detail_fields.append(("ArchivalRecordURL", archival_urls[0]))

        # Bare FIELD tags render Free Form; RM needs them under _TMPLT. No TID here -
        # that's on the master SOUR record only. RM's <...> omission logic only treats
        # a field as blank when it's declared with an empty VALUE, not when it's
        # missing from the list entirely - so every field is always declared.
        field_lines = []
        for f_name, f_val in detail_fields:
            field_lines.extend(["4 FIELD", f"5 NAME {f_name}", f"5 VALUE {f_val}"])
        return ["3 _TMPLT"] + field_lines

    @staticmethod
    def citation_text_block(rec: dict, _part: dict, raw_orig: str, raw_trans: str,
                            cfg: "General.GeneralRunConfig") -> List[str]:
        orig_val = Utils.clean_val(raw_orig)
        trans_val = Utils.clean_val(raw_trans)

        tf = rec.get('type_specific_fields') or {}
        keystone_records = tf.get('keystone_records') or {}
        hbca_refs = tf.get('hbca_references') or []

        keystone_parts = []
        for code in (hbca_refs if isinstance(hbca_refs, list) else [hbca_refs]):
            entry = keystone_records.get(code)
            if not isinstance(entry, dict):
                continue
            meta = entry.get('metadata') or {}
            item_desc = meta.get('item_description')
            date_str = meta.get('date')
            microfilm = meta.get('microfilm_no')

            if item_desc is not None:
                desc = str(item_desc)
                if date_str is not None:
                    desc += f", {str(date_str)}"
                desc += f" (Archives of Manitoba, HBCA {code}"
                if microfilm is not None:
                    desc += f", Microfilm {str(microfilm)}"
                desc += ")"
                keystone_parts.append(desc)

        if keystone_parts:
            narrative = "Archival Sources: " + "; ".join(keystone_parts)
            trans_val = f"{narrative}\n{trans_val}" if trans_val else narrative

        norm_orig = re.sub(r'\s+', ' ', orig_val).strip().lower() if orig_val else orig_val
        norm_trans = re.sub(r'\s+', ' ', trans_val).strip().lower() if trans_val else trans_val
        same_text = orig_val and trans_val and norm_orig == norm_trans
        lines = []
        # noinspection DuplicatedCode
        if same_text or not (orig_val and trans_val):
            single_text = Utils.wrap_text(trans_val or orig_val, '4 TEXT')
            if single_text:
                lines.append(single_text)
        else:
            citation_detail_header = cfg.citation_detail
            if citation_detail_header:
                lines.append(f"4 TEXT {citation_detail_header}")
                trans_text = Utils.wrap_text(trans_val, '5 CONT')
            else:
                trans_text = Utils.wrap_text(trans_val, '4 TEXT')
            if trans_text:
                lines.append(trans_text)

            citation_text_header = cfg.citation_text
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
    def primary_fact_date(rec: dict, is_primary: bool) -> str:
        return str(rec.get('year', '')) if is_primary and rec.get('year') else ""

    @staticmethod
    def build_primary_event_lines(rec: dict, part: dict, event_tag: str, witnesses: List[dict],
                                  vol: str, media_uid: str, target_software: str, _resi: str,
                                  alt_names: list, _scrip_fact_date: str, raw_event_date: str,
                                  age: str, cfg: "General.GeneralRunConfig") -> List[str]:
        return General.build_generic_primary_event_lines(
            rec, part, event_tag, witnesses, vol, media_uid, target_software,
            alt_names, raw_event_date, age, cfg
        )

    @staticmethod
    def volume_source_detail_fields(_v_clause: str, _cfg: "General.GeneralRunConfig") -> List[str]:
        return []

    @staticmethod
    def media_caption(sheet: dict, _vol: str, pages: str, _cfg: "General.GeneralRunConfig") -> str:
        meta = sheet.get('document_metadata') or {}
        first_rec = next(iter(sheet.get('records', [])), {})
        tf = first_rec.get('type_specific_fields') or {}
        emp = tf.get('employee_name')
        if emp is not None and str(emp).strip():
            return f"HBCA Biographical Sheet - {str(emp)}"
        pdf_url = meta.get('pdf_url', '')
        if pdf_url is not None and str(pdf_url).strip():
            return f"HBCA Biographical Sheet - {Path(str(pdf_url)).name}"
        return f"HBCA Biographical Sheet - Page {pages or '1'}"

    @staticmethod
    def resolve_source_templates(_json_data: dict, target_software: str,
                                 _cfg: "General.GeneralRunConfig") -> List[str]:
        display_name = "Hudson's Bay Company Archives: Biographical Sheets"
        abbr = "HBCA Biographical Sheets"
        repository = "Hudson's Bay Company Archives, Archives of Manitoba"
        pub_loc = "Winnipeg, Manitoba, Canada"
        bibl = f"{repository}. Biographical Sheets. {pub_loc}."

        block = [
            f"0 @S{HBCA_TEMPLATE_ID}@ SOUR",
            f"1 ABBR {abbr}",
            f"1 REFN {HBCA_TEMPLATE_ID}",
            f"1 TITL {display_name}",
            f"1 _BIBL {bibl}",
        ]
        if target_software == "RM":
            block.extend([
                "1 _TMPLT",
                f"2 TID {HBCA_TEMPLATE_ID}",
                "2 FIELD", "3 NAME PrimaryCreator", "3 VALUE Hudson's Bay Company",
                "2 FIELD", "3 NAME Department", "3 VALUE Archives of Manitoba",
                "2 FIELD", "3 NAME SourceDescription", "3 VALUE Biographical Sheets",
                "2 FIELD", "3 NAME Publisher", "3 VALUE Archives of Manitoba",
                "2 FIELD", "3 NAME PublishLocation", f"3 VALUE {pub_loc}",
                "2 FIELD", "3 NAME Repository", f"3 VALUE {repository}",
            ])
            block.extend(General.get_source_templates({HBCA_TEMPLATE_ID}))
        return block

    @staticmethod
    def repository_defaults() -> Tuple[str, str]:
        return "Hudson's Bay Company Archives, Archives of Manitoba", "Winnipeg, MB, Canada"

    @staticmethod
    def default_gedcom_output_name() -> Optional[str]:
        return "MasterDB_HBCA.ged"
