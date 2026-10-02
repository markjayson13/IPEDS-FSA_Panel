# Final institution-count reconciliation

This audit reads the final **157-field bridge**, SHA-256 `9e5657438017106d55c248d17d2268e74adaffbb1d68c1b33ed0b0f34d8cbfc6`. It changes no pipeline output and uses no aid columns. The [supplementary annual table](residual_universe_reconciliation.csv), [record-level residual evidence](residual_universe_records.csv), and [source/hash manifest](residual_universe_manifest.json) cover all26 award years. The PSET4FLG comparison universe is unavailable for1999; that row is explicitly marked unavailable and its residual counters must not be interpreted as observed zeros.

## What changed from the first bridge

The full OPEID/award-year keyset remains157,160 rows. Final linkage retains141,505 annual UNITID tags, including146 administrative identities; **141,359 rows qualify for annual institution identity**, covering7,797 distinct UNITIDs. The restrictive longitudinal sensitivity subset contains92,566 rows.

Compared with the initial HD-only bridge,1,422 formerly unassigned rows now retain annual identities and1,151 prior tags are withdrawn; **no previously assigned tag silently changes to another UNITID**. The withdrawals are1,096 official one-to-many site relationships,49 HD/crosswalk identity disagreements, and six truncated University of Phoenix candidate lists. The additional retained identities include637 official site aliases absent from the full-OPEID HD lookup; other additions reflect retention of administrative/inactive identities for explicit review rather than unconditional institutional eligibility.

Final unresolved UNITID rows comprise10,490 explicitly foreign rows,5,002 U.S./DC rows,162 territorial/freely-associated-state rows, and one noninstitutional consolidation placeholder. Another146 rows have resolved administrative identities and are separately ineligible for institution analysis. The domestic/territorial unresolved counts include1,646 official one-to-many rows,62 directory/crosswalk conflicts,41 unresolved nonadministrative directory duplicates, and six incomplete official lists. The London/Chubb false identity is blocked; the six truncated Phoenix observations remain unassigned. Geography and match status are separate dimensions rather than additive explanations.

## AY2024–2025 worked example

Using HD2024 PSET4FLG1/3 as the explicitly defined comparison universe gives5,829 UNITIDs. This is not the same object as the number of full FSA OPEIDs or a published universe count after other exclusions.

| Mutually exclusive category | UNITIDs |
|---|---:|
| Unique resolved FSA identity within the comparison universe |4,863|
| At least one direct site candidate, without unique resolution |82|
| No direct site candidate, but official parent-OPEID affiliation with an observed FSA main ID |788|
| No reviewed direct or group relationship evidence |96|
| Total |5,829|

The first two categories produce4,945 UNITIDs with direct candidate coverage. The remaining884 are the requested residual. Of those,788 have official affiliation with a main OPEID actually observed in FSA that award year;782 also share the diagnostic OPEID root, and six are connected by official affiliation despite different directory roots. **The788 are possible group representation, not new campus assignments.** The main school's FSA amount cannot be copied to all related UNITIDs or presumed to cover every campus/program.

Examples of the six affiliations beyond the directory-root diagnostic include Orange Technical College South/West/East campuses linked through observed main OPEID02513200; Salus University through00325600; Tennessee College of Applied Technology–McKenzie through02237900; and Presidio Graduate School through00132200. Their official source relations use reporting-map code2. The evidence table retains the actual site OPEID, directory OPEID, official parent and observed FSA IDs, making the relationship reviewable without assigning dollars.

For all788 affiliated residuals, current-reference-year official evidence is Source2 for766 UNITIDs, Source2 plus4 for13, Source1 for8, and Source1 plus4 for1. CW2024 is preliminary. Source-code agreement establishes a relationship in the published crosswalk; it does not certify the coverage of a particular FSA volume measure.

## The remaining96 are identifiable categories, not presumed missing aid

The [complete96-record ledger](residual_2024_without_group_evidence.csv) uses mutually exclusive classifications based on the actual HD2024 codes:

| Observed directory category | UNITIDs |
|---|---:|
| Administrative offices, sector0 |53|
| Deferment-only, OPEFLAG3: Air Force, Naval and Military Academies |3|
| Not participating with OPEID, OPEFLAG5: Coast Guard Academy |1|
| New institution, ACTN: High Alternative Education, Puerto Rico |1|
| Other active participating institutions without reviewed direct/group evidence |38|
| Total |96|

All96 have CYACTIVE1;95 have ACTA and one ACTN. None is classified inactive by those fields. All have PRCH_SFA=-2. These are verified source codes, not hypotheses about why aid is absent.

Of the remaining38 active participating institutions,36 have the same full OPEID observed in earlier FSA award years, while Los Angeles Pacific College03124500 and Brand College04194600 have no such observation in this panel. Earlier observations do not establish current-year aid, and the absence of a current row does not establish zero aid, closure, or an extraction error. The ledger supplies the first/last observed FSA years for further study. No name-only matching or temporal backfill resolves this remainder.

## Validation and interpretation

The record-level residual table contains29,753 distinct UNITID/award-year rows across the25 years with an available comparison universe. Checks confirmed:

- No duplicate UNITID/award-year residual records.
- Every available annual universe is exactly partitioned into unique resolved, additional direct-candidate, possible group, and no reviewed relationship categories.
- Every residual is exactly partitioned into official-affiliation-plus-root, official-affiliation-only, root-only, or neither. These four categories are disjoint.
- Annual residual counts equal record-ledger counts.
- The2024 remainder53+3+1+1+38 equals96 without overlap.
- All source hashes remained unchanged during the audit; the manifest contains strict JSON nulls for unavailable metadata.

Detailed affiliation counters can overlap: an observed main OPEID is also a member of its own official parent group, so those columns must not be added. `official_parent_reference_fsa_opeids_json` identifies reference-only parent links; `official_observed_main_fsa_opeids_json` identifies FSA rows for the main IDs of residual site records; `official_shared_parent_fsa_opeids_json` identifies observed siblings in the same official parent group; `diagnostic_root_fsa_opeids_json` is a root diagnostic only. `all_possible_group_fsa_opeids_json` is their union. Every such field is explanatory evidence, not an alternative analysis key.
