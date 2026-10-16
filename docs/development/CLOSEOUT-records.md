# Record/history/trend and admission closeout

Scope: T10/T16 and a related source/retention admission gap identified during implementation.
No clinical conversion formula, source approval or retention duration is supplied.

- History now validates date/precision/kind/timezone queries, applies current record and field grants before grouping/pagination, preserves unknown/relative/calendar precision, uses half-open timezone intersections, and signs cursors against query, current grants and epochs. The HTTP route opens a real repeatable-read read-only snapshot before any database query.
- Trends now apply own/assigned/all grants, rather than just the first grant's field codes. Explicitly qualified mapping policies bind exact concept, source revision, raw unit and method. Ambiguous/revoked/missing mappings do not create cross-unit trends. The read path does not materialize mappings or rewrite raw numbers. DTO and UI expose exact comparison values and configuration/mapping provenance; chart links use those values and preserve gaps.
- The analyte selector uses the actual approved dictionary search rather than the disabled placeholder.
- Family record intake rejects unknown/protected top-level/field provenance keys, invalid wire types and nonfinite/noncanonical decimals. Medical time validation uses the existing authoritative schema and local registry; omitted nullable slots are checked without altering request digests. Caller-assigned IDs/instants, incompatible precision, impossible dates and unknown timezones are rejected.
- Family create/correct now requires one current typed approved retention policy with qualified revocable approval evidence and one current purpose-authorized source. An arbitrary first active row is no longer authority. Professional intake reuses the same typed retention checker with its exact selected policy.
- The disposition UI explicitly identifies the limited per-file/copy scope. A file deletion receipt is never described as deletion of database/candidate/audit/backup categories. Their missing approved disposition protocols remain an explicit T26 dependency.

## Current engineering checks

- History: 7 focused tests passed, plus genuine PostgreSQL HTTP snapshot tests.
- Trend: 7 PostgreSQL regressions passed (exact mapping/raw preservation, revocation/ambiguity/source binding, qualified values, own/assigned grants, HTTP contract).
- Trend chart projection: 5 nonvisual checks passed; actual mapping DTO JSON Schema validated.
- Intake: 9 pure schema/input regressions passed.
- Retention/source admission: 5 tests authored; final integrated result recorded in closeout report.
- Frontend typecheck passed after trend/dictionary changes. No visual browser operation performed.

All counts are intermediate slice evidence until the final assembled source is tested.

## Additional time and version guards

Changed correction medical times must satisfy MedicalTimeInput and are resolved by
the server. Only an exact unchanged projection can reuse its already-resolved
immutable time; callers cannot replace an instant or claim resolution. Correction
versions reject boolean, noninteger, nonpositive and overflowing generations before
writes. Analyte comparisons reject ambiguous, invalid, unknown and unresolved exact
times, even when their local clock values appear ordered across a repeated DST hour.
The UI and schema expose the specific time reason. Development checks:
`tests.test_record_input_closeout` plus
`tests.test_analyte_closeout.AnalyteTimeResolutionTests`: 14 passed locally without
DB setup. Integrated DB checks are recorded separately by the final run.

## Final query-transaction closeout plan (2026-10-11)

The final independent audit found that the record detail, immutable version, version
history, source resolution and POST search endpoints did not share the same pure
query isolation contract as the newer history directory. Add an explicit read-only
repeatable-read transaction before identity/grant/selection reads and use a
read-only authorization predicate (never the write-locking mutation gate). Audit
admission/outcome remain in their existing separate durable transactions. Add real
TransactionTestCase HTTP checks and a separate-connection revocation interleaving;
permission-focused TestCase fixtures may suppress only the transaction-setting
statement because their outer test transaction already read fixture data.

The source-resolution endpoint now uses the record's original author for `own`
scope, consistent with detail/history/version reads. Being a later correction
author cannot turn somebody else's record into an own-scope original source.
An explicit denied-scope regression covers this boundary.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
