"""Independent summary of the completed, hash-verified local release."""
from pathlib import Path
import hashlib
import json
import sys
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'Scripts'))
from fsa_release_integrity import require_current_qa, require_current_linkage, acceptance_all_passed
root = REPO / 'ResearchBuild/2026-09-22-v2'
manifest_path = root / 'build/research_release_manifest.json'
manifest = json.loads(manifest_path.read_text())
master = Path(manifest['master'])
if not master.is_absolute(): master = REPO / master
require_current_qa(root)
linkage = require_current_linkage(root, master)
for item in manifest['code_and_metadata']:
    snapshot = root / 'build/source_snapshot' / item['path']
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == item['sha256']
    assert hashlib.sha256((REPO / item['path']).read_bytes()).hexdigest() == item['sha256']
assert manifest['acceptance_passed'] and linkage['all_original_cells_preserved']
b = pd.read_parquet(root / 'Panels/ipeds/fsa_ipeds_bridge.parquet')
u = pd.read_parquet(root / 'Panels/ipeds/unitid_research/fsa_unitid_award_year_panel.parquet',
                    columns=['unitid', 'award_year', 'included_source_family_count', 'blocked_source_family_count'])
assert not b.duplicated(['opeid8', 'award_year']).any()
assert not u.duplicated(['unitid', 'award_year']).any()
checks = pd.read_csv(root / 'Panels/ipeds/unitid_research/conservation.csv')
assert acceptance_all_passed(checks)
raw = pd.read_csv(root / 'Checks/research_qc/raw_to_final_conservation.csv')
assert acceptance_all_passed(raw)
residual_path = REPO / 'Analysis/research_limits_2026-09-22/ipeds_linkage/residual_universe_manifest.json'
residual = json.loads(residual_path.read_text())
for key, path in residual['input_paths'].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == residual['input_sha256'][key]
for name, digest in residual['outputs'].items():
    assert hashlib.sha256((residual_path.parent / name).read_bytes()).hexdigest() == digest
statistics = {
    'master_rows': manifest['rows'], 'master_columns': manifest['columns'],
    'fsa_reporting_ids_including_one_placeholder': manifest['fsa_reporting_unit_ids'],
    'annual_identity_eligible_fsa_rows': int(b.ipeds_annual_identity_eligible.sum()),
    'assigned_administrative_rows_excluded_from_institution_view': int(b.ipeds_identity_is_administrative.sum()),
    'unitid_year_rows': len(u), 'unique_unitids': int(u.unitid.nunique()),
    'unitid_year_rows_with_any_usable_family': int(u.included_source_family_count.gt(0).sum()),
    'unitid_year_rows_only_blocked_families': int(u.included_source_family_count.eq(0).sum()),
    'unitid_year_rows_any_blocked_family': int(u.blocked_source_family_count.gt(0).sum()),
    'strict_sensitivity_rows': linkage['strict_rows'],
    'descriptor_review_keys': manifest['descriptor_review_rows'],
    'acceptance_checks': manifest['acceptance_checks'],
    'source_measure_cells_conserved': int(raw.source_cells.sum()),
    'unitid_conservation_checks': len(checks),
    'resolution_status_counts': b.ipeds_resolution_status.value_counts().to_dict(),
    'identity_ineligible_by_geography': b.loc[~b.ipeds_annual_identity_eligible, 'ipeds_geography_class'].value_counts().to_dict(),
    'master_sha256': hashlib.sha256(master.read_bytes()).hexdigest(),
    'release_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    'unitid_panel_sha256': linkage['unitid_panel']['output_sha256']['panel'],
    'bridge_sha256': hashlib.sha256((root / 'Panels/ipeds/fsa_ipeds_bridge.parquet').read_bytes()).hexdigest(),
    'all_code_snapshot_source_and_artifact_checks_passed': True,
}
(root / 'build/verified_release_statistics.json').write_text(json.dumps(statistics, indent=2) + '\n')
print(json.dumps(statistics, indent=2))
