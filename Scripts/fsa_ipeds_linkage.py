"""Auditable, non-allocating annual FSA reporting-unit / IPEDS directory linkage.

An exact OPEID match identifies a directory record; it does not prove that the
FSA dollars cover only that UNITID.  Keep candidates separate from aid measures.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

NCES_BASE = "https://nces.ed.gov/ipeds/complete-data-files/"
NCES_LEGACY_BASE = "https://nces.ed.gov/ipeds/datacenter/data/"
EARLY_HD = {1999: "IC99_HD", 2000: "FA2000HD", 2001: "FA2001HD"}
HD_FIELDS = [
    "instnm", "stabbr", "city", "zip", "opeflag", "sector", "cyactive", "act",
    "newid", "deathyr", "closedat", "postsec", "pseflag", "pset4flg",
    "rptmth", "f1systyp", "f1sysnam", "f1syscod",
]
REQUIRED_SCOPE_FIELDS = {"prch_f", "idx_f", "prch_sfa", "idx_sfa"}
MODULE_CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def full_opeid(value: object) -> str | None:
    """Parse a *full* eight-digit ID, including numeric storage with lost zeros.

    Do not infer a six-digit root, strip arbitrary characters, truncate, or
    normalize an invalid sentinel into a real identifier.
    """
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if re.fullmatch(r"[0-9]{1,8}(?:\.0+)?", text) is None:
        return None
    text = text.split(".")[0].zfill(8)
    return text if int(text) > 0 else None


def opeid_scope_root(value: str) -> str:
    """Six-digit *diagnostic* group; first digit can encode location overflow.

    COD permits incrementing the first digit after 99 additional locations.
    Never use this group to assign UNITIDs or allocate FSA measures.
    """
    return "0" + value[1:6]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv_bytes(data: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            frame = pd.read_csv(io.BytesIO(data), dtype=str, encoding=encoding, low_memory=False)
            frame.columns = frame.columns.str.strip().str.lower()
            return frame
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode IPEDS CSV")


def _read_file(path: Path, stem: str) -> tuple[pd.DataFrame, str]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            # NCES includes revised/final CSVs alongside provisional files in
            # some archives. Prefer the revised member, and record its name.
            revised = [n for n in names if Path(n).stem.lower() in {stem.lower() + "_rv", stem.lower() + "_r"}]
            exact = [n for n in names if Path(n).stem.lower() == stem.lower()]
            choices = revised or exact
            if len(choices) != 1:
                raise ValueError(f"Cannot select one {stem} CSV from {path}: {names}")
            name = choices[0]
            return _read_csv_bytes(archive.read(name)), name
    return _read_csv_bytes(path.read_bytes()), path.name


def download_ipeds_sources(directory: Path | str, years: list[int], refresh: bool = False) -> pd.DataFrame:
    """Cache official HD/FLAGS, preferring the current complete-files endpoint.

    The legacy archive is attempted only after an explicit HTTP 404. Both
    attempts and the selected vintage remain in provenance. Cached sources are
    hash-verified against their manifests, never relabeled as newly downloaded.
    Existing files are immutable unless refresh is requested.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    jobs = [(year, EARLY_HD.get(year, f"HD{year}"), "hd") for year in sorted(set(years))]
    jobs += [(year, f"FLAGS{year}", "flags") for year in sorted(set(years)) if year >= 2004]

    def fetch(job: tuple[int, str, str]) -> dict:
        year, stem, kind = job
        path = directory / f"{stem}.zip"
        meta_path = directory / f"{stem}.download.json"
        if path.exists() and not refresh:
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            actual_sha = _sha(path)
            valid = bool(meta.get("sha256")) and meta["sha256"] == actual_sha
            result = {**meta, "ipeds_year": year, "kind": kind, "path": str(path.resolve()),
                      "sha256": actual_sha, "status": "cached" if valid else "failed",
                      "download_metadata_hash_matches": valid,
                      "url": meta.get("url"), "checked_utc": datetime.now(timezone.utc).isoformat()}
            if not valid:
                result["error"] = "Cached ZIP is missing its provenance hash or differs from the recorded hash"
            (directory / f"{stem}.attempt.json").write_text(json.dumps(result, indent=2) + "\n")
            return result
        result = {"ipeds_year": year, "kind": kind, "url": NCES_BASE + path.name,
                  "path": str(path.resolve()), "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                  "source_vintage_resolution": "current_complete_data_files_endpoint", "request_attempts": []}
        temporary = path.with_suffix(".download.zip")
        try:
            if path.exists():
                result["replaced_source_sha256"] = _sha(path)
            for base in (NCES_BASE, NCES_LEGACY_BASE):
                result["url"] = base + path.name
                response = requests.get(result["url"], timeout=45)
                result["request_attempts"].append({"url": result["url"], "http_status": response.status_code,
                                                    "resolved_url": response.url})
                if base == NCES_BASE and response.status_code == 404:
                    result.update(source_vintage_resolution="explicit_legacy_archive_fallback_after_current_endpoint_404",
                                  current_endpoint_probe_url=result["url"], current_endpoint_error="HTTP 404")
                    continue
                break
            result.update(http_status=response.status_code, resolved_url=response.url,
                          etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"))
            response.raise_for_status()
            if not zipfile.is_zipfile(io.BytesIO(response.content)):
                raise ValueError("NCES response is not a ZIP archive")
            # Validate before replacing a cached source.
            temporary.write_bytes(response.content)
            _read_file(temporary, stem)
            temporary.replace(path)
            result.update(status="downloaded", bytes=len(response.content), sha256=_sha(path))
            meta_path.write_text(json.dumps(result, indent=2) + "\n")
        except (requests.RequestException, ValueError, zipfile.BadZipFile) as exc:
            result.update(status="failed", error=str(exc))
            temporary.unlink(missing_ok=True)
        (directory / f"{stem}.attempt.json").write_text(json.dumps(result, indent=2) + "\n")
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = pd.DataFrame(pool.map(fetch, jobs))
    attempts_path = directory / "download_attempts.csv"
    previous = pd.read_csv(attempts_path) if attempts_path.exists() else pd.DataFrame()
    pd.concat([previous, attempts], ignore_index=True).drop_duplicates(
        ["ipeds_year", "kind"], keep="last").sort_values(["ipeds_year", "kind"]).to_csv(attempts_path, index=False)
    with (directory / "download_history.jsonl").open("a") as handle:
        for item in attempts.to_dict("records"):
            handle.write(json.dumps(item) + "\n")
    return attempts


def _find_source(directory: Path, stem: str) -> Path | None:
    matches = [p for p in directory.rglob("*") if p.is_file()
               and p.suffix.lower() in {".zip", ".csv"} and p.stem.lower() == stem.lower()]
    if not matches:
        return None
    # An official cached ZIP is preferred to an extraction of the same file.
    matches.sort(key=lambda p: (p.suffix.lower() != ".zip", str(p)))
    if len([p for p in matches if p.suffix.lower() == matches[0].suffix.lower()]) > 1:
        raise ValueError(f"Multiple sources for {stem}; supply a single source directory: {matches}")
    return matches[0]


def _source_provenance(source: Path, stem: str) -> dict:
    meta_path = source.with_name(stem + ".download.json")
    attempt_path = source.with_name(stem + ".attempt.json")
    metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    attempt = json.loads(attempt_path.read_text()) if attempt_path.exists() else {}
    return {"path": str(source.resolve()), "sha256": _sha(source),
            "url": metadata.get("url"),
            "retrieved_utc": metadata.get("retrieved_utc"), "last_modified": metadata.get("last_modified"),
            "etag": metadata.get("etag"), "http_status": metadata.get("http_status"),
            "resolved_url": metadata.get("resolved_url"),
            "download_metadata_hash_matches": metadata.get("sha256") == _sha(source) if metadata else None,
            "source_vintage_resolution": metadata.get("source_vintage_resolution"),
            "current_endpoint_probe_url": metadata.get("current_endpoint_probe_url"),
            "current_endpoint_error": metadata.get("current_endpoint_error"),
            "request_attempts_json": json.dumps(metadata.get("request_attempts", [])),
            "last_attempt_status": attempt.get("status"), "last_attempt_http_status": attempt.get("http_status")}


def _missing_source_record(directory: Path, year: int, stem: str, kind: str) -> dict:
    attempt_path = directory / f"{stem}.attempt.json"
    attempt = json.loads(attempt_path.read_text()) if attempt_path.exists() else {}
    return {"ipeds_year": year, "kind": kind, "status": "missing", "expected_stem": stem,
            "url": NCES_BASE + stem + ".zip", "last_attempt_status": attempt.get("status"),
            "last_attempt_http_status": attempt.get("http_status"), "last_attempt_error": attempt.get("error")}


def load_ipeds_directory(directory: Path | str, years: list[int], *, include_unlinked: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read raw annual HD (+ FLAGS) without using a derived IPEDS research panel."""
    directory = Path(directory)
    frames, manifest, rejected = [], [], []
    for year in sorted(set(years)):
        stem = EARLY_HD.get(year, f"HD{year}")
        source = _find_source(directory, stem)
        if source is None:
            manifest.append(_missing_source_record(directory, year, stem, "hd"))
            if year >= 2004:
                flags_stem = f"FLAGS{year}"
                if _find_source(directory, flags_stem) is None:
                    manifest.append(_missing_source_record(directory, year, flags_stem, "flags"))
            continue
        hd, member = _read_file(source, stem)
        required = {"unitid", "opeid"}
        if not required.issubset(hd):
            raise ValueError(f"{source} lacks required columns: {required - set(hd.columns)}")
        hd["ipeds_year"] = year
        hd["opeid_raw"] = hd["opeid"]
        hd["opeid8"] = hd["opeid"].map(full_opeid)
        hd["unitid"] = pd.to_numeric(hd["unitid"], errors="raise").astype("Int64")
        if hd["unitid"].isna().any() or hd["unitid"].le(0).any() or hd["unitid"].duplicated().any():
            raise ValueError(f"IPEDS {year} does not have unique positive nonmissing UNITIDs")
        bad = hd.loc[hd["opeid8"].isna(), ["ipeds_year", "unitid", "opeid_raw", "instnm"]].copy()
        bad["reason"] = "missing_or_invalid_full_opeid"
        rejected.append(bad)
        if not include_unlinked:
            hd = hd.loc[hd["opeid8"].notna()].copy()
        hd["ipeds_source_path"] = str(source.resolve())
        hd["ipeds_source_sha256"] = _sha(source)
        hd["ipeds_source_member"] = member
        hd["ipeds_flags_available"] = REQUIRED_SCOPE_FIELDS.issubset(hd.columns)
        manifest.append({"ipeds_year": year, "kind": "hd", "status": "loaded", **_source_provenance(source, stem),
                         "member": member, "rows": len(hd), "invalid_opeid_rows": len(bad),
                         "embedded_scope_fields_complete": REQUIRED_SCOPE_FIELDS.issubset(hd.columns),
                         "embedded_scope_fields_present": ";".join(c for c in hd if c.startswith(("prch_", "idx_", "prchtp_")))})
        flags_stem = f"FLAGS{year}"
        flags_source = _find_source(directory, flags_stem)
        if flags_source is not None:
            flags, flag_member = _read_file(flags_source, flags_stem)
            flag_columns = [c for c in flags if c.startswith(("prch_", "idx_", "prchtp_"))]
            flags["unitid"] = pd.to_numeric(flags["unitid"], errors="raise").astype("Int64")
            hd = hd.drop(columns=[c for c in flag_columns if c in hd]).merge(
                flags[["unitid"] + flag_columns].assign(ipeds_flags_record=True), on="unitid", how="left", validate="one_to_one")
            hd["ipeds_flags_available"] = (hd.pop("ipeds_flags_record").fillna(False).astype(bool)
                                            & REQUIRED_SCOPE_FIELDS.issubset(flag_columns))
            manifest.append({"ipeds_year": year, "kind": "flags", "status": "loaded", **_source_provenance(flags_source, flags_stem),
                             "member": flag_member, "rows": len(flags),
                             "scope_fields_complete": REQUIRED_SCOPE_FIELDS.issubset(flag_columns),
                             "scope_fields_present": ";".join(flag_columns)})
        elif year >= 2004:
            manifest.append(_missing_source_record(directory, year, flags_stem, "flags"))
        if REQUIRED_SCOPE_FIELDS.issubset(hd.columns):
            hd["ipeds_flags_available"] &= hd[sorted(REQUIRED_SCOPE_FIELDS)].apply(
                lambda s: s.map(lambda value: _valid_scope_code(value, s.name, year))).all(axis=1)
        keep = ["ipeds_year", "unitid", "opeid8", "opeid_raw"] + HD_FIELDS
        keep += [c for c in hd if c.startswith(("prch_", "idx_", "prchtp_", "ipeds_"))]
        frames.append(hd[list(dict.fromkeys(c for c in keep if c in hd))])
    if not frames:
        raise ValueError(f"No usable annual IPEDS HD sources found in {directory}")
    data = pd.concat(frames, ignore_index=True)
    data.attrs["available_years"] = sorted(data["ipeds_year"].unique().tolist())
    reject = pd.concat(rejected, ignore_index=True) if rejected else pd.DataFrame()
    return data, pd.DataFrame(manifest), reject


def _positive(value: object) -> bool:
    try:
        return pd.notna(value) and float(value) > 0
    except (ValueError, TypeError):
        return False


def _valid_scope_code(value: object, field: str = "idx", year: int | None = None) -> bool:
    try:
        numeric = float(value)
        if not numeric.is_integer():
            return False
        if field.startswith("prch_"):
            allowed = {-2, 1, 2}
            if field == "prch_f":
                allowed |= {3, 4, 5}
                if year is not None and year >= 2016:
                    allowed.add(6)
            return numeric in allowed
        return numeric == -2 or numeric > 0
    except (ValueError, TypeError):
        return False


def _record_flags(record: dict | None) -> dict:
    if record is None:
        return {"admin": False, "inactive": False, "branch": False, "parent_child": False,
                "flags_available": False, "merger_closure": False, "status_available": False,
                "not_full_title_iv": True}
    def number(key: str) -> float | None:
        try:
            return float(record.get(key))
        except (TypeError, ValueError):
            return None
    return {
        "admin": number("sector") == 0,
        "inactive": number("cyactive") in {0, 2, 3} or str(record.get("act", "")).strip() in {"C", "D", "I", "O", "W", "X"},
        "branch": number("opeflag") == 2 or str(record.get("act", "")).strip() == "G",
        # 1999 and 2001 describe 4 as out-of-scope eligibility; 2000 and
        # 2002+ describe new participation. 8 is a new spring participant.
        "not_full_title_iv": not (number("opeflag") == 1 or
            (number("opeflag") == 4 and (number("ipeds_year") == 2000 or (number("ipeds_year") or 0) >= 2002)) or
            (number("opeflag") == 8 and (number("ipeds_year") or 0) >= 2004)),
        "parent_child": any(_positive(v) for k, v in record.items() if k.startswith(("prch_", "idx_", "prchtp_"))),
        "flags_available": (bool(record.get("ipeds_flags_available", False))
                            and all(_valid_scope_code(record.get(k), k, int(number("ipeds_year") or 0)) for k in REQUIRED_SCOPE_FIELDS)),
        "merger_closure": _positive(record.get("newid")) or _positive(record.get("deathyr"))
                          or str(record.get("closedat", "")).strip() not in {"", "-1", "-2", "-9", "nan", "None", "<NA>"},
        "status_available": number("sector") in range(10) and number("cyactive") in {1, 2, 3},
    }


def build_ipeds_linkage(panel: pd.DataFrame, hd: pd.DataFrame, anchor: str = "start") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return bridge, candidate ledger, row-preserving linked panel, strict view.

    Strict means conservative directory/reporting-scope screening, not proof
    that every FSA program or subsequent IPEDS outcome has identical scope.
    """
    if anchor not in {"start", "end"}:
        raise ValueError("anchor must be 'start' or 'end'")
    if not {"opeid8", "award_year"}.issubset(panel):
        raise ValueError("FSA panel requires opeid8 and award_year")
    if panel[["opeid8", "award_year"]].isna().any().any() or panel.duplicated(["opeid8", "award_year"]).any():
        raise ValueError("FSA panel must have unique nonmissing opeid8 + award_year keys")
    if not panel["opeid8"].map(lambda x: isinstance(x, str) and bool(re.fullmatch(r"[0-9]{8}", x)) and int(x) > 0).all():
        raise ValueError("FSA IDs must already be normalized valid eight-digit strings")
    data = hd.copy()
    for required in ("ipeds_year", "unitid", "opeid8"):
        if required not in data:
            raise ValueError(f"IPEDS directory requires {required}")
    for numeric_column in ("unitid", "ipeds_year"):
        try:
            data[numeric_column] = pd.to_numeric(data[numeric_column], errors="raise").astype("Int64")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"IPEDS {numeric_column} must contain integers") from exc
        if data[numeric_column].isna().any() or data[numeric_column].le(0).any():
            raise ValueError(f"IPEDS {numeric_column} must contain positive nonmissing integers")
    if data.duplicated(["ipeds_year", "unitid"]).any():
        raise ValueError("IPEDS UNITID + year must be unique")
    if not data["opeid8"].map(lambda x: isinstance(x, str) and bool(re.fullmatch(r"[0-9]{8}", x)) and int(x) > 0).all():
        raise ValueError("IPEDS IDs must already be normalized valid eight-digit strings")
    data["opeid_scope_root"] = data["opeid8"].map(opeid_scope_root)
    data["unitid"] = pd.to_numeric(data["unitid"], errors="raise").astype("Int64")
    available_years = set(data.attrs.get("available_years", data["ipeds_year"].unique()))
    # Build dictionaries in one pass. Converting hundreds of thousands of
    # single-row pandas groups independently is prohibitively expensive.
    exact, groups, record_flags = defaultdict(list), defaultdict(list), {}
    for record in data.to_dict("records"):
        reference_year = int(record["ipeds_year"])
        exact[(reference_year, record["opeid8"])].append(record)
        groups[(reference_year, record["opeid_scope_root"])].append(record)
        record_flags[(reference_year, record["unitid"])] = _record_flags(record)
    opeid_history = data.groupby("opeid8")["unitid"].nunique().to_dict()
    unitid_history = data.groupby("unitid")["opeid8"].nunique().to_dict()
    keys = panel[["opeid8", "award_year"]].copy()
    # Accept canonical YYYY-YYYY, YYYY-YY, or integer start year.
    extracted = keys["award_year"].astype(str).str.extract(r"^(\d{4})(?:[-–](\d{2}|\d{4}))?$", expand=True)
    if extracted[0].isna().any():
        raise ValueError("award_year must be YYYY, YYYY-YY or YYYY-YYYY")
    keys["award_year_start"] = extracted[0].astype(int)
    if keys.duplicated(["opeid8", "award_year_start"]).any():
        raise ValueError("FSA panel has duplicate semantic OPEID + award-year-start keys")
    for i in keys.index:
        end = extracted.loc[i, 1]
        if pd.notna(end) and int(end) != ((keys.loc[i, "award_year_start"] + 1) % (100 if len(end) == 2 else 10000)):
            raise ValueError("award_year end must be the following calendar year")
    keys["scope_root"] = keys["opeid8"].map(opeid_scope_root)
    if "descriptor_review_required" in panel:
        # Unknown review state is conservatively unresolved; do not treat the
        # nonempty string "False" as truthy when reading an externally built panel.
        def needs_review(value: object) -> bool:
            if value is None or pd.isna(value):
                return True
            return str(value).strip().lower() not in {"false", "0", "0.0", "no"}
        keys["descriptor_review_required"] = panel["descriptor_review_required"].map(needs_review)
    fsa_groups = keys.groupby(["award_year_start", "scope_root"])["opeid8"].nunique().to_dict()
    rows, candidates = [], []
    for key in keys.to_dict("records"):
        opeid, year, root = key["opeid8"], key["award_year_start"], key["scope_root"]
        start = exact.get((year, opeid), [])
        end = exact.get((year + 1, opeid), [])
        selected_year = year if anchor == "start" else year + 1
        chosen = start if anchor == "start" else end
        record = chosen[0] if len(chosen) == 1 else None
        selected_flags = record_flags[(selected_year, record["unitid"])] if record else _record_flags(None)
        root_start = groups.get((year, root), [])
        root_end = groups.get((year + 1, root), [])
        start_id = start[0]["unitid"] if len(start) == 1 else None
        end_id = end[0]["unitid"] if len(end) == 1 else None
        sensitivity = ("year_unavailable" if not {year, year + 1}.issubset(available_years)
                       else "stable_unique" if start_id is not None and end_id is not None and start_id == end_id
                       else "different_unitid" if start_id is not None and end_id is not None
                       else "ambiguous_or_unmatched")
        status = ("ipeds_year_unavailable" if selected_year not in available_years else
                  "no_exact_match" if not chosen else "multiple_exact_unitids" if len(chosen) > 1 else
                  "exact_unique_administrative_unit" if selected_flags["admin"] else
                  "exact_unique_inactive" if selected_flags["inactive"] else "exact_unique")
        assigned = record["unitid"] if status == "exact_unique" else None
        reasons = []
        if key.get("descriptor_review_required", False): reasons.append("fsa_descriptor_review_required")
        if status != "exact_unique": reasons.append(status)
        if sensitivity != "stable_unique": reasons.append("anchor_" + sensitivity)
        if len(root_start) > 1 or len(root_end) > 1: reasons.append("multiple_ipeds_units_in_opeid_group")
        if fsa_groups[(year, root)] > 1: reasons.append("multiple_fsa_rows_in_opeid_group")
        if not opeid.endswith("00") or opeid[0] != "0": reasons.append("fsa_additional_location_identifier")
        if opeid_history.get(opeid, 0) > 1: reasons.append("opeid_maps_to_multiple_unitids_over_time")
        if assigned is not None and unitid_history.get(assigned, 0) > 1: reasons.append("unitid_has_multiple_opeids_over_time")
        all_flags = [record_flags[(r["ipeds_year"], r["unitid"])] for r in start + end]
        for flag, reason in [("branch", "ipeds_branch_flag"), ("parent_child", "ipeds_component_parent_child"),
                             ("merger_closure", "ipeds_merger_or_closure"), ("admin", "ipeds_administrative_unit"),
                             ("inactive", "ipeds_inactive"), ("not_full_title_iv", "ipeds_not_full_title_iv")]:
            if any(f[flag] for f in all_flags): reasons.append(reason)
        if any(not f["flags_available"] for f in all_flags) or not all_flags: reasons.append("parent_child_metadata_unavailable")
        if any(not f["status_available"] for f in all_flags) or not all_flags: reasons.append("activity_metadata_unavailable")
        row = {"opeid8": opeid, "award_year": key["award_year"], "ipeds_anchor": anchor,
               "ipeds_year": selected_year, "unitid": assigned, "ipeds_match_status": status,
               "ipeds_match_method": "exact_full_opeid_same_reference_year" if chosen else "none",
               "ipeds_exact_candidate_count": len(chosen), "ipeds_unitid_start": start_id,
               "ipeds_unitid_end": end_id, "ipeds_start_candidate_count": len(start), "ipeds_end_candidate_count": len(end),
               "ipeds_anchor_sensitivity": sensitivity, "ipeds_scope_root": root,
               "ipeds_root_unitid_count_start": len(root_start), "ipeds_root_unitid_count_end": len(root_end),
               "fsa_root_opeid_count": fsa_groups[(year, root)], "ipeds_opeid_lifetime_unitid_count": opeid_history.get(opeid, 0),
               "ipeds_unitid_lifetime_opeid_count": unitid_history.get(assigned, 0) if assigned is not None else 0,
               "ipeds_strict_eligible": not reasons, "ipeds_strict_exclusion_reasons": ";".join(dict.fromkeys(reasons)),
               "ipeds_reporting_scope": "singleton_directory_group_screened" if not reasons else "requires_scope_review"}
        if record:
            row.update({"ipeds_" + c: record.get(c) for c in HD_FIELDS})
            row.update({c: record.get(c) for c in data if c.startswith(("prch_", "idx_", "prchtp_", "ipeds_source_"))})
        rows.append(row)
        for reference_year in (year, year + 1):
            for candidate in groups.get((reference_year, root), []):
                candidates.append({"fsa_opeid8": opeid, "award_year": key["award_year"],
                                   "candidate_relation": "exact_opeid" if candidate["opeid8"] == opeid else "scope_group_only_no_assignment",
                                   "reference_anchor": "start" if reference_year == year else "end", **candidate})
    bridge = pd.DataFrame(rows)
    for key_column in ("opeid8", "award_year"):
        bridge[key_column] = bridge[key_column].astype(panel[key_column].dtype)
    for c in ["unitid", "ipeds_unitid_start", "ipeds_unitid_end"]:
        bridge[c] = bridge[c].astype("Int64")
    overlaps = (set(bridge) & set(panel)) - {"opeid8", "award_year"}
    if overlaps:
        raise ValueError(f"Panel already contains linkage columns: {sorted(overlaps)}")
    linked = panel.merge(bridge, on=["opeid8", "award_year"], how="left", validate="one_to_one", sort=False)
    # Exact per-cell preservation is stronger than floating-point total equality.
    pd.testing.assert_frame_equal(linked[panel.columns].reset_index(drop=True), panel.reset_index(drop=True), check_dtype=True)
    strict = linked.loc[linked["ipeds_strict_eligible"]].copy()
    if strict.duplicated(["unitid", "award_year"]).any():
        raise AssertionError("Strict research view contains duplicate UNITID + award_year")
    ledger = pd.DataFrame(candidates)
    if ledger.empty:
        ledger = pd.DataFrame(columns=["fsa_opeid8", "award_year", "candidate_relation", "reference_anchor", "ipeds_year", "opeid8", "unitid"])
    return bridge, ledger, linked, strict


def run_ipeds_linkage(panel_path: Path | str, hd_dir: Path | str, output_dir: Path | str,
                      anchor: str = "start", download_missing: bool = False, refresh_sources: bool = False,
                      crosswalk_dir: Path | str | None = None, research_root: Path | str | None = None,
                      fsa_source_repo: Path | str | None = None,
                      identity_ledger_path: Path | str | None = None) -> dict:
    from fsa_dependency import configure_source_repo, require_separate_output
    if research_root is None:
        raise ValueError("research_root is required; output parents are never interpreted as FSA inputs")
    root = Path(research_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Read-only FSA research root is missing: {root}")
    dependency = configure_source_repo(fsa_source_repo)
    source_repo = Path(dependency["repository"])
    panel_path, hd_dir = Path(panel_path).resolve(), Path(hd_dir).resolve()
    output_dir = require_separate_output(output_dir, root, source_repo, hd_dir, crosswalk_dir, panel_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Linkage output must be a new or empty directory")
    if download_missing or refresh_sources:
        require_separate_output(hd_dir, root, source_repo)
    from fsa_annual_linkage import enrich_annual_linkage, institution_count_reconciliation
    from fsa_official_crosswalk import load_official_crosswalks
    from fsa_unitid_panels import build_unitid_panel
    from fsa_unitid_panels import _identity_ledger
    ledger = Path(identity_ledger_path).expanduser().resolve() if identity_ledger_path is not None else _identity_ledger(root)
    if not ledger.is_file():
        raise FileNotFoundError(f"Reviewed identity evidence ledger is missing: {ledger}")
    from build_linkage_release import verify_source_release, verify_research_evidence
    _, _, _, accepted_release = verify_source_release(root, panel_path)
    evidence_binding = verify_research_evidence(root, panel_path, accepted_release, ledger, dependency)
    panel_sha256 = _sha(panel_path)
    panel = pd.read_parquet(panel_path)
    starts = panel["award_year"].astype(str).str[:4].astype(int)
    years = sorted(set(starts) | set(starts + 1))
    if download_missing or refresh_sources:
        download_ipeds_sources(hd_dir, years, refresh=refresh_sources)
    hd, source_manifest, rejects = load_ipeds_directory(hd_dir, years, include_unlinked=True)
    valid_hd = hd.loc[hd.opeid8.notna()].copy()
    base, candidates, _, _ = build_ipeds_linkage(panel, valid_hd, anchor=anchor)
    if crosswalk_dir is not None:
        relations, records, cw_manifest = load_official_crosswalks(crosswalk_dir, years=years)
    else:
        relations, records, cw_manifest = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    bridge, linked, strict, memberships = enrich_annual_linkage(panel, base, hd, records, relations)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"bridge": bridge, "candidates": candidates, "linked_panel": linked, "strict_panel": strict,
               "direct_site_memberships": memberships, "official_crosswalk_records": records,
               "official_crosswalk_relations": relations}
    artifacts = {}
    for name, frame in outputs.items():
        path = output_dir / f"fsa_ipeds_{name}.parquet"
        frame.to_parquet(path, index=False)
        artifacts[name] = {"path": str(path.resolve()), "rows": len(frame), "sha256": _sha(path)}
    reports = {"ipeds_source_manifest": source_manifest, "ipeds_invalid_opeid_records": rejects,
               "ipeds_institution_count_reconciliation": institution_count_reconciliation(bridge, hd, memberships),
               "ipeds_linkage_summary": bridge.groupby(["award_year", "ipeds_resolution_status", "ipeds_geography_class", "ipeds_annual_identity_eligible"], dropna=False).size().rename("rows").reset_index()}
    if not cw_manifest.empty:
        reports["ipeds_crosswalk_source_manifest"] = cw_manifest
    reasons = bridge.assign(reason=bridge["ipeds_strict_exclusion_reasons"].str.split(";")).explode("reason")
    reports["ipeds_strict_exclusions"] = reasons.loc[reasons["reason"].ne("")].groupby(["award_year", "reason"]).size().rename("rows").reset_index()
    for name, frame in reports.items():
        path = output_dir / (name + ".csv")
        frame.to_csv(path, index=False)
        artifacts[name] = {"path": str(path.resolve()), "rows": len(frame), "sha256": _sha(path)}
    # Keep every bridge field in a machine-readable schema. Raw official codes
    # are interpreted with the annual dictionary evidence, not generic labels.
    old_dictionary = Path(__file__).resolve().parents[1] / "Documentation/ipeds_linkage_fields.csv"
    documented = pd.read_csv(old_dictionary).fillna("") if old_dictionary.exists() else pd.DataFrame()
    definitions = {}
    if not documented.empty:
        if "table" in documented:
            documented = documented.loc[documented.table.eq("bridge")]
        field = "field" if "field" in documented else documented.columns[0]
        definitions = {r[field]: r for r in documented.to_dict("records")}
    schema = pd.DataFrame([{"field": c, "dtype": str(bridge[c].dtype),
        "description": str(definitions.get(c, {}).get("meaning", "Official crosswalk source field; consult original annual labels and notes" if c.startswith("cw_") else c.replace("_", " "))),
        "interpretation": str(definitions.get(c, {}).get("coding_research_use", "Identity does not certify FSA measure scope. Annual dictionary and relationship evidence must accompany analysis."))} for c in bridge])
    schema_path = output_dir / "ipeds_bridge_dictionary.csv"
    schema.to_csv(schema_path, index=False)
    artifacts["bridge_dictionary"] = {"path": str(schema_path.resolve()), "rows": len(schema), "sha256": _sha(schema_path)}
    unitid_manifest = build_unitid_panel(panel, bridge, root, output_dir / "unitid_research", memberships=memberships,
                                        identity_ledger_path=ledger, bridge_dictionary_path=schema_path)
    unitid_counts = pd.read_parquet(unitid_manifest["files"]["panel"], columns=[
        "unitid", "award_year", "included_source_family_count", "blocked_source_family_count"])
    count_rows = []
    for year, frame in unitid_counts.groupby("award_year", observed=True):
        count_rows.append({"award_year": year, "unitid_panel_rows": len(frame),
            "unitid_panel_rows_with_usable_family": int(frame.included_source_family_count.gt(0).sum()),
            "unitid_panel_rows_only_blocked_families": int(frame.included_source_family_count.eq(0).sum()),
            "unitid_panel_rows_any_blocked_family": int(frame.blocked_source_family_count.gt(0).sum())})
    count_path = output_dir / "ipeds_institution_count_reconciliation.csv"
    reports["ipeds_institution_count_reconciliation"].merge(pd.DataFrame(count_rows), on="award_year", how="left", validate="one_to_one").to_csv(count_path, index=False)
    artifacts["ipeds_institution_count_reconciliation"]["sha256"] = _sha(count_path)
    for name, path in unitid_manifest["files"].items():
        artifacts["unitid_" + name] = {"path": str(Path(path).resolve()), "sha256": _sha(Path(path))}
    unitid_manifest_path = output_dir / "unitid_research/manifest.json"
    artifacts["unitid_manifest"] = {"path": str(unitid_manifest_path.resolve()), "sha256": _sha(unitid_manifest_path)}
    if _sha(panel_path) != panel_sha256:
        raise RuntimeError("Input panel changed during linkage build; rebuild against a stable input")
    if verify_research_evidence(root, panel_path, accepted_release, ledger, dependency) != evidence_binding:
        raise RuntimeError("Accepted FSA evidence changed during linkage build")
    manifest = {"built_utc": datetime.now(timezone.utc).isoformat(), "source_panel": str(panel_path.resolve()),
                "source_panel_sha256": panel_sha256, "source_panel_rows": len(panel), "anchor": anchor,
                "read_only_research_root": str(root), "upstream_code_dependency": dependency,
                "accepted_source_evidence": evidence_binding,
                "source_identity_ledger": {"path": str(ledger), "sha256": _sha(ledger)} if ledger.exists() else None,
                "linkage_code_sha256": MODULE_CODE_SHA256, "ipeds_years_requested": years,
                "ipeds_years_available": sorted(hd["ipeds_year"].unique().tolist()),
                "crosswalk_years_available": sorted(records.cw_year.unique().tolist()) if not records.empty else [],
                "crosswalk_requested": crosswalk_dir is not None,
                "all_original_cells_preserved": True,
                "exact_assigned_rows": int(bridge["ipeds_directory_unitid"].notna().sum()),
                "resolved_identity_rows": int(bridge.unitid.notna().sum()),
                "annual_identity_eligible_rows": int(bridge.ipeds_annual_identity_eligible.sum()),
                "strict_rows": len(strict), "unitid_panel": unitid_manifest, "artifacts": artifacts,
                "scope": "Primary plus additional site relations; no amount allocation, root-only assignment, nearest-year filling, or automatic merger stitching.",
                "limitation": "Annual institution identity is not certification of program-specific campus reporting scope. Strict view is a restrictive sensitivity sample."}
    (output_dir / "ipeds_linkage_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
