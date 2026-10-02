from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from helpers import load_script_module

dependency = load_script_module("dependency_under_test", "Scripts/fsa_dependency.py")


class DependencyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "upstream"
        (self.source / "Scripts").mkdir(parents=True)
        files = {
            "fsa_observations": "TOKEN = 'reviewed upstream'\n",
            "fsa_build_utils": "from fsa_observations import TOKEN\n",
            "fsa_loan_harmonization": "TOKEN = 'reviewed harmonization'\n",
        }
        modules = {}
        for name, content in files.items():
            relative = "Scripts/" + name + ".py"
            (self.source / relative).write_text(content)
            modules[relative] = hashlib.sha256(content.encode()).hexdigest()
        self.lock = self.base / "lock.json"
        self.lock.write_text(json.dumps({"modules": modules}))
        for p in [patch.object(dependency, "LOCK_PATH", self.lock),
                  patch.object(dependency, "_configured_root", None),
                  patch.dict(os.environ, {}, clear=True)]:
            p.start(); self.addCleanup(p.stop)

    def test_missing_dependency_has_actionable_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "FSA_SOURCE_REPO"):
            dependency.resolve_source_repo(self.base / "missing")

    def test_explicit_bad_path_does_not_fall_back_to_environment(self):
        with patch.dict(os.environ, {"FSA_SOURCE_REPO": str(self.source)}):
            with self.assertRaises(FileNotFoundError):
                dependency.resolve_source_repo(self.base / "missing")

    def test_only_exact_sibling_is_considered(self):
        downstream = self.base / "projects/combined"
        downstream.mkdir(parents=True)
        # A parent's Scripts directory is intentionally not a dependency.
        (downstream.parent / "Scripts").mkdir()
        (downstream.parent / "Scripts/fsa_observations.py").write_text("TOKEN='wrong'\n")
        with self.assertRaises(FileNotFoundError):
            dependency.resolve_source_repo(downstream_repo=downstream)
        (downstream.parent / "FSAVolumeReports_Panel-main").symlink_to(self.source, target_is_directory=True)
        self.assertEqual(dependency.resolve_source_repo(downstream_repo=downstream), self.source)

    def test_changed_content_is_rejected_before_import(self):
        (self.source / "Scripts/fsa_observations.py").write_text("raise RuntimeError('must not execute')\n")
        with self.assertRaisesRegex(ValueError, "differs from reviewed"):
            dependency.upstream_module("fsa_observations", self.source)

    def test_import_uses_locked_file_and_restores_foreign_alias(self):
        foreign = types.ModuleType("fsa_observations")
        foreign.TOKEN = "unreviewed preloaded module"
        before_path = list(sys.path)
        with patch.dict(sys.modules, {"fsa_observations": foreign}):
            module = dependency.upstream_module("fsa_build_utils", self.source)
            self.assertEqual(module.TOKEN, "reviewed upstream")
            self.assertIs(sys.modules["fsa_observations"], foreign)
        self.assertEqual(sys.path, before_path)
        self.assertFalse(list(self.source.rglob("__pycache__")))

    def test_unapproved_module_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not an approved dependency"):
            dependency.upstream_module("unreviewed_module", self.source)

    def test_output_must_not_overlap_source_in_either_direction(self):
        for output in [self.source, self.source / "Panels/ipeds", self.source.parent]:
            with self.assertRaisesRegex(ValueError, "separate from read-only"):
                dependency.require_separate_output(output, self.source)
        destination = self.base / "downstream"
        self.assertEqual(dependency.require_separate_output(destination, self.source), destination)

    def test_output_symlink_cannot_hide_source_mutation(self):
        alias = self.base / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            dependency.require_separate_output(alias / "outputs", self.source)


if __name__ == "__main__":
    unittest.main()
