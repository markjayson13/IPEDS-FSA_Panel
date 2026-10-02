from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from helpers import load_script_module

cw = load_script_module("fsa_official_crosswalk_tests", "Scripts/fsa_official_crosswalk.py")


def row(**updates):
    return {"OPEID": "00100201", "IPEDSMatch": "100654", "AddMatch": "-2", "Source": "2",
            "OPEIDMain": "00100200", "IPEDSMain": "100654", "IPEDSrpt": "1", "COA1": "-2", **updates}


class OfficialCrosswalkTests(unittest.TestCase):
    def test_primary_additional_and_parent_roles_remain_distinct(self):
        frame = pd.DataFrame([row(AddMatch="100663 | 100690", IPEDSMain="100706")])
        relations, records = cw.parse_crosswalk_frame(frame, 2020)
        self.assertEqual(relations.unitid.tolist(), [100654, 100663, 100690, 100706])
        self.assertEqual(relations.relation_type.tolist(), ["primary_match", "additional_match", "additional_match", "parent_reference"])
        self.assertEqual(records.loc[0, "cw_site_candidate_count"], 3)
        self.assertTrue(records.loc[0, "cw_multiple_site_unitids"])
        # Even IPEDSrpt=1 does not erase additional current-year matches.
        self.assertEqual(records.loc[0, "cw_ipeds_reporting_code"], "1")
        self.assertEqual(records.loc[0, "cw_raw_AddMatch"], "100663 | 100690")

    def test_no_site_match_does_not_fall_back_to_parent(self):
        relations, records = cw.parse_crosswalk_frame(pd.DataFrame([row(IPEDSMatch="No match", Source="5")]), 2021)
        self.assertTrue(records.loc[0, "cw_official_no_match"])
        self.assertEqual(records.loc[0, "cw_site_candidate_count"], 0)
        self.assertEqual(set(relations.relation_type), {"parent_reference", "no_site_match"})
        self.assertTrue(relations.loc[relations.relation_type.eq("no_site_match"), "unitid"].isna().all())

    def test_invalid_or_freeform_matches_fail_closed(self):
        for value in ["0", "-1", "100654 and 100663", "100654 | reason", "100654,100663", "100654.5"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                cw.parse_unitid_tokens(value, allow_multiple=True)

    def test_history_raw_evidence_and_duplicate_source_rows_retained(self):
        frame = pd.DataFrame([row(COA1="00100201|00100301|2|20200701", Source="3"), row(IPEDSMatch="100663")])
        relations, records = cw.parse_crosswalk_frame(frame, 2020)
        self.assertTrue(records.cw_duplicate_opeid_rows.all())
        self.assertTrue(records.loc[0, "cw_has_change_notes"])
        self.assertEqual(records.loc[0, "cw_source_method"], "relationship_established_from_other_years")
        self.assertEqual(records.loc[0, "cw_raw_COA1"], "00100201|00100301|2|20200701")
        self.assertEqual(len(records), 2)

    def test_truncated_official_addmatch_retains_evidence_without_false_complete_union(self):
        relations, records = cw.parse_crosswalk_frame(pd.DataFrame([row(AddMatch="100663 | 100")]), 2010)
        self.assertEqual(records.loc[0, "cw_site_candidate_count"], 2)
        self.assertFalse(records.loc[0, "cw_relation_parse_complete"])
        self.assertFalse(records.loc[0, "cw_site_parse_complete"])
        self.assertIn('"100"', records.loc[0, "cw_parse_issues_json"])
        self.assertEqual(records.loc[0, "cw_raw_AddMatch"], "100663 | 100")
        self.assertNotIn(100, relations.unitid.dropna().tolist())

    def test_loader_year_release_labels_and_missing_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, unitid in [("CW2024_prelim.xlsx", "100654"), ("CW2024.xlsx", "100663")]:
                with pd.ExcelWriter(root / name) as writer:
                    pd.DataFrame([row(IPEDSMatch=unitid)]).to_excel(writer, sheet_name="Crosswalk", index=False)
                    pd.DataFrame([["Source", "1", "header"]]).to_excel(writer, sheet_name="Value Labels & Notes", index=False)
            relations, records, manifest = cw.load_official_crosswalks(root, [2023, 2024])
            self.assertEqual(records.loc[0, "cw_primary_unitid"], 100663)
            self.assertFalse(records.loc[0, "cw_preliminary"])
            self.assertEqual(manifest.loc[manifest.cw_year.eq(2023), "status"].tolist(), ["missing"])
            self.assertIn("Value Labels & Notes", manifest.loc[manifest.cw_year.eq(2024), "label_sheets_json"].iloc[0])


if __name__ == "__main__":
    unittest.main()
