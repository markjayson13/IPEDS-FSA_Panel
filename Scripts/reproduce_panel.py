#!/usr/bin/env python3
"""Reproduce the reviewed combined release from an external frozen bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def checked_file(root, relative, expected):
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts or not name.parts:
        raise ValueError(f'Unsafe input path: {relative}')
    path = root / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Missing or redirected input: {relative}')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError(f'Input changed since review: {relative}')


def preflight(bundle, mode='analysis', decisions=None):
    bundle = Path(bundle).resolve()
    manifest_path = bundle / 'build_manifest.json'
    if not manifest_path.is_file():
        raise ValueError('Supply the complete FSA-IPEDS_DS bundle with --bundle or IPEDS_FSA_BUNDLE.')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('completed') is not True:
        raise ValueError('The supplied master bundle is incomplete.')
    checked = {}
    if mode in ('analysis', 'all'):
        plan = json.loads((decisions or REPO / 'Analysis/Decisions/consolidation_plan.json').read_text())
        checked.update(plan['source_hashes'])
    if mode in ('masters', 'all'):
        selected = [x for x in manifest['artifacts'] if Path(x['path']).parts[0] in
                    ('Inputs', 'Metadata', 'Scripts', 'Panels') or
                    Path(x['path']).parts[:2] == ('Checks', 'canonical_ipeds_handoff')]
        if not selected:
            raise ValueError('The master bundle has no frozen input inventory.')
        checked.update({x['path']: x['sha256'] for x in selected})
    if not checked:
        raise ValueError('No reviewed inputs were selected.')
    for relative, expected in checked.items():
        checked_file(bundle, relative, expected)
    return {'bundle': str(bundle), 'mode': mode, 'checked_files': len(checked),
            'inputs_verified': True, 'data_rebuilt': False}


def commands(bundle, output, mode):
    result = []
    if mode in ('masters', 'all'):
        result.append([sys.executable, str(REPO / 'Scripts/16_build_fsa_ipeds_panel.py'),
                       '--bundle', str(bundle), '--output', str(output), '--formats', 'parquet'])
    if mode in ('analysis', 'all'):
        source = output if mode == 'all' else bundle
        destination = output / 'Analysis' if mode == 'all' else output
        result.append([sys.executable, str(REPO / 'Analysis/Scripts/build_analysis_views.py'),
                       '--root', str(source), '--output', str(destination),
                       '--decisions', str(REPO / 'Analysis/Decisions')])
    return result


def validate_destinations(bundle, output, runtime=None):
    bundle, output = Path(bundle).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(bundle):
        raise ValueError('Use a new output directory outside the frozen source bundle.')
    if runtime is not None:
        runtime = Path(runtime).expanduser()
        if runtime.is_symlink():
            raise ValueError('Runtime directory must not be a symlink.')
        resolved = runtime.resolve()
        for protected in (bundle, output.resolve()):
            if resolved == protected or resolved.is_relative_to(protected) or protected.is_relative_to(resolved):
                raise ValueError('Runtime directory must be separate from the source bundle and output.')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', help='New output directory; never the source bundle')
    parser.add_argument('--bundle', type=Path,
                        default=Path(os.environ.get('IPEDS_FSA_BUNDLE', str(REPO / 'Data'))))
    parser.add_argument('--mode', choices=['analysis', 'masters', 'all'], default='analysis',
                        help='analysis: labeled research views; masters: full Parquet views; all: both')
    parser.add_argument('--check', action='store_true', help='Verify reviewed input hashes without rebuilding')
    parser.add_argument('--prepare-only', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--runtime', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    bundle = args.bundle.resolve()
    if not args.check:
        if args.output is None:
            parser.error('Supply a new output directory, or use --check.')
        output = validate_destinations(bundle, args.output, args.runtime)
    receipt = preflight(bundle, args.mode)
    print(json.dumps(receipt, indent=2), flush=True)
    if args.check or args.prepare_only:
        return
    for command in commands(bundle, output, args.mode):
        subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
