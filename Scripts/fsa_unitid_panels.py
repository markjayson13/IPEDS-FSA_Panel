"""Non-allocating UNITID x award-year views of uniquely resolved FSA family rows.

The full OPEID master remains authoritative. Directory identity is not proof of
campus aid scope. No duplicate family records are selected, summed, or repeated.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from fsa_dependency import upstream_module, resolve_source_repo, require_separate_output

_observations = upstream_module("fsa_observations")
parse_measure, text = _observations.parse_measure, _observations.text
normalize_full_opeid, is_count_measure = _observations.normalize_full_opeid, _observations.is_count_measure
_loans = upstream_module("fsa_loan_harmonization")
harmonize_loan_panel, consolidate_loan_programs = _loans.harmonize_loan_panel, _loans.consolidate_loan_programs
derived_metadata = _loans.derived_metadata

PREFIXES = {"grants": "grant__", "campus_based": "campus__",
            "direct_loans": "loan_direct__", "ffel": "loan_ffel__"}
KEYS = ["unitid", "award_year"]
ORPHAN_STATUS = "verified_component_identity_opeid_unresolved"
SCOPE_NOTE = "Annual institution identity only; FSA campus aid reporting scope is not certified."
INCLUDED = "included_unique_family_record"
MULTIPLE = "ambiguous_family_multiple_records"
MODULE_CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _true(series: pd.Series) -> pd.Series:
    """Never treat the string 'False' as truthy."""
    tokens = series.astype("string").str.strip().str.lower()
    unknown = series.notna() & ~tokens.isin(["true", "false", "1", "0", ""])
    if unknown.any():
        raise ValueError(f"Invalid boolean tokens: {tokens[unknown].unique().tolist()}")
    return tokens.isin(["true", "1"]).fillna(False)


def _ids(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series.replace("", pd.NA), errors="raise").astype("Float64")
    if (values.notna() & (values.le(0) | values.mod(1).ne(0))).any():
        raise ValueError("UNITID must be a positive integer or missing")
    return values.astype("Int64")


def _identity_ledger(root: Path) -> Path:
    local = root / "Metadata/source_identity_resolutions.csv"
    return local if local.exists() else resolve_source_repo() / "Metadata/source_identity_resolutions.csv"


def _schema(root: Path, family: str) -> pd.DataFrame:
    path = root / "Checks/observation_qc" / f"{family}_schema_availability.csv"
    return pd.read_csv(path, dtype=str).fillna("") if path.exists() else pd.DataFrame(columns=["award_year", "column", "sheet"])


def _measure_columns(columns: list[str], prefix: str) -> list[str]:
    # The parser explicitly supplies a status companion to every raw measure.
    return [c for c in columns if c.startswith(prefix) and c + "__status" in columns]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=lambda v: None if pd.isna(v) else str(v))


def _orphans(master: pd.DataFrame, root: Path, memberships: pd.DataFrame | None,
             ledger_path: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Bind verified UNITID-only identities to still-quarantined raw source rows."""
    if not ledger_path.exists():
        return {}, pd.DataFrame()
    ledger = pd.read_csv(ledger_path, dtype=str, keep_default_na=False)
    if "verified_unitid" not in ledger:
        return {}, pd.DataFrame()
    if ledger.resolution_id.duplicated().any():
        raise ValueError("Duplicate source identity resolution IDs")
    selected_path = root / "Checks/download_qc/selected_panel_files.csv"
    selected = pd.read_csv(selected_path, dtype=str).fillna("") if selected_path.exists() else pd.DataFrame()
    digest_cache: dict[str, str] = {}
    result, remaining = {}, []
    for family, prefix in PREFIXES.items():
        path = root / "Checks/observation_qc" / f"{family}_quarantine.parquet"
        if not path.exists():
            continue
        quarantine = pd.read_parquet(path)
        if quarantine.empty:
            continue
        family_ledger = ledger.loc[ledger.family.eq(family)]
        join_cols = ["family", "award_year", "filename", "source_excel_row"]
        if family_ledger.duplicated(join_cols).any():
            raise ValueError("Multiple identity decisions target one source row")
        quarantine = quarantine.copy()
        quarantine["source_excel_row"] = quarantine.source_excel_row.astype(str)
        annotated = quarantine.merge(family_ledger, on=join_cols, how="left", suffixes=("", "__decision"), validate="one_to_one")
        # The unchanged raw quarantine and all unresolved evidence stay auditable.
        remaining.append(annotated)
        chosen = annotated.loc[annotated.get("unitid_resolution_status", pd.Series(index=annotated.index, dtype=str)).eq(ORPHAN_STATUS)]
        rows = []
        schema = _schema(root, family)
        measure_cols = _measure_columns(list(master.columns), prefix)
        for row in chosen.to_dict("records"):
            rid = text(row.get("resolution_id"))
            if text(row.get("resolution_status")) != "unresolved" or text(row.get("recovered_opeid8")):
                raise ValueError(f"UNITID-only identity cannot claim a recovered OPEID: {rid}")
            if text(row.get("row_class")) != "quarantined_institution" or normalize_full_opeid(row.get("normalized_opeid8")):
                raise ValueError(f"UNITID-only identity must still be quarantined: {rid}")
            unitid = _ids(pd.Series([row.get("verified_unitid")])).iloc[0]
            if pd.isna(unitid):
                raise ValueError(f"Verified identity has no UNITID: {rid}")
            for actual, expected in [("raw_opeid", "expected_raw_opeid"), ("school", "expected_school"),
                                     ("state", "expected_state"), ("zip_code", "expected_zip_code")]:
                if text(row.get(actual)) != text(row.get(expected)):
                    raise ValueError(f"Verified UNITID source {actual} changed: {rid}")
            source = selected.loc[selected.family.eq(family) & selected.award_year.eq(row["award_year"])
                                  & selected.filename.eq(row["filename"])] if not selected.empty else pd.DataFrame()
            if len(source) != 1:
                raise ValueError(f"Verified UNITID requires exactly one selected workbook: {rid}")
            entry = source.iloc[0]
            source_path = Path(entry.local_path)
            digest = digest_cache.setdefault(str(source_path), hashlib.sha256(source_path.read_bytes()).hexdigest())
            if digest != row.get("source_sha256") or digest != entry.sha256:
                raise ValueError(f"Verified UNITID source hash changed: {rid}")
            year_schema = schema.loc[schema.award_year.eq(row["award_year"])]
            if year_schema.empty or set(year_schema.sheet) != {row.get("source_sheet")}:
                raise ValueError(f"Verified UNITID source sheet/schema changed: {rid}")
            evidence = json.loads(row.get("unitid_evidence_json") or "[]")
            hd = [e for e in evidence if e.get("kind") == "unassigned_annual_hd_candidate"
                  and int(e.get("unitid", 0)) == unitid and int(e.get("ipeds_year", 0)) == int(row["award_year"][:4])]
            cw = [e for e in evidence if e.get("kind") == "official_fsa_nces_crosswalk_campus_identity_only"
                  and int(e.get("unitid", 0)) == unitid and str(e.get("match_source")) == "1"]
            if not hd or not cw or any(not re.fullmatch(r"[a-f0-9]{64}", e.get("sha256", "")) for e in hd + cw):
                raise ValueError(f"Verified UNITID lacks annual directory and official campus evidence: {rid}")
            record = {"unitid": unitid, "award_year": row["award_year"], "family": family,
                      "record_origin": "verified_quarantine_unitid_only", "record_disposition": "candidate",
                      prefix + "opeid8": pd.NA, prefix + "raw_opeid": row.get("raw_opeid"),
                      prefix + "source_record_present": True, prefix + "source_filename": row["filename"],
                      prefix + "source_sheet": row["source_sheet"], prefix + "source_excel_row": int(row["source_excel_row"]),
                      prefix + "source_identity_resolution": rid, prefix + "source_identity_evidence_json": row["unitid_evidence_json"],
                      prefix + "source_sha256": digest, prefix + "ipeds_resolution_status": ORPHAN_STATUS,
                      prefix + "ipeds_annual_identity_eligible": True,
                      prefix + "ipeds_scope_alignment": "campus_identity_verified_fsa_aid_scope_not_certified"}
            format_zip_value = upstream_module("fsa_build_utils").format_zip_value
            for descriptor in ("school", "state", "school_type"):
                record[prefix + descriptor] = row.get(descriptor)
            record[prefix + "raw_zip_code"] = row.get("zip_code")
            record[prefix + "zip_code"] = format_zip_value(row.get("zip_code"), state=row.get("state"), school_type=row.get("school_type"))
            available = set(year_schema.column)
            if available - set(measure_cols):
                raise ValueError(f"Verified UNITID source measures missing from master schema: {sorted(available - set(measure_cols))}")
            for column in measure_cols:
                measure = column[len(prefix):]
                if column in available:
                    parsed = parse_measure(pd.Series([row.get(measure)]), measure).iloc[0]
                    record[column] = parsed.value
                    for suffix in ("status", "raw_token", "lower_bound", "upper_bound"):
                        record[column + "__" + suffix] = parsed[suffix]
                else:
                    record[column] = pd.NA
                    record[column + "__status"] = "unavailable_in_schema"
            if memberships is None:
                record["record_disposition"] = "excluded_missing_official_membership_guard"
            else:
                present = _true(master.get(prefix + "source_record_present", pd.Series(False, index=master.index)))
                accepted_keys = master.loc[present, ["opeid8", "award_year"]]
                member_records = memberships.merge(accepted_keys, on=["opeid8", "award_year"], how="inner", validate="many_to_one")
                if "relation_type" in member_records:
                    # Parent references describe scope sensitivity, not direct
                    # campus assignment or demonstrated overlap in this family.
                    parent = member_records.relation_type.astype("string").str.contains("parent_reference", na=False)
                    parent_related = member_records.loc[parent & member_records.unitid.eq(unitid)
                                                        & member_records.award_year.eq(row["award_year"])]
                    if not parent_related.empty:
                        record[prefix + "source_identity_parent_scope_sensitivity_json"] = _json(parent_related.to_dict("records"))
                    member_records = member_records.loc[~parent]
                collision = member_records.unitid.eq(unitid) & member_records.award_year.eq(row["award_year"])
                if collision.any():
                    record["record_disposition"] = "excluded_official_membership_overlap"
                    record[prefix + "source_identity_collision_members_json"] = _json(member_records.loc[collision].to_dict("records"))
            rows.append(record)
        if rows:
            result[family] = pd.DataFrame(rows)
    return result, pd.concat(remaining, ignore_index=True) if remaining else pd.DataFrame()


