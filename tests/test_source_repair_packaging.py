"""Integrity and portability regressions for upstream metadata-only repairs."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Scripts'))
from fsa_ipeds_source_repair import LINEAGE, PANEL, PRCH, source_repair_inputs


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SourceRepairPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'repair'
        self.root.mkdir()
        self.contents = {
            PANEL: b'same clean panel values',
            'Dictionary/v2/dictionary_lake.parquet': b'corrected metadata only',
            'Dictionary/v2/dictionary_codes.parquet': b'codes unchanged',
            LINEAGE: b'lineage unchanged',
            PRCH + 'panel_clean.parquet': b'same clean panel values',
            PRCH + 'prch_cell_actions.parquet': b'actions unchanged',
            'README.md': b'Metadata-only repair.',
            'Evidence/source_metadata_corrections/test-v1.evidence.json': json.dumps({
                'correction_version': 'test-v1',
                'imputation_flag_evidence': 'Item imputation flags are absent from this Access release.',
            }).encode(),
            'Evidence/source_metadata_corrections/test-v1.json': b'{"version":"test-v1"}',
        }
        prch = []
        for relative, data in self.contents.items():
            if relative.startswith(PRCH):
                prch.append({'name': Path(relative).name, 'bytes': len(data), 'sha256': sha(data)})
        self.contents[PRCH + 'prch_run_manifest.json'] = json.dumps({
            'status': 'complete', 'artifacts': prch,
        }).encode()
        self.manifest = {'correction_version': 'test-v1', 'status': 'verified', 'artifacts': []}
        for relative, data in self.contents.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.manifest['artifacts'].append({'path': relative, 'bytes': len(data), 'sha256': sha(data)})
        self.receipt()

    def tearDown(self):
        self.temp.cleanup()

    def receipt(self):
        data = json.dumps(self.manifest).encode()
        (self.root / 'manifest.json').write_bytes(data)
        lines = [f"{record['sha256']}  {record['path']}" for record in self.manifest['artifacts']]
        lines.append(f'{sha(data)}  manifest.json')
        (self.root / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')

    def run_helper(self):
        return source_repair_inputs(self.root, self.root / PANEL)

    def test_metadata_only_bundle_recognized_and_evidence_portable(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        sources, summary = self.run_helper()
        self.assertEqual(summary['correction_version'], 'test-v1')
        self.assertEqual(summary['selected_panel_sha256'], sha(self.contents[PANEL]))
        self.assertEqual(summary['source_lineage_sha256'], sha(self.contents[LINEAGE]))
        self.assertEqual(summary['evidence_path_map']['contracts/source_metadata_corrections/test-v1.json'],
                         'Inputs/ipeds/source_metadata_repair/Evidence/source_metadata_corrections/test-v1.json')
        self.assertEqual(len(sources), 5)
        self.assertTrue(all(dest.startswith('ipeds/source_metadata_repair/') for _, dest in sources))
        self.assertEqual(summary['documented_unavailable_flags'][0]['documented_statement'],
                         'Item imputation flags are absent from this Access release.')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_baseline_without_manifest(self):
        (self.root / 'manifest.json').unlink()
        self.assertEqual(self.run_helper(), ([], None))

    def test_baseline_without_correction_version(self):
        (self.root / 'manifest.json').write_text('{"other_manifest": true}')
        self.assertEqual(self.run_helper(), ([], None))

    def test_unverified_bundle_rejected(self):
        self.manifest['status'] = 'building'
        self.receipt()
        with self.assertRaisesRegex(ValueError, 'status verified'):
            self.run_helper()

    def test_selected_baseline_panel_rejected_even_when_bytes_equal(self):
        baseline = Path(self.temp.name) / 'baseline.parquet'
        baseline.write_bytes(self.contents[PANEL])
        with self.assertRaisesRegex(ValueError, 'not the recorded'):
            source_repair_inputs(self.root, baseline)

    def test_panel_tamper_rejected(self):
        (self.root / PANEL).write_bytes(b'altered clean panelvalues')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.run_helper()

    def test_dictionary_same_size_tamper_rejected(self):
        p = self.root / 'Dictionary/v2/dictionary_lake.parquet'
        p.write_bytes(b'X' * p.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.run_helper()

    def test_lineage_tamper_rejected(self):
        (self.root / LINEAGE).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.run_helper()

    def test_evidence_tamper_rejected(self):
        (self.root / 'Evidence/source_metadata_corrections/test-v1.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.run_helper()

    def test_prch_tamper_rejected(self):
        (self.root / PRCH / 'prch_cell_actions.parquet').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.run_helper()

    def test_manifest_tamper_rejected(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest, indent=2))
        with self.assertRaisesRegex(ValueError, 'manifest.json SHA256SUMS mismatch'):
            self.run_helper()

    def test_unsafe_manifest_paths_rejected(self):
        for path in ('../outside', '/absolute', 'Evidence/../other', './Evidence/a', 'Evidence//a', 'Evidence\\a'):
            with self.subTest(path=path):
                original = self.manifest['artifacts'][-1]['path']
                self.manifest['artifacts'][-1]['path'] = path
                self.receipt()
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    self.run_helper()
                self.manifest['artifacts'][-1]['path'] = original

    def test_duplicate_manifest_path_rejected(self):
        self.manifest['artifacts'].append(self.manifest['artifacts'][0].copy())
        self.receipt()
        with self.assertRaisesRegex(ValueError, 'Duplicate repair manifest'):
            self.run_helper()

    def test_unsafe_checksum_path_rejected(self):
        with (self.root / 'SHA256SUMS').open('a') as f:
            f.write(f"{'0' * 64}  ../outside\n")
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.run_helper()

    def test_symlink_escape_rejected(self):
        path = self.root / 'Evidence/source_metadata_corrections/test-v1.json'
        outside = Path(self.temp.name) / 'outside'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes source root'):
            self.run_helper()

    def test_unrecorded_evidence_rejected(self):
        (self.root / 'Evidence/addendum.txt').write_text('not in receipt')
        with self.assertRaisesRegex(ValueError, 'unrecorded'):
            self.run_helper()

    def test_missing_required_metadata_rejected(self):
        self.manifest['artifacts'] = [a for a in self.manifest['artifacts'] if a['path'] != LINEAGE]
        self.receipt()
        with self.assertRaisesRegex(ValueError, 'omits required artifact'):
            self.run_helper()

    def test_checksum_disagreement_rejected(self):
        text = (self.root / 'SHA256SUMS').read_text()
        (self.root / 'SHA256SUMS').write_text(text.replace(sha(self.contents[LINEAGE]), '0' * 64))
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.run_helper()

    def test_unrecorded_prch_artifact_rejected(self):
        (self.root / PRCH / 'unrecorded.csv').write_text('extra artifact')
        with self.assertRaisesRegex(ValueError, 'PRCH directory contains'):
            self.run_helper()

    def test_prch_inner_manifest_disagreement_rejected(self):
        relative = PRCH + 'prch_run_manifest.json'
        document = json.loads((self.root / relative).read_text())
        document['artifacts'][0]['sha256'] = '0' * 64
        data = json.dumps(document).encode()
        (self.root / relative).write_bytes(data)
        record = next(a for a in self.manifest['artifacts'] if a['path'] == relative)
        record.update(bytes=len(data), sha256=sha(data))
        self.receipt()
        with self.assertRaisesRegex(ValueError, 'PRCH manifest disagrees'):
            self.run_helper()

    def test_unrelated_exports_not_rehashed(self):
        # The helper narrows to consumed input integrity; the upstream full audit
        # owns unrelated exports. This avoids an expensive 441-file replay.
        self.manifest['artifacts'].append({'path': 'Exports/unused.bin', 'bytes': 5, 'sha256': '0' * 64})
        self.receipt()
        _, summary = self.run_helper()
        self.assertNotIn('Exports/unused.bin', summary['verified_artifacts'])


if __name__ == '__main__':
    unittest.main()
