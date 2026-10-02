#!/usr/bin/env python3
"""Export an accepted frozen UNITID panel with labels, codebooks and readback checks."""
import argparse
from pathlib import Path
from fsa_portable_exports import export_release

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,help='Validated research release root')
    parser.add_argument('--output-dir',help='New empty directory (default: ROOT/Exports/unitid)')
    parser.add_argument('--formats',nargs='+',choices=['parquet','csv','stata','excel'],default=['parquet','csv','stata','excel'])
    args=parser.parse_args()
    export_release(args.root,args.output_dir or Path(args.root)/'Exports/unitid',formats=tuple(args.formats))
