"""Validate and freeze evidence for an explicitly versioned IPEDS metadata repair.

The SHA256SUMS check establishes consistency with the supplied upstream receipt;
without an independently trusted digest it is not proof of publisher identity.
Only consumed data and packaged evidence are hashed here, not every upstream export.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


PANEL = 'Panels/v2/panel_clean_prch_2004_2023.parquet'
LINEAGE = 'Checks/v2/wide_qc/qc_value_lineage.parquet'
PRCH = 'Checks/v2/prch_qc/'
REQUIRED = (
    PANEL,
    'Dictionary/v2/dictionary_lake.parquet',
    'Dictionary/v2/dictionary_codes.parquet',
    LINEAGE,
    PRCH + 'prch_run_manifest.json',
    'README.md',
)
FROZEN_PREFIX = 'ipeds/source_metadata_repair/'
HASH = re.compile(r'^[0-9a-fA-F]{64}$')
SUM_LINE = re.compile(r'^([0-9a-fA-F]{64}) [ *](.+)$')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _relative(value: Any) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'Unsafe repair manifest path: {value!r}')
    path = PurePosixPath(value)
    if (path.is_absolute() or value != path.as_posix()
            or any(part in ('', '.', '..') for part in value.split('/'))
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f'Unsafe repair manifest path: {value!r}')
    return value


def _file(root: Path, relative: str) -> Path:
    path = root / _relative(relative)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f'Repair artifact escapes source root or is not a file: {relative}')
    return path


def _records(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError('Repair manifest artifacts must be a list')
    result = {}
    for record in items:
        if not isinstance(record, dict):
            raise ValueError('Repair manifest artifact must be an object')
        path = _relative(record.get('path'))
        if path in result:
            raise ValueError(f'Duplicate repair manifest path: {path}')
        if (not isinstance(record.get('bytes'), int)
                or isinstance(record['bytes'], bool) or record['bytes'] < 0
                or not isinstance(record.get('sha256'), str)
                or not HASH.fullmatch(record['sha256'])):
            raise ValueError(f'Invalid repair artifact digest or size: {path}')
        result[path] = record
    return result


def _checksum_receipt(root: Path, records: dict[str, dict[str, Any]]) -> dict[str, str]:
    sums = {}
    for line in _file(root, 'SHA256SUMS').read_text().splitlines():
        if not line.strip():
            continue
        match = SUM_LINE.fullmatch(line)
        if not match:
            raise ValueError('Repair SHA256SUMS must use conventional SHA-256 filename lines')
        digest, relative = match.groups()
        relative = _relative(relative)
        if relative in sums:
            raise ValueError(f'Duplicate SHA256SUMS path: {relative}')
        sums[relative] = digest.lower()
    if sums.get('manifest.json') != _sha256(_file(root, 'manifest.json')):
        raise ValueError('Repair manifest.json SHA256SUMS mismatch or missing receipt entry')
    for relative, record in records.items():
        if sums.get(relative) != record['sha256'].lower():
            raise ValueError(f'Repair manifest and SHA256SUMS disagree: {relative}')
    return sums


def source_repair_inputs(
    ipeds_root: Path | str, ipeds_panel: Path | str
) -> tuple[list[tuple[Path, str]], dict[str, Any] | None]:
    """Return evidence-copy sources and a verified upstream repair summary.

    Destinations in ``sources`` are relative to the downstream ``Inputs`` folder.
    Summary evidence mapping destinations are relative to the downstream bundle
    root, so the archived dictionary's original contracts paths remain resolvable.
    A conventional baseline root returns ``([], None)`` unchanged.
    """
    root = Path(ipeds_root).resolve(strict=True)
    manifest_path = root / 'manifest.json'
    if not manifest_path.exists():
        return [], None
    manifest_path = _file(root, 'manifest.json')
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError('IPEDS root manifest must be an object')
    if 'correction_version' not in manifest:
        return [], None
    version = manifest['correction_version']
    if not isinstance(version, str) or not version.strip():
        raise ValueError('Repair correction_version must be a nonempty string')
    if manifest.get('status') != 'verified':
        raise ValueError('Metadata repair bundle must have status verified')
    records = _records(manifest.get('artifacts'))
    _checksum_receipt(root, records)
    if Path(ipeds_panel).resolve(strict=True) != _file(root, PANEL).resolve(strict=True):
        raise ValueError('Selected IPEDS panel is not the recorded corrected clean panel')

    # All manifest paths must stay inside the repair root, even if not consumed.
    # Existence/hashes for unrelated exports are checked by the upstream audit.
    for relative in records:
        if not (root / relative).resolve().is_relative_to(root):
            raise ValueError(f'Repair manifest path escapes source root: {relative}')
    evidence = sorted(path for path in records if path.startswith('Evidence/'))
    if not evidence:
        raise ValueError('Metadata repair bundle contains no recorded Evidence files')
    disk_evidence = {
        p.relative_to(root).as_posix()
        for p in (root / 'Evidence').rglob('*') if p.is_file()
    }
    if disk_evidence != set(evidence):
        raise ValueError('Evidence directory contains missing or unrecorded files')
    prch = sorted(path for path in records if path.startswith(PRCH))
    disk_prch = {
        p.relative_to(root).as_posix()
        for p in (root / PRCH).rglob('*') if p.is_file()
    }
    if disk_prch != set(prch):
        raise ValueError('PRCH directory contains missing or unrecorded files')
    needed = set(REQUIRED) | set(evidence) | set(prch)
    verified = {}
    for relative in sorted(needed):
        if relative not in records:
            raise ValueError(f'Repair manifest omits required artifact: {relative}')
        path = _file(root, relative)
        record = records[relative]
        if path.stat().st_size != record['bytes'] or _sha256(path) != record['sha256'].lower():
            raise ValueError(f'Repair artifact size or SHA-256 mismatch: {relative}')
        verified[relative] = {'bytes': record['bytes'], 'sha256': record['sha256'].lower()}

    prch_manifest = json.loads(_file(root, PRCH + 'prch_run_manifest.json').read_text())
    if prch_manifest.get('status') != 'complete':
        raise ValueError('Repaired PRCH manifest is not complete')
    prch_names = set()
    for artifact in prch_manifest.get('artifacts', []):
        name = _relative(artifact.get('name'))
        if '/' in name or name in prch_names:
            raise ValueError(f'Invalid or duplicate PRCH artifact name: {name}')
        prch_names.add(name)
        relative = PRCH + name
        if (relative not in verified
                or artifact.get('bytes') != verified[relative]['bytes']
                or artifact.get('sha256', '').lower() != verified[relative]['sha256']):
            raise ValueError(f'PRCH manifest disagrees with repaired artifact: {relative}')
    if 'panel_clean.parquet' not in prch_names:
        raise ValueError('PRCH manifest omits panel_clean.parquet')
    if verified[PRCH + 'panel_clean.parquet'] != verified[PANEL]:
        raise ValueError('Selected clean panel differs from verified PRCH output')

    freeze = evidence + ['README.md', 'manifest.json', 'SHA256SUMS']
    sources = [(_file(root, relative), FROZEN_PREFIX + relative) for relative in freeze]
    evidence_map = {
        'contracts/' + relative.removeprefix('Evidence/'):
            'Inputs/' + FROZEN_PREFIX + relative
        for relative in evidence if relative.startswith('Evidence/source_metadata_corrections/')
    }
    unavailable = []
    for relative in evidence:
        if not relative.endswith('.evidence.json'):
            continue
        document = json.loads(_file(root, relative).read_text())
        if document.get('correction_version') != version:
            raise ValueError(f'Repair evidence version disagrees: {relative}')
        value = document.get('imputation_flag_evidence')
        if value is not None:
            unavailable.append({
                'source': 'Inputs/' + FROZEN_PREFIX + relative,
                'field': 'imputation_flag_evidence',
                'documented_statement': value,
            })
    return sources, {
        'correction_version': version,
        'status': 'verified_consumed_inputs_and_frozen_evidence',
        'upstream_manifest_sha256': _sha256(manifest_path),
        'upstream_checksum_receipt_sha256': _sha256(root / 'SHA256SUMS'),
        'selected_panel_path': PANEL,
        'selected_panel_sha256': verified[PANEL]['sha256'],
        'source_lineage_sha256': verified[LINEAGE]['sha256'],
        'verified_artifacts': verified,
        'verification_scope': 'Consumed panel, dictionaries, full source lineage, all recorded PRCH artifacts, and all frozen Evidence files and README; other upstream exports are not rehashed here.',
        'checksum_trust': 'SHA256SUMS consistency verified; publisher identity requires an independently trusted receipt.',
        'evidence_path_map': evidence_map,
        'documented_unavailable_flags': unavailable,
        'frozen_root': 'Inputs/' + FROZEN_PREFIX.rstrip('/'),
    }
