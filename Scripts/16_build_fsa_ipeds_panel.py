#!/usr/bin/env python3
"""Build a lossless UNITID/collection-year FSA–IPEDS panel and timing sensitivity view."""
from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import json
import platform
import shutil
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from fsa_portable_exports import (
    prepare_export_frame, read_csv, verify_frame, verify_stata, write_excel,
    read_excel_data, _stata_frame, _check_metadata, NULL_TOKEN,
)

REPO = Path(__file__).resolve().parents[1]
FSA_SHA256 = '93d675fde748d6c480c7eb9050dccec49a88892154a60ed21e016539ba9983ba'
VIEWS = {'collection_anchor': 0, 'prior_award_year': -1}
DERIVED = [
    ('unitid', pa.int64()), ('year', pa.int32()), ('merge_status', pa.string()),
    ('has_ipeds_record', pa.bool_()), ('has_fsa_record', pa.bool_()),
    ('has_usable_fsa_record', pa.bool_()), ('has_blocked_fsa_family', pa.bool_()),
    ('fsa_year_offset', pa.int32()), ('expected_fsa_award_year_start', pa.int32()),
    ('expected_fsa_award_year_end', pa.int32()), ('ipeds_sfa_reference_year_start', pa.int32()),
    ('ipeds_sfa_reference_year_end', pa.int32()), ('fsa_sfa_periods_aligned', pa.bool_()),
]


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def read_keys(path, names):
    return pq.ParquetFile(path).read(columns=names).to_pandas()


