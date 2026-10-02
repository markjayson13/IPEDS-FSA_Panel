# Repository ownership and migration

This split assigns ownership by operation. A citation to another data source does not transfer ownership of the citing operation.

## Upstream FSA

FSA acquisition, annual/Q4 selection, spreadsheet parsing, eight-character OPEID normalization, verified source-OPEID recovery, loan harmonization, policy registries, and OPEID-grain QA/reproduction stay in FSAVolumeReports_Panel. Some approved source-ID repairs cite contemporaneous IPEDS evidence; those audited repairs and their evidence ledger remain intact upstream.

The active FSA orchestrator no longer invokes IPEDS linkage, packages stale `Panels/ipeds` artifacts, or requires an IPEDS directory to certify FSA transformations. Historical frozen mixed releases keep their original bytes and old tagged snapshots. They are not newly labeled as FSA-only releases.

## Downstream integration

This project owns annual directory/crosswalk matching rules, candidate evidence, conservative UNITID-year-family selection, reporting-scope flags, the UNITID research view, combined joins, period alignment, Pell validation, metadata adapters, reviewed consolidation recipes, and labeled UNITID/combined exports. `fsa_export_metadata.py` and `fsa_portable_exports.py` moved here because their release API exports the linked UNITID panel.

Original FSA parsing/loan semantics are imported through the declared source dependency for linkage rebuilds. They are not maintained as a second panelizer here. Source releases, quarantine records, schema definitions and identity evidence are read-only. Linkage and exports belong under a separate downstream output root.

IPEDS acquisition, database extraction, original dictionary corrections, and parent/child cleaning remain in IPEDSDB_Panel. The downstream correction consumer verifies supplied evidence; it does not implement the original repair.

## Code and published artifacts

Current combined/analysis code came from `/Volumes/CIRAGO/FSA-IPEDS_DS`, which contains fixes newer than the old FSA folder. Annual-linkage code and its tests moved from the FSA project. Source provenance is in `Audit/migration_sources.json`.

Large panels, original upstream metadata, raw inputs, generated cell ledgers and frozen historical scripts stay in the external release. `Data` is an ignored local link to that release. A Git clone can run fixture tests without CIRAGO; data-dependent checks explicitly require the bundle.

Historical mixed-project investigations and export/reproduction receipts are under `History`. Their original paths and claims are historical evidence. Do not run old ad hoc scripts there or infer that their temporary paths remain available.

## Reproduction boundary

Default reproduction consumes frozen masters and creates labeled analysis views. `--mode masters` replays the combined join from frozen inputs. `--mode all` runs both. Neither downloads or executes either upstream panelizer. Initial environment installation needs internet; data inputs are local and checksum-verified.

A Git clone alone does not include all data. The external bundle is a prerequisite until a separately documented data release/download is published. Existing public FSA mixed-release assets are not silently redirected or overwritten during this split.
