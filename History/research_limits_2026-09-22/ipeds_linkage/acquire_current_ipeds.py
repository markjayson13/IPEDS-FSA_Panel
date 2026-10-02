"""Acquire current annual HD/FLAGS, recording explicit legacy fallback.

Run from repository root with /usr/local/bin/python3. Existing destination
files are verified against their manifests and never silently overwritten.
Use a new --destination to intentionally capture a later source vintage.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import requests


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("ResearchBuild/IPEDS/official_hd_v2"))
    parser.add_argument("--legacy", type=Path, default=Path("ResearchBuild/IPEDS/official_hd"))
    parser.add_argument("--start", type=int, default=1999)
    parser.add_argument("--end", type=int, default=2025)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    jobs = []
    for year in range(args.start, args.end + 1):
        hd = {1999: "IC99_HD", 2000: "FA2000HD", 2001: "FA2001HD"}.get(year, f"HD{year}")
        jobs.append((year, "hd", hd))
        if year >= 2004:
            jobs.append((year, "flags", f"FLAGS{year}"))

    def acquire(job):
        year, kind, stem = job
        destination = args.destination / f"{stem}.zip"
        metadata_path = destination.with_suffix(".download.json")
        current_url = f"https://nces.ed.gov/ipeds/complete-data-files/{stem}.zip"
        if destination.exists():
            if not metadata_path.exists():
                raise RuntimeError(f"Existing input lacks manifest: {destination}")
            metadata = json.loads(metadata_path.read_text())
            if sha(destination) != metadata["sha256"]:
                raise RuntimeError(f"Cached source hash mismatch: {destination}")
            return {"file": stem, "result": "verified_cached", **metadata}
        response = requests.get(current_url, timeout=120)
        if response.status_code == 404:
            legacy_path = args.legacy / destination.name
            legacy_metadata_path = legacy_path.with_suffix(".download.json")
            if not legacy_path.exists() or not legacy_metadata_path.exists():
                raise RuntimeError(f"Current endpoint returned 404 and no documented fallback exists: {current_url}")
            metadata = json.loads(legacy_metadata_path.read_text())
            if sha(legacy_path) != metadata["sha256"]:
                raise RuntimeError(f"Legacy fallback hash mismatch: {legacy_path}")
            shutil.copyfile(legacy_path, destination)
            metadata.update({
                "path": str(destination.resolve()),
                "source_vintage_resolution": "explicit_legacy_archive_fallback_after_current_endpoint_404",
                "current_endpoint_probe_url": current_url,
                "current_endpoint_error": "HTTP 404",
                "current_endpoint_probe_utc": datetime.now(timezone.utc).isoformat(),
                "legacy_source_path": str(legacy_path.resolve()),
            })
        else:
            response.raise_for_status()
            if not response.content.startswith(b"PK"):
                raise RuntimeError(f"Expected ZIP bytes: {current_url}")
            destination.write_bytes(response.content)
            metadata = {"ipeds_year": year, "kind": kind, "url": current_url,
                        "resolved_url": response.url, "path": str(destination.resolve()),
                        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                        "bytes": len(response.content), "sha256": sha(destination),
                        "http_status": response.status_code, "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "source_vintage_resolution": "current_complete_data_files_endpoint"}
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        return {"file": stem, "result": "acquired", **metadata}

    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(acquire, jobs))
    output = args.destination / "acquisition_verification_manifest.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Verified {len(result)} annual HD/FLAGS files; manifest: {output}")


if __name__ == "__main__":
    main()
