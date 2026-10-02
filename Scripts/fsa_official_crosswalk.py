"""Lossless reader for NCES/FSA College Scorecard historical OPEID crosswalks.

This module supplies relationships and original evidence; it assigns no aid and
does not choose one UNITID from multiple primary/additional/parent candidates.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

SOURCE_LABELS = {
    "1": "ipeds_header_match", "2": "institution_reported_reporting_map",
    "3": "relationship_established_from_other_years", "4": "official_manual_match",
    "5": "no_match",
}
NO_MATCH = {"", "-2", "no match", "no matches", "nan", "none", "<na>"}
REQUIRED = {"OPEID", "IPEDSMatch", "AddMatch", "Source", "OPEIDMain", "IPEDSMain", "IPEDSrpt"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def _opeid(value: object) -> str | None:
    text = _text(value)
    if text.lower() in NO_MATCH:
        return None
    if not re.fullmatch(r"\d{1,8}(?:\.0+)?", text):
        raise ValueError(f"Invalid full OPEID token in official crosswalk: {text!r}")
    text = text.split(".")[0].zfill(8)
    if int(text) == 0:
        raise ValueError("Zero OPEID in official crosswalk")
    return text


def parse_unitid_tokens(value: object, *, allow_multiple: bool) -> tuple[int, ...]:
    """Parse only documented full UNITID tokens and explicit separators.

    Do not mine arbitrary text for numbers; unknown formatting fails closed.
    """
    text = _text(value)
    if text.lower() in NO_MATCH:
        return ()
    tokens = re.split(r"\s*\|\s*", text) if allow_multiple else [text]
    ids = []
    for token in tokens:
        if not re.fullmatch(r"[1-9]\d{5}(?:\.0+)?", token):
            raise ValueError(f"Invalid UNITID token in official crosswalk: {text!r}")
        ids.append(int(token.split(".")[0]))
    # Duplicate mentions are retained in raw fields and surfaced separately;
    # the relationships table expresses a set of IDs per relation role.
    return tuple(dict.fromkeys(ids))


def parse_crosswalk_frame(frame: pd.DataFrame, year: int, *, source_path: str = "",
                          source_sha256: str = "", preliminary: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return role-specific relations and one evidence record per workbook row."""
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Crosswalk {year} missing required columns: {sorted(missing)}")
    records, relations = [], []
    for excel_row, raw in enumerate(frame.to_dict("records"), start=2):
        opeid = _opeid(raw["OPEID"])
        if opeid is None:
            raise ValueError(f"Crosswalk {year}, row {excel_row}: OPEID is missing")
        parse_issues = {}
        def parse_field(field: str, multiple: bool) -> tuple[int, ...]:
            try:
                return parse_unitid_tokens(raw[field], allow_multiple=multiple)
            except ValueError:
                # Published AddMatch cells can be truncated mid-identifier.
                # Preserve valid full tokens and the unparsed evidence while
                # explicitly blocking any claim of a complete candidate union.
                valid, unparsed = [], []
                for token in re.split(r"\s*\|\s*", _text(raw[field])) if multiple else [_text(raw[field])]:
                    try:
                        valid.extend(parse_unitid_tokens(token, allow_multiple=False))
                    except ValueError:
                        unparsed.append(token)
                parse_issues[field] = unparsed
                return tuple(dict.fromkeys(valid))
        primary = parse_field("IPEDSMatch", False)
        additional = parse_field("AddMatch", True)
        parents = parse_field("IPEDSMain", True)
        parent_opeid = _opeid(raw["OPEIDMain"])
        source = _text(raw["Source"])
        if re.fullmatch(r"[1-5]\.0+", source):
            source = source.split(".")[0]
        if source not in SOURCE_LABELS:
            raise ValueError(f"Crosswalk {year}, row {excel_row}: unrecognized Source code {source!r}")
        reporting = _text(raw["IPEDSrpt"])
        if re.fullmatch(r"(?:1|2|-2)\.0+", reporting):
            reporting = reporting.split(".")[0]
        if reporting not in {"1", "2", "-2"}:
            raise ValueError(f"Crosswalk {year}, row {excel_row}: unrecognized IPEDSrpt {reporting!r}")
        site_union = tuple(sorted(set(primary + additional)))
        evidence = {
            "cw_year": int(year), "opeid8": opeid, "cw_source_row": excel_row,
            "cw_source_path": source_path, "cw_source_sha256": source_sha256,
            "cw_preliminary": preliminary, "cw_source_code": source,
            "cw_source_method": SOURCE_LABELS[source], "cw_ipeds_reporting_code": reporting,
            "cw_parent_opeid8": parent_opeid, "cw_site_candidate_count": len(site_union),
            "cw_primary_unitid": primary[0] if primary else None,
            "cw_additional_candidate_count": len(additional), "cw_parent_candidate_count": len(parents),
            "cw_has_additional_match": bool(additional), "cw_multiple_site_unitids": len(site_union) > 1,
            "cw_relation_parse_complete": not parse_issues,
            "cw_site_parse_complete": not any(k in parse_issues for k in ["IPEDSMatch", "AddMatch"]),
            "cw_parse_issues_json": json.dumps(parse_issues, sort_keys=True),
            "cw_site_parent_unitids_differ": bool(site_union and parents and set(site_union) != set(parents)),
            "cw_official_no_match": not site_union and not any(k in parse_issues for k in ["IPEDSMatch", "AddMatch"]),
            "cw_has_change_notes": any(_text(raw.get(f"COA{i}")).lower() not in NO_MATCH for i in range(1, 6)),
            "cw_has_prior_id_notes": any(_text(raw.get(k)).lower() not in NO_MATCH for k in ["PY_UNITID", "PY_OPEID", "PYNotes"]),
        }
        # Preserve labels, codes, previous IDs and affiliation events exactly as
        # workbook text, with a prefix that cannot be confused with panel data.
        evidence.update({"cw_raw_" + key: _text(value) for key, value in raw.items()})
        records.append(evidence)
        common = {k: evidence[k] for k in ["cw_year", "opeid8", "cw_source_row", "cw_source_path", "cw_source_sha256",
                  "cw_preliminary", "cw_source_code", "cw_source_method", "cw_ipeds_reporting_code", "cw_parent_opeid8",
                  "cw_relation_parse_complete", "cw_site_parse_complete"]}
        for role, ids in [("primary_match", primary), ("additional_match", additional), ("parent_reference", parents)]:
            for unitid in ids:
                relations.append({**common, "unitid": unitid, "relation_type": role})
        if not site_union:
            relations.append({**common, "unitid": None,
                              "relation_type": "no_site_match" if evidence["cw_site_parse_complete"] else "unparsed_site_match"})
    records = pd.DataFrame(records)
    if records.empty:
        raise ValueError(f"Crosswalk {year} has no institution records")
    records["cw_primary_unitid"] = records["cw_primary_unitid"].astype("Int64")
    # Duplicate source identities cannot be silently reduced to a chosen row.
    records["cw_duplicate_opeid_rows"] = records.duplicated(["cw_year", "opeid8"], keep=False)
    relations = pd.DataFrame(relations)
    relations["unitid"] = relations["unitid"].astype("Int64")
    return relations, records


