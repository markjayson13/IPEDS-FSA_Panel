from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
from fsa_ipeds_combined_excel import PRECISION_COLUMNS, export_excel, restore_excel_text_cells
from fsa_portable_exports import read_excel_data


class CombinedExcelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.xlsx"

    def metadata(self, frame, money=(), similarity=()):
        return pd.DataFrame([
            {"canonical_name": name + ("_name_similarity" if name in similarity else ""),
             "portable_name": name, "variable_label": name, "description": "Test definition for " + name,
             "export_storage": "string" if pd.api.types.is_string_dtype(frame[name].dtype) else "numeric",
             "units": "USD" if name in money else "", "original_dtype": str(frame[name].dtype),
             "category_kind": ""}
            for name in frame
        ])

    def export(self, frame, **kwargs):
        metadata = self.metadata(frame, **kwargs)
        labels = pd.DataFrame(columns=["canonical_name", "portable_name", "code", "value", "label", "description"])
        return export_excel(frame, self.path, metadata, labels, {"dataset": "Test"})

    def test_precision_text_roundtrip_keeps_source_unchanged_and_records_cells(self):
        frame = pd.DataFrame({"unitid": [1, 2, 3, 4, 5], "year": [2023] * 5,
                              "fte": [3.779999999999998, 0.4799999999999995, 0.0, np.nan, 12.25],
                              "opeid": pd.Series(["00000100", "00000200", "", None, "-2"], dtype="string")})
        original = frame.copy(deep=True)
        check, ledger = self.export(frame)
        pd.testing.assert_frame_equal(frame, original)
        self.assertTrue(check["all_values_passed"])
        self.assertEqual(check["excel_exact_numeric_text_cells"], 2)
        self.assertEqual([row["excel_cell"] for row in ledger], ["C2", "C3"])
        self.assertEqual([row["exact_numeric_text"] for row in ledger], ["3.779999999999998", "0.4799999999999995"])
        actual = read_excel_data(self.path, list(frame))
        self.assertIsInstance(actual.fte.iloc[0], str)
        self.assertEqual(float(actual.fte.iloc[1]), frame.fte.iloc[1])
        self.assertEqual(actual.fte.iloc[2], 0.0)
        self.assertEqual(actual.opeid.iloc[0], "00000100")
        with zipfile.ZipFile(self.path) as archive:
            self.assertIn(b"Checks/excel_precision_cells.csv", archive.read("xl/worksheets/sheet4.xml"))

    def test_existing_money_and_similarity_exceptions_stay_numeric(self):
        frame = pd.DataFrame({"unitid": [1], "year": [2023], "money": [3.900000000000001],
                              "similarity": [0.4799999999999995], "other": [0.4799999999999995]})
        check, ledger = self.export(frame, money=("money",), similarity=("similarity",))
        self.assertEqual([r["portable_name"] for r in ledger], ["other"])
        self.assertEqual(check["excel_binary_rounding_cells_cents_preserved"], 1)
        self.assertEqual(check["excel_similarity_rounding_cells"], 1)
        actual = read_excel_data(self.path, list(frame))
        self.assertIsInstance(actual.money.iloc[0], float)
        self.assertIsInstance(actual.similarity.iloc[0], float)

    def test_large_exact_integer_below_float_boundary_is_text(self):
        frame = pd.DataFrame({"unitid": [1], "year": [2023], "large": pd.Series([1234567890123456], dtype="Int64")})
        check, ledger = self.export(frame)
        self.assertEqual(ledger[0]["exact_numeric_text"], "1234567890123456")
        self.assertEqual(ledger[0]["reason"], "exceeds_excel_15_digit_magnitude")
        self.assertTrue(check["all_values_passed"])

    def test_unsafe_numbers_are_refused_before_writing(self):
        for value, dtype in [(float("inf"), "float64"), (-float("inf"), "float64"),
                             (2 ** 53 + 1, "Int64"), (float(2 ** 54), "float64")]:
            with self.subTest(value=value):
                frame = pd.DataFrame({"unitid": [1], "year": [2023], "unsafe": pd.Series([value], dtype=dtype)})
                with self.assertRaises(ValueError):
                    self.export(frame)
                self.assertFalse(self.path.exists())

    def test_reader_value_or_text_tampering_does_not_pass(self):
        frame = pd.DataFrame({"unitid": [1], "year": [2023], "fte": [3.779999999999998]})
        wrong = pd.DataFrame({"unitid": [1.0], "year": [2023.0], "fte": ["3.78"]})
        with patch("fsa_ipeds_combined_excel.read_excel_data", return_value=wrong):
            with self.assertRaisesRegex(ValueError, "text differs from precision ledger"):
                self.export(frame)

    def test_control_characters_and_literal_ooxml_strings_are_reversible(self):
        text = ["Mission\n\x1f\nquality\x1ehigh", "a\x00b\x0bc", "literal _x001F_ _X000A_ _x005f_x000A_",
                "allowed\ttab\nnewline\rcarriage return", "", None, "[FSA-IPEDS JSON text] ordinary", "bad\ufffe"]
        frame = pd.DataFrame({"unitid": range(1, len(text) + 1), "year": [2017] * len(text),
                              "mission": pd.Series(text, dtype="string")})
        original = frame.copy(deep=True)
        check, ledger = self.export(frame)
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual(check["excel_exact_numeric_text_cells"], 0)
        self.assertEqual(check["excel_escaped_original_text_cells"], 4)
        self.assertTrue(check["excel_original_text_values_exact"])
        self.assertTrue(check["all_values_passed"])
        self.assertEqual([r["excel_cell"] for r in ledger], ["C2", "C3", "C4", "C9"])
        self.assertTrue(all(r["exception_type"] == "text_escape" for r in ledger))
        self.assertEqual(json.loads(ledger[0]["exact_original_text_json"]), text[0])
        actual = read_excel_data(self.path, list(frame))
        self.assertIn("\\u001f", actual.mission.iloc[0])
        self.assertNotIn("_x001F_", actual.mission.iloc[2])
        self.assertNotIn("_x000A_", actual.mission.iloc[2])
        self.assertEqual(actual.mission.iloc[3], text[3])
        # Prove the persisted ledger is sufficient, not just in-memory originals.
        ledger_path = self.path.with_suffix(".csv")
        pd.DataFrame(ledger, columns=PRECISION_COLUMNS).to_csv(ledger_path, index=False)
        restored = restore_excel_text_cells(actual, pd.read_csv(ledger_path, keep_default_na=False))
        self.assertEqual(restored.mission.astype("string").fillna("").tolist(), frame.mission.fillna("").tolist())
        self.assertIn("[FSA-IPEDS JSON text]", actual.mission.iloc[0])  # reader frame remains unchanged

    def test_cell_length_and_utf16_limits_use_exact_sidecar(self):
        text = ["a" * 32768, "\U0001f600" * 16384, "\x1f" * 9000, "b" * 32767]
        frame = pd.DataFrame({"unitid": [1, 2, 3, 4], "year": [2017] * 4,
                              "mission": pd.Series(text, dtype="string")})
        check, ledger = self.export(frame)
        self.assertEqual(check["excel_escaped_original_text_cells"], 3)
        self.assertEqual(ledger[1]["original_text_length"], 16384)
        self.assertEqual(ledger[1]["original_text_utf16_units"], 32768)
        self.assertIn("escaped_text_exceeds_excel_text_length", ledger[2]["reason"])
        self.assertTrue(all("full text in cell ledger" in r["excel_display_text"] for r in ledger))
        actual = read_excel_data(self.path, list(frame))
        self.assertEqual(actual.mission.iloc[3], text[3])
        restored = restore_excel_text_cells(actual, ledger)
        self.assertEqual(restored.mission.tolist(), text)
        self.assertTrue(check["all_values_passed"])

    def test_text_restoration_rejects_wrong_display_key_or_ledger(self):
        frame = pd.DataFrame({"unitid": [1], "year": [2017], "mission": pd.Series(["bad\x1f"], dtype="string")})
        _, ledger = self.export(frame)
        actual = read_excel_data(self.path, list(frame))
        for field, replacement, message in [
            ("excel_display_text", "wrong", "displayed text differs"),
            ("unitid", 2, "institution/year"),
            ("excel_cell", "B2", "cell/column"),
            ("exact_original_text_json", json.dumps("new!"), "length/hash"),
        ]:
            with self.subTest(field=field):
                broken = [{**ledger[0], field: replacement}]
                with self.assertRaisesRegex(ValueError, message):
                    restore_excel_text_cells(actual, broken)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            restore_excel_text_cells(actual, ledger + ledger)


if __name__ == "__main__":
    unittest.main()
