# Identity descriptor follow-up

This review used the interim V2 bridge with SHA-256 `7394e3cd93998c2dc0af26c56b359816fc326374f9aa99c8b7368dda73134b7f`. The final rebuild incorporates the correction below; interim counts are not final release statistics.

## Confirmed foreign/domestic identity contradiction

FSA full OPEID `03462400`, AY2001–2002, describes **University of the Arts London / London College of Communication**, state `FC`, school type `Foreign Public`. The same full identifier occurs on the official HD2001 record for **The Chubb Institute**, UNITID439552, White Plains, New York, OPEFLAG5 and ACTP. HD2002 retains UNITID439552 but has no valid OPEID, OPEFLAG6 and ACTW. CW2000–2003 contains neither this OPEID nor a primary match to that UNITID. FSA reports consistently retain the London/foreign descriptor for this ID during AY2000–2001 through2011–2012.

This demonstrates that a syntactically valid, unique full-OPEID directory lookup can still produce an incorrect institutional identity. The annual linkage now blocks explicit foreign/domestic contradictions while retaining the candidate, source evidence and FSA amounts. No substitute UNITID is guessed. A regression supplies a hypothetical exact directory candidate and verifies that the foreign contradiction still prevents assignment.

The source evidence is locally reproducible in `ResearchBuild/IPEDS/official_hd_v2/FA2001HD.zip` and `HD2002.zip`, with their download manifests, the official crosswalk records, and the source-preserving FSA master. These are source-level contradictory identities, rather than a generic rule that different U.S. states imply different institutions.

## Domestic state and name differences

The independent screen found **359 institution-eligible FSA-year rows across113 OPEIDs** with different nonblank domestic/territorial FSA and HD state codes. Under the implementation's `school_compare_key` normalization and `SequenceMatcher` ratio, **91 rows across39 OPEIDs** also have a name similarity below0.5. The grouped evidence is in [reviewed_state_name_disagreements.csv](reviewed_state_name_disagreements.csv), with input hashes and exact screening limits in [its manifest](reviewed_state_name_disagreements_manifest.json). `years` counts observations; the first/last interval is not necessarily consecutive.

Those discrepancies are retained as review diagnostics. The screen did not establish any additional unequivocally incorrect identity. It includes FSA main-office addresses paired with IPEDS branches, institutional moves, rebranding, historical names, and differing update vintages. For example, FSA DeVry branch rows can use an Illinois address while IPEDS identifies a branch in another state; Kaplan/Purdue and Milan/Academy of Court Reporting names also reflect institutional changes. These patterns need source-specific interpretation rather than automatic rejection.

Even recent official names can lag other institutional descriptors: the [NCES 2023 reported-data page for UNITID154022](https://nces.ed.gov/ipeds/reported-data/html/154022?surveyNumber=1&viewMode=iframe&year=2023) identifies Ashford University, while the FSA source uses University of Arizona Global Campus. Accordingly, the numerical name comparison is a review aid, not a crosswalk or a proof of error.

The bridge exposes candidate UNITID/name/state before descriptor review, explicit foreign/domestic contradiction, domestic-state disagreement, normalized name similarity, and the low-similarity flag. Assignment and source dollars remain separate. Ordinary state/name differences do not silently replace the official annual relationship.

## Noninstitutional source placeholder

The unfiltered FSA source includes `88888800`, AY2009–2010, named `DEFAULT SCHOOL FOR CONSOLIDATED LOANS`, stateNR. Root verified FFEL source row2604 with subsidized11,393 and unsubsidized10,667. It is an accounting placeholder rather than a school. Its amounts remain in the source-preserving master; an explicit source-kind field and resolution status prevent institutional assignment even if a future directory were to contain a coincidental numeric match. This distinguishes source reporting-ID counts from actual institution counts.
