"""Pell comparison evidence, never a rule for assigning institutional identities.

The primary benchmark compares all-undergraduate UPGRNTN and UPGRNTT with FSA
Pell recipients and disbursements; verified TSTDPEL is a separate 2008 count-only
supplement. FTFT PGRNT_N/PGRNT_T and average UPGRNTA are never substituted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from fsa_ipeds_merge_diagnostics import normalize_full_opeid


OFFSETS = (-2, -1, 0, 1)
MEASURES = {"recipients": ("UPGRNTN", "grant__pell_recipients"),
            "dollars": ("UPGRNTT", "grant__pell_disbursements")}
AUDITED_VARIABLES = ("UPGRNTN", "UPGRNTT", "UPGRNTA", "PGRNT_N", "PGRNT_T",
                     "UDGPGRNTN", "UDGPGRNTT", "UNDPGRNTN", "UNDPGRNTT")
# Retain upstream evidence verbatim. A correction ID is provenance, not an
# exemption from the physical-source identity checks below.
SOURCE_PROVENANCE_COLUMNS = (
    "original_access_table_name", "original_source_file_label", "original_metadata_json",
    "resolved_physical_table", "metadata_correction_id", "metadata_correction_reason",
    "metadata_correction_evidence", "metadata_correction_registry_sha256",
    "source_archive_sha256", "source_database_sha256", "source_physical_table_sha256",
    "source_table_reference_period", "reference_period", "reference_period_start",
    "reference_period_end", "imputationvar", "imputation_flag_availability",
)
SOURCES = [
    {"title": "NCES 2014-15 SFA survey, public academic reporters, Part B and instructions",
     "url": "https://nces.ed.gov/IPEDS/use-the-data/download-survey-material/2014/student%20financial%20aid/package_7_16.pdf",
     "supports": "Group 1 includes all undergraduate students in the specified cohort, whereas Group 2 is FTFT. "
        "Academic reporters use fall students and program reporters use institution-defined academic-year students. "
        "The 2014 collection refers to aid in 2013-14."},
    {"title": "NCES 2023 Data Feedback Report methodological notes",
     "url": "https://nces.ed.gov/ipeds/dfr/2023/ReportHTML.aspx?unitId=199272",
     "supports": "SFA reports awarded/accepted aid; this can differ from disbursement. IPEDS may impute missing data."},
    {"title": "NCES SFA and enrollment population guidance",
     "url": "https://nces.ed.gov/ipeds/survey-components/12/how-percentage-finanical-aid-calculated-enrollment-sfa",
     "supports": "All-undergraduate SFA populations include non-degree/non-certificate students and depend on reporting period; "
        "reporting differences require review."},
    {"title": "NCES 2019-20 Finance survey, private for-profit institutions",
     "url": "https://nces.ed.gov/ipeds/use-the-data/download-survey-material/2019/finance/package_5_12.pdf",
     "supports": "Finance uses the institution's fiscal reporting period and separately identifies Pell pass-through versus revenue accounting."},
    {"title": "FSA 2026 Data Center report interpretation guidance",
     "url": "https://fsapartners.ed.gov/knowledge-center/library/electronic-announcements/2026-03-13/federal-student-aid-posts-updated-reports-fsa-data-center",
     "supports": "FSA program and loan-type reports do not supply a unique aggregate school recipient count; cumulative reports can be revised after initial publication."},
]
FSA_CONTEXT = [
    "grant__opeid8", "grant__unitid_record_status", "grant__ipeds_student_aid_scope",
    "grant__ipeds_scope_alignment", "grant__ipeds_reporting_scope", "grant__prch_sfa", "grant__idx_sfa",
    "grant__ipeds_anchor_sensitivity", "grant__ipeds_resolution_status",
    "grant__ipeds_identity_strict_contemporaneous", "grant__fsa_descriptor_review_required",
    "grant__ipeds_fsa_hd_name_similarity", "grant__unitid_aid_scope_note",
    "grant__source_filename", "grant__source_sheet", "grant__source_excel_row",
]
BRIDGE_CONTEXT = [
    "opeid8", "award_year", "unitid", "ipeds_resolution_status",
    "ipeds_crosswalk_site_unitids_json", "ipeds_crosswalk_parent_unitids_json",
    "ipeds_directory_candidate_unitids_json", "cw_source_path", "cw_source_sha256",
]


def measure_comparability_catalog(dictionary: pd.DataFrame, ipeds_columns: set[str],
                                  fsa_columns: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Document other possible clues without deriving totals or changing links."""
    # Exact annual definitions remain alongside this assessment, including
    # historical received/awarded wording and changes in program coverage.
    specifications = [
        ("pell_sfa_all_undergraduates", ["UPGRNTN", "UPGRNTT", "UPGRNTA"],
         ["grant__pell_recipients", "grant__pell_disbursements"], "quantitative_benchmark",
         "Pell program and all-undergraduate cohort are the closest reviewed SFA overlap. UPGRNTA is an average, not a total.",
         "IPEDS fall/full-year cohorts and awards versus FSA annual disbursements differ; parent/child scope remains unverified."),
        ("pell_legacy_2008_count", ["TSTDPEL"], ["grant__pell_recipients"], "supplemental_count_only_benchmark",
         "The 2008 dictionary has a separate all-undergraduate Pell count TSTDPEL in SFA0708.",
         "Compared separately as a 2008 count-only supplement; not silently coalesced with UPGRNTN and no dollars are inferred. "
         "The primary common-sample benchmark remains UPGRNTN/UPGRNTT."),
        ("federal_student_loan_counts", ["UFLOANN"],
         ["loan_direct__subsidized_undergraduate_recipients", "loan_direct__unsubsidized_undergraduate_recipients"],
         "not_an_exact_benchmark",
         "IPEDS counts all-undergraduate federal student borrowers; the borrower must be the student and parent PLUS is excluded.",
         "FSA category borrowers overlap, so subsidized plus unsubsidized counts are not unique borrowers. "
         "FSA also covers graduate and parent categories, and historical undergraduate splits are incomplete. "
         "Annual IPEDS loan definitions include programs beyond Direct/FFEL; no count equality or automatic matching is authorized."),
        ("federal_student_loan_dollars", ["UFLOANT", "UFLOANA"],
         ["loan_direct__subsidized_undergraduate_disbursements", "loan_direct__unsubsidized_undergraduate_disbursements",
          "campus__perkins_disbursements"], "requires_program_population_and_period_reconciliation",
         "UFLOANT is an all-undergraduate federal student-loan dollar total; UFLOANA is an average. "
         "Annual definitions describe additional federal health-professions/nursing loans beyond FSA Direct/FFEL reports.",
         "Exclude parent PLUS and graduate amounts; handle Perkins and other federal loan programs, historical level splits, "
         "FFEL transition, cohort-based cumulative disbursements, awarded-versus-disbursed basis and reporting-unit scope separately. No new sum is formed."),
        ("all_source_undergraduate_grants", ["UAGRNTN", "UAGRNTT", "UAGRNTA", "TOTGRNT"],
         ["grant__pell_recipients", "grant__pell_disbursements", "campus__fseog_disbursements"], "excluded_program_scope_mismatch",
         "IPEDS all-source undergraduate grants include federal, state, local, institutional and other known sources.",
         "FSA Volume reports cover selected federal programs only; adding their grants cannot reproduce all-source IPEDS grants. "
         "Recipient populations overlap; averages are not totals."),
        ("ftft_federal_grants_and_loans", ["FGRNT_N", "FGRNT_T", "FLOAN_N", "FLOAN_T", "OFGRT_N", "OFGRT_T"],
         ["grant__pell_disbursements", "campus__fseog_disbursements"], "excluded_ftft_population_and_program_mismatch",
         "These IPEDS measures describe full-time, first-time undergraduates; federal/other-federal grants can include FSEOG plus other programs.",
         "FSA family totals do not isolate FTFT students. No dedicated FSEOG series was identified in the supplied SFA dictionary titles; "
         "a broad federal-grant category cannot isolate FSEOG, and Campus-Based actual disbursements can include matching funds/transfers."),
        ("federal_work_study", ["ANYAIDN", "SCFA2", "SLO2"],
         ["campus__fws_recipients", "campus__fws_disbursements", "campus__fws_federal_award"], "excluded_no_dedicated_sfa_volume_counterpart",
         "SFA includes Work Study in some any-aid eligibility groups, but survey instructions exclude Work Study dollars from reported aid totals.",
         "No dedicated FWS recipient/dollar series was identified in the supplied SFA dictionary titles. Any-aid counts include other programs; "
         "the IC cooperative work-study program indicator SLO2 is not FWS volume. Campus federal awards are allocations, not actual disbursements."),
        ("pell_finance_dollars", ["F1E01", "F2C01", "F3C01", "F2PELL", "F3PELL"],
         ["grant__pell_disbursements"], "documented_clue_requires_fiscal_scope_accounting_review",
         "Finance Pell dollar fields exist under different reporting standards. F1E01 describes gross amounts disbursed/made available; "
         "F2C01/F3C01 describe administered amounts. F2PELL/F3PELL are accounting flags, not dollars.",
         "Finance fiscal years need institution-specific beginning/end dates; PRCH_F/IDX_F and finance group scope need review. "
         "Pass-through versus revenue accounting and source-form differences preclude automatic coalescing or comparison to award-year amounts."),
    ]
    records = []
    all_variables = set()
    for name, variables, fsa_variables, status, program, caveat in specifications:
        all_variables.update(variables)
        selected = dictionary[dictionary.varname.isin(variables)]
        actual = sorted(c for c in ipeds_columns if any(c == v or c.startswith(v + "__") for v in variables))
        records.append({
            "comparison_id": name, "assessment": status,
            "quantitative_benchmark_in_this_build": status in {"quantitative_benchmark", "supplemental_count_only_benchmark"},
            "ipeds_variables_json": json.dumps(variables), "ipeds_actual_columns_json": json.dumps(actual),
            "ipeds_dictionary_years_json": json.dumps(sorted(int(y) for y in selected.year.unique())),
            "fsa_candidate_fields_json": json.dumps(fsa_variables),
            "fsa_fields_present_json": json.dumps([v for v in fsa_variables if v in fsa_columns]),
            "program_and_population_assessment": program, "exclusion_or_review_reason": caveat,
            "time_and_basis_rule": "SFA collection year is distinct from the aid reference period; Finance uses fiscal years; "
                "FSA uses source award-year cohorts. Reconcile periods, awards/disbursements, and campus scope before comparing.",
            "identity_rule": "A discrepancy or similar volume is review evidence only; no UNITID reassignment, parent allocation, or coalescing.",
            "source_documentation": "Supplied annual IPEDS dictionary; FSA release codebook and Documentation/loan_harmonization.md; cited official survey/FSA guidance.",
            "source_urls_json": json.dumps([source["url"] for source in SOURCES]),
        })
    annual = dictionary[dictionary.varname.isin(all_variables)].copy()
    annual["actual_panel_columns_json"] = annual.varname.map(
        lambda v: json.dumps(sorted(c for c in ipeds_columns if c == v or c.startswith(v + "__"))))
    return pd.DataFrame(records), annual


