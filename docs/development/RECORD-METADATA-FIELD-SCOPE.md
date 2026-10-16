# Record metadata field-scope boundary

## Scope and API contract

The family record current read, historical-version read and version directory
must not expose a record payload when the caller cannot read any of its fields
for the requested action. A matching case-level grant alone is insufficient.

- `GET /api/records/{id}` uses the currently valid `read_raw` field scope.
- `GET /api/record-versions/{id}` uses the currently valid `read_history` field
  scope for that exact version.
- `GET /api/records/{id}/history` lists only versions containing at least one
  field in the caller's valid `read_history` scope. An empty visible directory
  is denied.
- Zero visible fields produce the existing `403 CASE_ACTION_DENIED` envelope,
  without record, version, source, correction, report, time or attachment data.
- An empty `field_codes` grant does not act as a wildcard for field reads.
  Existing whole-resource grants can coexist with explicit field grants;
  explicit grants continue to determine field visibility.

For an authorized partial read, the existing record-level metadata contract
remains unchanged: organization, correction information, report metadata and
attachment references accompany the readable fields. Unreadable fields, their
source anchors and their fact references remain excluded. Attachment references
do not grant access to file content; that endpoint has its own authorization.

A nonempty authorized history directory retains the actual `current_version`
number as record-header metadata. It does not reveal an unreadable version's
UUID or entry, label an older readable version as current, or grant direct
access to a hidden current version. The existing response schema is unchanged.

The current-record `read_history` action is advertised only when that same
directory contains a readable version under a `family_observation` grant with
an active relation. An older visible version is sufficient even if the current
version has no history-readable fields. The full-field `correct_record` check
and record-level `request_correction` behavior remain unchanged.

## Verification

Focused module: `tests.test_record_metadata_scope`.

Its synthetic records contain source anchors, report references and sample/result
times, attachment IDs, and correction metadata. Tests exercise actual HTTP
session and grant checks, response filtering, the generic denial envelope,
authorized partial reads, mixed visible/hidden history versions, empty grants,
grant narrowing, and revocation while an unrelated active grant remains.
Three additional focused methods check the history action, including
older-version-only visibility and unrelated-purpose or missing-relation grants.

The module uses Django `TestCase` and patches only the record/history transaction
bootstrap because the test harness already opened an outer transaction. It does
not mock authorization, query results, serializers, or HTTP responses. Actual
read-only repeatable-read behavior is independently covered by
`tests.test_snapshot_closeout.SnapshotHTTPTransactionTests`.

The analogous archive-fact path was inspected without modification:
`fact_access` authorizes its specific field both before and after acquiring the
fact lock; fact history also checks each returned version's field. A focused
HTTP denied/authorized control test guards the direct fact path.

All fixtures are synthetic. No external model provider, real patient data,
credentials, publication, or deployment is involved.

## Execution evidence

- Before the production change, the central runner captured the old source in
  an isolated snapshot and ran the 10-method focused module: 13 assertion or
  subtest failures across seven negative methods, each showing an actual HTTP
  200 response where denial or history filtering was required. All three
  legitimate partial-read, whole-plus-field, and archive-fact control methods
  passed. There were no fixture errors. Runtime: 0.738 seconds.
  Snapshot: `e5cfef661aabb935488372eabf4cfe140710fc94afa6cf36aca0b4953fb54df8`.
  Log: `original-metadata-reproduction.log`.
- Production and focused test Python compilation and scoped `git diff --check`
  passed after the bounded change.
- After the change, the central runner passed all 42 tests in 10.140 seconds:
  the 13 focused methods, existing record-access boundary coverage, actual
  read-only repeatable-read HTTP snapshot tests, and snapshot fixture-scope
  coverage. Modules: `tests.test_record_metadata_scope`,
  `tests.test_record_access_boundaries`, `tests.test_snapshot_closeout`,
  `tests.test_snapshot_fixture_scope`.
  Source snapshot:
  `4fd041d4b175e4cd3c63e4e390a5bcbcbeac0d1d27fd527b756e4a58ceb9034f`.
  Log: `original-metadata-fixed.log`.
