# FSA–IPEDS institution panel

Primary: `Panels/fsa_ipeds_panel_2004_2023.parquet`. 141,711 rows, 4,046 columns, unique `unitid year`. Every row of the supplied IPEDS PRCH-clean panel is retained.

The main view joins FSA award-year **start** to IPEDS collection-year **start**. IPEDS year 2023 is the 2023–24 collection cycle; FSA is AY2023–24. This is an institutional-context alignment, not a claim that all measures cover that period.

For student-aid period comparisons use `Panels/fsa_ipeds_aid_aligned_2004_2023.parquet`: IPEDS 2023 is matched to FSA AY2022–23. All 20 annual SFA table mappings support this prior-award-year convention; see `Metadata/annual_sfa_timing.csv`. Multi-year/cohort, fiscal-year, survey population and reporting-scope differences still require variable-specific treatment. Timing alignment alone does not make FSA and IPEDS totals equivalent.

`ipeds__*` and `fsa__*` retain all original source fields and Arrow types. No OPEID is used to perform this already-UNITID-linked join. Family-specific OPEIDs remain strings and are not collapsed across programs. IPEDS raw sentinel codes (including OPEID -2) are retained verbatim; they are not valid institution identifiers.

`merge_status` is matched/ipeds_only. `has_fsa_record` means a row in the conservative FSA UNITID research view, not program participation. `has_usable_fsa_record` requires at least one included family; blocked-only rows are retained. Missing FSA records and amounts are never filled with zero. Use family statuses and component scope flags. No aid is copied or allocated from reporting parents to children. The source IPEDS PRCH cleaning is preserved exactly.

`Checks/pell_validation/` compares only all-undergraduate Pell recipient and total-dollar measures across plausible time alignments; it excludes FTFT-only measures and distinguishes reporting-period evidence from population/reporting-scope differences. These checks flag discrepancies without changing identity links.

`Checks/annual_merge_summary.csv` and both diagnostic folders retain unmatched IDs, out-of-range FSA keys, blocked-only cases and exact-OPEID reporting evidence for unmatched IPEDS rows. Diagnostic OPEID matches do not assign data and do not prove scope equivalence.

This build uses the verified IPEDS source metadata correction `2023-sfa-v1`. Original metadata and correction evidence are frozen under `Inputs/ipeds/source_metadata_repair/`. The annual Pell audit recomputes verification from the corrected dictionary and physical lineage. Item-level imputation flags remain unavailable in the 2023 Access source; metadata verification does not mean a value was reported rather than imputed. Institution links and parent/child scope are unchanged by a table-reference correction.

Metadata: `Metadata/codebook.csv`, `name_map.csv`, `value_labels.csv`, and `ipeds_metadata.json`. IPEDS year-specific descriptions, units and code definitions remain in the latter; conflicting definitions are not silently collapsed to one value label. Parquet fields embed compact labels and metadata references. Canonical Parquet retains original status strings and booleans; statistical/spreadsheet exports use reversible status/boolean codes described by value_labels.csv.

