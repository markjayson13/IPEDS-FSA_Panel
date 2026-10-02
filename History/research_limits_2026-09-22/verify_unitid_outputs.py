"""Independent read-only checks of V2 master -> family ledgers -> UNITID panel.

Writes analysis evidence only. Does not import the UNITID-construction module.
"""
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone
import json
import hashlib
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "ResearchBuild/2026-09-22-v2"
OUT = Path(__file__).resolve().parent
UROOT = ROOT / "Panels/ipeds/unitid_research"
FAMILIES = {"grants": "grant__", "campus_based": "campus__", "direct_loans": "loan_direct__", "ffel": "loan_ffel__"}


def verify():
    master_path = ROOT / "Panels/final/fsa_volume_reports_panel_1999_2025.parquet"
    unitid_path = UROOT / "fsa_unitid_award_year_panel.parquet"
    master = pd.read_parquet(master_path)
    panel = pd.read_parquet(unitid_path)
    registry = pd.read_csv(REPO / "Metadata/source_identity_resolutions.csv", dtype=str, keep_default_na=False)
    selected_reports = pd.read_csv(ROOT / "Checks/download_qc/selected_panel_files.csv", dtype=str)
    checks, family_summary, yearly, recoveries, orphan_rows, disposition_dollars = [], {}, [], [], [], []

    def check(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    def compare(name, a, b, columns):
        try:
            assert len(a) == len(b), f"Different row counts: {len(a)} and {len(b)}"
            for column in columns:
                x, y = a[column].reset_index(drop=True), b[column].reset_index(drop=True)
                pd.testing.assert_series_equal(x.isna(), y.isna(), check_names=False)
                # Parquet can represent the same missing token as None or pd.NA
                # after adding UNITID-only rows; known values must remain exact.
                pd.testing.assert_series_equal(x.loc[x.notna()], y.loc[y.notna()], check_dtype=False,
                                               check_exact=True, check_names=False)
            check(name, True, {"rows": len(a), "columns": len(columns)})
        except AssertionError as exc:
            check(name, False, str(exc)[:1000])

    check("master_unique_opeid_year", not master.duplicated(["opeid8", "award_year"]).any(), len(master))
    check("master_valid_full_opeid", master.opeid8.astype(str).str.fullmatch(r"[0-9]{8}").all() and master.opeid8.ne("00000000").all(), len(master))
    check("unitid_unique_year", not panel.duplicated(["unitid", "award_year"]).any(), len(panel))
    check("unitid_positive_nonmissing_integer", panel.unitid.notna().all() and panel.unitid.gt(0).all() and panel.unitid.mod(1).eq(0).all(), len(panel))
    check("no_invented_canonical_opeid", "opeid8" not in panel, "Only family-specific full OPEIDs belong in the UNITID view")
    unitid_manifest = json.loads((UROOT / "manifest.json").read_text())
    current_module_digest = hashlib.sha256((REPO / "Scripts/fsa_unitid_panels.py").read_bytes()).hexdigest()
    check("unitid_manifest_matches_frozen_module", unitid_manifest.get("module_sha256") == current_module_digest, current_module_digest)
    check("unitid_dictionary_complete", set(pd.read_csv(UROOT / "dictionary.csv").panel_column) == set(panel), len(panel.columns))
    included_masks, blocked_masks = [], []
    for family, prefix in FAMILIES.items():
        ledger = pd.read_parquet(UROOT / f"{family}_source_record_ledger.parquet")
        source_cols = [c for c in master if c.startswith(prefix)]
        measures = [c for c in source_cols if c + "__status" in source_cols]
        observed = master.loc[master[prefix + "source_record_present"].fillna(False), ["opeid8", "award_year"] + source_cols].copy()
        observed = observed.rename(columns={"opeid8": prefix + "opeid8"}).sort_values([prefix + "opeid8", "award_year"])
        ledger_master = ledger.loc[ledger.record_origin.eq("opeid_master")].sort_values([prefix + "opeid8", "award_year"])
        compare(f"{family}_all_master_cells_present_in_ledger", observed, ledger_master, [prefix + "opeid8", "award_year"] + source_cols)
        check(f"{family}_source_record_ids_unique", not ledger.source_record_id.duplicated().any(), len(ledger))
        check(f"{family}_no_unresolved_candidate_disposition", not ledger.record_disposition.eq("candidate").any(), ledger.record_disposition.value_counts().to_dict())
        included = ledger.loc[ledger.record_disposition.eq("included_unique_family_record")].sort_values(["unitid", "award_year"])
        included_mask = panel[prefix + "unitid_record_status"].eq("included_unique_family_record")
        blocked_mask = ~panel[prefix + "unitid_record_status"].isin(["included_unique_family_record", "absent_source_record"])
        included_masks.append(included_mask)
        blocked_masks.append(blocked_mask)
        check(f"{family}_included_unique_unitid_year", not included.duplicated(["unitid", "award_year"]).any(), len(included))
        output = panel.loc[included_mask].sort_values(["unitid", "award_year"])
        columns = ["unitid", "award_year"] + [c for c in ledger if c.startswith(prefix)]
        compare(f"{family}_included_cells_not_changed_or_repeated", included, output, columns)
        check(f"{family}_included_identity_explicitly_eligible", output[prefix + "ipeds_annual_identity_eligible"].astype(str).eq("True").all(), len(output))
        check(f"{family}_blocked_source_measure_cells_missing", panel.loc[blocked_mask, measures].isna().all().all(), int(blocked_mask.sum()))
        schema = pd.read_csv(ROOT / "Checks/observation_qc" / f"{family}_schema_availability.csv", dtype=str)
        report_years = set(selected_reports.loc[selected_reports.family.eq(family), "award_year"])
        absent = panel[prefix + "unitid_record_status"].eq("absent_source_record")
        report_available = panel.award_year.isin(report_years)
        status_mismatches = {}
        for column in measures:
            available_years = set(schema.loc[schema.column.eq(column), "award_year"])
            expected = pd.Series("report_not_available", index=panel.index)
            expected.loc[report_available] = "unavailable_in_schema"
            expected.loc[report_available & panel.award_year.isin(available_years)] = "absent_source_record"
            statuses = panel[column + "__status"].astype("string")
            differences = int((statuses.loc[absent].ne(expected.loc[absent])).fillna(True).sum())
            differences += int((statuses.loc[blocked_mask].ne(panel.loc[blocked_mask, prefix + "unitid_record_status"])).fillna(True).sum())
            if differences:
                status_mismatches[column] = differences
        check(f"{family}_missing_cell_status_matches_report_and_schema", not status_mismatches, status_mismatches)
        check(f"{family}_scope_notes_nonmissing", panel[prefix + "unitid_aid_scope_note"].fillna("").ne("").all(), "Scope uncertainty remains independent of identity eligibility")
        dollar_columns = [c for c in measures if c.endswith(("_disbursements", "_amt"))]
        for (year, disposition, origin), group in ledger.groupby(["award_year", "record_disposition", "record_origin"]):
            for column in dollar_columns:
                values = pd.to_numeric(group[column])
                disposition_dollars.append({"family": family, "award_year": year, "record_disposition": disposition,
                    "record_origin": origin, "column": column, "units": "nominal_usd", "source_record_count": len(group),
                    "observed_amount_cells": int(values.notna().sum()), "unknown_amount_cells": int(values.isna().sum()),
                    "known_source_dollar_sum": values.sum(min_count=1)})
        if family == "campus_based":
            check("campus_combined_fisap_scope_not_certified", panel[prefix + "unitid_aid_scope_note"].str.contains("FISAP").all(), "Separate main OPEIDs can be combined in FISAP")
        for year, group in observed.groupby("award_year"):
            selected = included.loc[included.award_year.eq(year)]
            excluded = ledger.loc[ledger.award_year.eq(year) & ~ledger.record_disposition.eq("included_unique_family_record")]
            for column in measures:
                yearly.append({"family": family, "award_year": year, "column": column,
                               "master_source_records": len(group), "included_records": len(selected), "excluded_records": len(excluded),
                               "master_known_sum": pd.to_numeric(group[column]).sum(min_count=1),
                               "included_known_sum": pd.to_numeric(selected[column]).sum(min_count=1),
                               "excluded_known_sum": pd.to_numeric(excluded[column]).sum(min_count=1)})
        refs = master[prefix + "source_identity_resolution"].dropna()
        restored = master.loc[master[prefix + "source_identity_resolution"].notna(), ["opeid8", "award_year", prefix + "source_identity_resolution"]].copy()
        for r in restored.to_dict("records"):
            decision = registry.loc[registry.resolution_id.eq(r[prefix + "source_identity_resolution"])]
            check("recovered_id_" + r[prefix + "source_identity_resolution"], len(decision) == 1 and decision.iloc[0].resolution_status == "approved" and decision.iloc[0].recovered_opeid8 == r["opeid8"], r)
            recoveries.append({"family": family, "opeid8": r["opeid8"], "award_year": r["award_year"], "resolution_id": r[prefix + "source_identity_resolution"]})
        orphans = ledger.loc[ledger.record_origin.eq("verified_quarantine_unitid_only")]
        check(f"{family}_verified_orphans_have_no_fabricated_opeid", orphans[prefix + "opeid8"].isna().all(), len(orphans))
        quarantine = pd.read_parquet(ROOT / "Checks/observation_qc" / f"{family}_quarantine.parquet")
        raw_accounting = pd.read_parquet(ROOT / "Checks/observation_qc" / f"{family}_row_ledger.parquet", columns=["row_class"])
        raw_institutions = raw_accounting.row_class.isin(["accepted_institution", "quarantined_institution"]).sum()
        check(f"{family}_all_raw_institution_records_accounted", raw_institutions == len(observed) + len(quarantine),
              {"raw_institution_rows": int(raw_institutions), "master_records": len(observed), "raw_quarantined_records": len(quarantine)})
        orphan_keys = set(zip(orphans.award_year, orphans.get(prefix + "source_filename", []), orphans.get(prefix + "source_excel_row", [])))
        unresolved_quarantine = quarantine.loc[[tuple(row) not in orphan_keys for row in quarantine[["award_year", "filename", "source_excel_row"]].to_numpy()]]
        for year, group in unresolved_quarantine.groupby("award_year"):
            for column in dollar_columns:
                raw_column = column[len(prefix):]
                if raw_column not in group:
                    continue
                tokens = group[raw_column].astype("string").str.strip().str.replace(",", "", regex=False).str.replace("$", "", regex=False)
                tokens = tokens.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
                numeric = tokens.str.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)").fillna(False)
                values = pd.to_numeric(tokens.where(numeric), errors="raise")
                disposition_dollars.append({"family": family, "award_year": year, "record_disposition": "quarantined_identity_unresolved",
                    "record_origin": "raw_source_quarantine", "column": column, "units": "nominal_usd", "source_record_count": len(group),
                    "observed_amount_cells": int(values.notna().sum()), "unknown_amount_cells": int(values.isna().sum()),
                    "known_source_dollar_sum": values.sum(min_count=1)})
        if not orphans.empty:
            for row in orphans.to_dict("records"):
                raw = quarantine.loc[quarantine.award_year.eq(row["award_year"]) & quarantine.filename.eq(row[prefix + "source_filename"])
                                     & quarantine.source_excel_row.eq(row[prefix + "source_excel_row"])]
                check("orphan_unique_raw_row_" + row[prefix + "source_identity_resolution"], len(raw) == 1, row[prefix + "source_identity_resolution"])
                differences = []
                if len(raw) == 1:
                    for column in measures:
                        token = raw.iloc[0].get(column[len(prefix):])
                        if token is None or pd.isna(token):
                            continue
                        token = str(token).strip().replace(",", "").replace("$", "")
                        try:
                            expected = Decimal(token)
                        except Exception:
                            continue
                        actual = row.get(column)
                        if pd.isna(actual) or abs(Decimal(str(actual)) - expected) > Decimal("0.000001"):
                            differences.append(column)
                check("orphan_amounts_equal_raw_" + row[prefix + "source_identity_resolution"], not differences, differences)
                orphan_rows.append({"family": family, "unitid": row["unitid"], "award_year": row["award_year"],
                    "record_disposition": row["record_disposition"], "resolution_id": row[prefix + "source_identity_resolution"],
                    **{c: row[c] for c in measures}})
        family_summary[family] = {"master_records": len(observed), "ledger_records": len(ledger), "included_records": len(included),
                                  "dispositions": ledger.record_disposition.value_counts().to_dict(), "recovered_full_ids": len(refs),
                                  "unitid_only_verified_orphans": len(orphans), "source_measure_columns": len(measures)}
    expected_included = sum(mask.astype(int) for mask in included_masks)
    expected_blocked = sum(mask.astype(int) for mask in blocked_masks)
    check("source_family_count_matches_cells", panel.included_source_family_count.eq(expected_included).all(), expected_included.value_counts().to_dict())
    check("blocked_family_count_matches_cells", panel.blocked_source_family_count.eq(expected_blocked).all(), expected_blocked.value_counts().to_dict())
    blocked_only = expected_included.eq(0) & expected_blocked.gt(0)
    check("no_empty_unexplained_unitid_rows", (expected_included.gt(0) | expected_blocked.gt(0)).all(), len(panel))
    approved = set(registry.loc[registry.resolution_status.eq("approved"), "resolution_id"])
    check("all_approved_full_id_recoveries_appear", {r["resolution_id"] for r in recoveries} == approved, {"expected": len(approved), "actual": len(recoveries)})
    verified = set(registry.loc[registry.unitid_resolution_status.eq("verified_component_identity_opeid_unresolved"), "resolution_id"])
    check("all_verified_unitid_orphans_accounted", {r["resolution_id"] for r in orphan_rows} == verified, {"expected": len(verified), "actual": len(orphan_rows)})
    result = {"verified_utc": datetime.now(timezone.utc).isoformat(), "master_rows": len(master), "unitid_rows": len(panel), "unique_unitids": int(panel.unitid.nunique()),
              "unitid_columns": len(panel.columns), "blocked_only_rows": int(blocked_only.sum()),
              "blocked_only_by_year": panel.loc[blocked_only].groupby("award_year").size().to_dict(),
              "families": family_summary, "checks": checks, "all_passed": all(c["passed"] for c in checks),
              "input_sha256": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  [master_path, unitid_path, UROOT / "manifest.json", REPO / "Metadata/source_identity_resolutions.csv"]}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "independent_unitid_verification.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    pd.DataFrame(yearly).to_csv(OUT / "source_family_measure_totals.csv", index=False)
    pd.DataFrame(recoveries).to_csv(OUT / "verified_full_opeid_recoveries.csv", index=False)
    pd.DataFrame(orphan_rows).to_csv(OUT / "verified_unitid_only_source_values.csv", index=False)
    dollars = pd.DataFrame(disposition_dollars)
    dollars.to_csv(OUT / "source_family_year_disposition_dollars.csv", index=False)
    disposition_keys = ["family", "award_year", "record_disposition", "record_origin"]
    coverage = dollars.groupby(disposition_keys).source_record_count.first().to_frame()
    amounts = dollars.pivot(index=disposition_keys, columns="column", values="known_source_dollar_sum")
    coverage.join(amounts).reset_index().to_csv(OUT / "source_family_year_disposition_coverage.csv", index=False)
    print(json.dumps({k: v for k, v in result.items() if k not in {"checks", "input_sha256"}}, indent=2))
    print("checks", len(checks), "failed", [c for c in checks if not c["passed"]])


