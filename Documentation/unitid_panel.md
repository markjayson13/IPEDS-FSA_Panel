# UNITID × award-year research view

`Scripts/fsa_unitid_panels.py` creates a wide institution-year view from annually resolved FSA source-family records. The full OPEID-year master remains the row-preserving record of FSA reports. This additional view does not allocate parent amounts among children, infer missing OPEIDs, or sum competing records to manufacture one institution-year value.

The panel key is `unitid, award_year`. Each program family retains its own full source OPEID: `grant__opeid8`, `campus__opeid8`, `loan_direct__opeid8`, and `loan_ffel__opeid8`. They may differ within an institution-year. There is intentionally no single canonical `opeid8` in this panel. The source report's descriptors, workbook, sheet, Excel row, raw tokens, cell statuses, and bounds stay with the family that reported them. Annual bridge metadata, including component/reporting flags and scope alignment, are also copied under each source-family prefix.

## Inclusion and collisions

Only an explicit true `ipeds_annual_identity_eligible` and a positive resolved `unitid` authorize a master source record to enter this view. The string `False` is parsed as false. The module fails if the bridge lacks explicit annual eligibility; it does not substitute the obsolete strict-sample screen. A missing bridge record or unresolved annual identity remains in the full family ledger with an exclusion reason.

For each UNITID-year-family:

- One eligible source record supplies that family's columns unchanged.
- Two or more identified source records block **all** competing family records. No first/last preference, amount sum, name match, or OPEID-root rule chooses between them. Quantitative cells are missing and their status is `ambiguous_family_multiple_records`.
- No family record has a family-level status of `absent_source_record`; it never means zero aid. Its individual measure statuses use the selected report/schema catalogs: `report_not_available` when that family has no report for the year, `unavailable_in_schema` when the report lacks that variable, and `absent_source_record` when the report and field exist but no institution record is included. Collision/exclusion statuses take precedence on blocked cells. Missing availability metadata is explicit as `availability_metadata_missing`.

Other uniquely resolved program families can remain in the same institution-year when one family is blocked. A row with only blocked families remains as an explicit ambiguity record; `included_source_family_count` and `blocked_source_family_count` distinguish that situation. Use the family status appropriate to the outcome under analysis. A global descriptor flag does not remove a record: source-name, ZIP, historical identifier, and component-scope evidence remain available for study-specific sensitivity analyses.

## Verified UNITID with unresolved OPEID

The source-identity ledger currently contains 16 AY2009–2010 grant campus rows with a verified UNITID but unresolved full OPEID. These are distinct raw grant rows, not a distribution of another record's grants. A record qualifies through the explicit status `verified_component_identity_opeid_unresolved`. It must remain in the current source quarantine, its OPEID-recovery status must remain unresolved, and no recovered OPEID may be supplied.

At runtime, the module checks the selected source filename/year, actual workbook SHA-256, source sheet/schema, Excel row, original OPEID token, school name, state, and postal token against the evidence ledger. It requires a same-year HD identity and official campus-crosswalk evidence for the verified UNITID. Raw measures are parsed with the same parser as the main build; missing/suppressed values, cents, exceptional tokens, and suppression bounds retain their meanings. A verified UNITID never fills an unknown OPEID. The output retains the resolution ID, evidence JSON, source digest, and raw postal token.

The runtime membership guard checks the verified UNITID against **all accepted FSA records in the same family and award year**, including records whose official crosswalk has multiple site candidates and therefore no unique bridge assignment. Its membership input includes official primary/additional matches and applicable HD candidates. A collision blocks the orphan with `excluded_official_membership_overlap`; missing membership input blocks it with `excluded_missing_official_membership_guard`. If the orphan also shares the final UNITID-year-family key with another record, both receive the multiple-record status, and `identity_guard_status` preserves the earlier collision reason.

Official parent-reference relations are not direct campus assignments. If supplied in the guard input, they are retained as parent-scope sensitivity evidence, rather than counted as demonstrated duplication. A common OPEID root alone neither drops a verified campus record nor allocates another record to it. All unresolved quarantined rows and their evidence remain in `quarantine_identity_evidence.parquet`.

## What the panel does and does not establish

The construction verifies annual institutional identity and prevents repetition of the **same source-family record** in the UNITID view. This does not establish that the agency's published amounts contain no unobserved parent/child overlap, or that an amount belongs exclusively to the tagged IPEDS campus. The panel includes both a general scope warning and family-specific scope notes; it is not labeled a universally safe campus-allocation sample.

