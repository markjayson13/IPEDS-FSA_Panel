> Repository separation: this page describes the published bundle and its historical verification. For current source-checkout commands and external-bundle path semantics, use the [analysis recipe README](../Analysis/README.md). Relative `../Inputs`, `../Panels`, and `../Checks` references below describe the original published bundle layout.

# FSA and FSA–IPEDS analysis datasets

These are smaller analysis views of the verified research masters: 278 columns for FSA only and 2,888 for each combined view. They preserve every institution-year and every distinct aid outcome. They remove contact fields and repeated audit detail, consolidate only reviewed source columns, and convert explicitly documented IPEDS missing codes into nulls. They do not define a universal estimation sample or certify common parent/child reporting scope.

## Which file to use

- `fsa_analysis.dta` / `.parquet` / `.csv.gz`: FSA only, 141,038 institution–award-year rows, 1999–2000 through 2024–2025. Keys: `unitid award_year_start`.
- `fsa_ipeds_analysis.dta` / `.parquet` / `.csv.gz`: all substantive IPEDS measures plus FSA aid, 141,711 rows, IPEDS collection years 2004–2023. FSA award-year start equals IPEDS collection-year start. Keys: `unitid year`.
- `fsa_ipeds_aid_analysis.dta` / `.parquet` / `.csv.gz`: the same IPEDS rows, matched to the preceding FSA award year. Use for comparisons to the primary annual IPEDS student-aid reference period; individual historical/cohort/fiscal measures still have their own periods.

The two original files under `../Panels/` are unchanged, full research masters. Full raw tokens, suppression bounds, partial sums, source coordinates, and candidate links remain there and in `../Inputs/fsa_panel.parquet`. Analysis files contain no allocation of parent aid to campuses, no zero filling, and no row filtering.

## Open in Stata

From this directory:

```stata
use "fsa_analysis.dta", clear
xtset unitid award_year_start
```

For the combined aid view:

```stata
use "fsa_ipeds_aid_analysis.dta", clear
xtset unitid year
```

Files include variable and value labels. The combined files require Stata SE/MP to load all 2,888 variables at once. Your installed Stata BE can use the supplied topic loaders, which select a subset from the same file without creating duplicate datasets. For example:

```stata
do "load_fsa_ipeds_aid_analysis_student_aid.do"
```

There are loaders for institution characteristics, student aid, finance, enrollment, outcomes, admissions and staff. Each includes every FSA analysis field and common institution/scope fields, retains every row, and stays within 2,048 variables. Together they cover every variable in the combined dataset. Exact memberships are in `Metadata/stata_modules.json`. CSV cannot store labels; use the accompanying codebook and value-label CSVs. The supplied `load_csv.py` reads any of the three datasets with correct identifier types and missing values; in Python use `from load_csv import load; df = load("fsa_analysis")` from this directory. All formats use the same readable, unique names of at most 32 characters. A `load_*.do` helper is provided for each dataset.

## What was consolidated

All 143 IPEDS split-name groups (310 columns) were reviewed against 2,520 annual source dictionary records and 191 pair comparisons. Ninety-four groups of equivalent source columns were approved, removing 106 redundant columns. Matching full definitions at adjacent source-year boundaries, the same NCES variable number, compatible codes and types, and no conflicting observed values are required. All annual definitions and notices remain authoritative. Joining source columns is not a claim that measurement is constant across time.

FSA has 106 distinct primary measures after removing 28 redundant measure columns: ten exact FFEL duplicates, eight documented Direct Loan additive series with broader coverage, and ten Parent PLUS header changes. Direct Loan native recipient counts remain separate from harmonized recipient sums. Undergraduate/graduate breakdowns and Parent/Graduate PLUS remain separate. Generic PLUS through 2005 supplies Parent PLUS, not total PLUS; the source and status change together from 2006 onward. Shared scope fields and parent references are consolidated only after reference-year/anchor agreement and exact overlap checks. Family-specific OPEIDs and institution descriptors remain separate when they can differ.

Salary series with changed formulas/populations, grant series with changed aid populations, athletic aid versus association membership, incompatible old system-type codes, and unproven source equivalences remain separate. Administrative contact/address/URL fields and duplicate panel keys are excluded; geographic variables, substantive characteristics and survey/scope flags are retained. Every source column has a disposition in `Metadata/*_column_crosswalk.csv`.

## Missingness, categories and identifiers

