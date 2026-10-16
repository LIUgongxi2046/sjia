# Core workflow development closeout

Local engineering scope: T05, T06 dispatch/file-validation integration, T09, T15 and T17, with coordinated T08 adapter validation. Existing local changes are preserved. No commit, deployment, browser/device session, real model call or real patient data was used. Database execution is serialized by the lead; this worker only runs static, SimpleTestCase and synthetic DOM checks.

## Current implementation

### T05 and T08 original-request recovery

- `backend/requests/service.py` reloads locked persisted intent state for commit/fail/retry and rejects stale generations or any unresolved receipt.
- `backend/requests/record_intents.py` validates exact family target, patient, actor, field scopes and canonical request identity. It keeps the frozen payload on the server and reauthorizes every recovery read/retry. Expiration renews the same key only after proving no receipt exists. Create receipts always point to version 1; correction receipts point to the exact saved version, even after later corrections.
- `api/views_family.py` now requires/binds/commits intents for observation, report and therapy corrections, with therapy-only confirmation/configuration hooks preserved. Create/correct recheck locked active patient state. Correction rechecks active record state. Lead-owned typed source and retention admission remains intact.
- `api/views_family2.py` provides bounded pending recovery lists filtered by patient, operation and kind or exact correction record. Request-read admission follows the write gate prefix to avoid shared-to-exclusive lock inversion.
- `guardian-h5/src/api/recordSubmission.ts`, `state/useRecordSubmission.ts` and `components/RecordRecoveryPanel.tsx` preserve one frozen request across clicks, loss and reload. URLs store only actor/patient/intent identifiers, never clinical payload. Identity change aborts in-memory work. `CorrectionPage.tsx` now uses the same protocol for all supported family kinds and validates exact receipt identity/version before adoption.
- T08's separate active adapter-response validation was completed by the dedicated response-contract worker (12 new groups and 38 existing affected groups passed); generic fetch envelope/error/cancellation validation and typed authentication/configuration/draft/create-receipt adapters are integrated.

### T06 executable handlers

- `tasks/registry.py`, dispatcher and consumer reject unknown events instead of silently acknowledging them. `WorkflowContractTests` verifies refusal before broker access and worker success.
- The root worker now owns asynchronous summary in `summaries/jobs.py` and the corresponding task/receipt polling integration. It is no longer correctly described as synchronous-only.
- The explicit file-validation contract (`implementation-tasks.md:285`, upload-complete catalog row) is now implemented in `files/validation.py`. FileGroupComplete freezes the exact group version and ordered file IDs; qualified runtime supplies queue/execution/lease/heartbeat/retry/concurrency budgets. Admission commits TaskView202 and an Outbox event, without declaring the group available. The local worker verifies original bytes and decoded pages, then rechecks exact source/authorization/grant/runtime/parser/part/page identities under locks before adopting availability. Cancellation, deadlines, revoked grants, changed inputs, checksums and consumer deduplication are tested.
- `tasks/service.py` allows file_validation only with its complete current qualified context, while unknown task/event refusal remains intact. `api/views_family3.py` uploads/revisions now follow the same environment→actor→patient→group lock order as validation to prevent inverse-lock races. Existing low-level synchronous file service remains an internal unit primitive; the reachable completion endpoint is asynchronous.
- Failure callbacks are fenced by the captured lease generation, including reuse of the same worker owner. Unfenced generic failures cannot overwrite an active attempt. Task execution also binds the frozen input digest and group target to the original committed receipt, preventing rehashed input redirection.
- `guardian-h5/src/api/fileValidation.ts` validates TaskView, selected patient, exact group/task/request identity and nondecreasing status version while polling; original group ID is the stable completion request key across response loss/reload. Archive, photo and confirmed-text upload callers wait for completion and support abort. Failed groups require an explicit new upload action, never a silent fresh business key. `backend-design/file-validation.schema.json` validates original-request recovery.
- Root reconciliation owner supplied `tasks/reconciliation.py`; this worker merged reconcile.requested registry, dispatch/deferred/failure branches and its local-task lease classification, preserving summary and trace handling.

### T09 explicit association decisions

- `relations/inputs.py` requires decision, expected version and meaningful reason, rejects unknown actions/duplicate fields and disallows grant/patient changes on rejection. No decision, version or permission defaults are invented.
- `relations/service.py` locks the application, preserves a prebound patient, requires an explicitly selected active patient when approving an unbound application, and persists the decision reason. Relation creation does not implicitly grant access.
- `api/views_support.py` adds exact-ID `application_review` under current support `decide_relation` service authorization and makes decision authorization and versioned mutation atomic. There is no patient directory.
- Clinical UI owner supplied `/support/applications`, explicit operator-selected patient confirmation, no grant controls, required reason, immutable original decision during unknown-result recovery, and strict SupportApplicationReview/Input/Receipt schemas. Repeated approval cannot create a second relation; stale pending version cannot decide.

