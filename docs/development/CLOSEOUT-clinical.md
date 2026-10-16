# Clinical workflow closeout

Date: 2026-10-11. Scope: T18/T20–T24/T49–T52 bounded implementation fixes. No clinical approval, production data mutation, real model/channel calls, screenshots or browser operations.

## Source and priority

- P1: FR-08/AC-08 and backend-design/protocol.schema.json CorrectionRequestInput/CorrectionResolution. A configuration administrator must not resolve professional corrections without patient/field authority and an actual saved resolution. Creation must bind the caller-specified current record version and exact fields.
- P1: FR-06/07/12, AC-DYN-04, BR-D08/D09/D10. Current event validity is separate from the newest evaluation. Unknown or repeated met without a new event cannot hide a still-valid open-cycle event. Replay task activity cannot change current home processing state.
- P1: alert-design/policy.schema.json PublicationPolicy.valid_for_seconds and current release/epoch conditions. Events expire at their exact creation instant plus their approved lifetime; equality is expired. Missing/invalid lifetime, replay, closed/invalid cycle, mismatched episode/event, stale epochs and disabled release fail closed. No interval is invented.
- P2: BR-C00 per-condition output must be met/not_met/unknown rather than the knowledge label known.

## Dependencies and bounded implementation

Use current source schemas and existing authorization/retention/audit services. Preserve all pre-existing uncommitted fixes. Do not change patient clinical records when resolving a request: resolved refers to a separately saved current correction of the original record; rejected requires a reason and no resolution record. Professional resolution requires the verify_field service role plus exact patient/record/field grants under the original source use purpose. Resolution is version-checked, terminal retries only return the identical recorded result, and every successful state mutation is audited in the same transaction.

A shared event predicate will enforce the exact policy snapshot, current evaluation and open episode. Homepage selection independently examines eligible events while retaining the latest assessment status. Configured priority order needs an explicit direction; no severity-derived fallback or default priority is permitted. Historical events stay stored and accessible through authorized metadata; inactive clinical content is not returned as current.

## Verification specification (before edits)

New backend/tests/test_clinical_closeout.py must cover:

1. Classification true, false and unknown premises preserve accurate BR-C00 evidence; existing bounded-value regression remains unchanged.
2. Correction input: exact UUID version, non-empty reason, non-empty unique belonging fields, stale version and foreign fields rejected; duplicate identical request does not create another request.
3. Correction resolution: ordinary manage_config denied, missing/wrong field grant denied, expected-version conflict denied, missing/cross-record/non-current/unchanged resolution denied; actual saved correction and rejected decision persist exact reason/version and audit, identical retry does not mutate twice.
4. Event validity: just-before expiry allowed, exact expiry denied, no lifetime rejected, replay denied, epoch mismatch rejected, closed/invalid/superseded episode event rejected, retained open-cycle event allowed despite newer unknown or continued met.
5. Home: prior eligible event coexists with newer insufficient assessment; replay tasks ignored; event selection uses only explicit approved ordering; disabled/stale/expired events excluded.
6. Family current content and notification consumption use the shared fail-closed event predicate. Persisted acceptance and delivery history are not deleted.

DB tests are executed centrally by the lead in an isolated synthetic test database. Local pure/compile checks may run without DB. No historical report is counted as a new pass. Clinical, real-channel and real-device acceptance remain external and NOT_RUN.

## Retention route review

The canonical retention routes already use api/views_sources.py and sources/operations.py. RetentionInput requires explicit evidence and retention-1 rules use the existing strict RetentionRules model. Controlled intent preparation/submission checks service rights, current evidence, exact payload and target; registration creates draft, and the explicit StateAction performs activation with approved_by/approved_at and an audit receipt. Old direct handlers in views_support.py were not routed, but are forwarded to the same controlled operation so no import path silently activates arbitrary rules. No parallel lifecycle or clinical retention defaults were introduced.

T26 still lacks disposition executors for database records/versions, extracted candidates, audit objects and backups; provider-copy deletion is simulation-only. This is an implementation scope gap requiring exact object-specific policies, not an external-only checkbox.