def validate_keys(keys, id_name, time_name):
    if keys[[id_name, time_name]].isna().any().any():
        raise ValueError('Missing institution/time key')
    if keys.duplicated([id_name, time_name]).any():
        raise ValueError('Duplicate institution/time keys; refusing many-to-many merge')
    for name in (id_name, time_name):
        if keys[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError('Boolean institution/time key')
        values = pd.to_numeric(keys[name], errors='raise')
        if not np.isfinite(values).all() or not (values == np.floor(values)).all():
            raise ValueError(f'Noninteger key: {name}')
    if (keys[id_name] <= 0).any():
        raise ValueError('Nonpositive UNITID')


def exact_array(left, right):
    if left.type != right.type or len(left) != len(right):
        return False
    if left.equals(right):
        return True
    # Valid NaN and null remain distinct; tolerate only NaN == NaN, no numeric rounding.
    if pa.types.is_floating(left.type):
        if not left.is_null().equals(right.is_null()):
            return False
        equal = pc.or_kleene(pc.equal(left, right), pc.and_kleene(pc.is_nan(left), pc.is_nan(right)))
        return bool(pc.all(pc.fill_null(equal, True)).as_py())
    return False


def compare_tables(expected, actual, label):
    if expected.column_names != actual.column_names or expected.num_rows != actual.num_rows:
        raise ValueError(f'{label}: shape or column order differs')
    for name in expected.column_names:
        if not exact_array(expected[name], actual[name]):
            raise ValueError(f'{label}: source values, nulls, or Arrow type changed in {name}')
    return {'check': label, 'passed': True, 'rows': expected.num_rows,
            'columns': expected.num_columns, 'cells_checked': expected.num_rows * expected.num_columns}


def validate_lineage_cache(actual_records, cache_records):
    """A supplied cache is usable only when it equals the selected full source."""
    def records(values):
        return {json.dumps(row, sort_keys=True, allow_nan=False) for row in values}
    if records(actual_records) != records(cache_records):
        raise ValueError('Lineage cache differs from the selected IPEDS full lineage')


def combined_schema(ipeds_schema, fsa_schema, codebook, view, offset):
    rows = codebook.set_index('canonical_name').to_dict('index')
    fields = [pa.field(name, typ) for name, typ in DERIVED]
    fields += [pa.field('ipeds__' + f.name, f.type) for f in ipeds_schema]
    fields += [pa.field('fsa__' + f.name, f.type) for f in fsa_schema]
    if [f.name for f in fields] != codebook.canonical_name.tolist():
        raise ValueError('Combined codebook does not match exact output schema')
    result = []
    for field in fields:
        record = {k: str(rows[field.name].get(k, '')) for k in
                  ('variable_label', 'description', 'units', 'source_namespace', 'original_name', 'portable_name')}
        result.append(field.with_metadata({b'variable_metadata': json.dumps(record, ensure_ascii=False).encode()}))
    return pa.schema(result, metadata={b'fsa_ipeds_dataset': json.dumps({
        'schema_version': '1.0', 'view': view, 'primary_key': ['unitid', 'year'],
        'year_definition': 'IPEDS collection-cycle start; FSA award-year start equals year + fsa_year_offset',
        'fsa_year_offset': offset, 'universe': 'All rows of the supplied IPEDS PRCH-clean panel',
        'missing_rule': 'No FSA research-panel row is not evidence of zero aid or nonparticipation.',
        'metadata': '../Metadata/codebook.csv; ../Metadata/ipeds_metadata.json',
        'scope': 'Period alignment and UNITID linkage do not establish equal populations or campus-exclusive reporting.',
    }).encode()})


def merge_year(ipeds, fsa, fsa_index, year, offset, schema):
    target_year = year + offset
    ids = ipeds['UNITID'].to_pylist()
    indexer = fsa_index.get_indexer(pd.MultiIndex.from_arrays([ids, [target_year] * len(ids)]))
    matched = indexer >= 0
    take = pa.array(indexer, mask=~matched, type=pa.int64())
    aligned = fsa.take(take)
    n = len(ids)
    values = [
        ipeds['UNITID'].cast(pa.int64()), ipeds['year'].cast(pa.int32()),
        pa.array(np.where(matched, 'matched', 'ipeds_only')),
        pa.array([True] * n), pa.array(matched),
        pc.greater(aligned['included_source_family_count'], 0),
        pc.greater(aligned['blocked_source_family_count'], 0),
        pa.array([offset] * n, type=pa.int32()),
        pa.array([target_year] * n, type=pa.int32()), pa.array([target_year + 1] * n, type=pa.int32()),
        pa.array([year - 1] * n, type=pa.int32()), pa.array([year] * n, type=pa.int32()),
        pa.array([offset == -1] * n, mask=~matched, type=pa.bool_()),
        *ipeds.columns, *aligned.columns,
    ]
    table = pa.Table.from_arrays(values, schema=schema)
    return table, aligned, matched


def pandas_nullable(table):
    def dtype(typ):
        if pa.types.is_string(typ) or pa.types.is_large_string(typ): return pd.StringDtype()
        if pa.types.is_boolean(typ): return pd.BooleanDtype()
        if pa.types.is_integer(typ): return pd.Int64Dtype()
        return None
    return table.to_pandas(types_mapper=dtype)


def write_stata_combined(frame, path, codebook, value_labels):
    _check_metadata(codebook)
    stata = _stata_frame(frame, codebook)
    labels = dict(zip(codebook.portable_name, codebook.variable_label))
    values = {n: dict(zip(g.code.astype(int), g.label)) for n, g in value_labels.groupby('portable_name')}
    strings = [r.portable_name for r in codebook.itertuples() if r.export_storage == 'string'
               and stata[r.portable_name].str.len().max() > 120]
    stata.to_stata(path, write_index=False, version=118,
                   data_label='FSA-IPEDS UNITID x collection year | collection-anchor view',
                   variable_labels=labels, value_labels=values, convert_strl=strings)
    del stata
    return verify_stata(path, frame, codebook, value_labels)


def export_year(table, year, out, codebook, value_labels, formats):
    expected = pandas_nullable(table)
    frame = prepare_export_frame(expected, codebook, value_labels)
    checks = []
    if 'csv' in formats:
        path = out / 'Exports/csv' / f'fsa_ipeds_{year}.csv.gz'
        path.parent.mkdir(parents=True, exist_ok=True)
        from fsa_portable_exports import write_csv
        write_csv(frame, path)
        checks.append(verify_frame(frame, read_csv(path, codebook), codebook, 'csv'))
    if 'stata' in formats:
        path = out / 'Exports/stata' / f'fsa_ipeds_{year}.dta'
        path.parent.mkdir(parents=True, exist_ok=True)
        checks.append(write_stata_combined(frame, path, codebook, value_labels))
    if 'excel' in formats:
        from fsa_ipeds_combined_excel import export_excel, PRECISION_COLUMNS
        path = out / 'Exports/excel' / f'fsa_ipeds_{year}.xlsx'
        path.parent.mkdir(parents=True, exist_ok=True)
        excel_check, precision_records = export_excel(frame, path, codebook, value_labels, {
            'dataset': 'FSA–IPEDS collection-anchor panel', 'year': year,
            'numeric_text_ledger': f'Checks/excel_precision/exact_text_{year}.csv',
            'key': 'unitid, year', 'timing': 'FSA award-year start = IPEDS collection-year start.',
            'aid_comparisons': 'Use the prior_award_year Parquet view to align the primary SFA reporting award year.',
            'scope': 'No parent totals allocated to children. Source scope and eligibility flags retained.',
            'missing': 'Blank numeric cells are not zeros. String null masks are supplied in Exports/string_nulls.parquet.',
            'codes': 'FSA statuses/booleans use ValueLabels. IPEDS raw string codes retain year-specific definitions in Metadata/ipeds_metadata.json.',
        })
        checks.append(excel_check)
        ledger = out / 'Checks/excel_precision' / f'exact_text_{year}.csv'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(precision_records, columns=PRECISION_COLUMNS).to_csv(ledger, index=False)
    strings = codebook.loc[codebook.export_storage.eq('string'), 'portable_name']
    masks = pd.DataFrame({'unitid': frame.unitid, 'year': frame.year,
                          **{n: frame[n].isna() for n in strings}})
    null_table = pa.Table.from_pandas(masks, preserve_index=False)
    for check in checks: check['year'] = year
    del expected, frame
    gc.collect()
    return checks, null_table



def export_worker(config):
    panel, year, output, formats = config
    pa.set_cpu_count(2)
    out = Path(output)
    codebook = pd.read_csv(out / 'Metadata/codebook.csv', keep_default_na=False)
    labels = pd.read_csv(out / 'Metadata/value_labels.csv', keep_default_na=False)
    table = pq.read_table(panel, filters=[('year','=',year)])
    checks, masks = export_year(table, year, out, codebook, labels, set(formats))
    folder = out / 'Checks/export_years'
    folder.mkdir(parents=True, exist_ok=True)
    pq.write_table(masks, folder / f'string_nulls_{year}.parquet', compression='zstd')
    write_json(folder / f'checks_{year}.json', checks)
    return year, checks


def copy_file(source, target, records, role):
    source, target = Path(source), Path(target)
    if not source.is_file(): raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    digest = sha256(target)
    if digest != sha256(source): raise ValueError(f'Source changed during copy: {source}')
    records.append({'role': role, 'source_path': str(source), 'source_resolved_path': str(source.resolve()),
                    'relative_path': str(target), 'bytes': target.stat().st_size, 'sha256': digest})


def inventory(out):
    return [{'path': str(p.relative_to(out)), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
            for p in sorted(out.rglob('*')) if p.is_file() and p.name not in ('build_manifest.json', '.DS_Store') and '__pycache__' not in p.parts]


def promote_stage(out, output):
    """Publish verified files while preserving the destination's Finder state."""
    children = [p for p in out.iterdir() if p.name != '.DS_Store']
    for child in children:
        if (output / child.name).exists(): raise ValueError('Output changed during build; refusing overwrite')
    marker = out / '.DS_Store'
    if marker.exists() and (marker.is_symlink() or not marker.is_file()):
        raise ValueError('Unexpected staging Finder metadata type')
    for child in children: child.rename(output / child.name)
    if marker.exists(): marker.unlink()
    out.rmdir()


def export_context(out):
    paths = ['Panels/fsa_ipeds_panel_2004_2023.parquet', 'Metadata/codebook.csv', 'Metadata/value_labels.csv']
    context = {name: sha256(out / name) for name in paths}
    for name in ['16_build_fsa_ipeds_panel.py', 'fsa_portable_exports.py', 'fsa_export_metadata.py', 'fsa_ipeds_combined_excel.py']:
        context['implementation/' + name] = sha256(REPO / 'Scripts' / name)
    context['environment'] = {'python': platform.python_version(), 'pandas': pd.__version__,
                              'pyarrow': pa.__version__, 'numpy': np.__version__}
    return context


def checkpoint_export(out, year, context):
    """Bind a completed read-back verification to its exact source and artifact bytes."""
    check_path = out / 'Checks/export_years' / f'checks_{year}.json'
    checks = json.loads(check_path.read_text())
    if not checks or any(not c.get('all_values_passed') or c.get('year') != year for c in checks):
        raise ValueError('Cannot checkpoint an unverified annual export')
    formats = {c['format'] for c in checks}
    names = [str(check_path.relative_to(out)), f'Checks/export_years/string_nulls_{year}.parquet']
    for fmt, ext in [('csv','csv.gz'),('stata','dta'),('excel','xlsx')]:
        if fmt in formats: names.append(f'Exports/{fmt}/fsa_ipeds_{year}.{ext}')
    if 'excel' in formats: names.append(f'Checks/excel_precision/exact_text_{year}.csv')
    items = []
    for name in names:
        path = out / name
        if path.is_symlink() or not path.is_file(): raise ValueError('Missing export checkpoint artifact: ' + name)
        items.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha256(path)})
    record = {'year': year, 'formats': sorted(formats), 'source_hashes': context, 'artifacts': items,
              'verification': 'All annual read-back checks passed before checkpointing.'}
    write_json(out / 'Checks/export_years' / f'checkpoint_{year}.json', record)


