"""Conservative annual IPEDS missing-code policy, with exact lineage evidence.

Values are recoded only where the same collection year, table, source family,
source variable number, and variable name all agree in lineage, dictionary,
and code labels. No generic negative-value rule is permitted. Policy application
must precede consolidation so each split variable retains its own source mapping.
"""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

POLICY_VERSION = 'ipeds-explicit-missing-v1'
STATUS_PREFIXES = ('PRCH', 'STAT_', 'LOCK_', 'REV_', 'PTA', 'PTB_', 'PTC_', 'PTD_', 'PTE')


def exact_source_key(record):
    return (int(record['year']), str(record.get('source_file', '')).upper(),
            str(record.get('access_table_name', '')).upper(),
            str(record.get('varname', '')).upper(),
            str(record.get('source_varnumber') or record.get('varnumber') or ''))


def missing_reason(varname, code, label, source_file=''):
    """Explicit missingness only; substantive negative/No/status codes retained."""
    name = varname.split('__', 1)[0].upper()
    text = re.sub(r'\s+', ' ', str(label).strip().strip('{}').strip()).lower()
    if name.startswith(STATUS_PREFIXES):
        return None
    # A valid imputed endowment-ownership response, not absent financial values.
    if name == 'F1FHA' and str(code) == '0':
        return None
    # Positive DFRCGID exception groups are classifications, not numeric measures.
    if name == 'DFRCGID' and Decimal(str(code)) >= 0:
        return None
    if text == 'not reported' or text.startswith('not reported -'):
        return 'not_reported'
    if text == 'not applicable' or text.startswith(('not applicable,', 'not applicable -', 'not applicable  -')) or text == 'item not applicable':
        return 'not_applicable'
    if text in {'not available', 'item not available'}:
        return 'not_available'
    if text.startswith(('not derived due to', 'not derived -')):
        return 'not_derived'
    if name == 'LOCALE' and text == 'not assigned':
        return 'not_assigned'
    if name == 'SECTOR' and text == 'sector unknown (not active)':
        return 'unknown'
    # This negative DFRCGID code means no comparison group was assigned.
    if name == 'DFRCGID' and str(code) == '-1' and text in {
        'non-title iv institution, that did not respond to all component surveys',
        'non-title iv, institution that did not respond to all component surveys',
    }:
        return 'not_reported'
    return None


def build_policy(metadata, *, metadata_sha256=None):
    rules, exclusions, rejected = [], [], []
    for variable in metadata['variables']:
        name = variable['name']
        lineage = {exact_source_key(r) for r in variable.get('lineage_records', [])}
        dictionary = {exact_source_key(r) for r in variable.get('source_metadata', [])}
        grouped = defaultdict(list)
        for r in variable.get('value_label_records', []):
            grouped[(exact_source_key(r), str(r['codevalue']))].append(r)
        for (key, code), records in sorted(grouped.items()):
            reasons = {missing_reason(name, code, r['valuelabel'], r.get('source_file','')) for r in records}
            labels = sorted({r['valuelabel'] for r in records})
            candidate = any(x is not None for x in reasons)
            record = {'column': 'ipeds__' + name, 'analysis_column': name,
                      'year': key[0], 'source_file': key[1], 'access_table_name': key[2],
                      'varname': key[3], 'source_varnumber': key[4], 'code': code,
                      'labels': labels, 'storage_type': variable['storage_type']}
            if not candidate:
                if code.startswith('-') or any(x.lower().startswith(('not ', '{not ', '{item')) for x in labels):
                    exclusions.append({**record, 'reason': 'retain_substantive_or_reporting_status_category'})
                continue
            if key not in lineage or key not in dictionary:
                rejected.append({**record, 'reason': 'no_exact_lineage_and_dictionary_key'})
                continue
            if len(reasons) != 1:
                rejected.append({**record, 'reason': 'contradictory_labels_for_exact_source_key'})
                continue
            # Matching keys guarantee these label records describe this cell source.
            rules.append({**record, 'reason': next(iter(reasons)), 'action': 'set_null',
                          'evidence': 'Exact annual source key matches lineage_records, source_metadata, and value_label_records',
                          'metadata_tables': sorted({r.get('metadata_table_name','') for r in records}),
                          'is_imputation_label': any(r.get('is_imputation_label', False) for r in records)})
    if any(r['is_imputation_label'] for r in rules):
        raise ValueError('Do not apply imputation flag labels to substantive measures')
    return {'policy_version': POLICY_VERSION, 'metadata_sha256': metadata_sha256,
            'principles': [
                'Apply rules by IPEDS collection year, before combining split columns.',
                'Match year, source family, Access table, variable name, and variable number exactly.',
                'Only explicit source-coded missingness becomes null; do not infer missingness from sign.',
                'Preserve negative finances, geographic coordinates, zero/No responses, unknown-response categories, and unclassified categories.',
                'Preserve PRCH/PRCHTP not-applicable categories and all survey completion/revision/lock/status codes.',
                'Original panels remain the lossless source; retain cell-level missingness reason and source code in a companion audit.',
                'Do not turn FSA unmatched years or suppressed cells into zero.',
            ], 'rules': rules, 'explicit_exclusions': exclusions, 'rejected_rules': rejected,
            'summary': {'rules': len(rules), 'columns': len({r['column'] for r in rules}),
                        'column_years': len({(r['column'], r['year']) for r in rules}),
                        'rules_by_reason': dict(Counter(r['reason'] for r in rules)),
                        'rejected_rules': len(rejected), 'explicit_exclusions': len(exclusions)}}