def source_metadata_audit(dictionary: pd.DataFrame, lineage: pd.DataFrame | None) -> pd.DataFrame:
    """Compare actual annual value origins with dictionary source identities.

    Matching a column name and intended definition is weaker than verifying its
    originating table. Keep numerical diagnostics when that provenance conflicts,
    but never call those rows metadata verified.
    """
    required_lineage = {"year", "analysis_column", "variable_id", "source_file", "access_table_name"}
    if lineage is not None and not required_lineage.issubset(lineage):
        raise ValueError(f"Lineage lacks {sorted(required_lineage - set(lineage))}")
    def token(value):
        return "" if value is None or pd.isna(value) else str(value).strip()
    records = []
    selected = dictionary[dictionary.varname.isin(["UPGRNTN", "UPGRNTT", "TSTDPEL"])]
    for (year, variable), definitions in selected.groupby(["year", "varname"], sort=True):
        if len(definitions) != 1:
            raise ValueError(f"Ambiguous annual source metadata for {variable}/{year}")
        definition = definitions.iloc[0]
        expected = {c: token(definition.get(c)) for c in ("variable_id", "source_file", "access_table_name")}
        actual = (lineage[lineage.year.eq(year) & lineage.analysis_column.eq(variable)].drop_duplicates()
                  if lineage is not None else pd.DataFrame())
        actual_sources = sorted({tuple(token(r.get(c)).casefold() for c in expected) for _, r in actual.iterrows()})
        target = tuple(v.casefold() for v in expected.values())
        if lineage is None:
            status = "lineage_unavailable"
        elif not all(expected.values()):
            status = "dictionary_source_identity_incomplete"
        elif actual.empty:
            status = "lineage_entry_missing"
        elif len(actual_sources) != 1:
            status = "ambiguous_lineage_sources"
        elif actual_sources[0][:2] != target[:2]:
            status = "variable_or_source_family_conflict"
        elif actual_sources[0][2] != target[2]:
            status = "source_table_conflict"
        elif ("transformation_id" in actual and not actual.transformation_id.map(token).str.casefold().eq("identity").all()
              or "lineage_role" in actual and not actual.lineage_role.map(token).str.casefold().eq("direct").all()):
            status = "nonidentity_lineage_requires_review"
        else:
            status = "verified_annual_source_identity"
        origins = [{str(k): token(v) for k, v in r.items()} for r in actual.to_dict("records")]
        records.append({
            "year": int(year), "variable": variable, "source_metadata_status": status,
            "source_metadata_verified": status == "verified_annual_source_identity",
            "dictionary_variable_id": expected["variable_id"], "dictionary_source_file": expected["source_file"],
            "dictionary_access_table_name": expected["access_table_name"],
            "lineage_source_count": len(actual_sources),
            "lineage_access_tables_json": json.dumps(sorted({token(v) for v in actual.get("access_table_name", [])})),
            "lineage_source_records_json": json.dumps(origins, sort_keys=True),
            **{column: token(definition.get(column)) for column in SOURCE_PROVENANCE_COLUMNS},
            "interpretation": "Raw intended-variable comparisons are diagnostic only; source conflicts/unknowns are excluded from metadata-verified scope sensitivities.",
        })
    return pd.DataFrame(records, columns=[
        "year", "variable", "source_metadata_status", "source_metadata_verified", "dictionary_variable_id",
        "dictionary_source_file", "dictionary_access_table_name", "lineage_source_count", "lineage_access_tables_json",
        "lineage_source_records_json", *SOURCE_PROVENANCE_COLUMNS, "interpretation"])



