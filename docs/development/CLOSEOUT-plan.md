# PRD 1.0 / 2.0 local completion reconciliation

Baseline: `4767c6b50f3582738287a202c45a0b51ebf823a9`; audit started 2026-10-11 UTC.
All earlier local repairs remain in place. No commit, push, deployment, external
model invocation or real patient-data processing is part of this execution.

## Sources and scope

- The current PRD is V2.0.0 and explicitly retains FR-01–FR-12 and original
  acceptance IDs, adding FR-13–FR-22. Full reachable Git history has no distinct
  V1 PRD snapshot. “V1 coverage” therefore means the preserved original scope,
  not a comparison against an unavailable original document.
- `implementation-tasks.md`, `planning/delivery-ledger.json`, and the T01–T60
  development records are the ticket inventory. QA01–QA04 are also reviewed.
- The ledger records 60 IMPLEMENTED tasks but 49 NOT_RUN, 8 STALE and 3 historical
  PASS acceptances. Its 47 receipt references are unavailable in the tracked
  repository. Historical statuses are not fresh verification.
- Code completeness, reproducible synthetic engineering tests, and actual
  clinical/provider/device/production acceptance are separate conclusions.

## Execution order and ownership

| Slice | Priority | Owned scope | Dependencies / completion evidence |
| --- | --- | --- | --- |
| Core admission, audit, sessions, drafts | P1 | API audit wrapper, audit service, identity sessions, draft hook, import admission, frozen recognition endpoint | Current auth contracts; denied/rollback/PII-safe audit and synthetic API/DOM tests |
| Clinical lifecycle and correction | P1 | Classification premise evidence, correction requests, family-home/current-event validity | Existing approved-policy semantics only; revoked/stale/expired/new-insufficient regression tests |
| Archive/time semantics | P1 | Delta same-sampling qualification, fact time editor | Current verification predicates; precision/zone preservation, explicit common-time selection tests |
| History directory | P1 | records/history.py, queries wrapper, history API, protocol compatibility | Field-scoped access first; date/timezone, precision, sources, signed cursor, invalidation tests |
| Runtime packaging/observability | P1 | deploy, observability | Existing build/worker contracts; no certificate generation/security setting changes; offline tests and local HTTP |
| Deterministic evaluation | P1 | case_details evaluation execution / pure metrics | Independent eligible labels, fixed versions, approved protocol and denominator/split rules; unknown stays unknown |
| Test portability | P1 | historical automated test fixtures and current-contract tests | Classify all 83 failing IDs first; no weakening production gates or invented historical evidence |
| Evidence reconciliation | P2 | current ticket/requirement matrix, run report, portable patch | Every T01–T60 represented; final assembled tests, explicit external blockers and limitations |

Parallel edits are limited to the above owned files. Shared schema changes are
coordinated before editing. PostgreSQL/Redis runs are serialized by the local
runtime launcher. There is no browser/visual execution in this round.

## Verification and rollback

Each slice first documents its requirements, concrete gaps and regression cases.
New tests use isolated synthetic fixtures. Retired APIs are tested for intentional
rejection, not restored to bypass current controls. Missing real-case data remains
an explicitly external acceptance dependency. No production approval is created.

Run narrow regressions before the full Django suite, frontend DOM tests/typecheck
and build, offline model/evaluation tests, schema checks, and same-session HTTP
checks. Record PASS / FAIL / SKIP / NOT_RUN separately. Final source and tests are
delivered as an apply-checked patch; rollback is removal of that local patch, with
no irreversible migration or automatic deletion of user data.

## T16 approved unit comparison closeout

Add read-only mapping projection from the unique currently qualified mapping policy,
with exact concept, source revision, unit and method binding. Preserve all raw values;
expose mapping IDs/checksum and both comparison values. Missing/revoked/ambiguous
mappings, unknown methods, qualified values and incomparable times remain blocked.
No default conversion formula or clinical mapping is supplied. Trend reads apply
all current record/field grants in one repeatable-read snapshot. Tests cover exact
conversion, ambiguity/revocation, source binding, raw preservation and grant scope.

## Final development-only closeout boundary

The current user instruction makes feature implementation and engineering
self-tests the stopping condition. Joint comparison with the two original official
documents, clinical/protocol approval, real devices and actual deployment are the
next acceptance phase. They are not treated as missing feature code.

The final implementation pass includes real queued summary execution, registered
file-validation execution, metadata reconciliation, current legacy-response
validation, typed request-payload retention, and actual loopback HTTP development
checks. No unsupported task handler may silently report success.

The remaining indispensable T26 design choice is distinct from acceptance:
immutable database values and their native historical composite references need an
explicit purge-versus-tombstone/preservation contract. Existing byte executors and
the new authorized read-only coverage/dependency manifest do not implement or claim
whole-database/provider/backup erasure. Missing deletion semantics are not filled
with guessed retention periods, automatic cascading deletes or a facade that only
returns “unregistered”.
