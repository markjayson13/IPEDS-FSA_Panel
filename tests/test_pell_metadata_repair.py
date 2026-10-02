"""Regression checks for consuming documented upstream metadata corrections."""
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Scripts'))
from fsa_ipeds_pell_validation import (
    SOURCE_PROVENANCE_COLUMNS, _source_audit_narrative,
    build_pell_validation, source_metadata_audit,
)


def dictionary(corrected=True):
    records = []
    for name, number, title in (
        ('UPGRNTN', '70306', 'Number of undergraduate students awarded Federal Pell grants'),
        ('UPGRNTT', '70421', 'Total amount of Federal Pell grant aid awarded to undergraduate students'),
    ):
        row = dict(year=2023, varname=name, varnumber=number,
                   variable_id=f'nces:SFA_P:{number}:{name}', source_file='SFA_P',
                   access_table_name='SFA2223_P1' if corrected else 'SFA2223_P2',
                   source_file_label='SFA2223_P1' if corrected else 'SFA2223_P2',
                   varTitle=title, longDescription=title)
        if corrected:
            row.update(
                original_access_table_name='SFA2223_P2',
                original_source_file_label='SFA2223_P2', original_metadata_json='{"table":"P2"}',
                resolved_physical_table='SFA2223_P1',
                metadata_correction_id=f'2023-sfa-v1:{number}:{name}',
                metadata_correction_reason='Verified physical column in pinned release.',
                metadata_correction_evidence='contracts/source_metadata_corrections/evidence.json',
                metadata_correction_registry_sha256='a' * 64, source_archive_sha256='b' * 64,
                source_database_sha256='c' * 64, source_physical_table_sha256='d' * 64,
                source_table_reference_period='July 1, 2022 - June 30, 2023',
                reference_period='preceding academic year (July 1 to June 30)',
                reference_period_start='2022-07-01', reference_period_end='2023-06-30',
                imputationvar='X' + name,
                imputation_flag_availability='not included in this Access release; dictionary association only',
            )
        records.append(row)
    return pd.DataFrame(records)


def lineage():
    return pd.DataFrame([
        dict(year=2023, analysis_column=row.varname, variable_id=row.variable_id,
             source_file='SFA_P', access_table_name='sfa2223_p1',
             transformation_id='identity', lineage_role='direct')
        for row in dictionary().itertuples(index=False)
    ])


def observations():
    ipeds = pd.DataFrame([dict(UNITID=123456, year=2023, OPEID='00123400',
                              UPGRNTN=10, UPGRNTT=100, PRCH_SFA='-2',
                              IDX_SFA=None, RPTMTH='1', IMP_SFA='0')])
    fsa = pd.DataFrame([
        {'unitid': 123456, 'award_year_start': year, 'grant__opeid8': '00123400',
         'grant__unitid_record_status': 'included_unique_family_record',
         'grant__pell_recipients': 12, 'grant__pell_disbursements': 110,
         'grant__pell_recipients__status': 'observed',
         'grant__pell_disbursements__status': 'observed',
         'grant__ipeds_student_aid_scope': 'no_parent_child_relation_reported'}
        for year in (2021, 2022, 2023, 2024)
    ])
    return ipeds, fsa


class PellMetadataRepairTests(unittest.TestCase):
    def test_corrected_metadata_resolves_but_preserves_original_evidence(self):
        source = dictionary()
        audit = source_metadata_audit(source, lineage())
        self.assertTrue(audit.source_metadata_verified.all())
        self.assertEqual(set(audit.source_metadata_status), {'verified_annual_source_identity'})
        for column in SOURCE_PROVENANCE_COLUMNS:
            self.assertEqual(audit[column].tolist(), source[column].tolist(), column)

    def test_original_conflict_stays_unverified(self):
        audit = source_metadata_audit(dictionary(False), lineage())
        self.assertFalse(audit.source_metadata_verified.any())
        self.assertEqual(set(audit.source_metadata_status), {'source_table_conflict'})
        self.assertTrue(audit.metadata_correction_id.eq('').all())
        text = _source_audit_narrative(audit)
        self.assertIn('source_table_conflict: 2', text)
        self.assertNotIn('Documented upstream corrections', text)

    def test_correction_record_cannot_override_unknown_or_conflicting_lineage(self):
        for actual in (None, lineage().assign(access_table_name='sfa2223_p2')):
            audit = source_metadata_audit(dictionary(), actual)
            self.assertFalse(audit.source_metadata_verified.any())
            self.assertTrue(audit.metadata_correction_id.ne('').all())

    def test_narrative_uses_actual_years_and_statuses(self):
        source = dictionary(False).assign(year=2030)
        actual = lineage().assign(year=2030)
        text = _source_audit_narrative(source_metadata_audit(source, actual))
        self.assertIn('source_table_conflict: 2', text)
        self.assertNotIn('2023', text)
        verified = _source_audit_narrative(source_metadata_audit(dictionary(), lineage()))
        self.assertIn('verified_annual_source_identity: 2', verified)
        self.assertIn('SFA2223_P2 to SFA2223_P1', verified)
        self.assertIn('dictionary association only', verified)
        self.assertNotIn('identity remains unresolved', verified)

    def test_full_diagnostics_keep_observations_and_absent_flags_distinct(self):
        ipeds, fsa = observations()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = build_pell_validation(ipeds, fsa, dictionary(False), pd.DataFrame(),
                                        root / 'before', lineage=lineage())
            new = build_pell_validation(ipeds, fsa, dictionary(), pd.DataFrame(),
                                        root / 'after', lineage=lineage())
            a = pd.read_csv(old['files']['pell_row_comparisons_all_offsets'])
            b = pd.read_csv(new['files']['pell_row_comparisons_all_offsets'])
            expected_changes = {
                column for column in a if any(token in column for token in (
                    'source_metadata', 'dictionary_access_table_name',
                    'scope_screen_valid_pair', 'scope_common_all_offsets'))
            }
            pd.testing.assert_frame_equal(a.drop(columns=expected_changes),
                                          b.drop(columns=expected_changes))
            self.assertEqual(old['common_sample_rows'], new['common_sample_rows'])
            self.assertEqual(new['common_sample_rows'], {'recipients': 1, 'dollars': 1})
            self.assertFalse(new['cell_imputation_flags_available'])
            self.assertFalse(b.ipeds_pell_cell_imputation_flags_available.any())
            self.assertEqual(len(new['documented_item_imputation_flag_availability']), 2)
            self.assertEqual(len(new['annual_source_metadata_corrections']), 2)
            self.assertTrue(b.recipients_scope_screen_valid_pair.all())
            self.assertFalse(a.recipients_scope_screen_valid_pair.any())
            readme = Path(new['files']['README']).read_text()
            self.assertIn('verified_annual_source_identity: 2', readme)
            self.assertIn('Missing item-level flags do not establish that every value was reported', readme)
            self.assertNotIn('{current_source_audit}', readme)


if __name__ == '__main__':
    unittest.main()
