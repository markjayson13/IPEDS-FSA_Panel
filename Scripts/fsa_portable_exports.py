"""Labeled, reversible exports of a frozen UNITID release; never mutate its inputs."""
from __future__ import annotations

import gc
import hashlib
import json
import math
import platform
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fsa_export_metadata import build_export_metadata, encode_categorical_columns

NULL_TOKEN = "__FSA_NULL__"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REPO = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def _check_metadata(metadata):
    names = metadata.portable_name.tolist()
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]{0,31}", n) for n in names):
        raise ValueError("Portable variable names must be unique Stata-compatible names <=32 characters")
    if metadata.canonical_name.duplicated().any():
        raise ValueError("Duplicate canonical variable names")
    if metadata.variable_label.fillna("").str.len().eq(0).any() or metadata.variable_label.str.len().gt(80).any():
        raise ValueError("Every variable needs a nonempty label of at most 80 characters")
    if metadata.description.fillna("").str.strip().eq("").any():
        raise ValueError("Every variable needs a definition")


def prepare_export_frame(panel, metadata, value_labels):
    _check_metadata(metadata)
    if list(panel) != metadata.canonical_name.tolist():
        raise ValueError("Codebook must cover every input column in order")
    frame = encode_categorical_columns(panel, metadata, value_labels)
    for row in metadata.itertuples(index=False):
        if row.export_storage == "string":
            frame[row.portable_name] = frame[row.portable_name].astype("string")
        elif row.export_storage == "numeric":
            original = panel[row.canonical_name]
            # Retain doubles; do not downcast money to float32.
            frame[row.portable_name] = pd.to_numeric(original, errors="raise")
    return frame


def write_csv(frame, path):
    for name in frame:
        if not pd.api.types.is_numeric_dtype(frame[name]) and frame[name].dropna().eq(NULL_TOKEN).any():
            raise ValueError(f"Reserved CSV missing token collision in {name}")
    frame.to_csv(path, index=False, na_rep=NULL_TOKEN,
                 compression={"method": "gzip", "mtime": 0} if str(path).endswith(".gz") else None)


def read_csv(path, metadata):
    types = {r.portable_name: "string" if r.export_storage == "string" else
             ("Int64" if r.export_storage == "coded_category" or "int" in r.original_dtype.lower() else "float64")
             for r in metadata.itertuples(index=False)}
    return pd.read_csv(path, dtype=types, keep_default_na=False, na_values=[NULL_TOKEN], float_precision="round_trip")


def _stata_frame(frame, metadata):
    columns = {}
    for r in metadata.itertuples(index=False):
        s = frame[r.portable_name]
        if r.export_storage == "string":
            # Native Stata string missing is empty. A separately hashed null mask
            # preserves the source distinction between null and observed empty.
            columns[r.portable_name] = s.fillna("").astype(object)
        else:
            if pd.api.types.is_integer_dtype(s.dtype) and s.dropna().map(lambda v: abs(int(v)) > 2**53).any():
                raise ValueError(f"Unsafe Stata integer representation: {r.portable_name}")
            a = pd.to_numeric(s, errors="raise").to_numpy(dtype="float64", na_value=np.nan)
            if np.isinf(a).any() or (np.abs(a[np.isfinite(a)]) > 2**53).any():
                raise ValueError(f"Unsafe Stata numeric representation: {r.portable_name}")
            columns[r.portable_name] = a
    return pd.DataFrame(columns)


def write_stata(frame, path, metadata, value_labels):
    _check_metadata(metadata)
    labels = dict(zip(metadata.portable_name, metadata.variable_label))
    values = {name: dict(zip(g.code.astype(int), g.label)) for name, g in value_labels.groupby("portable_name")}
    stata = _stata_frame(frame, metadata)
    strings = [n for n in metadata.loc[metadata.export_storage.eq("string"), "portable_name"]
               if stata[n].str.len().max() > 120]
    stata.to_stata(path, write_index=False, version=118,
                   data_label="FSA UNITID x award year | frozen research release | see codebook",
                   variable_labels=labels, value_labels=values, convert_strl=strings)