def write_identity_reason_dollars():
    """Separate unresolved mapping, administrative identity and source collisions."""
    rows = []
    for family, prefix in FAMILIES.items():
        ledger = pd.read_parquet(UROOT / f"{family}_source_record_ledger.parquet")
        measures = [c for c in ledger if c.startswith(prefix) and c.endswith(("_disbursements", "_amt")) and c + "__status" in ledger]
        reason = ledger[prefix + "ipeds_resolution_status"].astype("string").fillna("annual_identity_reason_missing")
        administrative = ledger.get(prefix + "ipeds_identity_is_administrative", pd.Series(False, index=ledger.index)).astype("string").str.lower().eq("true").fillna(False)
        geography = ledger.get(prefix + "ipeds_identity_geography_conflict", pd.Series(False, index=ledger.index)).astype("string").str.lower().eq("true").fillna(False)
        reason.loc[administrative] = "resolved_administrative_directory_identity_ineligible"
        reason.loc[geography] = "contradicted_identity_geography"
        reason.loc[ledger.record_disposition.eq("ambiguous_family_multiple_records")] = "multiple_fsa_records_for_same_unitid_year_family"
        ledger = ledger.assign(detailed_identity_reason=reason)
        group_keys = ["award_year", "record_disposition", "record_origin", "detailed_identity_reason"]
        for (year, disposition, origin, detail), group in ledger.groupby(group_keys, dropna=False):
            for column in measures:
                values = pd.to_numeric(group[column])
                rows.append({"family": family, "award_year": year, "record_disposition": disposition,
                    "record_origin": origin, "detailed_identity_reason": detail, "column": column, "units": "nominal_usd",
                    "source_record_count": len(group), "observed_amount_cells": int(values.notna().sum()),
                    "unknown_amount_cells": int(values.isna().sum()), "known_source_dollar_sum": values.sum(min_count=1)})
    quarantine = pd.read_csv(OUT / "source_family_year_disposition_dollars.csv")
    quarantine = quarantine.loc[quarantine.record_origin.eq("raw_source_quarantine")].assign(detailed_identity_reason="raw_source_identity_unresolved")
    result = pd.concat([pd.DataFrame(rows), quarantine], ignore_index=True)
    result.to_csv(OUT / "source_family_year_identity_reason_dollars.csv", index=False)


if __name__ == "__main__":
    verify()
    write_identity_reason_dollars()
