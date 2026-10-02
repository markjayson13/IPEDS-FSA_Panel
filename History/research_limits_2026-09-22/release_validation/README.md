# Validated release evidence snapshot

This directory publishes the compact validation evidence from the completed `ResearchBuild/2026-09-22-v2/build` package. Start with the [validation report](VALIDATION_REPORT.md) and [verified statistics](verified_release_statistics.json). This snapshot records the September 22, 2026 local build; it does not claim a new upstream-data refresh or a validation run on GitHub.

The five files below are byte-for-byte copies of the original build artifacts. Their absolute local paths are historical provenance, not portable installation requirements. The manifest pins source workbooks, IPEDS inputs, code/metadata, output artifacts and the original environment. These hashes identify the validated bytes even when local paths differ. The [recorded dependency versions](environment-requirements.txt) supplement the repository's unpinned installation requirements.

| Exact copied artifact | SHA-256 |
| --- | --- |
| [research_release_manifest.json](research_release_manifest.json) | `547d26b6729897d0a4d67b330c1b20a2e4f45df3b14d358d3a74014d69602345` |
| [verified_release_statistics.json](verified_release_statistics.json) | `6d7d83d25534c89c143057d428f5cb0067c61b866790247e1873bdcc1022ac06` |
| [environment-requirements.txt](environment-requirements.txt) | `115cd54f9bb7700fecbd1377359cbebd8992623374efb378e907e4e80a88638d` |
| [test_results.txt](test_results.txt) | `de955a591a012ec8f49680127b0c65a02b6d8c24fef62d54b91c6a8960177397` |
| [build_log.txt](build_log.txt) | `2c780c81048c96fff663285a46b07cd11aabd1692eb113693b9814e9601bd5c8` |

`VALIDATION_REPORT.md` is a publication rendering: only its publication notice, file links and local-artifact labels have been adapted; reported quantities and output fingerprints are unchanged. Its original local report SHA-256 is `8f5ee1e888e8ec71ee03b9f71136c3d1a890b8d56a66f12bc2046823a6230c1a`. Documentation links point to the repository documentation reviewed for this release; the manifest's code/metadata and local `build/source_snapshot/` preserve the original validated implementation.

## What is published

This directory includes the release manifest, verified summary statistics, pinned environment, test transcript, pipeline transcript and readable validation report. The surrounding [independent analysis evidence](../README.md) includes the institution-count reconciliation and source-family conservation findings.

## What remains local

Existing `.gitignore` rules exclude `ResearchBuild/`. The raw FSA/NCES inputs, executable build snapshot, canonical Parquet panels, full build dictionaries and generated QA ledgers remain local; this evidence snapshot does not distribute those datasets. A Git clone alone therefore cannot perform an exact frozen rebuild without the separately retained input bytes and selected source inventory. Official sources, selected hashes, acquisition scripts, and the [reproduction instructions](../../../Documentation/research_use.md) describe the required inputs and workflow. Downloading current upstream files can yield different vintages and requires a new validated build.

The logs and manifest retain local paths for auditability. They contain institutional public-source data and build metadata, not personal student records. Treat the original build logs as recorded evidence; rerunning tests does not reproduce or certify the complete data release by itself.
