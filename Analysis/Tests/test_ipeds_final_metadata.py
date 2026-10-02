from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Scripts'))
import pandas as pd
from ipeds_final_metadata import enrich_analysis_metadata, scoped_integer_labels, year_ranges, bound_native_value_labels

LABEL_COLUMNS=['canonical_name','portable_name','code','value','label','description']

def variable(name, conversion='integer_code_strings_to_numeric', records=None, description='Definition.'):
    return {'name':name,'label':'Fresh '+name,'description':description,'metadata_status':'complete','comparability_status':'unknown','stata_storage_conversion':conversion,'resolved_value_label_records':records or []}

def rec(year,code,label): return {'year':year,'codevalue':str(code),'valuelabel':label}
def row(name, sources=None, storage='numeric', method='identity',namespace='ipeds'):
    import json
    return {'portable_name':name,'canonical_name':name,'source_namespace':namespace,'analysis_method':method,'analysis_sources_json':json.dumps(sources or ['ipeds__'+name]),'original_canonical_name':'ipeds__'+name,'variable_label':'Old '+name,'description':'Old description','export_storage':storage,'analysis_reason':'Reviewed equivalent definitions','analysis_value_label_status':''}

class FinalMetadataTests(unittest.TestCase):
    def run_adapter(self, rows, variables, issues=None, labels=None):
        return enrich_analysis_metadata(pd.DataFrame(rows), pd.DataFrame(labels or [],columns=LABEL_COLUMNS), {'variables':variables,'issues':issues or [],'scoped_issues':[]})
    def test_original_state_and_opeid_strings_preserved(self):
        rows=[row('STABBR',storage='string'),row('OPEID',storage='string')]
        result, labels, audit=self.run_adapter(rows,[variable('STABBR','string_categories_to_numeric',[rec(2020,'CA','California')]),variable('OPEID','none')])
        self.assertTrue(result.export_storage.eq('string').all()); self.assertEqual(len(labels),0)
        self.assertEqual(result.portable_name.tolist(),['STABBR','OPEID'])
        self.assertFalse(audit['storage_changed']); self.assertFalse(audit['data_values_changed'])
    def test_year_changes_explicit_and_unknown_code_omitted(self):
        issue={'code':'unknown_observed_code','variable':'CAT','count':1}
        result,labels,audit=self.run_adapter([row('CAT')],[variable('CAT',records=[rec(2004,1,'Yes'),rec(2005,1,'Included'),rec(2007,1,'Yes')])],[issue])
        self.assertEqual(labels.iloc[0].label,'2004,2007: Yes; 2005: Included')
        self.assertIn('Unresolved upstream',result.iloc[0].description);self.assertEqual(labels.code.tolist(),[1])
    def test_disjoint_consolidated_scopes_combine(self):
        result,labels,_=self.run_adapter([row('CAT',['ipeds__OLD','ipeds__NEW'],method='coalesce')],[variable('OLD',records=[rec(2004,1,'Same')]),variable('NEW',records=[rec(2005,1,'Same')])])
        self.assertEqual(labels.iloc[0].label,'2004-2005: Same'); self.assertIn('Analysis consolidation:',result.iloc[0].description)
    def test_overlapping_conflicting_scopes_rejected(self):
        with self.assertRaisesRegex(ValueError,'Conflicting annual'):
            self.run_adapter([row('CAT',['ipeds__A','ipeds__B'],method='coalesce')],[variable('A',records=[rec(2004,1,'Yes')]),variable('B',records=[rec(2004,1,'No')])])
    def test_arbitrary_numeric_category_encoding_rejected(self):
        with self.assertRaisesRegex(ValueError,'Refusing arbitrary'):
            self.run_adapter([row('STABBR')],[variable('STABBR','string_categories_to_numeric',[rec(2004,'CA','California')])])
    def test_fsa_and_derived_columns_unchanged(self):
        rows=[row('PELL',namespace='fsa'),row('OPEID8',method='opeid8'),row('CAT')]
        cb,_,audit=self.run_adapter(rows,[variable('CAT')]); self.assertEqual(cb.iloc[0]['description'],'Old description');self.assertEqual(cb.iloc[1]['description'],'Old description')
        self.assertEqual(audit['counts']['untouched_other_columns'],2)
    def test_idempotence_and_full_labels_retained(self):
        var=variable('CAT',records=[rec(2004,1,'Yes')]);var['label']='Long label '*20
        inputs=pd.DataFrame([row('CAT')]);empty=pd.DataFrame(columns=LABEL_COLUMNS);up={'variables':[var]}
        a,b,_=enrich_analysis_metadata(inputs,empty,up);c,d,_=enrich_analysis_metadata(a,b,up)
        pd.testing.assert_frame_equal(a,c);pd.testing.assert_frame_equal(b,d)
        self.assertEqual(len(a.iloc[0].variable_label),80);self.assertEqual(a.iloc[0].upstream_full_variable_label,var['label'])
    def test_names_cannot_be_inferred(self):
        with self.assertRaisesRegex(ValueError,'Unknown exact'):
            self.run_adapter([row('SHORT',['ipeds__MISSING'])],[variable('OTHER')])
    def test_leading_zero_code_not_coerced(self):
        with self.assertRaisesRegex(ValueError,'Noncanonical integer'):
            self.run_adapter([row('CAT')],[variable('CAT',records=[rec(2004,'01','One')])])
    def test_year_ranges_do_not_fill_gaps(self):
        self.assertEqual(year_ranges([2004,2006,2007]),'2004,2006-2007')

    def test_aggregate_utf8_label_limit_and_full_meaning(self):
        text='2004-2023: '+('Définition différente; '*100)
        labels=pd.DataFrame([{'canonical_name':'cat','portable_name':'cat','code':i,'value':str(i),'label':text,'description':'full'} for i in range(274)])
        bounded,audit=bound_native_value_labels(labels)
        self.assertLessEqual(sum(len(t.encode('utf8'))+1 for t in bounded.label),32000)
        self.assertTrue(bounded.full_label.eq(text).all())
        self.assertTrue(bounded.label.str.contains('see full_label').all())
        self.assertEqual(audit['cat']['entries'],274)
        again,_=bound_native_value_labels(bounded)
        pd.testing.assert_frame_equal(bounded,again)
    def test_compacted_native_labels_actual_stata_roundtrip(self):
        import tempfile
        from pathlib import Path
        text='2004-2023: '+('A very long year-specific definition; '*100)
        labels=pd.DataFrame([{'canonical_name':'cat','portable_name':'cat','code':i,'value':str(i),'label':text,'description':'full'} for i in range(274)])
        bounded,_=bound_native_value_labels(labels)
        mapping=dict(zip(bounded.code,bounded.label))
        frame=pd.DataFrame({'cat':range(274)})
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'test.dta'
            frame.to_stata(path,write_index=False,version=118,value_labels={'cat':mapping})
            with pd.read_stata(path,iterator=True,convert_categoricals=False) as reader:
                self.assertEqual(reader.value_labels(),{'cat':mapping})
                self.assertEqual(reader.read().cat.tolist(),list(range(274)))

if __name__=='__main__': unittest.main()
