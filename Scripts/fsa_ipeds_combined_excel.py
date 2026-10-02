"""Reversible cell exceptions for Excel's numeric and text limitations.

Canonical, CSV and Stata data are untouched. The existing writer's documented
cent-preserving money and tiny similarity exceptions continue to apply. Other
finite numeric values that it cannot represent are written as exact numeric
text, recorded in a cell-level ledger, and independently parsed with Python's
correctly rounded float conversion for round-trip verification. Unsupported
string cells use a visible JSON escape or ledger reference, with exact original
text retained in the same cell ledger and restored before value verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np
import pandas as pd

from fsa_portable_exports import (
    _check_metadata, _excel_col, _is_money,
    read_excel_data, verify_frame, write_excel,
)


MAX_EXACT_INTEGER = 2 ** 53
EXCEL_NUMERIC_LIMIT = 999999999999999
PRECISION_COLUMNS = [
    "unitid", "year", "canonical_name", "portable_name", "excel_cell",
    "exact_numeric_text", "reason", "exception_type", "exact_original_text_json",
    "excel_display_text", "original_text_length", "original_text_utf16_units",
    "original_text_sha256",
]
UNSUPPORTED_XML_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
OOXML_ESCAPE_LITERAL = re.compile(r"_[xX][0-9a-fA-F]{4}_")
EXCEL_TEXT_LIMIT = 32767


def _utf16_units(value):
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _text_exception(value):
    """Return reversible display/ledger details, or None for ordinary strings."""
    reasons = []
    if UNSUPPORTED_XML_TEXT.search(value):
        reasons.append("unsupported_xml_character")
    if OOXML_ESCAPE_LITERAL.search(value):
        reasons.append("literal_ooxml_escape_sequence")
    if len(value) > EXCEL_TEXT_LIMIT or (len(value) > EXCEL_TEXT_LIMIT // 2 and _utf16_units(value) > EXCEL_TEXT_LIMIT):
        reasons.append("exceeds_excel_text_length")
    if not reasons:
        return None
    exact = json.dumps(value, ensure_ascii=True)
    if json.loads(exact) != value:
        raise ValueError("Original text cannot be represented reversibly as a JSON string")
    digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()
    # Escape the underscore as JSON too, so an OOXML consumer cannot interpret
    # an original literal _xHHHH_ sequence inside the visible JSON text.
    # Escaping every underscore also covers overlapping _x005f_x000A_ forms.
    visible_json = exact.replace("_", "\\u005f")
    display = "[FSA-IPEDS JSON text] " + visible_json
    if len(display) > EXCEL_TEXT_LIMIT:
        display = f"[FSA-IPEDS full text in cell ledger; SHA256={digest}]"
        if "exceeds_excel_text_length" not in reasons:
            reasons.append("escaped_text_exceeds_excel_text_length")
    return {"exception_type": "text_escape", "exact_numeric_text": "",
            "reason": ";".join(reasons), "exact_original_text_json": exact,
            "excel_display_text": display, "original_text_length": len(value),
            "original_text_utf16_units": _utf16_units(value), "original_text_sha256": digest}


def restore_excel_text_cells(frame, records):
    """Restore ledgered original strings in an unchanged Excel Data sheet order.

    ``records`` may be dictionaries or a DataFrame read from the CSV ledger with
    ``keep_default_na=False``. The displayed value, column, row, institution/year,
    length and SHA256 must agree before restoring a cell. Returns a copy.
    """
    if isinstance(records, pd.DataFrame):
        records = records.to_dict("records")
    result = frame.copy(deep=False)
    replaced = set()
    cells = set()
    for record in records:
        if record.get("exception_type") != "text_escape":
            continue
        name = record["portable_name"]
        address = str(record["excel_cell"])
        match = re.fullmatch(r"([A-Z]+)([0-9]+)", address)
        if not match or name not in result:
            raise ValueError("Invalid Excel text ledger cell/column")
        position = int(match.group(2)) - 2
        if not 0 <= position < len(result) or match.group(1) != _excel_col(result.columns.get_loc(name) + 1):
            raise ValueError("Excel text ledger cell/column does not match Data sheet")
        if address in cells:
            raise ValueError("Duplicate Excel text ledger cell")
        cells.add(address)
        if (int(result["unitid"].iloc[position]) != int(record["unitid"])
                or int(result["year"].iloc[position]) != int(record["year"])):
            raise ValueError("Excel text ledger institution/year does not match Data sheet")
        if result[name].iloc[position] != record["excel_display_text"]:
            raise ValueError("Excel displayed text differs from text ledger")
        original = json.loads(record["exact_original_text_json"])
        if (not isinstance(original, str) or len(original) != int(record["original_text_length"])
                or _utf16_units(original) != int(record["original_text_utf16_units"])
                or hashlib.sha256(original.encode("utf-8", errors="surrogatepass")).hexdigest() != record["original_text_sha256"]):
            raise ValueError("Excel original text does not match ledger length/hash")
        if name not in replaced:
            result[name] = result[name].copy()
            replaced.add(name)
        result.iloc[position, result.columns.get_loc(name)] = original
    return result


def _accepted_numeric(value, money, similarity):
    """Match the existing writer's acceptance rule; introduce no new rounding."""
    safe = float(format(value, ".15g"))
    cent_roundoff = (
        money and round(value, 2) == round(safe, 2)
        and abs(value - round(value, 2)) <= 4 * math.ulp(max(abs(value), 1.0))
        and abs(value - safe) <= 4 * math.ulp(max(abs(value), 1.0))
    )
    similarity_roundoff = similarity and 0 <= value <= 1 and abs(value - safe) <= 5e-15
    return abs(value) <= EXCEL_NUMERIC_LIMIT and (safe == value or cent_roundoff or similarity_roundoff)


