# Core closeout: bounded implementation plan

Baseline: existing local repairs preserved. Scope is technical, synthetic-only; no database execution, model calls, patient-file reads, browser checks, clinical approvals or deployment.

## Allocated changes and dependencies

1. CORE-AUDIT-01 (T03): metadata-only API audit, successful local writes in the same transaction and refusal audit after rollback. Explicit protected-operation registry avoids putting external model or object work inside a database transaction. Missing/unqualified audit retention fails closed. Files: `backend/api/base.py`, new `backend/audit/api.py`, `backend/tests/test_core_closeout.py`. Existing synthetic API fixtures need an explicitly approved audit retention policy; no fallback to arbitrary record retention.
2. CORE-CSRF-01 (T09): rotate the CSRF secret when establishing a new authenticated session. File: `backend/identity/session.py`; regression checks old secret is replaced.
3. CORE-DRAFT-01 (T11): stable periodic autosave of latest dirty input, serialized writes and explicit continue/save-and-leave/discard choices. File: `guardian-h5/src/state/useFormDraft.tsx`; new nonvisual jsdom regression script. Preserve submission pause and existing registration repairs.
4. CORE-OCR-ENDPOINT-01 (T13): final purpose-copy proof recheck uses the validated task-frozen provider endpoint. File: `backend/recognition/jobs.py`; offline source/behavior regression.
5. CORE-IMPORT-01 (T27): explicit input admission before writes, target patient/source/use/policy checks and digest-bound ingestion identity, with no manufactured file availability. Existing case extension schema can validate domain data, but legacy projection lacks a registered top-level schema. Only implement a bounded guard with a declared supported contract; keep full legacy real-case import externally blocked until complete source/artifact proof exists. Files: `backend/case_details/case_import.py` and a small admission module/schema if necessary. Regression inputs are newly authored synthetic data.

## Outside this patch

- Lead owns history query/filter/pagination (`records/queries.py`, history handler in `views_family2.py`).
- Delivery worker owns `FactFieldsEditor` and `MedicalTimeInput`; this patch owns `useFormDraft.tsx`.
- Generic intent recovery, strict record provenance input, retention activation, professional-record creation and full task registry alignment remain identified gaps until allocated.
- Existing T27 tests read the bundled real-case projection and must not run under synthetic-only authorization.

## Verification

Run only static/type/isolated non-database checks here. Parent coordinates Django database tests using `tests.test_core_closeout` and shared existing regression labels. Record exact commands and results below after execution; never convert code inspection to runtime acceptance.

## Implemented and locally checked

- Audit now covers all protected `api.views_*` operations and the separate patient-selection handler. Public identity/bootstrap routes remain available without clinical policy. It records allowlisted route identifiers and HTTP outcome only; no body, query, credentials or free-form exception text. Audit persistence fails closed. Read-only/snapshot and external processing use short admission/outcome transactions; local writes and outcome audit are atomic. Policy preflight does not acquire a retention lock before environment/actor locks.
- Login/session establishment rotates both session key and CSRF secret.
- Draft autosave uses a stable 10-second interval reading the latest input. All four consumers share a native modal offering continue, save-and-leave, and discard. Save-and-leave awaits confirmed persistence; failure preserves input and the destination. Existing unknown-submission pause remains intact.
- Recognition final copy proof uses the validated provider endpoint. Expired unstarted OCR fails before lease admission and persists a terminal attempt and consumer receipt without a provider call.
- Case import now requires an explicit selected projection and expected SHA-256, validates the supported legacy projection plus existing medical-time/domain schemas, checks exact patient, operator scope, source/use/retention evidence, and records digest-bound ingestion identity. Same artifact replay returns the same run; changed content is rejected. Referenced originals must already be present, authorized and checksum-readable. The importer does not read a bundled manifest or create claimed-available bytes. It preserves text known/unknown and provided field medical-time links instead of forcing every text value unknown.

Verification performed at 2026-10-11 UTC:

- `python -m py_compile` on the seven changed/new Python modules: PASS.
- `npm run typecheck`: PASS on the shared tree after coordinated precision edits.
- `node scripts/check-core-closeout.mjs`: PASS, five synthetic nonvisual DOM groups.
- `npm run test:registration`: PASS, eleven existing synthetic React DOM groups. Harness now models repeated interval timers.
- `tests.test_core_closeout`: 20 synthetic tests authored; actual database runs are owned by the parent. Tests include audit failure rollback, refusal persistence, missing approval, safe metadata, snapshot/external boundary, CSRF rotation, frozen endpoint, expired OCR, and synthetic import safety/idempotency.
- No browser, screenshots, actual provider, real patient file, production data, remote write or clinical approval was used.