def _source_audit_narrative(audit: pd.DataFrame) -> str:
    """Describe the supplied evidence without assuming a particular release."""
    counts = audit.source_metadata_status.value_counts().sort_index()
    statuses = ", ".join(f"{status}: {int(count)}" for status, count in counts.items()) or "no annual entries"
    parts = [
        f"Current frozen-input annual source audit: {statuses}. "
        "The audit is recomputed from each invocation's dictionary and value lineage. "
        "Only verified annual source identities enter metadata-verified scope sensitivities; "
        "verification does not establish identical reporting populations or campus coverage."
    ]
    corrections = audit[audit.metadata_correction_id.ne("")]
    if not corrections.empty:
        entries = [
            f"{int(row.year)} {row.variable}: {row.original_access_table_name or 'original table unspecified'} "
            f"to {row.dictionary_access_table_name} ({row.metadata_correction_id}; {row.source_metadata_status})"
            for row in corrections.itertuples(index=False)
        ]
        parts.append(
            "Documented upstream corrections retained in `pell_annual_source_metadata_audit.csv`: "
            + "; ".join(entries) + ". Original metadata, source hashes, correction references, "
            "and reference periods remain in the annual evidence. A correction record does not "
            "override a remaining dictionary/lineage discrepancy."
        )
    availability = audit[audit.imputation_flag_availability.ne("")]
    if not availability.empty:
        entries = [f"{int(row.year)} {row.variable}: {row.imputation_flag_availability}"
                   for row in availability.itertuples(index=False)]
        parts.append("Supplied annual metadata documents item-level imputation-flag availability: "
                     + "; ".join(entries) + ".")
    parts.append(
        "A dictionary imputation-variable association does not establish that flag values exist "
        "in the supplied panel. Missing item-level flags do not establish that every value was "
        "reported or that no imputation occurred. Component-level status is separate from "
        "item-level status; no missing item flag is inferred from Pell amounts."
    )
    return "\n\n".join(parts)


