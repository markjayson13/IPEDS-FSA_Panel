from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from helpers import load_script_module

mod = load_script_module("fsa_ipeds_merge_diagnostics_test", "Scripts/fsa_ipeds_merge_diagnostics.py")


def fixture():
    ipeds = pd.DataFrame({
        "UNITID": range(101, 109), "year": 2005,
        "OPEID": pd.Series(["00100000", "00200000", "-2", None, "00500000", "00600000", "00700000", "00800000"], dtype="string"),
        "INSTNM": [f"Institution {i}" for i in range(8)],
    })
    fsa = pd.DataFrame({
        "unitid": [101, 102, 999, 998], "award_year_start": [2004, 2004, 2004, 2003],
        "award_year_end": [2005, 2005, 2005, 2004],
        "award_year": ["2004-2005", "2004-2005", "2004-2005", "2003-2004"],
        "included_source_family_count": [1, 0, 1, 1], "blocked_source_family_count": [0, 1, 0, 0],
        "grant__opeid8": ["00100000", None, "00999000", "00998000"],
        "grant__unitid_record_status": ["included_unique_family_record", "ambiguous_family_multiple_records",
                                         "included_unique_family_record", "included_unique_family_record"],
    })
    bridge = pd.DataFrame({
        "opeid8": ["00600000", "00700000", "00800000"], "award_year": "2004-2005",
        "unitid": pd.Series([pd.NA, 999, 108], dtype="Int64"),
        "ipeds_annual_identity_eligible": [False, True, True],
        "ipeds_resolution_status": ["conflict", "resolved", "resolved"],
        "ipeds_crosswalk_site_unitids_json": ['[106, 206]', '[999]', '[108]'],
    })
    return ipeds, fsa, bridge


class MergeDiagnosticTests(unittest.TestCase):
    def test_exact_evidence_does_not_reassign_or_zero_fill(self):
        ipeds, fsa, bridge = fixture()
        with tempfile.TemporaryDirectory() as d:
            result = mod.build_merge_diagnostics(ipeds, fsa, bridge, d, -1)
            self.assertEqual(result["matched_rows"], 2)
            self.assertEqual(result["matched_usable_family_rows"], 1)
            self.assertEqual(result["matched_blocked_only_rows"], 1)
            self.assertEqual(result["ipeds_only_rows"], 6)
            self.assertEqual(result["fsa_only_rows_within_ipeds_years"], 1)
            self.assertEqual(result["fsa_only_rows_outside_ipeds_years"], 1)
            evidence = pd.read_csv(result["files"]["ipeds_only_evidence"], dtype={"ipeds_opeid8": "string"}).set_index("UNITID")
            self.assertEqual(evidence.at[105, "ipeds_opeid8"], "00500000")
            self.assertEqual(evidence.at[103, "ipeds_opeid_raw"], -2)
            self.assertTrue(pd.isna(evidence.at[103, "ipeds_opeid8"]))
            self.assertEqual(evidence.at[105, "evidence_status"], "no_exact_opeid_award_year_in_fsa_master_bridge")
            self.assertEqual(evidence.at[106, "evidence_status"], "fsa_master_identity_unresolved_or_ineligible")
            self.assertEqual(evidence.at[107, "evidence_status"], "fsa_master_assigned_another_unitid")
            self.assertEqual(evidence.at[108, "evidence_status"], "fsa_master_same_unitid_without_unitid_panel_row")
            self.assertEqual(evidence.at[106, "bridge__ipeds_crosswalk_site_unitids_json"], "[106, 206]")
            self.assertTrue(evidence.fsa_has_usable_family.isna().all())
            self.assertEqual(len(ipeds), 8)
            self.assertNotIn("ipeds_opeid8", ipeds)

    def test_full_identifier_normalization_rejects_sentinels_without_root_inference(self):
        for value in (-2, "-2", None, pd.NA, "0", "00000000", "1e5", "123456789", "ABC12345", "123.5", True):
            self.assertIsNone(mod.normalize_full_opeid(value))
        self.assertEqual(mod.normalize_full_opeid("123456"), "00123456")
        self.assertEqual(mod.normalize_full_opeid(123400.0), "00123400")
        self.assertEqual(mod.normalize_full_opeid("01234001"), "01234001")

    def test_duplicate_keys_fail_before_output(self):
        ipeds, fsa, bridge = fixture()
        cases = ((pd.concat([ipeds, ipeds.iloc[:1]]), fsa, bridge, "IPEDS keys"),
                 (ipeds, pd.concat([fsa, fsa.iloc[:1]]), bridge, "FSA keys"),
                 (ipeds, fsa, pd.concat([bridge, bridge.iloc[:1]]), "FSA bridge keys"))
        with tempfile.TemporaryDirectory() as d:
            for i, (a, b, c, error) in enumerate(cases):
                out = Path(d) / str(i)
                with self.assertRaisesRegex(ValueError, error):
                    mod.build_merge_diagnostics(a, b, c, out, -1)
                self.assertFalse(out.exists())

    def test_exact_award_year_and_full_opeid_only(self):
        ipeds, fsa, bridge = fixture()
        bridge.loc[0, "award_year"] = "2003-2004"
        bridge.loc[1, "opeid8"] = "00700001"
        with tempfile.TemporaryDirectory() as d:
            result = mod.build_merge_diagnostics(ipeds, fsa, bridge, d, -1)
            evidence = pd.read_csv(result["files"]["ipeds_only_evidence"]).set_index("UNITID")
            for unitid in (106, 107):
                self.assertFalse(evidence.at[unitid, "exact_bridge_row_present"])

    def test_file_entrypoint_and_start_year_offset(self):
        ipeds, fsa, bridge = fixture()
        ipeds["year"] -= 1
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for name, frame in (("ipeds", ipeds), ("fsa", fsa), ("bridge", bridge)):
                frame.to_parquet(d / f"{name}.parquet", index=False)
            result = mod.create_diagnostics(d / "ipeds.parquet", d / "fsa.parquet", d / "bridge.parquet", d / "out", 0)
            self.assertEqual(result["matched_rows"], 2)
            counts = pd.read_csv(result["files"]["year_counts"])
            self.assertEqual(int(counts.ipeds_rows.sum()), len(ipeds))
            self.assertEqual(int(counts.fsa_rows.sum()), len(fsa))
            self.assertIn("sources", result)

    def test_false_string_is_not_eligible(self):
        ipeds, fsa, bridge = fixture()
        bridge["ipeds_annual_identity_eligible"] = [False, True, "False"]
        with tempfile.TemporaryDirectory() as d:
            result = mod.build_merge_diagnostics(ipeds, fsa, bridge, d, -1)
            evidence = pd.read_csv(result["files"]["ipeds_only_evidence"]).set_index("UNITID")
            self.assertEqual(evidence.at[108, "evidence_status"], "fsa_master_identity_unresolved_or_ineligible")


if __name__ == "__main__":
    unittest.main()
