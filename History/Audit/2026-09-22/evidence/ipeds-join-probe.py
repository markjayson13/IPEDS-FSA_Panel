from pathlib import Path
import pandas as pd
fsa=Path('/Users/markjaysonfarol13/Projects/FSAVolumeReports_Paneling')
p=pd.read_parquet('/Users/markjaysonfarol13/Projects/IPEDSDB_Paneling/Panels/panel_clean_analysis_2004_2023.parquet',columns=['year','UNITID','INSTNM','OPEID'])
p['opeid8']=p.OPEID.where(p.OPEID.gt(0)).round().astype('Int64').astype('string').str.zfill(8)
p['opeid6']=p.opeid8.str[:6]
q=p.loc[p.opeid8.notna()].copy()
for k in ['opeid8','opeid6']:
 n=q.groupby([k,'year']).UNITID.nunique()
 print(k,'keys with multiple UNITID',n.gt(1).sum(),'maximum',n.max(),'units in duplicated key rows',q.duplicated([k,'year'],keep=False).sum())
 print(q.merge(n.loc[n.gt(1)].rename('unitid_count'),on=[k,'year']).sort_values([k,'year','UNITID']).head(18).to_string(index=False))
n=q.groupby('opeid8').UNITID.nunique()
print('all time distinct UNITID perOPEID8 multiples',n.gt(1).sum(), 'max',n.max())
c=q.groupby('UNITID').opeid8.nunique()
print('UNITID multiple OPEID8 across time',c.gt(1).sum(),'max',c.max())
panel=pd.read_parquet(fsa/'Panels/final/fsa_volume_reports_clean_1999_2025.parquet',columns=['opeid8','opeid6','award_year_start','award_year_end','award_year'])
a=pd.read_csv('/tmp/fsa_identity_audit/source_id_audit.csv',dtype=str)
a['full_opeid8']=a.raw_opeid.str.strip().str.zfill(8)
a['year']=a.award_year.str[:4].astype(int)
for label,data in [('current',panel.rename(columns={'award_year_start':'year'})),('full8_probe',a[['full_opeid8','year']].drop_duplicates().rename(columns={'full_opeid8':'opeid8'}))]:
 data=data.loc[data.year.between(2004,2023)]
 j=data.merge(q[['opeid8','year','UNITID']],on=['opeid8','year'],how='left')
 n=j.groupby(['opeid8','year']).UNITID.count()
 print(label,'originalkeys',len(data),'unmatched',n.eq(0).sum(),'uniquelymatched',n.eq(1).sum(),'ambiguous',n.gt(1).sum(),'mergeoutputrows',len(j))
 print('ambiguoussample',j.loc[j.duplicated(['opeid8','year'],keep=False)].head(8).to_string(index=False))
