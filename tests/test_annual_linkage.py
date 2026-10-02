from __future__ import annotations

import json
import unittest
import pandas as pd

from helpers import load_script_module

annual = load_script_module("fsa_annual_linkage_test", "Scripts/fsa_annual_linkage.py")


def directory(uid=100001, opeid="00100000", **extra):
    record = {"ipeds_year": 2010, "unitid": uid, "opeid8": opeid, "sector": "1", "opeflag": "1", "cyactive": "1", "act": "A"}
    for component in annual.COMPONENTS.values():
        record.update({"prch_" + component: "-2", "idx_" + component: "-2"})
    return {**record, **extra}


def evidence(opeid="00100000", source="1", **extra):
    return {"cw_year": 2010, "opeid8": opeid, "cw_source_code": source, "cw_site_parse_complete": True, **extra}


def relation(uid, role="primary_match", opeid="00100000"):
    return {"cw_year": 2010, "opeid8": opeid, "unitid": uid, "relation_type": role}


def fixture(identities=("00100000",), states=None):
    panel = pd.DataFrame({"opeid8": pd.array(identities, dtype="string"), "award_year": pd.array(["2010-2011"] * len(identities), dtype="string"),
                          "amount": pd.array([123.45] * len(identities), dtype="Float64"), "state": states or ["AL"] * len(identities)})
    bridge = panel[["opeid8", "award_year"]].copy()
    bridge["ipeds_year"] = 2010
    bridge["unitid"] = pd.array([100001] * len(panel), dtype="Int64")
    bridge["ipeds_match_status"] = "exact_unique"
    bridge["ipeds_strict_eligible"] = False
    bridge["ipeds_strict_exclusion_reasons"] = "review"
    return panel, bridge


def run_case(hd, cw=(), relations=(), strict=False):
    panel, bridge = fixture()
    bridge["ipeds_strict_eligible"] = strict
    result = annual.enrich_annual_linkage(panel, bridge, pd.DataFrame(hd), pd.DataFrame(cw), pd.DataFrame(relations))
    pd.testing.assert_frame_equal(result[1][panel.columns], panel)
    return result


