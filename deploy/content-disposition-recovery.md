# Content disposition journal, isolated replay and local backup expiry

Status: implemented engineering paths with synthetic offline checks. Live content
execution remains disabled by default. No production policy, legal retention
period, external-provider protocol, real backup, real restore or production
acceptance is supplied by this document.

## Independent trust and approved metadata

- `SJIA_CONTENT_DISPOSITION_ENABLED` defaults false. Replay of existing trusted
  entries does not depend on enabling live execution.
- `SJIA_CONTENT_JOURNAL_ROOT` and `SJIA_CONTENT_CHECKPOINT_PATH` have no defaults.
  The journal and checkpoint must be existing protected **separate directory
  trees**, outside database backups, object stores, config-evidence trees and
  restore roots. Provisioning is explicit: `ContentJournal(...).initialize()`.
  It refuses existing journal state; normal execution never invents a genesis.
- The checkpoint is the independently protected latest monotonic trust anchor.
  Do not restore it from an old database/object backup. An operator able to roll
  back both this anchor and the journal can defeat a hash chain; this local
  implementation does not claim a remote WORM/signature/transparency service.
- Each immutable sequence entry binds the approved policy, exact manifest, exact
  resource/version identities, row digests, explicit approver/grant evidence and
  previous entry digest. The manifest contract forbids clinical payload fields.
  IDs, hashes, object keys and approval references are still sensitive/linkable
  metadata and require the explicitly approved preservation scope.
- Each entry is exclusively created, flushed and fsynced; its directory is synced
  before atomic checkpoint replacement and checkpoint-directory fsync. A process
  lock serializes writers. Missing sequences, altered entries, truncated tails,
  a missing anchor and an uncheckpointed tail fail verification. Only the exact
  original envelope can finish the one-entry append/checkpoint crash window.
- Existing `files.deletion` journal/receipt contracts and evidence paths are
  preserved. The content journal does not rewrite or replace legacy proofs.

## Backup binding

The existing backup admission can explicitly include:

```json
{
  "content_journal": {
    "root": "/APPROVED-INDEPENDENT-JOURNAL",
    "checkpoint_path": "/APPROVED-SEPARATE-TRUST/checkpoint.json"
  }
}
```

These illustrative paths are not configured by this change. The backup tool
validates separation, captures a verified checkpoint after writers are frozen,
and requires that checkpoint to remain unchanged until the backup is complete.
Only the watermark is stored in `manifest.json`; the independent stores are not
copied. Legacy backups without a content checkpoint remain valid for integrity
verification and isolated restoration, but cannot receive content-replay/runtime
admission until a separately reviewed compatible recovery procedure exists.

## Real controlled replay and runtime admission

1. `restore` retains the existing exact-target, empty-database, distinct-cluster,
   stopped-writer and externally isolated requirements. It writes
   `ADOPTION_BLOCKED.json` binding a fresh restore UUID, exact database/cluster,
   object root, backup-manifest digest and backup checkpoint.
2. After actual `pg_restore`, it commits a matching singleton
   `sjia.content_restore_state` row before later reference checks. The runtime
   queries this state, so omitting the filesystem marker mount cannot disguise a
   restored database as an ordinary deployment. Missing required schema fails
   serving admission; migration/management imports do not invoke this gate.
3. With the admitted isolated settings, `replay-content` calls the actual
   `retention.content_engine.replay_manifest` and `verify_manifest` implementations.
   It verifies the configured database/cluster, the restore marker, independent
   journal, and actual `files.service.object_store.root` before callbacks. A
   fresh process avoids import-time object-store instances from another target.
4. All trusted manifests are replayed and verified, including those at/before the
   backup watermark. This handles durable intent written before its original DB
   transaction committed. There are no arbitrary callback imports, SQL strings
   or field paths supplied by policies. Partial/unknown verification does not
   create a successful proof. `CONTENT_REPLAY_VERIFIED.json` binds the latest
   trusted checkpoint and exact restore target.
5. Content replay alone does not reconcile all historical grants, disabled
   configuration or uncertain provider/notification effects. A separate
   `admit-restored-runtime` approval must name this exact target and proof digest,
   attest completed permission/revocation/configuration/effect/release reviews,
   and include an unexpired approval plus operator references. The tool re-verifies
   actual content before recording `ADOPTION_ADMITTED.json`. It starts no service
   and enables no network route.
