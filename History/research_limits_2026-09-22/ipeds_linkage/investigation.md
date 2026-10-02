# FSA–IPEDS identity and reporting-scope evidence review

Reviewed September 22–23, 2026. This investigation explains why the first HD-only linkage was insufficient. Its counts describe the **157,160-row baseline**, identified by SHA-256 in [baseline_input_manifest.json](baseline_input_manifest.json), rather than the subsequently repaired release. The baseline SHA is `11113cfcb4f4757a500cfea6ae11c8bbd8743f51a1625adb4d51c637b4468089`. No aid allocation or duplicate expansion was performed for this investigation.

The [final institution-count reconciliation](final_count_reconciliation.md) supersedes baseline statistics for the repaired release. Its AY2024–2025 comparison universe partitions into4,863 uniquely resolved UNITIDs,82 additional direct-candidate UNITIDs,788 with official parent affiliation to an observed FSA main ID, and96 without reviewed direct/group evidence. The788 remain possible group representation, never allocated campus aid. The final157-field bridge and all26-year residual evidence are hash-pinned in that supplement.

## Principal corrections

**Use the official historical crosswalk as an additional relationship source.** NCES and FSA assembled it from PEPS, institution reporting maps, directory matching, and manual review. Eight-digit OPEIDs and UNITIDs can have many-to-one or one-to-many relations. These are different reporting systems, so a successful identity relationship does not certify that FSA totals have the same coverage as a particular IPEDS outcome. The current archive contains CW2000–CW2023 and CW2024_prelim, despite older website text mentioning fewer years. [Official Scorecard data](https://collegescorecard.ed.gov/data/), [technical documentation, pp. 4–5](https://collegescorecard.ed.gov/files/InstitutionDataDocumentation.pdf).

The 25 workbooks contain 620,663 OPEID-year records and 1,238,813 typed relations. The loader retains primary, additional, and parent references separately. Source counts are 549,685 header matches; 28,214 reporting-map matches; 16,175 relationships established using other years; 1,322 official manual matches; and 25,267 no matches. These are source-universe counts, not FSA institution counts. Workbooks contain extensive branch/location records beyond the FSA volume rows.

**Refresh source endpoints before declaring metadata unavailable.** The official Complete Data Files page now links to `/ipeds/complete-data-files/`. This endpoint supplies HD/FLAGS2023–2025. The older `/datacenter/data/` endpoint provided stale 2023/2024 bytes or a 2025 404. V2 now contains all 49 required annual data archives: HD1999–2025 and FLAGS2004–2025. For 43 older archives the new endpoint returned 404; the verified legacy archive bytes were retained with explicit fallback provenance. V1 was preserved. See [vintage comparison](ipeds_vintage_comparison.csv) and `ResearchBuild/IPEDS/official_hd_v2/acquisition_verification_manifest.json`. [Official Complete Data Files](https://nces.ed.gov/ipeds/datacenter/Default.aspx?gotoReportId=7&fromIpeds=true).

FLAGS2024 has complete Finance/SFA scope fields in the refreshed source. FLAGS2025 currently covers fall components only. This is a **component and release-vintage limitation**, not evidence that HD2025 is unavailable. The Fall2025 provisional release and NCES release schedule corroborate the timing. [Release schedule](https://nces.ed.gov/ipeds/survey-components/data-release-schedule), [2025–26 fall release memo](https://nces.ed.gov/ipeds/survey-components/release-memo?type=fall&year=2026).

## Baseline discrepancy measurement

| HD-only result | Official crosswalk result | FSA-year rows |
|---|---|---:|
| No exact HD match | One complete site candidate | 637 |
| No exact HD match | Official no site match | 464 |
| No exact HD match | No crosswalk entry | 13,435 |
| Unique HD match, including inactive/admin tags | Multiple site candidates | 1,100 |
| Unique ordinary HD match | Truncated/incomplete candidate set | 6 |
| Multiple exact HD UNITIDs | One complete site candidate | 46 |
| Unique HD and unique crosswalk | Different UNITID | 49 |

The 637 potentially informative new relations cover 260 OPEIDs: 355 Source1, 264 retrospective Source3, 16 Source4, and 2 Source2 rows. They are **candidates requiring eligibility and scope checks**, not 637 automatically approved assignments. The complete cross-tab and record evidence are in [hd_vs_official_crosswalk_counts.csv](hd_vs_official_crosswalk_counts.csv), [unmatched_with_official_unique_candidates.csv](unmatched_with_official_unique_candidates.csv), and [hd_crosswalk_unique_disagreements.csv](hd_crosswalk_unique_disagreements.csv).

Examples clarify the mechanisms:

- Lawson State–Bessemer, `00105901`, has eight baseline years with a CW Source1 relation to UNITID101569 despite no same-full-ID HD match. An official site relation can identify a branch that the directory's primary OPEID omits.
- Blessing Hospital, `00621400`, has 14 unmatched years linked retrospectively to UNITID143297 by Source3. This needs historical corroboration rather than automatic nearest-year filling.
- University of South Carolina Regional Campuses, `00991100`, is Source5/no site match in CW2000–2023. Its institutional label and parent references do not justify selecting a campus UNITID.
- Brown Mackie–South Bend, `00458300`, AY2010–11, has HD UNITID151944 versus CW primary460613; published sources conflict and neither identifier should silently overwrite the other.
- University of Alabama in Huntsville, `00105500`, CW2000, has primary100706 and additional100733 even though IPEDSrpt=1. The reporting code cannot override explicit additional matches.

Of the baseline's 14,536 unmatched rows, 6,039 explicitly use foreign-state code FC. Another 4,450 have blank state; 4,167 of those share a full OPEID with an FC-coded FSA row in another year. This is historical geography evidence, not a license to label every blank-state row foreign. The remaining 4,047 rows have other nonblank geography. U.S. territories must remain distinct from foreign schools. An unmatched row is neither zero aid nor necessarily a malformed OPEID.

## Temporal and source-quality limits

The default CW/HD year is the FSA award-year start, with end-year sensitivity considered separately. It is an explicit analytical convention. IPEDS outcome components often describe different reference periods; directory-year linkage alone cannot align enrollment, finance, and aid years.

The crosswalk is retrospective. CW2000 lists `00109103` as Arkansas State–Beebe–Searcy and maps it to historical Foothills UNITID106935 with Source3. It also lists old Foothills `00531900` with Source1. This illustrates why a CW year does not establish that every PEPS OPEID existed then. Preserve prior-ID and dated-change notes; do not backfill blank FSA OPEIDs solely from retrospective aliases.

All 25 workbooks' variable/value labels and notes were examined. They share one substantive schema. Pre2005 files lack detailed PEPS eligibility/certification/approval triplets because historical PEPS detail was unavailable; 2011 adds only empty spreadsheet columns. [Annual documentation coverage](crosswalk_annual_documentation_coverage.csv) records per-workbook hashes and notes hashes, and `cw_YYYY_dictionary_notes.txt` preserves each dictionary for inspection.

Six University of Phoenix `02098800` AddMatch cells, CW2003–2008, end with truncated three-digit fragments. Valid complete UNITIDs are retained but `cw_site_parse_complete=False`; these candidate sets must not be treated as complete. CW2015 has zero populated change-event notes across all 27,540 records, while CW2014 has635 and CW2016 has554. CW2024_prelim has only9. These observations do not prove that no changes occurred; lack of notes is insufficient evidence of stability.

## Institution counts and program-specific reporting scope

IPEDS reporting institutions can encompass multiple locations or systems, and their aggregation can change without a simple stable-campus interpretation. Finance and Academic Libraries also have component-specific parent/child reporting. These relationships should screen the relevant outcomes rather than automatically excluding an institution from every possible analysis. [NCES institutional grouping guidance](https://nces.ed.gov/ipeds/use-the-data/institutional-groupings-in-ipeds).

Finance distinguishes full from partial parent/child arrangements. Partial arrangements can combine balance-sheet information while retaining separate revenues and expenses; full arrangements combine more measures. NCES cautions that derived allocation factors can misrepresent individual institutions. An annual Finance relationship therefore cannot be applied mechanically to FSA aid amounts. [NCES finance reporting guidance](https://nces.ed.gov/ipeds/report-your-data/data-tip-sheet-reporting-finance-data-multiple-institutions).

A further limitation crosses OPEID roots: separate main OPEIDs under common control or ownership can submit one FISAP. Therefore unique root/UNITID identity does not by itself certify exclusive Campus-Based program scope. COD also documents OPEID first-digit overflow after99 additional locations and continuation of Campus-Based applications by the surviving school after a merger. Diagnostic roots can identify possible related locations; they cannot allocate dollars. [FSA COD Campus-Based FAQ, questions on combined applications, OPEID format, and mergers](https://cod.ed.gov/cod/ecbFAQ.action).

Published IPEDS institution counts use a defined universe rather than all raw directory rows. For 2025–26, NCES reports5,779 including administrative offices and four non-TitleIV service academies, and excludes498 institutions reported exclusively by parents from that universe count. Thus FSA full OPEID count, CW location count, raw HD UNITID count, active TitleIV count, and analytical matched count must be reconciled as separate quantities. [2025–26 release memo](https://nces.ed.gov/ipeds/survey-components/release-memo?type=fall&year=2026).

## Implemented evidence interfaces and remaining limits

The [documentation review coverage log](documentation_review_coverage.csv) identifies 80 primary-source entries, their hashes, reviewed topics, and limitations. Source retrieval is distinguished from the specific definitions reviewed.

`Scripts/fsa_official_crosswalk.py` returns `(relations, records, source_manifest)`. It preserves raw cells and does not assign aid. [Every normalized crosswalk field is defined here](official_crosswalk_fields.csv). `UNITIDs Not matched` tabs were separately retained in `official_crosswalk_unmatched_ipeds.parquet` (7,821 source UNITID-year rows), rather than interpreted as missing FSA aid.

Six crosswalk regression tests cover relationship roles, malformed/truncated IDs, duplicate source rows, history evidence, preliminary vintages, and no-parent fallback. Six downloader tests cover endpoint preference, explicit404 fallback, no fallback on403, cached hash verification, preserved origin, and failed-refresh preservation. `acquire_crosswalks.py` records remote archive/member provenance; `acquire_current_ipeds.py` verifies all49 cached inputs and can acquire a fresh separate vintage.

This review covers every available CW workbook and the required annual HD/FLAGS sources, plus the official reporting, universe, and release documentation listed above. It does **not** certify every campus boundary, audit every institution's private FISAP application, or resolve every conflicting historical ID. Parent references, no-match cases, retrospective aliases, incomplete relation lists, provisional metadata, and unresolved source conflicts remain explicit review categories. The corrected linkage release and family-specific eligibility outputs determine final usable counts; this report's baseline counts must not be substituted for those release statistics.
