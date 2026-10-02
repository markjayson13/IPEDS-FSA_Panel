# FSA–IPEDS delivery verification, September 24, 2026

Delivered to `/Volumes/CIRAGO/FSA-IPEDS_DS`. Both views contain 141,711 rows, 4,046 columns and 10,421 institutions, with unique `unitid year` keys spanning IPEDS collection years 2004–2023. The collection-anchor view has 108,841 FSA matches; the prior-award-year view has 108,513.

- `delivery_verification.json`: exact source/metadata checks, all 60 annual export files, Stata labels and a real-data test of the supplied CSV loader.
- `reproduction_verification.json` and `reproduction_log.txt`: a fresh isolated Python 3.13.0 environment rebuilt both canonical Parquet files byte-for-byte identically. This replay used `--formats parquet`; the annual exports were separately read-back verified in the source build.
- `final_test_results.txt`: 239 passing tests.
- `independent_merge_review.json`: independent pandas one-to-one join verification of every row for 40 selected columns, including all derived flags, Pell, OPEIDs and scope fields.
- `export_recovery_checkpoints.json` and `publication_verification.json`: previously verified exports were hash-bound and retained during recovery; all packaged artifacts were checked before final publication. Finder metadata was preserved.
- `excel_source_text_scan.json`: one unsupported source string, handled reversibly in Excel. There are also 22 exact numeric-text exceptions; original Parquet/CSV/Stata values are unchanged.

The bundle contains its own codebooks, annual definitions, lineage, PRCH evidence, frozen input panels, build code, hash inventory, diagnostic ledgers and reproduction command. Pell comparisons preserve the 2023 dictionary/lineage table conflict. Their primary common sample is 71,710 institution-years; the subset with verified metadata and scope contains 67,003.

Failure logs describe interrupted attempts, not the final dataset. The delivered `build_manifest.json` is complete and includes the final publication and reproduction evidence. Neither original input panel was modified. Native Stata/Excel desktop execution and non-macOS end-to-end reproduction were not tested.
