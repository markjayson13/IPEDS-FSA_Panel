from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from helpers import load_script_module


linkage = load_script_module("fsa_ipeds_linkage_test", "Scripts/fsa_ipeds_linkage.py")


def hd_record(year: int, unitid: int, opeid: str, **kwargs) -> dict:
    return {"ipeds_year": year, "unitid": unitid, "opeid8": opeid,
            "instnm": f"Institution {unitid}", "sector": "1", "cyactive": "1",
            "opeflag": "1", "act": "A", "newid": "-2", "deathyr": "-2",
            "closedat": "-2", "ipeds_flags_available": True, "prch_f": "-2",
            "idx_f": "-2", "prch_sfa": "-2", "idx_sfa": "-2", **kwargs}


def fsa(*ids: str) -> pd.DataFrame:
    return pd.DataFrame({"opeid8": list(ids), "award_year": ["2010-2011"] * len(ids),
                         "disbursements": pd.array([123.45] * len(ids), dtype="Float64")})


class IPEDSLinkageTests(unittest.TestCase):
    def test_numeric_full_ids_are_not_roots(self) -> None:
        for value in (100200, 100200.0, "100200", "00100200", "100200.00"):
            self.assertEqual(linkage.full_opeid(value), "00100200")
        self.assertEqual(linkage.full_opeid("105901"), "00105901")
        for value in ("bad100200", "1002x00", -2, "00000000", "100200.1", "100200000", None):
            self.assertIsNone(linkage.full_opeid(value))

    def test_unique_stable_singleton_is_strict_and_amounts_preserved(self) -> None:
        panel = fsa("00100200").astype({"opeid8": "string", "award_year": "string"})
        hd = pd.DataFrame([hd_record(y, 100654, "00100200") for y in [2010, 2011]])
        bridge, candidates, linked, strict = linkage.build_ipeds_linkage(panel, hd)
        self.assertEqual(bridge.loc[0, "unitid"], 100654)
        self.assertTrue(bridge.loc[0, "ipeds_strict_eligible"])
        pd.testing.assert_frame_equal(linked[panel.columns], panel)
        self.assertEqual(len(strict), 1)
        self.assertEqual(len(candidates), 2)

    def test_same_full_opeid_multiple_unitids_never_duplicates_aid(self) -> None:
        panel = fsa("00109000")
        hd = pd.DataFrame([hd_record(y, unit, "00109000", sector=str(sector))
                           for y in [2010, 2011] for unit, sector in [(106458, 1), (448336, 0)]])
        bridge, candidates, linked, strict = linkage.build_ipeds_linkage(panel, hd)
        self.assertEqual(bridge.loc[0, "ipeds_match_status"], "multiple_exact_unitids")
        self.assertTrue(pd.isna(linked.loc[0, "unitid"]))
        self.assertEqual(len(linked), 1)
        self.assertEqual(len(candidates), 4)
        self.assertEqual(len(strict), 0)
        self.assertEqual(linked["disbursements"].sum(), panel["disbursements"].sum())

    def test_parent_and_branch_unique_exact_are_flagged_for_scope(self) -> None:
        panel = fsa("00100000")
        hd = pd.DataFrame([hd_record(y, unit, opeid, opeflag=flag) for y in [2010, 2011]
                           for unit, opeid, flag in [(1, "00100000", "1"), (2, "00100001", "2")]])
        bridge, candidates, linked, strict = linkage.build_ipeds_linkage(panel, hd)
        self.assertEqual(bridge.loc[0, "unitid"], 1)
        self.assertEqual(bridge.loc[0, "ipeds_root_unitid_count_start"], 2)
        self.assertIn("multiple_ipeds_units", bridge.loc[0, "ipeds_strict_exclusion_reasons"])
        self.assertEqual(len(strict), 0)
        self.assertEqual((candidates["candidate_relation"] == "scope_group_only_no_assignment").sum(), 2)

    def test_root_only_and_location_overflow_never_assign(self) -> None:
        panel = fsa("00100000", "10100001")
        hd = pd.DataFrame([hd_record(y, 1, "00100001") for y in [2010, 2011]])
        bridge, candidates, linked, strict = linkage.build_ipeds_linkage(panel, hd)
        self.assertTrue(bridge["unitid"].isna().all())
        self.assertEqual(bridge["fsa_root_opeid_count"].tolist(), [2, 2])
        self.assertEqual(len(candidates), 4)
        self.assertEqual(len(strict), 0)

    def test_anchor_changes_id_history_and_no_temporal_fill(self) -> None:
        hd = pd.DataFrame([hd_record(2010, 1, "00100000"), hd_record(2011, 2, "00100000"),
                           hd_record(2012, 2, "00200000")])
        bridge, _, _, strict = linkage.build_ipeds_linkage(fsa("00100000", "00200000"), hd)
        self.assertEqual(bridge.loc[0, "ipeds_anchor_sensitivity"], "different_unitid")
        self.assertIn("opeid_maps_to_multiple_unitids_over_time", bridge.loc[0, "ipeds_strict_exclusion_reasons"])
        self.assertTrue(pd.isna(bridge.loc[1, "unitid"]))
        self.assertEqual(len(strict), 0)
        end, _, _, _ = linkage.build_ipeds_linkage(fsa("00100000"), hd, anchor="end")
        self.assertEqual(end.loc[0, "unitid"], 2)
        self.assertIn("unitid_has_multiple_opeids_over_time", end.loc[0, "ipeds_strict_exclusion_reasons"])

    def test_missing_year_is_distinct_from_unmatched(self) -> None:
        hd = pd.DataFrame([hd_record(2010, 1, "00100000")])
        bridge, _, _, strict = linkage.build_ipeds_linkage(fsa("00100000"), hd)
        self.assertEqual(bridge.loc[0, "ipeds_anchor_sensitivity"], "year_unavailable")
        self.assertEqual(len(strict), 0)
        end, _, _, _ = linkage.build_ipeds_linkage(fsa("00100000"), hd, anchor="end")
        self.assertEqual(end.loc[0, "ipeds_match_status"], "ipeds_year_unavailable")

    def test_unresolved_fsa_descriptors_excluded_from_strict(self) -> None:
        hd = pd.DataFrame([hd_record(y, 1, "00100000") for y in [2010, 2011]])
        for value, eligible in [(True, False), (False, True), ("False", True), (pd.NA, False)]:
            panel = fsa("00100000").assign(descriptor_review_required=value)
            bridge, _, linked, strict = linkage.build_ipeds_linkage(panel, hd)
            self.assertEqual(bridge.loc[0, "ipeds_strict_eligible"], eligible)
            pd.testing.assert_frame_equal(linked[panel.columns], panel)

    def test_component_parent_child_and_closure_block_strict(self) -> None:
        for metadata, expected in [({"prch_f": "1"}, "ipeds_component_parent_child"),
                                   ({"idx_f": "999999"}, "ipeds_component_parent_child"),
                                   ({"newid": "123456"}, "ipeds_merger_or_closure"),
                                   ({"ipeds_flags_available": False}, "parent_child_metadata_unavailable")]:
            with self.subTest(metadata=metadata):
                hd = pd.DataFrame([hd_record(y, 1, "00100000", **metadata) for y in [2010, 2011]])
                bridge, _, _, strict = linkage.build_ipeds_linkage(fsa("00100000"), hd)
                self.assertIn(expected, bridge.loc[0, "ipeds_strict_exclusion_reasons"])
                self.assertEqual(len(strict), 0)

    def test_administrative_and_inactive_units_are_candidates_only(self) -> None:
        for metadata in ({"sector": "0"}, {"cyactive": "3"}, {"act": "G", "cyactive": "3"}):
            hd = pd.DataFrame([hd_record(y, 1, "00100000", **metadata) for y in [2010, 2011]])
            bridge, candidates, _, strict = linkage.build_ipeds_linkage(fsa("00100000"), hd)
            self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
            self.assertEqual(len(candidates), 2)
            self.assertEqual(len(strict), 0)

    def test_fail_closed_on_bad_or_duplicate_input_keys(self) -> None:
        hd = pd.DataFrame([hd_record(y, 1, "00100000") for y in [2010, 2011]])
        for panel in [fsa("00100000", "00100000"), fsa("100000"), fsa("00000000"),
                      fsa("00100000").assign(award_year="2010-2012")]:
            with self.assertRaises(ValueError):
                linkage.build_ipeds_linkage(panel, hd)
        with self.assertRaises(ValueError):
            linkage.build_ipeds_linkage(fsa("00100000"), pd.concat([hd, hd]))
        mixed_years = fsa("00100000", "00100000").assign(award_year=["2010", "2010-2011"])
        with self.assertRaises(ValueError):
            linkage.build_ipeds_linkage(mixed_years, hd)
        for invalid_unitid in (0, -2, None, 1.5):
            with self.subTest(invalid_unitid=invalid_unitid), self.assertRaises(ValueError):
                linkage.build_ipeds_linkage(fsa("00100000"), hd.assign(unitid=invalid_unitid))

    def test_nonnumeric_scope_tokens_are_not_available_metadata(self) -> None:
        hd = pd.DataFrame([hd_record(y, 1, "00100000", idx_f=".") for y in [2010, 2011]])
        bridge, _, _, strict = linkage.build_ipeds_linkage(fsa("00100000"), hd)
        self.assertIn("parent_child_metadata_unavailable", bridge.loc[0, "ipeds_strict_exclusion_reasons"])
        self.assertEqual(len(strict), 0)

    def test_loader_uses_revised_member_and_flags_keeps_invalid_id_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / "HD2010.zip", "w") as archive:
                archive.writestr("hd2010.csv", "UNITID,OPEID,INSTNM\n1,100000,Old name\n")
                archive.writestr("hd2010_rv.csv", "UNITID,OPEID,INSTNM,SECTOR,CYACTIVE\n1,100000,Revised name,1,1\n2,-2,No OPEID,1,1\n")
            (root / "FLAGS2010.csv").write_text("UNITID,PRCH_F,IDX_F,PRCH_SFA,IDX_SFA\n1,1,-2,-2,-2\n2,-2,-2,-2,-2\n")
            hd, manifest, rejects = linkage.load_ipeds_directory(root, [2010, 2011])
            self.assertEqual(hd.loc[0, "opeid8"], "00100000")
            self.assertEqual(hd.loc[0, "instnm"], "Revised name")
            self.assertEqual(hd.loc[0, "prch_f"], "1")
            self.assertTrue(hd.loc[0, "ipeds_flags_available"])
            self.assertEqual(len(rejects), 1)
            self.assertEqual(manifest.loc[manifest["ipeds_year"].eq(2011), "status"].tolist(), ["missing", "missing"])

    def test_partial_flags_release_does_not_certify_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "HD2024.csv").write_text("UNITID,OPEID,INSTNM,SECTOR,CYACTIVE\n1,100000,A,1,1\n")
            (root / "FLAGS2024.csv").write_text("UNITID,PRCH_C,IDX_C\n1,-2,-2\n")
            hd, manifest, _ = linkage.load_ipeds_directory(root, [2024])
            self.assertFalse(hd.loc[0, "ipeds_flags_available"])
            self.assertFalse(manifest.loc[manifest.kind.eq("flags"), "scope_fields_complete"].iloc[0])


if __name__ == "__main__":
    unittest.main()
