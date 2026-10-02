"""Annual identity resolution and comparable-denominator diagnostics.

IPEDS HD, official primary/additional site matches, and parent references are
separate evidence. Identity resolution never allocates an FSA measure to sites.
"""
from __future__ import annotations
from collections import defaultdict
import json
import math
from difflib import SequenceMatcher
import pandas as pd

SITE_RELATIONS = {"primary_match", "additional_match"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
TERRITORIES = {"AS", "GU", "MP", "PR", "VI", "FM", "MH", "PW"}
COMPONENTS = {"finance": "f", "student_aid": "sfa", "fall_enrollment": "ef", "completions": "c", "twelve_month_enrollment": "e12"}


def _int(value):
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number.is_integer() else None
    except (TypeError, ValueError):
        return None


def _truth(value) -> bool:
    return str(value).strip().lower() in {"true", "1"}


def component_scope(record: dict, component: str) -> str:
    """Year/component definitions, not a generic positive-code exclusion."""
    suffix = COMPONENTS[component]
    code = _int(record.get("prch_" + suffix))
    parent = _int(record.get("idx_" + suffix))
    year = _int(record.get("ipeds_year")) or 0
    if code is None or parent is None or (parent != -2 and parent <= 0):
        return "metadata_unavailable_or_invalid"
    roles = {-2: "no_parent_child_relation_reported", 1: "parent", 2: "full_child"}
    if component == "finance":
        roles.update({3: "partial_child", 4: "full_child_nonpostsecondary_parent", 5: "partial_child_nonpostsecondary_parent"})
        if year >= 2016:
            roles[6] = "partial_parent_and_child"
    if code not in roles:
        return "undocumented_code"
    if code == -2 and parent > 0:
        return "parent_reference_without_role"
    return roles[code]


def title_iv_status(record: dict) -> str:
    code, year = _int(record.get("opeflag")), _int(record.get("ipeds_year")) or 0
    if code == 1: return "participating"
    if code == 2: return "branch_of_participating_institution"
    if code == 4:
        return "eligible_out_of_scope_or_historical_definition" if year in {1999, 2001} else "new_participant"
    if code == 8 and year >= 2004: return "new_spring_participant"
    return {3: "deferment_only", 5: "not_participating_with_opeid", 6: "not_participating_without_opeid", 7: "stopped_participating"}.get(code, "unknown")


def geography(row: dict) -> str:
    states = {str(row.get(c, "")).strip().upper() for c in ["state", "grant__state", "campus__state", "loan_direct__state", "loan_ffel__state"]}
    types = {str(row.get(c, "")).strip().lower() for c in ["school_type", "grant__school_type", "campus__school_type", "loan_direct__school_type", "loan_ffel__school_type"]}
    foreign = "FC" in states or any("foreign" in t for t in types)
    domestic = bool(states & (US_STATES | TERRITORIES))
    if foreign and domestic: return "conflicting_geography"
    if foreign: return "foreign_explicit"
    if states & US_STATES: return "us_states_dc"
    if states & TERRITORIES: return "territories_and_freely_associated_states"
    return "unknown"


def enrich_annual_linkage(panel: pd.DataFrame, base_bridge: pd.DataFrame, hd: pd.DataFrame,
                          cw_records: pd.DataFrame, cw_relations: pd.DataFrame):
    """Return bridge, unchanged-cell linked panel, sensitivity view, memberships.

    Primary plus additional official site matches form one candidate set. A
    parent reference is never substituted for a site. A singleton must exist in
    the same annual directory and must not contradict its full-ID candidate set.
    Administrative identity is retained but excluded from institutional analysis.
    """
    from fsa_dependency import upstream_module
    school_compare_key = upstream_module("fsa_build_utils").school_compare_key
    directory, exact = {}, defaultdict(set)
    for row in hd.to_dict("records"):
        year, uid = int(row["ipeds_year"]), int(row["unitid"])
        if (year, uid) in directory:
            raise ValueError("Annual directory UNITID is not unique")
        directory[(year, uid)] = row
        if pd.notna(row.get("opeid8")):
            exact[(year, row["opeid8"])].add(uid)
    available_years = set(hd.ipeds_year.astype(int))
    if not cw_records.empty and cw_records.duplicated(["cw_year", "opeid8"]).any():
        raise ValueError("Crosswalk has duplicate annual OPEIDs")
    cw = {(int(r["cw_year"]), r["opeid8"]): r for r in cw_records.to_dict("records")}
    sites, parents = defaultdict(set), defaultdict(set)
    for row in cw_relations.to_dict("records"):
        uid = _int(row.get("unitid"))
        if uid is None: continue
        key = (int(row["cw_year"]), row["opeid8"])
        if row["relation_type"] in SITE_RELATIONS: sites[key].add(uid)
        elif row["relation_type"] == "parent_reference": parents[key].add(uid)
    geo_cols = [c for c in panel if c in {"state", "school_type"} or c.endswith(("__state", "__school_type"))]
    geo = {(r["opeid8"], r["award_year"]): geography(r) for r in panel[["opeid8", "award_year"] + geo_cols].to_dict("records")}
    descriptors = {(r["opeid8"], r["award_year"]): r for r in panel[
        ["opeid8", "award_year"] + [c for c in ("school", "state") if c in panel]].to_dict("records")}
    rows, members = [], []
    for position, old in enumerate(base_bridge.to_dict("records")):
        row = dict(old)
        key = (int(row["ipeds_year"]), row["opeid8"])
        h, s, p, official = exact[key], sites[key], parents[key], cw.get(key)
        row["ipeds_directory_unitid"] = row.pop("unitid")
        row["ipeds_directory_match_status"] = row["ipeds_match_status"]
        row["ipeds_geography_class"] = geo[(row["opeid8"], row["award_year"])]
        row["ipeds_crosswalk_available"] = official is not None
        row["ipeds_crosswalk_site_unitids_json"] = json.dumps(sorted(s))
        row["ipeds_crosswalk_parent_unitids_json"] = json.dumps(sorted(p))
        row["ipeds_directory_candidate_unitids_json"] = json.dumps(sorted(h))
        start_year = int(str(row["award_year"])[:4])
        anchor_keys = [(start_year, row["opeid8"]), (start_year + 1, row["opeid8"])]
        anchor_sites = [sites[k] for k in anchor_keys]
        anchor_records = [cw.get(k) for k in anchor_keys]
        crosswalk_temporal_conflict = (any(len(ids) > 1 for ids in anchor_sites) or
            any(r is not None and not _truth(r.get("cw_site_parse_complete")) for r in anchor_records) or
            all(len(ids) == 1 for ids in anchor_sites) and anchor_sites[0] != anchor_sites[1])
        row["ipeds_crosswalk_anchor_sensitivity"] = (
            "scope_or_identity_conflict" if crosswalk_temporal_conflict else
            "year_unavailable" if any(r is None for r in anchor_records) else
            "stable_unique" if all(len(ids) == 1 for ids in anchor_sites) else "ambiguous_or_unmatched")
        # Full raw crosswalk records are an independent artifact. Keep original
        # change/ID notes and relation provenance in the bridge for interpretation.
        if official:
            row.update({k: v for k, v in official.items() if k not in {"opeid8", "cw_year"}})
        uid = None
        if official and not _truth(official.get("cw_site_parse_complete")):
            status = "crosswalk_site_parse_incomplete"
        elif len(s) > 1:
            status = "multiple_official_site_unitids"
        elif s:
            candidate = next(iter(s))
            if h and candidate not in h:
                status = "directory_crosswalk_identity_conflict"
            elif len(h) > 1 and any(_int(directory[(key[0], other)].get("sector")) != 0 for other in h - s):
                status = "multiple_nonadministrative_directory_unitids"
            elif (key[0], candidate) not in directory:
                status = "crosswalk_site_missing_annual_directory"
            else:
                uid = candidate
                status = "official_site_and_directory_agree" if candidate in h else "official_site_alias_annual_unitid_verified"
        elif len(h) == 1:
            uid = next(iter(h))
            status = "unique_annual_directory_only"
        elif len(h) > 1:
            status = "multiple_exact_directory_unitids"
        else:
            status = "annual_directory_unavailable" if key[0] not in available_years else "no_annual_site_match"
        record = directory.get((key[0], uid), {})
        candidate = record
        source_descriptor = descriptors[(row["opeid8"], row["award_year"])]
        placeholder = (row["opeid8"] == "88888800" and str(source_descriptor.get("school", "")).strip().upper() ==
                       "DEFAULT SCHOOL FOR CONSOLIDATED LOANS")
        row["fsa_source_identity_kind"] = "noninstitutional_placeholder" if placeholder else "institution_or_unverified_reporting_unit"
        hd_state = str(candidate.get("stabbr", "")).strip().upper()
        fsa_state = str(source_descriptor.get("state", "")).strip().upper()
        domestic = US_STATES | TERRITORIES
        geography_conflict = uid is not None and (
            row["ipeds_geography_class"] == "foreign_explicit" and hd_state in domestic or
            row["ipeds_geography_class"] in {"us_states_dc", "territories_and_freely_associated_states"} and hd_state == "FC")
        row["ipeds_candidate_unitid_before_descriptor_review"] = uid
        row["ipeds_candidate_instnm"] = candidate.get("instnm")
        row["ipeds_candidate_stabbr"] = candidate.get("stabbr")
        row["ipeds_identity_geography_conflict"] = bool(geography_conflict)
        row["ipeds_fsa_hd_state_conflict"] = bool(uid is not None and fsa_state in domestic and hd_state in domestic and fsa_state != hd_state)
        fsa_name, hd_name = school_compare_key(source_descriptor.get("school")), school_compare_key(candidate.get("instnm"))
        similarity = SequenceMatcher(None, fsa_name, hd_name).ratio() if fsa_name and hd_name else None
        row["ipeds_fsa_hd_name_similarity"] = similarity
        row["ipeds_fsa_hd_name_review_required"] = similarity is not None and similarity < .5
        if geography_conflict:
            uid, record, status = None, {}, "fsa_ipeds_geography_identity_conflict"
        if placeholder:
            uid, record, status = None, {}, "noninstitutional_source_placeholder"
        admin = _int(record.get("sector")) == 0
        row["unitid"] = uid
        row["ipeds_resolution_status"] = status
        row["ipeds_annual_identity_eligible"] = uid is not None and not admin
        row["ipeds_identity_is_administrative"] = admin
        row["ipeds_title_iv_status"] = title_iv_status(record)
        row["ipeds_activity_code"] = record.get("act")
        row["ipeds_current_year_active_code"] = record.get("cyactive")
        row["ipeds_closed_with_data"] = str(record.get("act", "")).strip() == "M"
        row["ipeds_identity_uses_historical_relation"] = bool(official and str(official.get("cw_source_code")) == "3")
        row["ipeds_identity_strict_contemporaneous"] = bool(uid is not None and uid in h and len(h) == 1)
        row["ipeds_scope_alignment"] = "institution_identity_only_aid_scope_unverified" if uid is not None else "unresolved_no_site_allocation"
        row["ipeds_has_other_parent_unitid"] = bool(uid is not None and p - {uid})
        row["ipeds_component_scope_review_required"] = False
        for field in [c for c in hd if c.startswith(("prch_", "idx_", "prchtp_"))]:
            row[field] = record.get(field)
        for component, suffix in COMPONENTS.items():
            scope = component_scope(record, component)
            row["ipeds_" + component + "_scope"] = scope
            row["prch_" + suffix] = record.get("prch_" + suffix)
            row["idx_" + suffix] = record.get("idx_" + suffix)
            row["ipeds_" + component + "_parent_reference_missing"] = (
                _int(record.get("prch_" + suffix)) in {2, 3, 6} and
                (_int(record.get("idx_" + suffix)) or -2) <= 0)
            if scope != "no_parent_child_relation_reported":
                row["ipeds_component_scope_review_required"] = True
        # These describe the resolved annual UNITID, which can differ from the
        # original HD candidate or be an alias absent from HD's OPEID column.
        for field in ["instnm", "stabbr", "city", "zip", "opeflag", "sector", "cyactive", "act", "newid", "deathyr", "closedat", "pset4flg", "postsec", "pseflag", "rptmth", "f1systyp", "f1sysnam", "f1syscod"]:
            row["ipeds_" + field] = record.get(field)
        for field in ["ipeds_source_path", "ipeds_source_sha256", "ipeds_source_member"]:
            row[field] = record.get(field)
        # Preserve the restrictive historical sensitivity definition, but never
        # certify a singleton when official additional site matches contradict it.
        row["ipeds_strict_eligible"] = bool(old["ipeds_strict_eligible"] and row["ipeds_annual_identity_eligible"] and
            uid == _int(row["ipeds_directory_unitid"]) and not crosswalk_temporal_conflict)
        row["ipeds_reporting_scope"] = "singleton_directory_group_screened" if row["ipeds_strict_eligible"] else "requires_scope_review"
        if old["ipeds_strict_eligible"] and not row["ipeds_strict_eligible"]:
            row["ipeds_strict_exclusion_reasons"] = (old["ipeds_strict_exclusion_reasons"] + ";" +
                ("crosswalk_anchor_scope_or_identity_conflict" if crosswalk_temporal_conflict else status)).strip(";")
        for candidate in sorted(h | s):
            members.append({"opeid8": row["opeid8"], "award_year": row["award_year"], "ipeds_year": key[0], "unitid": candidate,
                            "relation_type": "direct_site_candidate", "in_directory_exact": candidate in h, "in_official_site_matches": candidate in s,
                            "identity_resolved_to_candidate": uid == candidate, "cw_source_code": official.get("cw_source_code") if official else None})
        rows.append(row)
    bridge = pd.DataFrame(rows)
    for c in ["unitid", "ipeds_directory_unitid", "ipeds_candidate_unitid_before_descriptor_review"]: bridge[c] = bridge[c].astype("Int64")
    for c in ["opeid8", "award_year"]: bridge[c] = bridge[c].astype(panel[c].dtype)
    linked = panel.merge(bridge, on=["opeid8", "award_year"], how="left", validate="one_to_one", sort=False)
    pd.testing.assert_frame_equal(linked[panel.columns].reset_index(drop=True), panel.reset_index(drop=True), check_dtype=True)
    strict = linked.loc[linked.ipeds_strict_eligible].copy()
    if strict.duplicated(["unitid", "award_year"]).any(): raise AssertionError("Strict UNITID-year is not unique")
    memberships = pd.DataFrame(members, columns=["opeid8", "award_year", "ipeds_year", "unitid", "relation_type", "in_directory_exact", "in_official_site_matches", "identity_resolved_to_candidate", "cw_source_code"])
    memberships["unitid"] = memberships["unitid"].astype("Int64")
    return bridge, linked, strict, memberships


def institution_count_reconciliation(bridge: pd.DataFrame, hd: pd.DataFrame,
                                     memberships: pd.DataFrame | None = None) -> pd.DataFrame:
    """Different universes remain separate; report both row and unique-ID counts."""
    result = []
    for ay, frame in bridge.groupby("award_year", observed=True):
        year = int(frame.ipeds_year.iloc[0])
        directory = hd.loc[hd.ipeds_year.eq(year)]
        pset = pd.to_numeric(directory.get("pset4flg", pd.Series(index=directory.index, dtype=float)), errors="coerce")
        sector = pd.to_numeric(directory.get("sector", pd.Series(index=directory.index, dtype=float)), errors="coerce")
        eligible = frame.ipeds_annual_identity_eligible
        matched = set(frame.loc[eligible, "unitid"].dropna().astype(int))
        universe = set(directory.loc[pset.isin([1, 3] if year >= 2010 else [1]), "unitid"].astype(int))
        member_ids = set(memberships.loc[memberships.award_year.eq(ay), "unitid"].dropna().astype(int)) if memberships is not None else set()
        # A disproved identity must not count as institutional representation.
        # Other unresolved direct memberships describe possible group coverage,
        # never unique assignments or allocated dollars.
        if memberships is not None:
            contradicted = frame.loc[frame.ipeds_identity_geography_conflict, ["opeid8", "award_year"]]
            year_members = memberships.loc[memberships.award_year.eq(ay)].merge(contradicted.assign(contradicted=True), on=["opeid8", "award_year"], how="left")
            member_ids = set(year_members.loc[~year_members.contradicted.fillna(False).astype(bool), "unitid"].dropna().astype(int))
        result.append({"award_year": ay, "ipeds_year": year, "fsa_reporting_units": len(frame),
                       "fsa_noninstitutional_source_placeholders": int(frame.fsa_source_identity_kind.eq("noninstitutional_placeholder").sum()),
                       "fsa_foreign_explicit": int(frame.ipeds_geography_class.eq("foreign_explicit").sum()),
                       "fsa_us_states_dc": int(frame.ipeds_geography_class.eq("us_states_dc").sum()),
                       "fsa_territories_and_freely_associated_states": int(frame.ipeds_geography_class.eq("territories_and_freely_associated_states").sum()),
                       "fsa_geography_unknown_or_conflicting": int(frame.ipeds_geography_class.isin(["unknown", "conflicting_geography"]).sum()),
                       "fsa_identity_resolved_rows": int(eligible.sum()), "fsa_unique_resolved_institution_unitids": len(matched),
                       "fsa_unique_resolved_more_than_one_opeid": int(frame.loc[eligible].groupby("unitid").size().gt(1).sum()),
                       "fsa_unresolved_foreign": int((~eligible & frame.ipeds_geography_class.eq("foreign_explicit")).sum()),
                       "fsa_unresolved_us_states_dc": int((~eligible & frame.ipeds_geography_class.eq("us_states_dc")).sum()),
                       "fsa_one_to_many_official_site_rows": int(frame.ipeds_resolution_status.eq("multiple_official_site_unitids").sum()),
                       "fsa_directory_crosswalk_conflicts": int(frame.ipeds_resolution_status.eq("directory_crosswalk_identity_conflict").sum()),
                       "ipeds_all_directory_unitids": len(directory), "ipeds_administrative_unitids": int(sector.eq(0).sum()),
                       "ipeds_without_valid_full_opeid": int(directory.opeid8.isna().sum()),
                       "ipeds_pset4flg_universe_available": bool(pset.notna().any()), "ipeds_pset4flg_comparison_universe": len(universe) if pset.notna().any() else None,
                       "resolved_fsa_in_comparison_universe": len(matched & universe) if pset.notna().any() else None,
                       "comparison_universe_without_unique_fsa_identity": len(universe - matched) if pset.notna().any() else None,
                       "resolved_fsa_outside_comparison_universe": len(matched - universe) if pset.notna().any() else None,
                       "comparison_universe_with_any_direct_fsa_candidate": len(universe & member_ids) if pset.notna().any() and memberships is not None else None,
                       "comparison_universe_with_candidates_but_no_unique_identity": len((universe & member_ids) - matched) if pset.notna().any() and memberships is not None else None,
                       "comparison_universe_without_any_direct_fsa_candidate": len(universe - member_ids) if pset.notna().any() and memberships is not None else None,
                       "scope_note": "HD reference-year universe; FSA observed award-year reporting units. No-volume, foreign, agency scope, temporal coverage and one-to-many relations can differ."})
    return pd.DataFrame(result)
