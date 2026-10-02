import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd
import pyarrow.parquet as pq

BUILDER = Path(__file__).resolve().parents[1] / 'Scripts/build_analysis_views.py'
spec = importlib.util.spec_from_file_location('analysis_builder_under_test', BUILDER)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
ROOT = Path(os.environ.get('FSA_MASTER_ROOT', str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(ROOT / 'Scripts'))
import fsa_portable_exports as h


def meta_row(name, kind='status'):
    return dict(canonical_name=name,portable_name=name,variable_label='Status',description='Documented status or numeric source code.',category_kind=kind,export_storage='coded_category' if kind else 'numeric',original_dtype='object' if kind else 'Int64',status_variable='',reference_year_variable='',lower_bound_variable='',upper_bound_variable='',partial_sum_variable='',raw_token_variable='',units='category')


def group(names=('a','b'), method='coalesce'):
    return dict(sources=list(names),name='result',target='result',method=method,reason='Approved equivalent definitions',metadata_source=names[0])


def rules(status=False):
    names=['old__status','new__status'] if status else ['old','new']
    g=group(names, 'year_partition')
    g['year_rules']=[dict(source=names[0],min=1999,max=2005),dict(source=names[1],min=2006,max=2024)]
    return g


class CoalescingTests(unittest.TestCase):
    def test_zero_null_and_extension(self):
        f=pd.DataFrame({'a':pd.Series([0,pd.NA,7,pd.NA],dtype='Int64'),'b':pd.Series([0,4,pd.NA,pd.NA],dtype='Int64')})
        out,n=b.coalesce_checked(f,['a','b'])
        self.assertEqual(n,1)
        pd.testing.assert_series_equal(out.reset_index(drop=True),pd.Series([0,4,7,pd.NA],dtype='Int64',name='a'))
    def test_conflicting_zero_rejected(self):
        with self.assertRaisesRegex(ValueError,'Conflicting observed values'):
            b.coalesce_checked(pd.DataFrame({'a':[0,3],'b':[1,3]}),['a','b'])
    def test_multiple_overlapping_sources_not_summed(self):
        out,n=b.coalesce_checked(pd.DataFrame({'a':[7,0],'b':[7,0],'c':[7,0]}),['a','b','c'])
        self.assertEqual(out.tolist(),[7,0]);self.assertEqual(n,4)
    def test_source_order_does_not_change_values(self):
        f=pd.DataFrame({'a':pd.Series([pd.NA,3,0],dtype='Int64'),'b':pd.Series([2,pd.NA,0],dtype='Int64')})
        x,_=b.coalesce_checked(f,['a','b']);y,_=b.coalesce_checked(f,['b','a'])
        pd.testing.assert_series_equal(x,y,check_names=False)
    def test_empty_string_is_an_observed_value(self):
        with self.assertRaises(ValueError):b.coalesce_checked(pd.DataFrame({'a':[''],'b':['school']}),['a','b'])


class PartitionTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({'award_year_start':pd.Series([2005,2006,pd.NA],dtype='Int64'),'old':pd.Series([0,pd.NA,pd.NA],dtype='Int64'),'new':pd.Series([pd.NA,9,pd.NA],dtype='Int64')})
    def test_values_follow_year_boundary(self):
        out,_=b.project_spec(self.frame(),rules(),True)
        self.assertEqual(out.iloc[:2].tolist(),[0,9]);self.assertTrue(pd.isna(out.iloc[2]))
    def test_status_uses_year_even_when_other_source_nonmissing(self):
        f=pd.DataFrame({'award_year_start':[2005,2006],'old__status':['observed_zero','unavailable_in_schema'],'new__status':['unavailable_in_schema','source_symbol']})
        out,_=b.project_spec(f,rules(True),True)
        self.assertEqual(out.tolist(),['observed_zero','source_symbol'])
    def test_out_of_period_zero_is_rejected(self):
        f=self.frame();f.loc[1,'old']=0
        with self.assertRaisesRegex(ValueError,'outside'):b.project_spec(f,rules(),True)
    def test_overlapping_ranges_rejected(self):
        s=rules();s['year_rules'][1]['min']=2005
        with self.assertRaisesRegex(ValueError,'overlap'):b.project_spec(self.frame(),s,True)
    def test_omitted_year_rejected(self):
        s=rules();s['year_rules'][0]['max']=2004
        with self.assertRaisesRegex(ValueError,'omit'):b.project_spec(self.frame(),s,True)
    def test_missing_source_rule_rejected(self):
        s=rules();s['year_rules']=s['year_rules'][:1]
        with self.assertRaisesRegex(ValueError,'Every partitioned'):b.project_spec(self.frame(),s,True)
    def test_missing_year_does_not_accept_observed_amount(self):
        f=self.frame();f.loc[2,'old']=4
        with self.assertRaisesRegex(ValueError,'outside'):b.project_spec(f,rules(),True)
    def test_combined_namespace_uses_fsa_award_year(self):
        f=self.frame().rename(columns={'award_year_start':'fsa__award_year_start','old':'fsa__old','new':'fsa__new'});f['year']=[2006,2007,2008]
        s=rules();s['sources']=['fsa__'+c for c in s['sources']];s['year_rules']=[{**r,'source':'fsa__'+r['source']} for r in s['year_rules']]
        out,_=b.project_spec(f,s,False);self.assertEqual(out.iloc[:2].tolist(),[0,9])


class MetadataTests(unittest.TestCase):
    def labels(self, rows):
        return pd.DataFrame([dict(canonical_name=n,portable_name=n,code=c,value=v,label=l) for n,c,v,l in rows],columns=['canonical_name','portable_name','code','value','label'])
    def test_category_union_recodes_tokens_without_loss(self):
        cb=pd.DataFrame([meta_row('a'),meta_row('b')]);vl=self.labels([('a',1,'observed','Observed'),('b',1,'source_symbol','Source symbol')])
        meta,labels,_=b.metadata_for_specs([group()],cb,vl)
        self.assertFalse(labels.duplicated(['canonical_name','code']).any());self.assertEqual(set(labels.value),{'observed','source_symbol'})
        frame=h.prepare_export_frame(pd.DataFrame({'result':['observed','source_symbol',None]}),meta,labels)
        self.assertEqual(int(frame.result.notna().sum()),2);self.assertEqual(frame.result.nunique(),2)
    def test_same_token_with_incompatible_meaning_rejected(self):
        cb=pd.DataFrame([meta_row('a'),meta_row('b')]);vl=self.labels([('a',1,'observed','Observed'),('b',2,'observed','Not observed')])
        with self.assertRaisesRegex(ValueError,'Conflicting category'):b.metadata_for_specs([group()],cb,vl)
    def test_numeric_annual_label_conflict_does_not_change_numeric_codes(self):
        cb=pd.DataFrame([meta_row('a',''),meta_row('b','')]);vl=self.labels([('a',1,'1','Active'),('b',1,'1','Inactive')])
        meta,labels,_=b.metadata_for_specs([group()],cb,vl)
        self.assertEqual(len(labels),0);self.assertIn('vary by year',meta.iloc[0].description)
        frame=h.prepare_export_frame(pd.DataFrame({'result':[1,None]}),meta,labels);self.assertEqual(frame.result.iloc[0],1)
    def test_year_partition_retains_both_companion_paths(self):
        a=meta_row('old','');z=meta_row('new','');a['lower_bound_variable']='old_lb';z['lower_bound_variable']='new_lb'
        s=rules();s['metadata_source']='new'
        meta,_,_=b.metadata_for_specs([s],pd.DataFrame([a,z]),self.labels([]))
        r=meta.iloc[0];paths=json.loads(r.analysis_companions_json)
        self.assertEqual(paths['old']['lower_bound_variable'],'old_lb');self.assertEqual(paths['new']['lower_bound_variable'],'new_lb')
        self.assertIn('analysis_companions_json',r.lower_bound_variable);self.assertEqual(len(json.loads(r.analysis_year_rules_json)),2)
    def test_export_numeric_categories_roundtrip_and_stata_labels(self):
        cb=pd.DataFrame([meta_row('a'),meta_row('b')]);vl=self.labels([('a',1,'observed','Observed'),('b',1,'source_symbol','Source symbol')])
        meta,labels,_=b.metadata_for_specs([group()],cb,vl)
        frame=h.prepare_export_frame(pd.DataFrame({'result':['observed','source_symbol',None]}),meta,labels)
        with tempfile.TemporaryDirectory(prefix='fsa-analysis-unit-') as td:
            td=Path(td);h.write_labeled_parquet(frame,td/'fixture.parquet',meta,labels,{'title':'test'});h.verify_frame(frame,pd.read_parquet(td/'fixture.parquet'),meta,'parquet')
            self.assertIn(b'fsa_dataset',pq.read_schema(td/'fixture.parquet').metadata)
            h.write_csv(frame,td/'fixture.csv.gz');h.verify_frame(frame,h.read_csv(td/'fixture.csv.gz',meta),meta,'csv')
            h.write_stata(frame,td/'fixture.dta',meta,labels);self.assertTrue(h.verify_stata(td/'fixture.dta',frame,meta,labels)['value_labels_passed'])


@unittest.skipUnless(os.environ.get('FSA_MASTER_ROOT'),
                     'Set FSA_MASTER_ROOT to a frozen combined bundle for source integration tests')
class ActualPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=json.loads((Path(__file__).resolve().parents[1] / 'Decisions/consolidation_plan.json').read_text())
        cls.cb=b.read_csv(ROOT/'Inputs/fsa/codebook.csv');cls.vl=b.read_csv(ROOT/'Inputs/fsa/value_labels.csv')
        cls.specs=b.make_specs(cls.cb,True,cls.plan,cls.cb)
        cls.meta,cls.labels,cls.mapping=b.metadata_for_specs(cls.specs,cls.cb,cls.vl)
    def test_106_distinct_measures_and_native_recipient_populations_remain(self):
        self.assertEqual(int(self.meta.role.eq('measure').sum()),106)
        for col in ['loan_direct__subsidized_recipients','loan_direct__unsubsidized_recipients']:
            self.assertIn(col,self.mapping)
    def test_every_selected_measure_has_present_status(self):
        for r in self.meta.loc[self.meta.role.eq('measure')].itertuples():self.assertIn(r.status_variable,set(self.meta.canonical_name),r.canonical_name)
    def test_status_and_reference_links_do_not_dangle(self):
        for ref in ['status_variable','reference_year_variable']:
            missing=self.meta.loc[self.meta[ref].ne('')&~self.meta[ref].isin(self.meta.canonical_name),['canonical_name',ref]]
            self.assertTrue(missing.empty,missing.to_dict('records'))
    def test_category_codes_are_reversible(self):
        self.assertFalse(self.labels.duplicated(['canonical_name','code']).any());self.assertFalse(self.labels.duplicated(['canonical_name','value']).any())
    def test_combined_status_links_resolve_original_portable_names(self):
        cb=b.read_csv(ROOT/'Metadata/codebook.csv');vl=b.read_csv(ROOT/'Metadata/value_labels.csv')
        specs=b.make_specs(cb,False,self.plan,self.cb);meta,_,_=b.metadata_for_specs(specs,cb,vl)
        for ref in ['status_variable','reference_year_variable']:
            missing=meta.loc[meta[ref].ne('')&~meta[ref].isin(meta.canonical_name),['canonical_name',ref]]
            self.assertTrue(missing.empty,missing.to_dict('records'))
    def test_combined_companions_use_master_canonical_names(self):
        cb=b.read_csv(ROOT/'Metadata/codebook.csv');vl=b.read_csv(ROOT/'Metadata/value_labels.csv')
        specs=b.make_specs(cb,False,self.plan,self.cb);meta,_,_=b.metadata_for_specs(specs,cb,vl)
        available=set(cb.canonical_name)
        for r in meta.itertuples():
            for source, refs in json.loads(r.analysis_companions_json).items():
                for kind, ref in refs.items():
                    if ref:self.assertIn(ref,available,(r.canonical_name,source,kind,ref))
    def test_full_source_parent_plus_partition_and_status(self):
        part=[s for s in self.specs if s['method']=='year_partition'];cols=list(dict.fromkeys(['award_year_start']+[c for s in part for c in s['sources']]))
        source=pq.read_table(ROOT/'Inputs/fsa_panel.parquet',columns=cols).to_pandas()
        self.assertEqual(len(part),20)
        for s in part:
            actual,_=b.project_spec(source,s,True)
            for r in s['year_rules']:
                take=source.award_year_start.between(r['min'],r['max']).fillna(False)
                pd.testing.assert_series_equal(actual.loc[take],source.loc[take,r['source']],check_names=False)


if __name__=='__main__':unittest.main(verbosity=2)
