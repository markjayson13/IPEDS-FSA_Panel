"""Portable replay boundaries, adapted from the published handoff audit fixtures.

These tests use tiny invented bundles. They do not replay the full data or run
the historical publication script, whose absolute paths are audit evidence.
"""
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'Scripts'))


def load_builder():
    spec = importlib.util.spec_from_file_location(
        'replay_contract_builder', REPO / 'Scripts/16_build_fsa_ipeds_panel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body) if isinstance(body, (dict, list)) else body)
    return path


def record(root, relative):
    path = root / relative
    return {'path': relative, 'sha256': sha(path), 'bytes': path.stat().st_size}


class ReadBoundary(Exception):
    """Stop after replay verification/copy, before any full panel data read."""


class ReplayContracts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='ipeds-fsa-replay-fixture-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def replay_fixture(self, tamper=None):
        bundle, output = self.base / 'bundle', self.base / 'replay'
        bundle.mkdir()
        for relative, body in [
            ('Inputs/ipeds_panel.parquet', 'fixture'),
            ('Inputs/fsa_panel.parquet', 'fixture'),
            ('Inputs/fsa_bridge.parquet', 'fixture'),
            ('Metadata/codebook.csv', 'name\nx\n'),
            ('Metadata/value_labels.csv', 'code\n1\n'),
            ('Metadata/ipeds_metadata.json', {}),
            ('Scripts/placeholder.py', '# fixture'),
            ('Checks/canonical_ipeds_handoff/comparison.json', {'status': 'passed'}),
            ('Checks/canonical_ipeds_handoff/publication_validation.json', {'passed': True}),
            ('Inputs/ipeds/final_labeled_panel/binding.json', {'status': 'verified'}),
        ]:
            put(bundle, relative, body)
        files = [str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file()]
        put(bundle, 'build_manifest.json', {
            'completed': True,
            'artifacts': [record(bundle, relative) for relative in files],
            'source_records': [],
            'canonical_ipeds_handoff': {
                'binding': 'Inputs/ipeds/final_labeled_panel/binding.json',
                'validation': 'Checks/canonical_ipeds_handoff/publication_validation.json',
                'combined_analysis_labels_refreshed': True,
            },
        })
        if tamper == 'changed':
            put(bundle, 'Checks/canonical_ipeds_handoff/comparison.json', {'status': 'changed'})
        if tamper == 'extra':
            put(bundle, 'Checks/canonical_ipeds_handoff/uninventoried.json', {})
        builder, captured = load_builder(), {}

        def stop(*args, **kwargs):
            captured.update(inspect.currentframe().f_back.f_locals['manifest'])
            raise ReadBoundary()

        builder.read_keys = stop
        args = types.SimpleNamespace(output=output, bundle=bundle, resume_stage=None,
                                     formats='parquet')
        if tamper:
            with self.assertRaisesRegex(ValueError, 'Replay input differs|Unexpected or missing'):
                builder.build(args)
            return
        with self.assertRaises(ReadBoundary):
            builder.build(args)
        staged = next(output.glob('.building-*'))
        for name in ('comparison.json', 'publication_validation.json'):
            relative = 'Checks/canonical_ipeds_handoff/' + name
            self.assertEqual(sha(staged / relative), sha(bundle / relative))
        handoff = captured['canonical_ipeds_handoff']
        for field in ('binding', 'validation'):
            self.assertTrue((staged / handoff[field]).is_file())
        self.assertFalse(handoff['combined_analysis_outputs_created_by_this_replay'])
        self.assertTrue(handoff['combined_analysis_labels_refreshed_in_source_bundle'])
        self.assertNotIn('combined_analysis_labels_refreshed', handoff)

    def test_replay_copies_hashed_evidence_and_preserves_reference_scope(self):
        self.replay_fixture()

    def test_replay_refuses_changed_handoff_evidence(self):
        self.replay_fixture('changed')

    def test_replay_refuses_uninventoried_handoff_evidence(self):
        self.replay_fixture('extra')

    def test_actual_helpers_copy_and_isolated_imports(self):
        output = self.base / 'helpers'
        output.mkdir()
        builder = load_builder()
        builder.write_helpers(output, [2004, 2023])
        scripts = sorted((output / 'Scripts').glob('*.py'))
        self.assertEqual(len(scripts), 9)
        for path in scripts:
            self.assertEqual(sha(path), sha(REPO / 'Scripts' / path.name))
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(
            [str(output / 'Scripts')] + [p for p in sys.path if 'site-packages' in p])
        modules = [p.stem for p in scripts if not p.stem[0].isdigit()]
        for command in (
            [sys.executable, '-c', '\n'.join('import ' + name for name in modules)],
            [sys.executable, str(output / 'Scripts/16_build_fsa_ipeds_panel.py'), '--help'],
        ):
            result = subprocess.run(command, cwd=output, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
