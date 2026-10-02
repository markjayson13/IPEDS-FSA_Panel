"""Portable, loss-aware metadata for FSA research exports.

The source dictionary is preserved verbatim alongside a more specific export
dictionary.  This module does not change observations, infer missing aid as zero,
or turn annual IPEDS classifications into timeless categories.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pandas as pd
from pandas.api.types import infer_dtype, is_bool_dtype, is_numeric_dtype

METADATA_VERSION = "1.0.0"
FAMILIES = {
    "loan_direct_harmonized__": ("direct_loans_harmonized", "DL harmonized"),
    "loan_ffel_harmonized__": ("ffel_harmonized", "FFEL harmonized"),
    "loan_direct__": ("direct_loans", "Direct Loan"),
    "loan_ffel__": ("ffel", "FFEL"),
    "grant__": ("grants", "Grant"),
    "campus__": ("campus_based", "Campus-based"),
    "loan__": ("combined_loans", "Combined loan"),
}
STATUS_DEFINITIONS = {
    "observed": "An explicitly reported numeric nonzero value.",
    "observed_zero": "An explicitly reported numeric zero; not an imputed zero.",
    "source_blank": "The source cell is blank or a recognized missing-text token.",
    "source_symbol": "The source cell contains a dash, dot, NA, or other recognized nonnumeric symbol; not zero.",
    "suppressed_lt10": "Source reports <10; count bounds are 0 and 9, while the point value remains missing.",
    "suppressed": "Source explicitly suppresses or redacts the value; no numeric value is imputed.",
    "unavailable_in_schema": "The selected source report does not supply this variable in this year.",
    "absent_source_record": "No usable source-family record for this panel row; not evidence of zero aid.",
    "report_not_available": "No selected report for this source family and award year is available in the build.",
    "invalid_numeric": "Source token failed numeric parsing; acceptance must resolve this condition.",
    "invalid_fractional_count": "A count has a fractional source value; the point value is withheld.",
    "invalid_negative_count": "A count has a negative source value; the point value is withheld.",
    "complete_source_reported": "The complete harmonized value comes from one observed source measure.",
    "complete_component_sum": "All required components are observed; the value is their complete sum.",
    "complete_direct_only_scope": "The complete combined value covers the available Direct Loan report only.",
    "incomplete_component_sum": "Some required components are unknown; an observed partial sum is not a complete total.",
    "no_observed_components": "None of the required component point values is observed.",
    "schema_not_reviewed": "The source-year component schema has not been approved for this derived measure.",
    "incomplete_blocked_unitid_family": "At least one required source family is blocked from the UNITID panel.",
    "included_unique_family_record": "Exactly one eligible source-family record supplies this UNITID-year; no aid allocation.",
    "ambiguous_family_multiple_records": "Multiple source-family records could supply this UNITID-year; values are blocked, not summed.",
    "excluded_missing_official_membership_guard": "UNITID-only identity cannot pass the official site-membership overlap guard.",
    "excluded_official_membership_overlap": "UNITID-only source record overlaps another official site membership; blocked.",
    "excluded_annual_identity_unresolved": "No eligible singleton annual institutional identity was established.",
    "exact_unique": "One full-OPEID match in the selected annual directory, passing the original activity/office screen.",
    "exact_unique_inactive": "One full-OPEID directory match is classified inactive by the original directory screen.",
    "exact_unique_administrative_unit": "One full-OPEID directory match is an administrative unit.",
    "multiple_exact_unitids": "The full OPEID matches multiple UNITIDs in the selected annual directory.",
    "no_exact_match": "No full-OPEID match exists in the selected annual directory.",
    "ipeds_year_unavailable": "The selected annual IPEDS directory is not available.",
    "official_site_alias_annual_unitid_verified": "Official site crosswalk resolves a singleton UNITID present in the annual directory under another OPEID.",
    "official_site_and_directory_agree": "Official site relation and full-OPEID annual directory evidence agree on the resolved UNITID.",
    "unique_annual_directory_only": "A unique annual full-OPEID directory identity is available without a usable official site match.",
    "verified_component_identity_opeid_unresolved": "Independent evidence verifies this family's UNITID; a full source OPEID remains unresolved.",
    "crosswalk_site_parse_incomplete": "At least one official primary/additional site field is incompletely parsed; singleton matching is blocked.",
    "multiple_official_site_unitids": "The union of official primary and additional site matches contains multiple UNITIDs.",
    "directory_crosswalk_identity_conflict": "Annual directory and official site crosswalk identify different institutions.",
    "multiple_nonadministrative_directory_unitids": "Multiple nonadministrative annual directory identities remain despite site crosswalk evidence.",
    "crosswalk_site_missing_annual_directory": "Official site UNITID is absent from the selected annual directory.",
    "multiple_exact_directory_unitids": "Multiple annual full-OPEID directory identities remain unresolved.",
    "annual_directory_unavailable": "Annual directory evidence for the selected reference year is unavailable.",
    "no_annual_site_match": "Neither the annual directory nor official site relations resolves an institutional identity.",
    "fsa_ipeds_geography_identity_conflict": "FSA and candidate IPEDS identity contradict one another on domestic versus foreign geography.",
    "noninstitutional_source_placeholder": "Source identifier belongs to a reviewed noninstitutional consolidation-account placeholder.",
    "participating": "Year-aware OPEFLAG interpretation: participating in Title IV programs.",
    "branch_of_participating_institution": "Year-aware OPEFLAG interpretation: branch of a Title IV participating institution.",
    "deferment_only": "Year-aware OPEFLAG interpretation: deferment-only limited participation.",
    "new_participant": "Year-aware OPEFLAG interpretation: new participant under the reviewed year's definition.",
    "new_spring_participant": "Year-aware OPEFLAG interpretation: new spring participant (reviewed years from 2004).",
    "not_participating_with_opeid": "Year-aware OPEFLAG interpretation: not participating, with an OPEID.",
    "not_participating_without_opeid": "Year-aware OPEFLAG interpretation: not participating, without an OPEID.",
    "stopped_participating": "Year-aware OPEFLAG interpretation: stopped participating in Title IV.",
    "eligible_out_of_scope_or_historical_definition": "OPEFLAG 4 in 1999 or 2001 has a historical eligibility/out-of-scope definition; consult annual documentation.",
    "unknown": "The pipeline cannot interpret the annual source classification under its reviewed rules.",
}
# Append new entries, never reorder: exported integer values are a public schema.
STATUS_CODES = {value: i + 1 for i, value in enumerate(STATUS_DEFINITIONS)}
BOOL_TOKENS = {"true": 1, "false": 0, "1": 1, "0": 0}
BOOL_FIELDS = {
    "source_record_present", "source_report_present", "descriptor_review_required",
    "fsa_descriptor_review_required", "ipeds_strict_eligible", "ipeds_crosswalk_available",
    "ipeds_identity_geography_conflict", "ipeds_fsa_hd_state_conflict", "ipeds_fsa_hd_name_review_required",
    "ipeds_annual_identity_eligible", "ipeds_identity_is_administrative", "ipeds_closed_with_data",
    "ipeds_identity_uses_historical_relation", "ipeds_identity_strict_contemporaneous",
    "ipeds_has_other_parent_unitid", "ipeds_component_scope_review_required",
    "cw_preliminary", "cw_has_additional_match", "cw_multiple_site_unitids", "cw_relation_parse_complete",
    "cw_site_parse_complete", "cw_site_parent_unitids_differ", "cw_official_no_match",
    "cw_has_change_notes", "cw_has_prior_id_notes", "cw_duplicate_opeid_rows",
}
ANNUAL_IPEDS_FIELDS = {
    "ipeds_" + field: field.upper() for field in (
        "instnm", "stabbr", "city", "zip", "opeflag", "sector", "cyactive", "act", "newid",
        "deathyr", "closedat", "postsec", "pseflag", "pset4flg", "rptmth", "f1systyp", "f1sysnam", "f1syscod")
}
GENERIC_DEFINITIONS = (
    "Original source token, workbook, sheet, Excel row or report/record availability; not an aid measure.",
    "Source-family annual linkage metadata; preserved from the resolved bridge.",
    "Annual panel key: IPEDS UNITID and FSA award year; award-year start/end are calendar years.",
)


def _text(value: Any) -> str:
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return ""
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _family(column: str) -> tuple[str, str, str]:
    for prefix, (family, label) in FAMILIES.items():
        if column.startswith(prefix):
            return family, label, column[len(prefix):]
    return "panel", "", column


def portable_names(columns: list[str]) -> dict[str, str]:
    """Names are order independent, ASCII, <=32 chars, and safe for Stata 14+."""
    if len(columns) != len(set(columns)):
        raise ValueError("Duplicate canonical column names")
    result = {}
    reserved = {"_all", "_n", "_N", "_b", "_coef", "_cons", "_pi", "_rc", "if", "in", "using"}
    substitutions = [("loan_direct_harmonized", "dlh"), ("loan_ffel_harmonized", "ffh"),
                     ("loan_direct", "dl"), ("loan_ffel", "ffel"),
                     ("recipient", "recip"), ("disbursements", "disb"),
                     ("subsidized", "sub"), ("undergraduate", "ug"), ("graduate", "grad"),
                     ("observed_partial_sum", "partial"), ("lower_bound", "lb"), ("upper_bound", "ub")]
    for column in columns:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", column) and column not in reserved:
            candidate = column
        else:
            stem = column
            for old, new in substitutions:
                stem = stem.replace(old, new)
            stem = re.sub(r"[^A-Za-z0-9_]", "_", stem)
            stem = re.sub(r"_+", "_", stem).strip("_") or "variable"
            if not stem[0].isalpha():
                stem = "v_" + stem
            candidate = stem[:21] + "_" + hashlib.sha256(column.encode()).hexdigest()[:10]
        result[column] = candidate
    if len(set(result.values())) != len(result):
        raise ValueError("Portable-name collision; do not publish an ambiguous name map")
    return result


def _human(token: str) -> str:
    words = token.replace("__", " ").replace("_", " ")
    for old, new in {"opeid8": "OPEID8", "opeid6": "OPEID6", "unitid": "UNITID", "ipeds": "IPEDS",
                     "fsa": "FSA", "pell": "Pell", "acg": "ACG", "smart": "SMART", "teach": "TEACH",
                     "iasg": "IASG", "fws": "FWS", "fseog": "FSEOG", "plus": "PLUS", "amt": "amount",
                     "n": "count", "cw": "crosswalk"}.items():
        words = re.sub(r"\b" + old + r"\b", new, words)
    return words[:1].upper() + words[1:]


def _specific_definition(column: str, token: str, family_label: str, entry: dict, columns: set[str]) -> tuple[str, str]:
    """Return definition and resolution quality; never invent unknown source semantics."""
    exact = {
        "unitid": "IPEDS UNITID identifying the institution in this annual identity panel. Numeric integer panel identifier; annual identity does not certify FSA aid reporting scope.",
        "award_year": "FSA award-year label YYYY-YYYY, spanning July 1 of the starting year through June 30 of the ending year. Use award_year_start as the numeric annual time index.",
        "award_year_start": "Calendar year in which the FSA award year begins (July 1); numeric annual time index for panel declarations such as xtset unitid award_year_start.",
        "award_year_end": "Calendar year in which the FSA award year ends (June 30), equal to award_year_start plus one.",
        "opeid8": "Full eight-character FSA OPEID including the location suffix, stored as text with leading zeros. No parent roll-up; missing remains missing for UNITID-only verified records.",
        "opeid6": "First six characters of the normalized full OPEID, retained as a legacy root descriptor. Not a universally valid parent identifier or a standalone IPEDS matching key.",
        "included_source_family_count": "Number of source families with exactly one included eligible record in this UNITID-year, across grants, Campus-Based, Direct Loan and FFEL (0 to 4).",
        "blocked_source_family_count": "Number of source families blocked in this UNITID-year by ambiguity or a membership guard, across the four families (0 to 4); excludes absent source records.",
        "raw_opeid": "Original source OPEID cell token before normalization or evidence-ledger recovery; text is preserved without padding or coercion.",
        "raw_zip_code": "Original source ZIP/postal cell token before descriptor normalization; not numeric geography.",
        "source_filename": "Filename of the selected FSA workbook supplying this source-family record.",
        "source_sheet": "Worksheet name in the selected FSA workbook supplying this source-family record.",
        "source_excel_row": "One-based Excel row number of this source-family institution record in its original selected worksheet, including header rows.",
        "source_sha256": "SHA-256 digest of the exact FSA source workbook bytes supporting this source-family record.",
        "source_identity_evidence_json": "JSON evidence references for independently verified source-record identity, including UNITID-only records whose OPEID remains unresolved.",
        "source_report_present": "Whether a selected workbook for this source family and award year is available; does not imply this institution has a usable record.",
        "source_record_present": ("Whether exactly one eligible source record from this family is included for this UNITID-year. Blocked and absent records are false."
                                  if "unitid" in columns else "Whether this full-OPEID award-year has a record in the selected source-family report; does not imply all measures are observed."),
        "unitid_source_record_count": "Number of identity-linked candidate source-family records for this UNITID-year, including records blocked by ambiguity or membership guards; missing when absent.",
        "unitid_record_status": "Disposition of this source family at UNITID-year level: included unique record, absent source record, or a stated blocking condition. Multiple records are never automatically summed.",
    }
    if column in exact:
        return exact[column], "specific_definition"
    if token in exact:
        return exact[token], "specific_definition"
    if token in {"school", "state", "zip_code", "school_type"}:
        concept = {"school": "institution name", "state": "state or jurisdiction descriptor", "zip_code": "ZIP or postal code (text with leading zeros)", "school_type": "institution control/type descriptor"}[token]
        if family_label == "Combined loan":
            return f"Combined-loan {concept}, selected from Direct Loan then FFEL when missing. Original channel-specific descriptors remain available.", "specific_definition"
        if family_label:
            return f"{family_label} source-family {concept}, preserved from that family's descriptor resolution. This is an FSA reporting-unit descriptor, not independent certification of campus aid scope.", "specific_definition"
        return f"Canonical FSA {concept} resolved across available source families and documented overrides. Unresolved conflicts remain missing; consult the descriptor review flags.", "specific_definition"
    if token.endswith("__review_required") or token in {"descriptor_review_required", "fsa_descriptor_review_required"}:
        descriptor = token.removeprefix("fsa_").removesuffix("__review_required")
        return f"Whether unresolved source-family descriptor disagreement requires review for {_human(descriptor).lower()}. A true flag is a data-quality warning, not a value for the descriptor.", "specific_definition"
    if column.endswith("__raw_token"):
        return f"Original exceptional source-cell token for {column.removesuffix('__raw_token')}; stored only when the point value is not observed. Consult its status to distinguish blank, symbol, suppression, and unavailability.", "specific_definition"
    if column.endswith("__status"):
        return f"Observation or completeness status of {column.removesuffix('__status')}. Distinguishes observed zero, missing/suppressed values, source/schema absence, incomplete components, and blocked family records. Export integer codes are defined in the value-label table.", "specific_definition"
    if column.endswith(("__lower_bound", "__upper_bound")):
        suffix = "__lower_bound" if column.endswith("__lower_bound") else "__upper_bound"
        direction = "lower" if suffix == "__lower_bound" else "upper"
        return f"{direction.capitalize()} bound for {column.removesuffix(suffix)}. Equals the observed point value when observed; for a suppressed count <10, lower=0 and upper=9. Otherwise remains unknown; not an imputed point estimate.", "specific_definition"
    definition = _text(entry.get("definition")) or _text(entry.get("description"))
    if definition and not any(definition.startswith(g) for g in GENERIC_DEFINITIONS):
        if _text(entry.get("column_role")) in {"measure", "bound_or_partial_measure"}:
            definition = ((family_label + ": ") if family_label else "") + _human(token) + ". " + definition
        return definition, "inherited_source_definition"
    return f"Preserved field {column}. Its detailed source semantics are not resolved by the inherited dictionary; review the original dictionary and source documentation before analytical use.", "definition_requires_review"


def _units(column: str, token: str, entry: dict, category_kind: str, role: str) -> str:
    if category_kind:
        return "category"
    if column in {"award_year_start", "award_year_end"} or token in {"ipeds_year", "ipeds_deathyr"}:
        return "calendar_year"
    if column == "award_year":
        return "award_year_label"
    if token in {"opeid8", "opeid6", "unitid", "cw_parent_opeid8", "ipeds_newid", "ipeds_f1syscod"} or token.startswith("idx_") or "unitid" in token and not any(x in token for x in ("count", "status", "json", "scope", "note")):
        return "identifier"
    if token.endswith("__raw_token") or token in {"raw_opeid", "raw_zip_code", "school", "state", "school_type", "zip_code"}:
        return "text"
    if token.endswith("name_similarity"):
        return "ratio_0_to_1"
    if token == "ipeds_closedat":
        return "date_as_reported"
    if role in {"measure", "bound_or_partial_measure"}:
        base = re.sub(r"__(?:lower_bound|upper_bound|observed_partial_sum)$", "", token)
        if base.endswith("loans_originated_n"):
            return "count_loans"
        if base.endswith("disbursements_n"):
            return "count_disbursement_transactions"
        if base.endswith("recipient_count_sum"):
            return "sum_of_component_recipient_counts_not_unique_people"
        if "unique_recipient_" in base:
            return "bound_on_unique_student_recipients_within_named_scope"
        value = _text(entry.get("units")) or _text(entry.get("unit"))
        return {"USD_nominal": "USD_nominal", "USD nominal": "USD_nominal", "nominal_usd": "USD_nominal"}.get(value, value or "not_documented")
    if token.endswith("count") or "count_" in token or token in {"source_excel_row", "cw_source_row"}:
        return "count" if "row" not in token else "one_based_row_number"
    if token.endswith(("_status", "_scope", "_code")) or token.startswith("prch_") or token in {
        "prchtp_f", "ipeds_opeflag", "ipeds_sector", "ipeds_cyactive", "ipeds_act", "ipeds_postsec",
        "ipeds_pseflag", "ipeds_pset4flg", "ipeds_rptmth", "ipeds_f1systyp"}:
        return "category"
    return "text" if role in {"descriptor", "provenance", "reporting_scope"} else (_text(entry.get("units")) or "not_applicable")


def _is_boolean(token: str, series: pd.Series) -> bool:
    return (is_bool_dtype(series.dtype) or infer_dtype(series.dropna(), skipna=True) == "boolean"
            or token in BOOL_FIELDS or token.endswith(("__review_required", "_parent_reference_missing")))


def _status_code(value: str) -> int:
    return STATUS_CODES.get(value, 10000 + int(hashlib.sha256(value.encode()).hexdigest()[:7], 16))


def build_export_metadata(panel: pd.DataFrame, dictionary: pd.DataFrame, *,
                          dataset_name: str = "FSA institution award-year panel") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build one metadata row per column and reversible category-code tables.

    Does not mutate either input. Existing dictionary rows/fields are retained
    both as source_dictionary__ columns and as original_dictionary_json.
    """
    key = "panel_column" if "panel_column" in dictionary else "canonical_name"
    if key not in dictionary:
        raise ValueError("Dictionary must contain panel_column or canonical_name")
    if dictionary[key].duplicated().any():
        raise ValueError("Duplicate dictionary column names")
    inherited = dictionary.set_index(key, drop=False).to_dict("index")
    names = portable_names(list(panel.columns))
    columns = set(panel.columns)
    metadata_rows, label_rows = [], []
    for column in panel:
        entry = inherited.get(column, {})
        family, family_label, token = _family(column)
        series = panel[column]
        category_kind = "boolean" if _is_boolean(token, series) else "status" if column.endswith("_status") else ""
        role = _text(entry.get("column_role")) or "provenance"
        if column in {"unitid", "opeid8", "opeid6", "award_year", "award_year_start", "award_year_end"} or token == "opeid8":
            role = "identifier_or_time"
        elif category_kind or token.endswith("__review_required"):
            role = "quality_status"
        elif column.endswith("__raw_token"):
            role = "provenance"
        elif column.endswith(("__lower_bound", "__upper_bound", "__observed_partial_sum")):
            role = "bound_or_partial_measure"
        description, quality = _specific_definition(column, token, family_label, entry, columns)
        label = ((family_label + ": ") if family_label else "") + _human(token)
        if len(label) > 80:
            label = label[:69].rstrip() + " [" + hashlib.sha256(column.encode()).hexdigest()[:7] + "]"
        units = _units(column, token, entry, category_kind, role)
        identifier_text = token in {"opeid8", "opeid6", "zip_code", "raw_zip_code", "raw_opeid", "ipeds_zip", "ipeds_scope_root", "cw_parent_opeid8"}
        numeric = is_numeric_dtype(series.dtype) or infer_dtype(series.dropna(), skipna=True) in {"integer", "floating", "mixed-integer-float", "decimal"}
        storage = "coded_category" if category_kind else "numeric" if numeric and not identifier_text else "string"
        categories = []
        if category_kind == "boolean":
            actual = series.dropna().map(lambda v: str(v).lower()).unique()
            if any(value not in BOOL_TOKENS for value in actual):
                raise ValueError(f"Invalid boolean value in {column}: {actual.tolist()}")
            categories = [(0, "false", "False", "The condition described by this variable is false; missing is distinct."),
                          (1, "true", "True", "The condition described by this variable is true; missing is distinct.")]
        elif category_kind == "status":
            for value in sorted(series.dropna().astype(str).unique()):
                definition = STATUS_DEFINITIONS.get(value, "Unrecognized source status token; its meaning requires review against the generating pipeline/source.")
                if value not in STATUS_DEFINITIONS:
                    quality = "status_definition_requires_review"
                categories.append((_status_code(value), value, value, definition))
            if len({c[0] for c in categories}) != len(categories):
                raise ValueError(f"Category-code collision in {column}")
        for code, value, value_label, definition in categories:
            label_rows.append({"canonical_name": column, "portable_name": names[column], "code": code,
                               "value": value, "label": value_label, "description": definition})
        base = re.sub(r"__(?:status|raw_token|lower_bound|upper_bound|observed_partial_sum)$", "", column)
        if "unique_recipient_lower_bound" in column or "unique_recipient_upper_bound" in column:
            base = re.sub(r"unique_recipient_(?:lower|upper)_bound$", "recipient_count_sum", column)
        companions = {"status_variable": base + "__status", "lower_bound_variable": base + "__lower_bound",
                      "upper_bound_variable": base + "__upper_bound", "raw_token_variable": base + "__raw_token",
                      "partial_sum_variable": base + "__observed_partial_sum"}
        if base.endswith("recipient_count_sum"):
            companions.update(lower_bound_variable=base.replace("recipient_count_sum", "unique_recipient_lower_bound"),
                              upper_bound_variable=base.replace("recipient_count_sum", "unique_recipient_upper_bound"))
        companions = {k: v if v in columns else "" for k, v in companions.items()}
        missing_rule = (_text(entry.get("missing_value_rule")) or
                        "Missing values remain missing. Consult the observation status and family inclusion flags; no automatic zero filling.")
        source_notes = [description]
        for field in ("counting_caveat", "caveat", "reporting_scope", "interpretation", "unitid_panel_method"):
            detail = _text(entry.get(field))
            if detail and detail not in source_notes:
                source_notes.append(detail)
        annual_variable = ANNUAL_IPEDS_FIELDS.get(token, token.upper() if token.startswith(("prch_", "idx_")) or token == "prchtp_f" else "")
        reference_year = column.removesuffix(token) + "ipeds_year" if annual_variable else ""
        if annual_variable:
            source_notes.append("Raw IPEDS code/reference is preserved, including published sentinel codes. Interpret using the associated ipeds_year and annual dictionary evidence; definitions can vary over time.")
        row = {"canonical_name": column, "portable_name": names[column], "variable_label": label,
               "description": " ".join(source_notes), "units": units, "role": role,
               "source_family": family, "original_dtype": str(series.dtype), "export_storage": storage,
               "category_kind": category_kind, "metadata_quality": quality, "dataset_name": dataset_name,
               "metadata_version": METADATA_VERSION, "precision_format": "%18.2f" if units == "USD_nominal" else "%18.0g" if storage == "numeric" else "",
               "missing_value_rule": missing_rule, "formula": _text(entry.get("formula")),
               "source_documentation": _text(entry.get("source")),
               "annual_dictionary_variable": annual_variable,
               "reference_year_variable": reference_year if reference_year in columns else "",
               "source_mappings_json": _text(entry.get("source_mappings_json")) or "[]",
               "policy_event_ids_json": _text(entry.get("policy_event_ids_json")) or "[]",
               "policy_source_urls_json": _text(entry.get("policy_source_urls_json")) or "[]",
               "original_dictionary_json": json.dumps(_json_value(entry), ensure_ascii=False, sort_keys=True),
               **companions}
        for k, value in entry.items():
            row["source_dictionary__" + k] = value
        metadata_rows.append(row)
    metadata = pd.DataFrame(metadata_rows)
    labels = pd.DataFrame(label_rows, columns=["canonical_name", "portable_name", "code", "value", "label", "description"])
    if metadata.variable_label.str.len().gt(80).any() or metadata.description.eq("").any():
        raise AssertionError("Incomplete export labels or descriptions")
    return metadata, labels


