import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
from fsa_ipeds_combined_metadata import build_combined_metadata, portable_names, DERIVED
from fsa_portable_exports import prepare_export_frame


class CombinedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ipeds_root = self.root / "ipeds"
        self.fsa_root = self.root / "fsa"
        (self.ipeds_root / "Dictionary/v2").mkdir(parents=True)
        (self.ipeds_root / "Checks/v2/wide_qc").mkdir(parents=True)
        self.fsa_root.mkdir()
        self.lineage = []
        dictionary = []
        codes = []
        for year in [2022, 2023]:
            for column, source, identifier, title in [
                ("AID_A", "SFA_P", "nces:SFA_P:1:AID", "Aid amount"),
                ("AID_B", "OTHER", "nces:OTHER:1:AID", "Unrelated amount"),
                ("SECTOR", "HD", "nces:HD:2:SECTOR", "Institution category"),
            ]:
                table = f"SFA{year-1-2000:02d}{year-2000:02d}_P1" if source == "SFA_P" else f"{source}{year}"
                name = "SECTOR" if column == "SECTOR" else "AID"
                scope = {"year": year, "source_file": source, "access_table_name": table, "varname": name}
                self.lineage.append({**scope, "analysis_column": column, "variable_id": identifier,
                                     "source_varnumber": "2" if name == "SECTOR" else "1",
                                     "transformation_id": "identity", "lineage_role": "direct"})
                dictionary.append({**scope, "variable_id": identifier, "varnumber": "2" if name == "SECTOR" else "1",
                                   "varTitle": title, "longDescription": f"{title} for collection {year}",
                                   "DataType": "Disc" if name == "SECTOR" else "Cont"})
                if name == "SECTOR":
                    codes.append({**scope, "codevalue": "1", "valuelabel": "Old definition" if year == 2022 else "Changed definition"})
        self.dictionary = dictionary
        self.codes = codes
        self._write_sources()
        self.ipeds_schema = pa.schema([("UNITID", pa.int64()), ("year", pa.int32()), ("AID_A", pa.float64()), ("AID_B", pa.float64()), ("SECTOR", pa.string())])
        self.fsa_schema = pa.schema([("unitid", pa.int64()), ("amount", pa.float64()), ("amount__status", pa.string()), ("usable", pa.bool_())])
        rows = []
        for name, dtype, kind, storage in [("unitid", "Int64", "", "numeric"), ("amount", "float64", "", "numeric"),
                                           ("amount__status", "object", "status", "coded_category"), ("usable", "bool", "boolean", "coded_category")]:
            rows.append({"canonical_name": name, "portable_name": name, "variable_label": name,
                         "description": f"Definition of {name}", "units": "", "original_dtype": dtype,
                         "export_storage": storage, "category_kind": kind,
                         "status_variable": "amount__status" if name == "amount" else ""})
        pd.DataFrame(rows).to_csv(self.fsa_root / "codebook.csv", index=False)
        pd.DataFrame([
            {"canonical_name": "amount__status", "portable_name": "amount__status", "code": 1, "value": "observed", "label": "Observed", "description": "Observed value"},
            {"canonical_name": "usable", "portable_name": "usable", "code": 0, "value": "false", "label": "False", "description": "False"},
            {"canonical_name": "usable", "portable_name": "usable", "code": 1, "value": "true", "label": "True", "description": "True"},
        ]).to_csv(self.fsa_root / "value_labels.csv", index=False)

    def _write_sources(self):
        pq.write_table(pa.Table.from_pylist(self.dictionary), self.ipeds_root / "Dictionary/v2/dictionary_lake.parquet")
        pq.write_table(pa.Table.from_pylist(self.codes), self.ipeds_root / "Dictionary/v2/dictionary_codes.parquet")
        pq.write_table(pa.Table.from_pylist(self.lineage), self.ipeds_root / "Checks/v2/wide_qc/qc_value_lineage.parquet")

    def build(self, **kwargs):
        return build_combined_metadata(self.ipeds_schema, self.fsa_schema, self.ipeds_root, self.fsa_root, [2022, 2023], **kwargs)

    def test_scoped_lineage_conflicting_definitions_and_codes_are_preserved(self):
        book, values, metadata = self.build()
        variable = {r["name"]: r for r in metadata["variables"]}
        self.assertEqual({r["source_file"] for r in variable["AID_A"]["source_metadata"]}, {"SFA_P"})
        self.assertEqual({r["source_file"] for r in variable["AID_B"]["source_metadata"]}, {"OTHER"})
        self.assertEqual(len(variable["AID_A"]["source_metadata"]), 2)
        self.assertIn("No universal definition", variable["AID_A"]["description"])
        self.assertEqual(variable["SECTOR"]["stable_value_labels"], [])
        self.assertEqual(len(variable["SECTOR"]["value_label_records"]), 2)
        self.assertFalse(values.canonical_name.str.startswith("ipeds__").any())
        self.assertEqual(book.set_index("canonical_name").loc["ipeds__SECTOR", "export_storage"], "string")
        self.assertEqual(len(metadata["annual_sfa_timing"]), 2)
        self.assertEqual({r["primary_sfa_reference_year_start"] for r in metadata["annual_sfa_timing"]}, {2021, 2022})
        self.assertEqual(len(metadata["column_lineage"]), 6)
        json.dumps(metadata, allow_nan=False)

    def test_codebook_full_coverage_companions_and_existing_export_encoder(self):
        book, values, _ = self.build(lineage_records=self.lineage)
        expected = [r[0] for r in DERIVED] + ["ipeds__" + name for name in self.ipeds_schema.names] + ["fsa__" + name for name in self.fsa_schema.names]
        self.assertEqual(book.canonical_name.tolist(), expected)
        self.assertFalse(book.description.eq("").any())
        self.assertTrue(book.variable_label.str.len().between(1, 80).all())
        self.assertTrue(book.portable_name.str.len().le(32).all())
        self.assertEqual(book.portable_name.nunique(), len(book))
        lookup = book.set_index("canonical_name")
        self.assertEqual(lookup.loc["fsa__amount", "status_variable"], lookup.loc["fsa__amount__status", "portable_name"])
        data = {}
        for row in book.itertuples(index=False):
            data[row.canonical_name] = [True, None] if row.category_kind == "boolean" else ["observed", None] if row.category_kind else ["00123400", None] if row.export_storage == "string" else [1, None]
        frame = prepare_export_frame(pd.DataFrame(data), book, values)
        self.assertEqual(frame[lookup.loc["ipeds__SECTOR", "portable_name"]].iloc[0], "00123400")
        self.assertEqual(frame[lookup.loc["fsa__usable", "portable_name"]].iloc[0], 1)
        self.assertTrue(pd.isna(frame[lookup.loc["has_usable_fsa_record", "portable_name"]].iloc[1]))

    def test_inconsistent_sfa_timing_fails(self):
        self.dictionary[0]["access_table_name"] = "SFA2223_P1"
        self._write_sources()
        with self.assertRaisesRegex(ValueError, "Unverified IPEDS SFA year alignment"):
            self.build(lineage_records=self.lineage)

    def test_table_scope_disagreement_retains_candidate_without_asserting_match(self):
        for row in self.dictionary:
            if row["source_file"] == "SFA_P":
                row["access_table_name"] = row["access_table_name"].replace("_P1", "_P2")
                row["optional_numeric_metadata"] = float("nan")
        self._write_sources()
        _, _, metadata = self.build(lineage_records=self.lineage)
        variable = next(row for row in metadata["variables"] if row["name"] == "AID_A")
        self.assertEqual(variable["source_metadata"], [])
        self.assertEqual(len(variable["candidate_source_metadata"]), 2)
        self.assertEqual(variable["metadata_status"], "requires_metadata_review")
        self.assertIn("dictionary_lineage_access_table_mismatch", variable["metadata_issues"])
        self.assertEqual(variable["stable_value_labels"], [])
        json.dumps(metadata, allow_nan=False)

    def test_names_are_stable_unique_and_do_not_collapse_case_or_long_aliases(self):
        names = ["ipeds__A", "ipeds__a", "fsa__" + "a" * 50, "fsa__" + "a" * 49 + "b", "year"]
        mapping = portable_names(names)
        self.assertEqual(mapping, portable_names(list(reversed(names))))
        self.assertEqual(len(mapping), len(set(mapping.values())))
        self.assertTrue(all(len(name) <= 32 for name in mapping.values()))
        self.assertEqual(mapping["year"], "year")


if __name__ == "__main__":
    unittest.main()