def _dictionary(panel: pd.DataFrame, root: Path, bridge_dictionary_path: Path | str | None = None) -> pd.DataFrame:
    path = root / "Dictionary/fsa_volume_panel_dictionary.parquet"
    inherited = pd.read_parquet(path).set_index("panel_column").to_dict("index") if path.exists() else {}
    bridge_path = Path(bridge_dictionary_path) if bridge_dictionary_path is not None else None
    bridge_definitions = pd.read_csv(bridge_path, dtype=str).fillna("") if bridge_path is not None and bridge_path.exists() else pd.DataFrame()
    bridge_definitions = bridge_definitions.set_index("field").to_dict("index") if not bridge_definitions.empty else {}
    rows = []
    for column in panel:
        entry = dict(inherited.get(column, {}))
        prefix = next((p for p in PREFIXES.values() if column.startswith(p)), "")
        token = column[len(prefix):] if prefix else column
        if not entry:
            role, unit = "provenance", "not_applicable"
            definition = "Source-family annual linkage metadata; preserved from the resolved bridge. " + SCOPE_NOTE
            if prefix and token != "opeid8" and token in bridge_definitions:
                entry.update(bridge_definitions[token])
                definition = bridge_definitions[token].get("description") or bridge_definitions[token].get("definition") or definition
                role = "reporting_scope" if "scope" in token else "provenance"
            elif column in KEYS or column.startswith("award_year_") or token == "opeid8":
                role, unit = "identifier_or_time", "identifier"
                definition = ("Full source-family eight-digit OPEID; missing for independently verified UNITID-only quarantine records. No canonical OPEID is invented."
                              if token == "opeid8" else "Annual panel key: IPEDS UNITID and FSA award year; award-year start/end are calendar years.")
            elif token in {"unitid_record_status", "unitid_source_record_count"}:
                role, unit = "quality_status", "category" if token.endswith("status") else "count"
                definition = "Selection status or candidate record count for this UNITID-year-family. Multiple records are blocked, never summed or arbitrarily chosen."
            elif token == "unitid_aid_scope_note":
                role, unit, definition = "reporting_scope", "text", "Family-specific limitation on interpreting an annual identity tag as the scope of aid measurement."
            elif column in {"included_source_family_count", "blocked_source_family_count"}:
                role, unit, definition = "quality_status", "count", "Number of included unique or blocked program families in this UNITID-year."
            elif column == "unitid_panel_scope_warning":
                role, unit, definition = "reporting_scope", "text", SCOPE_NOTE
            elif derived_metadata(column):
                metadata = derived_metadata(column)
                role, unit, definition = "quality_status" if column.endswith("__status") else "measure", metadata["unit"], metadata["definition"]
                entry.update(metadata)
            elif column.endswith("__status"):
                role, unit, definition = "quality_status", "category", "Preserved source-cell status or explicit missing/blocked UNITID-family status. No missing value is a structural zero."
            entry.update(column_role=role, units=unit, definition=definition)
        entry.update(panel_column=column, dtype=str(panel[column].dtype))
        entry["unitid_panel_method"] = "One eligible source record per UNITID x award year x family; complete source values retained; no aid allocation."
        rows.append(entry)
    return pd.DataFrame(rows)


