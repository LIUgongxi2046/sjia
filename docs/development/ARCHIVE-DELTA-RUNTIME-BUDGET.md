# Deterministic archive delta runtime policy

## Contract

`POST /api/v2/archive-batches/{batch_id}/delta-summaries` retains its synchronous,
local deterministic implementation and existing 202 archive-delta receipt. It
creates neither an outbox event nor an autonomous worker/provider invocation.

Admission requires the current qualified `backend-runtime-1` configuration and
exactly one `tasks` entry whose `task_type` is `summary`. The complete entry is
copied into the immutable write-intent/task context alongside the existing exact
runtime entity reference. A missing summary entry is unavailable; duplicate
entries fail real runtime qualification and are never resolved by first-match or
a fallback. No operating threshold is introduced by this implementation.

Generation rechecks the current runtime, exact policy, environment gate, actor,
authentication epoch, patient and source epochs before creating its task. Its
`deadline_at` is `created_at + execution_seconds`, explicitly passed to the shared
task service. Its initial lease uses `lease_seconds`, clamped by the shared service
to the task deadline. There is no queue wait, heartbeat renewal or automatic retry
in this synchronous path. The maximum attempt count is checked before lease
acquisition; normal execution creates one attempt, and an idempotent HTTP replay
returns the committed receipt without another attempt.

## Capacity and fencing

Gate → actor → patient locks precede delta batch/task locks and use the same lock
prefix as asynchronous summaries. Read routes pass unlocked identity hints and reload
the result under that prefix; stale caller validity/ownership/patient fields never
override the locked database row. Capacity checks count running `summary-task-2`
and `archive-delta-1` tasks bound to the current runtime with both a live lease and
a future deadline. Queued, terminal, expired and other-runtime tasks do not occupy
this budget. A full budget returns `ARCHIVE_DELTA_TASK_CONCURRENCY_EXCEEDED`; it
never cancels, modifies or takes over another task. The gate lock remains held for
the synchronous render, including output commit, so another summary admission
cannot race its capacity check.

Execution checkpoints reject an expired lease/deadline or changed generation,
owner or task state before output and after output/dependencies. Final completion
still uses the shared generation/deadline/lease compare-and-set. A transaction
inside `generate_delta` rolls back task, attempt, summary, items and dependency
writes on any failure, including callers outside the HTTP transaction. This is
cooperative local deadline enforcement, not process preemption of a Python
operation already running. Existing request/statement timeouts remain separate.

## Existing snapshots

The public schema and task schema name remain unchanged. Internal snapshots from
before this change have no explicit `task_policy`; their hash remains checked,
but current context comparison marks them invalid/superseded and hides items.
They are not backfilled with a budget that was never enforced. A new user request
regenerates under the current qualified policy. Prepared old intents similarly
require the existing retry/reprepare protocol. Current snapshots also invalidate
when their qualified runtime or exact task policy changes.

## Verification scope

`tests.test_archive_delta_budget` covers exact policy selection, qualified missing
and duplicate rejection, actual non-default timing, exhaustion before/after partial
inserts, attempt and generation fences, both summary families' capacity, expired
and unrelated capacity exclusions, native HTTP create/read/task/receipt/replay,
old-snapshot invalidation, stale-prefetch result rereads and two real
separate-PostgreSQL-connection races (capacity and read/invalidation lock order).
All fixtures and evidence are explicitly synthetic software tests; they do not
constitute clinical approval, production validation or provider execution.

### Executed focused results — 2026-10-11 UTC

- Local no-database policy selection: 2/2 passed; compilation and changed-source
  whitespace checks passed.
- Central isolated PostgreSQL full module: **22/22 passed**, 0 failures/errors/
  skips, in **64.275 seconds**. This includes 18 transactional behavior tests,
  both physical separate-connection races and the two pure policy tests.
- Immutable source snapshot SHA-256:
  `ecffac038eacb0ae430c95f9dbf7330ffeb63c22d9edfe5c33a796f2fdeb69c8`.
- Detailed execution log:
  `/workspace/shared/sjia-local-runtime-20261011/original-archive-delta-budget-round2.log`.

The initial focused run on source
`ae440b7a31e674c36ef3175bb6a0d4e4b0e52b114458bce3f68e3851a43c6190`
passed 20 cases and encountered two concurrency-fixture setup errors before their
test bodies ran. Reusing archive setup outside a transaction split a record and
its current-version row across commits, violating the genuine deferred foreign
key. The fixture was corrected to commit the pair in one `transaction.atomic`
block; no constraints were disabled and no production change was required.
The complete 22-case module was then rerun successfully, rather than treating
unexecuted concurrency bodies as passes.

This is focused synthetic engineering verification. The lead's final all-source
aggregate run remains a separate completion check; prior aggregate runs do not
establish a pass for this new source snapshot.
