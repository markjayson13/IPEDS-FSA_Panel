import tempfile
import unittest
from pathlib import Path
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Scripts'))
from fsa_ipeds_merge_diagnostics import build_merge_diagnostics


class DiagnosticClassificationTests(unittest.TestCase):
    def frames(self):
        tokens = ['001234A1', '-2', None, 'junk', '00111100', '00222200',
                  '00333300', '00444400', '00555500', '00666600', '00777700', '00888800']
        ipeds = pd.DataFrame({'UNITID': range(100,112), 'year': 2023,
                              'OPEID': pd.Series(tokens,dtype='string')})
        fsa = pd.DataFrame({'unitid':[111,199], 'award_year_start':[2023,2023],
            'award_year_end':[2024,2024], 'award_year':['2023-2024']*2,
            'included_source_family_count':[1,1], 'blocked_source_family_count':[0,0],
            'grant__opeid8':['00888800','00666600'],
            'grant__unitid_record_status':['included_unique_family_record']*2})
        bridge=pd.DataFrame({'opeid8': ['00222200','00333300','00444400','00555500','00666600','00777700','00888800'],
            'award_year':['2023-2024']*7,'unitid':[None,None,None,None,199,110,111],
            'ipeds_annual_identity_eligible':[False,False,False,False,True,True,True],
            'ipeds_resolution_status':['multiple_official_site_unitids','no_annual_site_match',
                'directory_crosswalk_identity_conflict','crosswalk_site_parse_incomplete',
                'official_site_and_directory_agree','official_site_and_directory_agree','official_site_and_directory_agree']})
        return ipeds,fsa,bridge

    def test_distinguish_sources_and_resolution_without_reassigning(self):
        ipeds,fsa,bridge=self.frames()
        originals=[x.copy(deep=True) for x in (ipeds,fsa,bridge)]
        with tempfile.TemporaryDirectory() as folder:
            report=build_merge_diagnostics(ipeds,fsa,bridge,folder)
            rows=pd.read_csv(Path(folder)/'ipeds_only_evidence.csv',keep_default_na=False).set_index('UNITID')
            expected={100:'ipeds_alphanumeric_branch_not_exact_fsa_id',
                101:'not_applicable_ipeds_opeid_no_exact_lookup',102:'missing_ipeds_opeid_no_exact_lookup',
                103:'unrecognized_ipeds_opeid_no_exact_lookup',104:'no_exact_opeid_award_year_in_fsa_master_bridge',
                105:'fsa_master_identity_ambiguous',106:'fsa_master_no_annual_site_match',
                107:'fsa_master_identity_evidence_conflict',108:'fsa_master_identity_evidence_incomplete',
                109:'fsa_master_assigned_another_unitid',110:'fsa_master_same_unitid_without_unitid_panel_row'}
            self.assertEqual(rows.evidence_status.to_dict(),expected)
            self.assertEqual(rows.loc[100,'ipeds_opeid_raw'],'001234A1')
            self.assertEqual(rows.loc[100,'ipeds_opeid8'],'')
            self.assertTrue(rows.loc[100,'ipeds_opeid_valid'])
            self.assertFalse(rows.loc[100,'ipeds_opeid_exact_lookup_eligible'])
            self.assertEqual(report['matched_rows'],1)
            self.assertEqual(report['fsa_only_rows'],1)
            coverage=pd.read_csv(Path(folder)/'row_coverage.csv')
            self.assertEqual(coverage.loc[coverage.merge_status.eq('matched'),'UNITID'].tolist(),[111])
            self.assertEqual(sum(report['ipeds_only_evidence_counts'].values()),11)
        for actual,original in zip((ipeds,fsa,bridge),originals): pd.testing.assert_frame_equal(actual,original)

    def test_prior_award_year_mapping_is_explicit(self):
        ipeds,fsa,bridge=self.frames(); ipeds['year']=2024
        with tempfile.TemporaryDirectory() as folder:
            report=build_merge_diagnostics(ipeds,fsa,bridge,folder,offset=-1)
            coverage=pd.read_csv(Path(folder)/'row_coverage.csv')
            self.assertEqual(report['matched_rows'],1)
            self.assertTrue(coverage.fsa_award_year_start.eq(2023).all())

    def test_duplicate_ipeds_key_fails(self):
        ipeds,fsa,bridge=self.frames(); ipeds=pd.concat([ipeds,ipeds.iloc[[0]]])
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError,'not unique at UNITID/year'):
            build_merge_diagnostics(ipeds,fsa,bridge,folder)

    def test_duplicate_fsa_key_fails(self):
        ipeds,fsa,bridge=self.frames(); fsa=pd.concat([fsa,fsa.iloc[[0]]])
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError,'not unique at unitid/award_year_start'):
            build_merge_diagnostics(ipeds,fsa,bridge,folder)

    def test_duplicate_bridge_key_fails(self):
        ipeds,fsa,bridge=self.frames(); bridge=pd.concat([bridge,bridge.iloc[[0]]])
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError,'bridge keys are not unique'):
            build_merge_diagnostics(ipeds,fsa,bridge,folder)

    def test_numeric_ipeds_storage_fails_instead_of_losing_original_representation(self):
        ipeds,fsa,bridge=self.frames(); ipeds['OPEID']=123400
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError,'must be read as string'):
            build_merge_diagnostics(ipeds,fsa,bridge,folder)

    def test_whitespace_is_reviewed_not_silently_trimmed(self):
        ipeds,fsa,bridge=self.frames(); ipeds.loc[0,'OPEID']=' 00123400'
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError,'Unexpected whitespace'):
            build_merge_diagnostics(ipeds,fsa,bridge,folder)

    def test_unlisted_resolution_and_legacy_bridge_keep_explicit_fallback(self):
        ipeds,fsa,bridge=self.frames(); bridge=bridge.drop(columns='ipeds_resolution_status')
        with tempfile.TemporaryDirectory() as folder:
            report=build_merge_diagnostics(ipeds,fsa,bridge,folder)
            self.assertEqual(report['ipeds_only_evidence_counts']['fsa_master_identity_unresolved_or_ineligible'],4)

if __name__=='__main__': unittest.main()
