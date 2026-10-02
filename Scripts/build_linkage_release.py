#!/usr/bin/env python3
"""Build a downstream UNITID linkage release from read-only FSA panel inputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from fsa_dependency import configure_source_repo, require_separate_output, sha256

FAMILIES = ("grants", "campus_based", "direct_loans", "ffel")
QA_PATHS = ("Checks/acceptance_qc/qa_fingerprints.json", "build/run_started.json",
            "build/transformations_completed.json")


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def verify_source_release(research_root, panel_path=None):
    """Bind selected input panel and dictionary to upstream acceptance evidence."""
    root = Path(research_root).expanduser().resolve()
    manifest_path = root / "build/research_release_manifest.json"
    release = json.loads(manifest_path.read_text())
    if release.get("acceptance_passed") is not True:
        raise ValueError("Upstream FSA release did not pass acceptance")
    panel = Path(panel_path).expanduser().resolve() if panel_path else root / "Panels/final/fsa_volume_reports_panel_1999_2025.parquet"
    if not panel.is_relative_to(root):
        raise ValueError("Selected FSA panel must belong to the explicit research root")
    artifacts = {}
    for record in release["artifacts"]:
        rel = Path(record["path"])
        if rel.is_absolute() or ".." in rel.parts or str(rel) in artifacts:
            raise ValueError("Unsafe or duplicate upstream artifact path")
        artifacts[str(rel)] = record
    required = [panel, root / "Dictionary/fsa_volume_panel_dictionary.parquet"]
    for path in required:
        key = str(path.relative_to(root))
        record = artifacts.get(key)
        if record is None or not path.is_file() or sha256(path) != record["sha256"]:
            raise ValueError(f"FSA input differs from accepted release: {key}")
        if "bytes" in record and path.stat().st_size != record["bytes"]:
            raise ValueError(f"FSA input size differs from accepted release: {key}")
    return root, panel, manifest_path, release


def verify_research_evidence(root, panel, release, identity_ledger, dependency):
    """Check accepted fingerprints before consuming quarantine or schema cells.

    A run-start hash alone only detects concurrent changes. These upstream
    transformation/QA records establish what was actually accepted previously.
    They are cross-bound by run ID, code/metadata, and accepted panel hashes.
    """
    import fsa_dependency
    records = []
    for relative in QA_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Accepted FSA evidence binding is missing: {relative}")
        records.append(json.loads(path.read_text()))
    qa, started, completed = records
    run_id = started.get("run_id")
    if not run_id or qa.get("run_id") != run_id or completed.get("run_id") != run_id:
        raise ValueError("FSA transformation and QA evidence belong to different runs")
    required_stages = ("skip_profile", "skip_dictionary", "skip_grants", "skip_campus",
                       "skip_loans", "skip_merge", "skip_analysis_panel")
    if any(started.get("arguments", {}).get(flag, False) for flag in required_stages):
        raise ValueError("Partial FSA transformations cannot bind accepted linkage inputs")
    code = qa.get("code_and_metadata")
    if not code or code != started.get("code_and_metadata") or code != completed.get("code_and_metadata"):
        raise ValueError("FSA code/metadata fingerprints disagree across acceptance evidence")
    release_code = {}
    for entry in release.get("code_and_metadata", []):
        if entry["path"] in release_code:
            raise ValueError("Duplicate accepted FSA code/metadata fingerprint")
        release_code[entry["path"]] = entry["sha256"]
    if any(release_code.get(name) != digest for name, digest in code.items()):
        raise ValueError("FSA QA code/metadata differ from the accepted release")
    if any(code.get(name) != digest for name, digest in dependency["modules"].items()):
        raise ValueError("Accepted FSA parsing helpers differ from the reviewed dependency")
    lock = json.loads(fsa_dependency.LOCK_PATH.read_text())
    ledger_record = lock["identity_ledger"]
    ledger_digest = sha256(identity_ledger) if identity_ledger.is_file() else None
    if ledger_digest != ledger_record["sha256"]:
        raise ValueError("Identity ledger differs from the reviewed dependency lock")
    if code.get(ledger_record["path"]) != ledger_digest:
        raise ValueError("Identity ledger differs from accepted FSA QA metadata")
    expected = qa.get("data", {})
    transformed = completed.get("data", {})
    required = [str(panel.relative_to(root)), "Dictionary/fsa_volume_panel_dictionary.parquet",
                "Checks/download_qc/selected_panel_files.csv"]
    required += [f"Checks/observation_qc/{family}_{suffix}" for family in FAMILIES
                 for suffix in ("quarantine.parquet", "schema_availability.csv")]
    # Any other matching evidence would also be consumed by the snapshot/reader;
    # reject additions rather than declaring their run-start bytes accepted.
    for pattern in ("*_quarantine.parquet", "*_schema_availability.csv"):
        required.extend(str(p.relative_to(root)) for p in (root / "Checks/observation_qc").glob(pattern))
    checked = {}
    for relative in sorted(set(required)):
        path = root / relative
        if not path.is_file() or not expected.get(relative) or sha256(path) != expected[relative]:
            raise ValueError(f"FSA input differs from accepted QA fingerprints: {relative}")
        if relative.startswith(("Panels/", "Dictionary/", "Checks/observation_qc/")):
            if transformed.get(relative) != expected[relative]:
                raise ValueError(f"FSA input is not bound to completed transformations: {relative}")
        checked[relative] = expected[relative]
    return {"run_id": run_id, "accepted_qa_inputs": checked,
            "identity_ledger_sha256": ledger_digest,
            "evidence": {p: sha256(root / p) for p in QA_PATHS}}


def input_snapshot(root, panel, manifest, identity_ledger):
    """Record files actually needed by linkage; no historical output reuse."""
    files = {panel, manifest, root / "Dictionary/fsa_volume_panel_dictionary.parquet"}
    files.update((root / "Checks/observation_qc").glob("*_schema_availability.csv"))
    files.update((root / "Checks/observation_qc").glob("*_quarantine.parquet"))
    files.update(root / path for path in QA_PATHS)
    for p in [root / "Checks/download_qc/selected_panel_files.csv", identity_ledger]:
        if p is not None and p.is_file():
            files.add(p)
    return {str(p): sha256(p) for p in sorted(files)}


def build_linkage_release(research_root, ipeds_dir, output_dir, *, fsa_source_repo=None,
                          panel_path=None, crosswalk_dir=None, identity_ledger=None,
                          anchor="start", download_missing=False, refresh_sources=False,
                          formats=("parquet",)):
    dependency = configure_source_repo(fsa_source_repo)
    root, panel, source_manifest_path, source_release = verify_source_release(research_root, panel_path)
    output = require_separate_output(output_dir, root, dependency["repository"], ipeds_dir, crosswalk_dir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Downstream release output must be a new or empty directory")
    from fsa_ipeds_linkage import run_ipeds_linkage
    from fsa_unitid_panels import _identity_ledger
    ledger = Path(identity_ledger).expanduser().resolve() if identity_ledger else _identity_ledger(root)
    evidence_binding = verify_research_evidence(root, panel, source_release, ledger, dependency)
    before = input_snapshot(root, panel, source_manifest_path, ledger)
    output.mkdir(parents=True, exist_ok=True)
    release_path = output / "build/research_release_manifest.json"
    release = {
        "release_version": "ipeds-fsa-linkage-v1",
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "completed": False, "acceptance_passed": False,
        "upstream_fsa": {"root": str(root), "release_version": source_release["release_version"],
                         "manifest_path": str(source_manifest_path), "manifest_sha256": sha256(source_manifest_path),
                         "acceptance_passed": True, "source_panel": str(panel), "source_panel_sha256": sha256(panel)},
        "upstream_code_dependency": dependency,
        "accepted_source_evidence": evidence_binding,
        "read_only_source_hashes": before,
        "scope": "Downstream institution identity linkage; no upstream FSA panelization or campus allocation.",
        "artifacts": [],
    }
    _json(release_path, release)
    linkage = run_ipeds_linkage(panel, ipeds_dir, output / "Panels/ipeds", anchor=anchor,
                                download_missing=download_missing, refresh_sources=refresh_sources,
                                crosswalk_dir=crosswalk_dir, research_root=root,
                                fsa_source_repo=dependency["repository"], identity_ledger_path=ledger)
    if not linkage.get("all_original_cells_preserved") or not linkage["unitid_panel"].get("all_conservation_passed"):
        raise ValueError("Linkage did not pass source preservation and conservation")
    if input_snapshot(root, panel, source_manifest_path, ledger) != before:
        raise ValueError("Read-only FSA inputs changed during downstream linkage")
    records = []
    for path in sorted((output / "Panels").rglob("*")):
        if path.is_file():
            records.append({"path": str(path.relative_to(output)), "bytes": path.stat().st_size, "sha256": sha256(path)})
    release.update(completed=True, acceptance_passed=True, scope_validation_passed=True,
                   acceptance_scope="Verified upstream acceptance, unchanged FSA input cells and passing UNITID source conservation; scope limitations remain.",
                   linkage={"manifest": "Panels/ipeds/ipeds_linkage_manifest.json", "rows": linkage["unitid_panel"]["rows"]},
                   artifacts=records)
    _json(release_path, release)
    from fsa_portable_exports import export_release
    export_release(output, output / "Exports/unitid", formats=tuple(formats))
    if input_snapshot(root, panel, source_manifest_path, ledger) != before:
        raise ValueError("Read-only FSA inputs changed during downstream export")
    # Keep the accepted manifest immutable after export: export metadata binds its
    # hash. Completion belongs in a separate receipt, avoiding a circular hash.
    receipt = {"completed": True, "release_manifest": str(release_path),
               "release_manifest_sha256": sha256(release_path), "source_inputs_unchanged": True,
               "unitid_rows": linkage["unitid_panel"]["rows"], "formats": list(formats),
               "exports_manifest": "Exports/unitid/export_manifest.json"}
    _json(output / "build/linkage_release_completion.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", required=True, help="Read-only accepted FSA release")
    parser.add_argument("--input-parquet", help="Accepted unfiltered FSA panel inside research root")
    parser.add_argument("--fsa-source-repo", help="Reviewed FSA code checkout; FSA_SOURCE_REPO is also supported")
    parser.add_argument("--ipeds-dir", required=True, help="Annual official HD/FLAGS source cache")
    parser.add_argument("--crosswalk-dir", help="Official annual NCES/FSA CW workbook cache")
    parser.add_argument("--identity-ledger", help="Read-only reviewed source identity decision ledger")
    parser.add_argument("--output-dir", required=True, help="New separate downstream linkage release root")
    parser.add_argument("--anchor", choices=["start", "end"], default="start")
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--refresh-sources", action="store_true")
    parser.add_argument("--formats", nargs="+", choices=["parquet", "csv", "stata", "excel"], default=["parquet"])
    args = parser.parse_args()
    result = build_linkage_release(args.research_root, args.ipeds_dir, args.output_dir,
                                  fsa_source_repo=args.fsa_source_repo, panel_path=args.input_parquet,
                                  crosswalk_dir=args.crosswalk_dir, identity_ledger=args.identity_ledger,
                                  anchor=args.anchor, download_missing=args.download_missing,
                                  refresh_sources=args.refresh_sources, formats=args.formats)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
