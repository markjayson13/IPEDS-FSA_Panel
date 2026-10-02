import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import pyarrow as pa

SCRIPTS = Path(__file__).resolve().parents[1] / 'Scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('combined_builder', SCRIPTS / '16_build_fsa_ipeds_panel.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CombinedPanelTests(unittest.TestCase):
    def setUp(self):
        self.ipeds = pa.table({'UNITID': pa.array([1,2,3], type=pa.int64()), 'year': pa.array([2004]*3, type=pa.int32()),
                               'OPEID': ['00000100','00000200','-2'], 'aid': [100.0, None, 0.0]})
        self.fsa = pa.table({'unitid': pa.array([1,1,2,9], type=pa.int64()), 'award_year_start':[2003,2004,2004,2004],
                             'included_source_family_count':[1,1,0,1], 'blocked_source_family_count':[0,0,1,0],
                             'grant__opeid8':['00000100','00000100','00000200','00000900'],
                             'grant__pell_disbursements':[101.125,202.25,None,999.0]})
        self.index = pd.MultiIndex.from_arrays([self.fsa['unitid'].to_pylist(), self.fsa['award_year_start'].to_pylist()])

    def schema(self, offset):
        names = [n for n,t in builder.DERIVED] + ['ipeds__'+n for n in self.ipeds.column_names] + ['fsa__'+n for n in self.fsa.column_names]
        md = pd.DataFrame({'canonical_name':names})
        return builder.combined_schema(self.ipeds.schema,self.fsa.schema,md,'test',offset)

    def test_collection_alignment_preserves_unmatched_and_blocked(self):
        output, aligned, matched = builder.merge_year(self.ipeds,self.fsa,self.index,2004,0,self.schema(0))
        self.assertEqual(output['merge_status'].to_pylist(), ['matched','matched','ipeds_only'])
        self.assertEqual(output['has_usable_fsa_record'].to_pylist(), [True,False,None])
        self.assertEqual(output['has_blocked_fsa_family'].to_pylist(), [False,True,None])
        self.assertEqual(output['fsa__grant__pell_disbursements'].to_pylist(), [202.25,None,None])
        self.assertEqual(output['ipeds__aid'].to_pylist(), [100.0,None,0.0])
        self.assertEqual(output['ipeds__OPEID'].to_pylist(), ['00000100','00000200','-2'])
        self.assertEqual(output['fsa_sfa_periods_aligned'].to_pylist(), [False,False,None])
        self.assertEqual(output['fsa__unitid'].type,pa.int64())
        self.assertEqual(len(output),3)  # FSA-only UNITID9 is never injected into IPEDS universe.

    def test_prior_alignment_uses_previous_award_year_only(self):
        output, aligned, matched = builder.merge_year(self.ipeds,self.fsa,self.index,2004,-1,self.schema(-1))
        self.assertEqual(output['fsa__grant__pell_disbursements'].to_pylist(), [101.125,None,None])
        self.assertEqual(output['fsa_sfa_periods_aligned'].to_pylist(), [True,None,None])
        self.assertEqual(output['expected_fsa_award_year_start'].to_pylist(),[2003]*3)
        self.assertEqual(output['ipeds_sfa_reference_year_end'].to_pylist(),[2004]*3)

    def test_keys_reject_null_duplicate_fraction_nonpositive(self):
        for values in [[1,None],[1,1],[1,1.5],[1,0]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                builder.validate_keys(pd.DataFrame({'id':values,'year':[2004,2004]}),'id','year')

    def test_exact_comparison_rejects_type_null_and_small_numeric_changes(self):
        a=pa.chunked_array([[1.0,None,float('nan')]])
        self.assertTrue(builder.exact_array(a,pa.chunked_array([[1.0,None,float('nan')]])))
        self.assertFalse(builder.exact_array(a,pa.chunked_array([[1.0,float('nan'),None]])))
        self.assertFalse(builder.exact_array(a,pa.chunked_array([[1.0000000000000002,None,float('nan')]])))
        self.assertFalse(builder.exact_array(pa.array([1]),pa.array([1.0])))

    def test_export_checkpoint_rejects_changed_data_or_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['Exports/csv', 'Checks/export_years']:
                (root / name).mkdir(parents=True)
            data = root / 'Exports/csv/fsa_ipeds_2004.csv.gz'
            data.write_bytes(b'previously verified export bytes')
            (root / 'Checks/export_years/string_nulls_2004.parquet').write_bytes(b'verified masks')
            checks = [{'format': 'csv', 'year': 2004, 'all_values_passed': True}]
            builder.write_json(root / 'Checks/export_years/checks_2004.json', checks)
            context = {'panel': 'reviewed-source-hash'}
            builder.checkpoint_export(root, 2004, context)
            self.assertEqual(builder.reusable_export(root, 2004, context, {'csv'}), checks)
            self.assertIsNone(builder.reusable_export(root, 2004, {'panel': 'changed'}, {'csv'}))
            self.assertIsNone(builder.reusable_export(root, 2004, context, {'csv', 'excel'}))
            data.write_bytes(b'altered export bytes')
            self.assertIsNone(builder.reusable_export(root, 2004, context, {'csv'}))

    def test_export_checkpoint_requires_all_artifact_records_and_passed_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'Checks/export_years'
            folder.mkdir(parents=True)
            checks = [{'format': 'csv', 'year': 2004, 'all_values_passed': False}]
            builder.write_json(folder / 'checks_2004.json', checks)
            with self.assertRaises(ValueError):
                builder.checkpoint_export(root, 2004, {})
            builder.write_json(folder / 'checkpoint_2004.json', {
                'year': 2004, 'formats': ['csv'], 'source_hashes': {}, 'artifacts': []})
            self.assertIsNone(builder.reusable_export(root, 2004, {}, {'csv'}))

    def test_promotion_preserves_destination_finder_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stage = root / '.building-example'
            stage.mkdir()
            (root / '.DS_Store').write_bytes(b'original Finder state')
            (stage / '.DS_Store').write_bytes(b'temporary Finder state')
            (stage / 'panel.parquet').write_bytes(b'verified panel')
            builder.promote_stage(stage, root)
            self.assertEqual((root / '.DS_Store').read_bytes(), b'original Finder state')
            self.assertEqual((root / 'panel.parquet').read_bytes(), b'verified panel')
            self.assertFalse(stage.exists())

    def test_promotion_refuses_real_collisions_before_moving_any_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stage = root / '.building-example'
            stage.mkdir()
            (root / 'existing').write_bytes(b'keep')
            (stage / 'existing').write_bytes(b'different')
            (stage / 'new').write_bytes(b'new')
            with self.assertRaises(ValueError): builder.promote_stage(stage, root)
            self.assertEqual((root / 'existing').read_bytes(), b'keep')
            self.assertTrue((stage / 'new').exists())

    def test_source_column_name_collisions_are_separate(self):
        schema=self.schema(0)
        self.assertIn('unitid',schema.names)
        self.assertIn('ipeds__UNITID',schema.names)
        self.assertIn('fsa__unitid',schema.names)
        self.assertEqual(len(schema.names),len(set(schema.names)))
        self.assertIn(b'variable_metadata',schema.field('fsa__unitid').metadata)


if __name__ == '__main__': unittest.main()
