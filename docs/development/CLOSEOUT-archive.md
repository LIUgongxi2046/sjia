# Archive closeout: evidence, contract, implementation and validation

Date: 2026-10-11 UTC. Scope: T34–T36 and T39–T48. Baseline is checkout 4767c6b plus preserved local repairs. PRD 2.0 §8.19.1–8.19.9, implementation tasks, delivery ledger, task specifications and actual code/tests were reviewed. Historical simulation receipts were not treated as current runtime proof.

## Bounded repairs implemented

- **T41:** Shared medical-time editing preserves all eight precisions, source text, IANA timezone and explicit fixed offset. Opening correction editors never mutates the source. Incomplete year/range input blocks submission. Explicit common-time application requires source evidence, per-item selection and confirmation; it cannot overwrite existing individual times. Existing registration defaults remain compatible. Correction draft validation supports the same precision/offset contract.
- **T45:** Current projection follows the exact field ordinal and code, without broadening to other same-code fields. Additive `time_basis=document|episode` defaults to document formation time. Episode mode evaluates linked encounter start/end boundaries, retains unknown ranges and binds actual source times into the cursor snapshot. The archive filter exposes this choice.
- **T46:** Same-sampling deduplication requires the existing qualified verification proof to match the relation's author, evidence and verification ID. Unqualified relations preserve both facts and produce an explicit conflict. No clinical rule or authority is invented.
- **T43:** Atomic regrouping implements split, merge, transfer, reorder and explicit unassignment within one batch. Backend, migration, strict schemas, exact request recovery, active-reader lifecycle handling and usable family UI are included.

## T43 API and transaction contract

`POST /api/v2/patients/{patient_id}/archive-document-regroupings`, operation `archive_documents_regroup`, uses the existing WriteIntent/WriteRequest protocol. `GET /api/v2/archive-batches/{batch_id}/regroup-options` lists only current source documents the caller may read and write.

Input contains one batch, 1–20 sources `{document_id, expected_version}`, 0–20 outputs with explicit type/organization/time/text/proposed metadata/episode IDs, ordered page IDs, `metadata_confirmed=true`, an explicit unassigned-page list and a nonblank reason. A source identity may be reused once. Every source page must appear exactly once in an output or the unassigned list; no outside page is admitted. Empty output sets explicitly retire the selected sources while retaining all original pages and facts.

All affected documents/pages are locked in stable ID order inside the caller's transaction, under the patient/environment/actor and batch locks. Expected versions, active page exclusivity, source bytes/checksums, purpose/retention, complete-original permission, per-field correction permission and episode evidence are checked. Grant/relation locks are followed by authorization rechecks. Each affected record is corrected once even when its fields span several selected documents. New fact versions retain raw values, original medical-time identities, context, source anchors and confirmation lineage; only selected document provenance is rebound or explicitly detached. Previous versions are never mutated or deleted.

Migration `archives.0008_document_regrouping` adds nullable retirement metadata and an operation receipt table with composite patient/batch constraints. Omitted source identities are soft-retired; existing versions and original file rows remain intact. No production migration has been run. The migration is additive and deliberately has no destructive reverse SQL.

One atomic write creates outputs, new record/fact versions, retirement markers, dependency invalidations, data epochs and the committed audit/receipt. Replays return the same original receipt, including exact original source versions, output versions/page order, retired IDs and unassigned pages. Changed payloads or stale expected versions fail without partial writes. Recovery rechecks current permissions against immutable original sources, including history permission after commit.

## Active versus historical readers

Current archive search, candidate document selection, normal grouping, current document reads and batch delta selection exclude retired documents. Document history and exact document-version source reads remain available with history authorization. Delta fingerprints include lifecycle changes; current dependencies become invalid. Assessment current provenance rejects retirement; replay before the recorded retirement instant retains the old evidence, while later cutoffs reject it. Exact frozen evaluation objects and fact/source history continue to reference immutable named versions deliberately. They are not silently rewritten into current projections. Observation periods consume corrected current facts, so detached/reassigned document provenance flows through their existing epoch checks.

The UI entry is on the actual document grouping screen. `/archives/regroup?batch=…` supports source selection, explicit per-page allocation, output addition/removal, page ordering, full-precision medical time and metadata/episode confirmation. Boundary changes clear old full-document text, proposed metadata and episode selections so the user must re-confirm the new boundary. Only opaque recovery identifiers enter the URL; clinical payloads remain in memory/server intent recovery. A lost response leads to exact request lookup, never a new request. The retry button uses only the server-authorized next generation. Hidden/pagehide state clears private content and aborts in-flight work. Receipt links open exact original and resulting document versions.

## Dependencies and ownership

T46 reuses existing field verification qualification. T43 reuses source-purpose/retention enforcement, record correction, exact source anchors and dependency invalidation. New Regroup definitions/routes are additive; the clinical worker owns v1 correction, while other workers own evaluation schema additions. No caller's existing duplicate-page guard was removed. Shared editor changes were verified against existing registration tests.

## Validation record

- PASS: 18 no-database Python tests (`tests.test_archive_closeout`, `tests.test_T47_time_values`).
- PASS: 2 no-database regroup input/partition tests (`tests.test_archive_regrouping.RegroupInputTests`).
- PASS: Django system checks and offline migration graph load; no database connection.
- PASS: TypeScript typecheck after all current archive edits.
- PASS: 9 actual React DOM groups in `guardian-h5/scripts/check-archive-closeout-dom.mjs`, including precision preservation, common-time flow, encounter filter, split editor, lost-response lookup, receipt mismatch rejection, retry generation and privacy cleanup. This is synthetic jsdom, not browser/visual/device acceptance.
- PASS: current registration DOM/regression suite (12 groups) and production frontend build (3009 modules). Existing main-chunk size warning remains; no build failure.
- Initial central run identified synthetic fixture permission/FK setup failures; those fixtures were repaired without weakening permissions. **Round8 PASS for all 16 regroup tests**, including 14 PostgreSQL TestCase regressions for immutable split, merge/retirement, shared-record correction once, reorder/unassignment, multiple episodes, active-page guard, cross-patient/batch/version/permission rejection, rollback, idempotent API/recovery, epoch/cursor/dependency invalidation and replay/historical access. The combined round had an unrelated workflow fixture failure and DB teardown session issue; it is not claimed as an overall suite pass. Include the later exact-anchor, permission-before-conflict and overflow guards in the final integrated rerun.

External acceptance is separate: real data/model quality, independent source/label approval, provider deletion results, capacity, physical-device behavior and visual review have not been run or represented as passed. Local synthetic qualification fixtures are software test prerequisites, never professional or production approval.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