def reusable_export(out, year, context, formats):
    path = out / 'Checks/export_years' / f'checkpoint_{year}.json'
    if not path.is_file(): return None
    record = json.loads(path.read_text())
    if record.get('year') != year or record.get('source_hashes') != context or set(record.get('formats', [])) != formats:
        return None
    expected_names = {f'Checks/export_years/checks_{year}.json', f'Checks/export_years/string_nulls_{year}.parquet'}
    for fmt, ext in [('csv','csv.gz'),('stata','dta'),('excel','xlsx')]:
        if fmt in formats: expected_names.add(f'Exports/{fmt}/fsa_ipeds_{year}.{ext}')
    if 'excel' in formats: expected_names.add(f'Checks/excel_precision/exact_text_{year}.csv')
    if {a['path'] for a in record['artifacts']} != expected_names or len(record['artifacts']) != len(expected_names):
        return None
    for item in record['artifacts']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts: raise ValueError('Unsafe export checkpoint path')
        target = out / relative
        if target.is_symlink() or not target.is_file() or target.stat().st_size != item['bytes'] or sha256(target) != item['sha256']:
            return None
    checks = json.loads((out / 'Checks/export_years' / f'checks_{year}.json').read_text())
    if {c['format'] for c in checks} != formats or any(not c.get('all_values_passed') or c.get('year') != year for c in checks):
        return None
    return checks


