from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from helpers import load_script_module

mod = load_script_module("fsa_ipeds_pell_validation_test", "Scripts/fsa_ipeds_pell_validation.py")


def inputs():
    ipeds = pd.DataFrame({
        "UNITID": [101, 102, 103, 104, 105], "year": 2009,
        "OPEID": [100000, 200000, 300000, 400000, 500000],
        "UPGRNTN": [100, 200, 0, -1, 5], "UPGRNTT": [1000, 2000, 0, -2, 50],
        "UPGRNTA": [10, 10, None, -2, 10], "PGRNT_T": 999999,
        "PRCH_SFA": "-2", "IDX_SFA": -2, "RPTMTH": ["1", "2", "3", "1", "2"], "IMP_SFA": "-2",
    })
    fsa_rows = []
    for uid in ipeds.UNITID:
        for year in (2007, 2008, 2009, 2010):
            if uid == 102 and year == 2007:
                continue
            value = ({2007: 120, 2008: 100, 2009: 80, 2010: 60}[year] if uid == 101
                     else {102: 200, 103: 0, 104: 20, 105: 5}[uid])
            fsa_rows.append({
                "unitid": uid, "award_year_start": year,
                "grant__pell_recipients": value, "grant__pell_disbursements": value * 10,
                "grant__pell_recipients__status": "suppressed_lt10" if uid == 105 else "observed_zero" if value == 0 else "observed",
                "grant__pell_disbursements__status": "observed_zero" if value == 0 else "observed",
                "grant__unitid_record_status": "included_unique_family_record",
                "grant__opeid8": f"00{uid-100}00000",
                "grant__ipeds_student_aid_scope": "no_parent_child_relation_reported",
            })
    dictionary = pd.DataFrame([
        {"year": 2009, "varname": "UPGRNTN", "varTitle": "Number of undergraduate students awarded Pell grants",
         "longDescription": "Number of undergraduate students who were awarded Pell grants", "source_file_label": "SFA0809_P1"},
        {"year": 2009, "varname": "UPGRNTT", "varTitle": "Total amount of Pell grant aid awarded to undergraduate students",
         "longDescription": "Total amount awarded to undergraduate students", "source_file_label": "SFA0809_P1"},
        {"year": 2009, "varname": "PGRNT_T", "varTitle": "Total Pell awarded to full-time first-time undergraduates",
         "longDescription": "Full-time, first-time cohort only", "source_file_label": "SFA0809_P1"},
    ])
    return ipeds, pd.DataFrame(fsa_rows), dictionary, pd.DataFrame(columns=["opeid8", "award_year"])


