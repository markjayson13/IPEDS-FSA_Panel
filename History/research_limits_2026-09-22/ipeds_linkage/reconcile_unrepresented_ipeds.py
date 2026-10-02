"""Explain IPEDS universe residuals with possible grouping evidence, not assignments.

Read-only with respect to the research pipeline. Produces supplementary annual
counts and one row per residual UNITID/year under this Analysis directory.
"""
from collections import defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'Scripts'))
from fsa_ipeds_linkage import load_ipeds_directory, opeid_scope_root


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(values):
    return json.dumps(sorted(set(values)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=REPO / 'ResearchBuild/2026-09-22-v2')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    paths = {
        'bridge': args.build/'Panels/ipeds/fsa_ipeds_bridge.parquet',
        'direct_membership': args.build/'Panels/ipeds/fsa_ipeds_direct_site_memberships.parquet',
        'root_candidates': args.build/'Panels/ipeds/fsa_ipeds_candidates.parquet',
        'crosswalk_records': REPO/'ResearchBuild/IPEDS/official_crosswalks/normalized/records.parquet',
        'crosswalk_relations': REPO/'ResearchBuild/IPEDS/official_crosswalks/normalized/relations.parquet',
    }
    source_hashes = {k: _sha(p) for k, p in paths.items()}
    bridge = pd.read_parquet(paths['bridge'])
    members = pd.read_parquet(paths['direct_membership'])
    roots = pd.read_parquet(paths['root_candidates'], columns=['fsa_opeid8','award_year','ipeds_year','unitid','candidate_relation'])
    cw = pd.read_parquet(paths['crosswalk_records'], columns=['cw_year','opeid8','cw_parent_opeid8','cw_source_code','cw_site_parse_complete','cw_preliminary'])
    rel = pd.read_parquet(paths['crosswalk_relations'], columns=['cw_year','opeid8','unitid','relation_type'])
    years = sorted(bridge.ipeds_year.astype(int).unique())
    hd, hd_manifest, _ = load_ipeds_directory(REPO/'ResearchBuild/IPEDS/official_hd_v2', years, include_unlinked=True)
    annual, residual_records = [], []
    for ay, observed in bridge.groupby('award_year', observed=True, sort=True):
        year = int(observed.ipeds_year.iloc[0])
        directory = hd[hd.ipeds_year.eq(year)]
        pset = pd.to_numeric(directory.pset4flg, errors='coerce')
        available_universe = bool(pset.notna().any())
        universe_frame = directory[pset.isin([1,3] if year >= 2010 else [1])]
        universe = set(universe_frame.unitid.astype(int))
        valid_fsa = set(observed.opeid8.astype(str))
        # Exclude disproved or noninstitutional reporting rows from evidence of
        # representation. Legacy interim bridge is supported for audit iteration.
        excluded = set(observed.loc[observed.opeid8.eq('88888800'),'opeid8'].astype(str))
        if 'ipeds_identity_geography_conflict' in observed:
            excluded |= set(observed.loc[observed.ipeds_identity_geography_conflict,'opeid8'].astype(str))
        else:
            excluded |= set(observed.loc[observed.ipeds_geography_class.eq('foreign_explicit') & observed.ipeds_stabbr.isin(list('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC AS GU MP PR VI FM MH PW'.split())),'opeid8'].astype(str))
        valid_fsa -= excluded
        selected_members = members[members.award_year.eq(ay) & members.opeid8.isin(valid_fsa)]
        direct = set(selected_members.unitid.dropna().astype(int))
        resolved = set(observed.loc[observed.ipeds_annual_identity_eligible & observed.opeid8.isin(valid_fsa),'unitid'].dropna().astype(int))
        residual = universe-direct
        root_links = defaultdict(set)
        rr = roots[roots.award_year.eq(ay) & roots.ipeds_year.eq(year) & roots.fsa_opeid8.isin(valid_fsa)]
        for uid, op in zip(rr.unitid,rr.fsa_opeid8):
            if int(uid) in residual:root_links[int(uid)].add(str(op))
        cr = cw[cw.cw_year.eq(year)]
        cr_by_id = cr.set_index('opeid8').to_dict('index')
        observed_parent_members = defaultdict(set)
        for op in valid_fsa:
            p = cr_by_id.get(op,{}).get('cw_parent_opeid8')
            if isinstance(p,str) and p:observed_parent_members[p].add(op)
        site_opeids, parent_ref_links = defaultdict(set), defaultdict(set)
        yr = rel[rel.cw_year.eq(year) & rel.unitid.notna()]
        for r in yr.itertuples():
            uid = int(r.unitid)
            if uid not in residual:continue
            if r.relation_type in {'primary_match','additional_match'}:site_opeids[uid].add(str(r.opeid8))
            elif r.relation_type=='parent_reference' and r.opeid8 in valid_fsa:parent_ref_links[uid].add(str(r.opeid8))
        counters=defaultdict(int)
        for row in universe_frame[universe_frame.unitid.isin(residual)].to_dict('records'):
            uid=int(row['unitid']); parents=set(); codes=set(); incomplete=False
            for op in site_opeids[uid]:
                evidence=cr_by_id.get(op,{})
                p=evidence.get('cw_parent_opeid8')
                if isinstance(p,str) and p:parents.add(p)
                if evidence.get('cw_source_code') is not None:codes.add(str(evidence['cw_source_code']))
                incomplete |= not bool(evidence.get('cw_site_parse_complete',False))
            observed_mains=parents & valid_fsa
            shared=set().union(*(observed_parent_members[p] for p in parents)) if parents else set()
            official_links=observed_mains|shared|parent_ref_links[uid]
            root_evidence=root_links[uid]
            group=official_links|root_evidence
            category=('both_official_affiliation_and_diagnostic_root' if official_links and root_evidence else
                      'official_affiliation_only' if official_links else 'diagnostic_root_only' if root_evidence else 'no_group_evidence_in_reviewed_sources')
            counters[category]+=1
            for name,flag in [('possible_group_representation',bool(group)),('official_affiliation',bool(official_links)),
                ('observed_main_parent',bool(observed_mains)),('shared_parent_affiliation',bool(shared)),
                ('parent_reference',bool(parent_ref_links[uid])),('diagnostic_root',bool(root_evidence)),
                ('administrative',str(row.get('sector'))=='0'),('deferment_only',str(row.get('opeflag'))=='3')]:
                counters[name]+=int(flag)
            evidence_row={'award_year':ay,'ipeds_year':year,'unitid':uid,'instnm':row.get('instnm'),
                'opeid8':row.get('opeid8'),'stabbr':row.get('stabbr'),'city':row.get('city'),
                'sector':row.get('sector'),'opeflag':row.get('opeflag'),'act':row.get('act'),
                'cyactive':row.get('cyactive'),'pset4flg':row.get('pset4flg'),
                'prch_f':row.get('prch_f'),'idx_f':row.get('idx_f'),'prch_sfa':row.get('prch_sfa'),'idx_sfa':row.get('idx_sfa'),
                'possible_group_evidence_category':category,
                'diagnostic_root_fsa_opeids_json':_json(root_evidence),
                'official_observed_main_fsa_opeids_json':_json(observed_mains),
                'official_shared_parent_fsa_opeids_json':_json(shared),
                'official_parent_reference_fsa_opeids_json':_json(parent_ref_links[uid]),
                'all_possible_group_fsa_opeids_json':_json(group),
                'cw_site_opeids_for_residual_unitid_json':_json(site_opeids[uid]),
                'cw_site_parent_opeids_json':_json(parents),'cw_site_source_codes_json':_json(codes),
                'cw_site_evidence_has_incomplete_parse':incomplete,'cw_reference_year_available':not cr.empty,
                'cw_preliminary_year':bool(cr.cw_preliminary.any()) if len(cr) else False,
                'assignment_status':'no_direct_candidate; affiliation_and_root_are_review_only; no_assignment_or_amount_allocation'}
            residual_records.append(evidence_row)
        annual.append({'award_year':ay,'ipeds_year':year,'universe_available':available_universe,
            'ipeds_pset4flg_universe':len(universe) if available_universe else None,
            'unique_resolved_fsa_in_universe':len(universe&resolved) if available_universe else None,
            'any_direct_fsa_candidate_in_universe':len(universe&direct) if available_universe else None,
            'direct_candidate_without_unique_resolution':len((universe&direct)-resolved) if available_universe else None,
            'residual_without_direct_candidate':len(residual) if available_universe else None,
            'residual_with_possible_group_representation':counters['possible_group_representation'],
            'residual_with_official_affiliation':counters['official_affiliation'],
            'residual_with_observed_main_parent':counters['observed_main_parent'],
            'residual_with_shared_parent_affiliation':counters['shared_parent_affiliation'],
            'residual_as_official_parent_reference':counters['parent_reference'],
            'residual_with_diagnostic_root_evidence':counters['diagnostic_root'],
            'residual_both_official_affiliation_and_diagnostic_root':counters['both_official_affiliation_and_diagnostic_root'],
            'residual_official_affiliation_only':counters['official_affiliation_only'],
            'residual_diagnostic_root_only':counters['diagnostic_root_only'],
            'residual_without_reviewed_group_evidence':counters['no_group_evidence_in_reviewed_sources'],
            'residual_administrative_offices':counters['administrative'],
            'residual_deferment_only':counters['deferment_only'],
            'note':'Possible grouping is overlapping relationship evidence, not matched campus identity or aid allocation.'})
    result=pd.DataFrame(annual); detail=pd.DataFrame(residual_records)
    args.out.mkdir(parents=True,exist_ok=True)
    summary_path=args.out/'residual_universe_reconciliation.csv';detail_path=args.out/'residual_universe_records.parquet';detail_csv=args.out/'residual_universe_records.csv'
    result.to_csv(summary_path,index=False);detail.to_parquet(detail_path,index=False);detail.to_csv(detail_csv,index=False)
    assert {k:_sha(p) for k,p in paths.items()}==source_hashes,'Input changed during read-only audit; rerun'
    metadata={'created_utc':datetime.now(timezone.utc).isoformat(),'input_paths':{k:str(p) for k,p in paths.items()},'input_sha256':source_hashes,
        'code_sha256':_sha(Path(__file__)),'rows':len(detail),'award_years':len(result),'aid_columns_used':[],
        'outputs':{p.name:_sha(p) for p in [summary_path,detail_path,detail_csv]},
        'definitions':'Reference-year HD PSET4FLG universe1 before2010;1/3 from2010. Residual=no direct HD/CW site candidate after disproved/placeholder exclusion. Official affiliation or diagnostic root identifies only possible group representation. No allocation or identity assignment.',
        'hd_source_manifest':json.loads(hd_manifest.to_json(orient='records'))}
    (args.out/'residual_universe_manifest.json').write_text(json.dumps(metadata,indent=2,default=str,allow_nan=False)+'\n')
    print(result.to_string(index=False));print('Residual records',len(detail))


if __name__=='__main__':main()
