# Explicit-policy content disposition

## Authorization and production state

This engine is disabled by default (`SJIA_CONTENT_DISPOSITION_ENABLED=False`). No migration installs a policy, retention period, approval, hold clearance, or enabled runtime. The real retention-policy question remains unanswered. Existing file-only disposition routes, object keys and deletion receipts remain valid.

A live action needs all of the following, checked again at execution:

1. An enabled environment, active authenticated support principal and current `manage_disposition` role.
2. A whole-patient `read_case` grant for the policy's explicit purpose.
3. A typed policy with an explicit mode, retention clock/period, registry and preservation fingerprints, allowed adapters, finite receipt/metadata/recheck dates and explicit local/copy requirements. Its approved SourceRevision attests policy ID/version/environment, rules digest, approver/time and execution enablement.
4. A current patient-bound hold attestation with exact review identity/version/state/reviewer/time/expiry. Missing, held, expired, tied or superseded clearances refuse execution.
5. A previewed closure of exact objects/versions, a separate approved SourceRevision matching that manifest, and current approval-role/case authority.
6. Both runtime and policy execution switches. Production policy selection and any required real irreversible-action confirmation remain an operational handoff, not an installation side effect.

Policy JSON cannot supply SQL, model columns, JSON paths or an alternative erase implementation. Clinical payload is set to SQL NULL only in the explicit erased shape; no invented zero, unknown, clinical reason or observation is substituted.

## Actual adapters

`backend/retention/payload_registry.json` is the versioned, field-exhaustive registry for 72 models across records, medical time, recognition, drafts, verification, assessments, summaries, alerts, notifications, archives, requests, tasks and case details. Each field is classified as payload or preserved relation/provenance metadata. A newly added unclassified field makes the adapter fail closed.

The database adapter erases all declared payload fields while preserving exact relationship skeletons, original object IDs and operational provenance. The controlled local-object adapter uses the existing checksum/size/path-aware deletion implementation and real local receipts. It rejects shared path aliases across all patients, unselected attachments and shared canonical versions. The complete registered path inventory is checked through the existing safe path resolver before erasure and again before local I/O; noncanonical or symlink entries fail closed without reading file bodies. A SQL write fence rejects noncanonical new object keys and prevents reusing a disposed or access-revoked object path under a new FileObject identity.

Closure includes declared JSON snapshots, task input/result/evidence, outbox copies, source anchors, original/derived files and dependency graph dependents. This includes clinical confidence-interval bindings in EvaluationRun input references and TaskAttempt clinical-adoption evidence. Shared, unpartitioned evaluation outputs refuse erasure. Global task/outbox content without a provable patient partition also refuses.

These adapters do not claim account/session erasure, erasure of shared global source/configuration evidence, arbitrary unregistered storage, physical PostgreSQL pages/WAL, or external provider logs. Registered coverage is not itself runtime proof; native tests execute synthetic canaries through each adapter and separately verify preserved neighbors.

## Concurrency, HTTP and recovery

Normal API and worker lifetimes hold the shared PostgreSQL advisory read/write barrier. Execution takes its exclusive transaction counterpart and rechecks closure and fingerprints. Native triggers reject late writes and nested references to erased/revoked identities, and erased rows are immutable. Default model managers hide unavailable payloads without revealing disposition through a pre-authorization lookup. Explicit 410 responses occur only after retained identity metadata has authorized the request.

The independent hash-linked journal is fsynced before SQL erasure. SQL payload erasure and consumer fences commit before local unlink. The execute service refuses an outer transaction; its HTTP audit uses separate committed admission and outcome records. Failure of outcome auditing can make the response uncertain, but cannot roll SQL fences back after unlink. Exact-plan lookup/resume is supported from the screen after reload or an uncertain response.

Restore performs actual replay from the journal outside the restored snapshot, verifies sequence/watermark/checkpoint integrity, and blocks runtime admission until replay and verification succeed. A restored database stores a durable admission marker, so omitting a mount does not waive the gate. Restored object inodes use restore-specific operation IDs; original file paths and receipts are preserved. See `deploy/content-disposition-recovery.md` and `tests/operations/t26_native_restore.py` for the synthetic native pg_dump/pg_restore exercise.

## Retention and outstanding copy obligations

Family users can submit only exact, currently whole-readable record versions within their own current request permissions and relation. Current versions may use whole-resource read_raw or read_history authority; historical versions require read_history. This boundary is re-evaluated during later handling, so a version that becomes historical is not processed under a stale current-only permission. The application is immutable, idempotent and distinct from execution authority. Staff explicitly link the same policy and exact source roots to a plan; the resulting manifest binds the family request ID and separately exposes expanded internal mirrors for staff approval. A later correction is never silently included. Family rereads return only their own selected versions and limited request status, including after erasure. Existing file-only family applications keep their separate semantics.

The support screen queries authorized identities, valid approved policies, hold review state and exact target/version metadata. Nothing is selected by default. Preview, manifest approval, execution and receipt recovery remain separate actions.

History provides a due-review queue with original policy evidence, explicit receipt/metadata retention floors, next recheck and each unresolved copy obligation's reason and review deadline. `ContentDispositionReview` records a separately sourced, versioned review with exact plan/policy/actor/time/reason/next-review bindings. It never silently extends a policy or runs erasure. Passing a metadata or receipt date marks review due; it does not authorize destroying anti-resurrection identities before a proven backup/retry window. There is no automatic audit/receipt/tombstone purge.

Current receipts can attest current-row logical payload erasure and controlled local path absence. They always state `all_copies_erased=false`. Physical database/WAL copies and backup copies remain obligations. Recognition-related manifests also retain an external-provider obligation. Review deadlines are not provider deletion deadlines or deletion proof.

The selected actual recognition adapter calls Ark Chat Completions. Its completion/request IDs are not established as deletable stored Responses objects. No DELETE endpoint, credentials, provider request or external deletion receipt is invented. Missing input is the selected provider's authorized deletion/retention contract for this specific request data, including logs and backups, with verifiable object scope and receipt semantics. A Responses deletion API by itself cannot establish that contract for existing Chat Completions requests. Local approved backup-artifact expiry is a separate supported adapter; no cloud backup-provider contract is selected or presumed.

## Verification boundaries

All new destructive tests use generated synthetic rows and temporary local objects. Native database runs are performed only by the central isolated test runner; no existing demonstration or user database is read or modified. The full current-source regression and native stale-backup restore must be reported separately from earlier snapshots and from nonvisual DOM checks. No blanket T26 completion or physical all-copy-erasure claim follows from passing logical adapter tests.