Priority ordering remains deliberately undefined for unequal configured integers. Current release qualification permits one rule component. Equal-priority ties follow the documented stable rule_key order and newest event within that rule. An ambiguous multi-priority set returns no main event with alert_state=invalid; no clinical ordering is guessed.

## Verification evidence

2026-10-11: AST parse of all eight touched/new Python modules succeeded. Scoped git diff --check succeeded. A no-Django/no-database unittest harness loaded the actual classification module, the current_event_reason/select_current_event function ASTs and the unchanged ClassificationEvidenceTest/EventValidityTest test classes: 10 tests passed. This verifies only the pure predicates/classification evidence, not ORM integration, permissions, current release qualification or notification sends. The full tests.test_clinical_closeout (37 methods) remains assigned to the lead's isolated test database run.

Lead verification: all 37 methods in tests.test_clinical_closeout passed in the isolated target-round2 execution (.qa-work/closeout/target-round2.log; confirmed by lead 2026-10-11). The combined run had unrelated failures, so this is a focused-suite pass, not an aggregate/full-suite pass. Correction audit outcome uses the allowed committed state with CORRECTION_RESOLVED/REJECTED reason code; pure source/schema checks remain distinct from clinical approval.

Remaining known scope: BR-D08 multi-rule concurrent publication cannot be claimed because the current release contract requires exactly one rule component. T24 professional correction-request persistence is secured, but the full catalog WriteIntent/WriteReceipt contract and professional review queue/UI remain separate missing work. No ticket is marked clinically accepted by these software tests.

## T24 controlled correction review closeout (planned before implementation)

Priority: complete the existing intent-required correction request/create and resolve contract, then expose a scoped professional queue and family request entry. Dependencies are the existing `CorrectionRequestInput`, `CorrectionResolution`, `WriteIntent`, `WriteReceipt`, case grants and professional correction service; no new medical decision or approval is generated.

Prepare, submission, query and retry must bind the exact operation/route/body/actor/patient/field/version and current grants, source authorization, retention and environment. A committed retry returns the original receipt only after current access checks; request identity or payload changes fail closed. Unknown network outcomes are recoverable by original request identity and cannot silently create another write. A professional queue exposes only individually authorized requests. Resolution may reference only an actually saved, current correction directly replacing the requested version, or explicitly reject with a reason. It never edits professional facts itself.

Tests: header/digest/generation/route mismatch, stale version, revoked relation/source/service/case scope, exact commit audit/receipt, lost response lookup, expired-intent retry with new generation and no duplicate effect, professional queue filtering, and UI unknown-result recovery. Database tests remain centralized with the lead; no real records or approvals are changed.

## BR-D08 additive publication groups (planned before implementation)

Preserve the legacy release with exactly one component of each required kind. Add optional, explicit per-rule groups binding rule/window/verification/mapping/content/independent-validation config IDs. A grouped release lists the exact union of those components plus one shared visibility and notification configuration; every rule must explicitly reference those shared configurations and the same release contact. A legacy validation reference must identify one of the declared group validations; it does not replace any other group's independent validation.

`publication.priority_order` is optional with `unconfigured`, `higher_first`, or `lower_first`; absence remains unconfigured. Multi-rule activation requires every approved rule to declare the same explicit direction, unique rule identities/keys, complete bindings, and independent qualification for every group. No priority number, direction, medical threshold, lifetime or clinical evidence is assigned by this implementation. Selection follows declared direction, then stable rule key, then newest same-rule event. Historical event storage is unchanged.

Lock order: environment gate, release, sorted group rows/configurations/approvals/validations before qualification and publication. Group edits are draft-only and happen atomically with component creation. No existing enabled release or real approval is modified. Tests cover legacy path compatibility, both explicit directions, missing/conflicting order, duplicate rule IDs, foreign/missing group components, per-rule validation failure, per-rule content routing, and stale/revoked current event fail-closed behavior.

### T24 implementation checks

Controlled operations now use the catalogued v1 `WriteReceipt`, with request-body digest, generation, route and current authorization revalidation. Professional queue and detail have strict response schemas and bounded signed-cursor scanning; invisible requests do not leak fields. The family and professional pages recover by original identity, lock duplicate submissions when outcomes are unknown, and only return to editing after a missing prepare is proved by lookup. Five synthetic nonvisual DOM checks passed in `guardian-h5/scripts/check-correction-closeout-dom.mjs`; TypeScript checks pass. New database tests are `tests.test_correction_requests_closeout` (14); execution is delegated to the lead.

