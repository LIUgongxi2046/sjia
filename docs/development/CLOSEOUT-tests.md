# Default-suite engineering closeout

Date: 2026-10-11. Scope: offline synthetic software tests, not clinical acceptance.

## Starting evidence

Saved repaired full-suite result: 354 tests, 83 failed IDs. Independent source/log classification:
- 66 obsolete request, fixture or endpoint contracts
- 13 missing explicitly synthetic image fixtures
- 3 tests requiring an absent real clinical projection
- 1 `TestCase` transaction incompatible with a `REPEATABLE READ` endpoint

All 83 IDs also failed at baseline. This does not prove no regression. Fixing their first blockers can uncover additional defects; every rerun must preserve and explain the remaining failures.

## Plan before edits

1. Add reusable test-only synthetic input/evidence helpers. Refuse non-test databases, use temporary evidence/object/model-identity files, compute real checksums, and keep clinical validation false. Never change application qualification or provider gates to accommodate fixtures.
2. Replace the ignored synthetic PNG dependency with deterministic in-memory bytes. Keep the original recognition state/idempotency assertions where the current workflow supports them.
3. Separate the three original real-case tests into an explicitly opt-in clinical-input category. Missing input is a named skip/blocker, never a development pass. Add a distinct small synthetic import test if its scope can be covered without reading any original case data.
4. Modernize record, application, dictionary, correction and voice requests to current schemas and identities. Keep positive behavior assertions and add explicit missing-authorization/config rejections.
5. Cover snapshot HTTP isolation using `TransactionTestCase` with test-owned cleanup. Do not weaken endpoint transaction isolation.
6. For deliberately retired v1 assessment/evaluation/notification APIs, rename tests to describe current refusal behavior. This is a change of coverage scope, not equivalent proof of the old positive path. Add current v2 synthetic positive coverage where feasible and report any gap explicitly.
7. Modernize typed configuration, release, summary and disposition fixtures only within their intended contract. Never fake production approval, external provider delivery, independent clinical evidence, or actual object destruction.
8. Have the coordinating process run targeted labels and the full suite in its isolated PostgreSQL wrapper. Record commands, results, skips, new defects and still-unverified positive workflows below.

## Execution status

Plan recorded before source/test edits. Classification artifact is held with the audit deliverables. Database execution remains coordinated by the lead. The lead subsequently allocated the bounded T59 runtime bridge; its source changes and verification are documented in CLOSEOUT-evaluation.md.

## Implemented coverage and explicit limits

- Generated images and all evidence files are synthetic, local and temporary. The helper rejects non-test databases, validates actual config schemas/checksums, binds the real current implementation artifact, and exercises qualification. Its evidence records describe software tests only; no independent clinical validation is claimed.
- Record/application/configuration/contact/source/retention/dictionary/correction requests use the current audience, intent, version and field contracts. Read-back assertions remain, including source identity, exact values and immutable snapshot checks.
- Field-config/history/summary snapshot HTTP reads use real `TransactionTestCase` transactions. History includes a permitted and excluded field and observes `SET TRANSACTION ... REPEATABLE READ`. Older permission-only history tests mock only that statement's helper and make no isolation claim.
- Recognition state-machine tests retain expiry, idempotency and ownership assertions with explicit synthetic admission/purpose-copy injection. A separate unpatched admission test proves an unconfigured real provider stays blocked. This is not real OCR/model-governance positive acceptance.
- T21 publication and T25/QA02 summary/alert projection fixtures use real typed qualified synthetic configurations. The source data remain software-only.
- T26 deletes a real temporary synthetic object and verifies the actual receipt/checksum and absence. Its old external-provider status-positive was replaced by a current scope-rejection assertion. External provider destruction remains NOT_RUN.
- Retired v1 assessment, evaluation, verification and unsigned notification APIs are explicitly tested as current refusals. These renamed tests are not equivalent positive coverage. Current native notification permit, acceptance, signed receipt and delivery cryptography are additionally tested in `test_native_notification_protocol`, with explicitly injected pre-admitted scope. The separate real dynamic-worker → qualified publication → signed native acceptance → owner HTTP delivery integration passes both tests on the immutable round7 snapshot. No qualification or publication function is mocked.
- T27 retains its original real-case assertions as external evidence expectations. Default runs skip all three. Even explicit opt-in does not read data without a separately qualified authorized runner. Missing projection and qualified admission remain external blockers, not passes. The core closeout suite supplies distinct synthetic multi-domain importer coverage.

## Coordinated diagnostic evidence

All PostgreSQL execution belongs to the coordinating runner; this worker performs only static compilation and log inspection.

- `.qa-work/closeout/full-round1.log`: 473 tests, 1 failure, 9 errors, 3 external-evidence skips. Eight errors were already-loaded obsolete T23 methods while test modernization continued; one was a correction-request test expecting an embedded request instead of the creation receipt (fixed to GET/read back the created request). The failure occurred during fixture runtime qualification before the expired-draft scenario, while application code changed concurrently. Qualification binds the complete implementation artifact; a frozen rerun is required. Diagnostic assertion now reports registered/current artifact hashes without bypassing the guard.
- Original first-blocker classification remains in the separate 83-ID audit artifact. Subsequent fixes exposed real SQL lock alias and recognition deadline state bugs, reported to the application owner; passing fixture modernization must not erase that history.

Immutable round7 verifies the two full dynamic-to-native tests and all four actual snapshot HTTP tests. A final immutable full-suite result is still pending. Native local delivery establishes software behavior; external providers, actual clinical evidence and device acceptance remain separate.

## Record-read snapshot fixture boundary

The final T03 closeout introduces a separate `records.snapshot.begin_record_snapshot` on record detail/version/history/search/source HTTP reads. Permission/wire tests running inside Django `TestCase` cannot change isolation after their fixture queries. `patch_permission_record_snapshot(case)` therefore patches only that SQL-entry helper, only for `TestCase`, with one cleanup per case. `SyntheticEvidence` invokes the adapter and audit-only TestCase fixtures opt in explicitly with `case=self`. `TransactionTestCase`, `LiveServerTestCase`, `SimpleTestCase` and absent cases retain the exact production helper. Two guard regressions in `test_snapshot_fixture_scope.py` verify that scope. Real SQL/coherent HTTP coverage remains in the lead's transaction and live-HTTP suites.

Immutable target-round20 (`.qa-work/closeout/target-round20.log`) passes all34 targeted tests, including both snapshot-fixture scope guards and the actual record detail/version/history/source/search read-only snapshot tests. Transaction instrumentation verifies PostgreSQL `repeatable read` and `transaction_read_only=on` and excludes the separately committed outcome-audit transaction by atomic-block identity; no production assertion was relaxed.

Full frozen round3 ran729 tests:724 passed,2 failures,3 external clinical-input
skips. The archive immutability assertion compared two unordered queries with
different predicates; both now explicitly order by primary key while retaining
full historical-row and field/time/anchor equality assertions. The other failure
was a package-only Docker import smoke invoking new real production admission;
its harness is being separated from actual serving startup without relaxing
production admission. A new frozen full run is required after both repairs.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
