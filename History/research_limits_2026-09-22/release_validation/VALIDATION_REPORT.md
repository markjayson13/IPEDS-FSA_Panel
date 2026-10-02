# Verified FSA research release, version 2

Publication copy of the completed local build report. Quantitative results are unchanged; links below point to tracked repository evidence. Paths labeled **local generated artifact** are under ignored `ResearchBuild/` and are not included in this Git publication. The original report and build-file hashes are recorded in the [snapshot README](README.md).

The completed local build preserves the FSA reporting-unit master and adds an annual UNITID panel with explicit source-family eligibility. Its institutional count is **not** the number of distinct source OPEIDs. The earlier version-1 master remains unchanged.

## Files to use

- Institution-year panel (**local generated artifact**: `ResearchBuild/2026-09-22-v2/Panels/ipeds/unitid_research/fsa_unitid_award_year_panel.parquet`): **141,038 rows**, **7,797 distinct UNITIDs**, **1,312 columns**. **141,020 rows** have at least one included family; 18 rows retain blocked families only. Select the program-family status appropriate to the analysis.
- Source-preserving FSA master (**local generated artifact**: `ResearchBuild/2026-09-22-v2/Panels/final/fsa_volume_reports_panel_1999_2025.parquet`): **157,160 OPEID-years**, **8,743 reporting IDs**, **665 columns**. One reporting ID is an explicit default-school placeholder, not an institution.
- Annual count reconciliation (**local generated artifact**: `ResearchBuild/2026-09-22-v2/Panels/ipeds/ipeds_institution_count_reconciliation.csv`), UNITID dictionary (**local generated artifact**: `ResearchBuild/2026-09-22-v2/Panels/ipeds/unitid_research/dictionary.csv`), source-family exclusions (**local generated artifact**: `ResearchBuild/2026-09-22-v2/Panels/ipeds/unitid_research/exclusions.csv`), and [release manifest](research_release_manifest.json).
- [Complete investigation of the count gap](../ipeds_linkage/final_count_reconciliation.md), including [all-year residual partitions](../ipeds_linkage/residual_universe_reconciliation.csv) and a record-level queue.

The annual bridge has **141,359 institution-eligible FSA rows**; collapsing eligible source families onto UNITID/year and adding the 16 verified UNITID-only rows produces the institution view. Another 146 source rows have identified administrative UNITIDs and are excluded from the institution view. The restrictive sensitivity sample has **92,566 rows**; it is not the definition of all matched institutions.

## Why the latest institution counts differ

For AY2024–2025, with HD/CW2024 as the declared start-year anchor:

| Disjoint part of the HD publication universe | UNITIDs |
| --- | ---: |
| Unique FSA institutional assignment within the universe | 4,863 |
| Additional direct HD/crosswalk candidates without a unique institutional assignment | 82 |
| No direct candidate, but official affiliation to an observed FSA parent reporting unit | 788 |
| No reviewed direct/group evidence | 96 |
| **IPEDS PSET4FLG 1/3 universe** | **5,829** |

The 788 relationships explain possible reporting-group representation. They do **not** allocate aid to campuses and do not enter the unique UNITID panel through a parent substitution. The remaining 96 comprise **53 offices, four service academies, one new institution and 38 other active participating institutions**. No zero-aid or closure assumption was applied. Thirty-six of the 38 appear in earlier FSA years. All 25 years with the required universe field have exact, disjoint count partitions; the 1999 publication-universe comparison is explicitly unavailable.

## Repairs and documentation evidence