def build_unitid_panel(master: pd.DataFrame, bridge: pd.DataFrame, root: Path | str,
                      outputdir: Path | str, *, memberships: pd.DataFrame | None = None,
                      identity_ledger_path: Path | str | None = None,
                      bridge_dictionary_path: Path | str | None = None) -> dict:
    """Write a wide UNITID panel, full family ledgers, exclusions and conservation.

    ``memberships`` contains opeid8, award_year, unitid rows for every official
    site candidate, including additional matches and unresolved bridge rows.
    Missing membership evidence blocks UNITID-only quarantine insertion.
    """
    root = Path(root).resolve()
    outputdir = require_separate_output(outputdir, root, resolve_source_repo())
    outputdir.mkdir(parents=True, exist_ok=True)
    fsa_keys = ["opeid8", "award_year"]
    if master.duplicated(fsa_keys).any() or bridge.duplicated(fsa_keys).any():
        raise ValueError("Master and annual bridge must have unique OPEID-year keys")
    required = set(fsa_keys + ["unitid", "ipeds_annual_identity_eligible"])
    if not required.issubset(bridge):
        raise ValueError("UNITID view requires explicit annual identity eligibility and resolved UNITID")
    bridge = bridge.copy()
    bridge["unitid"] = _ids(bridge.unitid)
    eligible = _true(bridge.ipeds_annual_identity_eligible)
    if (eligible & bridge.unitid.isna()).any():
        raise ValueError("Annual-eligible bridge row has no UNITID")
    bridge["ipeds_annual_identity_eligible"] = eligible
    if memberships is not None:
        if not set(fsa_keys + ["unitid"]).issubset(memberships):
            raise ValueError("Official memberships lack OPEID/year/UNITID")
        memberships = memberships.copy()
        memberships["unitid"] = _ids(memberships.unitid)
        if memberships.unitid.isna().any():
            raise ValueError("Official membership rows require nonmissing UNITID")
    orphan_frames, unresolved = _orphans(master, root, memberships,
        Path(identity_ledger_path) if identity_ledger_path is not None else _identity_ledger(root))
    selected_path = root / "Checks/download_qc/selected_panel_files.csv"
    selected_reports = pd.read_csv(selected_path, dtype=str).fillna("") if selected_path.exists() else None
    family_frames, source_columns, family_keys = {}, {}, []
    review_cols = [c for c in master if c == "descriptor_review_required" or c.endswith("__review_required") and not c.startswith(tuple(PREFIXES.values()))]
    for family, prefix in PREFIXES.items():
        cols = [c for c in master if c.startswith(prefix)]
        present_column = prefix + "source_record_present"
        present = _true(master.get(present_column, pd.Series(False, index=master.index)))
        frame = master.loc[present, fsa_keys + cols + review_cols].copy()
        frame = frame.rename(columns={c: prefix + "fsa_" + c for c in review_cols})
        renamed_bridge = bridge.rename(columns={c: prefix + c for c in bridge if c not in fsa_keys + ["unitid"]})
        frame = frame.merge(renamed_bridge, on=fsa_keys, how="left", validate="many_to_one")
        frame = frame.rename(columns={"opeid8": prefix + "opeid8"})
        frame["family"] = family
        frame["record_origin"] = "opeid_master"
        frame["record_disposition"] = "candidate"
        allowed = _true(frame[prefix + "ipeds_annual_identity_eligible"])
        frame.loc[~allowed | frame.unitid.isna(), "record_disposition"] = "excluded_annual_identity_unresolved"
        if family in orphan_frames:
            frame = pd.concat([frame, orphan_frames[family]], ignore_index=True)
        frame["unitid"] = _ids(frame.unitid)
        frame["identity_guard_status"] = frame.record_disposition
        candidates = frame.unitid.notna() & frame.record_disposition.eq("candidate")
        # Count every identified row, including blocked orphan rows. No hidden
        # precedence can select an accepted record over a conflicting orphan.
        identified = frame.unitid.notna() & ~frame.record_disposition.eq("excluded_annual_identity_unresolved")
        duplicate = frame.loc[identified].duplicated(KEYS, keep=False)
        frame.loc[duplicate.index[duplicate], "record_disposition"] = MULTIPLE
        frame.loc[candidates & frame.record_disposition.eq("candidate"), "record_disposition"] = INCLUDED
        frame["source_record_id"] = (family + "|" + frame.award_year.astype(str) + "|"
            + frame.get(prefix + "source_filename", pd.Series("", index=frame.index)).fillna("").astype(str) + "|"
            + frame.get(prefix + "source_excel_row", pd.Series("", index=frame.index)).astype("string").fillna("") + "|"
            + frame[prefix + "opeid8"].fillna("UNITID_ONLY").astype(str))
        if frame.source_record_id.duplicated().any():
            raise ValueError(f"Duplicated source record identity in {family}")
        family_frames[family] = frame
        source_columns[family] = [c for c in frame if c.startswith(prefix)]
        family_keys.append(frame.loc[identified, KEYS])
    keys = pd.concat(family_keys, ignore_index=True).drop_duplicates().sort_values(KEYS).reset_index(drop=True)
    panel = keys.copy()
    panel["award_year_start"] = pd.to_numeric(panel.award_year.str[:4]).astype("Int64")
    panel["award_year_end"] = pd.to_numeric(panel.award_year.str[-4:]).astype("Int64")
    exclusions, conservation, files, included_counts = [], [], {}, {}
    for family, prefix in PREFIXES.items():
        records = family_frames[family]
        columns = source_columns[family]
        included = records.loc[records.record_disposition.eq(INCLUDED)]
        if included.duplicated(KEYS).any():
            raise AssertionError("Included UNITID-family records are not unique")
        piece = keys.merge(included[KEYS + columns], on=KEYS, how="left", validate="one_to_one")
        known = records.loc[records.unitid.notna() & ~records.record_disposition.eq("excluded_annual_identity_unresolved")]
        summaries = known.groupby(KEYS, dropna=False).agg(
            **{prefix + "unitid_source_record_count": ("source_record_id", "size"),
               prefix + "unitid_record_status": ("record_disposition", lambda s: MULTIPLE if len(s) > 1 else s.iloc[0])}).reset_index()
        piece = piece.merge(summaries, on=KEYS, how="left", validate="one_to_one")
        status_col = prefix + "unitid_record_status"
        piece[status_col] = piece[status_col].fillna("absent_source_record").astype("string")
        piece[prefix + "unitid_source_record_count"] = piece[prefix + "unitid_source_record_count"].fillna(0).astype("Int64")
        piece[prefix + "source_record_present"] = piece[status_col].eq(INCLUDED)
        piece[prefix + "unitid_aid_scope_note"] = (
            "Institution identity only. Campus-Based applications can combine separate main OPEIDs in one FISAP; UNITID-exclusive amounts are not certified."
            if family == "campus_based" else SCOPE_NOTE)
        schema = _schema(root, family)
        schema_path = root / "Checks/observation_qc" / f"{family}_schema_availability.csv"
        absent = piece[status_col].eq("absent_source_record")
        blocked = ~piece[status_col].isin([INCLUDED, "absent_source_record"])
        for measure in _measure_columns(columns, prefix):
            column = measure + "__status"
            piece[column] = piece[column].astype("string")
            missing = piece[column].isna()
            if (missing & piece[status_col].eq(INCLUDED)).any():
                raise ValueError(f"Included source record has an unpopulated cell status: {column}")
            # A failed record-selection gate takes precedence over availability:
            # no quantitative cell is exposed from a blocked source family.
            piece.loc[missing & blocked, column] = piece.loc[missing & blocked, status_col]
            absent_missing = missing & absent
            if selected_reports is None:
                piece.loc[absent_missing, column] = "availability_metadata_missing"
                continue
            report_years = set(selected_reports.loc[selected_reports.family.eq(family), "award_year"])
            report_available = piece.award_year.isin(report_years)
            piece.loc[absent_missing & ~report_available, column] = "report_not_available"
            if not schema_path.exists():
                piece.loc[absent_missing & report_available, column] = "availability_metadata_missing"
                continue
            header_years = set(schema.loc[schema.column.eq(measure), "award_year"])
            header_available = piece.award_year.isin(header_years)
            piece.loc[absent_missing & report_available & ~header_available, column] = "unavailable_in_schema"
            piece.loc[absent_missing & report_available & header_available, column] = "absent_source_record"
        panel = panel.merge(piece, on=KEYS, how="left", validate="one_to_one")
        checks = included[KEYS + columns].merge(piece[KEYS + columns], on=KEYS, suffixes=("__input", "__output"), validate="one_to_one")
        for column in columns:
            left, right = checks[column + "__input"], checks[column + "__output"]
            equal = left.isna() & right.isna()
            both_present = left.notna() & right.notna()
            equal.loc[both_present] = left.loc[both_present].eq(right.loc[both_present]).fillna(False)
            bad = int((~equal).sum())
            conservation.append({"family": family, "award_year": "all", "column": column,
                                 "included_records": len(checks), "mismatched_cells": bad,
                                 "input_known_sum": None, "output_known_sum": None, "passed": bad == 0})
        for column in _measure_columns(columns, prefix):
            for year, group in checks.groupby("award_year"):
                left = pd.to_numeric(group[column + "__input"], errors="raise")
                right = pd.to_numeric(group[column + "__output"], errors="raise")
                a, b = left.sum(min_count=1), right.sum(min_count=1)
                okay = (pd.isna(a) and pd.isna(b)) or abs(float(a) - float(b)) <= (0 if is_count_measure(column) else .011)
                conservation.append({"family": family, "award_year": year, "column": column,
                                     "included_records": len(group), "mismatched_cells": 0,
                                     "input_known_sum": a, "output_known_sum": b, "passed": bool(okay)})
        ledger_path = outputdir / f"{family}_source_record_ledger.parquet"
        records.to_parquet(ledger_path, index=False)
        files[family + "_source_record_ledger"] = str(ledger_path)
        excluded = records.loc[~records.record_disposition.eq(INCLUDED)]
        exclusion_cols = ["family", "unitid", "award_year", "source_record_id", "record_origin", "record_disposition", "identity_guard_status"] + [c for c in columns if c.endswith(("opeid8", "raw_opeid", "source_filename", "source_excel_row", "ipeds_resolution_status", "source_identity_resolution"))]
        exclusions.append(excluded[exclusion_cols])
        included_counts[family] = {"input_master_records": int(records.record_origin.eq("opeid_master").sum()),
                                  "verified_quarantine_records": int(records.record_origin.eq("verified_quarantine_unitid_only").sum()),
                                  "included_records": len(included), "excluded_records": len(excluded)}
    panel, _ = harmonize_loan_panel(panel)
    panel, _ = consolidate_loan_programs(panel)
    for family in ("direct_loans", "ffel"):
        prefix = PREFIXES[family]
        blocked = ~panel[prefix + "unitid_record_status"].isin([INCLUDED, "absent_source_record"])
        report_unavailable = pd.Series(False, index=panel.index)
        if selected_reports is not None:
            report_years = set(selected_reports.loc[selected_reports.family.eq(family), "award_year"])
            report_unavailable = panel[prefix + "unitid_record_status"].eq("absent_source_record") & ~panel.award_year.isin(report_years)
        for c in panel:
            if c.startswith(prefix.replace("__", "_harmonized__")) and c.endswith("__status"):
                panel.loc[blocked, c] = panel.loc[blocked, prefix + "unitid_record_status"]
                panel.loc[report_unavailable, c] = "report_not_available"
    combined_blocked = (~panel.loan_direct__unitid_record_status.isin([INCLUDED, "absent_source_record"])) | (
        panel.award_year_start.le(2009) & ~panel.loan_ffel__unitid_record_status.isin([INCLUDED, "absent_source_record"]))
    for c in panel:
        if c.startswith("loan__") and c.endswith("__status"):
            panel.loc[combined_blocked, c] = "incomplete_blocked_unitid_family"
    panel["included_source_family_count"] = sum(panel[p + "unitid_record_status"].eq(INCLUDED).astype(int) for p in PREFIXES.values()).astype("Int64")
    panel["blocked_source_family_count"] = sum((~panel[p + "unitid_record_status"].isin([INCLUDED, "absent_source_record"])).astype(int) for p in PREFIXES.values()).astype("Int64")
    panel["unitid_panel_scope_warning"] = SCOPE_NOTE
    if panel.duplicated(KEYS).any() or panel.unitid.isna().any():
        raise AssertionError("UNITID panel keys are invalid")
    checks = pd.DataFrame(conservation)
    if not checks.passed.all():
        checks.to_csv(outputdir / "conservation.csv", index=False)
        raise AssertionError("UNITID source-cell conservation failed")
    dictionary = _dictionary(panel, root, bridge_dictionary_path)
    if dictionary.panel_column.duplicated().any() or set(dictionary.panel_column) != set(panel):
        raise AssertionError("UNITID dictionary does not enumerate every output column")
    outputs = {"panel": ("fsa_unitid_award_year_panel.parquet", panel),
               "dictionary": ("dictionary.csv", dictionary), "conservation": ("conservation.csv", checks),
               "exclusions": ("exclusions.csv", pd.concat(exclusions, ignore_index=True)),
               "quarantine_identity_evidence": ("quarantine_identity_evidence.parquet", unresolved)}
    for name, (filename, frame) in outputs.items():
        path = outputdir / filename
        frame.to_parquet(path, index=False) if path.suffix == ".parquet" else frame.to_csv(path, index=False)
        files[name] = str(path)
    manifest = {"grain": "unitid x award_year", "rows": len(panel), "unique_unitids": int(panel.unitid.nunique()),
                "source_families": included_counts, "conservation_checks": len(checks), "all_conservation_passed": True,
                "dictionary_columns": len(dictionary), "panel_columns": len(panel.columns),
                "canonical_opeid_created": False, "scope_warning": SCOPE_NOTE, "files": files,
                "official_membership_rows": None if memberships is None else len(memberships),
                "official_membership_key_sha256": None if memberships is None else hashlib.sha256(
                    pd.util.hash_pandas_object(memberships[fsa_keys + ["unitid"]], index=False).values.tobytes()).hexdigest(),
                "module_sha256": MODULE_CODE_SHA256,
                "output_sha256": {name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in files.items()}}
    (outputdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
