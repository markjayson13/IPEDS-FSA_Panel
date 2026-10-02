# IPEDS–FSA Panel

Link FSA volume reports to IPEDS institutions, align annual aid periods, and export labeled research panels. This repository owns annual OPEID–UNITID linkage, reporting-scope checks, combined panels, Pell diagnostics, analysis-variable consolidation, and portable exports.

| Project | Responsibility |
| --- | --- |
| [FSAVolumeReports_Panel](https://github.com/markjayson13/FSAVolumeReports_Panel) | Download and panelize FSA reports; normalize OPEIDs; repair source identities; harmonize program measures; retain suppression/missingness; publish FSA OPEID–award-year data. |
| [IPEDSDB_Panel](https://github.com/markjayson13/IPEDSDB_Panel) | Build and label IPEDS panels; maintain annual dictionaries, lineage and parent/child cleaning. |
| **This repository** | Consume those outputs, resolve annual UNITIDs, compare reporting scope and periods, and build combined research datasets. |

The two upstream panelizers and their raw datasets are not copied here. Linkage uses a declared, verified FSA source-code dependency for original parsing/harmonization primitives; frozen combined-panel reproduction does not need either upstream checkout.

## Existing research data

The verified release remains at `/Volumes/CIRAGO/FSA-IPEDS_DS`. The local ignored `Data` link opens that same directory without duplicating or rebuilding datasets. Set `IPEDS_FSA_BUNDLE` or pass `--bundle` elsewhere. A fresh Git clone contains code, reviewed analysis recipes, tests and documentation; it does **not** contain the external replication bundle.

| File within `Data/Analysis` | Grain and scope |
| --- | --- |
| `fsa_ipeds_aid_analysis.dta` / `.parquet` / `.csv.gz` | 141,711 IPEDS institution-years, 2,888 variables; IPEDS 2004–2023 matched to preceding FSA award year. |
| `fsa_ipeds_analysis.dta` / `.parquet` / `.csv.gz` | Same IPEDS rows and variables; FSA award-year start equals IPEDS collection year. |
| `fsa_analysis.dta` / `.parquet` / `.csv.gz` | 141,038 linked UNITID–award-year records, 278 variables; FSA measures after downstream IPEDS linkage. This differs from the upstream OPEID-grain FSA panel. |

Codebooks, value labels, crosswalks and missing-code ledgers are beside the files in `Data/Analysis/Metadata`. Full masters, source bindings, diagnostics and provenance remain under `Data/Panels`, `Data/Inputs`, `Data/Metadata` and `Data/Checks`. [Release catalog](Releases/2004-2023.json).

## Storage

**The dataset is large.** The current complete bundle uses about **16.2 GB (15.1 GiB)**. Rebuilding creates additional files; the code repository is much smaller. If your computer has limited disk space, keep the input bundle and generated datasets on an external hard drive. Allow additional room for the selected exports and Python environment.

You can direct both outputs and the environment to that drive:

```sh
IPEDS_FSA_RUNTIME="/Volumes/ResearchDrive/environments/ipeds-fsa" bash reproduce.sh "/Volumes/ResearchDrive/IPEDS-FSA_Analysis" --bundle "/Volumes/ResearchDrive/FSA-IPEDS_DS"
```

Replace those paths with your drive's actual location. Keep the drive mounted while reading or rebuilding the data. The local `Data` link points to the existing bundle; it does not make another copy.

## Reproduce

Verify reviewed inputs without generating data or installing packages:

```sh
bash reproduce.sh --bundle /Volumes/CIRAGO/FSA-IPEDS_DS --check
```

Create the three labeled analysis views in a new directory with one command:

```sh
bash reproduce.sh /path/to/new-analysis-output --bundle /path/to/FSA-IPEDS_DS
```

The command installs a pinned isolated Python environment on first use. The complete external bundle is required; no unpublished download endpoint is assumed. Use `--mode masters` for the two full Parquet masters or `--mode all` for masters followed by analysis exports. Existing outputs and the source bundle cannot be overwritten. `IPEDS_FSA_RUNTIME` selects the runtime location; `IPEDS_FSA_PYTHON` can select an already provisioned compatible interpreter.

Rebuilding linkage from upstream FSA outputs is separate. See [linkage input contract](Documentation/linkage_inputs.md). Reviewed analysis recipes reject changed source hashes; extending years or changing values requires a new documented review.

For source preparation, follow the build instructions in [FSAVolumeReports_Panel](https://github.com/markjayson13/FSAVolumeReports_Panel#readme) and [IPEDSDB_Panel](https://github.com/markjayson13/IPEDSDB_Panel#readme). Preserve their release manifests, annual dictionaries and validation evidence. This project uses those outputs; it does not rerun either upstream panelizer during frozen reproduction.

## Interpretation

For the aid-aligned view, `year` remains the IPEDS collection year and `fsa_year_offset = -1`: IPEDS 2023 receives FSA AY2022–23. The collection-anchor view has offset zero. Fiscal/cohort/enrollment measures retain their own reference periods.

UNITID identity and aid-period alignment do not certify equal populations or campus-exclusive aid. Competing source-family records are blocked; parent totals are never allocated to children. Missing, suppressed, blocked and absent aid are not zero. Native recipient counts and category sums are distinct. Pell similarities are diagnostic evidence and never reassign identities.

The 2023 metadata/table-reference correction and later canonical IPEDS labels are already incorporated in the published analysis release. No values, links, missingness or scope decisions changed during this repository separation. See [analysis methods](Documentation/analysis_views.md), [published release history](Documentation/published_bundle.md), and [repository boundaries](Documentation/repository_boundaries.md).

## Development and tests

```sh
python3 -m pip install -r requirements-source.txt
FSA_SOURCE_REPO=/path/to/FSAVolumeReports_Panel python3 -m unittest discover -s tests -v
python3 -m unittest discover -s Analysis/Tests -v
```

Analysis fixture tests run without data. Set `FSA_MASTER_ROOT=/path/to/FSA-IPEDS_DS` to include integration tests against the reviewed release. Linkage tests use the FSA helper dependency, not a copied panelizer. Historical execution records in `History` are evidence, not supported entry points. Current tests/validation receipts are separate from historical release claims.