Campus-Based scope needs particular care. The COD FAQ describes combined FISAP reporting by separately identified institutions under common ownership/control. FSA guidance also distinguishes location-level eligibility from Campus-Based funding at the main-campus level and describes when an institution must submit its own FISAP instead of being included in an affiliate's. A one-to-one directory or crosswalk identity is therefore insufficient to certify UNITID-exclusive Campus-Based amounts. Sources: [COD Campus-Based FAQ, question 5](https://cod.ed.gov/cod/ecbFAQ.action), [FSA 2020–2021 waiver and reporting guidance](https://fsapartners.ed.gov/knowledge-center/library/electronic-announcements/2019-12-17/designation-title-iii-or-title-v-institution-and-waiver-non-federal-share-requirement-fws-and-fseog-2020-21-award-year-updated-january-15-2020), [FSA 2026–2027 Campus-Based common elements](https://fsapartners.ed.gov/knowledge-center/fsa-handbook/2026-2027/vol6/ch1-campus-based-programs-common-elements). These identify reporting mechanisms; they are not proof that a particular historical institution used a combined application.

Parent-reported Direct/FFEL or Campus-Based amounts are never copied into the 16 verified campus-only grant rows. Any loans appearing at those UNITIDs must come from separately included unique loan-family records. Borrower/recipient counts retain the limitations in [loan harmonization](https://github.com/markjayson13/FSAVolumeReports_Panel/blob/main/Documentation/loan_harmonization.md).

## Loan recomputation

After the family records are merged, the module recomputes loan harmonization and Direct/FFEL combinations from preserved source channels. It never carries an old OPEID-row combined total into a different UNITID combination. Complete sums require the same source-year components as the main panel. A blocked required family prevents a complete total; a separately labeled observed partial sum may still describe the available channel. Its combined status is `incomplete_blocked_unitid_family`. Counts summed across loan categories or channels are still count sums, not unique recipients.

## Artifacts and validation

The stage-13 caller supplies an output directory, normally `Panels/ipeds/unitid_research` inside the build root:

| Artifact | Content |
|---|---|
| `fsa_unitid_award_year_panel.parquet` | Wide UNITID × award-year view, family values and statuses |
| `{family}_source_record_ledger.parquet` | Every accepted master source-family record plus verified quarantine candidates, including excluded raw values |
| `quarantine_identity_evidence.parquet` | Still-quarantined raw rows joined to the full resolution evidence, including unresolved cases |
| `exclusions.csv` | Excluded family records, provenance and reasons; full measures remain in the family ledger |
| `conservation.csv` | Exact included-cell comparisons for values, descriptors, provenance, statuses and bounds; included monetary/count sums by year |
| `dictionary.csv` | Every panel column, inherited source and annual bridge definitions, and added UNITID-selection/provenance definitions |
| `manifest.json` | Output paths/hashes, source-family inclusion/exclusion counts, dictionary coverage and conservation results |

The build fails on duplicate keys, malformed UNITIDs, absent explicit eligibility, changed verified source evidence, missing source measures, nonunique source-record IDs, any included-cell mismatch, or incomplete dictionary enumeration. Excluded values remain in source ledgers; they are not treated as a discrepancy between national source totals and the selected UNITID view. National totals from the UNITID view therefore require its explicit coverage/exclusion accounting.

The API is:

```python
build_unitid_panel(master, bridge, root, outputdir,
                  memberships=memberships,
                  identity_ledger_path=None)  # returns the manifest dictionary
```

`memberships` has `opeid8`, `award_year`, and `unitid`, with optional `relation_type` and source-evidence fields. The caller must include unresolved one-to-many site candidates, not only rows that obtained a bridge UNITID. The default identity ledger is `<root>/Metadata/source_identity_resolutions.csv` if present, otherwise the repository's metadata ledger. Outputs do not mutate either the master or the input bridge.

The regression suite checks separate family OPEIDs, no blanket descriptor exclusions, exact cents, suppression intervals and exceptional tokens, false boolean strings, multiple-record blocking, verified orphan insertion without an OPEID, missing/colliding membership evidence, changed workbook hashes, parent-reference sensitivity, and recomputed/incomplete loan totals. Run:

```sh
/usr/local/bin/python3 -m unittest discover -s tests -p test_unitid_panels.py -v
```

Use the final build manifest for empirical inclusion counts. Fixture tests verify the selection and conservation rules; they do not replace the full raw-source rebuild and annual linkage checks.