Remaining: original clinical and actual-device acceptance are not closed. Full18-domain synthetic roundtrip, original bytes and collision checks passed centrally; concurrent business assertions passed, and cleanup was corrected to use one exact-owned atomic transaction. Draft retention is now implemented with approved existing rules; generic intent recovery is owned by the workflow worker. Initial 22-ticket audit and subsequent closure notes are in the parent handoff artifact.

## Additional allocated closeout, 2026-10-11 01:09 UTC

- T11 draft policy: reuse the existing strict `sources.operations.RetentionRules`; require exactly one currently approved active draft policy and calculate an expiry only from its explicit finite delete period. Do not invent a retention duration. Scope is new `drafts/policy.py`, the draft_collection policy/create arguments, and synthetic tests. Intent/history owners have been notified about shared-file boundaries.
- T13 opt-in report-recognition-3: add a separately fingerprinted contract for explicit report/sample/item medical-time candidates. Existing v2 prompt/schema and default provider behavior stay unchanged. New selection must be explicit in a new qualified provider configuration; no real configuration is activated. Preserve source text and raw time, reject invented precision/timezones, display candidates without adopting them, and require a deliberate user action plus ordinary report confirmation. Add schema/contract/adoption offline regressions. Coordinate shared MedicalTimeInput ownership and avoid changing that component.


## Final scoped handoff, 2026-10-11 01:16 UTC

- Core database label: `tests.test_core_closeout` (20 tests). New optional model-time label: `tests.test_report_time_closeout` (7 no-database tests). Parent runs current immutable consolidated DB evidence.
- Synthetic18-domain schema validation and roundtrip passed; StatementEvidence schema-key bug fixed. Existing-byte checksum and cross-patient supplied-UUID rejection passed. Concurrent same-source/digest import produced one run, one replay, one record and one epoch increase; cleanup cyclic FK error fixed with a single atomic exact-owned cleanup.
- Shared writer extraction by workflow worker is `case_details/domain_import.py::import_domain_entities`; admission/audit/idempotency remains in case_import.
- Local final checks: v3 contract7 PASS; legacy model-contract18 PASS and unchanged v2 prompt/schema hashes; frontend typecheck PASS; registration React DOM12 groups PASS; report-time component/schema/adoption PASS; existing draft-closeout DOM5 groups PASS. No DB execution by this worker.
- New v3 report times are optional, strictly typed and separately fingerprinted. A new properly qualified provider version must explicitly select report-recognition-3. Queued metadata is never automatically adopted; H5 requires separate time-adoption actions and ordinary record confirmation. Existing v2 remains the default.
- Initial audit corrections are recorded explicitly in the reconciled22-ticket matrix: canonical retention was already controlled; professional routes existed; v2 recognition never promised structured timestamps. None is represented as a proven former live vulnerability.

## T01/T04 process-runtime admission plan (2026-10-11 02:00 UTC)

The final source review still found real implementation gaps, distinct from target-environment acceptance: production startup checked only base identity, readiness trusted a runtime FK without qualification, capability states were constant, and loaded_config_version was desired database state rather than process evidence (backend LLD6.1/6.3 and T01/T04).

Allocated bounded remedy: explicit production WSGI/ASGI and Celery worker/Beat admission, never AppConfig/management-command import admission. Check local DB/schema/broker, exact runtime qualification/schema/session/timeouts, supported enabled provider configuration/endpoint/declared secret availability, retention evidence, local object/evidence dependencies and required capability policies. No provider call, model execution, approval generation or capability enablement. Store an immutable process-local admitted runtime/dependency identity; desired DB identity remains separately named. Readiness rechecks qualification and reports drift/revocation without silently refreshing the admitted snapshot. Every per-operation current gate, patient, purpose and budget check remains authoritative.

Files: new config/runtime_admission.py; config WSGI/ASGI/Celery hooks and production flag; configurations/service.py reporting; observability/health.py and views.py; dedicated synthetic tests. Existing operational export/metrics/recovery code is frozen and preserved. Parent owns all DB execution and remaining query snapshots.

