# Explicit backup and isolated restore

`deploy/recovery.py` is an operator-triggered tool, not a scheduler. It performs no
database access or service changes without `--execute`. The default command,
`python deploy/recovery.py backup`, prints a plan only. Real execution needs an
approved maintenance window, exact project/database/cluster/paths, responsible
operator, protected backup destination, retention and isolation decisions. An
admission file is an operator record, not a substitute for obtaining approval.

## Requirements and admission

- Use the checked-out approved release and its locked Python dependencies, Docker
  Compose and PostgreSQL 17-compatible `pg_dump`/`pg_restore`. This reference
  composition has exactly one API host and shared local object volume. Additional
  writers, hosts, object-store adapters or schedules need a separate runbook.
- The operator supplies an existing libpq service configuration and password file
  through the approved secret mechanism. The tool never reads, prints, creates or
  changes credentials. No secret may appear in admission JSON or command arguments.
  Record the exact service name, database name and `pg_control_system()` cluster
  identity obtained by an authorized operator. The existing operator account must
  be able to inspect sessions, read all application data and acquire table locks;
  this tool never grants it additional permissions.
- Fill the appropriate `backup.admission.example.json` or
  `restore.admission.example.json`, including an expiry and an approved operation
  timeout. The example is intentionally unapproved and cannot execute. Record the
  immutable release/image digest as `release_reference` (an identifier without
  spaces; preserve the full supporting evidence in the release record).
- All paths in the admission must match the resolved command-line paths. The
  backup destination and restore destination must be new, nonoverlapping and
  outside this checkout. The operator must provide an already encrypted/restricted
  destination and approved retention. The tool does not change security settings
  or encrypt/manage keys on the operator's behalf. A dump and its manifests contain
  sensitive information, including object keys. Never attach them to a public
  issue, publish them, or put them in Git.
- For a named Docker volume, give the actual approved host-mounted object-tree
  path, not a guessed container path. The evidence path must likewise refer to
  the exact source directory mounted by this deployment. Obtain access using the
  operator's approved process; the script does not inspect arbitrary containers.

## Backup execution and exact freeze

After admission, the operator may run:

`python deploy/recovery.py backup --admission BACKUP_APPROVAL.json --destination
NEW_PROTECTED_BACKUP --objects EXACT_OBJECT_ROOT --evidence EXACT_EVIDENCE_ROOT
--execute`

The script stops only the admitted Compose project's `nginx`, `api`, `beat`,
`worker`, `worker-purpose`, `worker-assessment`, `worker-notification` and `migrate`
services. This is an outage and requires prior approval. It never uses `down`,
deletes volumes or stops unrelated processes. Remaining services other than
`postgres` and `redis` fail admission: stop optional collectors/other senders using
their own approved procedure first. Graceful stop has the approved timeout;
external in-flight effects must already be reconciled before the operator attests
`external_effects_reconciled`. A forced process stop cannot prove an external send
did not occur. Unknown effects still need reconciliation before any restart.

Then it verifies exact database and physical-cluster identities and rejects every
other client session, including idle clients. It obtains SHARE locks on all user
tables for the whole dump/object/evidence capture. These block ordinary database
writes while allowing `pg_dump` ACCESS SHARE reads. Lock acquisition has a bounded
five-second wait. The stopped application processes plus explicit exclusive object
writer admission provide the matching filesystem freeze. The operator must not
restart writers or allow schema/sequence operations during this window.

Within that freeze, it writes a custom-format PostgreSQL dump, copies complete
object/evidence trees, verifies both source and copy inventories, rejects links
and special files, and checks every non-disposed FileObject checksum/size against
the copied bytes. Disposed rows may legitimately refer to already removed bytes.
The manifest records every object version, source cluster/database, release,
approval, capture timestamps and elapsed duration. Dump and manifest have separate
SHA-256 integrity receipts. A backup is complete only when `COMPLETE.json` and all
checks in `verify` succeed. `WRITE_FREEZE.json` is the durable freeze record.

Services remain stopped on both success and failure. No automatic restart is
implemented. Partial destinations remain for authorized inspection; do not reuse
or overwrite them. Read-only integrity verification is:

`python deploy/recovery.py verify --source EXISTING_BACKUP --execute`

## Isolated restore

The operator separately creates a new empty database on a different physical
cluster, with a different database name, and an isolated filesystem destination.
They must establish and verify outbound blocking and keep API/Beat/workers stopped
before granting restore admission. These network/security actions are not performed
by this script. The backup must be trusted and reviewed: PostgreSQL dumps execute
source-supplied SQL, so never restore an untrusted dump under this procedure.

`python deploy/recovery.py restore --admission RESTORE_APPROVAL.json --source
VERIFIED_BACKUP --destination NEW_ISOLATED_ROOT --execute`

The script verifies the full manifest before writing, verifies the target's actual
cluster/database identities, and refuses existing user tables, schemas, public
objects/functions or nondefault extensions. It never creates/drops a database and
never passes `--clean` or `--create`. It copies the verified object/evidence trees,
then runs `pg_restore --single-transaction --exit-on-error --no-owner --no-acl` into
that exact target. It re-reads the restored file references and checks their exact
versions/checksums/sizes. Roles/ACLs and server configuration are not included in
this database backup; those require the operator's separately approved configuration
inventory and isolation review. Source Redis/Beat queues are not restored. Pending
business work is in PostgreSQL outbox/task rows and must be reconciled before any
fresh isolated broker/worker is admitted.

The restore always retains `ADOPTION_BLOCKED.json`. Success means
`RESTORED_ADOPTION_BLOCKED`, never ready for clinical or outbound operation. Before
adoption, authorized checks must verify full record/professional-verification/
configuration/dependency references and current permissions, replay deletion,
revocation and disabled-publication decisions newer than the backup, reconcile
uncertain provider/notification effects, and review migrations and release-bound
configuration evidence. Mount the copied configuration-evidence tree at the same
approved runtime path as the original absolute source references, or use a separate
approved relocation/revalidation procedure. Byte copying does not confer clinical
qualification or authorize stale configuration. Record actual RTO/RPO against D01/
D09 targets, then obtain separate approval to start each service. The script cannot
certify externally enforced isolation or authorize the restored environment.

The content-disposition extension now provides an independently checkpointed
journal, actual controlled manifest replay, a database-backed restore marker,
runtime admission verification and exact local backup catalog/expiry. See
[content-disposition recovery](content-disposition-recovery.md) for the explicit
configuration and separate release steps. A normal `restore` remains blocked;
its marker is now consumed by API/Worker/Beat admission rather than being merely
an operator reminder. No feature flag bypasses a restored database's requirement.

Only synthetic file copies and mocked command/DB orchestration were run in this
closeout. No real backup, dump, restore, outage or network change was executed.

Sources: PostgreSQL [pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html),
[table lock modes](https://www.postgresql.org/docs/17/explicit-locking.html).