- FSA outcome statuses remain alongside their measures. Missing/suppressed/blocked amounts are not zero. Loans originated, loan disbursements, recipient counts, recipient sums, federal awards and disbursement amounts are different constructs. Recipient-count sums are not unique borrowers; dollars are nominal.
- IPEDS missing recodes use explicit annual dictionary/code evidence matched by year, source family, physical table, variable name and variable number. The original value and reason for each changed cell are in `Metadata/*_missing_code_ledger.parquet`. No global negative-to-missing rule is used. Legitimate negative finances/net prices/coordinates remain.
- IPEDS numeric categories previously stored as text become nullable integers only where every annual definition and observed token supports a reversible conversion. Codes retain their nominal meanings. Universal value labels are applied only when meanings are stable; otherwise consult annual metadata. Retained negative status codes, including PRCH=-2, are valid categories; Stata factor notation requires an explicit study-specific positive recode if those codes are used as factor levels.
- FSA OPEIDs remain eight-character strings, including leading zeros. `ipeds_opeid` preserves the raw IPEDS identifier, including the source sentinel -2; it is not the analysis key. `ipeds_opeid8` exposes only actual eight-numeric-digit IDs; this is format compatibility, not verified FSA linkage. `ipeds_opeid_kind` distinguishes numeric IDs, legitimate NCES alphanumeric reporting branches, source not-applicable and source missing. No alpha suffix is replaced with an invented parent 00.
- Native Stata strings do not distinguish null from observed empty strings. `Metadata/*_string_nulls.parquet` preserves that distinction. CSV uses only `__FSA_NULL__` as its missing token; preserve identifier columns as strings and do not let spreadsheet software infer their type.

Some unlabeled source negatives and anomalous domain values cannot be safely recoded from the available documentation. They remain unchanged and are listed in `Decisions/ipeds_nonfinance_negatives_audit.json`. Do not assume all remaining negative codes are real measurements. Variable-specific annual definitions and that review list are part of the analysis contract.

## Metadata and remaining research limits

`Metadata/*_codebook.csv` contains labels, units, definitions, source mappings, statuses, formulas and policy references. `analysis_companions_json` identifies every original bound/raw-token/partial-sum field; `analysis_year_rules_json` specifies the same year selection used for renamed Parent PLUS measures. Use these mappings to retrieve detailed bounds or original source statuses from the full masters. The compact codebook does not silently substitute one segment's bound for another segment.

`Decisions/` contains the reviewed consolidation plans, reasons for keeping variables separate, annual definition evidence, the independent safety review, the missing-code and storage rules, and identifier audits. The complete annual IPEDS dictionary, coding and physical lineage remain at `../Metadata/ipeds_metadata.json` in the source bundle. They must be consulted for varying annual code meanings, population/formula changes, privacy perturbation, historical/cohort periods and parent/child scope.

Institution identity does not certify exclusive campus-level aid measurement. Keep family record/identity flags and shared component scope/parent-reference fields when defining a sample. The 2023 upstream metadata repair is inherited unchanged; absent item-level imputation flags are not recreated or inferred.

## Verification and reproduction

`Checks/` records key/row preservation, zero-conflict consolidation checks, missing-code counts, and complete Parquet/CSV/Stata readbacks including Stata variable/value labels. Source hashes bind the decisions to the reviewed masters and metadata. `manifest.json` hashes all deliverables. Native StataNow/BE 19.5 also verified the complete FSA file and selected finance and aid modules from the combined files; the exact do-files and logs are retained. Full combined-file loading in SE/MP and other platforms was not exercised.

From this bundle, with Bash and curl:

```sh
bash reproduce.sh /path/to/new-analysis-output
```

The source master bundle must remain available beside this Analysis directory, or set `FSA_MASTER_ROOT` to its location. The command installs a pinned isolated Python environment on first use and derives the analysis views from the verified local masters. It does not download or rebuild raw FSA/IPEDS sources. The original bundle's `../reproduce.sh` remains the command for rebuilding the full combined masters. Files under `Analysis` alone are not a replacement for the full replication bundle.


## Canonical IPEDS Final labels

The combined analysis files incorporate `full-panel-labels-v1` from the canonical `IPEDSDB_PANEL/Final` release. All 141,711 x 2,721 IPEDS input cells were checked against the previous frozen source, including nulls and original storage; only export metadata differs. The upstream Stata export was checked after reversing its documented encodings. This refresh changes descriptions and labels, with no changes to analysis values, variable names/storage, institution-year rows, consolidation/missing-code decisions, linkage or year alignment. The standalone FSA analysis files and combined CSV data bytes are unchanged.

Codebook fields beginning `upstream_` retain full source labels, completeness/comparability status, explicit remaining issues and source references. Numeric value labels state the reporting years for which each meaning is documented. Where a complete value-label set exceeds Stata's size limit, the value-label CSV preserves the full definition alongside the compact native display label. String identifiers/categories retain source tokens; numeric codes used by the upstream Stata exporter are not imposed on these analysis files. In particular, `ipeds_stabbr` remains the original state abbreviation string.

The canonical release still has definition gaps or conflicts for F1C196, LINE_55, PCF_F_RV, REV_IC and SFTETOTL, and unresolved observed year/code meanings for ADMCON9, CUFASB, CUGASB, DEATHYR and STAT_AL. Those meanings are not guessed. Exact upstream metadata, source-code maps and issues are frozen under `../Inputs/ipeds/final_labeled_panel/`; see its `binding.json` and `../Checks/canonical_ipeds_handoff/` for hashes and verification. The analysis reproducer applies the same metadata adapter to the same frozen inputs and requires their recorded hashes.

Paths stored in the analysis codebooks are relative to the Analysis directory; paths in the canonical binding are relative to the FSA-IPEDS_DS bundle root.