def _number_array(series, name):
    if not pd.api.types.is_numeric_dtype(series.dtype):
        raise TypeError(f"Prepared numeric export column is not numeric: {name}")
    if pd.api.types.is_integer_dtype(series.dtype):
        # Check integers before float conversion, which would erase low bits.
        if series.dropna().map(lambda value: abs(int(value)) > MAX_EXACT_INTEGER).any():
            raise ValueError(f"Unsafe integer above 2**53 in {name}")
    values = series.to_numpy(dtype="float64", na_value=np.nan)
    if np.isinf(values).any():
        raise ValueError(f"Infinite numeric value cannot be exported in {name}")
    finite = values[np.isfinite(values)]
    if (np.abs(finite) > MAX_EXACT_INTEGER).any():
        raise ValueError(f"Unsafe numeric magnitude above 2**53 in {name}")
    return values


def export_excel(frame, path, metadata, value_labels, readme):
    """Write/verify Excel and return (check dictionary, exception-cell records).

    Input ``frame`` already has portable names and encoded FSA categories, as
    produced by ``prepare_export_frame``. Numeric text exceptions are necessary
    because Excel cannot represent every IEEE-754 value as a numeric cell.
    Missing numeric values remain blank. Infinities and magnitudes above 2**53
    are rejected; this interface does not promise a lossless conversion for them.
    Unsupported strings are escaped visibly only in Excel; original text is
    recoverable from ``exact_original_text_json`` in the returned cell ledger.
    """
    _check_metadata(metadata)
    if list(frame.columns) != metadata.portable_name.tolist():
        raise ValueError("Excel metadata must enumerate every prepared column in order")
    if "unitid" not in frame or "year" not in frame:
        raise ValueError("Precision ledger requires portable unitid and year columns")
    # Assignment replaces only an affected column, leaving the original frame
    # and its arrays intact. Unaffected columns need no full-size duplicate.
    excel = frame.copy(deep=False)
    records = []
    exception_positions = {}
    exception_text = {}
    for column_index, row in enumerate(metadata.itertuples(index=False)):
        if row.export_storage == "string":
            series = frame[row.portable_name]
            # Only unusually long strings need the UTF-16 byte-count check.
            suspect = (series.str.contains(UNSUPPORTED_XML_TEXT, na=False)
                       | series.str.contains(OOXML_ESCAPE_LITERAL, na=False)
                       | series.str.len().gt(EXCEL_TEXT_LIMIT // 2).fillna(False))
            converted = None
            for position in np.flatnonzero(suspect.to_numpy(dtype=bool)):
                original = series.iloc[position]
                exception = _text_exception(original)
                if exception is None:
                    continue
                if converted is None:
                    converted = series.copy()
                converted.iloc[position] = exception["excel_display_text"]
                records.append({
                    "unitid": int(frame["unitid"].iloc[position]), "year": int(frame["year"].iloc[position]),
                    "canonical_name": row.canonical_name, "portable_name": row.portable_name,
                    "excel_cell": f"{_excel_col(column_index + 1)}{position + 2}", **exception,
                })
            if converted is not None:
                excel[row.portable_name] = converted
            continue
        values = _number_array(frame[row.portable_name], row.portable_name)
        finite = values[np.isfinite(values)]
        # Ordinary integer-valued observations require no decimal formatting.
        candidates = np.unique(finite[(finite != np.floor(finite)) | (np.abs(finite) > EXCEL_NUMERIC_LIMIT)])
        rejected = [value for value in candidates
                    if not _accepted_numeric(float(value), _is_money(row.units), row.canonical_name.endswith("name_similarity"))]
        if not rejected:
            continue
        positions = np.flatnonzero(np.isin(values, rejected))
        exception_positions[row.portable_name] = set(positions.tolist())
        exception_text[row.portable_name] = {}
        converted = frame[row.portable_name].astype(object).copy()
        for position in positions:
            number = float(values[position])
            original = frame[row.portable_name].iloc[position]
            exact = str(int(original)) if pd.api.types.is_integer_dtype(frame[row.portable_name].dtype) else repr(number)
            if float(exact) != number:
                raise ValueError("Exact numeric-text conversion failed")
            converted.iloc[position] = exact
            exception_text[row.portable_name][int(position)] = exact
            records.append({
                "unitid": int(frame["unitid"].iloc[position]), "year": int(frame["year"].iloc[position]),
                "canonical_name": row.canonical_name, "portable_name": row.portable_name,
                "excel_cell": f"{_excel_col(column_index + 1)}{position + 2}",
                "exact_numeric_text": exact,
                "exception_type": "numeric_precision",
                "reason": "exceeds_excel_15_digit_magnitude" if abs(number) > EXCEL_NUMERIC_LIMIT else "exceeds_excel_15_significant_digit_precision",
            })
        excel[row.portable_name] = converted
    numeric_count = sum(record["exception_type"] == "numeric_precision" for record in records)
    text_count = len(records) - numeric_count
    notes = dict(readme)
    notes["exact_numeric_text_cells"] = (
        f"{numeric_count} numeric observations exceed Excel's 15-digit capacity and are stored as exact text. "
        "They retain their original numeric values when read with Python float(); converting them to Excel numbers may round them. "
        "Excel numeric formulas may ignore these cells until converted. Use canonical Parquet, CSV or Stata for full-precision calculations; those values are unchanged."
    )
    notes["numeric_text_ledger"] = notes.get("numeric_text_ledger", "Checks/excel_precision_cells.csv")
    notes["escaped_original_text_cells"] = (
        f"{text_count} string observations need reversible Excel-only escaping because of unsupported XML characters, "
        "literal OOXML escape sequences, or the 32767-character/UTF-16-unit cell limit. They display a visible JSON string "
        "or a reference to the same cell ledger. The ledger's exact_original_text_json retains every original character; "
        "restore with json.loads() or Scripts/fsa_ipeds_combined_excel.py restore_excel_text_cells(). "
        "Do not treat the displayed escape/reference as the source value. Canonical Parquet, CSV and Stata are unchanged."
    )
    write_excel(excel, path, metadata, value_labels, notes)
    actual = read_excel_data(path, list(frame))
    actual = restore_excel_text_cells(actual, records)
    for row in metadata.itertuples(index=False):
        if row.export_storage == "string":
            continue
        series = actual[row.portable_name]
        text_positions = {position for position, value in enumerate(series) if isinstance(value, str)}
        if text_positions != exception_positions.get(row.portable_name, set()):
            raise ValueError(f"Excel numeric-text cells differ from precision ledger: {row.portable_name}")
        if any(series.iloc[position] != exception_text[row.portable_name][position] for position in text_positions):
            raise ValueError(f"Excel exact numeric text differs from precision ledger: {row.portable_name}")
        if text_positions:
            # Do not use pd.to_numeric on the mixed string/float series: its
            # decimal parser may round a 17-digit repr differently from float().
            actual[row.portable_name] = pd.Series(
                [np.nan if value is None or pd.isna(value) else float(value) for value in series],
                index=series.index, dtype="float64",
            )
    check = verify_frame(frame, actual, metadata, "excel")
    check.update({"excel_exact_numeric_text_cells": numeric_count, "excel_numeric_text_ledger_verified": True,
                  "excel_numeric_text_values_exact": True, "excel_escaped_original_text_cells": text_count,
                  "excel_original_text_ledger_verified": True, "excel_original_text_values_exact": True})
    return check, records