class AnnualIdentityTests(unittest.TestCase):
    def test_addmatch_prevents_false_singleton_even_if_reporting_code_one(self):
        bridge, linked, strict, membership = run_case([directory(), directory(100002, "00200000")],
            [evidence(cw_ipeds_reporting_code="1")], [relation(100001), relation(100002, "additional_match")], strict=True)
        self.assertEqual(bridge.loc[0, "ipeds_resolution_status"], "multiple_official_site_unitids")
        self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
        self.assertEqual(len(linked), 1)
        self.assertEqual(len(strict), 0)
        self.assertEqual(len(membership), 2)

    def test_primary_crosswalk_conflicting_with_exact_hd_is_not_chosen(self):
        bridge, _, _, _ = run_case([directory(), directory(100002, "00200000")], [evidence()], [relation(100002)])
        self.assertEqual(bridge.loc[0, "ipeds_resolution_status"], "directory_crosswalk_identity_conflict")
        self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
        self.assertEqual(bridge.loc[0, "ipeds_directory_unitid"], 100001)

    def test_parent_reference_never_fills_missing_site_match(self):
        bridge, _, _, members = run_case([directory(100001, "00200000")], [evidence(source="5")], [relation(100001, "parent_reference")])
        self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
        self.assertEqual(json.loads(bridge.loc[0, "ipeds_crosswalk_parent_unitids_json"]), [100001])
        self.assertEqual(len(members), 0)

    def test_retrospective_alias_requires_same_year_unitid_and_remains_flagged(self):
        bridge, _, strict, _ = run_case([directory(100001, "00200000")], [evidence(source="3")], [relation(100001)])
        self.assertEqual(bridge.loc[0, "unitid"], 100001)
        self.assertTrue(bridge.loc[0, "ipeds_identity_uses_historical_relation"])
        self.assertFalse(bridge.loc[0, "ipeds_identity_strict_contemporaneous"])
        self.assertEqual(len(strict), 0)
        missing, _, _, _ = run_case([directory(100002, "00200000")], [evidence(source="3")], [relation(100001)])
        self.assertEqual(missing.loc[0, "ipeds_resolution_status"], "crosswalk_site_missing_annual_directory")

    def test_administrative_duplicate_can_be_disambiguated_but_campus_duplicate_cannot(self):
        hd = [directory(), directory(100002, sector="0")]
        bridge, _, _, _ = run_case(hd, [evidence()], [relation(100001)])
        self.assertEqual(bridge.loc[0, "unitid"], 100001)
        hd[1]["sector"] = "1"
        blocked, _, _, _ = run_case(hd, [evidence()], [relation(100001)])
        self.assertEqual(blocked.loc[0, "ipeds_resolution_status"], "multiple_nonadministrative_directory_unitids")

    def test_incomplete_candidate_parse_cannot_certify_exact_hd(self):
        bridge, _, _, _ = run_case([directory()], [evidence(cw_site_parse_complete=False)], [relation(100001)])
        self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
        self.assertEqual(bridge.loc[0, "ipeds_resolution_status"], "crosswalk_site_parse_incomplete")

    def test_source_five_keeps_directory_only_status_without_parent_substitution(self):
        bridge, _, _, _ = run_case([directory()], [evidence(source="5")], [relation(100002, "parent_reference")])
        self.assertEqual(bridge.loc[0, "unitid"], 100001)
        self.assertEqual(bridge.loc[0, "ipeds_resolution_status"], "unique_annual_directory_only")
        self.assertTrue(bridge.loc[0, "ipeds_has_other_parent_unitid"])

    def test_component_scope_is_not_global_campus_exclusion(self):
        hd = directory(prch_f="3", idx_f="100002")
        bridge, _, _, _ = run_case([hd], [evidence()], [relation(100001)])
        self.assertTrue(bridge.loc[0, "ipeds_annual_identity_eligible"])
        self.assertEqual(bridge.loc[0, "ipeds_finance_scope"], "partial_child")
        self.assertEqual(bridge.loc[0, "ipeds_student_aid_scope"], "no_parent_child_relation_reported")
        self.assertEqual(annual.component_scope({"ipeds_year": 2015, "prch_f": "6", "idx_f": "100002"}, "finance"), "undocumented_code")
        self.assertEqual(annual.component_scope({"ipeds_year": 2016, "prch_f": "6", "idx_f": "100002"}, "finance"), "partial_parent_and_child")

    def test_geography_is_keyed_to_fsa_identity_when_bridge_reordered(self):
        panel, bridge = fixture(("00100000", "00200000"), ["FC", "PR"])
        bridge = bridge.iloc[::-1].reset_index(drop=True)
        result, linked, _, _ = annual.enrich_annual_linkage(panel, bridge, pd.DataFrame([directory(), directory(100002, "00200000")]), pd.DataFrame(), pd.DataFrame())
        by_id = result.set_index("opeid8")
        self.assertEqual(by_id.loc["00100000", "ipeds_geography_class"], "foreign_explicit")
        self.assertEqual(by_id.loc["00200000", "ipeds_geography_class"], "territories_and_freely_associated_states")
        pd.testing.assert_frame_equal(linked[panel.columns], panel)

    def test_end_crosswalk_conflict_blocks_strict_without_erasing_start_identity(self):
        for end_case in ["additional", "shifted", "incomplete"]:
            with self.subTest(end_case=end_case):
                hd = [directory(ipeds_year=year) for year in [2010, 2011]]
                records = [evidence(), evidence(cw_year=2011, cw_site_parse_complete=end_case != "incomplete")]
                end_relations = [dict(relation(100002 if end_case == "shifted" else 100001), cw_year=2011)]
                if end_case == "additional":
                    end_relations.append(dict(relation(100002, "additional_match"), cw_year=2011))
                bridge, linked, strict, _ = run_case(hd, records, [relation(100001)] + end_relations, strict=True)
                self.assertEqual(bridge.loc[0, "unitid"], 100001)
                self.assertTrue(bridge.loc[0, "ipeds_annual_identity_eligible"])
                self.assertEqual(bridge.loc[0, "ipeds_crosswalk_anchor_sensitivity"], "scope_or_identity_conflict")
                self.assertFalse(bridge.loc[0, "ipeds_strict_eligible"])
                self.assertEqual(bridge.loc[0, "ipeds_reporting_scope"], "requires_scope_review")
                self.assertEqual(len(strict), 0)
                self.assertEqual(len(linked), 1)

    def test_foreign_full_id_collision_keeps_candidate_but_blocks_assignment(self):
        panel, base = fixture(states=["FC"])
        panel["school"] = "University of the Arts London"
        panel["school_type"] = "Foreign Public"
        base["ipeds_strict_eligible"] = True
        hd = pd.DataFrame([directory(stabbr="NY", instnm="The Chubb Institute")])
        bridge, linked, strict, membership = annual.enrich_annual_linkage(panel, base, hd, pd.DataFrame(), pd.DataFrame())
        self.assertEqual(bridge.loc[0, "ipeds_resolution_status"], "fsa_ipeds_geography_identity_conflict")
        self.assertTrue(bridge.loc[0, "ipeds_identity_geography_conflict"])
        self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
        self.assertEqual(bridge.loc[0, "ipeds_candidate_unitid_before_descriptor_review"], 100001)
        self.assertEqual(bridge.loc[0, "ipeds_candidate_instnm"], "The Chubb Institute")
        self.assertFalse(bridge.loc[0, "ipeds_annual_identity_eligible"])
        self.assertEqual(membership.loc[0, "unitid"], 100001)
        self.assertFalse(membership.loc[0, "identity_resolved_to_candidate"])
        self.assertEqual(len(strict), 0)
        pd.testing.assert_frame_equal(linked[panel.columns], panel)

    def test_noninstitutional_consolidation_placeholder_never_receives_identity(self):
        panel, base = fixture(identities=("88888800",), states=["NR"])
        panel["school"] = "DEFAULT SCHOOL FOR CONSOLIDATED LOANS"
        hd = pd.DataFrame([directory(opeid="88888800")])
        bridge, linked, _, membership = annual.enrich_annual_linkage(panel, base, hd, pd.DataFrame(), pd.DataFrame())
        self.assertEqual(bridge.loc[0, "fsa_source_identity_kind"], "noninstitutional_placeholder")
        self.assertEqual(bridge.loc[0, "ipeds_resolution_status"], "noninstitutional_source_placeholder")
        self.assertTrue(pd.isna(bridge.loc[0, "unitid"]))
        self.assertFalse(bridge.loc[0, "ipeds_annual_identity_eligible"])
        self.assertEqual(membership.loc[0, "unitid"], 100001)
        self.assertFalse(membership.loc[0, "identity_resolved_to_candidate"])
        pd.testing.assert_frame_equal(linked[panel.columns], panel)


if __name__ == "__main__":
    unittest.main()
