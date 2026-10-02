import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
import pandas as pd

HERE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('FSA_MASTER_ROOT', str(HERE.parent)))
sys.path.insert(0, str(ROOT / 'Scripts'))
import fsa_portable_exports as h
spec = importlib.util.spec_from_file_location('builder_guard_test', HERE / 'Scripts/build_analysis_views.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

class StataCountGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'small.dta'
        self.data = pd.DataFrame({'x': [0.0, 12.5]})
        self.metadata = pd.DataFrame([{'canonical_name':'x','portable_name':'x','variable_label':'Amount',
            'description':'A measure','export_storage':'numeric','units':'USD','original_dtype':'float64'}])
        self.labels = pd.DataFrame(columns=['canonical_name','portable_name','code','value','label','description'])
        h.write_stata(self.data, self.path, self.metadata, self.labels)
    def tearDown(self):
        self.tmp.cleanup()
    def test_valid_exact_count(self):
        self.assertTrue(b.verify_stata_exact(self.path,self.data,self.metadata,self.labels,h)['exact_observation_count_passed'])
    def test_extra_row_rejected(self):
        with self.assertRaisesRegex(ValueError,'observation count'):
            b.verify_stata_exact(self.path,self.data.iloc[:1],self.metadata,self.labels,h)
    def test_missing_row_rejected(self):
        with self.assertRaisesRegex(ValueError,'observation count'):
            b.verify_stata_exact(self.path,pd.concat([self.data,self.data]),self.metadata,self.labels,h)

if __name__ == '__main__':
    unittest.main()