def encode_categorical_columns(frame: pd.DataFrame, metadata: pd.DataFrame,
                               value_labels: pd.DataFrame) -> pd.DataFrame:
    """Return portable names and reversible numeric status/boolean codes.

    Text identifiers are not parsed as numbers. Non-category values are untouched
    except an explicit string cast for fields whose export_storage is string.
    """
    if set(frame) != set(metadata.canonical_name) or metadata.canonical_name.duplicated().any():
        raise ValueError("Export metadata must enumerate exactly the input columns")
    if metadata.portable_name.duplicated().any() or not metadata.portable_name.str.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}").all():
        raise ValueError("Export metadata has invalid or duplicate portable names")
    if value_labels.duplicated(["canonical_name", "value"]).any() or value_labels.duplicated(["canonical_name", "code"]).any():
        raise ValueError("Value-label table has duplicate category tokens or codes")
    result = {}
    tables = {k: v for k, v in value_labels.groupby("canonical_name", sort=False)}
    for row in metadata.to_dict("records"):
        column = row["canonical_name"]
        source = frame[column]
        if row["category_kind"]:
            table = tables.get(column, pd.DataFrame(columns=["value", "code"]))
            mapping = dict(zip(table.value, table.code))
            tokens = source.astype("string")
            if row["category_kind"] == "boolean":
                tokens = tokens.str.lower().replace({"1": "true", "0": "false"})
            encoded = tokens.map(mapping)
            if (source.notna() & encoded.isna()).any():
                raise ValueError(f"Unmapped nonmissing category in {column}")
            result[row["portable_name"]] = encoded.astype("Int32")
        elif row["export_storage"] == "string":
            result[row["portable_name"]] = source.astype("string")
        elif row["export_storage"] == "numeric" and not is_numeric_dtype(source.dtype):
            result[row["portable_name"]] = pd.to_numeric(source, errors="raise")
        else:
            result[row["portable_name"]] = source.copy(deep=False)
    return pd.DataFrame(result, index=frame.index)
