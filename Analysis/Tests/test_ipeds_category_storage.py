import os
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
# Generated Analysis bundles carry this shared helper locally; source checkouts
# import the canonical implementation from the repository Scripts directory.
SCRIPTS = HERE / 'Scripts'
if not (SCRIPTS / 'ipeds_category_storage.py').is_file():
    SCRIPTS = HERE.parent / 'Scripts'
sys.path.insert(0, str(SCRIPTS))
import json
from pathlib import Path
import unittest
import pandas as pd
from ipeds_category_storage import convert_numeric_category,ipeds_opeid_views

class Category(unittest.TestCase):
    def test_retains_negative_nominal_codes_and_nulls(self):
        s=pd.Series(['-2','0','1','2',None],dtype='string')
        got=convert_numeric_category(s,{'source_column':'ipeds__PRCH_SFA','target_storage':'Int8'})
        self.assertEqual(str(got.dtype),'Int8')
        self.assertEqual(got.iloc[:4].tolist(),[-2,0,1,2])
        self.assertTrue(pd.isna(got.iloc[4]))
    def test_no_lossy_leading_zero_cast(self):
        with self.assertRaises(ValueError):convert_numeric_category(pd.Series(['01']),{'source_column':'X','target_storage':'Int8'})
    def test_no_silent_whitespace_or_decimal_cast(self):
        for v in ['1.0',' 1','1 ','+1']:
            with self.assertRaises(ValueError):convert_numeric_category(pd.Series([v]),{'source_column':'X','target_storage':'Int8'})
    def test_all_observed_real_token_roundtrips(self):
        policy=json.loads((Path(__file__).resolve().parents[1] / 'Decisions/ipeds_numeric_categories.json').read_text())
        for rule in policy['allowlist']:
            s=pd.Series(list(rule['observed_codes'])+[None],dtype='string')
            result=convert_numeric_category(s,rule)
            self.assertTrue(s.equals(result.astype('string')),rule['source_column'])
    def test_stable_values_use_no_mixed_meanings(self):
        policy=json.loads((Path(__file__).resolve().parents[1] / 'Decisions/ipeds_numeric_categories.json').read_text())
        for r in policy['allowlist']:
            if r['stable_value_labels']:
                self.assertFalse(r['value_label_conflicts'])
                self.assertFalse(r['observed_codes_without_annual_labels'])
                self.assertEqual(r['code_records_not_matching_exact_lineage'],0)
    def test_opeid_alpha_is_preserved_not_parent_or_numeric_branch(self):
        out=ipeds_opeid_views(pd.Series(['00100200','00100201','001002A1','001002G1','-2',None,'00000000']))
        self.assertEqual(out.loc[0,'ipeds_opeid8_numeric'],'00100200')
        self.assertEqual(out.loc[2,'ipeds_opeid_source'],'001002A1')
        self.assertTrue(pd.isna(out.loc[2,'ipeds_opeid8_numeric']))
        self.assertTrue(pd.isna(out.loc[3,'ipeds_opeid8_numeric']))
        self.assertTrue(pd.isna(out.loc[4,'ipeds_opeid_source']))
        self.assertEqual(out.loc[4,'ipeds_opeid_format_status'],'not_applicable')
        self.assertTrue(pd.isna(out.loc[6,'ipeds_opeid8_numeric']))
    def test_numeric_id_input_refused(self):
        with self.assertRaises(ValueError):ipeds_opeid_views(pd.Series([100200]))
if __name__=='__main__':unittest.main(verbosity=2)
