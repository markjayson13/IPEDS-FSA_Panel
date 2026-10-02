# Read-only FSA inputs and downstream institution linkage

`FSAVolumeReports_Panel` owns the original FSA workbook ingestion, OPEID
normalization and repairs, program panelization, source-cell status handling,
and program harmonization. `IPEDS-FSA_Panel` owns the annual OPEID-to-UNITID
linkage, the conservative UNITID research view, its exports, and the combined
IPEDS data products. No IPEDS database-panelization code is required here.

The source FSA checkout is a dependency, not copied source code. Set
`FSA_SOURCE_REPO` or pass `--fsa-source-repo`. Without either, only the sibling
directory named `FSAVolumeReports_Panel-main` is considered. The resolver does
not search ancestors, the working directory, or `PYTHONPATH` for FSA modules.
An invalid explicit setting fails rather than falling back to another checkout.

The three helpers recorded in `Metadata/fsa_dependency_lock.json` are loaded
directly from verified file bytes into private module namespaces:

| Upstream module | Shared behavior used by linkage |
| --- | --- |
| `fsa_observations.py` | Source-token parsing, full OPEID validation, count semantics |
| `fsa_loan_harmonization.py` | Reviewed loan harmonization and measure definitions |
| `fsa_build_utils.py` | Institution-name comparison and ZIP formatting |

The source SHA-256 checks reject unreviewed helper changes. The loader does not
extend `sys.path`, retain public aliases, or write Python bytecode in the FSA
checkout. A future upstream revision requires content review and a deliberate
dependency-lock update; changing a checkout path alone cannot bypass the lock.

## Build a separate linkage release

With `requirements-source.txt` installed, run:

```sh
python Scripts/build_linkage_release.py \
  --fsa-source-repo /path/to/FSAVolumeReports_Panel-main \
  --research-root /path/to/accepted-fsa-release \
  --ipeds-dir /path/to/official-annual-HD-and-FLAGS \
  --crosswalk-dir /path/to/official-CW-workbooks \
  --output-dir /path/to/new-ipeds-fsa-linkage-release
```

`--research-root` is mandatory and read-only. Its accepted, unfiltered panel
defaults to `Panels/final/fsa_volume_reports_panel_1999_2025.parquet`; use
`--input-parquet` for another accepted panel inside that root. Its hash and the
panel dictionary hash must appear in `build/research_release_manifest.json`,
whose upstream acceptance must have passed.

Before any output is created, the wrapper also requires the upstream
`Checks/acceptance_qc/qa_fingerprints.json`, `build/run_started.json`, and
`build/transformations_completed.json`. Their run IDs and code/metadata maps
must agree with the accepted release. Each of the four program-family
quarantine Parquets and schema CSVs must match both accepted QA and completed
transformation hashes; the selected source inventory must match accepted QA.
This rejects evidence modified before invocation, even when a new run-start
snapshot or internally consistent conservation calculation would pass. Missing
acceptance bindings are an error, not permission to trust current file bytes.

The command reads the following FSA evidence in addition to the panel:

- `Dictionary/fsa_volume_panel_dictionary.parquet`;
- `Checks/observation_qc/*_schema_availability.csv` and `*_quarantine.parquet`;
- `Checks/download_qc/selected_panel_files.csv` and original workbooks referenced
  by its `local_path` fields for independently verified quarantine recoveries;
- the reviewed source-identity decision ledger described below.

Those source files remain in the FSA release. Referenced original workbooks
must remain readable and match their recorded SHA-256 values; historical paths
must not be silently redirected to different bytes. Missing evidence cannot
authorize invented identifiers or parent/child allocations.

All new output goes under the separate `--output-dir`:

```text
Panels/ipeds/                 bridge, diagnostics and source memberships
Panels/ipeds/unitid_research/ UNITID panel, dictionary, conservation, exclusions
Exports/unitid/               labeled Parquet and portable metadata by default
build/research_release_manifest.json
build/linkage_release_completion.json
```

The release manifest references verified upstream acceptance and the new
downstream preservation/conservation checks. Its artifact list includes only
files inside this new release. Export metadata binds the accepted manifest's
hash; a separate completion receipt records successful exports without changing
that bound manifest afterward. Use `--formats parquet csv stata excel` to export
all supported formats. The default Parquet export also creates codebooks and
value labels needed by the combined builder.

Pass the resulting downstream release to Stage 16's `--linkage-root` (the legacy
alias `--fsa-root` remains supported). The historical option name describes its
input schema; it does not mean Stage 16 should write
into the FSA source project. The reviewed Stage 16 build additionally pins the
exact UNITID-panel hash and will reject a changed source release until reviewed.

The low-level `Scripts/13_link_ipeds.py` remains available. It takes explicit
`--research-root`, `--input-parquet`, and `--output-dir`, and emits just the
linkage subtree. The wrapper above adds release acceptance metadata and exports.

## Source identity decisions

The upstream `Metadata/source_identity_resolutions.csv` currently combines
OPEID repair decisions with 16 independently reviewed UNITID-only decisions.
The original upstream ledger contains 87 rows and 34 approved OPEID repairs.
It remains upstream-owned and unmodified; splitting the ledger would also change
the annotated quarantine evidence. This downstream consumer uses the ledger
read-only and cannot convert a UNITID-only decision into a recovered OPEID.

Selection is explicit `--identity-ledger`, otherwise the release's
`Metadata/source_identity_resolutions.csv`, otherwise the verified FSA checkout's
file of the same name. Its bytes must match the reviewed dependency-lock hash
and the accepted upstream QA/release metadata hash. `--identity-ledger` permits
relocating the same reviewed evidence; it does not bypass review. New identity
decisions require an explicit reviewed lock update and matching accepted
upstream QA/release metadata, followed by the same source-row, workbook-hash,
annual HD/crosswalk, scope, and collision checks.

UNITID-only source rows remain quarantined in the upstream OPEID panel. The
downstream view includes them only when the existing evidence rules permit it.
Their OPEID stays missing. Competing family records remain blocked rather than
summed or chosen; source-conservation ledgers identify inclusions and exclusions.

## Write boundaries and verification

Output paths overlapping the read-only research root, FSA code checkout, annual
directory cache, or crosswalk cache are rejected, including symlink aliases.
Existing nonempty output directories are not overwritten. Source downloads are
off by default. If requested explicitly, the HD/FLAGS cache must also be outside
the FSA research and code roots. IPEDS source readers otherwise do not write to
the input caches.

The wrapper snapshots all directly consumed FSA evidence before linkage and
compares it again after linkage and export. Original source cells are preserved
in the linked view, and the UNITID research view must pass source conservation.
Fixture tests exercise real linkage and labeled exports, unchanged upstream
bytes, bad dependency content, foreign preloaded imports, missing explicit input
roots, unsafe output paths, and refusal to overwrite an existing result.

These checks concern reproducibility and ownership. An annual UNITID identity
tag still does not certify equivalent reporting populations or campus-exclusive
aid, and no parent aid is allocated to child institutions.