- Full OPEID normalization retains location suffixes and leading zeroes; full IDs, roots and FAFSA school codes remain distinct.
- Official annual crosswalks add site aliases, additional site matches, parent references and historical-relation provenance. **637 source rows** obtain verified annual UNITIDs through official aliases. Against the frozen V1 bridge, 1,422 previously null tags are added and 1,151 tags are retracted because of official ambiguity/conflict; no assigned UNITID is silently changed to another. These tag counts include administrative identity, not only the institutional analysis sample.
- One exact-ID London/Chubb geographic contradiction is blocked. The `88888800` default-school FFEL record stays in the master for source conservation but is excluded from institutional identity. Name/state disagreements remain explicit review diagnostics.
- Leading-zero ZIP+4 and cosmetic descriptor corrections reduce flagged OPEID-years from 15,125 to **10,277**, including the newly exposed review case after identity recovery.
- **34 full-OPEID recoveries** restore **$29,648,694.58 Pell** using same-year independent FSA and official HD evidence. **16 additional verified UNITID-only grant rows** retain unknown OPEIDs and restore **$151,082,578.97 Pell** to the separate institution view after collision checks.
- Refreshed HD/FLAGS sources reach 2025; current FLAGS2024 restores Finance/SFA metadata. FLAGS2025 remains a partial release.
- The review covers all **86 selected FSA workbooks**, **1,515 observed header records**, **64 source measures**, **48 registered policy/schema events**, **53 IPEDS dictionary vintages** (49 selected annual dictionaries, 916 relevant field entries), and **25 official historical crosswalk workbooks**. Ten FSA workbooks lack definition sheets. Annual meanings and conflicting official definitions are preserved rather than generalized across years.

See the reviewed repository [annual dictionary review](../../../Documentation/ipeds_documentation_review.md), [research limits](../../../Documentation/research_limits_review.md), [policy review](../../../Documentation/policy_and_reporting_changes.md), [UNITID method](../../../Documentation/unitid_panel.md), and [source-identity evidence](../../../Documentation/source_identity_recovery.md).

## Quantitative verification and exclusions

**134 regression tests**, **313 acceptance checks**, **24 scope checks**, **3,047 UNITID conservation checks**, and **123 independent whole-panel checks** pass. There are no duplicate UNITID/year keys. All **4,627,373 accepted source measure cells** conserve into the master. Every included source-family value, status, bound and provenance cell conserves into the institution view; excluded source values remain in full ledgers. Source/code/artifact hashes, the executable source snapshot and the residual-count evidence were independently revalidated after packaging.

Across all award years, the UNITID view includes **$567,083,119,853.66 known Pell dollars**, including the verified UNITID-only supplement. It withholds **$36,554,159,051.12** for annual identity ineligibility (including administrative units and one-to-many/conflicting/incomplete relationships), **$1,367,571,266.30** for duplicate-family ambiguity, and **$9,015,765.83** in still-unidentified raw grant rows. These are known-source-dollar sums, not certified national totals. The $9.02 million is only the remaining raw missing-identity subset, not all dollars excluded from institutional analysis. The generic source-ledger disposition `excluded_annual_identity_unresolved` includes identified administrative units; use the accompanying resolution status and administrative flag to distinguish the actual cause.

The [independent dollar coverage table](../source_family_year_disposition_coverage.csv) records exclusions by source family and year. Missing, unavailable, suppressed and blocked cells remain distinct. The 53 raw source rows still without FSA OPEIDs consist of 32 grant and 21 zero-only Campus-Based rows; 16 of those grant rows have the separately verified UNITID identities described above.

## Research limits

Identity does not certify campus-exclusive aid scope. Combined FISAP applications can cover separate main OPEIDs, component reporting parents can differ, and historical identifiers can reflect later reconstructed relationships. The release does not allocate group aid, deduplicate people, automatically stitch mergers, claim current upstream FSA freshness, or turn absent records into zeroes. Three observed schema changes still lack verified publication/policy explanations; those searches and unresolved causes are documented.

The source vintage remains the explicitly frozen 86-workbook scope: grant/Direct AY1999–2000 through AY2024–2025; Campus-Based AY2001–2002 through AY2023–2024; FFEL AY1999–2000 through AY2009–2010. The derived loan series retain exact/partial distinctions, and the later Direct-only scope does not claim all later FFEL activity is zero.

## Fingerprints

- Master SHA-256: `975a82f20ee7bfff47dccf5a622d499b0ee20f432fa41f366a09154ec711670e`
- UNITID panel SHA-256: `93d675fde748d6c480c7eb9050dccec49a88892154a60ed21e016539ba9983ba`
- Bridge SHA-256: `9e5657438017106d55c248d17d2268e74adaffbb1d68c1b33ed0b0f34d8cbfc6`
- Release manifest SHA-256: `547d26b6729897d0a4d67b330c1b20a2e4f45df3b14d358d3a74014d69602345`

Reproduce using the recorded environment and frozen source snapshot; the original version-1 master hash was separately confirmed unchanged. This report records the completed local build; generated panels and inputs remain local and are excluded from this Git publication.