6. API/Worker/Beat entrypoints consume admission independently of
   `REQUIRE_PROCESS_RUNTIME_ADMISSION` and the live erasure flag. API requests also
   check the DB marker and current journal watermark. A new journal entry makes a
   previous restore proof stale; it cannot silently permit restored content.
   Mount the full approved restore root and independent trust stores for every
   consumer, including Beat. `SJIA_RESTORE_ROOT` may explicitly name that root;
   the database marker also detects its omission. Root relocation requires its
   own exact reviewed procedure; it is not guessed or silently rewritten.

Operator commands are plan-only unless `--execute` is present:

```text
python deploy/recovery.py replay-content --admission APPROVAL.json --destination RESTORE_ROOT --execute
python deploy/recovery.py admit-restored-runtime --admission RELEASE_APPROVAL.json --destination RESTORE_ROOT --execute
```

The replay approval uses `operation: replay-content`, the existing isolated-restore
admission fields and exact `content_journal`. Release uses
`operation: admit-restored-runtime`, the same isolated-target fields, exact `target`
from the restore marker, `content_proof_sha256` computed over canonical proof JSON,
plus true `revocations_replayed`, `disabled_configuration_reconciled`,
`external_effects_reconciled`, `permissions_revalidated`, `release_qualified`, and
`isolated_content_validation_complete`. These are approval records, not a
substitute for obtaining authority or executing the corresponding reviews.

## Supported local backup catalog and expiry

`deploy/local_backup_catalog.py` supports only immutable local directories made
by `deploy/recovery.py`. It has actual catalog, exact expiry and absence-verification
paths; it does not choose a retention period or automatically run them.

- `catalog-local-backup` approval binds a UUID `backup_id`, exact `backup_root`
  and separate protected `catalog_root`, backup manifest SHA-256, explicit
  `policy_sha256`, explicit timezone-aware `expires_at`, approval/operator
  references, `approval_expires_at`, and `protected_catalog_confirmed: true`.
- Cataloging first verifies the complete backup, then durably stores its exact
  immutable inventory and approved expiry. It rejects changed catalog identities.
- Expiry requires a separate `expire-local-backup` approval with the same backup
  identity, exact `catalog_entry_sha256` and manifest digest,
  `irreversible_expiry_approved: true`,
  `exclusive_backup_writer_scope_confirmed: true`, and `legal_hold_state: clear`.
  Unknown/held state, missing expiry/policy, unexpired backup or scope drift reject.
- A durable expiry intent is written before any unlink. The implementation deletes
  only inventoried exact checksum/size-matching files, refuses unknown additions,
  preserves neighboring backups, supports interrupted exact retry, and writes an
  independent receipt only after the cataloged directory is absent. Concurrent
  catalog/expiry attempts are serialized. Its receipt explicitly says
  `all_physical_copies_erased: false`.

```text
python deploy/local_backup_catalog.py catalog --admission APPROVAL.json --backup EXACT_BACKUP --catalog PROTECTED_CATALOG --execute
python deploy/local_backup_catalog.py expire --admission NEW_EXPIRY_APPROVAL.json --backup EXACT_BACKUP --catalog PROTECTED_CATALOG --execute
```

## Verification and remaining boundaries

Offline tests use temporary synthetic data. They verify hash/sequence/truncation
checks, approval/manifest binding, exact append retries, concurrent sequence
allocation, real filesystem callback replay, missing/stale/partial restore proof,
separate release approval, exact local backup expiry, neighboring-copy isolation,
unresolved holds and post-catalog drift. Existing recovery tests use mocked
PostgreSQL/command orchestration and are explicitly not a real restore proof.

A real isolated PostgreSQL dump/restore plus actual controlled ORM and local-object
replay must be run by the central synthetic integration runner. Production policy
approval, deployment mount/protection configuration, real provider protocols,
physical DB pages/WAL/replicas, storage snapshots, cloud object versions, delivered
exports and forensic media erasure remain separate. No generic interface or
simulation receipt establishes these unsupported copies as erased.