def verify_frame(expected, actual, metadata, format_name):
    if list(expected) != list(actual) or len(expected) != len(actual):
        raise ValueError(f"{format_name}: shape/column order changed")
    excel_binary_rounding_cells = 0
    excel_similarity_rounding_cells = 0
    for row in metadata.itertuples(index=False):
        name = row.portable_name
        left, right = expected[name].reset_index(drop=True), actual[name].reset_index(drop=True)
        if row.export_storage == "string":
            if format_name in {"stata", "excel"}:
                same = left.astype("string").fillna("").equals(right.astype("string").fillna(""))
            else:
                same = left.astype("string").equals(right.astype("string"))
        else:
            a = pd.to_numeric(left, errors="raise").to_numpy(dtype="float64", na_value=np.nan)
            b = pd.to_numeric(right, errors="raise").to_numpy(dtype="float64", na_value=np.nan)
            same = np.array_equal(a, b, equal_nan=True)
            if not same and format_name == 'excel' and _is_money(row.units):
                equal = (a == b) | (np.isnan(a) & np.isnan(b))
                finite = np.isfinite(a) & np.isfinite(b)
                # Excel stores 15 significant digits. Permit only binary
                # roundoff of already cent-valued USD, never substantive loss.
                cents = ((np.round(a, 2) == np.round(b, 2)) &
                         (np.abs(a - np.round(a, 2)) <= 4 * np.spacing(np.maximum(np.abs(a), 1))))
                tiny = np.abs(a - b) <= 4 * np.spacing(np.maximum(np.abs(a), 1))
                same = bool((equal | (finite & cents & tiny)).all())
                excel_binary_rounding_cells += int((~equal).sum())
            elif not same and format_name == 'excel' and row.canonical_name.endswith('name_similarity'):
                equal = (a == b) | (np.isnan(a) & np.isnan(b))
                bounded = (a >= 0) & (a <= 1) & (b >= 0) & (b <= 1)
                same = bool((equal | (bounded & (np.abs(a - b) <= 5e-15))).all())
                excel_similarity_rounding_cells += int((~equal).sum())
        if not same:
            raise ValueError(f"{format_name}: roundtrip mismatch in {name}")
    return {"format": format_name, "rows": len(expected), "columns": len(expected.columns),
            "cells_checked": int(expected.size), "all_values_passed": True,
            "native_string_empty_null_equivalent": format_name in {"stata", "excel"},
            "excel_binary_rounding_cells_cents_preserved": excel_binary_rounding_cells,
            "excel_similarity_rounding_cells": excel_similarity_rounding_cells}


def _is_money(units):
    return 'USD' in str(units).upper() or 'dollar' in str(units).lower()


def verify_stata(path, expected, metadata, value_labels):
    with pd.io.stata.StataReader(path, convert_categoricals=False) as reader:
        if reader.variable_labels() != dict(zip(metadata.portable_name, metadata.variable_label)):
            raise ValueError("Stata variable labels changed")
        expected_labels = {n: dict(zip(g.code.astype(int), g.label)) for n, g in value_labels.groupby("portable_name")}
        if reader.value_labels() != expected_labels:
            raise ValueError("Stata value labels changed")
    # pandas 2.x value_labels() seeks beyond the strL table. A fresh reader is
    # needed for data, otherwise strL pointers can be returned as integers.
    with pd.io.stata.StataReader(path, convert_categoricals=False) as reader:
        # Chunking avoids holding a second full 1,312-column data set in memory.
        offset = 0
        while offset < len(expected):
            actual = reader.read(nrows=min(5000, len(expected) - offset))
            verify_frame(expected.iloc[offset:offset + len(actual)], actual, metadata, "stata")
            offset += len(actual)
    return {"format": "stata", "rows": len(expected), "columns": len(expected.columns),
            "cells_checked": int(expected.size), "all_values_passed": True,
            "variable_labels_passed": True, "value_labels_passed": True,
            "native_string_empty_null_equivalent": True}