Runtime declaration clarification: `tasks` rows define budgets, not enable flags. Only explicit `providers[].enabled` failures block startup as enabled-provider failures. Missing local summary/assessment policy reports `not_configured`; a local handler budget alone reports `budget_configured`, never clinical readiness. Every capability still requires current actor/patient/purpose admission. Loaded identity retains original environment/gate/publication/runtime-ID keys plus admitted checksum/process/time metadata; current database desired identity is separate.

First production provisioning is an operator-only CLI path: run migrations with production settings and exact DB identity, then use Django's existing `loaddata` for externally approved bootstrap records/evidence or `shell` to call the existing controlled lifecycle services under the authorized deployment procedure. No supplied artifact, initial grant, approval or secret is created by this patch. Imported rows do not bypass startup qualification, source evidence, schema, checksum or retention checks. The serving/support API is deliberately unavailable until that initial qualified runtime exists. Management imports remain possible on an empty database; starting a production API/worker/Beat is a distinct admission step.

Runtime increment verification before central DB execution: staged17-schema WSGI/API/Celery import smoke PASS with DB connections forbidden; runtime-admission8 SimpleTestCase plus existing runtime22 tests PASS (30 total, no DB setup). Dedicated label `tests.test_runtime_admission_closeout` contains13 tests (8 pure,5 qualified synthetic DB). Original `tests.test_T28` now asserts that a bare EnvironmentGate has no loaded process identity. Its genuine DB/Redis probes remain. Central immutable targeted/full runs are pending; source frozen after this test-contract update.

### Runtime final-review regression plan (02:13 UTC)

Current authoritative runtime schema does allow optional `billing_scope_key` and `reserved_cost`; its domain validator requires both for enabled OCR/voice, and provider-1 forbids placing reservation in the provider payload. Preserve that contract, but make process admission return a stable budget rejection for missing/nonpositive/nonfinite reservation rather than relying on indexing. Check OCR/voice provider and runtime qualification for the family audience used by actual invocation. Add a schema-valid enabled OCR positive path without provider calls. Existing domain validation rejects duplicate kinds; add defensive fail-closed aggregation and both-order unit regressions anyway, with no new configuration ban or relaxation. Files remain runtime_admission.py and its dedicated tests. Parent coordinates DB runs.

Final-review increment: positive schema-valid runtime OCR entry uses the existing runtime reservation/billing fields, never provider.payload; budget absence/nonfinite/nonpositive values now reject predictably. Enabled OCR/voice requires family qualification on both runtime and provider, while notification keeps support scope. Defensive aggregation retains any enabled block across both orders of disabled/configured entries. Existing duplicate-kind domain validation is unchanged. New no-provider positive and audience regressions added: dedicated runtime label now18 tests (11 pure,7 DB). Pure runtime11 + existing closeout22 PASS (33 total); compile and whitespace checks PASS. Central runtime18 + closeout22 + T28 3 (43 total) and integrated final remain pending.

Packaging smoke correction plan (02:15 UTC): Dockerfile already invokes smoke_runtime.py with development settings because it is a schema/package import check without DB access. The DeployRuntimeTests staging harness incorrectly forced production settings, which now correctly enforces serving admission. Align only that test's explicit settings with the real Docker build command, and label the smoke result import-only. Do not add a production environment bypass or mock production admission in application code. Include DeployRuntimeTests4 in local pure and central narrow runs.

Packaging correction verification: runtime pure11 + closeout22 + deployment4 PASS (37 total, no DB setup). Staged COPY smoke imports actual packaged WSGI/API/worker modules with DB connection forbidden and rejects a missing schema. Production entrypoint refusal tests still pass. Central narrow labels now47 total: tests.test_runtime_admission_closeout18 + tests.test_closeout_runtime22 + tests.test_deploy_runtime4 + tests.test_T28 3. Latest full729 contained a packaging-smoke fixture failure and an unrelated unordered archive fixture failure; neither is recorded as integrated final acceptance.

Central round22: all47 runtime/admission/health/deployment tests PASS, including actual synthetic qualified enabled OCR, wrong runtime/provider audience, production refusal and import-only packaging. Combined targeted round was89/90 passing; the sole failure was the lead's new unrelated source-own correction fixture missing required metadata and is being rerun after fixture repair. Application source is frozen. Full assembled repeat remains pending.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