### T15 both audio modes

- `VoiceEntry.tsx` loads approved source options for both structured and nonstructured modes. Recording/upload requires explicit approved audio source. Missing admission exits transcribing with an error; successful review/apply releases busy state. Existing manual text behavior is retained.

### T17 professional create/correct and all 18 case domains

- Original create/correct routes did exist under `/api` and `/api/v1`; the actual gap was their legacy global-role/first-authorization implementation. `records/professional.py` now consumes the existing ClinicalDataset 1.0/1.1 and ProfessionalCorrection contracts, with explicit source revision, use authorization, current `write_professional` patient/field scope, approved typed retention and exact originals.
- `write_professional` is a case permission, never a global configuration-role side effect. Support session navigation and `ProfessionalRecordPage.tsx` expose reviewed create/correct actions and server-held original-request recovery.
- Client IDs are remapped to new server identities. Existing source anchors/attachments remain bound. Input cannot assert verification, confirmation, resolution or quality authority. New versions remain pending professional verification.
- `case_details/domain_import.py:import_domain_entities(*, case, patient, source_revision, original_files, now, existing_report_ids=())` is the bounded shared extraction from the importer. `case_import.py` retains its existing admission/idempotency/audit logic. Professional imports use this same writer for all 18 domain tables; image originals, statement evidence and report source binding remain intact.
- All professional entrypoints lock the stable environment→actor prefix before reading mutable intent status. Concurrent identical correction submissions therefore see the committed original receipt after waiting instead of incorrectly failing against a newer version. Resolved exchange times are checked, then the server re-derives the instant through the shared input writer.

## Verification evidence

Passed locally on the current implementation:

- `tests.test_workflow_closeout.WorkflowContractTests`: 4 SimpleTestCase tests, no database setup.
- `guardian-h5/scripts/check-registration-dom.mjs`: 12 actual registration DOM groups, including frozen retry, identity switch, TTL recovery and explicit v3 time adoption.
- `guardian-h5/scripts/check-workflow-closeout-dom.mjs`: 5 recovery/audio groups.
- `guardian-h5/scripts/check-family-correction-recovery-dom.mjs`: 7 checks across observation/report/therapy, loss before or after commit, later-version receipt recovery, and malformed-receipt rejection.
- `guardian-h5/scripts/check-file-validation-closeout.mjs`: 7 task-polling checks (exact result, same-key lost acceptance, wrong group, revocation, abort, selected-patient binding and nondecreasing status version).
- `npm run typecheck`, changed Python compilation and `git diff --check` passed.
- Clinical owner reports `check-application-review-dom.mjs`: 8 actual-page checks passed.

Lead-owned serialized database evidence:

- `.qa-work/closeout/full-round2.log`: all 20 `tests.test_workflow_closeout` and all 8 `tests.test_professional_closeout` checks passed. That assembled run still had five outdated integration fixture failures and one unrelated LiveHTTP cleanup error; it was not an overall passing run.
- `.qa-work/closeout/target-round20.log`: all 34 selected checks passed, including all 11 `FileValidationTests`, its separate error-contract check, the corrected family correction/request integration flows, draft consumption/rollback assertions and read-only snapshot checks.
- `RecordRecoveryTests` (6), `RecordReceiptReplayTests` (4), `ApplicationDecisionTests` (3) and `ApplicationReviewHTTPTests` (3) cover persisted retry state, current authorization, immutable creation/correction receipts, active patient state, exact patient binding and stale/repeated decisions.
- `ProfessionalRecordTests` (6), `ProfessionalResolvedCorrectionTests` (1) and `ProfessionalConcurrencyTests` (1) cover all 18 imported domains, strict source/retention admission, receipt replay, resolved timestamp handling and two actual database connections competing at the stable environment lock boundary. Thread connections close in `finally`.
- `FileValidationTests` (11) cover reachable HTTP admission/polling, deduplicated worker adoption, cancellation, revocation, changed source metadata, wrong bytes, queue deadlines, qualified concurrency budget, stale same-owner lease-generation failure callbacks and original-receipt-bound rehash rejection.
- Legacy integration fixtures now call real frozen-intent preparation/submission. Existing 201/409 and record/draft rollback assertions remain intact. No production protocol was relaxed for those fixtures.
- `.qa-work/closeout/frontend-final.log`: the complete assembled frontend script loop, typecheck and production build passed. Build output retains non-fatal chunk-size/dynamic-import warnings.

Source and tests are frozen. The lead is running the final assembled backend regression; its result supersedes these targeted results for overall engineering acceptance.

Engineering checks do not imply clinical/provider qualification or production/device acceptance.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