class PellValidationTests(unittest.TestCase):
    def run_build(self, ipeds, fsa, dictionary, bridge, d):
        result = mod.build_pell_validation(ipeds, fsa, dictionary, bridge, d)
        rows = pd.read_csv(result["files"]["pell_row_comparisons_all_offsets"])
        summary = pd.read_csv(result["files"]["pell_summary"])
        return result, rows, summary

    def test_common_samples_do_not_reward_missing_matches(self):
        args = inputs()
        with tempfile.TemporaryDirectory() as d:
            result, rows, summary = self.run_build(*args, d)
            common = summary[(summary.measure == "recipients") & (summary["sample"] == "common_all_four_offsets")
                             & (summary.ipeds_year == "all") & (summary.reporting_population == "all")]
            self.assertEqual(common.comparable_rows.tolist(), [2, 2, 2, 2])
            prior = common[common.offset == -1].iloc[0]
            self.assertEqual(prior.ipeds_total_comparable_cells, 100)
            self.assertEqual(prior.fsa_total_comparable_cells, 100)
            self.assertEqual(prior.weighted_absolute_relative_difference, 0)
            same = common[common.offset == 0].iloc[0]
            self.assertEqual(same.weighted_absolute_relative_difference, .2)
            self.assertEqual(result["common_sample_rows"]["recipients"], 2)
            self.assertEqual(set(rows.UNITID), set(args[0].UNITID))
            self.assertNotIn("preferred_unitid", rows)
            self.assertNotIn("selected_offset", result)

    def test_zero_denominators_suppression_and_negative_sentinels(self):
        ipeds, fsa, dictionary, bridge = inputs()
        fsa.loc[fsa.unitid.eq(103) & fsa.award_year_start.eq(2010), "grant__pell_recipients"] = 1
        fsa.loc[fsa.unitid.eq(103) & fsa.award_year_start.eq(2010), "grant__pell_recipients__status"] = "observed"
        with tempfile.TemporaryDirectory() as d:
            _, rows, _ = self.run_build(ipeds, fsa, dictionary, bridge, d)
            zero = rows[(rows.UNITID == 103) & (rows.offset == -1)].iloc[0]
            self.assertTrue(zero.recipients_valid_pair)
            self.assertTrue(zero.recipients_exact_equal)
            self.assertTrue(pd.isna(zero.recipients_relative_difference))
            zero_positive = rows[(rows.UNITID == 103) & (rows.offset == 1)].iloc[0]
            self.assertTrue(zero_positive.zero_ipeds_positive_fsa_review_flag)
            self.assertTrue(zero_positive.large_difference_review_flag)
            self.assertTrue(pd.isna(zero_positive.recipients_relative_difference))
            negative = rows[rows.UNITID == 104]
            suppressed = rows[rows.UNITID == 105]
            self.assertFalse(negative.recipients_valid_pair.any())
            self.assertFalse(negative.dollars_valid_pair.any())
            self.assertFalse(suppressed.recipients_valid_pair.any())
            self.assertTrue(suppressed.recipients_difference.isna().all())

    def test_absent_reported_total_never_uses_average_or_ftft_total(self):
        ipeds, fsa, dictionary, bridge = inputs()
        ipeds = ipeds.drop(columns="UPGRNTT")
        with tempfile.TemporaryDirectory() as d:
            result, rows, _ = self.run_build(ipeds, fsa, dictionary, bridge, d)
            self.assertFalse(rows.dollars_valid_pair.any())
            self.assertTrue(rows.dollars_ipeds_value.isna().all())
            self.assertEqual(result["all_undergraduate_schema_years"]["UPGRNTT"], [])
            evidence = pd.read_csv(result["files"]["pell_annual_dictionary_evidence"])
            self.assertEqual(evidence.loc[evidence.varname.eq("PGRNT_T"), "comparison_role"].iloc[0], "excluded_ftft_population")

    def test_wrong_population_dictionary_fails(self):
        ipeds, fsa, dictionary, bridge = inputs()
        dictionary.loc[dictionary.varname.eq("UPGRNTN"), "varTitle"] = "Number full-time first-time undergraduates receiving Pell"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "all-undergraduate"):
                self.run_build(ipeds, fsa, dictionary, bridge, d)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_parent_child_and_imputation_are_sensitivity_screens(self):
        ipeds, fsa, dictionary, bridge = inputs()
        ipeds.loc[ipeds.UNITID.eq(101), "PRCH_SFA"] = "1"
        ipeds.loc[ipeds.UNITID.eq(102), "IMP_SFA"] = "1"
        dictionary["variable_id"] = "nces:" + dictionary.varname
        dictionary["source_file"] = "SFA_P"
        dictionary["access_table_name"] = "SFA0809_P1"
        lineage = dictionary.rename(columns={"varname": "analysis_column"})[
            ["year", "analysis_column", "variable_id", "source_file", "access_table_name"]].copy()
        with tempfile.TemporaryDirectory() as d:
            result = mod.build_pell_validation(ipeds, fsa, dictionary, bridge, d, lineage=lineage)
            rows = pd.read_csv(result["files"]["pell_row_comparisons_all_offsets"])
            selected = rows[rows.UNITID.isin([101, 102]) & rows.offset.eq(-1)]
            self.assertTrue(selected.recipients_valid_pair.all())
            self.assertTrue(selected.recipients_source_metadata_verified.all())
            self.assertFalse(selected.recipients_scope_screen_valid_pair.any())
            self.assertTrue(rows.loc[rows.UNITID.eq(101), "identity_or_scope_review_flag"].all())
            self.assertTrue(rows.loc[rows.UNITID.eq(103), "recipients_scope_screen_valid_pair"].all())

    def test_bridge_candidates_are_evidence_without_volume_reassignment(self):
        ipeds, fsa, dictionary, _ = inputs()
        bridge = pd.DataFrame({"opeid8": ["00100000"], "award_year": ["2008-2009"],
                               "unitid": [101], "ipeds_crosswalk_site_unitids_json": ["[101,999]"]})
        with tempfile.TemporaryDirectory() as d:
            _, rows, _ = self.run_build(ipeds, fsa, dictionary, bridge, d)
            row = rows[(rows.UNITID == 101) & (rows.offset == -1)].iloc[0]
            self.assertEqual(row.bridge__ipeds_crosswalk_site_unitids_json, "[101,999]")
            self.assertEqual(row.recipients_fsa_value, 100)
            self.assertNotIn(999, set(rows.UNITID))
            self.assertTrue(row.recipients_documented_reference_year_agrees)

    def test_other_measure_catalog_records_scope_without_new_totals(self):
        _, _, dictionary, _ = inputs()
        dictionary = pd.concat([dictionary, pd.DataFrame([{
            "year": 2009, "varname": "UFLOANT", "varTitle": "Total federal student loans awarded to undergraduates",
            "longDescription": "Includes federal student loans; excludes parent PLUS", "source_file_label": "SFA0809_P1",
        }])], ignore_index=True)
        catalog, evidence = mod.measure_comparability_catalog(dictionary, {"UFLOANT", "UPGRNTN", "UPGRNTT"}, set())
        loans = catalog[catalog.comparison_id.eq("federal_student_loan_dollars")].iloc[0]
        self.assertFalse(loans.quantitative_benchmark_in_this_build)
        self.assertIn('"UFLOANT"', loans.ipeds_actual_columns_json)
        self.assertIn("No new sum", loans.exclusion_or_review_reason)
        self.assertIn("UFLOANT", set(evidence.varname))
        work = catalog[catalog.comparison_id.eq("federal_work_study")].iloc[0]
        self.assertIn("exclude Work Study dollars", work.program_and_population_assessment)

    def test_legacy_count_is_separate_and_does_not_change_primary_benchmark(self):
        ipeds, fsa, dictionary, bridge = inputs()
        legacy = ipeds.iloc[:1].copy()
        legacy["year"] = 2008
        legacy["TSTDPEL"] = 120
        ipeds = pd.concat([ipeds, legacy], ignore_index=True)
        dictionary = pd.concat([dictionary, pd.DataFrame([{
            "year": 2008, "varname": "TSTDPEL", "varTitle": "Number of undergraduate students who received Pell grants",
            "longDescription": "Number of undergraduate students who received Pell grants", "source_file_label": "SFA0708",
        }])], ignore_index=True)
        with tempfile.TemporaryDirectory() as d:
            result, rows, _ = self.run_build(ipeds, fsa, dictionary, bridge, d)
            legacy_rows = pd.read_csv(result["files"]["pell_legacy_2008_count_only"])
            prior = legacy_rows[legacy_rows.offset.eq(-1)].iloc[0]
            self.assertEqual(prior.source_column, "TSTDPEL")
            self.assertTrue(prior.recipients_exact_equal)
            self.assertEqual(prior.documented_reference_award_year_start, 2007)
            self.assertNotIn("grant__pell_disbursements", legacy_rows)
            self.assertNotIn("UPGRNTT", legacy_rows)
            self.assertEqual(result["common_sample_rows"]["recipients"], 2)
            self.assertFalse(rows.loc[rows.year.eq(2008), "recipients_valid_pair"].any())
            self.assertFalse(result["supplemental_2008_count_only"]["dollar_comparison"])

    def test_source_table_conflict_preserves_values_but_blocks_verified_sensitivity(self):
        ipeds, fsa, dictionary, bridge = inputs()
        dictionary["variable_id"] = "nces:" + dictionary.varname
        dictionary["source_file"] = "SFA_P"
        dictionary["access_table_name"] = "SFA0809_P1"
        lineage = dictionary.rename(columns={"varname": "analysis_column"})[
            ["year", "analysis_column", "variable_id", "source_file", "access_table_name"]].copy()
        # The source family/ID agree but the table identity conflicts for counts.
        lineage.loc[lineage.analysis_column.eq("UPGRNTN"), "access_table_name"] = "sfa0809_p2"
        with tempfile.TemporaryDirectory() as d:
            result = mod.build_pell_validation(ipeds, fsa, dictionary, bridge, d, lineage=lineage)
            rows = pd.read_csv(result["files"]["pell_row_comparisons_all_offsets"])
            row = rows[rows.UNITID.eq(101) & rows.offset.eq(-1)].iloc[0]
            self.assertTrue(row.recipients_valid_pair)
            self.assertEqual(row.recipients_difference, 0)
            self.assertEqual(row.recipients_source_metadata_status, "source_table_conflict")
            self.assertFalse(row.recipients_scope_screen_valid_pair)
            self.assertTrue(row.dollars_source_metadata_verified)
            self.assertTrue(row.dollars_scope_screen_valid_pair)
            self.assertEqual(result["common_sample_rows"]["recipients"], 2)
            self.assertEqual(result["metadata_verified_schema_years"]["UPGRNTN"], [])

    def test_missing_lineage_is_unknown_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            result, rows, _ = self.run_build(*inputs(), d)
            self.assertFalse(result["source_lineage_available"])
            self.assertFalse(rows.recipients_source_metadata_verified.any())
            self.assertFalse(rows.recipients_scope_screen_valid_pair.any())
            self.assertTrue(rows.recipients_valid_pair.any())

    def test_frozen_lineage_sibling_is_discovered_and_case_insensitive(self):
        ipeds, fsa, dictionary, bridge = inputs()
        dictionary["variable_id"] = "nces:" + dictionary.varname
        dictionary["source_file"] = "SFA_P"
        dictionary["access_table_name"] = "SFA0809_P1"
        lineage = dictionary.rename(columns={"varname": "analysis_column"})[
            ["year", "analysis_column", "variable_id", "source_file", "access_table_name"]].copy()
        lineage["access_table_name"] = "sfa0809_p1"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for name, frame in (("ipeds", ipeds), ("fsa", fsa), ("dictionary", dictionary), ("bridge", bridge), ("column_lineage", lineage)):
                frame.to_parquet(d / f"{name}.parquet", index=False)
            result = mod.create_pell_validation(d / "ipeds.parquet", d / "fsa.parquet", d / "dictionary.parquet", d / "bridge.parquet", d / "out")
            self.assertTrue(result["source_lineage_available"])
            self.assertEqual(result["metadata_verified_schema_years"]["UPGRNTN"], [2009])
            self.assertEqual(result["source_lineage_path"], str(d / "column_lineage.parquet"))


if __name__ == "__main__":
    unittest.main()
