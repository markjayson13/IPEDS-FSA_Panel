"""Auditable analysis projections of the frozen FSA/IPEDS research masters.

Decisions are explicit, source-hash bound, and independent of column order.
No row filtering, aggregation, campus allocation, or zero filling is permitted.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


SCRIPT_DIR = Path(__file__).resolve().parent
# The source repository owns one category-storage implementation. A generated
# analysis bundle receives that implementation beside this script so it can move
# independently of the checkout. Export helpers remain bound to --root below.
CATEGORY_HELPER = SCRIPT_DIR / "ipeds_category_storage.py"
if not CATEGORY_HELPER.is_file():
    CATEGORY_HELPER = SCRIPT_DIR.parents[1] / "Scripts/ipeds_category_storage.py"
sys.path.insert(0, str(CATEGORY_HELPER.parent))
sys.path.insert(0, str(SCRIPT_DIR))
from ipeds_category_storage import convert_numeric_category, ipeds_opeid_views


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    return pd.read_csv(path, keep_default_na=False)


def relative_reference(target, output):
    """A filesystem path based at the directory holding the analysis data."""
    return Path(os.path.relpath(Path(target).resolve(), Path(output).resolve())).as_posix()


def source_references(root, output, source_relative, *, include_handoff=False):
    """Preserve the consumed bundle identity for sibling and external outputs.

    The same object is embedded in Parquet metadata, the root manifest, and the
    Checks record. Paths always use the analysis data directory as their base,
    not the directory of a particular sidecar. The source-in-bundle name stays
    available for interpreting the unchanged recipe's source-hash inventory.
    """
    relative = Path(source_relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Source must be a relative path inside the frozen bundle')
    references = {
        'path_base': 'analysis_data_directory',
        'source_bundle': relative_reference(root, output),
        'source': relative_reference(root / relative, output),
        'source_in_bundle': relative.as_posix(),
        'annual_metadata': relative_reference(root / 'Metadata/ipeds_metadata.json', output),
    }
    if include_handoff:
        references['canonical_ipeds_handoff'] = relative_reference(
            root / 'Inputs/ipeds/final_labeled_panel/binding.json', output)
    return references


def coalesce_checked(frame, sources):
    """Combine equivalent definitions only; never use precedence to hide conflict."""
    out = frame[sources[0]].copy()
    overlap_cells = 0
    for name in sources[1:]:
        other = frame[name]
        both = out.notna() & other.notna()
        mismatch = both & ~out.eq(other).fillna(False)
        if mismatch.any():
            raise ValueError(f"Conflicting observed values in {sources}: {int(mismatch.sum())}")
        overlap_cells += int(both.sum())
        # Preserve pandas nullable numeric types when the source dtypes agree.
        out = out.combine_first(other)
    for name in sources:
        observed = frame[name].notna()
        if not out[observed].eq(frame.loc[observed, name]).fillna(False).all():
            raise AssertionError(f"Coalescing lost a source observation: {name}")
    return out, overlap_cells


def apply_missing_rules(frame, rules, keys):
    """Only explicit variable/year/source rules; preserve a cell-level reversal ledger."""
    ledger = []
    counts = []
    from ipeds_analysis_missing import mask_for_code
    year_indices = {int(year): idx for year, idx in frame.groupby("year", sort=False).groups.items()}
    for rule in rules:
        name = rule["column"]
        if name not in frame:
            continue
        values = frame[name]
        annual = values.loc[year_indices.get(rule["year"], [])]
        mask = annual.index[mask_for_code(annual, rule["code"])]
        n = len(mask)
        if not n:
            continue
        cells = frame.loc[mask, keys].copy()
        cells["source_column"] = name
        cells["original_value"] = values.loc[mask].astype("string")
        cells["reason"] = rule["label"]
        cells["rule_id"] = rule["rule_id"]
        ledger.append(cells)
        frame.loc[mask, name] = pd.NA
        counts.append({"source_column": name, "year": rule["year"], "code": rule["code"],
                       "label": rule["label"], "rule_id": rule["rule_id"], "cells": n})
    if ledger:
        ledger = pd.concat(ledger, ignore_index=True)
        if ledger.duplicated(keys + ["source_column"]).any():
            raise AssertionError("A source cell was recoded more than once")
    else:
        ledger = pd.DataFrame(columns=keys + ["source_column", "original_value", "reason", "rule_id"])
    return ledger, counts


def alias(name):
    if name.startswith("fsa__"):
        name = name[5:]
    if name.startswith("ipeds__"):
        parts = name[7:].lower().split("__")
        return "ipeds_" + "_".join(parts[:2])
    prefixes = {"loan_direct_harmonized__": "dlh_", "loan_ffel_harmonized__": "ffelh_",
                "loan_direct__": "dl_", "loan_ffel__": "ffel_", "grant__": "grant_",
                "campus__": "campus_", "loan__": "loan_"}
    for old, new in prefixes.items():
        if name.startswith(old):
            name = new + name[len(old):]
            break
    substitutions = {
        "ipeds_identity_strict_contemporaneous": "identity_sameyear",
        "ipeds_identity_uses_historical_relation": "identity_historical",
        "ipeds_component_scope_review_required": "scope_review",
        "fsa_descriptor_review_required": "descriptor_review",
        "ipeds_twelve_month_enrollment_scope": "enroll12_scope",
        "ipeds_fall_enrollment_scope": "enroll_scope",
        "ipeds_student_aid_scope": "aid_scope", "ipeds_completions_scope": "completion_scope",
        "ipeds_finance_scope": "finance_scope", "ipeds_reporting_scope": "reporting_scope",
        "ipeds_resolution_status": "identity_status", "source_report_present": "report_available",
        "unitid_record_status": "record_status", "recipient_count_sum": "recip_sum",
        "loans_originated_amt": "origin_amt", "loans_originated_n": "origin_n",
        "unsubsidized": "unsub", "subsidized": "sub", "undergraduate": "ug",
        "graduate": "grad", "disbursements_amt": "disb_amt", "disbursements_n": "disb_n",
        "disbursements": "disb_amt", "recipients": "recip_n", "federal_award": "fed_award",
    }
    for old, new in substitutions.items():
        name = name.replace(old, new)
    return name.replace("__", "_").lower()


def assign_names(specs):
    seen = set()
    for spec in specs:
        stem = alias(spec.get("target", spec["sources"][0]))
        candidate = stem[:32]
        index = 1
        while candidate in seen:
            index += 1
            suffix = f"_v{index}"
            candidate = stem[:32-len(suffix)] + suffix
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", candidate):
            raise ValueError(f"Invalid analysis variable name {candidate}")
        spec["name"] = candidate
        seen.add(candidate)
    return specs


FAMILY_PREFIXES = ("grant", "campus", "loan_direct", "loan_ffel")
FAMILY_FLAGS = (
    "source_report_present", "unitid_record_status", "ipeds_resolution_status",
    "ipeds_identity_strict_contemporaneous", "ipeds_identity_uses_historical_relation",
    "ipeds_reporting_scope", "ipeds_component_scope_review_required", "ipeds_student_aid_scope",
    "ipeds_finance_scope", "ipeds_fall_enrollment_scope", "ipeds_completions_scope",
    "ipeds_twelve_month_enrollment_scope", "fsa_descriptor_review_required",
    "ipeds_year", "ipeds_anchor", "idx_sfa", "idx_f", "idx_ef", "idx_c", "idx_e12",
)
ADMIN_IPEDS = {"year", "UNITID", "ADDR", "ADMINURL", "ADMTELE", "APPLURL", "ATHURL",
              "CHFNM", "CHFTITLE", "DISAURL", "FAIDURL", "FAXTELE", "FINTELE", "GENTELE",
              "GRDISURL", "MISSIONURL", "NPRICURL", "VETURL", "WEBADDR"}


def fsa_selection(codebook):
    selected = set(codebook.loc[codebook.role.isin(["measure", "identifier_or_time", "descriptor"]), "canonical_name"])
    selected.update(codebook.loc[codebook.role.eq("measure"), "status_variable"])
    selected.add("loan__program_scope")
    selected.update(f"{family}__{flag}" for family in FAMILY_PREFIXES for flag in FAMILY_FLAGS)
    # These four descriptors are harmonizer copies, not additional institution characteristics.
    selected.difference_update(f"loan__{x}" for x in ("school", "state", "zip_code", "school_type"))
    unknown = selected.difference(codebook.canonical_name)
    if unknown:
        raise ValueError(f"Unknown selected FSA columns: {unknown}")
    return selected


def make_specs(codebook, standalone, decisions, fsa_cb):
    selected_fsa = fsa_selection(fsa_cb)
    selected_fsa.difference_update(decisions.get("fsa_omit_status_columns", []))
    selected = []
    for row in codebook.itertuples(index=False):
        col = row.canonical_name
        if standalone:
            keep = col in selected_fsa
        elif row.source_namespace == "ipeds":
            keep = row.original_name not in ADMIN_IPEDS
        elif row.source_namespace == "fsa":
            keep = col[5:] in selected_fsa and col != "fsa__unitid"
        else:
            keep = True
        if keep:
            selected.append(col)
    selected_set = set(selected)
    groups = []
    removed = {}
    for original in decisions["groups"]:
        if original["namespace"] == "ipeds" and standalone:
            continue
        group = dict(original)
        sources = group["sources"]
        if original["namespace"] == "fsa" and not standalone:
            sources = ["fsa__" + col for col in sources]
            if "year_rules" in group:
                group["year_rules"] = [{**r, "source": "fsa__" + r["source"]} for r in group["year_rules"]]
        group["sources"] = sources
        if not set(sources).issubset(selected_set):
            raise ValueError(f"Consolidation source omitted from projection: {sources}")
        for col in sources:
            if col in removed:
                raise ValueError(f"Column used in more than one consolidation: {col}")
            removed[col] = group
        groups.append(group)
    emitted = set()
    specs = []
    for col in selected:
        if col in removed:
            group = removed[col]
            target = group["target"]
            if target not in emitted:
                specs.append(group)
                emitted.add(target)
        else:
            specs.append({"sources": [col], "target": col, "method": "identity",
                          "reason": "Retained substantive measure, identifier, descriptor or interpretation flag"})
    if not standalone:
        specs.extend([
            {"sources": ["ipeds__OPEID"], "target": "ipeds_opeid8", "method": "opeid8", "reason": "Strict eight-digit source ID; alphanumeric NCES reporting entities are not assigned a numeric FSA ID"},
            {"sources": ["ipeds__OPEID"], "target": "ipeds_opeid_kind", "method": "opeid_kind", "reason": "Distinguish numeric source format, alphanumeric reporting branches, source sentinels and missing IDs"},
        ])
    return assign_names(specs)


def metadata_for_specs(specs, original_cb, original_labels):
    indexed = original_cb.set_index("canonical_name")
    source_map = {}
    for spec in specs:
        for source in spec["sources"]:
            source_map.setdefault(source, spec["name"])
    rows, label_rows = [], []
    portable_to_source = dict(zip(original_cb.portable_name, original_cb.canonical_name))
    reference_map = dict(source_map)
    reference_map.update({r.portable_name: source_map[r.canonical_name] for r in original_cb.itertuples(index=False) if r.canonical_name in source_map})
    for spec in specs:
        source = spec.get("metadata_source", spec["sources"][0])
        if source not in indexed.index and "fsa__" + source in indexed.index:
            source = "fsa__" + source
        row = indexed.loc[source].to_dict()
        row["canonical_name"] = row["portable_name"] = spec["name"]
        row["analysis_sources_json"] = json.dumps(spec["sources"])
        row["analysis_method"] = spec["method"]
        row["analysis_reason"] = spec["reason"]
        row["analysis_year_rules_json"] = json.dumps(spec.get("year_rules", []))
        companion_fields = ("status_variable", "lower_bound_variable", "upper_bound_variable", "partial_sum_variable", "raw_token_variable")
        row["analysis_companions_json"] = json.dumps({c: {k: portable_to_source.get(indexed.loc[c].get(k, ""), indexed.loc[c].get(k, "")) for k in companion_fields} for c in spec["sources"]})
        row["original_canonical_name"] = source
        if len(spec["sources"]) > 1:
            row["description"] += " Analysis consolidation: " + spec["reason"] + ". All annual definitions remain in the master metadata."
        for ref in ("status_variable", "reference_year_variable"):
            row[ref] = reference_map.get(row.get(ref, ""), row.get(ref, ""))
        for ref in ("lower_bound_variable", "upper_bound_variable", "partial_sum_variable", "raw_token_variable"):
            if row.get(ref):
                row[ref] = "master:" + portable_to_source.get(row[ref], row[ref]) if len(spec["sources"]) == 1 else "See analysis_companions_json and analysis_year_rules_json"
        tables = original_labels.loc[original_labels.canonical_name.isin(spec["sources"])].copy()
        if spec["method"] == "opeid8":
            row.update(variable_label="IPEDS OPEID: eight numeric digits (format only, not a verified FSA match)",
                       description=spec["reason"], original_dtype="string", export_storage="string", category_kind="", role="identifier")
            tables = tables.iloc[0:0]
        elif spec["method"] == "opeid_kind":
            row.update(variable_label="IPEDS OPEID source format and availability", description=spec["reason"],
                       original_dtype="Int8", export_storage="numeric", category_kind="", role="quality_status", units="nominal_code")
            tables = pd.DataFrame([{"canonical_name":spec["name"],"portable_name":spec["name"],"code":code,"value":str(code),"label":label,"description":label}
                                   for code,label in [(1,"Eight numeric digits; format compatibility only"),(2,"IPEDS alphanumeric reporting-branch ID"),(3,"Source not applicable (-2)"),(4,"Source missing")]])
        elif any(indexed.loc[c].get("analysis_value_label_status", "") == "year_specific" for c in spec["sources"]):
            tables = tables.iloc[0:0]
            row["description"] += " Categorical code meanings vary by year; consult annual metadata."
        if len(tables):
            unique = tables[["code", "value", "label"]].drop_duplicates()
            conflicting = unique.groupby("code").label.nunique().gt(1).any() or unique.groupby("value").code.nunique().gt(1).any()
            if conflicting:
                if row["category_kind"]:
                    by_token = tables.groupby("value").label.nunique()
                    if by_token.gt(1).any():
                        raise ValueError(f"Conflicting category token meanings in {spec['name']}")
                    tables = tables.sort_values("value").drop_duplicates("value").copy()
                    tables["code"] = np.arange(1, len(tables) + 1)
                    tables["canonical_name"] = tables["portable_name"] = spec["name"]
                    label_rows.append(tables)
                    rows.append(row)
                    continue
                # Codes with annual semantic variation cannot have a single Stata value label.
                tables = tables.iloc[0:0]
                row["description"] += " Value labels vary by year/source; consult annual metadata."
            else:
                tables = tables.drop_duplicates(["code", "value", "label"])
                tables["canonical_name"] = tables["portable_name"] = spec["name"]
                label_rows.append(tables)
        rows.append(row)
    metadata = pd.DataFrame(rows).fillna("")
    labels = pd.concat(label_rows, ignore_index=True) if label_rows else original_labels.iloc[0:0].copy()
    return metadata, labels, source_map



def project_spec(source, spec, standalone):
    if spec["method"] in {"opeid8", "opeid_kind"}:
        views = ipeds_opeid_views(source[spec["sources"][0]])
        if spec["method"] == "opeid8":
            return views.ipeds_opeid8_numeric, 0
        mapping = {"numeric8_format_only_not_verified_match":1,"ipeds_alpha_reporting_branch_not_exact_fsa_id":2,"not_applicable":3,"source_missing":4}
        out = views.ipeds_opeid_format_status.map(mapping)
        if out.isna().any():
            raise ValueError("Unrecognized IPEDS source OPEID requires review")
        return out.astype("Int8"), 0
    if spec["method"] != "year_partition":
        return coalesce_checked(source, spec["sources"])
    year_name = "award_year_start" if standalone else "fsa__award_year_start"
    years = source[year_name]
    masks = {}
    for rule in spec["year_rules"]:
        masks[rule["source"]] = years.between(rule["min"], rule["max"]).fillna(False)
    if set(masks) != set(spec["sources"]):
        raise ValueError("Every partitioned source needs an explicit year range")
    covered = sum(mask.astype(int) for mask in masks.values())
    if (covered > 1).any() or ((covered == 0) & years.notna()).any():
        raise ValueError("Year partitions overlap or omit an observed award year")
    is_status = all(n.endswith("__status") for n in spec["sources"])
    series = []
    for name in spec["sources"]:
        if not is_status and (source[name].notna() & ~masks[name]).any():
            raise ValueError(f"Observed measure lies outside its approved source years: {name}")
        series.append(source[name].where(masks[name]))
    return coalesce_checked(pd.concat(series, axis=1), spec["sources"])


def write_loader(path, stem, time_name):
    path.write_text(f'''version 14
* Run from the Analysis directory. Native file has variable and value labels.
use "{stem}.dta", clear
isid unitid {time_name}
xtset unitid {time_name}
notes _dta: Analysis projection; read README.md and Metadata/{stem}_codebook.csv.
notes _dta: Missing aid is not zero. Annual identity does not certify campus aid scope.
notes _dta: Nominal dollars; loan recipient-count sums are not unique borrowers.
''')


def build_one(root, output, source_relative, stem, standalone, decisions, missing_rules, helpers):
    print(f"Building {stem}", flush=True)
    source_path = root / source_relative
    metadata_dir = root / ("Inputs/fsa" if standalone else "Metadata")
    cb = read_csv(metadata_dir / "codebook.csv")
    labels = read_csv(metadata_dir / "value_labels.csv")
    fsa_cb = cb if standalone else read_csv(root / "Inputs/fsa/codebook.csv")
    specs = make_specs(cb, standalone, decisions, fsa_cb)
    source_cols = list(dict.fromkeys(c for spec in specs for c in spec["sources"]))
    keys = ["unitid", "award_year_start" if standalone else "year"]
    source = pq.read_table(source_path, columns=source_cols).to_pandas()
    if source[keys].isna().any().any() or source.duplicated(keys).any():
        raise AssertionError(f"Invalid master keys: {stem}")
    original_keys = source[keys].copy()
    if standalone:
        ledger, missing_counts = pd.DataFrame(), []
    else:
        ledger, missing_counts = apply_missing_rules(source, missing_rules, keys)
        category_policy = json.loads((output / "Decisions/ipeds_numeric_categories.json").read_text())
        for rule in category_policy["allowlist"]:
            name = rule["source_column"]
            if name not in source:
                continue
            source[name] = convert_numeric_category(source[name], rule)
            select = cb.canonical_name.eq(name)
            cb.loc[select, "original_dtype"] = rule["target_storage"]
            cb.loc[select, "export_storage"] = "numeric"
            cb.loc[select, "category_kind"] = ""
            cb.loc[select, "analysis_value_label_status"] = "stable" if rule["value_label_status"] == "stable_exact_annual_meanings" else "year_specific"
            labels = labels.loc[~labels.canonical_name.eq(name)]
            if rule["stable_value_labels"]:
                new = [{"canonical_name":name,"portable_name":name,"code":v["value"],"value":str(v["value"]),"label":v["label"],"description":"Stable annual source code meaning"} for v in rule["stable_value_labels"]]
                labels = pd.concat([labels,pd.DataFrame(new)],ignore_index=True)
        cb = cb.fillna("")
    projected, group_checks = {}, []
    for spec in specs:
        value, overlaps = project_spec(source, spec, standalone)
        projected[spec["name"]] = value
        if len(spec["sources"]) > 1:
            group_checks.append({"target": spec["name"], "sources": spec["sources"],
                                 "observed_overlaps_checked": overlaps, "conflicts": 0,
                                 "nonmissing_output": int(value.notna().sum())})
    panel = pd.DataFrame(projected)
    pd.testing.assert_frame_equal(panel[keys], original_keys, check_dtype=False)
    source_shape = pq.ParquetFile(source_path).metadata
    if len(panel) != source_shape.num_rows:
        raise AssertionError("Projection filtered rows")
    metadata, value_labels, source_map = metadata_for_specs(specs, cb, labels)
    upstream_label_audit = None
    upstream_path = root / "Inputs/ipeds/final_labeled_panel/panel_clean_prch_2004_2023.dta.metadata.json.gz"
    if not standalone and upstream_path.is_file():
        import gzip
        from ipeds_final_metadata import enrich_analysis_metadata
        with gzip.open(upstream_path, "rt", encoding="utf-8") as stream:
            upstream = json.load(stream)
        metadata, value_labels, upstream_label_audit = enrich_analysis_metadata(metadata, value_labels, upstream)
        del upstream
    frame = helpers.prepare_export_frame(panel, metadata, value_labels)
    # Every source column has a disposition, including deliberately omitted audit detail.
    selected = set(source_map)
    crosswalk = []
    for row in cb.itertuples(index=False):
        col = row.canonical_name
        entry = {"source_column": col, "analysis_column": source_map.get(col, ""),
                 "source_role": row.role, "source_label": row.variable_label}
        if col in selected:
            entry["disposition"] = "retained_or_consolidated"
        elif not standalone and col.startswith("ipeds__"):
            entry["disposition"] = "administrative_contact_or_duplicate_key"
        else:
            entry["disposition"] = "audit_detail_in_master"
        crosswalk.append(entry)
    md = output / "Metadata"
    md.mkdir(exist_ok=True)
    # Avoid reproducing megabytes of raw dictionary JSON in each compact codebook.
    keep_meta = [c for c in metadata if not c.startswith("source_dictionary__") and c != "original_dictionary_json"]
    metadata[keep_meta].to_csv(md / f"{stem}_codebook.csv", index=False)
    value_labels.to_csv(md / f"{stem}_value_labels.csv", index=False)
    pd.DataFrame(crosswalk).to_csv(md / f"{stem}_column_crosswalk.csv", index=False)
    if len(ledger):
        ledger["analysis_column"] = ledger.source_column.map(source_map)
        ledger.to_parquet(md / f"{stem}_missing_code_ledger.parquet", index=False, compression="zstd")
    string_names = metadata.loc[metadata.export_storage.eq("string"), "portable_name"]
    null_mask = frame[list(string_names)].isna()
    null_mask.insert(0, keys[1], frame[keys[1]])
    null_mask.insert(0, keys[0], frame[keys[0]])
    null_mask.to_parquet(md / f"{stem}_string_nulls.parquet", index=False, compression="zstd")
    dataset_metadata = {"title": stem, "primary_key": keys, "rows": len(frame), "columns": len(frame.columns),
                        **source_references(root, output, source_relative,
                                            include_handoff=upstream_label_audit is not None),
                        "source_sha256": sha256(source_path),
                        "all_source_rows_retained": True, "sentinel_recode_cells": len(ledger),
                        "scope": "Annual institution identity; aid scope not certified",
                        "missing": "Numeric null is not zero; statuses and missing-code ledger preserve reasons"}
    if upstream_label_audit is not None:
        dataset_metadata["upstream_labels"] = "full-panel-labels-v1; source string tokens and analysis storage retained"
    checks = {"dataset": dataset_metadata, "consolidations": group_checks, "missing_recodes": missing_counts}
    if upstream_label_audit is not None:
        checks["upstream_label_enrichment"] = upstream_label_audit
    del source, panel, projected
    gc.collect()
    helpers.write_labeled_parquet(frame, output / f"{stem}.parquet", metadata, value_labels, dataset_metadata)
    readback = pd.read_parquet(output / f"{stem}.parquet")
    checks["parquet"] = helpers.verify_frame(frame, readback, metadata, "parquet")
    del readback
    print(f"{stem}: Parquet verified; writing CSV/Stata ({frame.shape})", flush=True)
    helpers.write_csv(frame, output / f"{stem}.csv.gz")
    readback = helpers.read_csv(output / f"{stem}.csv.gz", metadata)
    checks["csv"] = helpers.verify_frame(frame, readback, metadata, "csv")
    del readback
    stata = helpers._stata_frame(frame, metadata)
    long_strings = [n for n in string_names if stata[n].str.len().max() > 120]
    stata_labels = {n: dict(zip(g.code.astype(int), g.label)) for n, g in value_labels.groupby("portable_name")}
    stata.to_stata(output / f"{stem}.dta", write_index=False, version=118,
                   data_label=f"{stem} | analysis view | see README"[:80],
                   variable_labels=dict(zip(metadata.portable_name, metadata.variable_label)),
                   value_labels=stata_labels, convert_strl=long_strings,
                   time_stamp=datetime(2026, 9, 29))
    del stata
    gc.collect()
    checks["stata"] = verify_stata_exact(output / f"{stem}.dta", frame, metadata, value_labels, helpers)
    write_loader(output / f"load_{stem}.do", stem, keys[1])
    (output / "Checks" / f"{stem}.json").write_text(json.dumps(checks, indent=2))
    print(f"{stem}: all formats and labels verified", flush=True)
    del frame
    gc.collect()
    return dataset_metadata



def verify_stata_exact(path, expected, metadata, value_labels, helpers):
    with pd.io.stata.StataReader(path, convert_categoricals=False) as reader:
        reader._ensure_open()
        if reader._nobs != len(expected):
            raise ValueError("Stata header observation count differs from source")
        if reader.variable_labels() != dict(zip(metadata.portable_name, metadata.variable_label)):
            raise ValueError("Stata variable labels changed")
        wanted = {n: dict(zip(g.code.astype(int), g.label)) for n, g in value_labels.groupby("portable_name")}
        if reader.value_labels() != wanted:
            raise ValueError("Stata value labels changed")
    with pd.io.stata.StataReader(path, convert_categoricals=False) as reader:
        offset = 0
        while offset < len(expected):
            n = min(5000, len(expected) - offset)
            actual = reader.read(nrows=n)
            if len(actual) != n:
                raise ValueError("Stata data truncated or empty read chunk")
            helpers.verify_frame(expected.iloc[offset:offset+n], actual, metadata, "stata")
            offset += n
    return {"format":"stata", "rows":len(expected), "columns":len(expected.columns),
            "cells_checked":int(expected.size), "all_values_passed":True,
            "variable_labels_passed":True, "value_labels_passed":True,
            "exact_observation_count_passed":True, "native_string_empty_null_equivalent":True}


def write_stata_modules(output, stems=("fsa_ipeds_analysis", "fsa_ipeds_aid_analysis")):
    themes = {
        "finance": {"F_F", "F_FA", "DRVF"},
        "student_aid": {"SFA", "SFA_P", "SFAV"},
        "enrollment": {"DRVEF", "EFFY_HS", "EFD", "EFIA", "EFDS", "EFF"},
        "outcomes": {"GR200", "GR_L", "DRVGR", "DRVOM", "DRVC", "C_B", "GR_GENDER", "DFR"},
        "admissions": {"ADM", "DRVADM"},
        "staff": {"SAL_NIS", "DRVHR"},
        "institution": {"IC", "IC_AY", "IC_PY", "DRVIC", "AL", "DRVAL", "ICMISSION", "CUSTOMCG"},
    }
    modules = []
    for stem in stems:
        metadata = read_csv(output / "Metadata" / f"{stem}_codebook.csv")
        common = []
        for r in metadata.itertuples(index=False):
            families = set(str(r.source_family).split("|"))
            if r.source_namespace != "ipeds" or families & {"HD", "FLAGS", "ipeds_panel_key"}:
                common.append(r.portable_name)
        covered = set(common)
        for theme, families in themes.items():
            names = set(common)
            names.update(r.portable_name for r in metadata.itertuples(index=False)
                         if set(str(r.source_family).split("|")) & families)
            if len(names) > 2048:
                raise ValueError(f"Stata BE module {theme} exceeds 2048 columns")
            ordered = [c for c in metadata.portable_name if c in names]
            covered.update(names)
            modules.append({"dataset":stem,"module":theme,"variables":len(ordered),"names":ordered})
            lines = ["version 14", "* Run from the Analysis directory; loads only a Stata BE compatible subset.",
                     "* Every row is retained; all FSA measures and common institution/scope fields included.", "use ///"]
            lines.extend("    " + " ".join(ordered[i:i+8]) + " ///" for i in range(0,len(ordered),8))
            lines.extend([f'    using "{stem}.dta", clear', "isid unitid year", "xtset unitid year"])
            (output / f"load_{stem}_{theme}.do").write_text("\n".join(lines) + "\n")
        if covered != set(metadata.portable_name):
            raise ValueError(f"Substantive variables missing from Stata modules: {set(metadata.portable_name)-covered}")
    (output / "Metadata/stata_modules.json").write_text(json.dumps(modules, indent=2))
    return [{k:v for k,v in x.items() if k != "names"} for x in modules]


def copy_support_files(output):
    source = Path(__file__).resolve().parents[1]
    for name in ("README.md", "reproduce.sh", "load_csv.py"):
        path = source / name
        if path.exists():
            shutil.copy2(path, output / name)
    tests = source / "Tests"
    if tests.exists():
        shutil.copytree(tests, output / "Tests", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def copy_analysis_scripts(output):
    """Publish this recipe with its shared helper, without copying source data."""
    target = output / "Scripts"
    target.mkdir()
    for path in SCRIPT_DIR.glob("*.py"):
        shutil.copy2(path, target / path.name)
    if (target / CATEGORY_HELPER.name).resolve() != CATEGORY_HELPER.resolve():
        shutil.copy2(CATEGORY_HELPER, target / CATEGORY_HELPER.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, default=Path(__file__).resolve().parents[1] / "Decisions")
    args = parser.parse_args()
    root, out = args.root.resolve(), args.output.resolve()
    if out.exists():
        raise ValueError("Output must be a new directory; never overwrite the master panels")
    decisions = json.loads((args.decisions / "consolidation_plan.json").read_text())
    rules = json.loads((args.decisions / "missing_rules.json").read_text())["rules"]
    for relative, expected in decisions["source_hashes"].items():
        if sha256(root / relative) != expected:
            raise ValueError(f"Source changed since decision review: {relative}")
    sys.path.insert(0, str(root / "Scripts"))
    import fsa_portable_exports as helpers
    out.mkdir(parents=True)
    (out / "Checks").mkdir()
    shutil.copytree(args.decisions, out / "Decisions")
    copy_analysis_scripts(out)
    datasets = []
    for relative, stem, standalone in [
        ("Inputs/fsa_panel.parquet", "fsa_analysis", True),
        ("Panels/fsa_ipeds_panel_2004_2023.parquet", "fsa_ipeds_analysis", False),
        ("Panels/fsa_ipeds_aid_aligned_2004_2023.parquet", "fsa_ipeds_aid_analysis", False),
    ]:
        datasets.append(build_one(root, out, relative, stem, standalone, decisions, rules, helpers))
    modules = write_stata_modules(out)
    copy_support_files(out)
    manifest = {"schema_version": "1.0", "datasets": datasets, "stata_modules": modules, "source_hashes": decisions["source_hashes"],
                "source_bundle": relative_reference(root, out),
                "path_base": "analysis_data_directory", "source_hashes_path_base": "source_bundle",
                "source_master_untouched": True,
                "artifacts": [{"path": str(p.relative_to(out)), "sha256": sha256(p), "bytes": p.stat().st_size}
                              for p in sorted(out.rglob("*")) if p.is_file()]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(datasets, indent=2), flush=True)


if __name__ == "__main__":
    main()
