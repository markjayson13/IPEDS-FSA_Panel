from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from helpers import load_script_module

mod = load_script_module("fsa_unitid_panels_test", "Scripts/fsa_unitid_panels.py")


def master_rows(opeids, *, grant=True):
    frame = pd.DataFrame({"opeid8": opeids, "award_year": "2009-2010", "award_year_start": 2009})
    if grant:
        frame["grant__source_record_present"] = True
        frame["grant__source_filename"] = "grants.xls"
        frame["grant__source_excel_row"] = range(10, 10 + len(frame))
        frame["grant__pell_disbursements"] = pd.Series([100.25 + i for i in range(len(frame))], dtype="Float64")
        frame["grant__pell_disbursements__status"] = "observed"
        frame["grant__pell_recipients"] = pd.Series([10 + i for i in range(len(frame))], dtype="Int64")
        frame["grant__pell_recipients__status"] = "observed"
        frame["grant__pell_recipients__lower_bound"] = frame["grant__pell_recipients"]
        frame["grant__pell_recipients__upper_bound"] = frame["grant__pell_recipients"]
        frame["grant__pell_recipients__raw_token"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    return frame


def bridge_rows(master, units, eligible=True):
    result = master[["opeid8", "award_year"]].copy()
    result["unitid"] = pd.Series(units, dtype="Int64")
    result["ipeds_annual_identity_eligible"] = eligible
    result["ipeds_resolution_status"] = "resolved_unique_annual"
    result["ipeds_scope_alignment"] = "scope_unverified"
    result["ipeds_component_sfa_flag"] = "parent"
    return result


class UnitidPanelTests(unittest.TestCase):
    def run_build(self, master, bridge, root, **kwargs):
        temporary = tempfile.TemporaryDirectory(prefix="downstream-unitid-")
        self.addCleanup(temporary.cleanup)
        manifest = mod.build_unitid_panel(master, bridge, root, Path(temporary.name) / "out", **kwargs)
        panel = pd.read_parquet(manifest["files"]["panel"])
        return manifest, panel

    def test_different_source_family_opeids_merge_without_a_canonical_opeid(self):
        m = master_rows(["00100000", "00100001"])
        m.loc[1, "grant__source_record_present"] = False
        m["campus__source_record_present"] = [False, True]
        m["campus__source_filename"] = "campus.xls"
        m["campus__source_excel_row"] = [pd.NA, 20]
        m["campus__fws_disbursements"] = pd.Series([pd.NA, 32.19], dtype="Float64")
        m["campus__fws_disbursements__status"] = ["absent_source_record", "observed"]
        m["descriptor_review_required"] = True
        with tempfile.TemporaryDirectory() as d:
            manifest, p = self.run_build(m, bridge_rows(m, [123456, 123456]), Path(d))
            self.assertEqual(len(p), 1)
            self.assertNotIn("opeid8", p)
            self.assertEqual(p.loc[0, "grant__opeid8"], "00100000")
            self.assertEqual(p.loc[0, "campus__opeid8"], "00100001")
            self.assertEqual(p.loc[0, "grant__pell_disbursements"], 100.25)
            self.assertEqual(p.loc[0, "campus__fws_disbursements"], 32.19)
            self.assertTrue(p.loc[0, "grant__fsa_descriptor_review_required"])
            self.assertIn("FISAP", p.loc[0, "campus__unitid_aid_scope_note"])
            self.assertTrue(manifest["all_conservation_passed"])
            dictionary = pd.read_csv(manifest["files"]["dictionary"])
            self.assertEqual(set(dictionary.panel_column), set(p))

    def test_multiple_family_records_block_amounts_and_keep_full_excluded_values(self):
        m = master_rows(["00100000", "00100001"])
        with tempfile.TemporaryDirectory() as d:
            manifest, p = self.run_build(m, bridge_rows(m, [123456, 123456]), Path(d))
            self.assertEqual(len(p), 1)
            self.assertTrue(pd.isna(p.loc[0, "grant__pell_disbursements"]))
            self.assertEqual(p.loc[0, "grant__pell_disbursements__status"], mod.MULTIPLE)
            self.assertEqual(p.loc[0, "grant__unitid_source_record_count"], 2)
            self.assertFalse(p.loc[0, "grant__source_record_present"])
            ledger = pd.read_parquet(manifest["files"]["grants_source_record_ledger"])
            self.assertEqual(ledger["grant__pell_disbursements"].sum(), 201.5)
            self.assertEqual(set(ledger.record_disposition), {mod.MULTIPLE})

    def test_false_string_is_ineligible_and_missing_explicit_flag_fails(self):
        m = master_rows(["00100000"])
        b = bridge_rows(m, [123456], eligible="False")
        with tempfile.TemporaryDirectory() as d:
            manifest, p = self.run_build(m, b, Path(d))
            self.assertTrue(p.empty)
            self.assertEqual(manifest["source_families"]["grants"]["excluded_records"], 1)
            with self.assertRaisesRegex(ValueError, "explicit annual"):
                self.run_build(m, b.drop(columns="ipeds_annual_identity_eligible"), Path(d))

    def test_suppression_bounds_and_raw_token_are_conserved(self):
        m = master_rows(["00100000"])
        m.loc[0, "grant__pell_recipients"] = pd.NA
        m.loc[0, "grant__pell_recipients__status"] = "suppressed_lt10"
        m.loc[0, "grant__pell_recipients__lower_bound"] = 0
        m.loc[0, "grant__pell_recipients__upper_bound"] = 9
        m.loc[0, "grant__pell_recipients__raw_token"] = "<10"
        with tempfile.TemporaryDirectory() as d:
            _, p = self.run_build(m, bridge_rows(m, [123456]), Path(d))
            self.assertTrue(pd.isna(p.loc[0, "grant__pell_recipients"]))
            self.assertEqual(p.loc[0, "grant__pell_recipients__raw_token"], "<10")
            self.assertEqual(p.loc[0, "grant__pell_recipients__upper_bound"], 9)

    def test_loans_are_recomputed_after_channel_merge(self):
        m = master_rows(["00100000", "00100001"], grant=False)
        for prefix, i, amount in [("loan_direct__", 0, 100.25), ("loan_ffel__", 1, 45.19)]:
            m[prefix + "source_record_present"] = [i == 0, i == 1]
            m[prefix + "source_filename"] = prefix + ".xls"
            m[prefix + "source_excel_row"] = [10, 20]
            m[prefix + "subsidized_disbursements"] = pd.Series([amount if n == i else pd.NA for n in range(2)], dtype="Float64")
            m[prefix + "subsidized_disbursements__status"] = ["observed" if n == i else "absent_source_record" for n in range(2)]
        m["loan__subsidized_disbursements"] = 999999
        with tempfile.TemporaryDirectory() as d:
            _, p = self.run_build(m, bridge_rows(m, [123456, 123456]), Path(d))
            self.assertAlmostEqual(p.loc[0, "loan__subsidized_disbursements"], 145.44)
            self.assertEqual(p.loc[0, "loan__subsidized_disbursements__status"], "complete_component_sum")

    def orphan_fixture(self, root):
        raw = root / "source.xls"
        raw.write_bytes(b"immutable selected workbook fixture")
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        for directory in ["Metadata", "Checks/observation_qc", "Checks/download_qc"]:
            (root / directory).mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"family": "grants", "award_year": "2009-2010", "filename": "source.xls", "sha256": digest,
                       "local_path": str(raw)}]).to_csv(root / "Checks/download_qc/selected_panel_files.csv", index=False)
        pd.DataFrame([{"family": "grants", "award_year": "2009-2010", "column": "grant__pell_disbursements", "sheet": "Aid"},
                      {"family": "grants", "award_year": "2009-2010", "column": "grant__pell_recipients", "sheet": "Aid"}]).to_csv(root / "Checks/observation_qc/grants_schema_availability.csv", index=False)
        pd.DataFrame([{"family": "grants", "award_year": "2009-2010", "filename": "source.xls", "source_excel_row": 30,
                       "row_class": "quarantined_institution", "raw_opeid": "", "normalized_opeid8": None,
                       "school": "Campus A", "state": "LA", "zip_code": "713023137", "school_type": "Public",
                       "pell_disbursements": "25.18", "pell_recipients": "<10"}]).to_parquet(root / "Checks/observation_qc/grants_quarantine.parquet", index=False)
        evidence = [{"kind": "unassigned_annual_hd_candidate", "unitid": 654321, "ipeds_year": 2009, "sha256": "a" * 64},
                    {"kind": "official_fsa_nces_crosswalk_campus_identity_only", "unitid": 654321, "match_source": "1", "sha256": "b" * 64}]
        pd.DataFrame([{"resolution_id": "test_30", "family": "grants", "award_year": "2009-2010", "filename": "source.xls",
                       "source_excel_row": "30", "source_sha256": digest, "source_sheet": "Aid", "resolution_status": "unresolved",
                       "recovered_opeid8": "", "verified_unitid": "654321", "unitid_resolution_status": mod.ORPHAN_STATUS,
                       "expected_raw_opeid": "", "expected_school": "Campus A", "expected_state": "LA", "expected_zip_code": "713023137",
                       "unitid_evidence_json": json.dumps(evidence)}]).to_csv(root / "Metadata/source_identity_resolutions.csv", index=False)
        return raw

    def test_verified_quarantine_unitid_keeps_opeid_unknown_and_parses_values(self):
        m = master_rows(["00100000"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.orphan_fixture(root)
            membership = pd.DataFrame({"opeid8": ["00100000"], "award_year": ["2009-2010"], "unitid": [123456]})
            manifest, p = self.run_build(m, bridge_rows(m, [123456]), root, memberships=membership)
            orphan = p.set_index("unitid").loc[654321]
            self.assertTrue(pd.isna(orphan["grant__opeid8"]))
            self.assertEqual(orphan["grant__pell_disbursements"], 25.18)
            self.assertEqual(orphan["grant__pell_recipients__status"], "suppressed_lt10")
            self.assertEqual(orphan["grant__pell_recipients__upper_bound"], 9)
            self.assertEqual(orphan["grant__raw_zip_code"], "713023137")
            self.assertEqual(manifest["source_families"]["grants"]["included_records"], 2)

    def test_orphan_membership_collision_blocks_without_allocating_to_any_opeid(self):
        m = master_rows(["00100000"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.orphan_fixture(root)
            membership = pd.DataFrame({"opeid8": ["00100000"], "award_year": ["2009-2010"], "unitid": [654321], "relation_type": ["additional_match"]})
            _, p = self.run_build(m, bridge_rows(m, [pd.NA], eligible=False), root, memberships=membership)
            self.assertEqual(p.loc[0, "grant__unitid_record_status"], "excluded_official_membership_overlap")
            self.assertTrue(pd.isna(p.loc[0, "grant__pell_disbursements"]))
            self.assertEqual(p.loc[0, "included_source_family_count"], 0)

    def test_orphan_requires_runtime_membership_guard_and_immutable_source(self):
        m = master_rows(["00100000"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = self.orphan_fixture(root)
            _, p = self.run_build(m, bridge_rows(m, [123456]), root)
            self.assertEqual(p.set_index("unitid").loc[654321, "grant__unitid_record_status"], "excluded_missing_official_membership_guard")
            source.write_bytes(b"changed workbook")
            with self.assertRaisesRegex(ValueError, "hash changed"):
                self.run_build(m, bridge_rows(m, [123456]), root)

    def test_same_unitid_orphan_and_accepted_record_blocks_both(self):
        m = master_rows(["00100000"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.orphan_fixture(root)
            membership = pd.DataFrame({"opeid8": ["00100000"], "award_year": ["2009-2010"], "unitid": [654321]})
            manifest, p = self.run_build(m, bridge_rows(m, [654321]), root, memberships=membership)
            self.assertEqual(p.loc[0, "grant__unitid_record_status"], mod.MULTIPLE)
            self.assertTrue(pd.isna(p.loc[0, "grant__pell_disbursements"]))
            ledger = pd.read_parquet(manifest["files"]["grants_source_record_ledger"])
            self.assertEqual(set(ledger.record_disposition), {mod.MULTIPLE})
            self.assertIn("excluded_official_membership_overlap", set(ledger.identity_guard_status))

    def test_parent_reference_is_scope_sensitivity_not_direct_duplicate(self):
        m = master_rows(["00100000"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.orphan_fixture(root)
            membership = pd.DataFrame({"opeid8": ["00100000"], "award_year": ["2009-2010"], "unitid": [654321], "relation_type": ["parent_reference"]})
            _, p = self.run_build(m, bridge_rows(m, [123456]), root, memberships=membership)
            orphan = p.set_index("unitid").loc[654321]
            self.assertEqual(orphan["grant__unitid_record_status"], mod.INCLUDED)
            self.assertIn("parent_reference", orphan["grant__source_identity_parent_scope_sensitivity_json"])

    def test_blocked_direct_family_does_not_produce_complete_combined_loans(self):
        m = master_rows(["00100000", "00100001", "00100002"], grant=False)
        m["loan_direct__source_record_present"] = [True, True, False]
        m["loan_ffel__source_record_present"] = [False, False, True]
        for prefix, values in [("loan_direct__", [100.0, 200.0, None]), ("loan_ffel__", [None, None, 50.25])]:
            m[prefix + "source_filename"] = prefix + ".xls"
            m[prefix + "source_excel_row"] = [10, 20, 30]
            m[prefix + "subsidized_disbursements"] = pd.Series(values, dtype="Float64")
            m[prefix + "subsidized_disbursements__status"] = ["observed" if v is not None else "absent_source_record" for v in values]
        with tempfile.TemporaryDirectory() as d:
            _, p = self.run_build(m, bridge_rows(m, [123456, 123456, 123456]), Path(d))
            self.assertTrue(pd.isna(p.loc[0, "loan__subsidized_disbursements"]))
            self.assertEqual(p.loc[0, "loan__subsidized_disbursements__observed_partial_sum"], 50.25)
            self.assertEqual(p.loc[0, "loan_direct_harmonized__subsidized_disbursements__status"], mod.MULTIPLE)
            self.assertEqual(p.loc[0, "loan__subsidized_disbursements__status"], "incomplete_blocked_unitid_family")

    def test_absent_family_cells_distinguish_unavailable_report_and_historical_schema(self):
        m = master_rows(["00100000", "00100001"])
        m["award_year"] = ["2001-2002", "2014-2015"]
        m["award_year_start"] = [2001, 2014]
        m["grant__source_record_present"] = False
        m["grant__teach_disbursements"] = pd.Series([pd.NA, pd.NA], dtype="Float64")
        m["grant__teach_disbursements__status"] = ["unavailable_in_schema", "absent_source_record"]
        m["loan_ffel__source_record_present"] = False
        m["loan_ffel__subsidized_disbursements"] = pd.Series([pd.NA, pd.NA], dtype="Float64")
        m["loan_ffel__subsidized_disbursements__status"] = ["absent_source_record", "report_not_available"]
        m["campus__source_record_present"] = True
        m["campus__source_filename"] = "campus.xls"
        m["campus__source_excel_row"] = [10, 11]
        m["campus__fws_disbursements"] = 42.19
        m["campus__fws_disbursements__status"] = "observed"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Checks/download_qc").mkdir(parents=True)
            (root / "Checks/observation_qc").mkdir(parents=True)
            pd.DataFrame({"family": ["grants", "grants", "ffel", "campus_based", "campus_based"],
                          "award_year": ["2001-2002", "2014-2015", "2001-2002", "2001-2002", "2014-2015"]}).to_csv(root / "Checks/download_qc/selected_panel_files.csv", index=False)
            pd.DataFrame({"award_year": ["2001-2002", "2014-2015", "2014-2015"],
                          "column": ["grant__pell_disbursements", "grant__pell_disbursements", "grant__teach_disbursements"]}).to_csv(root / "Checks/observation_qc/grants_schema_availability.csv", index=False)
            pd.DataFrame({"award_year": ["2001-2002"], "column": ["loan_ffel__subsidized_disbursements"]}).to_csv(root / "Checks/observation_qc/ffel_schema_availability.csv", index=False)
            _, p = self.run_build(m, bridge_rows(m, [123456, 123456]), root)
            p = p.set_index("award_year")
            self.assertEqual(p.loc["2001-2002", "grant__teach_disbursements__status"], "unavailable_in_schema")
            self.assertEqual(p.loc["2014-2015", "loan_ffel__subsidized_disbursements__status"], "report_not_available")
            self.assertEqual(p.loc["2014-2015", "loan_ffel_harmonized__subsidized_disbursements__status"], "report_not_available")
            self.assertEqual(p.loc["2001-2002", "loan_ffel__subsidized_disbursements__status"], "absent_source_record")
            self.assertEqual(p.loc["2014-2015", "grant__teach_disbursements__status"], "absent_source_record")

    def test_bridge_dictionary_definitions_follow_family_prefixed_fields(self):
        m = master_rows(["00100000"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Panels/ipeds").mkdir(parents=True)
            pd.DataFrame({"field": ["ipeds_component_sfa_flag"], "description": ["Annual SFA collection parent/child status from official FLAGS."],
                          "interpretation": ["Not an aid allocation rule."]}).to_csv(root / "Panels/ipeds/ipeds_bridge_dictionary.csv", index=False)
            manifest, _ = self.run_build(m, bridge_rows(m, [123456]), root,
                                         bridge_dictionary_path=root / "Panels/ipeds/ipeds_bridge_dictionary.csv")
            dictionary = pd.read_csv(manifest["files"]["dictionary"]).set_index("panel_column")
            self.assertEqual(dictionary.loc["grant__ipeds_component_sfa_flag", "definition"], "Annual SFA collection parent/child status from official FLAGS.")
            self.assertEqual(dictionary.loc["grant__ipeds_component_sfa_flag", "interpretation"], "Not an aid allocation rule.")


if __name__ == "__main__":
    unittest.main()