Annual exports under `Exports/` retain all columns. `load_stata.do` appends the annual Stata files and sets `xtset unitid year` (Stata SE/MP; this exceeds the smaller edition's variable limit). `load_csv.py` loads all years or a chosen set, preserves leading zeros, and recognizes only the reserved missing token `__FSA_NULL__`. Use the XLSX files for spreadsheet opening rather than letting a spreadsheet infer types from CSV. `Exports/string_nulls.parquet` distinguishes original null strings from observed empty strings in Stata/Excel. Excel uses 15 significant digits; only the existing documented cent-preserving FSA money binary tails and tiny name-similarity rounding are permitted and counted in validation. Numeric values beyond Excel's exact 15-significant-digit capacity are stored as exact numeric text and identified in `Checks/excel_precision/`; Excel formulas may ignore these text cells until converted, which can round them. Load the ledger's exact_numeric_text column as text. Parquet, CSV and Stata keep these values numeric and unchanged. No native Stata or Excel desktop execution was performed.

Source text containing characters unsupported by Excel is displayed with visible JSON escapes, with exact original text and a checksum in the same `Checks/excel_precision/` ledger. Oversized text, if present, uses an explicit ledger reference instead of truncation. The `exception_type` column separates these text exceptions from numeric precision cases. `restore_excel_text_cells` in `Scripts/fsa_ipeds_combined_excel.py` verifies the ledger and restores original strings after reading the spreadsheet. Canonical Parquet, CSV and Stata preserve the original text directly.

Reproduce from this self-contained bundle with Bash and curl on macOS/Linux/Windows WSL; the script installs isolated uv, Python 3.13.0 and the pinned environment listed in `environment-requirements.txt` without changing shell profiles:

```sh
bash reproduce.sh /path/to/new-empty-output
```

The isolated environment is stored in `.reproduction-runtime/` beside this bundle (set `FSA_RUNTIME` to choose another location). Internet is needed for initial environment setup, then the data build uses only frozen local inputs.

Use `--formats parquet` for only the two canonical panels and their complete metadata/diagnostics. The copied source inputs, definitions, code and output artifacts are hashed in `build_manifest.json`; original input files were not modified. Replay verifies all frozen Inputs/Metadata/Scripts and reference Panels and compares every cell/type in the newly written canonical panels with this reference bundle. Source paths in the manifest are historical provenance, not runtime requirements.

## September 29 metadata refresh verification

This bundle incorporates the IPEDS `2023-sfa-v1` correction. All 343 corrected definitions and their original metadata are preserved. The broader metadata audit has zero dictionary/lineage table mismatches and zero missing exact source dictionaries. All 31 audited annual Pell source identities verify, including the two 2023 measures. Item imputation flags remain unavailable in the 2023 Access release; institution reporting scope limitations remain.

Both combined panels have exactly the same observations, values, types, nulls, identity links and scope flags as the previous bundle. The primary Pell comparison sample remains 71,710 institution-years per measure. Metadata-screened eligibility is recomputed from corrected source evidence. Detailed checks and changes are in `Checks/metadata_refresh/`.

All 60 annual CSV/Stata/Excel exports passed readback verification. Thirty focused downstream regression tests passed. A fresh isolated Python 3.13.0 environment rebuilt both Parquet views byte-for-byte identically using the one-command bootstrap with `--formats parquet`; the fresh replay did not regenerate the 60 annual exports. Native Stata/Excel desktop execution and other platforms were not tested for this combined refresh.

The previous combined bundle was replaced after validation, and temporary full dataset copies were removed. The correction evidence, tests and source-code patch remain here. The original source workspace became unavailable during this refresh; the updated build code is preserved under `Scripts/`, with a portable repository patch under `Checks/metadata_refresh/downstream_changes.patch`.


## Canonical IPEDS Final handoff

The canonical source is now `/Volumes/CIRAGO/IPEDSDB_PANEL/Final`, pointing to `Releases/full-panel-labels-v1`. Every value, missing value, row, original variable name and storage type in its full Parquet was compared with the frozen `Inputs/ipeds_panel.parquet`; they are identical. The labeled Stata file was also checked against the full panel after reversing its documented storage conversions. The data snapshot used by this bundle therefore remains unchanged. The exact canonical hashes, resolved paths and equivalence receipt are in `Inputs/ipeds/final_labeled_panel/binding.json` and `Checks/canonical_ipeds_handoff/`.

The combined `Analysis` datasets now inherit the canonical release's improved variable descriptions and explicitly year-scoped numeric value labels. State abbreviations, OPEIDs, CIP tokens and other preserved string categories keep their existing analysis representation. The upstream Stata numeric encoding of STABBR is neither a FIPS code nor the original abbreviation; use the frozen source-code mapping when reading that upstream DTA. All original upstream metadata bytes are retained as deterministic gzip under `Inputs/ipeds/final_labeled_panel/`, with readable mapping and remaining-issue companions. Source metadata gaps remain explicit. Full master panels and historical annual exports retain their original data and labels; the latest compact labeled research views are under `Analysis`.

Unmatched diagnostics now distinguish valid NCES alphanumeric reporting branches, source -2 sentinels, missing/malformed identifiers, ambiguous identity resolutions, and missing annual identity matches. This classification change assigns no new links and changes no aid amounts or institution counts. The original-grain conservation and exclusion ledgers are now frozen under `Inputs/fsa/source_conservation/`; they cover included source records and identify exclusions separately.