### BR-D08 implementation checks

The additive `ReleasePublicationGroup` migration, exact union validation, draft-only group registration, independent per-group validation checks, declared priority ordering, per-group lifecycle publication binding and per-rule content selection are implemented. Legacy single-group releases omit the optional group property and retain their exact qualification path. The support editor collects each group explicitly and exposes each approved text separately; group rules must carry a common explicit ordering before activation. Database labels: `tests.test_publication_groups_closeout` (5 pure + 8 database cases) and the existing `tests.test_T21`. Projection tests use synthetic stored evaluations; they do not establish clinical/model validity or full replay adoption.

## T09 delegated support application UI

Workflow owner provides exact support-only application GET/decision routes and immutable patient binding. The added UI opens one operator-supplied application UUID, exposes no patient directory, requires a nonblank reason and explicit confirmation of the exact target for approval, and cannot send grant actions/field codes. Existing patient bindings are read-only. Unknown outcomes lock editing and are reconciled against the same application's version/status/patient/reason; an unchanged version permits only an explicit same-body retry.

Strict `SupportApplicationReview`, `SupportApplicationDecisionInput`, and `SupportApplicationDecisionReceipt` definitions validate this UI contract. Route: `/support/applications?application_id=...`; navigation is gated on `decide_relation`. `guardian-h5/scripts/check-application-review-dom.mjs` passed eight actual-page synthetic nonvisual checks: exact-ID read, no patient directory, bad UUID rejected before dispatch, no implicit grants, immutable prebound target, rejection preserving binding, lost-response recovery without repost, and no evidence read when service scope is unavailable. TypeScript passes. Backend tests and actual user acceptance remain with the workflow owner/lead.

## T04/T05 request-retention admission (planned before implementation)

Replace the generic v1 intent adapter's arbitrary first-active retention choice with exactly one approved, active `data_class=request`, `schema_version=retention-1` policy. Require current active approver, recorded past approval, meaningful evidence reference and strict existing `RetentionRules`; deletion requires an explicitly supplied interval, and no interval is inferred. Medical recovery payloads bind that policy ID. An existing request identity cannot silently move between policies.

Keep correction and professional operations on their own already-scoped policies, before the generic selector. The adapter transaction locks environment first and uses the existing shared retention-policy row lock through intent persistence, avoiding policy→environment lock inversion. Tests cover missing/unapproved/wrong-class/wrong-schema/malformed/ambiguous policies, exact recoverable payload-policy binding, changed-policy retries, and own-policy branch bypass. This change registers no real retention policy and performs no disposition.

## Latest focused verification (2026-10-11)

Lead isolated `target-round10` reran all 37 clinical closeout tests, all 15 controlled-correction tests (including the genuine two-connection lock-order regression), and all 14 publication-group tests successfully. The 98-test combined run had one unrelated async-summary cleanup error, so this is evidence for these 66 focused checks, not a full-suite pass. The clinical audit matrix now records T20/T22/T24/T50 as implemented; T26 retains its explicit unsupported disposition-target contract gaps. Independent clinical qualification and actual user/channel acceptance remain NOT_RUN.

The new request-retention admission suite has eight synthetic cases and is awaiting the centralized run. Its API adapter uses Django's RequestFactory, avoiding the repository's `requests` application shadowing the unrelated HTTP package in DRF's test helpers. Latest TypeScript and synthetic correction/application UI checks pass: five correction-recovery checks, one grouped-publication editor check and eight application-review checks.

Lead `target-round14` confirmed all eight `tests.test_request_retention_closeout` cases passed, together with four saved clinical-capture adoption and four record-receipt replay tests (16/16). This verifies the real adapter's unique approved typed policy selection, recovery-payload binding, rejection cases and own-policy branch bypass. That snapshot preceded the explicit REQUEST_RETENTION_UNAVAILABLE→503 / REQUEST_RETENTION_CHANGED→409 mapper additions and their stronger status assertions; those exact statuses still await the final centralized run. No test policy is a production approval.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
