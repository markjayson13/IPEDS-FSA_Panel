"""Coverage evidence for a UNITID/year merge; diagnostic OPEIDs never assign aid.

The supplied IPEDS panel defines the analysis universe. A missing FSA UNITID row
does not establish zero aid. Exact OPEID evidence is looked up only in the frozen
FSA reporting-master bridge, which does not contain every quarantined source row.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from ipeds_category_storage import ipeds_opeid_views


FAMILIES = ("grant", "campus", "loan_direct", "loan_ffel")
FSA_REQUIRED = (
    "unitid", "award_year_start", "award_year", "award_year_end",
    "included_source_family_count", "blocked_source_family_count",
)
EVIDENCE_DEFINITIONS = {
    "missing_ipeds_opeid_no_exact_lookup":
        "The supplied OPEID is missing; no exact OPEID lookup was possible.",
    "not_applicable_ipeds_opeid_no_exact_lookup":
        "The source OPEID is the documented -2 not-applicable sentinel, not an institution identifier.",
    "ipeds_alphanumeric_branch_not_exact_fsa_id":
        "This is a valid NCES alphanumeric reporting-branch identifier, not an exact numeric FSA OPEID. "
        "No suffix is stripped and no parent 00 identifier is invented.",
    "unrecognized_ipeds_opeid_no_exact_lookup":
        "The original OPEID token is unrecognized under the reviewed IPEDS source-format policy; "
        "it is preserved without an exact FSA lookup.",
    "no_exact_opeid_award_year_in_fsa_master_bridge":
        "No exact full OPEID/award-year row exists in the supplied FSA master bridge. "
        "This does not exclude differently reported or quarantined source records and is not zero aid.",
    "fsa_master_identity_unresolved_or_ineligible":
        "An exact FSA master bridge row exists, but it has no positive resolved UNITID "
        "or is not explicitly annual-identity eligible. Inspect the preserved bridge evidence.",
    "fsa_master_identity_ambiguous":
        "An exact FSA bridge row has multiple annual official-site or nonadministrative directory UNITID candidates. "
        "No candidate is selected by this diagnostic.",
    "fsa_master_identity_evidence_conflict":
        "An exact FSA bridge row has conflicting official identity evidence; aid remains unassigned here.",
    "fsa_master_no_annual_site_match":
        "An exact FSA bridge row exists but has no annual institution-site match; "
        "this differs from an absent FSA reporting-unit record.",
    "fsa_master_identity_evidence_incomplete":
        "An exact FSA bridge row has incomplete official-site parsing evidence; identity is unresolved.",
    "fsa_master_noninstitutional_source_placeholder":
        "The exact FSA bridge row is classified as a noninstitutional source placeholder.",
    "fsa_master_assigned_another_unitid":
        "An exact FSA master bridge row resolves to a different UNITID. "
        "The diagnostic does not transfer its aid to the supplied IPEDS institution.",
    "fsa_master_same_unitid_without_unitid_panel_row":
        "An exact FSA master bridge row has the same eligible UNITID, but the supplied "
        "FSA UNITID panel has no corresponding row. Consult source-family exclusion ledgers.",
}


def normalize_full_opeid(value: object) -> str | None:
    """Parse the documented FULL OPEID field, preserving all location digits.

    Integer storage may have lost leading zeros. Six digits are padded on the
    left, never treated as a root. Sentinels, zero, fractions and scientific
    notation strings are rejected. The original token is retained separately.
    """
    if value is None or pd.isna(value) or isinstance(value, bool):
        return None
    token = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,8}(?:\.0+)?", token):
        return None
    digits = token.split(".")[0]
    return digits.zfill(8) if int(digits) else None


def _integers(series: pd.Series, name: str, *, positive: bool = False) -> pd.Series:
    if series.map(lambda value: isinstance(value, bool)).any():
        raise ValueError(f"{name} contains boolean keys")
    values = pd.to_numeric(series, errors="coerce")
    if values.isna().any() or values.mod(1).ne(0).any():
        raise ValueError(f"{name} must contain nonmissing integer values")
    if positive and values.le(0).any():
        raise ValueError(f"{name} must contain positive values")
    return values.astype("int64")


def _explicit_true(value: object) -> bool:
    return value is not None and not pd.isna(value) and str(value).lower() in {"true", "1"}


def _require(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{name} lacks columns: {missing}")
    if frame.columns.duplicated().any():
        raise ValueError(f"{name} has duplicate column names")


def build_merge_diagnostics(
    ipeds_keys: pd.DataFrame,
    fsa_keys: pd.DataFrame,
    bridge: pd.DataFrame,
    output_dir: str | Path,
    offset: int = 0,
) -> dict:
    """Write coverage ledgers without changing either source or assigning aid.

    ``target FSA award_year_start = IPEDS year + offset``. Only offsets 0 and
    -1 are supported. Each source must be unique at its institution/year grain.
    All bridge columns survive under ``bridge__`` in the IPEDS-only ledger.
    """
    if isinstance(offset, bool) or offset not in (0, -1):
        raise ValueError("offset must be 0 (start-year anchor) or -1 (prior award year)")
    _require(ipeds_keys, ("UNITID", "year", "OPEID"), "IPEDS keys")
    _require(fsa_keys, FSA_REQUIRED, "FSA keys")
    _require(bridge, ("opeid8", "award_year", "unitid", "ipeds_annual_identity_eligible"), "FSA bridge")
    left = ipeds_keys[[c for c in ("UNITID", "year", "OPEID", "INSTNM") if c in ipeds_keys]].copy()
    left["UNITID"] = _integers(left.UNITID, "IPEDS UNITID", positive=True)
    left["year"] = _integers(left.year, "IPEDS year", positive=True)
    if left.duplicated(["UNITID", "year"]).any():
        raise ValueError("IPEDS keys are not unique at UNITID/year")
    left = left.rename(columns={"OPEID": "ipeds_opeid_raw", "INSTNM": "ipeds_instnm"})
    identifier_views = ipeds_opeid_views(left.ipeds_opeid_raw)
    left["ipeds_opeid8"] = identifier_views.ipeds_opeid8_numeric
    left["ipeds_opeid_format_status"] = identifier_views.ipeds_opeid_format_status
    left["ipeds_opeid_valid"] = left.ipeds_opeid_format_status.isin([
        "numeric8_format_only_not_verified_match", "ipeds_alpha_reporting_branch_not_exact_fsa_id",
    ]).astype("boolean")
    left["ipeds_opeid_exact_lookup_eligible"] = left.ipeds_opeid8.notna().astype("boolean")
    left["fsa_award_year_start"] = left.year + offset
    left["fsa_award_year_end"] = left.fsa_award_year_start + 1
    left["fsa_award_year"] = left.fsa_award_year_start.astype(str) + "-" + left.fsa_award_year_end.astype(str)

    optional = [f"{family}__{suffix}" for family in FAMILIES
                for suffix in ("opeid8", "unitid_record_status")]
    right = fsa_keys[list(FSA_REQUIRED) + [c for c in optional if c in fsa_keys]].copy()
    right["unitid"] = _integers(right.unitid, "FSA unitid", positive=True)
    right["award_year_start"] = _integers(right.award_year_start, "FSA award_year_start", positive=True)
    right["award_year_end"] = _integers(right.award_year_end, "FSA award_year_end", positive=True)
    for name in ("included_source_family_count", "blocked_source_family_count"):
        right[name] = _integers(right[name], name)
        if not right[name].between(0, 4).all():
            raise ValueError(f"{name} must be between 0 and 4")
    if (right.included_source_family_count + right.blocked_source_family_count).gt(4).any():
        raise ValueError("FSA included and blocked family counts exceed four")
    expected_year = right.award_year_start.astype(str) + "-" + right.award_year_end.astype(str)
    if not (right.award_year_end.eq(right.award_year_start + 1) & right.award_year.eq(expected_year)).all():
        raise ValueError("FSA award-year labels/end years disagree with start years")
    if right.duplicated(["unitid", "award_year_start"]).any():
        raise ValueError("FSA keys are not unique at unitid/award_year_start")
    right = right.rename(columns={c: "fsa_" + c for c in right if "__" not in c})
    right = right.rename(columns={c: "fsa__" + c for c in right if "__" in c})
    right["UNITID"] = right.fsa_unitid
    right["year"] = right.fsa_award_year_start - offset
    # Build only the evidence needed from the left so no duplicated time values
    # or ambiguous suffixed join keys are introduced by the full outer merge.
    coverage = left.merge(
        right.drop(columns=["fsa_unitid", "fsa_award_year", "fsa_award_year_start", "fsa_award_year_end"]),
        on=["UNITID", "year"], how="outer", validate="one_to_one", indicator=True,
    )
    coverage["fsa_award_year_start"] = (coverage.year + offset).astype("int64")
    coverage["fsa_award_year_end"] = coverage.fsa_award_year_start + 1
    coverage["fsa_award_year"] = coverage.fsa_award_year_start.astype(str) + "-" + coverage.fsa_award_year_end.astype(str)
    coverage["ipeds_row_present"] = coverage._merge.ne("right_only")
    coverage["fsa_row_present"] = coverage._merge.ne("left_only")
    coverage["ipeds_year_in_supplied_panel"] = coverage.year.isin(left.year.unique())
    coverage["fsa_has_usable_family"] = coverage.fsa_included_source_family_count.gt(0)
    coverage["fsa_blocked_only"] = (coverage.fsa_row_present
                                     & coverage.fsa_included_source_family_count.eq(0)
                                     & coverage.fsa_blocked_source_family_count.gt(0))
    coverage["merge_status"] = coverage._merge.map({
        "left_only": "ipeds_only", "right_only": "fsa_only", "both": "matched",
    }).astype("string")
    coverage = coverage.drop(columns="_merge").sort_values(["year", "UNITID"]).reset_index(drop=True)
    for name in ("fsa_included_source_family_count", "fsa_blocked_source_family_count"):
        coverage[name] = coverage[name].astype("Int64")
    # Absence is not a negative boolean finding about aid. Keep these missing
    # when the corresponding FSA row is absent.
    for name in ("fsa_has_usable_family", "fsa_blocked_only"):
        coverage[name] = coverage[name].astype("boolean").where(coverage.fsa_row_present)

    bridge_evidence = bridge.copy()
    bridge_evidence["_lookup_opeid8"] = bridge_evidence.opeid8.map(normalize_full_opeid).astype("string")
    if bridge_evidence._lookup_opeid8.isna().any():
        raise ValueError("FSA master bridge contains invalid full OPEIDs")
    if bridge_evidence.award_year.isna().any():
        raise ValueError("FSA bridge contains missing award years")
    bridge_years = bridge_evidence.award_year.astype("string").str.extract(r"^([0-9]{4})-([0-9]{4})$")
    if (bridge_years.isna().any().any()
            or not pd.to_numeric(bridge_years[1]).eq(pd.to_numeric(bridge_years[0]) + 1).all()):
        raise ValueError("FSA bridge contains malformed award-year labels")
    if bridge_evidence.duplicated(["_lookup_opeid8", "award_year"]).any():
        raise ValueError("FSA bridge keys are not unique at normalized OPEID8/award_year")
    bridge_evidence = bridge_evidence.rename(columns={c: "bridge__" + c for c in bridge_evidence})
    only_ipeds = coverage[coverage.merge_status.eq("ipeds_only")].copy()
    only_ipeds = only_ipeds.merge(
        bridge_evidence, left_on=["ipeds_opeid8", "fsa_award_year"],
        right_on=["bridge___lookup_opeid8", "bridge__award_year"],
        how="left", validate="many_to_one", indicator="_bridge_merge",
    )
    found = only_ipeds._bridge_merge.eq("both")
    assigned = pd.to_numeric(only_ipeds.bridge__unitid, errors="coerce")
    eligible = only_ipeds.bridge__ipeds_annual_identity_eligible.map(_explicit_true)
    only_ipeds["exact_bridge_row_present"] = found
    format_reasons = {
        "source_missing": "missing_ipeds_opeid_no_exact_lookup",
        "not_applicable": "not_applicable_ipeds_opeid_no_exact_lookup",
        "ipeds_alpha_reporting_branch_not_exact_fsa_id": "ipeds_alphanumeric_branch_not_exact_fsa_id",
        "unrecognized_source_identifier": "unrecognized_ipeds_opeid_no_exact_lookup",
        "numeric8_format_only_not_verified_match": "no_exact_opeid_award_year_in_fsa_master_bridge",
    }
    only_ipeds["evidence_status"] = only_ipeds.ipeds_opeid_format_status.map(format_reasons)
    if only_ipeds.evidence_status.isna().any():
        raise ValueError("Unmapped IPEDS identifier format status")
    only_ipeds.loc[found, "evidence_status"] = "fsa_master_identity_unresolved_or_ineligible"
    resolution_reasons = {
        "multiple_official_site_unitids": "fsa_master_identity_ambiguous",
        "multiple_nonadministrative_directory_unitids": "fsa_master_identity_ambiguous",
        "directory_crosswalk_identity_conflict": "fsa_master_identity_evidence_conflict",
        "fsa_ipeds_geography_identity_conflict": "fsa_master_identity_evidence_conflict",
        "no_annual_site_match": "fsa_master_no_annual_site_match",
        "crosswalk_site_parse_incomplete": "fsa_master_identity_evidence_incomplete",
        "noninstitutional_source_placeholder": "fsa_master_noninstitutional_source_placeholder",
    }
    if "bridge__ipeds_resolution_status" in only_ipeds:
        reason = only_ipeds.bridge__ipeds_resolution_status.map(resolution_reasons)
        only_ipeds.loc[found & reason.notna(), "evidence_status"] = reason
    only_ipeds.loc[found & assigned.gt(0) & assigned.ne(only_ipeds.UNITID), "evidence_status"] = "fsa_master_assigned_another_unitid"
    only_ipeds.loc[found & assigned.eq(only_ipeds.UNITID) & eligible, "evidence_status"] = "fsa_master_same_unitid_without_unitid_panel_row"
    only_ipeds["evidence_interpretation"] = only_ipeds.evidence_status.map(EVIDENCE_DEFINITIONS)
    only_ipeds = only_ipeds.drop(columns=["_bridge_merge", "bridge___lookup_opeid8"])
    only_fsa = coverage[coverage.merge_status.eq("fsa_only")].copy()
    only_fsa["unmatched_reason"] = only_fsa.ipeds_year_in_supplied_panel.map({
        True: "unitid_year_not_in_supplied_ipeds_universe",
        False: "year_not_in_supplied_ipeds_panel",
    })
    blocked = coverage[coverage.merge_status.eq("matched") & coverage.fsa_blocked_only.fillna(False)].copy()

    counts = []
    for year, rows in coverage.groupby("year", sort=True):
        counts.append({
            "ipeds_year": int(year), "fsa_award_year_start": int(year + offset),
            "ipeds_year_in_supplied_panel": bool(rows.ipeds_year_in_supplied_panel.iloc[0]),
            "ipeds_rows": int(rows.ipeds_row_present.sum()),
            "fsa_rows": int(rows.fsa_row_present.sum()),
            "matched_rows": int(rows.merge_status.eq("matched").sum()),
            "matched_usable_family_rows": int((rows.merge_status.eq("matched") & rows.fsa_has_usable_family.fillna(False)).sum()),
            "matched_blocked_only_rows": int((rows.merge_status.eq("matched") & rows.fsa_blocked_only.fillna(False)).sum()),
            "ipeds_only_rows": int(rows.merge_status.eq("ipeds_only").sum()),
            "fsa_only_rows": int(rows.merge_status.eq("fsa_only").sum()),
        })
    year_counts = pd.DataFrame(counts)
    evidence_counts = only_ipeds.groupby(["year", "evidence_status"], dropna=False).size().rename("rows").reset_index()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = {
        "row_coverage": coverage, "ipeds_only_evidence": only_ipeds,
        "fsa_only": only_fsa, "matched_blocked_only": blocked,
        "year_counts": year_counts, "ipeds_only_evidence_counts": evidence_counts,
    }
    files = {name: str(out / f"{name}.csv") for name in frames}
    for name, frame in frames.items():
        frame.to_csv(files[name], index=False)
    summary = {
        "schema_version": "1.1.0",
        "alignment": "FSA award_year_start = IPEDS year + offset", "offset": int(offset),
        "ipeds_rows": len(left), "fsa_rows": len(right),
        "matched_rows": int(coverage.merge_status.eq("matched").sum()),
        "matched_usable_family_rows": int((coverage.merge_status.eq("matched") & coverage.fsa_has_usable_family.fillna(False)).sum()),
        "matched_blocked_only_rows": len(blocked), "ipeds_only_rows": len(only_ipeds),
        "fsa_only_rows": len(only_fsa),
        "fsa_only_rows_within_ipeds_years": int(only_fsa.ipeds_year_in_supplied_panel.sum()),
        "fsa_only_rows_outside_ipeds_years": int((~only_fsa.ipeds_year_in_supplied_panel).sum()),
        "ipeds_only_evidence_counts": {str(k): int(v) for k, v in only_ipeds.evidence_status.value_counts().items()},
        "ipeds_identifier_format_counts": {str(k): int(v) for k, v in left.ipeds_opeid_format_status.value_counts().items()},
        "ipeds_only_identifier_format_counts": {str(k): int(v) for k, v in only_ipeds.ipeds_opeid_format_status.value_counts().items()},
        "identifier_policy": {
            "source": "Scripts/ipeds_category_storage.py:ipeds_opeid_views (same policy as the Analysis release)",
            "ipeds_opeid_valid": "Recognized numeric8 or NCES alphanumeric branch format only; not proof of identity.",
            "ipeds_opeid_exact_lookup_eligible": "Only a positive eight-digit source string can be looked up in the numeric FSA bridge.",
            "source_tokens_preserved": True,
            "alphanumeric_branch_ids_collapsed": False,
        },
        "evidence_definitions": EVIDENCE_DEFINITIONS,
        "interpretation": "Diagnostics only: no aid values are assigned, redistributed, or zero-filled. "
            "An absent exact FSA master bridge row does not rule out different source identifiers or quarantine records. "
            "The supplied IPEDS PRCH-cleaned universe may omit institutions present in FSA.",
        "files": files,
    }
    summary["files"]["summary"] = str(out / "diagnostic_summary.json")
    Path(summary["files"]["summary"]).write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def create_diagnostics(
    ipeds_path: str | Path,
    fsa_path: str | Path,
    bridge_path: str | Path,
    output_dir: str | Path,
    offset: int = 0,
) -> dict:
    """Read a small key projection from each panel, plus full bridge evidence."""
    ipeds_columns = set(pq.read_schema(ipeds_path).names)
    fsa_columns = set(pq.read_schema(fsa_path).names)
    ipeds = pd.read_parquet(ipeds_path, columns=[c for c in ("UNITID", "year", "OPEID", "INSTNM") if c in ipeds_columns])
    selected_fsa = list(FSA_REQUIRED) + [f"{family}__{suffix}" for family in FAMILIES
        for suffix in ("opeid8", "unitid_record_status")
        if f"{family}__{suffix}" in fsa_columns]
    fsa = pd.read_parquet(fsa_path, columns=[c for c in selected_fsa if c in fsa_columns])
    bridge = pd.read_parquet(bridge_path)
    result = build_merge_diagnostics(ipeds, fsa, bridge, output_dir, offset)
    result["sources"] = {"ipeds": str(ipeds_path), "fsa": str(fsa_path), "bridge": str(bridge_path)}
    Path(result["files"]["summary"]).write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipeds", required=True, type=Path)
    parser.add_argument("--fsa", required=True, type=Path)
    parser.add_argument("--bridge", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--offset", type=int, choices=(0, -1), default=0)
    args = parser.parse_args()
    print(json.dumps(create_diagnostics(args.ipeds, args.fsa, args.bridge, args.output_dir, args.offset), indent=2))
