from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from helpers import load_script_module

release = load_script_module("linkage_release_test", "Scripts/build_linkage_release.py")
linkage = load_script_module("linkage_separation_test", "Scripts/fsa_ipeds_linkage.py")


class SeparateLinkageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "fsa"
        self.root.mkdir()

    def fixture(self):
        panel = self.root / "Panels/final/fsa_volume_reports_panel_1999_2025.parquet"
        panel.parent.mkdir(parents=True)
        pd.DataFrame({"opeid8": ["00100000"], "award_year": ["2010-2011"],
                      "award_year_start": [2010], "grant__source_record_present": [True],
                      "grant__source_filename": ["grants.xls"], "grant__source_excel_row": [10],
                      "grant__pell_disbursements": [100.25], "grant__pell_disbursements__status": ["observed"],
                      "grant__pell_recipients": [10], "grant__pell_recipients__status": ["observed"]}).to_parquet(panel, index=False)
        dictionary = self.root / "Dictionary/fsa_volume_panel_dictionary.parquet"
        dictionary.parent.mkdir()
        pd.DataFrame({"panel_column": ["grant__pell_disbursements", "grant__pell_recipients"],
                      "column_role": ["measure", "measure"], "units": ["USD", "count"],
                      "definition": ["Pell disbursement amount", "Pell recipients"]}).to_parquet(dictionary, index=False)
        manifest = self.root / "build/research_release_manifest.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"release_version": "fixture", "acceptance_passed": True,
             "artifacts": [{"path": str(p.relative_to(self.root)), "bytes": p.stat().st_size,
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [panel, dictionary]]}))
        self.hd = self.base / "official"
        self.hd.mkdir()
        for year in [2010, 2011]:
            pd.DataFrame([{"UNITID": 100001, "OPEID": "00100000", "INSTNM": "Fixture College",
                           "SECTOR": 1, "OPEFLAG": 1, "CYACTIVE": 1, "ACT": "A"}]).to_csv(self.hd / f"HD{year}.csv", index=False)
        self.ledger = self.base / "identity.csv"
        self.ledger.write_text("resolution_id,family\n")
        import fsa_dependency
        lock = json.loads(fsa_dependency.LOCK_PATH.read_text())
        lock["identity_ledger"]["sha256"] = release.sha256(self.ledger)
        lock_path = self.base / "reviewed_fixture_dependency.json"
        lock_path.write_text(json.dumps(lock))
        selected_lock = patch.object(fsa_dependency, "LOCK_PATH", lock_path)
        selected_lock.start(); self.addCleanup(selected_lock.stop)
        observation = self.root / "Checks/observation_qc"
        observation.mkdir(parents=True)
        for family in release.FAMILIES:
            pd.DataFrame({"family": [], "award_year": [], "source_excel_row": []}).to_parquet(
                observation / f"{family}_quarantine.parquet", index=False)
            pd.DataFrame(columns=["award_year", "column", "sheet"]).to_csv(
                observation / f"{family}_schema_availability.csv", index=False)
        selected = self.root / "Checks/download_qc/selected_panel_files.csv"
        selected.parent.mkdir()
        pd.DataFrame(columns=["family", "award_year", "filename", "local_path", "sha256"]).to_csv(selected, index=False)
        code = {**lock["modules"], lock["identity_ledger"]["path"]: release.sha256(self.ledger)}
        accepted = json.loads(manifest.read_text())
        accepted["code_and_metadata"] = [{"path": name, "sha256": digest} for name, digest in code.items()]
        manifest.write_text(json.dumps(accepted))
        fingerprints = {str(p.relative_to(self.root)): release.sha256(p)
                        for p in [panel, dictionary, selected, *observation.iterdir()]}
        qa = {"run_id": "fixture-accepted-run", "code_and_metadata": code, "data": fingerprints}
        for name, body in [
                (release.QA_PATHS[0], qa),
                (release.QA_PATHS[1], {"run_id": qa["run_id"], "code_and_metadata": code, "arguments": {}}),
                (release.QA_PATHS[2], {**qa, "data": {k: v for k, v in fingerprints.items() if k != str(selected.relative_to(self.root))}})]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body))
        return panel, manifest

    def test_missing_research_root_never_uses_output_parents(self):
        with self.assertRaisesRegex(ValueError, "research_root is required"):
            linkage.run_ipeds_linkage(self.base / "missing", self.base / "hd", self.root / "Panels/ipeds")

    def test_panel_tamper_and_outside_panel_are_rejected(self):
        panel, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "explicit research root"):
            release.verify_source_release(self.root, self.base / "foreign.parquet")
        panel.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "differs from accepted release"):
            release.verify_source_release(self.root)

    def test_actual_linkage_and_exports_leave_upstream_unchanged(self):
        self.fixture()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = self.base / "combined_linkage"
        result = release.build_linkage_release(self.root, self.hd, output,
                                               identity_ledger=self.ledger, formats=("parquet",))
        self.assertTrue(result["completed"])
        self.assertTrue(result["source_inputs_unchanged"])
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        panel = pd.read_parquet(output / "Panels/ipeds/unitid_research/fsa_unitid_award_year_panel.parquet")
        self.assertEqual(panel.unitid.tolist(), [100001])
        self.assertEqual(panel.grant__pell_disbursements.tolist(), [100.25])
        accepted = json.loads((output / "build/research_release_manifest.json").read_text())
        self.assertTrue(accepted["acceptance_passed"])
        for entry in accepted["artifacts"]:
            self.assertEqual(release.sha256(output / entry["path"]), entry["sha256"])
        exported = json.loads((output / "Exports/unitid/dataset_metadata.json").read_text())
        self.assertEqual(exported["source_release_manifest_sha256"], result["release_manifest_sha256"])
        self.assertTrue((output / "Exports/unitid/source_unitid_manifest.json").is_file())
        with self.assertRaisesRegex(ValueError, "new or empty"):
            release.build_linkage_release(self.root, self.hd, output, identity_ledger=self.ledger)

    def test_source_cache_cannot_be_refreshed_inside_readonly_release(self):
        panel, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "separate from read-only"):
            linkage.run_ipeds_linkage(panel, self.root / "official", self.base / "out",
                                      research_root=self.root, download_missing=True)
        self.assertFalse((self.root / "official").exists())

    def test_preinvocation_quarantine_tamper_is_rejected(self):
        self.fixture()
        path = self.root / "Checks/observation_qc/grants_quarantine.parquet"
        pd.DataFrame({"family": ["grants"], "award_year": ["2010-2011"],
                      "source_excel_row": [11], "pell_disbursements": [999999999]}).to_parquet(path, index=False)
        output = self.base / "out"
        with self.assertRaisesRegex(ValueError, "accepted QA fingerprints.*grants_quarantine"):
            release.build_linkage_release(self.root, self.hd, output, identity_ledger=self.ledger)
        self.assertFalse(output.exists())

    def test_preinvocation_schema_tamper_is_rejected(self):
        self.fixture()
        path = self.root / "Checks/observation_qc/grants_schema_availability.csv"
        path.write_text("award_year,column,sheet\n2010-2011,invented_measure,Sheet1\n")
        with self.assertRaisesRegex(ValueError, "accepted QA fingerprints.*grants_schema"):
            release.build_linkage_release(self.root, self.hd, self.base / "out", identity_ledger=self.ledger)

    def test_inventory_tamper_is_rejected(self):
        self.fixture()
        path = self.root / "Checks/download_qc/selected_panel_files.csv"
        path.write_text("family,award_year,filename,local_path,sha256\ngrants,2010-2011,wrong.xls,wrong,wrong\n")
        with self.assertRaisesRegex(ValueError, "accepted QA fingerprints.*selected_panel_files"):
            release.build_linkage_release(self.root, self.hd, self.base / "out", identity_ledger=self.ledger)

    def test_changed_identity_ledger_requires_reviewed_lock(self):
        self.fixture()
        self.ledger.write_text("resolution_id,family\ninvented,grants\n")
        with self.assertRaisesRegex(ValueError, "Identity ledger differs from the reviewed"):
            release.build_linkage_release(self.root, self.hd, self.base / "out", identity_ledger=self.ledger)

    def test_missing_or_conflicting_acceptance_binding_is_rejected(self):
        self.fixture()
        path = self.root / release.QA_PATHS[2]
        original = path.read_text()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "evidence binding is missing"):
            release.build_linkage_release(self.root, self.hd, self.base / "out", identity_ledger=self.ledger)
        body = json.loads(original)
        body["data"]["Checks/observation_qc/grants_quarantine.parquet"] = "0" * 64
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "not bound to completed transformations"):
            release.build_linkage_release(self.root, self.hd, self.base / "out", identity_ledger=self.ledger)


if __name__ == "__main__":
    unittest.main()