def write_labeled_parquet(frame, path, metadata, value_labels, dataset_metadata):
    table = pa.Table.from_pandas(frame, preserve_index=False)
    by_name = metadata.set_index("portable_name").to_dict("index")
    fields = []
    for f in table.schema:
        m = by_name[f.name]
        vals = value_labels[value_labels.portable_name.eq(f.name)].to_dict("records")
        fields.append(f.with_metadata({b"fsa_variable": json.dumps(m, ensure_ascii=False).encode(),
                                       b"value_labels": json.dumps(vals, ensure_ascii=False).encode()}))
    schema_metadata = dict(table.schema.metadata or {})
    schema_metadata[b"fsa_dataset"] = json.dumps(dataset_metadata, ensure_ascii=False).encode()
    table = table.cast(pa.schema(fields, metadata=schema_metadata))
    pq.write_table(table, path, compression="zstd")


def _xml_text(value):
    value = str(value)
    if len(value) > 32767 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError("Excel text exceeds limits or contains an unsupported control character")
    return escape(value).replace('\r', '&#13;')


def _excel_col(i):
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _write_sheet(archive, number, names, rows, kinds=None):
    if len(names) > 16384:
        raise ValueError("Excel column limit exceeded")
    letters = [_excel_col(i + 1) for i in range(len(names))]
    kinds = kinds or ["string"] * len(names)
    with archive.open(f"xl/worksheets/sheet{number}.xml", "w", force_zip64=True) as f:
        f.write((f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}">'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 '<sheetFormatPr defaultColWidth="22"/><sheetData>').encode())
        def emit(rownum, values, header=False):
            cells = []
            for j, v in enumerate(values):
                if v is None or v is pd.NA or (not isinstance(v, str) and pd.isna(v)):
                    continue
                ref = f"{letters[j]}{rownum}"
                if header or isinstance(v, str):
                    cells.append(f'<c r="{ref}" t="inlineStr" s="{1 if header else 2}"><is><t xml:space="preserve">{_xml_text(v)}</t></is></c>')
                else:
                    n = float(v)
                    safe = float(format(n, '.15g'))
                    cent_roundoff = (kinds[j] == 'money' and math.isfinite(n) and
                                     round(n, 2) == round(safe, 2) and
                                     abs(n - round(n, 2)) <= 4 * math.ulp(max(abs(n), 1.0)) and
                                     abs(n - safe) <= 4 * math.ulp(max(abs(n), 1.0)))
                    similarity_roundoff = kinds[j] == 'similarity' and 0 <= n <= 1 and abs(n - safe) <= 5e-15
                    if not math.isfinite(n) or abs(n) > 999999999999999 or (safe != n and not cent_roundoff and not similarity_roundoff):
                        raise ValueError("Numeric cell cannot safely be represented in Excel")
                    n = safe
                    style = 3 if kinds[j] == "money" else 0
                    text = str(int(n)) if n.is_integer() else repr(n)
                    cells.append(f'<c r="{ref}" s="{style}"><v>{text}</v></c>')
            f.write((f'<row r="{rownum}">' + ''.join(cells) + '</row>').encode())
        emit(1, names, True)
        last = 1
        for last, values in enumerate(rows, 2):
            if last > 1048576:
                raise ValueError("Excel row limit exceeded")
            emit(last, values)
        f.write((f'</sheetData><autoFilter ref="A1:{letters[-1]}{last}"/></worksheet>').encode())


def write_excel(frame, path, metadata, value_labels, readme):
    """Stream valid OOXML without materializing millions of worksheet objects."""
    _check_metadata(metadata)
    sheets = ["Data", "Codebook", "ValueLabels", "README"]
    compact = [c for c in ("portable_name", "canonical_name", "variable_label", "description", "units", "role",
                           "source_family", "export_storage", "status_variable", "lower_bound_variable", "upper_bound_variable",
                           "metadata_quality") if c in metadata]
    styles = ('<styleSheet xmlns="' + NS + '"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,5)) + '</Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i,n in enumerate(sheets,1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,5)) + '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml', styles)
        kinds = ["money" if _is_money(r.units) else ('similarity' if r.canonical_name.endswith('name_similarity') else "numeric") for r in metadata.itertuples()]
        _write_sheet(z, 1, list(frame), frame.itertuples(index=False, name=None), kinds)
        _write_sheet(z, 2, compact, metadata[compact].fillna("").itertuples(index=False, name=None))
        _write_sheet(z, 3, list(value_labels), value_labels.itertuples(index=False, name=None))
        _write_sheet(z, 4, ["item", "value"], [(k, str(v)) for k,v in readme.items()])


