# Portable research exports

The portable package makes the validated institution-by-award-year panel usable in Stata, Python, R and Excel while retaining its identifiers, value statuses, provenance and research restrictions. Its source is `Panels/ipeds/unitid_research/fsa_unitid_award_year_panel.parquet` under the selected build root. The package is a derived export: it does not replace or modify that canonical panel, its source workbooks, or the frozen release manifest.

Generate the package with:

```sh
python3 Scripts/15_export_research_formats.py \
  --root ResearchBuild/2026-09-22-v2 \
  --output-dir ResearchBuild/2026-09-22-v2/Exports/unitid
```

The package retains every institution-year and every source column. The completed v2 panel has 141,038 rows and 1,312 columns. Rows containing only blocked source families remain visible; export availability does not make them usable aid observations. Use the per-family inclusion/scope flags and the [research-use guidance](https://github.com/markjayson13/FSAVolumeReports_Panel/blob/main/Documentation/research_use.md) to select an analysis sample.

## Files that travel together

| Artifact | Purpose |
| --- | --- |
| `fsa_unitid_panel.dta` | Stata dataset with valid variable names, dataset/variable labels and value labels for coded fields. |
| `fsa_unitid_panel.csv.gz` | Compressed UTF-8 CSV with portable column names; use its typed import helper and metadata. |
| `fsa_unitid_panel.parquet` | Nullable typed data with embedded dataset and field metadata. |
| `codebook.csv`, `codebook.json` | Every variable's original/export name, readable label, definition, type, units, missingness, derivation and available source/policy metadata. |
| `value_labels.csv` | Explicit code-to-meaning mappings; numeric codes are not measured aid values. |
| `name_map.csv` | Reversible mapping between original column names and Stata's shorter names. |
| `load_stata.do`, `load_csv.py`, `load_csv.R` | Consistent loading, types, labels and panel-key setup without automatic identifier coercion. |
| `excel/fsa_unitid_YYYY_YYYY.xlsx` | One complete award year per workbook, including `Data`, `Codebook`, `ValueLabels` and `README` sheets. |
| `dataset_metadata.json`, `fsa_unitid_panel.csv-metadata.json` | Release scope, keys, units, null rules and a CSVW schema for the CSV columns. |
| `string_nulls.parquet` | UNITID/time keys and one Boolean original-null mask per exported text field; supports exact restoration after Stata/Excel loading. |
| `export_manifest.json` | Exact source/artifact hashes, export settings, dimensions and complete data readback checks. |
| `source_release_manifest.json`, `source_unitid_manifest.json` | Copies of the frozen source build's provenance and conservation evidence. |
| `export_source/` | Frozen copies of the three exporter modules, with hashes recorded in the export manifest. |

All four data formats use the same portable names and the same numeric codes for status and Boolean categories. `name_map.csv` preserves the original canonical names. The full `codebook.json` also preserves the original dictionary metadata. The compact Excel `Codebook` sheet has a definition for every data column; the complete source/policy metadata remain in the package-level codebook.

Distribute the complete directory. A CSV alone cannot carry its column types, labels, null rules or research caveats. Separate column metadata is consistent with the [W3C tabular metadata model](https://www.w3.org/TR/tabular-metadata/); the package's explicit schema and import helpers provide those semantics.

## Identity, time and measured values

`unitid` is an integer IPEDS institution identifier. `award_year_start` is the integer time index; `award_year` is the readable award-year range. An award year is not a calendar-year sum. The panel key is `unitid × award_year_start`, equivalent to `unitid × award_year` for this release. The panel is unbalanced; gaps are preserved.

Every family OPEID remains text, including its leading zeroes and all eight digits. Raw OPEID tokens remain separate. The grant family can have a verified UNITID with a missing FSA OPEID; the export must not invent an OPEID for those records. Parent OPEIDs and source-family OPEIDs are not interchangeable. Keep family OPEIDs, linkage evidence and scope restrictions with analytic extracts.

Amounts remain nominal US dollars in the source reporting period; the export does not adjust prices. Stata, CSV and labeled Parquet retain exact source numeric values. Excel preserves source cents but can remove tiny binary floating-point tails under the explicit rules below. Counts remain counts under the source program's definition. Recipient sums across programs/channels do not count unique people. A label ending in a source count or dollar unit does not override the detailed definition and counting caveat in the codebook.

## Missingness remains information

Numeric missingness remains missing, with zero reserved for an observed zero or an explicitly documented derivation. Read each measure together with its status and any supplied bounds. Source blank, source symbol, suppressed, unavailable schema, absent source record, unavailable report, blocked identity/scope and incomplete derived totals are different states.

Stata supports ordinary numeric missing `.` and extended missings `.a` through `.z`, but only one native string missing, the empty string. Its numeric missings also compare greater than ordinary numbers. The package preserves the existing companion statuses rather than embedding a new interpretation in extended missing codes. In Stata, use `!missing(x)` when an inequality should exclude missing values. See the official [missing-value reference](https://www.stata.com/manuals/dmissingvalues.pdf) and [logical-comparison guidance](https://www.stata.com/support/faqs/data-management/logical-expressions-and-missing-values/).

The canonical panel distinguishes true string nulls from some non-null empty strings, including original blank source tokens and empty exclusion explanations. CSV represents a null with the reserved token `__FSA_NULL__`; a collision with actual source text stops the export. A non-null empty string remains an empty field. The typed import helpers disable default null guesses, so literal text such as `NA` stays text. Labeled Parquet preserves the distinction directly.

Stata uses its native empty-string missing value, and Excel displays missing text as blank. Consequently those formats alone cannot convey whether a blank was originally null or an observed empty string. `string_nulls.parquet` preserves that distinction: its `unitid` and `award_year_start` columns identify rows, and a true value under a text column means the original source value was null. For exact Stata readback into Python, retain the exported order and restore the masks explicitly:

```python
import pandas as pd

data = pd.read_stata("fsa_unitid_panel.dta", convert_categoricals=False)
masks = pd.read_parquet("string_nulls.parquet")
keys = ["unitid", "award_year_start"]
assert data[keys].astype("int64").equals(masks[keys].astype("int64"))
for column in masks.columns.difference(keys):
    data[column] = data[column].astype("string").mask(masks[column])
```

If rows have been filtered or sorted, first align the masks using the two keys with a validated one-to-one join. Do not apply masks by position to a different sample. The mask is for exact provenance restoration; Stata's native `missing()` still correctly identifies unavailable text identifiers during normal research use.

## Format-specific handling

**Stata.** The writer uses `.dta` format 118, readable by Stata 14 and later with Unicode support. Original names that exceed Stata's 32-character limit receive deterministic unique shorter names; consult `name_map.csv` rather than guessing a truncation. Variable and dataset labels are limited to 80 characters; complete definitions and sources remain in the codebook. Labels describe fields, and value labels describe the meaning of category codes. Never sum or average a category merely because Stata stores it numerically. These limits come from [Stata's size specification](https://www.stata.com/products/detailed-size-limits/) and the [pandas Stata writer](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_stata.html).

Run `do load_stata.do` from the package directory. The helper loads the DTA, verifies its unique key, runs `xtset unitid award_year_start`, sets display formats, and attaches original names, units and source families as variable characteristics. It leaves all rows and the research-selection flags available. Underlying numeric values remain double precision; a two-decimal dollar display is formatting, not rounding the stored value.

**CSV.** The file contains one header row and no implicit dataframe index. Text identifiers and nullable types need the schema even when a CSV value appears numeric. In Python, import `load` from the generated `load_csv.py`; `load()` returns portable names and category codes. `load(canonical_names=True, decode_categories=True)` restores canonical names and category meaning tokens. Executing `python load_csv.py` performs the load/key check and prints the shape. In R, run `source("load_csv.R")` from the package directory with `readr` installed; the resulting `panel` retains codes, variable labels and units, with `value_labels` providing code meanings. The R helper does not turn codes into ordered factors.

For pandas Stata readback, `convert_categoricals=False` is necessary when comparing stored numeric codes rather than rendered value labels. See the [pandas CSV reader](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html) and [Stata reader](https://pandas.pydata.org/docs/reference/api/pandas.read_stata.html).

**Excel.** Open the `.xlsx` award-year workbook for a year-specific view. All variables and quality fields remain present. Annual workbooks reduce the size of an individual file and make the award-year scope explicit. Text identifiers are stored as text; a custom numeric display that merely adds zeroes is insufficient. Source text is written as text, including text that starts with `=`. Do not convert those cells into formulas.

An XLSX worksheet permits 1,048,576 rows and 16,384 columns, with at most 32,767 characters per cell and 15 significant digits of numerical precision. The exporter must reject a future source that exceeds the supported cell representation rather than silently truncate it. The current panel fits the worksheet dimensions; Excel's practical memory limits still depend on the computer. These are [Microsoft's documented limits](https://support.microsoft.com/en-us/excel/excel-specifications-and-limits).

The Excel writer uses 15 significant digits and accepts changes only in two narrowly checked cases. For a monetary value that is already cent-valued apart from its binary representation, the stored value must preserve its two-decimal cent amount and differ by no more than four units in the last place (ULPs), evaluated at `max(abs(value), 1)`. For a `name_similarity` score, both values must remain in the interval 0–1 and the change must be at most `5e-15`. Every other numeric value must read back exactly. The export manifest reports the affected-cell counts as `excel_binary_rounding_cells_cents_preserved` and `excel_similarity_rounding_cells` for each workbook. These are documented Excel representation differences; use Stata, CSV or Parquet when exact source floating-point values are required.

Opening a CSV directly in Excel can strip leading zeroes or infer dates/scientific notation. Use the prepared XLSX workbook, or import through **Data → From Text/CSV**, explicitly assigning identifier and raw-token fields to Text. Saving a spreadsheet as CSV removes workbook metadata. See [Microsoft's instructions for preserving leading zeroes](https://support.microsoft.com/en-gb/excel/keeping-leading-zeros-and-large-numbers).

## Verification and interpretation

An export is complete only after `export_manifest.json` reports `all_checks_passed: true`. Verification reads every exported data cell in each requested format, checks the original string-null masks, and verifies the DTA's embedded variable labels and value-label mappings. Names, labels, definitions, unique non-null panel keys, reserved-null collisions and source hashes are checked before publication. Excel verification covers all annual workbooks, each retaining all 1,312 variables, with only the explicitly bounded numerical differences above. Stata, CSV and Parquet numerical comparisons are exact. Artifact hashes bind those results to the exact files distributed.

The exporter copies its three implementation modules into `export_source/` before writing data, then checks their hashes again before certifying the package. A source-panel, dictionary or exporter-code change during the run stops certification. The frozen research panel and its existing release manifest remain unchanged. The package includes the repository's Markdown research documentation for offline review.

These are Python readback checks: pandas reads Stata and CSV, PyArrow/pandas read Parquet, and the Excel data cells are read from their OOXML representation. Tests also exercise an independent openpyxl reader on adversarial Excel examples. The manifest explicitly records whether native Stata or Excel execution occurred; a successful serialization check does not imply those desktop applications were launched. The helper scripts document how to open the prepared files in those applications.

Roundtrip verification certifies serialization, not a new substantive match between FSA and IPEDS. Annual identity eligibility, reporting-unit scope, incomplete policy-era coverage, suppressed observations and family collisions remain the restrictions documented in [IPEDS linkage](ipeds_linkage.md), [policy chronology](https://github.com/markjayson13/FSAVolumeReports_Panel/blob/main/Documentation/policy_and_reporting_changes.md) and [research use](https://github.com/markjayson13/FSAVolumeReports_Panel/blob/main/Documentation/research_use.md). Citation and replication should identify the frozen source release and the export manifest together.
