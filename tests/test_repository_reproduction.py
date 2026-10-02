import importlib.util
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import subprocess
import os

spec = importlib.util.spec_from_file_location('repository_reproduction', Path(__file__).resolve().parents[1] / 'Scripts/reproduce_panel.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ReproductionContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bundle = self.base / 'bundle'
        self.bundle.mkdir()
        (self.bundle / 'build_manifest.json').write_text(json.dumps({'completed': True}))
        (self.bundle / 'input').write_bytes(b'verified-source')
        self.plan = self.base / 'plan.json'
        self.plan.write_text(json.dumps({'source_hashes': {'input': hashlib.sha256(b'verified-source').hexdigest()}}))

    def test_verified_source_then_tampering_rejected(self):
        self.assertEqual(runner.preflight(self.bundle, decisions=self.plan)['checked_files'], 1)
        (self.bundle / 'input').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed since review'):
            runner.preflight(self.bundle, decisions=self.plan)

    def test_incomplete_bundle_rejected(self):
        (self.bundle / 'build_manifest.json').write_text('{"completed": false}')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            runner.preflight(self.bundle, decisions=self.plan)

    def test_external_symlink_or_path_escape_rejected(self):
        other = self.base / 'outside'
        other.write_bytes(b'verified-source')
        digest = hashlib.sha256(other.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            runner.checked_file(self.bundle, '../outside', digest)
        (self.bundle / 'link').symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'redirected'):
            runner.checked_file(self.bundle, 'link', digest)

    def test_all_builds_analysis_from_new_masters(self):
        output = self.base / 'output'
        commands = runner.commands(self.bundle, output, 'all')
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[1][commands[1].index('--root') + 1], str(output))
        self.assertEqual(commands[1][commands[1].index('--output') + 1], str(output / 'Analysis'))

    def test_existing_output_refused_before_build(self):
        with self.assertRaisesRegex(ValueError, 'new output directory'):
            runner.main([str(self.bundle), '--bundle', str(self.bundle)])

    def test_bootstrap_rejects_overlap_before_creating_runtime(self):
        script = runner.REPO / 'reproduce.sh'
        scenarios = [(self.bundle / 'output', self.base / 'runtime'),
                     (self.base / 'output', self.bundle / 'runtime'),
                     (self.base / 'output', self.base / 'output/runtime')]
        before = {str(p.relative_to(self.bundle)): p.read_bytes() for p in self.bundle.rglob('*') if p.is_file()}
        for output, runtime in scenarios:
            with self.subTest(output=output, runtime=runtime):
                env = dict(os.environ)
                env.pop('IPEDS_FSA_PYTHON', None)
                env['IPEDS_FSA_RUNTIME'] = str(runtime)
                result = subprocess.run(['bash', str(script), str(output), '--bundle', str(self.bundle)],
                                        env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(runtime.exists())
                self.assertFalse(output.exists())
                self.assertEqual(before, {str(p.relative_to(self.bundle)): p.read_bytes() for p in self.bundle.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