def load_official_crosswalks(directory: Path | str, years: list[int] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load annual official crosswalks: relations, original records, provenance.

    CW2000 means the 2000/2000-01 reference year, consistent with the official
    historical series beginning in 2000-01. This loader does not map that year to
    an FSA award-year anchor; the caller must explicitly choose the convention.
    A nonpreliminary file takes precedence if both vintages are supplied.
    """
    directory = Path(directory)
    found = {}
    for path in directory.glob("CW*.xls*"):
        match = re.fullmatch(r"CW(\d{4})(_prelim)?\.(xlsx|xls)", path.name, flags=re.I)
        if not match:
            continue
        year, preliminary = int(match[1]), bool(match[2])
        if years is not None and year not in years:
            continue
        found.setdefault(year, []).append((preliminary, path))
    requested = sorted(set(years)) if years is not None else sorted(found)
    archive_metadata = json.loads((directory / "download_manifest.json").read_text()) if (directory / "download_manifest.json").exists() else {}
    downloaded = {Path(item["path"]).name: item for item in archive_metadata.get("members", [])}
    all_relations, all_records, manifest = [], [], []
    for year in requested:
        choices = sorted(found.get(year, []), key=lambda pair: (pair[0], str(pair[1])))
        if not choices:
            manifest.append({"cw_year": year, "status": "missing"})
            continue
        if len([x for x in choices if x[0] == choices[0][0]]) > 1:
            raise ValueError(f"Multiple crosswalk files for {year} at the same release level")
        preliminary, path = choices[0]
        digest = _sha(path)
        provenance = downloaded.get(path.name, {})
        if provenance and provenance.get("sha256") != digest:
            raise ValueError(f"Official crosswalk hash disagrees with download manifest: {path}")
        workbook = pd.ExcelFile(path)
        if "Crosswalk" not in workbook.sheet_names:
            raise ValueError(f"{path} has no Crosswalk sheet")
        raw = workbook.parse("Crosswalk", dtype=str)
        relations, records = parse_crosswalk_frame(raw, year, source_path=str(path.resolve()), source_sha256=digest, preliminary=preliminary)
        label_sheets = {}
        for sheet in workbook.sheet_names:
            if "label" in sheet.lower() or "note" in sheet.lower():
                label_sheets[sheet] = workbook.parse(sheet, header=None, dtype=str).fillna("").values.tolist()
        unmatched_sheet = next((s for s in workbook.sheet_names if "not match" in s.lower()), None)
        unmatched_rows = len(workbook.parse(unmatched_sheet, dtype=str)) if unmatched_sheet else None
        manifest.append({"cw_year": year, "status": "loaded", "source_path": str(path.resolve()), "sha256": digest,
                         "preliminary": preliminary, "rows": len(records), "relation_rows": len(relations),
                         "duplicate_opeid_rows": int(records["cw_duplicate_opeid_rows"].sum()),
                         "unmatched_ipeds_sheet": unmatched_sheet, "unmatched_ipeds_sheet_rows_including_header": unmatched_rows,
                         "archive_url": archive_metadata.get("archive_url"), "archive_member": provenance.get("member"),
                         "archive_etag": archive_metadata.get("archive_etag"), "retrieved_utc": archive_metadata.get("retrieved_utc"),
                         "schema_columns_json": json.dumps(raw.columns.tolist()), "label_sheets_json": json.dumps(label_sheets)})
        all_relations.append(relations)
        all_records.append(records)
    if not all_records:
        raise ValueError(f"No official crosswalk files found for requested years in {directory}")
    return pd.concat(all_relations, ignore_index=True), pd.concat(all_records, ignore_index=True), pd.DataFrame(manifest)
