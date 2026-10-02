# Annual FSA–IPEDS identity and reporting scope

The source master is **full eight-digit OPEID × award year**. The institution view is **UNITID × award year**, assembled separately for each FSA source family. Identity, eligibility, institutional activity, component reporting scope and sample restrictions are separate fields. An identified institution is not proof that its FSA amounts cover only that campus.

The former strict subset remains a **sensitivity sample**, not the recommended national institution count. Its whole-history, descriptor and component restrictions discard otherwise valid annual identities. See [annual dictionary review](ipeds_documentation_review.md), [UNITID panel construction](unitid_panel.md), [source identity recovery](https://github.com/markjayson13/FSAVolumeReports_Panel/blob/main/Documentation/source_identity_recovery.md), and [descriptor correction evidence](https://github.com/markjayson13/FSAVolumeReports_Panel/blob/main/Documentation/descriptor_resolution.md).

## Reviewed documentation and frozen sources

The review covers all **49 selected annual HD/FLAGS dictionaries**, four superseded 2023/2024 dictionary vintages, and all **25 official historical crosswalk workbooks** (CW2000–CW2023 and CW2024_prelim). The annual dictionary ledger contains 916 relevant field entries. The crosswalk review covers primary/additional matches, parent references, Source codes, reporting-map relationships, prior-ID and change notes, and each workbook's unmatched-IPEDS sheet. The [documentation review](ipeds_documentation_review.md) and [crosswalk investigation](../History/research_limits_2026-09-22/ipeds_linkage/investigation.md) distinguish observed schema changes from policy or reporting changes.

The directory bundle includes **HD1999–HD2025 and FLAGS2004–FLAGS2025**, 49 ZIPs. NCES's current `/ipeds/complete-data-files/` endpoint supplies 2023–2025 vintages; 43 older sources use the recorded legacy fallback. Both the current and superseded source hashes remain documented. The new FLAGS2024 contains Finance/SFA relationships; FLAGS2025 is still a partial fall release. A 404 from the old endpoint was a delivery limitation, not evidence that HD2025 was unreleased. Consult [NCES releases](https://nces.ed.gov/ipeds/survey-components/data-release-schedule) and the [fall 2026 release memo](https://nces.ed.gov/ipeds/survey-components/release-memo?type=fall&year=2026).

The crosswalks come from the official College Scorecard archive identified in the source manifests, not a name-derived private crosswalk. Its annual year indexes the IPEDS reference year. PEPS location identities and historical links may reflect retrospective reconstruction. `Source=3` explicitly uses relationships established in other years; `Source=4` identifies an official manual match. A workbook named CW2000 does not prove every OPEID or change note describes conditions in 2000. Missing change notes do not prove stability: CW2015 has no populated COA notes and CW2024 is preliminary.

Each input retains URL, SHA-256, archive member and available retrieval metadata. Revised `_rv`/`_r` CSVs take precedence when supplied. Every release verifies the input and output hashes. Annual dictionaries and source labels remain evidence even where their boilerplate is inconsistent, notably OPEFLAG=4 in 2001.

## Resolution rules

1. Preserve the complete OPEID, including leading zeroes and the location suffix. Numeric full-ID storage is left-padded to eight digits. A six-digit eligibility root, FAFSA school code, name or parent OPEID cannot replace a missing full identifier.
2. At the selected annual anchor, form the union of official `IPEDSMatch` **and** `AddMatch` site UNITIDs. `IPEDSrpt=1` does not override additional matches. Parent `IPEDSMain` references stay separate and never assign a site.
3. Incomplete site parsing or multiple site UNITIDs leaves `unitid` missing. Six University of Phoenix AddMatch cells have a truncated trailing fragment; their valid members and raw fragment remain visible, and incomplete parsing cannot certify a singleton.
4. A unique official site must exist in the same annual HD. It must agree with any exact full-ID HD candidates. Multiple HD records can be resolved only when the official site is among them and every other HD candidate is an administrative office. A second nonadministrative candidate or a contradictory unique HD identity remains unresolved.
5. If the crosswalk supplies no site match, a unique full-ID annual HD record can identify the institution, with directory-only provenance and the crosswalk's no-match flag retained. A missing official crosswalk year is distinct from an unmatched institution.
6. Official historical aliases may identify a unique contemporaneous UNITID even when its HD OPEID differs. These carry `ipeds_identity_uses_historical_relation` and `ipeds_identity_strict_contemporaneous`; researchers can restrict to direct annual corroboration. There is no nearest-year fill and no inference of an unprinted FSA OPEID.
7. A printed foreign-school record cannot be assigned to a domestic HD institution merely because its numeric OPEID matches. Such contradictions remain unresolved with candidate evidence retained. Domestic state differences and name similarity below 0.5 are review diagnostics, because moves, rebranding and reporting addresses can legitimately differ. The review identified London College of Communication versus The Chubb Institute at OPEID `03462400`, AY2001–2002; that assignment is blocked.
8. Administrative identities remain tagged but are excluded by `ipeds_annual_identity_eligible`. Closed or inactive records can retain a verified historical identity. Activity and scope are study restrictions, not automatic evidence that an identifier is wrong.

`ipeds_directory_unitid` preserves the earlier exact-directory assignment separately from the new `unitid`. `ipeds_resolution_status` explains every resolution or unresolved case. The linked panel preserves every original FSA cell and row. The direct membership ledger contains no aid measures and must not be used as a one-to-many aid join.

## Time and reporting boundaries

The default anchor joins AY2010–2011 to HD2010 and CW2010. This is an analytic convention. `--anchor end` uses 2011. The directory start/end candidates and sensitivity flags remain available. To link an IPEDS outcome, use that survey's actual measurement period; Finance, SFA, enrollment and completions can refer to different prior years. [IPEDS methodology](https://nces.ed.gov/ipeds/survey-components/ipeds-survey-methodology), [NCES statistical handbook](https://nces.ed.gov/statprog/handbook/ipeds.asp).

IPEDS statistical institutions can represent campuses, districts or systems and can change their aggregation over time. The federal financial-aid data review documents substantial FSA–IPEDS aggregation differences, including historical University of Phoenix examples. A stable ID does not certify a stable boundary. [NCES institutional groupings](https://nces.ed.gov/ipeds/use-the-data/institutional-groupings-in-ipeds), [NCES financial-aid data review](https://nces.ed.gov/pubs2012/2012834.pdf).

Component relationships are retained separately for Finance, SFA, fall enrollment, twelve-month enrollment and completions, with other raw PRCH/IDX/PRCHTP fields preserved. Finance partial-child codes 3/5 and partial-parent-and-child code 6 do not mean all aid or enrollment is reported at that same parent. A missing component field is unknown; `-2` is an explicit not-applicable code. [NCES Finance reporting guidance](https://nces.ed.gov/ipeds/report-your-data/data-tip-sheet-reporting-finance-data-multiple-institutions).

Campus-Based reporting can combine institutions with distinct main OPEIDs under common control into one FISAP. Therefore even a singleton eligibility root does not certify UNITID-exclusive Campus-Based amounts. The scope note stays on each family view. COD's location-overflow rule also allows increments to the first OPEID digit after 99 additional locations; `0 + OPEID[1:6]` is a diagnostic only, never an assignment or allocation key. [Official COD Campus-Based FAQ](https://cod.ed.gov/cod/ecbFAQ.action).

## Institution counts and outputs

`ipeds_institution_count_reconciliation.csv` reports annual FSA reporting units, explicitly foreign institutions, states/DC, territories/freely associated states, unknown/conflicting geography, resolved unique UNITIDs, multiple FSA OPEIDs per UNITID, one-to-many relations and HD/CW disagreements. It separately enumerates all HD units, administrative offices, missing OPEIDs and the documented PSET4FLG comparison universe (1 before 2010; 1/3 thereafter). Candidate-membership coverage separately identifies IPEDS units represented by a possible group/crosswalk relationship without a unique assignment, and units with no direct FSA candidate. Candidate representation does not allocate dollars or establish a unique identity. These are different denominators. An IPEDS institution without a uniquely linked FSA record is not automatically missing aid: it may have no reportable volume, be represented within a group, or fall outside the selected source years/programs.

| Output under `Panels/ipeds/` | Use |
| --- | --- |
| `fsa_ipeds_bridge.parquet` | One row per OPEID/year; annual identity and scope evidence. |
| `fsa_ipeds_linked_panel.parquet` | All original source-master rows and measures plus the bridge. |
| `unitid_research/fsa_unitid_award_year_panel.parquet` | UNITID/year panel with source-family OPEIDs and eligibility statuses; no fabricated canonical OPEID. |
| `unitid_research/*_source_record_ledger.parquet` | Every source-family record, including complete measures for exclusions and collisions. |
| `unitid_research/conservation.csv` | Exact included-cell preservation and annual measure reconciliation. |
| `fsa_ipeds_strict_panel.parquet` | Restrictive sensitivity sample; excludes wider scope and time-review cases. |
| `fsa_ipeds_direct_site_memberships.parquet` | Direct HD and official site candidates; no aid allocation. |
| `fsa_ipeds_official_crosswalk_{records,relations}.parquet` | Original crosswalk fields and typed site/parent relations. |
| `fsa_ipeds_candidates.parquet` | Start/end HD exact and diagnostic-root candidates. |
| `ipeds_bridge_dictionary.csv` | Every generated bridge column. |
| `ipeds_source_manifest.csv`, `ipeds_crosswalk_source_manifest.csv`, `ipeds_linkage_manifest.json` | Source and artifact hashes, vintages and coverage. |

A UNITID/year/family with multiple identified FSA rows is blocked for that family. Its amounts are neither summed nor selected arbitrarily; other uniquely identified families remain usable. The panel retains rows whose only identified families are blocked, with `included_source_family_count=0`; exclude those from a count of institutions with usable aid. Sixteen verified grant institution identities with unknown FSA IDs enter through the quarantined-source ledger after checking that no accepted grant's direct official membership already includes them. They retain missing OPEIDs and explicit evidence. This is a preservation and identity procedure, not a certification that parent/group aid elsewhere cannot overlap.

The strict view still screens both HD anchors, singleton diagnostic groups, source descriptor flags, activity, full/new participation interpreted by year, parent/child metadata and lifetime ID changes. ACT=M means closure with reportable prior-year data; ACT=G describes a perfect child, not generic inactivity. Broad strict exclusions remain sensitivity decisions rather than match failures.

## Rebuild

```sh
python3 Scripts/00_run_all.py \
  --root ResearchBuild/2026-09-22-v2 --skip-download \
  --ipeds-dir ResearchBuild/IPEDS/official_hd_v2 \
  --ipeds-crosswalk-dir ResearchBuild/IPEDS/official_crosswalks --run-qaqc
```

The root must contain the frozen selected inventory and raw workbooks. Stage13 also runs separately with `--input-parquet`, `--ipeds-dir`, `--crosswalk-dir`, `--research-root` and `--output-dir`. Source refresh is explicit. The earlier build remains a historical snapshot; the version-2 release records its own executable source, metadata, documentation, tests and environment.

## Explicit noninstitutional source record

The AY2009–2010 FFEL report contains full-ID token `88888800`, label `DEFAULT SCHOOL FOR CONSOLIDATED LOANS`, state `NR`, at Excel row 2604. It reports $11,393 subsidized and $10,667 unsubsidized disbursements. The source-preserving master retains those cells, while `fsa_source_identity_kind=noninstitutional_placeholder` explicitly excludes the row from institution assignment/counts. It is not an ordinary unmatched institution and does not establish a debt-consolidation volume series. The release manifest's legacy `institutions` number counts distinct FSA reporting IDs; use the UNITID panel and count reconciliation for educational-institution counts.