def write_helpers(out, years):
    scripts = out / 'Scripts'
    scripts.mkdir(exist_ok=True)
    for name in ('16_build_fsa_ipeds_panel.py', 'fsa_ipeds_combined_metadata.py',
                 'fsa_ipeds_merge_diagnostics.py', 'ipeds_category_storage.py', 'fsa_ipeds_pell_validation.py', 'fsa_ipeds_combined_excel.py', 'fsa_portable_exports.py', 'fsa_export_metadata.py', 'fsa_ipeds_source_repair.py'):
        shutil.copy2(REPO / 'Scripts' / name, scripts / name)
    (out / 'reproduce.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
OUTPUT="${1:?Supply a new output directory}"
shift
RUNTIME="${FSA_RUNTIME:-$ROOT/.reproduction-runtime}"
[[ ! -L "$RUNTIME" ]] || { echo "Runtime directory must not be a symlink" >&2; exit 1; }
mkdir -p "$RUNTIME"
UV="$RUNTIME/uv/uv"
if [[ ! -x "$UV" ]]; then
  curl -fsSL --retry 5 https://astral.sh/uv/0.12.18/install.sh -o "$RUNTIME/install-uv.sh"
  env UV_UNMANAGED_INSTALL="$RUNTIME/uv" UV_NO_MODIFY_PATH=1 sh "$RUNTIME/install-uv.sh" </dev/null
fi
[[ "$("$UV" --version)" == "uv 0.12.18"* ]] || { echo "Unexpected uv version" >&2; exit 1; }
export UV_CACHE_DIR="$RUNTIME/cache" UV_PYTHON_INSTALL_DIR="$RUNTIME/python" UV_NO_MODIFY_PATH=1
if [[ ! -x "$RUNTIME/.venv/bin/python" ]]; then
  "$UV" --no-config venv --python 3.13.0 --managed-python "$RUNTIME/.venv" </dev/null
fi
"$RUNTIME/.venv/bin/python" -c 'import sys; assert sys.version_info[:3] == (3,13,0)'
"$UV" --no-config pip install --python "$RUNTIME/.venv/bin/python" --index-url https://pypi.org/simple -r "$ROOT/environment-requirements.txt" </dev/null
exec "$RUNTIME/.venv/bin/python" "$ROOT/Scripts/16_build_fsa_ipeds_panel.py" --bundle "$ROOT" --output "$OUTPUT" "$@"
''')
    (out / 'reproduce.sh').chmod(0o755)
    (out / 'load_stata.do').write_text('version 14\nclear all\n* Run from FSA-IPEDS_DS. Stata SE/MP supports this 4,046-variable dataset.\n'
        + f'use "Exports/stata/fsa_ipeds_{years[0]}.dta", clear\n'
        + ''.join(f'append using "Exports/stata/fsa_ipeds_{y}.dta"\n' for y in years[1:])
        + 'isid unitid year\nxtset unitid year\nnotes _dta: year is IPEDS collection-cycle start, FSA award-year start in this view.\n'
        + 'notes _dta: For aid-period alignment use Panels/fsa_ipeds_aid_aligned_2004_2023.parquet.\n'
        + 'notes _dta: Consult Metadata/codebook.csv and Metadata/ipeds_metadata.json. Missing FSA is not zero aid.\n')
    (out / 'load_csv.py').write_text('''from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parent

def load(years=None, canonical_names=True):
    meta = pd.read_csv(ROOT / "Metadata/codebook.csv", keep_default_na=False)
    types = {r.portable_name: "string" if r.export_storage == "string" else
             ("Int64" if r.export_storage == "coded_category" or "int" in r.original_dtype.lower() else "float64")
             for r in meta.itertuples(index=False)}
    paths = sorted((ROOT / "Exports/csv").glob("*.csv.gz"))
    if years is not None: paths = [p for p in paths if int(p.name.split(".")[0].split("_")[-1]) in years]
    if not paths: raise ValueError("No annual CSV files selected")
    data = pd.concat([pd.read_csv(p, dtype=types, keep_default_na=False, na_values=["__FSA_NULL__"], float_precision="round_trip") for p in paths], ignore_index=True)
    if data.duplicated(["unitid", "year"]).any(): raise ValueError("Duplicate panel keys")
    if canonical_names: data = data.rename(columns=dict(zip(meta.portable_name, meta.canonical_name)))
    return data

if __name__ == "__main__": print(load().shape)
''')


def build(args):
    output = args.output.expanduser().absolute()
    resume = getattr(args, 'resume_stage', None)
    if resume:
        out = resume.expanduser().absolute()
        if args.bundle or output.is_symlink() or out.is_symlink() or not out.is_dir() or out.parent != output or not out.name.startswith('.building-'):
            raise ValueError('Resume requires the existing source-build staging directory directly inside output')
        if any(p.name not in (out.name, '.DS_Store') or p.is_symlink() for p in output.iterdir()):
            raise ValueError('Unexpected output contents; refusing resume')
        previous_run = json.loads((out / 'build_manifest.json').read_text())
        if previous_run.get('completed'):
            raise ValueError('Cannot resume an already completed build')
        if set(previous_run.get('formats', [])) != set(args.formats.split(',')):
            raise ValueError('Resume must retain the failed build formats')
    elif output.is_symlink() or (output.exists() and (not output.is_dir() or any(p.name != '.DS_Store' or p.is_symlink() for p in output.iterdir()))):
        raise ValueError('Output must be a new or empty nonsymlink directory; existing work is preserved')
    else:
        output.mkdir(parents=True, exist_ok=True)
        out = output / ('.building-' + uuid.uuid4().hex)
        out.mkdir()
    manifest = {'schema_version': '1.0', 'completed': False, 'started_utc': datetime.now(timezone.utc).isoformat(),
                'environment': {'python': platform.python_version(), 'pandas': pd.__version__, 'pyarrow': pa.__version__},
                'views': VIEWS, 'formats': args.formats.split(','), 'primary_key': ['unitid', 'year']}
    write_json(out / 'build_manifest.json', manifest)
    records = []
    if args.bundle:
        bundle = args.bundle.resolve()
        previous = json.loads((bundle / 'build_manifest.json').read_text())
        if not previous.get('completed'): raise ValueError('Replay bundle is not a completed build')
        for item in previous['artifacts']:
            relative = Path(item['path'])
            if relative.is_absolute() or '..' in relative.parts: raise ValueError('Unsafe bundle path')
            if (relative.parts[0] in ('Inputs', 'Metadata', 'Scripts', 'Panels')
                    or relative.parts[:2] == ('Checks', 'canonical_ipeds_handoff')):
                p = bundle / relative
                if p.is_symlink() or not p.is_file() or p.stat().st_size != item['bytes'] or sha256(p) != item['sha256']:
                    raise ValueError(f'Replay input differs: {relative}')
        checked_directories = ['Inputs', 'Metadata', 'Scripts']
        if previous.get('canonical_ipeds_handoff'):
            checked_directories.append('Checks/canonical_ipeds_handoff')
        for directory in checked_directories:
            expected_paths = {a['path'] for a in previous['artifacts'] if a['path'].startswith(directory + '/')}
            actual_paths = {str(p.relative_to(bundle)) for p in (bundle / directory).rglob('*') if p.is_file() and p.name != '.DS_Store' and '__pycache__' not in p.parts}
            if actual_paths != expected_paths: raise ValueError('Unexpected or missing frozen files: ' + directory)
        shutil.copytree(bundle / 'Inputs', out / 'Inputs', ignore=shutil.ignore_patterns('.DS_Store'))
        shutil.copytree(bundle / 'Metadata', out / 'Metadata', ignore=shutil.ignore_patterns('.DS_Store'))
        if previous.get('canonical_ipeds_handoff'):
            shutil.copytree(bundle / 'Checks/canonical_ipeds_handoff', out / 'Checks/canonical_ipeds_handoff', ignore=shutil.ignore_patterns('.DS_Store', '__pycache__'))
        ipeds_path, fsa_path, bridge_path = out / 'Inputs/ipeds_panel.parquet', out / 'Inputs/fsa_panel.parquet', out / 'Inputs/fsa_bridge.parquet'
        codebook = pd.read_csv(out / 'Metadata/codebook.csv', keep_default_na=False)
        labels = pd.read_csv(out / 'Metadata/value_labels.csv', keep_default_na=False)
        metadata = json.loads((out / 'Metadata/ipeds_metadata.json').read_text())
        manifest['replayed_from'] = str(bundle)
        manifest['source_records'] = previous['source_records']
        for key in ('canonical_ipeds_handoff', 'fsa_conservation_evidence'):
            if key in previous:
                manifest[key] = dict(previous[key])
        if manifest.get('canonical_ipeds_handoff'):
            handoff = manifest['canonical_ipeds_handoff']
            handoff['combined_analysis_labels_refreshed_in_source_bundle'] = handoff.pop('combined_analysis_labels_refreshed', True)
            handoff['combined_analysis_outputs_created_by_this_replay'] = False
            handoff['validation_scope'] = 'Inherited canonical handoff evidence; this replay independently validates frozen inputs and regenerated master panels.'
        if previous.get('ipeds_source_metadata_repair'):
            manifest['ipeds_source_metadata_repair'] = previous['ipeds_source_metadata_repair']
    else:
        ipeds_path, fsa_path, bridge_path = out / 'Inputs/ipeds_panel.parquet', out / 'Inputs/fsa_panel.parquet', out / 'Inputs/fsa_bridge.parquet'
        source_fsa = args.fsa_root / 'Panels/ipeds/unitid_research/fsa_unitid_award_year_panel.parquet'
        if sha256(source_fsa) != FSA_SHA256: raise ValueError('FSA input differs from reviewed research release')
        prch_path = args.ipeds_root / 'Checks/v2/prch_qc/prch_run_manifest.json'
        prch = json.loads(prch_path.read_text())
        if prch.get('status') != 'complete': raise ValueError('IPEDS PRCH run is incomplete')
        expected_ipeds = next(a for a in prch['artifacts'] if a['name'] == 'panel_clean.parquet')
        if args.ipeds_panel.stat().st_size != expected_ipeds['bytes'] or sha256(args.ipeds_panel) != expected_ipeds['sha256']:
            raise ValueError('Supplied IPEDS panel does not match its PRCH manifest')
        for item in prch['artifacts']:
            if item['name'] != 'panel_clean.parquet':
                source = prch_path.parent / item['name']
                if source.stat().st_size != item['bytes'] or sha256(source) != item['sha256']:
                    raise ValueError('IPEDS PRCH evidence does not match its manifest: ' + item['name'])
        copy_file(args.ipeds_panel, ipeds_path, records, 'IPEDS PRCH-clean panel')
        copy_file(source_fsa, fsa_path, records, 'FSA UNITID research panel')
        if sha256(ipeds_path) != expected_ipeds['sha256'] or sha256(fsa_path) != FSA_SHA256:
            raise ValueError('Copied panels differ from the reviewed hashes')
        copy_file(args.fsa_root / 'Panels/ipeds/fsa_ipeds_bridge.parquet', bridge_path, records, 'FSA OPEID/IPEDS bridge for diagnostic evidence only')
        release = json.loads((args.fsa_root / 'build/research_release_manifest.json').read_text())
        bridge_record = next(a for a in release['artifacts'] if a['path'] == 'Panels/ipeds/fsa_ipeds_bridge.parquet')
        if sha256(bridge_path) != bridge_record['sha256']:
            raise ValueError('FSA diagnostic bridge differs from the reviewed release')
        sources = [
            (args.ipeds_root / 'Dictionary/v2/dictionary_lake.parquet', 'ipeds/dictionary_lake.parquet'),
            (args.ipeds_root / 'Dictionary/v2/dictionary_codes.parquet', 'ipeds/dictionary_codes.parquet'),
            (args.ipeds_root / 'Checks/v2/prch_qc/prch_run_manifest.json', 'ipeds/prch_run_manifest.json'),
            (args.ipeds_root / 'Checks/v2/prch_qc/prch_flag_policy.csv', 'ipeds/prch_flag_policy.csv'),
            (args.ipeds_root / 'Checks/v2/prch_qc/prch_cell_actions.parquet', 'ipeds/prch_cell_actions.parquet'),
            (args.fsa_root / 'build/research_release_manifest.json', 'fsa/research_release_manifest.json'),
        ]
        for item in prch['artifacts']:
            if item['name'] not in ('panel_clean.parquet','prch_flag_policy.csv','prch_cell_actions.parquet'):
                sources.append((prch_path.parent / item['name'], 'ipeds/' + item['name']))
        for p in (args.fsa_root / 'Exports/unitid').glob('*'):
            if p.name in ('codebook.csv','codebook.json','value_labels.csv','dataset_metadata.json','source_unitid_manifest.json'):
                sources.append((p, 'fsa/' + p.name))
        from fsa_ipeds_source_repair import source_repair_inputs
        repair_sources, repair_summary = source_repair_inputs(args.ipeds_root, args.ipeds_panel)
        sources.extend(repair_sources)
        if repair_summary is not None:
            manifest['ipeds_source_metadata_repair'] = repair_summary
        for source, relative in sources: copy_file(source, out / 'Inputs' / relative, records, relative)
        for item in records: item['relative_path'] = str(Path(item['relative_path']).relative_to(out))
        manifest['source_records'] = records
        from fsa_ipeds_combined_metadata import build_combined_metadata
        print('Building annual source metadata', flush=True)
        years = sorted(read_keys(ipeds_path, ['year']).year.unique().tolist())
        from fsa_ipeds_combined_metadata import _lineage_records
        lineage_source = args.ipeds_root / 'Checks/v2/wide_qc/qc_value_lineage.parquet'
        lineage_source_hash = sha256(lineage_source)
        if repair_summary is not None and repair_summary['source_lineage_sha256'] != lineage_source_hash:
            raise ValueError('Selected full lineage differs from the verified metadata repair package')
        print('Extracting and binding annual column lineage to the selected IPEDS source', flush=True)
        lineage_records = _lineage_records(lineage_source, years)
        if args.lineage_cache:
            validate_lineage_cache(lineage_records, pq.read_table(args.lineage_cache).to_pylist())
        if sha256(lineage_source) != lineage_source_hash:
            raise ValueError('IPEDS full lineage changed during extraction')
        lineage_target = out / 'Inputs/ipeds/column_lineage.parquet'
        pq.write_table(pa.Table.from_pylist(lineage_records), lineage_target, compression='zstd')
        records.append({'role': 'Distinct annual IPEDS column lineage verified against full source',
                        'source_path': str(lineage_source), 'source_resolved_path': str(lineage_source.resolve()),
                        'source_sha256': lineage_source_hash,
                        'relative_path': str(lineage_target.relative_to(out)),
                        'bytes': lineage_target.stat().st_size, 'sha256': sha256(lineage_target)})
        codebook, labels, metadata = build_combined_metadata(pq.read_schema(ipeds_path), pq.read_schema(fsa_path), args.ipeds_root, args.fsa_root / 'Exports/unitid', years, lineage_records=lineage_records)
        if metadata['metadata_sources']['lineage']['sha256'] != lineage_source_hash:
            raise ValueError('IPEDS full lineage changed between extraction and metadata build')
        for name, relative in [('dictionary','ipeds/dictionary_lake.parquet'),('codes','ipeds/dictionary_codes.parquet')]:
            if metadata['metadata_sources'][name]['sha256'] != sha256(out / 'Inputs' / relative):
                raise ValueError('IPEDS metadata source differs from frozen copy: ' + name)
        for name in ['codebook.csv','value_labels.csv']:
            if metadata['fsa_metadata_sources'][name]['sha256'] != sha256(out / 'Inputs/fsa' / name):
                raise ValueError('FSA metadata source differs from frozen copy: ' + name)
        (out / 'Metadata').mkdir(exist_ok=True)
        codebook.to_csv(out / 'Metadata/codebook.csv', index=False)
        labels.to_csv(out / 'Metadata/value_labels.csv', index=False)
        write_json(out / 'Metadata/ipeds_metadata.json', metadata)
        pd.DataFrame(metadata.get('issues',[])).to_csv(out / 'Metadata/ipeds_metadata_issues.csv', index=False)
        codebook[['canonical_name','portable_name','variable_label']].to_csv(out / 'Metadata/name_map.csv', index=False)
        if metadata.get('annual_sfa_timing'):
            pd.DataFrame(metadata['annual_sfa_timing']).to_csv(out / 'Metadata/annual_sfa_timing.csv', index=False)
    keys = read_keys(ipeds_path, ['UNITID', 'year'])
    fsa_keys = read_keys(fsa_path, ['unitid', 'award_year_start','award_year_end','award_year'])
    validate_keys(keys, 'UNITID', 'year'); validate_keys(fsa_keys, 'unitid', 'award_year_start')
    if not (fsa_keys.award_year_end == fsa_keys.award_year_start + 1).all(): raise ValueError('Invalid FSA award-year endpoints')
    expected_labels = fsa_keys.award_year_start.astype(str) + '-' + fsa_keys.award_year_end.astype(str)
    if not expected_labels.equals(fsa_keys.award_year.astype(str)): raise ValueError('Invalid FSA award-year labels')
    years = sorted(keys.year.unique().tolist())
    if years != list(range(2004, 2024)): raise ValueError('Expected supplied IPEDS panel years 2004–2023')
    _check_metadata(codebook)
    fsa = pq.ParquetFile(fsa_path).read()
    fsa_index = pd.MultiIndex.from_frame(fsa_keys[['unitid','award_year_start']])
    ipeds_schema = pq.read_schema(ipeds_path)
    schemas = {name: combined_schema(ipeds_schema, fsa.schema, codebook, name, offset) for name, offset in VIEWS.items()}
    (out / 'Panels').mkdir(exist_ok=True)
    checks, annual = [], []
    files = {'collection_anchor': out / 'Panels/fsa_ipeds_panel_2004_2023.parquet',
             'prior_award_year': out / 'Panels/fsa_ipeds_aid_aligned_2004_2023.parquet'}
    writers = {name: pq.ParquetWriter(path, schemas[name], compression='zstd') for name, path in files.items()}
    formats = set(args.formats.split(',')) - {'parquet'}
    try:
        for year in years:
            print(f'Merging {year}', flush=True)
            ipeds = pq.read_table(ipeds_path, filters=[('year','=',year)]).sort_by([('UNITID','ascending')])
            for view, offset in VIEWS.items():
                merged, aligned, matched = merge_year(ipeds, fsa, fsa_index, year, offset, schemas[view])
                checks.append(compare_tables(ipeds, merged.select(['ipeds__' + n for n in ipeds.column_names]).rename_columns(ipeds.column_names), view + f'/{year}/ipeds_source'))
                checks.append(compare_tables(aligned, merged.select(['fsa__' + n for n in fsa.column_names]).rename_columns(fsa.column_names), view + f'/{year}/fsa_source'))
                writers[view].write_table(merged, row_group_size=10000)
                annual.append({'view': view, 'year': year, 'rows': merged.num_rows, 'matched': int(matched.sum()), 'ipeds_only': int((~matched).sum()),
                               'usable_fsa_rows': pc.sum(pc.cast(merged['has_usable_fsa_record'], pa.int64())).as_py(),
                               'matched_blocked_only': int(matched.sum()) - (pc.sum(pc.cast(merged['has_usable_fsa_record'], pa.int64())).as_py() or 0)})
                del merged, aligned
            del ipeds
            gc.collect()
    finally:
        for writer in writers.values(): writer.close()
    print('Verifying every canonical output cell against source inputs', flush=True)
    for year in years:
        ipeds = pq.read_table(ipeds_path, filters=[('year','=',year)]).sort_by([('UNITID','ascending')])
        for view, offset in VIEWS.items():
            expected, _, _ = merge_year(ipeds, fsa, fsa_index, year, offset, schemas[view])
            actual = pq.read_table(files[view], filters=[('year','=',year)])
            checks.append(compare_tables(expected, actual, view + f'/{year}/parquet_roundtrip'))
            if args.bundle:
                reference = pq.read_table(args.bundle / files[view].relative_to(out), filters=[('year','=',year)])
                checks.append(compare_tables(reference, actual, view + f'/{year}/replay_reference'))
        del ipeds, expected, actual
    del fsa
    gc.collect()
    if formats:
        print('Exporting and checking annual CSV/Stata/Excel files with two workers', flush=True)
        context = export_context(out)
        configs, reused = [], []
        for year in years:
            cached = reusable_export(out, year, context, formats) if resume else None
            if cached is not None:
                checks.extend(cached)
                reused.append(year)
                print(f'Reused hash-verified export checkpoint {year}', flush=True)
            else:
                configs.append((str(files['collection_anchor']), year, str(out), sorted(formats)))
        manifest['reused_verified_export_years'] = reused
        with ProcessPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(export_worker, config) for config in configs]
            for future in as_completed(futures):
                try:
                    year, export_checks = future.result()
                except Exception:
                    print('Annual export failed; allowing running workers to finish before reporting the error', flush=True)
                    for remaining in futures: remaining.cancel()
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise
                checks.extend(export_checks)
                checkpoint_export(out, year, context)
                print(f'Completed and verified exports {year}', flush=True)
        with pq.ParquetWriter(out / 'Exports/string_nulls.parquet', pq.read_schema(out / 'Checks/export_years' / f'string_nulls_{years[0]}.parquet'), compression='zstd') as null_writer:
            for year in years:
                null_writer.write_table(pq.read_table(out / 'Checks/export_years' / f'string_nulls_{year}.parquet'))
    (out / 'Checks').mkdir(exist_ok=True)
    pd.DataFrame(annual).to_csv(out / 'Checks/annual_merge_summary.csv', index=False)
    write_json(out / 'Checks/validation_checks.json', checks)
    from fsa_ipeds_merge_diagnostics import create_diagnostics
    diagnostic = {view: create_diagnostics(ipeds_path, fsa_path, bridge_path, out / 'Checks' / view, offset=offset) for view, offset in VIEWS.items()}
    write_json(out / 'Checks/merge_diagnostics.json', diagnostic)
    from fsa_ipeds_pell_validation import create_pell_validation
    print('Cross-checking Pell reporting periods and institution volumes', flush=True)
    pell_validation = create_pell_validation(ipeds_path, fsa_path, out / 'Inputs/ipeds/dictionary_lake.parquet', bridge_path, out / 'Checks/pell_validation')
    write_json(out / 'Checks/pell_validation_summary.json', pell_validation)
    # Generated report links resolve from the bundle root after staged promotion.
    def portable_paths(value):
        if isinstance(value, dict): return {k: portable_paths(v) for k,v in value.items()}
        if isinstance(value, list): return [portable_paths(v) for v in value]
        if isinstance(value, str) and value.startswith(str(out) + '/'):
            return str(Path(value).relative_to(out))
        return value
    for report in (out / 'Checks').rglob('*.json'):
        write_json(report, portable_paths(json.loads(report.read_text())))
    write_helpers(out, years)
    manifest.update({'rows_per_view': len(keys), 'columns_per_view': len(codebook), 'years': years,
                     'annual_summary': annual, 'validation_check_count': len(checks),
                     'all_canonical_values_nulls_types_preserved': True, 'native_stata_excel_execution_tested': False,
                     'finished_utc': datetime.now(timezone.utc).isoformat(), 'completed': True})
    repair = manifest.get('ipeds_source_metadata_repair')
    repair_note = ('This build uses the verified IPEDS source metadata correction `'
                   + repair['correction_version']
                   + '`. Original metadata and correction evidence are frozen under '
                   '`Inputs/ipeds/source_metadata_repair/`. '
                   'The annual Pell audit recomputes verification from the corrected dictionary and physical lineage. '
                   'Item-level imputation flags remain unavailable in the 2023 Access source; '
                   'metadata verification does not mean a value was reported rather than imputed. '
                   'Institution links and parent/child scope are unchanged by a table-reference correction.'
                   if repair else 'No separate IPEDS metadata repair bundle was supplied. '
                   'Consult the annual source audit for actual source-table verification status.')
    handoff_note = ('The current canonical IPEDS Final release was checked against the frozen IPEDS input '
                    'for every value, missing value, key and source storage type. '
                    'See `Inputs/ipeds/final_labeled_panel/binding.json` and its compressed upstream metadata. '
                    'Analysis label enrichment uses exact original names and explicit year scopes; '
                    'source category encodings are not substituted for original state/CIP/OPE identifiers. '
                    'Detailed original-reporting-grain conservation and exclusion evidence is frozen under '
                    '`Inputs/fsa/source_conservation/`.' if manifest.get('canonical_ipeds_handoff') else '')
    readme = f'''# FSA–IPEDS institution panel

Primary: `Panels/fsa_ipeds_panel_2004_2023.parquet`. {len(keys):,} rows, {len(codebook):,} columns, unique `unitid year`. Every row of the supplied IPEDS PRCH-clean panel is retained.

The main view joins FSA award-year **start** to IPEDS collection-year **start**. IPEDS year 2023 is the 2023–24 collection cycle; FSA is AY2023–24. This is an institutional-context alignment, not a claim that all measures cover that period.

For student-aid period comparisons use `Panels/fsa_ipeds_aid_aligned_2004_2023.parquet`: IPEDS 2023 is matched to FSA AY2022–23. All 20 annual SFA table mappings support this prior-award-year convention; see `Metadata/annual_sfa_timing.csv`. Multi-year/cohort, fiscal-year, survey population and reporting-scope differences still require variable-specific treatment. Timing alignment alone does not make FSA and IPEDS totals equivalent.

`ipeds__*` and `fsa__*` retain all original source fields and Arrow types. No OPEID is used to perform this already-UNITID-linked join. Family-specific OPEIDs remain strings and are not collapsed across programs. IPEDS raw sentinel codes (including OPEID -2) are retained verbatim; they are not valid institution identifiers.

`merge_status` is matched/ipeds_only. `has_fsa_record` means a row in the conservative FSA UNITID research view, not program participation. `has_usable_fsa_record` requires at least one included family; blocked-only rows are retained. Missing FSA records and amounts are never filled with zero. Use family statuses and component scope flags. No aid is copied or allocated from reporting parents to children. The source IPEDS PRCH cleaning is preserved exactly.

`Checks/pell_validation/` compares only all-undergraduate Pell recipient and total-dollar measures across plausible time alignments; it excludes FTFT-only measures and distinguishes reporting-period evidence from population/reporting-scope differences. These checks flag discrepancies without changing identity links.

`Checks/annual_merge_summary.csv` and both diagnostic folders retain unmatched IDs, out-of-range FSA keys, blocked-only cases and exact-OPEID reporting evidence for unmatched IPEDS rows. Diagnostic OPEID matches do not assign data and do not prove scope equivalence.

{repair_note}

{handoff_note}

Metadata: `Metadata/codebook.csv`, `name_map.csv`, `value_labels.csv`, and `ipeds_metadata.json`. IPEDS year-specific descriptions, units and code definitions remain in the latter; conflicting definitions are not silently collapsed to one value label. Parquet fields embed compact labels and metadata references. Canonical Parquet retains original status strings and booleans; statistical/spreadsheet exports use reversible status/boolean codes described by value_labels.csv.

Annual exports under `Exports/` retain all columns. `load_stata.do` appends the annual Stata files and sets `xtset unitid year` (Stata SE/MP; this exceeds the smaller edition's variable limit). `load_csv.py` loads all years or a chosen set, preserves leading zeros, and recognizes only the reserved missing token `{NULL_TOKEN}`. Use the XLSX files for spreadsheet opening rather than letting a spreadsheet infer types from CSV. `Exports/string_nulls.parquet` distinguishes original null strings from observed empty strings in Stata/Excel. Excel uses 15 significant digits; only the existing documented cent-preserving FSA money binary tails and tiny name-similarity rounding are permitted and counted in validation. Numeric values beyond Excel's exact 15-significant-digit capacity are stored as exact numeric text and identified in `Checks/excel_precision/`; Excel formulas may ignore these text cells until converted, which can round them. Load the ledger's exact_numeric_text column as text. Parquet, CSV and Stata keep these values numeric and unchanged. No native Stata or Excel desktop execution was performed.

Source text containing characters unsupported by Excel is displayed with visible JSON escapes, with exact original text and a checksum in the same `Checks/excel_precision/` ledger. Oversized text, if present, uses an explicit ledger reference instead of truncation. The `exception_type` column separates these text exceptions from numeric precision cases. `restore_excel_text_cells` in `Scripts/fsa_ipeds_combined_excel.py` verifies the ledger and restores original strings after reading the spreadsheet. Canonical Parquet, CSV and Stata preserve the original text directly.

Reproduce from this self-contained bundle with Bash and curl on macOS/Linux/Windows WSL; the script installs isolated uv, Python 3.13.0 and the pinned environment listed in `environment-requirements.txt` without changing shell profiles:

```sh
bash reproduce.sh /path/to/new-empty-output
```

The isolated environment is stored in `.reproduction-runtime/` beside this bundle (set `FSA_RUNTIME` to choose another location). Internet is needed for initial environment setup, then the data build uses only frozen local inputs.

Use `--formats parquet` for only the two canonical panels and their complete metadata/diagnostics. The copied source inputs, definitions, code and output artifacts are hashed in `build_manifest.json`; original input files were not modified. Replay verifies all frozen Inputs/Metadata/Scripts and reference Panels and compares every cell/type in the newly written canonical panels with this reference bundle. Source paths in the manifest are historical provenance, not runtime requirements.
'''
    (out / 'README.md').write_text(readme)
    packages = ['pandas','pyarrow','numpy','python-dateutil','pytz','tzdata','six']
    from importlib.metadata import version
    (out / 'environment-requirements.txt').write_text('# Python ' + platform.python_version() + '\n' + ''.join(f'{name}=={version(name)}\n' for name in packages))
    manifest['source_checksums_rechecked_at_completion'] = True
    for item in manifest['source_records']:
        target = out / item['relative_path']
        if not target.is_file() or sha256(target) != item['sha256']:
            raise ValueError('Frozen source changed during build: ' + item['relative_path'])
    manifest['artifacts'] = inventory(out)
    write_json(out / 'build_manifest.json', manifest)
    # No consumers see the normal output folders until the entire build passes.
    promote_stage(out, output)
    print(json.dumps({'completed': True, 'output': str(output), 'rows': len(keys), 'columns': len(codebook), 'checks': len(checks)}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipeds-panel', type=Path)
    parser.add_argument('--ipeds-root', type=Path)
    parser.add_argument('--linkage-root', '--fsa-root', dest='fsa_root', type=Path, help='Downstream UNITID linkage release root; never the FSA-only source checkout')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--lineage-cache', type=Path)
    parser.add_argument('--resume-stage', type=Path, help='Resume a failed source build in its .building-* directory; reuse only hash-verified annual checkpoints')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--formats', default='parquet,csv,stata,excel')
    args = parser.parse_args()
    if not set(args.formats.split(',')) <= {'parquet','csv','stata','excel'}: parser.error('Unknown format')
    if not args.bundle and not all((args.ipeds_panel,args.ipeds_root,args.fsa_root)): parser.error('Supply source paths or --bundle')
    if args.resume_stage and not args.lineage_cache and (args.resume_stage / 'Inputs/ipeds/column_lineage.parquet').exists():
        parser.error('Resume must explicitly supply the lineage cache used by this source build')
    build(args)