def read_excel_data(path, columns):
    """Read all emitted data cells independently from their OOXML representation."""
    rows = []
    with zipfile.ZipFile(path) as z, z.open('xl/worksheets/sheet1.xml') as f:
        for _, element in ET.iterparse(f, events=("end",)):
            if element.tag != f"{{{NS}}}row":
                continue
            values = [None] * len(columns)
            for cell in element:
                ref = cell.attrib['r']
                col = 0
                for c in re.match(r"[A-Z]+", ref).group():
                    col = col * 26 + ord(c) - 64
                if cell.attrib.get('t') == 'inlineStr':
                    values[col - 1] = ''.join(cell.itertext())
                else:
                    value = cell.find(f'{{{NS}}}v')
                    values[col - 1] = float(value.text) if value is not None else None
            if element.attrib['r'] == '1':
                if values != list(columns):
                    raise ValueError("Excel header mismatch")
            else:
                rows.append(values)
            element.clear()
    return pd.DataFrame(rows, columns=columns)


def _write_helpers(out, metadata, value_labels):
    do = ['version 14', 'clear all', '* Run from the export package directory.',
          'use "fsa_unitid_panel.dta", clear', 'isid unitid award_year_start',
          'xtset unitid award_year_start', '* Stata string missing is empty; source null mask is stored separately.',
          '* Missing numeric aid is never zero. See program-specific status labels.',
          '* Monetary amounts are nominal USD; source recipients are not unique across programs.']
    for r in metadata.itertuples(index=False):
        if r.export_storage != 'string':
            fmt = '%16.2fc' if 'USD' in str(r.units).upper() or 'dollar' in str(r.units).lower() else '%16.0g'
            do.append(f'format {r.portable_name} {fmt}')
        for key, value in [('canonical_name', r.canonical_name), ('units', r.units), ('source_family', r.source_family),
                           ('definition', r.description)]:
            safe = str(value).replace('"', "'").replace('`', "'").replace('$', '').replace('\n',' ').replace('\r',' ')
            do.append(f'char {r.portable_name}[{key}] "{safe}"')
    do.extend(['notes _dta: Unbalanced FSA institution-year panel; annual identity is not proof of campus-exclusive aid.',
               'notes _dta: Read codebook.csv, value_labels.csv, README.md and export_manifest.json.',
               '* Save a new study-specific file after selecting the appropriate program and scope flags.'])
    (out/'load_stata.do').write_text('\n'.join(do)+'\n')
    py = '''"""Run from any directory: python load_csv.py. No automatic sample restriction."""
from pathlib import Path
import json
import pandas as pd
ROOT = Path(__file__).resolve().parent
def load(canonical_names=False, decode_categories=False):
    meta = pd.read_csv(ROOT / "codebook.csv", keep_default_na=False)
    types = {r.portable_name: "string" if r.export_storage == "string" else
             ("Int64" if r.export_storage == "coded_category" or "int" in r.original_dtype.lower() else "float64")
             for r in meta.itertuples(index=False)}
    data = pd.read_csv(ROOT / "fsa_unitid_panel.csv.gz", dtype=types, keep_default_na=False,
                       na_values=["__FSA_NULL__"], float_precision="round_trip")
    if data.duplicated(["unitid", "award_year_start"]).any():
        raise ValueError("Duplicate panel keys")
    if decode_categories:
        labels = pd.read_csv(ROOT / "value_labels.csv", keep_default_na=False)
        for name, group in labels.groupby("portable_name"):
            data[name] = data[name].map(dict(zip(group.code, group.value))).astype("string")
    if canonical_names:
        data = data.rename(columns=dict(zip(meta.portable_name, meta.canonical_name)))
    data.attrs["fsa_metadata"] = json.loads((ROOT / "dataset_metadata.json").read_text())
    return data
if __name__ == "__main__":
    frame = load()
    print(frame.shape)
'''
    (out/'load_csv.py').write_text(py)
    (out/'load_csv.R').write_text('''# Run with the export package as the working directory. Requires readr.
meta <- readr::read_csv("codebook.csv", show_col_types = FALSE, na = character())
spec <- paste(ifelse(meta$export_storage == "string", "c", "d"), collapse = "")
panel <- readr::read_csv("fsa_unitid_panel.csv.gz", col_types = spec,
                         na = "__FSA_NULL__", trim_ws = FALSE, name_repair = "minimal")
stopifnot(!anyDuplicated(panel[c("unitid", "award_year_start")]))
for (i in seq_len(nrow(meta))) {
  attr(panel[[meta$portable_name[i]]], "label") <- meta$variable_label[i]
  attr(panel[[meta$portable_name[i]]], "units") <- meta$units[i]
}
value_labels <- readr::read_csv("value_labels.csv", show_col_types = FALSE, na = character())
# Coded values are interpreted with value_labels; missing numbers are never zero.
''')


