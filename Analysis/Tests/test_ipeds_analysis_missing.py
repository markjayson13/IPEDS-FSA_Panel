import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
import json
import unittest
from copy import deepcopy
import pandas as pd
import ipeds_analysis_missing as x


def metadata(label='Not reported', code='-1', name='TEST', year=2023, table='HD2023'):
    base={'year': year, 'source_file': 'HD', 'access_table_name': table, 'varname': name, 'varnumber': '123'}
    return {'variables':[{'name': name,'storage_type':'double','source_metadata':[base],
       'lineage_records':[{**base,'source_varnumber':'123'}],
       'value_label_records':[{**base,'codevalue':code,'valuelabel':label}]}]}


class Rules(unittest.TestCase):
    def test_exact_table_no_fallback(self):
        m=metadata(); m['variables'][0]['value_label_records'][0]['access_table_name']='HD2022'
        p=x.build_policy(m)
        self.assertEqual(len(p['rules']),0)
        self.assertEqual(p['rejected_rules'][0]['reason'],'no_exact_lineage_and_dictionary_key')
    def test_exact_source_number(self):
        m=metadata();m['variables'][0]['value_label_records'][0]['varnumber']='456'
        self.assertEqual(len(x.build_policy(m)['rules']),0)
    def test_conflicting_labels_fail_closed(self):
        m=metadata();r=deepcopy(m['variables'][0]['value_label_records'][0]);r['valuelabel']='No';m['variables'][0]['value_label_records'].append(r)
        p=x.build_policy(m)
        self.assertEqual(len(p['rules']),0)
        self.assertEqual(p['rejected_rules'][0]['reason'],'contradictory_labels_for_exact_source_key')
    def test_parent_child_no_relationship_retained(self):
        self.assertIsNone(x.missing_reason('PRCH_SFA','-2','Not applicable'))
        self.assertIsNone(x.missing_reason('PRCHTP_F','-2','Not applicable'))
    def test_status_retained(self):
        for n in ['LOCK_IC','STAT_F','REV_SFA','PTA99_EF']:
            self.assertIsNone(x.missing_reason(n,'-2','Not applicable'))
    def test_no_and_imputed_response_retained(self):
        for n,c,l in [('F1FHA','0','Not reported - (imputed endowment assets)'),('F1FHA','2','No'),('ADMCON1','4','Do not know'),('CCBASIC','0','Not classified'),('DFRCGID__HD__X','901','Not applicable - U.S. Service schools')]:
            self.assertIsNone(x.missing_reason(n,c,l))
    def test_apply_exact_annual_code_and_keep_negative_finance(self):
        p=x.build_policy(metadata())
        frame=pd.DataFrame({'unitid':[1,2,3,4], 'year':[2023,2023,2022,2023], 'ipeds__TEST':[-1.,0.,-1.,-2.], 'ipeds__F1A01':[-1.,-2.,-300.,0.]})
        out,audit,counts=x.apply_missing_policy(frame,p)
        self.assertTrue(pd.isna(out.loc[0,'ipeds__TEST']))
        self.assertEqual(out.loc[2,'ipeds__TEST'],-1.)
        self.assertEqual(out.loc[3,'ipeds__TEST'],-2.)
        self.assertTrue(out['ipeds__F1A01'].equals(frame['ipeds__F1A01']))
        self.assertEqual(frame.loc[0,'ipeds__TEST'],-1.)
        self.assertEqual(len(audit),1)
        self.assertEqual(counts[0]['cells'],1)
    def test_text_codes_only_exact_numeric_spelling(self):
        p=x.build_policy(metadata())
        f=pd.DataFrame({'year':[2023]*6,'ipeds__TEST':['-1','-1.0',' -1.00 ','-01','1',None]})
        o,a,c=x.apply_missing_policy(f,p)
        self.assertEqual(o['ipeds__TEST'].isna().tolist(),[True,True,True,False,False,True])
    def test_source_label_reversal_preserved(self):
        self.assertEqual(x.missing_reason('DISTPGS','-1','Not applicable'),'not_applicable')
        self.assertEqual(x.missing_reason('DISTPGS','-2','Not reported'),'not_reported')
    def test_positive_missing_codes(self):
        self.assertEqual(x.missing_reason('CIPCODE6','991','Not reported'),'not_reported')
        self.assertEqual(x.missing_reason('SECTOR','99','Sector unknown (not active)'),'unknown')
    def test_no_globally_assumed_negatives(self):
        f=pd.DataFrame({'year':[2023]*3,'ipeds__TEST':[-1.,-2.,-3.]})
        out,a,c=x.apply_missing_policy(f,{'rules':[]})
        self.assertTrue(out.equals(f))
    def test_real_metadata_parent_and_finances_have_no_rules(self):
        p=json.load(open(Path(__file__).resolve().parents[1] / 'Decisions/ipeds_sentinel_policy.json'))
        self.assertFalse(any(r['analysis_column'].startswith('PRCH') for r in p['rules']))
        self.assertFalse(any(r['analysis_column']=='F1A01' for r in p['rules']))
        self.assertEqual(p['summary']['rejected_rules'],0)

if __name__=='__main__':unittest.main(verbosity=2)
