"""Repository and exported recipe boundaries; no canonical source writes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ANALYSIS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'analysis_portability_builder', ANALYSIS / 'Scripts/build_analysis_views.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class RecipePortabilityTests(unittest.TestCase):
    def check_reference_layout(self, external):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp).resolve() / 'original tree'
            root = tree / 'frozen bundle'
            output = tree / 'exports' / 'analysis output' if external else root / 'Analysis'
            output.mkdir(parents=True)
            source = 'Panels/fsa_ipeds_aid_aligned_2004_2023.parquet'
            expected = {
                'source': source,
                'annual_metadata': 'Metadata/ipeds_metadata.json',
                'canonical_ipeds_handoff': 'Inputs/ipeds/final_labeled_panel/binding.json',
            }
            for key, relative in expected.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('fixture ' + key)
            refs = b.source_references(root, output, source, include_handoff=True)
            # JSON round trip covers paths containing spaces without shell/URL
            # escaping. Sidecar location does not change the declared path base.
            refs = json.loads(json.dumps(refs))
            self.assertEqual(refs['path_base'], 'analysis_data_directory')
            self.assertEqual((output / refs['source_bundle']).resolve(), root.resolve())
            self.assertEqual(refs['source_in_bundle'], source)
            for key, relative in expected.items():
                self.assertFalse(Path(refs[key]).is_absolute())
                self.assertEqual((output / refs[key]).resolve(), root / relative)
                self.assertEqual((output / refs[key]).read_text(), 'fixture ' + key)
            if not external:
                self.assertEqual(refs['source_bundle'], '..')
                self.assertEqual(refs['annual_metadata'], '../Metadata/ipeds_metadata.json')
            moved = Path(tmp).resolve() / 'relocated tree'
            shutil.copytree(tree, moved)
            moved_output = moved / output.relative_to(tree)
            moved_root = moved / root.relative_to(tree)
            self.assertEqual((moved_output / refs['source_bundle']).resolve(), moved_root)
            for key, relative in expected.items():
                self.assertEqual((moved_output / refs[key]).resolve(), moved_root / relative)
                self.assertEqual((moved_output / refs[key]).read_text(), 'fixture ' + key)

    def test_sibling_bundle_references_resolve_after_relocation(self):
        self.check_reference_layout(external=False)

    def test_external_output_references_resolve_after_relocation(self):
        self.check_reference_layout(external=True)

    def test_export_receives_exact_canonical_category_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            b.copy_analysis_scripts(out)
            copied = out / 'Scripts/ipeds_category_storage.py'
            self.assertEqual(copied.read_bytes(), b.CATEGORY_HELPER.read_bytes())
            env = dict(os.environ)
            env['PYTHONPATH'] = os.pathsep.join(
                p for p in sys.path if p and 'site-packages' in p)
            result = subprocess.run(
                [sys.executable, str(out / 'Scripts/build_analysis_views.py'), '--help'],
                cwd=out, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--root', result.stdout)

    def test_source_hash_mismatch_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, decisions, out = (Path(tmp) / n for n in ('root', 'decisions', 'out'))
            root.mkdir(); decisions.mkdir()
            (root / 'Scripts').mkdir()
            helper = root / 'Scripts/fsa_portable_exports.py'
            helper.write_text('changed frozen helper')
            (decisions / 'consolidation_plan.json').write_text(json.dumps({
                'source_hashes': {'Scripts/fsa_portable_exports.py':
                                  hashlib.sha256(b'reviewed helper').hexdigest()}}))
            (decisions / 'missing_rules.json').write_text('{"rules": []}')
            argv = ['build_analysis_views.py', '--root', str(root), '--output', str(out),
                    '--decisions', str(decisions)]
            with patch.object(sys, 'argv', argv), self.assertRaisesRegex(
                    ValueError, 'Source changed since decision review'):
                b.main()
            self.assertFalse(out.exists())
            self.assertEqual(helper.read_text(), 'changed frozen helper')


if __name__ == '__main__':
    unittest.main()