def export_release(root, output_dir, *, formats=("parquet", "csv", "stata", "excel")):
    root, out = Path(root).resolve(), Path(output_dir).resolve()
    exporter_paths = [REPO/'Scripts/fsa_portable_exports.py', REPO/'Scripts/fsa_export_metadata.py',
                      REPO/'Scripts/15_export_research_formats.py']
    exporter_hashes = {str(p.relative_to(REPO)): sha256(p) for p in exporter_paths}
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a new empty export directory; validated exports are not overwritten")
    source = root/'Panels/ipeds/unitid_research/fsa_unitid_award_year_panel.parquet'
    dictionary = source.parent/'dictionary.csv'
    manifest_path = root/'build/research_release_manifest.json'
    release = json.loads(manifest_path.read_text())
    if release.get('acceptance_passed') is not True:
        raise ValueError("Source research release did not pass acceptance")
    relative = str(source.relative_to(root))
    expected = next((a['sha256'] for a in release['artifacts'] if a['path'] == relative), None)
    source_hash = sha256(source)
    if expected != source_hash:
        raise ValueError("Source panel differs from the validated release")
    unit_manifest_path = source.parent/'manifest.json'
    um = json.loads(unit_manifest_path.read_text())
    if not um.get('all_conservation_passed') or um['output_sha256'].get('dictionary') != sha256(dictionary):
        raise ValueError("Unitid dictionary or conservation evidence does not match its manifest")
    out.mkdir(parents=True, exist_ok=True)
    for p in exporter_paths:
        target = out/'export_source'/p.name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(p,target)
    print('Reading frozen panel and building export metadata', flush=True)
    panel = pd.read_parquet(source)
    if panel.duplicated(['unitid', 'award_year_start']).any() or panel[['unitid','award_year_start']].isna().any().any():
        raise ValueError("Invalid panel keys")
    md, vl = build_export_metadata(panel, pd.read_csv(dictionary, keep_default_na=False))
    frame = prepare_export_frame(panel, md, vl)
    del panel
    gc.collect()
    dataset = {'title': 'FSA volume: IPEDS UNITID by award year', 'export_schema_version': '1.0.0',
               'created_utc': datetime.now(timezone.utc).isoformat(), 'source_release': release['release_version'],
               'source_release_manifest_sha256': sha256(manifest_path), 'source_panel_sha256': source_hash,
               'source_dictionary_sha256': sha256(dictionary), 'rows': len(frame), 'columns': len(frame.columns),
               'primary_key': ['unitid','award_year_start'], 'award_year_min': str(frame.award_year.min()),
               'award_year_max': str(frame.award_year.max()), 'currency': 'USD', 'price_basis': 'nominal; not inflation adjusted',
               'scope': 'Unbalanced annual institutional identity panel. Campus-exclusive aid allocation is not certified.',
               'missing_rule': 'Missing is not zero; consult each measure and family status and source scope.',
               'category_rule': 'Status and boolean numeric codes use value_labels.csv. Codes are nominal, not ordinal.',
               'text_missingness': 'CSV uses __FSA_NULL__; Stata/Excel empty and null require string_nulls.parquet for exact restoration.',
               'excel_precision': '15 significant digits: only cent-preserving monetary binary tails (<=4 ulps) and name-similarity scores (<=5e-15) may change; checked and counted in the export manifest. Other formats retain exact numeric values.',
               'csv_security': 'CSV text is preserved verbatim. Use typed loaders; use the XLSX export for spreadsheet opening.',
               'freshness': 'Re-export of frozen validated source bytes, not a new data collection or linkage build.'}
    (out/'dataset_metadata.json').write_text(json.dumps(dataset,indent=2)+'\n')
    md.to_csv(out/'codebook.csv',index=False)
    (out/'codebook.json').write_text(md.to_json(orient='records',indent=2,force_ascii=False)+'\n')
    md[['portable_name','canonical_name','variable_label']].to_csv(out/'name_map.csv',index=False)
    vl.to_csv(out/'value_labels.csv',index=False)
    nulls = pd.DataFrame({**{n:frame[n] for n in ['unitid','award_year_start']},
                         **{n:frame[n].isna() for n in md.loc[md.export_storage.eq('string'),'portable_name']}})
    nulls.to_parquet(out/'string_nulls.parquet',index=False)
    del nulls
    _write_helpers(out,md,vl)
    checks=[]
    if 'parquet' in formats:
        print('Writing labeled Parquet',flush=True)
        p=out/'fsa_unitid_panel.parquet'
        write_labeled_parquet(frame,p,md,vl,dataset)
        actual=pd.read_parquet(p)
        checks.append(verify_frame(frame,actual,md,'parquet'))
        del actual
    if 'csv' in formats:
        print('Writing and checking all CSV cells',flush=True)
        p=out/'fsa_unitid_panel.csv.gz'
        write_csv(frame,p)
        actual=read_csv(p,md)
        checks.append(verify_frame(frame,actual,md,'csv'))
        del actual
        schema={'@context':'http://www.w3.org/ns/csvw','url':p.name,'dialect':{'encoding':'utf-8','header':True},
                'tableSchema':{'primaryKey':['unitid','award_year_start'],'columns':[
                    {'name':r.portable_name,'titles':r.variable_label,'description':r.description,
                     'datatype':'string' if r.export_storage=='string' else ('integer' if r.export_storage=='coded_category' or 'int' in r.original_dtype.lower() else 'double'),
                     'null':[NULL_TOKEN]} for r in md.itertuples(index=False)]}}
        (out/'fsa_unitid_panel.csv-metadata.json').write_text(json.dumps(schema,indent=2)+'\n')
    if 'stata' in formats:
        print('Writing and checking labeled Stata 118',flush=True)
        p=out/'fsa_unitid_panel.dta'
        write_stata(frame,p,md,vl)
        checks.append(verify_stata(p,frame,md,vl))
    if 'excel' in formats:
        (out/'excel').mkdir()
        for year in sorted(frame.award_year.unique()):
            print(f'Writing and checking Excel {year}',flush=True)
            part=frame.loc[frame.award_year.eq(year)].reset_index(drop=True)
            p=out/'excel'/f'fsa_unitid_{year.replace("-","_")}.xlsx'
            write_excel(part,p,md,vl,{**dataset,'workbook_award_year':year,'workbook_rows':len(part),
                                    'full_codebook':'../codebook.json','status_labels':'ValueLabels worksheet'})
            actual=read_excel_data(p,list(part))
            check=verify_frame(part,actual,md,'excel');check['file']=str(p.relative_to(out))
            checks.append(check)
            del part,actual
    nulls=pd.read_parquet(out/'string_nulls.parquet')
    for n in md.loc[md.export_storage.eq('string'),'portable_name']:
        if not frame[n].isna().reset_index(drop=True).equals(nulls[n].reset_index(drop=True)):
            raise ValueError('String missingness mask changed')
    checks.append({'check':'string_null_masks','passed':True})
    shutil.copy2(manifest_path,out/'source_release_manifest.json')
    shutil.copy2(unit_manifest_path,out/'source_unitid_manifest.json')
    for n in (p.name for p in (REPO/'Documentation').glob('*.md')):
        target=out/'documentation'/n;target.parent.mkdir(exist_ok=True)
        shutil.copy2(REPO/'Documentation'/n,target)
    (out/'README.md').write_text('# FSA institution-year export package\n\n'
        f'{len(frame):,} rows; {len(frame.columns):,} variables; UNITID x award-year start is unique. All rows and columns are retained.\n\n'
        'Start with `fsa_unitid_panel.dta` and `load_stata.do` (Stata 14+), or `load_csv.py` / `load_csv.R`. '
        'Excel workbooks are partitioned by award year; each contains Data, Codebook, ValueLabels and README. '
        'Parquet embeds dataset and variable metadata. `codebook.json` retains full original dictionary metadata.\n\n'
        'All formats use the same portable variable names. `name_map.csv` maps them to canonical research names. '
        'Status/boolean fields use labeled numeric codes; these are nominal categories, not ordinal quantities. '
        'Missing aid is not zero. Do not count blocked-only rows as usable aid observations. '
        'Read program-family status and reporting-scope fields before selecting a research sample.\n\n'
        'OPEIDs are text. Avoid double-clicking CSV in Excel: use the supplied XLSX files or a typed import. '
        'CSV keeps source strings verbatim and uses `__FSA_NULL__` only for missing values. '
        'Stata uses native empty-string missing values; Excel renders missing text blank. '
        '`string_nulls.parquet` contains keys and original null masks for every text field, preserving null versus observed-empty distinctions.\n\n'
        'Currency is nominal USD. Recipient sums are not deduplicated students. Annual identity linkage does not certify campus-exclusive aid. '
        'This package exports the frozen release; it does not change upstream data or resolve remaining research limits.\n\n'
        'See `export_manifest.json` for source/output hashes and exhaustive format readback checks. '
        'Checks verify data and embedded Stata labels using Python; native Stata/Excel execution is separately reported.\n')
    if sha256(source)!=source_hash or sha256(dictionary)!=dataset['source_dictionary_sha256']:
        raise ValueError('Source changed during export')
    if exporter_hashes != {str(p.relative_to(REPO)):sha256(p) for p in exporter_paths}:
        raise ValueError('Exporter changed during the run; restart with a frozen implementation')
    outputs=[{'path':str(p.relative_to(out)),'bytes':p.stat().st_size,'sha256':sha256(p)} for p in sorted(out.rglob('*')) if p.is_file()]
    result={**dataset,'formats':list(formats),'checks':checks,'all_checks_passed':True,'native_stata_execution':False,
            'native_excel_execution':False,'outputs':outputs,'environment':{'python':platform.python_version(),'pandas':pd.__version__,'pyarrow':pa.__version__},
            'metadata_coverage': {'variables':len(md),'nonempty_labels':int(md.variable_label.str.len().gt(0).sum()),
                                  'nonempty_definitions':int(md.description.str.len().gt(0).sum()),
                                  'max_name_length':int(md.portable_name.str.len().max()),
                                  'max_label_length':int(md.variable_label.str.len().max()),
                                  'value_labels':len(vl)},
            'export_code':[{'path':path,'sha256':digest} for path,digest in exporter_hashes.items()]}
    (out/'export_manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'Validated export package: {out}',flush=True)
    return result
