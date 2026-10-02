"""Load an analysis CSV with its identifier types and explicit missing token."""
from pathlib import Path
import argparse
import pandas as pd

DATASETS = ('fsa_analysis', 'fsa_ipeds_analysis', 'fsa_ipeds_aid_analysis')

def load(dataset='fsa_analysis', directory=None):
    if dataset not in DATASETS:
        raise ValueError('Choose one of: ' + ', '.join(DATASETS))
    root = Path(directory) if directory is not None else Path(__file__).resolve().parent
    metadata = pd.read_csv(root/'Metadata'/f'{dataset}_codebook.csv', keep_default_na=False)
    types = {r.portable_name: ('string' if r.export_storage == 'string' else
             'Int64' if r.export_storage == 'coded_category' or 'int' in r.original_dtype.lower() else 'float64')
             for r in metadata.itertuples(index=False)}
    frame = pd.read_csv(root/f'{dataset}.csv.gz', dtype=types, keep_default_na=False,
                        na_values=['__FSA_NULL__'], float_precision='round_trip')
    keys = ['unitid', 'award_year_start' if dataset == 'fsa_analysis' else 'year']
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise ValueError('Invalid panel keys')
    return frame

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', nargs='?', default='fsa_analysis', choices=DATASETS)
    args = parser.parse_args()
    data = load(args.dataset)
    print(f'{args.dataset}: {len(data):,} rows; {len(data.columns):,} columns; keys verified')