def mask_for_code(series, code):
    """Numeric compare for numeric storage; narrowly support Access '1.0' strings.

    This does not convert identifiers or the series in place. It only selects
    exact numeric spelling variants of an explicitly labeled missing code.
    """
    import pandas as pd
    from pandas.api.types import is_numeric_dtype
    if is_numeric_dtype(series.dtype):
        try:
            return series.eq(float(Decimal(code))).fillna(False)
        except InvalidOperation:
            return pd.Series(False, index=series.index)
    text = series.astype('string').str.strip()
    try:
        decimal = Decimal(code)
        if decimal.is_finite() and decimal == decimal.to_integral_value():
            # No fuzzy conversion of literal codes, leading zeros or CIP fields.
            pattern = re.escape(str(int(decimal))) + r'(?:\.0+)?'
            return text.str.fullmatch(pattern).fillna(False)
    except InvalidOperation:
        pass
    return text.eq(code).fillna(False)


def apply_missing_policy(frame, policy, *, year_column='year', column_prefix='ipeds__',
                         in_place=False, collect_cells=True):
    """Return (clean_frame, cell_audit, rule_counts); subset columns are supported.

    cell_audit keeps unitid/year, original source column/code, and reason. Empty
    missing source cells stay empty; only explicit observed sentinels are changed.
    """
    import pandas as pd
    out = frame if in_place else frame.copy()
    audits, counts = [], []
    if year_column not in out:
        raise ValueError(f'Required collection-year column missing: {year_column}')
    year_indices = {int(year): indices for year, indices in out.groupby(year_column, sort=False).groups.items()}
    for rule in policy['rules']:
        if rule['year'] not in year_indices:
            continue
        col = column_prefix + rule['analysis_column']
        if col not in out:
            continue
        annual = out.loc[year_indices[rule['year']], col]
        local_mask = mask_for_code(annual, rule['code'])
        selected = annual.index[local_mask]
        count = len(selected)
        if not count:
            continue
        counts.append({'column': col, 'year': rule['year'], 'code': rule['code'],
                       'reason': rule['reason'], 'cells': count})
        if collect_cells:
            keys = [k for k in ('unitid', year_column) if k in out]
            rows = out.loc[selected, keys].copy()
            rows['source_column'] = col
            rows['source_code'] = out.loc[selected, col].astype('string')
            rows['missing_reason'] = rule['reason']
            rows['source_table'] = rule['access_table_name']
            audits.append(rows)
        out.loc[selected, col] = None
    audit = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame()
    return out, audit, counts


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--panel')
    args = parser.parse_args()
    raw = Path(args.metadata).read_bytes()
    policy = build_policy(json.loads(raw), metadata_sha256=hashlib.sha256(raw).hexdigest())
    if args.panel:
        import pyarrow.parquet as pq
        cols = ['unitid', 'year'] + sorted({r['column'] for r in policy['rules']})
        pf = pq.ParquetFile(args.panel)
        cols = [c for c in cols if c in pf.schema_arrow.names]
        counts = Counter()
        for batch in pf.iter_batches(batch_size=8192, columns=cols):
            frame = batch.to_pandas()
            _, _, changes = apply_missing_policy(frame, policy, in_place=True, collect_cells=False)
            for r in changes:
                counts[(r['column'], r['year'], r['code'], r['reason'])] += r['cells']
        policy['observed_counts'] = [dict(zip(('column', 'year', 'code', 'reason'), key), cells=value) for key,value in sorted(counts.items())]
        policy['summary']['observed_cells'] = sum(counts.values())
        policy['summary']['observed_columns'] = len({k[0] for k in counts})
        policy['summary']['observed_cells_by_reason'] = dict(sum((Counter({k[3]: v}) for k,v in counts.items()), Counter()))
    Path(args.output).write_text(json.dumps(policy, indent=2) + '\n')
    print(json.dumps(policy['summary'], indent=2))


if __name__ == '__main__':
    main()
