"""Storage-only numeric categories and conservative IPEDS source-ID views."""
from __future__ import annotations


def convert_numeric_category(series, rule):
    """Return nullable integers with the same nominal codes; never ordinal rank."""
    import pandas as pd
    original = series.astype('string')
    valid = original.str.fullmatch(r'-?(?:0|[1-9][0-9]*)').fillna(True)
    if not valid.all():
        bad = original.loc[~valid].drop_duplicates().head(5).tolist()
        raise ValueError(f"Noncanonical numeric category in {rule['source_column']}: {bad}")
    result = pd.to_numeric(original, errors='raise').astype(rule['target_storage'])
    if not result.astype('string').equals(original):
        raise ValueError(f"Category conversion changed values in {rule['source_column']}")
    return result


def ipeds_opeid_views(series):
    """Describe identifiers; this never joins, collapses campuses or invents 00.

    Source OPEID may contain an NCES alphanumeric reporting-branch suffix.
    The numeric view is FSA-format compatible only, not a verified FSA match.
    Input numeric storage is prohibited because its original zero padding is lost.
    """
    import pandas as pd
    from pandas.api.types import is_numeric_dtype
    if is_numeric_dtype(series.dtype):
        raise ValueError('IPEDS source OPEID must be read as string to retain leading zeros')
    raw = series.astype('string')
    if raw.str.contains(r'^\s|\s$', regex=True).fillna(False).any():
        raise ValueError('Unexpected whitespace in IPEDS OPEID; preserve and review original spelling')
    numeric = raw.str.fullmatch(r'[0-9]{8}').fillna(False) & raw.ne('00000000').fillna(False)
    alpha = raw.str.fullmatch(r'[0-9]{6}[A-Z][0-9]').fillna(False)
    absent = raw.isna()
    notapp = raw.eq('-2').fillna(False)
    status = pd.Series('unrecognized_source_identifier', index=raw.index, dtype='string')
    status.loc[absent] = 'source_missing'
    status.loc[notapp] = 'not_applicable'
    status.loc[numeric] = 'numeric8_format_only_not_verified_match'
    status.loc[alpha] = 'ipeds_alpha_reporting_branch_not_exact_fsa_id'
    return pd.DataFrame({'ipeds_opeid_source': raw.mask(notapp),
                         'ipeds_opeid8_numeric': raw.where(numeric),
                         'ipeds_opeid_format_status': status})
