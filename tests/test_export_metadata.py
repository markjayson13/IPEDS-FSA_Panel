from __future__ import annotations

import json
import unittest

import pandas as pd

from helpers import load_script_module

metadata = load_script_module("fsa_export_metadata_tests", "Scripts/fsa_export_metadata.py")


def dictionary(frame, **overrides):
    rows = []
    for column in frame:
        row = {"panel_column": column, "definition": "", "units": "not_applicable", "column_role": "provenance"}
        row.update(overrides.get(column, {}))
        rows.append(row)
    return pd.DataFrame(rows)


class ExportMetadataTests(unittest.TestCase):
    def test_long_names_remain_unique_portable_and_order_independent(self):
        names = ["loan_direct_harmonized__subsidized_loans_originated_amt__observed_partial_sum",
                 "loan_direct_harmonized__unsubsidized_loans_originated_amt__observed_partial_sum",
                 "grant__opeid8", "unitid", "award_year_start", "_all", "123 wrong name"]
        actual = metadata.portable_names(names)
        self.assertEqual(actual, metadata.portable_names(list(reversed(names))))
        self.assertEqual(len(set(actual.values())), len(names))
        for name in actual.values():
            self.assertRegex(name, r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
        self.assertEqual(actual["grant__opeid8"], "grant__opeid8")
        with self.assertRaisesRegex(ValueError, "Duplicate canonical"):
            metadata.portable_names(["a", "a"])

    def test_preserves_original_dictionary_and_corrects_status_units(self):
        frame = pd.DataFrame({"grant__pell_disbursements": [1.25], "grant__pell_disbursements__status": ["observed"],
                              "grant__pell_disbursements__raw_token": [pd.NA]})
        original = dictionary(frame, grant__pell_disbursements={"definition": "Actual dollars.", "units": "USD_nominal", "column_role": "measure", "policy_source_urls_json": '["https://example.test/source"]'})
        original.loc[original.panel_column.ne("grant__pell_disbursements"), "units"] = "USD_nominal"
        built, labels = metadata.build_export_metadata(frame, original)
        indexed = built.set_index("canonical_name")
        self.assertEqual(indexed.loc["grant__pell_disbursements__status", "units"], "category")
        self.assertEqual(indexed.loc["grant__pell_disbursements__raw_token", "units"], "text")
        self.assertEqual(indexed.loc["grant__pell_disbursements__status", "source_dictionary__units"], "USD_nominal")
        inherited = json.loads(indexed.loc["grant__pell_disbursements", "original_dictionary_json"])
        self.assertEqual(inherited["definition"], "Actual dollars.")
        self.assertEqual(indexed.loc["grant__pell_disbursements", "status_variable"], "grant__pell_disbursements__status")
        self.assertEqual(labels.iloc[0]["code"], metadata.STATUS_CODES["observed"])

    def test_stable_status_codes_do_not_depend_on_sample(self):
        col = "grant__pell_recipients__status"
        first = pd.DataFrame({col: ["observed_zero", "source_blank", pd.NA]})
        second = pd.DataFrame({col: ["observed", "source_blank"]})
        _, labels1 = metadata.build_export_metadata(first, dictionary(first))
        _, labels2 = metadata.build_export_metadata(second, dictionary(second))
        code1 = labels1.set_index("value").loc["source_blank", "code"]
        code2 = labels2.set_index("value").loc["source_blank", "code"]
        self.assertEqual(code1, code2)
        self.assertNotEqual(code1, metadata.STATUS_CODES["observed_zero"])

    def test_booleans_have_explicit_false_true_and_missing(self):
        frame = pd.DataFrame({"grant__source_record_present": pd.Series([True, False, None], dtype=object)})
        built, labels = metadata.build_export_metadata(frame, dictionary(frame))
        encoded = metadata.encode_categorical_columns(frame, built, labels)
        self.assertEqual(encoded.iloc[:2, 0].tolist(), [1, 0])
        self.assertTrue(pd.isna(encoded.iloc[2, 0]))
        self.assertEqual(labels.value.tolist(), ["false", "true"])
        self.assertEqual(labels.code.tolist(), [0, 1])

    def test_unknown_status_is_preserved_but_flagged(self):
        frame = pd.DataFrame({"grant__unitid_record_status": ["new_unreviewed_status"]})
        built, labels = metadata.build_export_metadata(frame, dictionary(frame))
        self.assertEqual(built.iloc[0].metadata_quality, "status_definition_requires_review")
        self.assertIn("requires review", labels.iloc[0].description)
        self.assertEqual(labels.iloc[0].value, "new_unreviewed_status")

    def test_empty_text_and_null_are_distinct_and_identifiers_remain_text(self):
        frame = pd.DataFrame({"grant__opeid8": ["00123400", None, ""], "grant__zip_code": ["00123", "", None],
                              "grant__school": ["NA", "", None]})
        built, labels = metadata.build_export_metadata(frame, dictionary(frame))
        encoded = metadata.encode_categorical_columns(frame, built, labels)
        self.assertEqual(encoded.loc[0, "grant__opeid8"], "00123400")
        self.assertEqual(encoded.loc[0, "grant__zip_code"], "00123")
        self.assertEqual(encoded.loc[0, "grant__school"], "NA")
        self.assertEqual(encoded.loc[2, "grant__opeid8"], "")
        self.assertTrue(pd.isna(encoded.loc[1, "grant__opeid8"]))
        self.assertTrue(built.export_storage.eq("string").all())

    def test_time_key_and_scope_definitions_are_explicit(self):
        frame = pd.DataFrame({"unitid": [123456], "award_year": ["2023-2024"], "award_year_start": [2023], "award_year_end": [2024],
                              "included_source_family_count": [2], "blocked_source_family_count": [1]})
        built, _ = metadata.build_export_metadata(frame, dictionary(frame))
        indexed = built.set_index("canonical_name")
        self.assertIn("xtset unitid award_year_start", indexed.loc["award_year_start", "description"])
        self.assertIn("July 1", indexed.loc["award_year", "description"])
        self.assertIn("excludes absent", indexed.loc["blocked_source_family_count", "description"])
        self.assertEqual(indexed.loc["award_year_start", "units"], "calendar_year")

    def test_recipient_bounds_link_to_the_right_derived_status(self):
        base = "loan__plus_recipient_count_sum"
        lower = "loan__plus_unique_recipient_lower_bound"
        upper = "loan__plus_unique_recipient_upper_bound"
        frame = pd.DataFrame({base: [20], base + "__status": ["complete_component_sum"], lower: [12], upper: [20]})
        source = dictionary(frame)
        source.loc[source.panel_column.ne(base + "__status"), "column_role"] = "bound_or_partial_measure"
        built, _ = metadata.build_export_metadata(frame, source)
        indexed = built.set_index("canonical_name")
        self.assertEqual(indexed.loc[lower, "status_variable"], base + "__status")
        self.assertEqual(indexed.loc[base, "lower_bound_variable"], lower)
        self.assertEqual(indexed.loc[base, "upper_bound_variable"], upper)
        self.assertEqual(indexed.loc[base, "units"], "sum_of_component_recipient_counts_not_unique_people")

    def test_does_not_recategorize_school_descriptors_or_dollar_values(self):
        frame = pd.DataFrame({"grant__school_type": ["Public", "Private"], "grant__pell_disbursements": [0.0, 1.0]})
        built, labels = metadata.build_export_metadata(frame, dictionary(frame))
        self.assertEqual(built.category_kind.tolist(), ["", ""])
        self.assertTrue(labels.empty)

    def test_unknown_definition_is_explicitly_unresolved(self):
        frame = pd.DataFrame({"new_source_field": ["x"]})
        built, _ = metadata.build_export_metadata(frame, dictionary(frame))
        self.assertEqual(built.iloc[0].metadata_quality, "definition_requires_review")
        self.assertTrue(built.description.str.len().gt(0).all())

    def test_annual_ipeds_codes_are_not_assigned_timeless_value_labels(self):
        frame = pd.DataFrame({"grant__ipeds_opeflag": ["4", "1"], "grant__prch_f": ["-2", "6"]})
        source = dictionary(frame)
        source["definition"] = "Annual raw IPEDS classification."
        built, labels = metadata.build_export_metadata(frame, source)
        self.assertTrue(labels.empty)
        self.assertTrue(built.description.str.contains("definitions can vary over time", regex=False).all())
        self.assertEqual(built.export_storage.tolist(), ["string", "string"])

    def test_no_input_mutations(self):
        frame = pd.DataFrame({"grant__source_record_present": [True, None]})
        source = dictionary(frame)
        original, original_dictionary = frame.copy(deep=True), source.copy(deep=True)
        built, labels = metadata.build_export_metadata(frame, source)
        metadata.encode_categorical_columns(frame, built, labels)
        pd.testing.assert_frame_equal(frame, original)
        pd.testing.assert_frame_equal(source, original_dictionary)

    def test_rejects_ambiguous_dictionary_and_invalid_flags(self):
        frame = pd.DataFrame({"grant__source_record_present": ["no idea"]})
        with self.assertRaisesRegex(ValueError, "Invalid boolean"):
            metadata.build_export_metadata(frame, dictionary(frame))
        with self.assertRaisesRegex(ValueError, "Duplicate dictionary"):
            metadata.build_export_metadata(frame, pd.concat([dictionary(frame), dictionary(frame)]))

    def test_encoder_rejects_name_and_category_collisions(self):
        frame = pd.DataFrame({"grant__source_record_present": [True], "unitid": [123456]})
        built, labels = metadata.build_export_metadata(frame, dictionary(frame))
        bad = built.copy()
        bad["portable_name"] = "duplicate"
        with self.assertRaisesRegex(ValueError, "duplicate portable"):
            metadata.encode_categorical_columns(frame, bad, labels)
        bad_labels = pd.concat([labels, labels.iloc[:1]])
        with self.assertRaisesRegex(ValueError, "duplicate category"):
            metadata.encode_categorical_columns(frame, built, bad_labels)


if __name__ == "__main__":
    unittest.main()