def _legacy_2008_pell(ipeds: pd.DataFrame, fsa: pd.DataFrame,
                      dictionary: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Keep TSTDPEL separate from the primary UPGRNTN/UPGRNTT benchmark."""
    empty_rows = pd.DataFrame(columns=["UNITID", "year", "offset", "source_column", "recipients_valid_pair"])
    empty_summary = pd.DataFrame(columns=["benchmark", "measure", "sample", "offset", "comparable_rows"])
    metadata = {"source_column": "TSTDPEL", "collection_year": 2008, "dollar_comparison": False,
                "primary_benchmark_modified": False}
    evidence = dictionary[dictionary.varname.eq("TSTDPEL") & dictionary.year.eq(2008)]
    if "TSTDPEL" not in ipeds or evidence.empty:
        return empty_rows, empty_summary, {**metadata, "status": "source_column_or_dictionary_unavailable"}
    if len(evidence) != 1:
        raise ValueError("Ambiguous 2008 TSTDPEL dictionary")
    record = evidence.iloc[0]
    title = str(record.varTitle).lower()
    if ("undergraduate" not in title or "pell" not in title or "number" not in title
            or "first-time" in title or "first time" in title or str(record.source_file_label) != "SFA0708"):
        return empty_rows, empty_summary, {**metadata, "status": "population_or_source_period_not_verified"}
    base = ipeds[ipeds.year.eq(2008)].copy()
    if base.empty:
        return empty_rows, empty_summary, {**metadata, "status": "collection_year_not_in_supplied_panel"}
    pieces = []
    for offset in OFFSETS:
        left = base.copy()
        left["offset"] = offset
        left["fsa_award_year_start"] = left.year + offset
        merged = left.merge(fsa, how="left", on=["UNITID", "fsa_award_year_start"], validate="one_to_one", indicator="_fsa")
        merged["fsa_row_present"] = merged.pop("_fsa").eq("both")
        a, b = _numeric(merged.TSTDPEL), _numeric(merged.grant__pell_recipients)
        valid = (np.isfinite(a) & np.isfinite(b) & a.ge(0) & b.ge(0) & a.mod(1).eq(0) & b.mod(1).eq(0)
                 & merged.grant__unitid_record_status.eq("included_unique_family_record")
                 & merged.grant__pell_recipients__status.isin(["observed", "observed_zero"]))
        merged["source_column"] = "TSTDPEL"
        merged["source_metadata_verified"] = merged.TSTDPEL__source_metadata_verified
        merged["source_metadata_status"] = merged.TSTDPEL__source_metadata_status
        merged["documented_reference_award_year_start"] = 2007
        merged["documented_reference_year_agrees"] = merged.fsa_award_year_start.eq(2007)
        merged["recipients_valid_pair"] = valid
        merged["recipients_ipeds_value"] = a
        merged["recipients_fsa_value"] = b
        merged["recipients_difference"] = (b - a).where(valid)
        merged["recipients_absolute_difference"] = (b - a).abs().where(valid)
        merged["recipients_relative_difference"] = ((b - a) / a.where(a.gt(0))).where(valid)
        merged["recipients_exact_equal"] = b.eq(a).astype("boolean").where(valid)
        merged["comparison_limit"] = "Supplemental count-only diagnostic; cohort/reporting scope differ; no dollar inference or UNITID reassignment."
        # Do not put primary amounts or averages in the supplemental comparison.
        drop = [c for c in merged if c.startswith(("UPGRNT", "grant__pell_disbursements"))]
        pieces.append(merged.drop(columns=drop))
    rows = pd.concat(pieces, ignore_index=True)
    rows["common_all_four_offsets"] = rows.groupby(["UNITID", "year"]).recipients_valid_pair.transform("all")
    summaries = []
    for sample, selector in (("available_pairs", "recipients_valid_pair"), ("common_all_four_offsets", "common_all_four_offsets")):
        for offset in OFFSETS:
            selected = rows[rows.offset.eq(offset) & rows[selector]]
            summaries.append({"benchmark": "legacy_2008_TSTDPEL_count_only", "measure": "recipients",
                              "sample": sample, "offset": offset, **_metric_summary(selected, "recipients")})
    return rows, pd.DataFrame(summaries), {**metadata, "status": "verified_separate_count_only_diagnostic",
        "source_file_label": "SFA0708", "reference_award_year_start": 2007, "ipeds_rows": len(base),
        "source_metadata_status": str(base.TSTDPEL__source_metadata_status.iloc[0]),
        "source_metadata_verified": bool(base.TSTDPEL__source_metadata_verified.iloc[0]),
        "common_sample_rows": int(rows[rows.offset.eq(0)].common_all_four_offsets.sum())}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _codes(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.replace(r"\.0+$", "", regex=True)


def _key(frame: pd.DataFrame, id_col: str, year_col: str) -> None:
    for name in (id_col, year_col):
        if name not in frame:
            raise ValueError(f"Missing key {name}")
        values = _numeric(frame[name])
        if not (np.isfinite(values) & values.gt(0) & values.mod(1).eq(0)).all():
            raise ValueError(f"Invalid {name} key")
        frame[name] = values.astype("int64")
    if frame.duplicated([id_col, year_col]).any():
        raise ValueError(f"Duplicate {id_col}/{year_col} keys")


def _dictionary_rules(dictionary: pd.DataFrame, panel_columns: set[str]) -> tuple[pd.DataFrame, dict]:
    required = {"year", "varname", "varTitle", "longDescription", "source_file_label"}
    if not required.issubset(dictionary):
        raise ValueError(f"IPEDS dictionary lacks {sorted(required - set(dictionary))}")
    evidence = dictionary[dictionary.varname.isin(AUDITED_VARIABLES)].copy()
    evidence["comparison_role"] = evidence.varname.map({
        "UPGRNTN": "all_undergraduate_recipient_count", "UPGRNTT": "all_undergraduate_total_dollars",
        "UPGRNTA": "average_only_not_a_total", "PGRNT_N": "excluded_ftft_population",
        "PGRNT_T": "excluded_ftft_population", "UDGPGRNTN": "excluded_degree_seeking_subset",
        "UDGPGRNTT": "excluded_degree_seeking_subset", "UNDPGRNTN": "excluded_nondegree_subset",
        "UNDPGRNTT": "excluded_nondegree_subset",
    })
    evidence["present_in_supplied_panel_schema"] = evidence.varname.isin(panel_columns)
    rules = {}
    for variable in ("UPGRNTN", "UPGRNTT"):
        rows = evidence[evidence.varname.eq(variable)]
        if rows.duplicated("year").any():
            raise ValueError(f"Ambiguous annual dictionary for {variable}")
        for _, row in rows.iterrows():
            title = str(row.varTitle).lower()
            description = str(row.longDescription).lower()
            if ("pell" not in title or "undergraduate" not in title
                    or "first-time" in title or "first time" in title
                    or any(word in title for word in ("degree/certificate-seeking", "non-degree", "part-time", "full-time"))
                    or "full-time, first-time" in description):
                raise ValueError(f"{variable}/{row.year} is not verified as all-undergraduate Pell")
            if variable == "UPGRNTT" and ("total" not in title or "average" in title):
                raise ValueError(f"{variable}/{row.year} is not a total-dollar variable")
            if variable == "UPGRNTN" and "number" not in title:
                raise ValueError(f"{variable}/{row.year} is not a recipient count")
            match = re.fullmatch(r"SFA([0-9]{2})([0-9]{2})(?:_P[0-9]+)?", str(row.source_file_label))
            reference_start = None
            if match:
                start = (int(row.year) // 100) * 100 + int(match[1])
                if start > int(row.year):
                    start -= 100
                if (start + 1) % 100 != int(match[2]):
                    raise ValueError(f"Invalid SFA source-period label: {row.source_file_label}")
                reference_start = start
            rules[(int(row.year), variable)] = {
                "available": variable in panel_columns, "reference_start": reference_start,
                "source_file_label": str(row.source_file_label),
            }
    evidence["reference_award_year_start_from_source_label"] = [
        rules.get((int(r.year), r.varname), {}).get("reference_start") for _, r in evidence.iterrows()]
    return evidence, rules


def _metric_summary(rows: pd.DataFrame, measure: str) -> dict:
    n = len(rows)
    ipeds = rows[f"{measure}_ipeds_value"]
    fsa = rows[f"{measure}_fsa_value"]
    difference = rows[f"{measure}_difference"]
    absolute = difference.abs()
    positive = ipeds.gt(0)
    denominator = float(ipeds.sum()) if n else None
    return {
        "comparable_rows": n, "positive_ipeds_denominator_rows": int(positive.sum()),
        "zero_ipeds_denominator_rows": int(ipeds.eq(0).sum()),
        "exact_equal_rows": int(difference.eq(0).sum()),
        "within_1pct_rows_positive_denominator": int(rows[f"{measure}_relative_difference"].abs().le(.01).sum()),
        "ipeds_total_comparable_cells": denominator,
        "fsa_total_comparable_cells": float(fsa.sum()) if n else None,
        "signed_difference_total": float(difference.sum()) if n else None,
        "absolute_difference_total": float(absolute.sum()) if n else None,
        "weighted_absolute_relative_difference": float(absolute.sum() / denominator) if denominator and denominator > 0 else None,
        "signed_relative_total_difference": float(difference.sum() / denominator) if denominator and denominator > 0 else None,
        "median_absolute_difference": float(absolute.median()) if n else None,
        "median_absolute_relative_difference_positive_denominator":
            float(rows.loc[positive, f"{measure}_relative_difference"].abs().median()) if positive.any() else None,
    }


def build_pell_validation(ipeds: pd.DataFrame, fsa: pd.DataFrame, dictionary: pd.DataFrame,
                          bridge: pd.DataFrame, output_dir: str | Path,
                          lineage: pd.DataFrame | None = None) -> dict:
    """Compare existing institution identities across four offsets on common samples."""
    ipeds, fsa = ipeds.copy(), fsa.copy()
    _key(ipeds, "UNITID", "year")
    _key(fsa, "unitid", "award_year_start")
    original_ipeds_columns = set(ipeds)
    catalog, catalog_evidence = measure_comparability_catalog(
        dictionary, set(ipeds.attrs.get("full_source_schema_columns", original_ipeds_columns)),
        set(fsa.attrs.get("full_source_schema_columns", set(fsa))))
    evidence, rules = _dictionary_rules(dictionary, original_ipeds_columns)
    annual_source_audit = source_metadata_audit(dictionary, lineage)
    for variable in ("UPGRNTN", "UPGRNTT", "TSTDPEL"):
        annual = annual_source_audit[annual_source_audit.variable.eq(variable)].set_index("year")
        for column, default in (("source_metadata_status", "dictionary_entry_unavailable"),
                                ("source_metadata_verified", False),
                                ("dictionary_access_table_name", ""), ("lineage_access_tables_json", "[]")):
            mapped = ipeds.year.map(annual[column])
            if column == "source_metadata_verified":
                mapped = mapped.astype("boolean")
            ipeds[variable + "__" + column] = mapped.fillna(default)
    required_fsa = {c for _, c in MEASURES.values()} | {c + "__status" for _, c in MEASURES.values()} | {"grant__unitid_record_status"}
    if not required_fsa.issubset(fsa):
        raise ValueError(f"FSA panel lacks {sorted(required_fsa - set(fsa))}")
    optional_ipeds = ("OPEID", "INSTNM", "PRCH_SFA", "IDX_SFA", "RPTMTH", "CALSYS", "IMP_SFA", "REV_SFA", "SFAFORM", "UPGRNTA")
    for column in (*optional_ipeds, "UPGRNTN", "UPGRNTT"):
        if column not in ipeds:
            ipeds[column] = pd.NA
    for column in FSA_CONTEXT:
        if column not in fsa:
            fsa[column] = pd.NA
    for variable in ("UPGRNTN", "UPGRNTT"):
        ipeds[variable + "__definition_available"] = ipeds.year.map(lambda y: rules.get((y, variable), {}).get("available", False))
        ipeds[variable + "__reference_start"] = ipeds.year.map(lambda y: rules.get((y, variable), {}).get("reference_start"))
    ipeds["reporting_population"] = _codes(ipeds.RPTMTH).map({
        "1": "academic_fall_cohort", "2": "program_full_year_cohort", "3": "academic_full_year_cohort",
    }).fillna("unknown_reporting_population")
    ipeds["ipeds_opeid8"] = ipeds.OPEID.map(normalize_full_opeid).astype("string")
    ipeds["ipeds_sfa_parent_child_flag"] = _codes(ipeds.PRCH_SFA).map({
        "-2": "no_parent_child_relation_reported", "1": "parent", "2": "child",
    }).fillna("missing_or_undocumented")
    ipeds["ipeds_sfa_component_imputation_flag"] = _codes(ipeds.IMP_SFA).isin(["1", "2", "3"])
    # No absent imputation flag is treated as proof that every cell was reported.
    imputation_columns = [c for c in ("XUPGRNTN", "XUPGRNTT", "XUPGRNTA") if c in ipeds]
    ipeds["ipeds_pell_cell_imputation_flags_available"] = len(imputation_columns) == 3
    fsa = fsa.rename(columns={"unitid": "UNITID", "award_year_start": "fsa_award_year_start"})
    legacy_rows, legacy_summary, legacy_metadata = _legacy_2008_pell(ipeds, fsa, dictionary)
    pieces = []
    for offset in OFFSETS:
        left = ipeds.copy()
        left["offset"] = offset
        left["fsa_award_year_start"] = left.year + offset
        merged = left.merge(fsa, on=["UNITID", "fsa_award_year_start"], how="left", validate="one_to_one", indicator="_fsa")
        merged["fsa_row_present"] = merged._fsa.eq("both")
        merged = merged.drop(columns="_fsa")
        merged["grant_family_included"] = merged.grant__unitid_record_status.eq("included_unique_family_record")
        merged["source_full_opeid_agrees"] = (merged.ipeds_opeid8.eq(merged.grant__opeid8)
            .astype("boolean").where(merged.ipeds_opeid8.notna() & merged.grant__opeid8.notna()))
        idx = _numeric(merged.IDX_SFA)
        merged["scope_screen_passed"] = (
            merged.ipeds_sfa_parent_child_flag.eq("no_parent_child_relation_reported")
            & (idx.isna() | idx.le(0) | idx.eq(merged.UNITID))
            & merged.grant__ipeds_student_aid_scope.eq("no_parent_child_relation_reported")
            & ~merged.ipeds_sfa_component_imputation_flag
        )
        for measure, (ipeds_column, fsa_column) in MEASURES.items():
            a, b = _numeric(merged[ipeds_column]), _numeric(merged[fsa_column])
            definition = merged[ipeds_column + "__definition_available"]
            status_ok = merged[fsa_column + "__status"].isin(["observed", "observed_zero"])
            numeric_ok = np.isfinite(a) & np.isfinite(b) & a.ge(0) & b.ge(0)
            if measure == "recipients":
                numeric_ok &= a.mod(1).eq(0) & b.mod(1).eq(0)
            valid = definition & merged.grant_family_included & status_ok & numeric_ok
            merged[measure + "_valid_pair"] = valid
            reason = pd.Series("comparable_numeric_pair_population_limits_apply", index=merged.index)
            reason.loc[~numeric_ok] = "missing_negative_nonfinite_or_invalid_numeric"
            reason.loc[~status_ok] = "fsa_value_not_observed"
            reason.loc[~merged.grant_family_included] = "fsa_grant_family_not_included"
            reason.loc[~merged.fsa_row_present] = "no_fsa_unitid_row"
            reason.loc[~definition] = "all_undergraduate_measure_unavailable_in_dictionary_or_panel"
            merged[measure + "_comparison_status"] = reason
            merged[measure + "_ipeds_value"] = a
            merged[measure + "_fsa_value"] = b
            merged[measure + "_difference"] = (b - a).where(valid)
            merged[measure + "_absolute_difference"] = (b - a).abs().where(valid)
            merged[measure + "_relative_difference"] = ((b - a) / a.where(a.gt(0))).where(valid)
            merged[measure + "_exact_equal"] = (b.eq(a)).astype("boolean").where(valid)
            merged[measure + "_documented_reference_year_agrees"] = (
                merged.fsa_award_year_start.eq(merged[ipeds_column + "__reference_start"])
                .astype("boolean").where(merged[ipeds_column + "__reference_start"].notna()))
            merged[measure + "_source_metadata_verified"] = merged[ipeds_column + "__source_metadata_verified"]
            merged[measure + "_source_metadata_status"] = merged[ipeds_column + "__source_metadata_status"]
            merged[measure + "_scope_screen_valid_pair"] = (
                valid & merged.scope_screen_passed & merged[measure + "_source_metadata_verified"])
        pieces.append(merged)
    rows = pd.concat(pieces, ignore_index=True)
    for measure in MEASURES:
        for sample, source in (("common_all_offsets", measure + "_valid_pair"),
                               ("scope_common_all_offsets", measure + "_scope_screen_valid_pair")):
            rows[measure + "_" + sample] = rows.groupby(["UNITID", "year"])[source].transform("all")
    # Existing official candidates are context, not competing aid assignments.
    selected = [c for c in BRIDGE_CONTEXT if c in bridge]
    if {"opeid8", "award_year"}.issubset(selected):
        bridge_view = bridge[selected].copy()
        if bridge_view.duplicated(["opeid8", "award_year"]).any():
            raise ValueError("Duplicate FSA bridge OPEID/award-year keys")
        rows["_bridge_award_year"] = rows.fsa_award_year_start.astype(str) + "-" + (rows.fsa_award_year_start + 1).astype(str)
        bridge_view = bridge_view.rename(columns={c: "bridge__" + c for c in bridge_view})
        rows = rows.merge(bridge_view, how="left", left_on=["grant__opeid8", "_bridge_award_year"],
                          right_on=["bridge__opeid8", "bridge__award_year"], validate="many_to_one")
        rows = rows.drop(columns="_bridge_award_year")
    rows["identity_or_scope_review_flag"] = (
        rows.source_full_opeid_agrees.eq(False).fillna(False)
        | rows.grant__ipeds_anchor_sensitivity.isin(["different_unitid", "ambiguous_or_unmatched"])
        | rows.ipeds_sfa_parent_child_flag.isin(["parent", "child"])
        | rows.grant__ipeds_student_aid_scope.isin(["parent", "full_child", "partial_child"])
    )
    rows["zero_ipeds_positive_fsa_review_flag"] = (
        (rows.recipients_valid_pair & rows.recipients_ipeds_value.eq(0) & rows.recipients_fsa_value.gt(0))
        | (rows.dollars_valid_pair & rows.dollars_ipeds_value.eq(0) & rows.dollars_fsa_value.gt(0)))
    rows["large_difference_review_flag"] = (rows.recipients_relative_difference.abs().gt(.20)
                                             | rows.dollars_relative_difference.abs().gt(.20)
                                             | rows.zero_ipeds_positive_fsa_review_flag)
    # Available samples answer coverage questions; only common samples support
    # offset comparisons without changing institution-year membership.
    summaries = []
    for measure in MEASURES:
        samples = {
            "available_pairs": measure + "_valid_pair",
            "common_all_four_offsets": measure + "_common_all_offsets",
            "scope_screened_available": measure + "_scope_screen_valid_pair",
            "scope_screened_common_all_four_offsets": measure + "_scope_common_all_offsets",
        }
        for sample, selector in samples.items():
            for offset in OFFSETS:
                selected_rows = rows[rows.offset.eq(offset) & rows[selector]]
                summaries.append({"measure": measure, "sample": sample, "offset": offset,
                    "ipeds_year": "all", "reporting_population": "all", **_metric_summary(selected_rows, measure)})
                for year in sorted(ipeds.year.unique()):
                    subset = selected_rows[selected_rows.year.eq(year)]
                    summaries.append({"measure": measure, "sample": sample, "offset": offset,
                        "ipeds_year": int(year), "reporting_population": "all", **_metric_summary(subset, measure)})
                for population, subset in selected_rows.groupby("reporting_population"):
                    summaries.append({"measure": measure, "sample": sample, "offset": offset,
                        "ipeds_year": "all", "reporting_population": population, **_metric_summary(subset, measure)})
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    def save(name, frame, *, compressed=False):
        path = out / (name + (".csv.gz" if compressed else ".csv"))
        frame.to_csv(path, index=False)
        files[name] = str(path)
    save("pell_row_comparisons_all_offsets", rows, compressed=True)
    save("pell_prior_award_year", rows[rows.offset.eq(-1)], compressed=True)
    save("pell_same_start_year", rows[rows.offset.eq(0)], compressed=True)
    save("pell_identity_scope_review", rows[rows.offset.isin([-1, 0]) & rows.identity_or_scope_review_flag], compressed=True)
    save("pell_large_difference_review", rows[rows.offset.isin([-1, 0]) & rows.large_difference_review_flag], compressed=True)
    save("pell_summary", pd.DataFrame(summaries))
    save("pell_annual_dictionary_evidence", evidence)
    save("pell_annual_source_metadata_audit", annual_source_audit)
    save("other_measure_comparability_catalog", catalog)
    save("other_measure_annual_dictionary_evidence", catalog_evidence)
    save("pell_legacy_2008_count_only", legacy_rows, compressed=True)
    save("pell_legacy_2008_count_only_summary", legacy_summary)
    exclusion = pd.concat([rows.groupby(["year", "offset", m + "_comparison_status"]).size()
                          .rename("rows").reset_index().rename(columns={m + "_comparison_status": "comparison_status"})
                          .assign(measure=m) for m in MEASURES], ignore_index=True)
    save("pell_comparison_eligibility_counts", exclusion)
    summary = {
        "schema_version": "1.0.0", "ipeds_rows": len(ipeds), "comparison_rows": len(rows),
        "offset_definition": "FSA award_year_start = IPEDS year + offset", "offsets": list(OFFSETS),
        "selected_measures": {m: {"ipeds": a, "fsa": b} for m, (a, b) in MEASURES.items()},
        "all_undergraduate_schema_years": {v: sorted({y for (y, name), rule in rules.items() if name == v and rule["available"]})
                                             for v in ("UPGRNTN", "UPGRNTT")},
        "common_sample_rows": {m: int(rows[rows.offset.eq(0)][m + "_common_all_offsets"].sum()) for m in MEASURES},
        "cell_imputation_flags_available": bool(ipeds.ipeds_pell_cell_imputation_flags_available.all()),
        "comparability_catalog_entries": len(catalog),
        "supplemental_2008_count_only": legacy_metadata,
        "source_lineage_available": lineage is not None,
        "annual_source_metadata_status_counts": {str(k): int(v) for k, v in annual_source_audit.source_metadata_status.value_counts().items()},
        "annual_source_metadata_corrections": annual_source_audit.loc[
            annual_source_audit.metadata_correction_id.ne(""),
            ["year", "variable", "metadata_correction_id", "original_access_table_name",
             "dictionary_access_table_name", "source_metadata_status", "metadata_correction_registry_sha256"],
        ].to_dict("records"),
        "documented_item_imputation_flag_availability": annual_source_audit.loc[
            annual_source_audit.imputation_flag_availability.ne(""),
            ["year", "variable", "imputationvar", "imputation_flag_availability"],
        ].to_dict("records"),
        "metadata_verified_schema_years": {variable: annual_source_audit.loc[
            annual_source_audit.variable.eq(variable) & annual_source_audit.source_metadata_verified, "year"].tolist()
            for variable in ("UPGRNTN", "UPGRNTT", "TSTDPEL")},
        "limitations": [
            "Numeric comparability is not equality of population, aid basis, or institution reporting scope.",
            "Academic fall-cohort Pell aid is not the complete FSA annual recipient universe.",
            "IPEDS awarded/accepted aid may differ from FSA disbursements; revisions and imputation may also differ.",
            "Scope screening removes explicit parent/child and component-imputation flags but does not certify exclusive campus aid.",
            "No alternative UNITID or year is automatically selected from volume similarity.",
            "Annual source-table metadata conflicts and missing lineage remain explicit; raw numerical comparisons are retained but excluded from metadata-verified scope sensitivities.",
            "Missing, negative sentinels, suppression and blocked families are not zero; zero denominators have missing relative differences.",
        ],
        "sources": SOURCES, "files": files,
    }
    readme = """# Pell comparison diagnostics

These comparisons audit existing UNITID links. They do not change institutional identities or select an offset automatically.

UPGRNTN is the all-undergraduate Pell recipient count; UPGRNTT is the reported all-undergraduate Pell dollar total. UPGRNTA is an average and is retained only as context. PGRNT_N and PGRNT_T concern full-time, first-time students and are never substituted. The annual dictionary evidence records exact titles, descriptions, source file labels, and availability. The source label SFA0809_P1, for example, identifies 2008-09 aid in collection year 2009. No total is manufactured by multiplying rounded averages.

FSA award_year_start = IPEDS year + offset. Four offsets (-2, -1, 0, +1) are audited. Prior-year and same-start-year row files are supplied separately. All values remain tied to the declared UNITID. Official bridge candidates are retained as evidence; similar amounts never authorize reassignment.

Both cells must be finite, nonnegative, from the dictionary-described all-undergraduate variable, and from an included FSA grant family with observed cell status. Recipient counts must be integers. Annual value lineage is compared separately with dictionary year, variable identity, source family and source-table identity, case-insensitively. `pell_annual_source_metadata_audit.csv` preserves both sides. Missing lineage is unknown, never verified. Raw primary comparisons remain available when intended-variable metadata conflicts, with explicit per-measure flags. Scope-screened samples additionally require verified annual source metadata, no reported SFA parent/child relation in IPEDS and FSA metadata, no other positive IPEDS SFA parent reference, and no flagged component-level IPEDS imputation. This screen is a sensitivity restriction, not campus-allocation certification. Missing item-level imputation flags remain a limitation.

{current_source_audit}

Differences are FSA minus IPEDS. Relative differences divide by positive IPEDS values only. Zero/zero is an exact numeric agreement but has no relative difference. A positive FSA value paired with an IPEDS zero has a separate review flag and still no relative difference. Totals sum exactly the paired valid cells; they are not national totals. Weighted absolute relative difference is sum(abs(FSA-IPEDS))/sum(IPEDS); it is missing when the denominator is zero or the sample empty. Common-sample summaries use the identical UNITID/collection-year rows with valid pairs across all four offsets, separately for recipients and dollars. Available-pair summaries have varying membership and cannot alone justify a preferred offset. Reporter strata distinguish fall cohorts from full-year cohorts. The 20-percent review flag is a descriptive screen, not an error determination.

Academic reporters' fall undergraduate cohorts differ from FSA's full annual recipient universe. Program and hybrid/full-year reporters have other period definitions. IPEDS aid awarded and accepted may differ from amounts disbursed. Parent/child reporting, revisions, institutional changes, and imputation can produce differences without an incorrect UNITID. Review source evidence before drawing conclusions.

Other possible clues are documented in `other_measure_comparability_catalog.csv`, with their actual supplied-panel column names, annual dictionary coverage and reasons for exclusion from numeric benchmarking. `other_measure_annual_dictionary_evidence.csv` preserves the source definitions. UFLOANN/UFLOANT cover all-undergraduate federal student loans, but FSA category counts overlap and loan-program coverage, undergraduate splits and disbursement cohorts differ. All-source grants include nonfederal aid. FTFT federal-grant/loan variables do not represent all students, broad grants cannot isolate FSEOG, and Work Study is excluded from IPEDS SFA aid-dollar totals. Finance Pell fields F1E01/F2C01/F3C01 need fiscal-period, accounting and reporting-scope review; they are not coalesced into UPGRNTT.

The separate `pell_legacy_2008_count_only.csv.gz` and summary compare TSTDPEL when its annual dictionary verifies the all-undergraduate Pell population and SFA0708 source period. This is an explicit 2008 count-only supplement using the same four offsets and common-sample rule. It neither infers dollars nor modifies the primary UPGRNTN/UPGRNTT benchmark. Population and reporting-scope limitations still apply. Other catalog entries are research leads, not additional matched outcomes or identity assignments.

Sources:
"""
    readme = readme.replace("{current_source_audit}", _source_audit_narrative(annual_source_audit))
    for source in SOURCES:
        readme += f"\n- [{source['title']}]({source['url']}): {source['supports']}\n"
    files["README"] = str(out / "README.md")
    Path(files["README"]).write_text(readme)
    files["manifest"] = str(out / "pell_validation_manifest.json")
    Path(files["manifest"]).write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def create_pell_validation(ipeds_path, fsa_path, ipeds_dictionary_path, bridge_path, output_dir,
                           lineage_path: str | Path | None = None) -> dict:
    """Use a supplied DISTINCT annual lineage snapshot, or discover a frozen sibling.

    Discovery checks dictionary/column_lineage.parquet, then
    ipeds_panel_parent/ipeds/column_lineage.parquet, then panel_parent/column_lineage.parquet.
    It never scans the original multi-million-row value-lineage files. Absence is
    explicitly unknown and yields no metadata-verified scope sample.
    """
    ipeds_names = set(pq.read_schema(ipeds_path).names)
    needed = {"UNITID", "year", "OPEID", "INSTNM", "UPGRNTN", "UPGRNTT", "UPGRNTA", "TSTDPEL",
              "PRCH_SFA", "IDX_SFA", "RPTMTH", "CALSYS", "IMP_SFA", "REV_SFA", "SFAFORM",
              "XUPGRNTN", "XUPGRNTT", "XUPGRNTA"}
    ipeds = pd.read_parquet(ipeds_path, columns=sorted(needed & ipeds_names))
    ipeds.attrs["full_source_schema_columns"] = sorted(ipeds_names)
    fsa_names = set(pq.read_schema(fsa_path).names)
    fsa_needed = {"unitid", "award_year_start"} | set(FSA_CONTEXT)
    fsa_needed |= {c for _, c in MEASURES.values()} | {c + "__status" for _, c in MEASURES.values()}
    fsa = pd.read_parquet(fsa_path, columns=sorted(fsa_needed & fsa_names))
    fsa.attrs["full_source_schema_columns"] = sorted(fsa_names)
    dictionary_path = Path(ipeds_dictionary_path)
    dictionary = (pd.read_parquet(dictionary_path) if dictionary_path.suffix == ".parquet"
                  else pd.read_csv(dictionary_path, low_memory=False))
    bridge_names = set(pq.read_schema(bridge_path).names)
    bridge = pd.read_parquet(bridge_path, columns=[c for c in BRIDGE_CONTEXT if c in bridge_names])
    if lineage_path is None:
        candidates = [dictionary_path.parent / "column_lineage.parquet",
                      Path(ipeds_path).parent / "ipeds" / "column_lineage.parquet",
                      Path(ipeds_path).parent / "column_lineage.parquet"]
        lineage_path = next((p for p in candidates if p.is_file()), None)
    if lineage_path is not None and pq.ParquetFile(lineage_path).metadata.num_rows > 1_000_000:
        raise ValueError("Expected a DISTINCT annual column-lineage snapshot, not the full value-lineage file")
    lineage = pd.read_parquet(lineage_path) if lineage_path is not None else None
    result = build_pell_validation(ipeds, fsa, dictionary, bridge, output_dir, lineage=lineage)
    result["input_paths"] = {"ipeds": str(ipeds_path), "fsa": str(fsa_path), "dictionary": str(ipeds_dictionary_path), "bridge": str(bridge_path)}
    with dictionary_path.open("rb") as handle:
        result["dictionary_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    result["source_lineage_path"] = str(lineage_path) if lineage_path is not None else None
    if lineage_path is not None:
        with Path(lineage_path).open("rb") as handle:
            result["source_lineage_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    Path(result["files"]["manifest"]).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("ipeds", "fsa", "ipeds-dictionary", "bridge", "output-dir"):
        parser.add_argument("--" + arg, required=True, type=Path)
    parser.add_argument("--lineage", type=Path, help="Distinct annual column-lineage snapshot (optional)")
    args = parser.parse_args()
    print(json.dumps(create_pell_validation(args.ipeds, args.fsa, args.ipeds_dictionary, args.bridge,
                                           args.output_dir, lineage_path=args.lineage), indent=2))
