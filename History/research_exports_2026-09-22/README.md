# Portable research export validation

The exporter creates a labeled distribution of the frozen `ResearchBuild/2026-09-22-v2` UNITID panel without modifying its canonical data or original release manifest. See [export instructions](../../Documentation/portable_exports.md).

The distribution retains all 141,038 rows and all 1,312 variables. Its codebook preserves full canonical names, definitions, units, source mappings, reporting-policy metadata, annual dictionary references, and missing-value/status interpretation. Portable names are unique and Stata-compatible. The master research limits and program-family exclusions still apply.

[test_results.txt](test_results.txt) records the local regression suite. A successful data export additionally requires its own `export_manifest.json`: that manifest contains source and artifact SHA-256 hashes and complete data readback results for Parquet, CSV, Stata, and each annual Excel workbook. Native Stata and Excel desktop execution are not implied by those checks.

The actual datasets remain excluded from Git under `ResearchBuild/`. The tracked summary and codebook are evidence and metadata; they do not substitute for the data files.

## Completed verification

- All 167 regression tests passed, including 33 export/metadata tests.
- All 1,312 variables have nonempty labels and definitions; 1,100 value-label entries describe status and boolean codes.
- Parquet, CSV, Stata, and all 26 annual Excel workbooks passed readback: 740,167,424 data-cell comparisons across the four formats.
- Stata's embedded variable labels and value labels match the codebook. An independent OpenPyXL check also confirmed the latest annual workbook's sheets and text-typed leading-zero OPEID.
- Excel normalized 1,029 preexisting currency binary rounding tails while preserving cents, and 73,966 name-similarity values within the declared precision tolerance. The other three formats preserve numeric values exactly.
- The canonical source panel still has SHA-256 `93d675fde748d6c480c7eb9050dccec49a88892154a60ed21e016539ba9983ba`.

The copied [export manifest](export_manifest.json) records paths relative to the local export package, not this audit directory. [export_codebook.csv](export_codebook.csv), [name_map.csv](name_map.csv), and [value_labels.csv](value_labels.csv) provide the compact metadata for review. The package's larger `codebook.json`/`codebook.csv` additionally retain every original dictionary field and full source/policy mappings. [export_build_log.txt](export_build_log.txt) records the successful run. Native Stata and Excel desktop execution was not performed.
