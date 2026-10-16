# Approved patient reminder presets

Development revision: 2026-10-11. This implements the confirmed role model: professional administrators maintain/approve parameter plans; guardians select approved IDs. It does not establish clinical validity of a particular sensitivity level.

## Configuration and activation

1. Maintain a versioned `alert-trigger-2` using explicit core/general membership, positive decimal weights, full configured denominator, known-data fraction and core gate. Unknown facts remain unknown. Provide a named sensitivity preset and actual governance evidence.
2. Qualify the input mappings, verification/window policies and the complete `dynamic-schedule-2` for its patient, professional execution actor, environment and original purpose. HLH can be included as an explicitly approved optional condition, never as an automatic prerequisite for all warnings.
3. Bind each offered rule to an active approved family publication group. A single active release may carry multiple independently bound rule groups with an explicitly approved priority direction. The code does not invent their medical ranking.
4. Register/approve/validate/enable `patient-alert-presets-1`. It contains patient/environment/purpose, immutable schedule IDs, approved labels/descriptions and a required professional default. An ambiguous or ineligible directory fails closed. `family` and `support` approvals are required; references resolve through existing evidence/source/retention checks.
5. Separately grant the guardian `select_alert_preset` for the existing `family_observation` purpose, active relation and whole-patient scope. Selection itself never creates a grant, data-use authorization or medical approval.

No active preset, medical weight, threshold, missing-data policy or clinical approval is installed by this change. New source schemas are `backend-design/patient-alert-presets.schema.json`, `alert-preset-selection.schema.json` and `alert-preset-view.schema.json`.

## Actual workflow

The account entry `/alert-presets` only appears for the current verified relation's action hint. The API independently reauthorizes every read, submission and recovery request. GET `/api/patients/{id}/alert-presets` returns only approved choice labels/IDs and selection state, not editable clinical parameters.

POST `/api/patients/{id}/alert-preset-selection` uses the existing write-intent protocol with operation `alert_preset_select`, schema `1.0.0`, immutable payload hash and compare-and-swap `expected_version`. A selection creates one immutable history row, one request receipt and a metadata-only audit in the same transaction, then emits a local context-change event. Replays return the original exact receipt. Two competing writes from the same version cannot both commit.

An uncertain response retains the original request ID/body in memory. A URL recovery marker contains only the original request, actor and patient IDs; it is not authority. Reload performs a read-only original-request query. Expiration may renew the same intent generation only when no write receipt exists. Unknown outcomes never create a replacement ID. A proven uncommitted, superseded original can be discarded only through an explicit reselect action. Account/patient/authorization changes abort and hide stale local state; every recovered payload is returned only after current server authorization.

## Execution and published-content safety

Both scheduled and manually requested current evaluations are bound to the selected plan, assignment and schedule approval/validation/source hashes. The worker rechecks that binding before adoption. Historical replay remains a support workflow with publication disabled. New choices invalidate the old choice dependency version. Removed/replaced assignments and revoked selection grants require explicit reselection rather than falling back silently to a default.

Already-published family home/detail content rechecks the choice's actual grant, relation, principal epoch and time validity. The home becomes stale and old content unavailable after those prerequisites change, even for another still-authorized viewer. Native notification authorization, send and message delivery retain their live computation/content checks. Graph edges are supplementary provenance; they do not replace the live eligibility check at time boundaries.

## Development validation

`tests.test_patient_alert_presets` covers real qualified synthetic plans, actual API intent/commit/query wiring, idempotency, expiration, unknown receipts, stale choice, revoked/changed field-purpose scope, queued-task fencing, manual current-task bypass prevention and published-content revocation/expiry. `tests.test_preset_concurrency` uses two PostgreSQL connections for CAS contention. `node scripts/check-alert-presets-dom.mjs` exercises the production page, adapter and access provider with synthetic responses.

Exact final counts are recorded in the dated aggregate verification report after the assembled snapshot finishes. These are software self-tests; no real patient or live model request is used. Browser screenshots of older frozen-backend pages do not verify this newly added API until both versions are integrated.

Independent scoped code review traced a stale-published-event risk: immutable choice/task history alone did not recheck the selecting guardian's later authorization. The new live family-publication check and separate-viewer expiry/revocation tests address that risk. Review also covered directory, intent/retry/receipt, CAS commit, schedule/manual admission and worker binding; it is a scoped engineering review, not a global security certification. Native send tests exposed a strict public-DTO mismatch when internal preset proofs were emitted in the existing automatic-trigger response. The public response now projects only its established trigger identities; internal proofs remain stored and checked.
