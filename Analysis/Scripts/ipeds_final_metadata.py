"""Enrich reviewed FSA--IPEDS analysis metadata without changing data or storage.

The full upstream DTA metadata is the authority for canonical names and annual
code meanings. Numeric Stata encodings of alphabetic categories are deliberately
not transplanted to the analysis view's original string identifiers/categories.
"""
from __future__ import annotations
import collections
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import pandas as pd

ADAPTER_VERSION = 'ipeds-final-labels-v1'
STATA_LABEL_SET_MAX_BYTES = 32000
INTEGER = re.compile(r'-?(?:0|[1-9][0-9]*)\Z')


def read_final_metadata(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def year_ranges(years):
    years = sorted(set(int(y) for y in years))
    result = []
    for y in years:
        if result and result[-1][1] + 1 == y:
            result[-1][1] = y
        else:
            result.append([y, y])
    return ','.join(str(a) if a == b else f'{a}-{b}' for a, b in result)


def canonical_sources(row):
    # The exact source crosswalk is required. Do not guess from short Stata names.
    raw = json.loads(row['analysis_sources_json'])
    if not raw or any(not s.startswith('ipeds__') for s in raw):
        raise ValueError(f"Not exact IPEDS source names for {row['portable_name']}: {raw}")
    return [s[len('ipeds__'):] for s in raw]


def scoped_integer_labels(variables):
    meanings = collections.defaultdict(set)
    for var in variables:
        conversion = var.get('stata_storage_conversion')
        if conversion == 'string_categories_to_numeric':
            raise ValueError(f"Refusing arbitrary upstream category codes for {var['name']}")
        for rec in var.get('resolved_value_label_records', []):
            token, label = str(rec.get('codevalue', '')), str(rec.get('valuelabel', '')).strip()
            if not label:
                continue
            if not INTEGER.fullmatch(token) or str(int(token)) != token:
                raise ValueError(f"Noncanonical integer code {token!r} for {var['name']}")
            year = int(rec['year'])
            meanings[(int(token), year)].add(label)
    conflicts = {f'{code}@{year}': sorted(labels) for (code, year), labels in meanings.items() if len(labels) > 1}
    if conflicts:
        raise ValueError(f'Conflicting annual source meanings: {conflicts}')
    by_code = collections.defaultdict(lambda: collections.defaultdict(set))
    for (code, year), labels in meanings.items():
        by_code[code][next(iter(labels))].add(year)
    result = {}
    for code, groups in sorted(by_code.items()):
        parts = sorted(groups.items(), key=lambda item: (min(item[1]), item[0]))
        label = '; '.join(f'{year_ranges(years)}: {text}' for text, years in parts)
        if len(label.encode('utf-8')) > 32000:
            raise ValueError(f'Label exceeds Stata string limit for code {code}')
        result[code] = label
    return result


def bound_native_value_labels(labels):
    """Keep exact full labels while bounding Stata's aggregate text buffer.

    Stata/pandas limit each variable's complete label set to 32,000 bytes,
    including a NUL terminator per label. Individual-label checks are insufficient.
    """
    labels = labels.copy(deep=True)
    # Always reconstruct from the newly derived complete meanings, not an earlier
    # native excerpt. For unchanged FSA rows preserve their original full label.
    if 'full_label' not in labels:
        labels['full_label'] = labels['label']
    else:
        absent = labels.full_label.isna() | labels.full_label.eq('')
        labels.loc[absent, 'full_label'] = labels.loc[absent, 'label']
    labels['native_label_rule'] = 'complete_label'
    excerpts = {}
    for name, rows in labels.groupby('portable_name', sort=False):
        full = rows.full_label.astype(str)
        original_bytes = sum(len(x.encode('utf-8')) + 1 for x in full)
        labels.loc[rows.index, 'label'] = full
        if original_bytes <= STATA_LABEL_SET_MAX_BYTES:
            continue
        budget = (STATA_LABEL_SET_MAX_BYTES - 1) // len(rows) - 1
        for idx, row in rows.iterrows():
            text = str(labels.at[idx, 'full_label'])
            if len(text.encode('utf-8')) <= budget:
                continue
            prefix = f"Code {int(row['code'])}; "
            suffix = '... [see full_label]'
            remaining = budget - len((prefix + suffix).encode('utf-8'))
            if remaining < 8:
                raise ValueError(f'Too many labels for a meaningful native excerpt: {name}')
            excerpt = text.encode('utf-8')[:remaining].decode('utf-8', errors='ignore').rstrip()
            labels.at[idx, 'label'] = prefix + excerpt + suffix
            labels.at[idx, 'native_label_rule'] = 'utf8_excerpt_full_meaning_in_full_label'
        native_bytes = sum(len(str(x).encode('utf-8')) + 1 for x in labels.loc[rows.index, 'label'])
        if native_bytes > STATA_LABEL_SET_MAX_BYTES:
            raise AssertionError(f'Native label set remains too long: {name}')
        excerpts[name] = {'entries':len(rows), 'full_text_utf8_bytes':original_bytes, 'native_text_utf8_bytes':native_bytes, 'per_entry_text_byte_budget':budget}
    return labels, excerpts


def enrich_analysis_metadata(codebook_df, labels_df, final_metadata_dict):
    """Return enriched codebook, labels and audit; never mutate inputs or data."""
    upstream = {v['name']: v for v in final_metadata_dict['variables']}
    if len(upstream) != len(final_metadata_dict['variables']):
        raise ValueError('Duplicate canonical upstream variable names')
    issues = collections.defaultdict(list)
    for issue in final_metadata_dict.get('issues', []):
        # Export-only name/length/null diagnostics are not scientific limitations.
        if not issue.get('code', '').startswith('stata_'):
            issues[issue.get('variable')].append(issue)
    scoped_issues = collections.defaultdict(list)
    for issue in final_metadata_dict.get('scoped_issues', []):
        scoped_issues[issue.get('variable')].append(issue)
    cb, labels = codebook_df.copy(deep=True), labels_df.copy(deep=True)
    counts = collections.Counter()
    replacements = []
    replaced_names = set()
    for idx, row in cb.iterrows():
        if row.get('source_namespace') != 'ipeds' or row.get('analysis_method') not in ('identity', 'coalesce'):
            continue
        names = canonical_sources(row)
        missing = [n for n in names if n not in upstream]
        if missing:
            raise ValueError(f'Unknown exact upstream source names: {missing}')
        vv = [upstream[n] for n in names]
        preferred = row.get('original_canonical_name', '')
        preferred = preferred[len('ipeds__'):] if preferred.startswith('ipeds__') else names[0]
        primary = upstream[preferred] if preferred in names else vv[0]
        full_labels = {v['name']: v.get('label', '') for v in vv}
        label = primary.get('label') or row['variable_label']
        if len(set(full_labels.values())) > 1 and not label.startswith('[varies'):
            label = '[varies by source/year] ' + label
        cb.at[idx, 'variable_label'] = label[:80]
        cb.at[idx, 'upstream_full_variable_label'] = label
        cb.at[idx, 'upstream_source_labels_json'] = json.dumps(full_labels, ensure_ascii=False, sort_keys=True)
        descriptions = [(v['name'], v.get('description', '').strip()) for v in vv]
        unique = list(dict.fromkeys(d for _, d in descriptions if d))
        if not unique:
            description = 'The source dictionary does not provide a complete description. No definition is inferred; review the source metadata and remaining issues.'
        elif len(unique) == 1:
            description = unique[0]
        else:
            description = '\n\n'.join(f'{name}: {desc}' for name, desc in descriptions if desc)
        if len(names) > 1:
            description += ' Analysis consolidation: ' + row['analysis_reason'] + '. Every source annual definition is retained; consolidation does not assert time-invariant meaning.'
        var_issues = [issue for name in names for issue in issues[name]]
        if var_issues:
            description += ' Unresolved upstream metadata issues remain; see upstream_metadata_issues_json.'
        cb.at[idx, 'description'] = description
        cb.at[idx, 'upstream_metadata_status'] = json.dumps({v['name']: v.get('metadata_status') for v in vv}, sort_keys=True)
        cb.at[idx, 'upstream_comparability_status'] = json.dumps({v['name']: v.get('comparability_status') for v in vv}, sort_keys=True)
        cb.at[idx, 'upstream_metadata_issues_json'] = json.dumps(var_issues, ensure_ascii=False, sort_keys=True)
        cb.at[idx, 'upstream_scoped_issues_json'] = json.dumps([x for name in names for x in scoped_issues[name]], ensure_ascii=False, sort_keys=True)
        cb.at[idx, 'upstream_metadata_adapter'] = ADAPTER_VERSION
        cb.at[idx, 'upstream_metadata_reference'] = '../Inputs/ipeds/final_labeled_panel/panel_clean_prch_2004_2023.dta.metadata.json.gz'
        if row['export_storage'] == 'numeric' and any(v.get('resolved_value_label_records') for v in vv):
            maps = scoped_integer_labels(vv)
            replaced_names.add(row['portable_name'])
            for code, text in maps.items():
                replacements.append({'canonical_name':row['canonical_name'], 'portable_name':row['portable_name'], 'code':code, 'value':str(code), 'label':text, 'description':'Exact upstream source meanings, explicitly scoped to IPEDS reporting years; unlisted year/code combinations remain unresolved.'})
            cb.at[idx, 'analysis_value_label_status'] = 'explicit_upstream_year_scopes'
            counts['numeric_columns_with_scoped_labels'] += bool(maps)
            counts['native_label_entries'] += len(maps)
        elif row['export_storage'] == 'string' and any(v.get('resolved_value_label_records') for v in vv):
            # Keep source tokens such as CA, A, 01.0000, and 012003 unchanged.
            cb.at[idx, 'analysis_value_label_status'] = 'source_string_labels_in_upstream_companion'
            counts['string_category_columns_preserved'] += 1
        counts['enriched_ipeds_columns'] += 1
        counts['columns_with_unresolved_upstream_issues'] += bool(var_issues)
    labels = labels.loc[~labels.portable_name.isin(replaced_names)].copy()
    if replacements:
        labels = pd.concat([labels, pd.DataFrame(replacements, columns=labels.columns)], ignore_index=True)
    if labels.duplicated(['portable_name','code']).any():
        raise ValueError('Duplicate output variable/code value labels')
    labels, excerpts = bound_native_value_labels(labels)
    for name in excerpts:
        cb.loc[cb.portable_name.eq(name), 'description'] += ' Native Stata value labels use marked excerpts because the complete label set exceeds the format limit; exact year-scoped meanings are in the companion value-label CSV full_label field.'
        cb.loc[cb.portable_name.eq(name), 'analysis_value_label_status'] = 'explicit_upstream_year_scopes_native_excerpts'
    protected = codebook_df.loc[~codebook_df.portable_name.isin(cb.loc[cb.upstream_metadata_adapter.eq(ADAPTER_VERSION), 'portable_name'])] if 'upstream_metadata_adapter' in cb else codebook_df
    counts['untouched_other_columns'] = len(protected)
    audit = {'adapter_version':ADAPTER_VERSION, 'metadata_only':True, 'data_values_changed':False, 'storage_changed':False, 'names_changed':False, 'missing_rules_changed':False, 'consolidation_rules_changed':False, 'counts':dict(counts), 'remaining_upstream_observation_issues':final_metadata_dict.get('observation_validation',{}).get('issues',[]), 'native_label_excerpts':excerpts, 'upstream_metadata_status':final_metadata_dict.get('metadata_status'), 'upstream_readiness_status':final_metadata_dict.get('readiness_status')}
    return cb.fillna(''), labels.fillna(''), audit


def freeze_final_metadata(source, output):
    """Freeze exact upstream JSON bytes plus compact lookup/issue sidecars."""
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (source.name + '.gz')
    h = hashlib.sha256()
    with source.open('rb') as src, target.open('wb') as dst:
        with gzip.GzipFile(fileobj=dst, mode='wb', filename='', mtime=0, compresslevel=6) as gz:
            while block := src.read(1024*1024):
                h.update(block); gz.write(block)
    data = read_final_metadata(target)
    with (output/'canonical_source_code_map.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['canonical_name','upstream_stata_name','storage_conversion','source_code','upstream_stata_code','upstream_label'])
        writer.writeheader()
        for var in data['variables']:
            for rec in var.get('stata_source_code_map', []):
                writer.writerow({'canonical_name':var['name'],'upstream_stata_name':var['export_name'],'storage_conversion':var['stata_storage_conversion'],'source_code':rec['source_code'],'upstream_stata_code':rec['export_code'],'upstream_label':rec['label']})
    issue_payload = {k:data.get(k) for k in ['metadata_status','readiness_status','comparability_status','metadata_supplement','metadata_scope_policy','issues','scoped_issues']}
    issue_payload['observation_issues'] = data.get('observation_validation',{}).get('issues',[])
    (output/'remaining_metadata_issues.json').write_text(json.dumps(issue_payload,ensure_ascii=False,indent=2)+'\n')
    provenance = {'adapter_version':ADAPTER_VERSION, 'source_metadata_path':str(source), 'source_metadata_sha256':h.hexdigest(), 'frozen_metadata_gzip_sha256':hashlib.sha256(target.read_bytes()).hexdigest(), 'data_sha256':data.get('data_sha256'),'source_panel_sha256':data.get('source_panel_sha256'),'variable_count':len(data['variables']), 'policy':'Exact original metadata bytes, deterministic gzip; source-code maps are labels only and do not authorize storage/value recodes.'}
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    return provenance
