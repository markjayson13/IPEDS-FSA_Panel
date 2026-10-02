"""Lossless source metadata for the collection-anchored FSA–IPEDS merge.

This module reads the supplied v2 annual IPEDS dictionary, code dictionary and
actual wide-panel value lineage. It does not import code from another checkout.
Definitions and code labels are retained by year and source; conflicting labels
are never presented as a universal definition. IPEDS string codes remain strings.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


VALUE_COLUMNS = ["canonical_name", "portable_name", "code", "value", "label", "description"]
DERIVED = [
    ("unitid", "int64", "IPEDS institution identifier", "Shared institution key; copied from IPEDS UNITID. A matched UNITID does not certify common FSA and IPEDS reporting scope.", "identifier"),
    ("year", "int32", "IPEDS collection-year start", "Collection-year start of the IPEDS row. For example, 2023 means the 2023–24 collection cycle; individual components have different reference periods.", "calendar_year"),
    ("merge_status", "string", "FSA–IPEDS record matching status", "matched means the specified FSA UNITID and award-year record is present; ipeds_only means no such FSA row. Matching alone does not establish reporting-scope comparability.", "category"),
    ("has_ipeds_record", "bool", "IPEDS row is present", "True for each row of this IPEDS-universe left merge.", "boolean"),
    ("has_fsa_record", "bool", "FSA row is present", "Whether an FSA UNITID-year row exists for the specified offset. False does not mean zero aid or nonparticipation.", "boolean"),
    ("has_usable_fsa_record", "bool", "FSA row has a usable aid family", "FSA record-level usability flag copied from the matched source; null when no FSA record is present. Inspect individual family eligibility before analysis.", "boolean"),
    ("has_blocked_fsa_family", "bool", "FSA row has a blocked aid family", "FSA family-blocking flag copied from the matched source; null when no FSA record is present. Values are not allocated to resolve blocked reporting scope.", "boolean"),
    ("fsa_year_offset", "int32", "FSA award-year offset from IPEDS collection year", "Expected FSA award-year start minus IPEDS collection-year start: 0 in the collection-anchor view and -1 in the prior-aid view.", "years"),
    ("expected_fsa_award_year_start", "int32", "Expected FSA award-year start", "IPEDS collection-year start plus fsa_year_offset. Retained even where the expected FSA record is absent.", "calendar_year"),
    ("expected_fsa_award_year_end", "int32", "Expected FSA award-year end", "Expected FSA award-year start plus one; the award year runs July 1 through June 30.", "calendar_year"),
    ("ipeds_sfa_reference_year_start", "int32", "IPEDS SFA reference-year start", "IPEDS SFA primary aid reference period starts one year before the collection-year start in the supplied 2004–2023 source tables. Some SFA fields contain older historical periods; consult annual source metadata.", "calendar_year"),
    ("ipeds_sfa_reference_year_end", "int32", "IPEDS SFA reference-year end", "End year of the primary IPEDS SFA aid reference period, equal to the collection-year start for the supplied 2004–2023 sources.", "calendar_year"),
    ("fsa_sfa_periods_aligned", "bool", "FSA and primary IPEDS SFA periods align", "True only when a matched FSA row uses offset -1; false for a matched offset-0 row and null without an FSA row. Indicates timing only, not equal populations, monetary concepts or reporting scope.", "boolean"),
]
COMPANIONS = ["status_variable", "lower_bound_variable", "upper_bound_variable", "raw_token_variable", "partial_sum_variable", "reference_year_variable", "annual_dictionary_variable"]


def _text(value):
    return "" if value is None else str(value).strip()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _number(value):
    value = _text(value)
    return str(int(value.split(".")[0])) if re.fullmatch(r"\d+(?:\.0+)?", value) else value


def _unique(records):
    result, seen = [], set()
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source(path, *, hash_bytes=True):
    path = Path(path)
    result = {"path": str(path), "resolved_path": str(path.resolve()), "bytes": path.stat().st_size}
    if hash_bytes:
        result["sha256"] = _sha256(path)
    return result


def portable_names(names):
    """Stable, case-insensitively unique Stata names; never truncate silently."""
    if len(names) != len(set(names)):
        raise ValueError("Duplicate canonical names")
    proposed = {name: re.sub(r"[^a-zA-Z0-9_]", "_", name).lower() for name in names}
    counts = defaultdict(int)
    for name in proposed.values():
        counts[name] += 1
    result = {}
    for name in names:
        token = proposed[name]
        if not token or token[0].isdigit():
            token = "v_" + token
        if len(token) > 32 or counts[proposed[name]] > 1:
            token = token[:21] + "_" + hashlib.sha256(name.encode()).hexdigest()[:10]
        result[name] = token
    if len(set(result.values())) != len(names):
        raise ValueError("Portable-name hash collision")
    return result


def _lineage_records(path, years):
    """Read distinct metadata only from the large, lossless value-lineage table."""
    import duckdb

    columns = ["year", "analysis_column", "variable_id", "varname", "source_varnumber", "source_file", "access_table_name", "transformation_id", "lineage_role"]
    schema = pq.read_schema(path)
    missing = set(columns) - set(schema.names)
    if missing:
        raise ValueError(f"IPEDS value lineage lacks required fields: {sorted(missing)}")
    with duckdb.connect() as connection:
        connection.execute("SET threads=2")
        connection.execute("SET memory_limit='512MB'")
        placeholders = ",".join("?" for _ in years)
        return connection.execute(
            "SELECT DISTINCT " + ",".join(columns) +
            f" FROM read_parquet(?) WHERE year IN ({placeholders}) ORDER BY year, analysis_column, variable_id, source_file, access_table_name, transformation_id, lineage_role",
            [str(path), *years],
        ).fetch_arrow_table().to_pylist()


def _scope(row):
    return (int(row["year"]), _text(row.get("source_file")).upper(), _text(row.get("access_table_name")).upper())


def _build_ipeds_metadata(schema, root, years, *, lineage_records=None):
    root = Path(root)
    paths = {
        "dictionary": root / "Dictionary/v2/dictionary_lake.parquet",
        "codes": root / "Dictionary/v2/dictionary_codes.parquet",
        "lineage": root / "Checks/v2/wide_qc/qc_value_lineage.parquet",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"Required exact v2 IPEDS metadata source is missing: {path}")
    year_set = set(years)
    dictionary = [_json_safe(r) for r in pq.read_table(paths["dictionary"]).to_pylist() if int(r["year"]) in year_set]
    codes = [_json_safe(r) for r in pq.read_table(paths["codes"]).to_pylist() if int(r["year"]) in year_set]
    lineage = _lineage_records(paths["lineage"], years) if lineage_records is None else [dict(row) for row in lineage_records if int(row["year"]) in year_set]
    lineage = sorted(_unique(_json_safe(lineage)), key=lambda row: json.dumps(row, sort_keys=True, allow_nan=False))
    dictionary_by_id, codes_by_scope_name, lineage_by_column = defaultdict(list), defaultdict(list), defaultdict(list)
    for record in dictionary:
        dictionary_by_id[(int(record["year"]), _text(record.get("variable_id")))].append(record)
    for record in codes:
        codes_by_scope_name[(*_scope(record), _text(record.get("varname")).upper())].append(record)
    for record in lineage:
        lineage_by_column[record["analysis_column"]].append(record)
    variables, issues = [], []
    for field in schema:
        records = lineage_by_column[field.name]
        definitions = []
        unmatched_candidates = []
        for record in records:
            candidates = dictionary_by_id[(int(record["year"]), _text(record["variable_id"]))]
            exact = [candidate for candidate in candidates if _scope(candidate) == _scope(record)]
            definitions.extend(exact)
            if not exact:
                unmatched_candidates.extend(candidate for candidate in candidates
                                            if _scope(candidate)[:2] == _scope(record)[:2]
                                            and _scope(candidate) != _scope(record))
        definitions = _unique(definitions)
        unmatched_candidates = _unique(unmatched_candidates)
        matched_codes = _unique([
            code for row in definitions
            for code in codes_by_scope_name[(*_scope(row), _text(row.get("varname")).upper())]
            if not _number(code.get("varnumber")) or not _number(row.get("varnumber"))
            or _number(code.get("varnumber")) == _number(row.get("varnumber"))
        ])
        labels = sorted({_text(r.get("varTitle")) for r in definitions} - {""})
        descriptions = sorted({_text(r.get("longDescription")) for r in definitions} - {""})
        field_issues = []
        if field.name in {"UNITID", "year"}:
            controlled = DERIVED[0 if field.name == "UNITID" else 1]
            label, description = controlled[2:4]
            status = "controlled_panel_key"
        else:
            if unmatched_candidates:
                field_issues.append("dictionary_lineage_access_table_mismatch")
            if not records:
                field_issues.append("missing_actual_column_lineage")
            if not definitions:
                field_issues.append("missing_exact_year_source_dictionary")
            if len(labels) != 1:
                field_issues.append("varying_or_missing_variable_labels")
            if len(descriptions) != 1:
                field_issues.append("varying_or_missing_variable_definitions")
            label = labels[0] if len(labels) == 1 else field.name + " (year/source-specific definition)"
            description = descriptions[0] if len(descriptions) == 1 else (
                f"IPEDS source variable {field.name}. Definitions vary by source or year, or are unavailable; "
                "consult this variable's source_metadata, lineage_records and metadata_issues in ipeds_metadata.json. "
                "No universal definition is asserted."
            )
            if unmatched_candidates:
                description += (" Dictionary candidates share the annual variable identity and source family but disagree with the actual lineage's Access table; "
                                "candidate_source_metadata preserves these records without treating their scope as verified.")
            status = "source_metadata_available" if definitions else "requires_metadata_review"
        mapping = defaultdict(set)
        for code in matched_codes:
            mapping[_text(code.get("codevalue"))].add(_text(code.get("valuelabel")))
        conflicts = sorted(code for code, labels_for_code in mapping.items() if len(labels_for_code) != 1 or "" in labels_for_code)
        if conflicts:
            field_issues.append("year_or_source_specific_value_labels")
        categorical_definitions = [row for row in definitions if _text(row.get("DataType")).lower() in {"disc", "categorical", "category"}]
        label_scopes = {(*_scope(row), _text(row.get("varname")).upper()) for row in matched_codes}
        label_coverage = all((*_scope(row), _text(row.get("varname")).upper()) in label_scopes for row in categorical_definitions)
        if categorical_definitions and not label_coverage:
            field_issues.append("value_label_scope_coverage_incomplete")
        stable = [{"value": code, "label": next(iter(labels_for_code))} for code, labels_for_code in sorted(mapping.items())] if mapping and not conflicts and label_coverage else []
        variables.append({
            "name": field.name, "canonical_name": "ipeds__" + field.name, "label": label,
            "description": description, "storage_type": str(field.type), "metadata_status": status,
            "metadata_issues": field_issues, "source_metadata": definitions,
            "candidate_source_metadata": unmatched_candidates,
            "value_label_records": matched_codes, "stable_value_labels": stable,
            "lineage_records": records, "value_labels_applied_to_portable_data": False,
        })
        issues.extend({"variable": field.name, "code": issue} for issue in field_issues)
    timing = []
    for record in dictionary:
        if record.get("source_file") not in {"SFA", "SFA_P", "SFAV"}:
            continue
        table = _text(record.get("access_table_name"))
        year = int(record["year"])
        match = re.fullmatch(r"SFAV?(\d{2})(\d{2})(?:_P\d+)?", table)
        if not match or int(match[1]) != (year - 1) % 100 or int(match[2]) != year % 100:
            raise ValueError(f"Unverified IPEDS SFA year alignment: {year}, {table}")
        timing.append({"ipeds_collection_year": year, "source_file": record["source_file"], "access_table_name": table,
                       "primary_sfa_reference_year_start": year - 1, "primary_sfa_reference_year_end": year,
                       "evidence": "Annual dictionary Access table identifier; individual historical fields may refer to older periods."})
    timing = _unique(timing)
    return {
        "metadata_schema_version": "1.0", "years": years, "variables": variables, "issues": issues,
        "annual_sfa_timing": timing, "column_lineage": lineage,
        "column_lineage_sha256": hashlib.sha256(json.dumps(lineage, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "metadata_sources": {name: _source(path) for name, path in paths.items()},
        "lineage_method": "Exact distinct annual source-variable identities from supplied v2 qc_value_lineage.parquet; dictionary join by year, variable_id, source_file and access_table_name.",
        "code_label_method": "Preserve raw IPEDS values and all annual code records. Stable mappings are descriptive metadata only; no IPEDS code remapping or universal labels applied to portable observations.",
    }


def build_combined_metadata(ipeds_schema, fsa_schema, ipeds_root, fsa_export_root, years, *, lineage_records=None):
    """Return (complete codebook, export value-label table, IPEDS source metadata).

    Both merge views have the same schema. ``fsa_year_offset`` and the explicit
    expected/reference periods identify the selected view without rewriting
    source labels. FSA companion links are remapped to portable merged names.
    """
    years = sorted({int(year) for year in years})
    if not years:
        raise ValueError("At least one IPEDS collection year is required")
    export_root = Path(fsa_export_root)
    fsa_codebook = pd.read_csv(export_root / "codebook.csv", keep_default_na=False)
    fsa_values = pd.read_csv(export_root / "value_labels.csv", keep_default_na=False, dtype={"value": str})
    if fsa_codebook.canonical_name.duplicated().any() or set(fsa_codebook.canonical_name) != set(fsa_schema.names):
        raise ValueError("FSA codebook must enumerate exactly the supplied FSA schema")
    ipeds = _build_ipeds_metadata(ipeds_schema, ipeds_root, years, lineage_records=lineage_records)
    names = [r[0] for r in DERIVED] + ["ipeds__" + f.name for f in ipeds_schema] + ["fsa__" + f.name for f in fsa_schema]
    portable = portable_names(names)
    rows, value_rows = [], []

    def base(name, dtype, label, description, namespace, original_name, units=""):
        boolean = dtype == "bool"
        if len(description) > 32000:
            description = f"Full definition exceeds spreadsheet cell capacity; consult ipeds_metadata.json for source variable {original_name}."
        return {"canonical_name": name, "portable_name": portable[name],
                "variable_label": re.sub(r"\s+", " ", label).strip()[:80], "description": description,
                "units": units, "role": "derived_merge_metadata" if namespace == "merge" else "source_variable",
                "source_family": namespace, "original_dtype": dtype,
                "export_storage": "coded_category" if boolean else "string" if dtype in {"string", "large_string"} else "numeric",
                "category_kind": "boolean" if boolean else "", "metadata_quality": "explicit_definition",
                "source_namespace": namespace, "original_name": original_name,
                "dataset_name": "FSA–IPEDS collection-year panel", "metadata_version": "1.0",
                "missing_value_rule": "Source nulls and coded missing tokens are preserved; no zero filling. A missing FSA row is identified separately.",
                "precision_format": "" if dtype in {"string", "large_string"} else "%18.0g",
                **{companion: "" for companion in COMPANIONS}}

    def boolean_values(name):
        return [{"canonical_name": name, "portable_name": portable[name], "code": code,
                 "value": value, "label": label, "description": "Null remains distinct from both true and false."}
                for code, value, label in [(0, "false", "False"), (1, "true", "True")]]

    for name, dtype, label, description, units in DERIVED:
        rows.append(base(name, dtype, label, description, "merge", name, units))
        if dtype == "bool":
            value_rows.extend(boolean_values(name))
    for field, variable in zip(ipeds_schema, ipeds["variables"]):
        name = "ipeds__" + field.name
        units = "identifier" if field.name in {"UNITID", "OPEID"} else "calendar_year" if field.name == "year" else "not_asserted; consult annual source metadata"
        row = base(name, str(field.type), "IPEDS: " + variable["label"], variable["description"], "ipeds", field.name, units)
        row["metadata_quality"] = variable["metadata_status"]
        row["source_family"] = "|".join(sorted({_text(x.get("source_file")) for x in (variable["source_metadata"] or variable["lineage_records"])})) or ("ipeds_panel_key" if field.name in {"UNITID", "year"} else "unresolved")
        row["source_metadata_reference"] = "ipeds_metadata.json:variables[name=" + field.name + "]"
        row["source_years"] = "|".join(map(str, sorted({int(x["year"]) for x in variable["source_metadata"]})))
        row["source_lineage_years"] = "|".join(map(str, sorted({int(x["year"]) for x in variable["lineage_records"]})))
        row["metadata_issues_json"] = json.dumps(variable["metadata_issues"])
        row["reference_year_variable"] = portable["year"]
        rows.append(row)
        if pa.types.is_boolean(field.type):
            value_rows.extend(boolean_values(name))
    fsa_by_name = fsa_codebook.set_index("canonical_name").to_dict("index")
    fsa_alias = dict(zip(fsa_codebook.portable_name, fsa_codebook.canonical_name))
    fsa_alias.update({name: name for name in fsa_schema.names})
    for field in fsa_schema:
        original = fsa_by_name[field.name]
        name = "fsa__" + field.name
        row = {**original, "canonical_name": name, "portable_name": portable[name],
               "source_namespace": "fsa", "original_name": field.name,
               "original_dtype": str(field.type), "variable_label": ("FSA: " + original["variable_label"])[:80],
               "upstream_portable_name": original["portable_name"]}
        for companion in COMPANIONS:
            target = _text(original.get(companion))
            row[companion] = portable["fsa__" + fsa_alias[target]] if target in fsa_alias else ""
            if target and target not in fsa_alias:
                row["upstream_" + companion] = target
        rows.append(row)
    for record in fsa_values.to_dict("records"):
        name = "fsa__" + record["canonical_name"]
        if name not in portable:
            raise ValueError("FSA value labels reference a field outside the supplied schema")
        value_rows.append({**record, "canonical_name": name, "portable_name": portable[name]})
    codebook = pd.DataFrame(rows).fillna("")
    if list(codebook.canonical_name) != names or codebook.description.str.strip().eq("").any():
        raise ValueError("Incomplete combined codebook")
    ipeds["fsa_metadata_sources"] = {name: _source(export_root / name) for name in ["codebook.csv", "value_labels.csv"]}
    return codebook, pd.DataFrame(value_rows, columns=VALUE_COLUMNS), ipeds
