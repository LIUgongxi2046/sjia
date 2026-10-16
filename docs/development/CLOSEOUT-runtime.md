# Runtime closeout implementation plan

## Remaining capability plan (2026-10-11)

T28 explicitly requires OpenTelemetry, allowlisted logging, operational alerting and
backup/object manifests. T57 requires isolated recovery and honest measured evidence
under D01/D09 admission. This increment completes source capabilities, not operational
or clinical acceptance:

1. Replace manual process counters with the official Prometheus client, a dedicated
   per-API-instance multiprocess directory, Gunicorn child cleanup and startup lifecycle;
   preserve read-only database snapshots and fail-safe availability reporting.
2. Add manual OpenTelemetry SDK spans with bounded attributes, sampling, and strictly
   opt-in OTLP HTTP export. Persist only validated W3C traceparent in the outbox,
   independent of Task input/checksum. Trace API → outbox → dispatch → worker without
   bodies, resource identifiers, URLs, exception messages, baggage or credentials.
3. Ship disabled-by-default alert/collector configuration and an operator rendering
   tool requiring explicit alert thresholds and recipient configuration. Never send.
4. Ship an operator-only backup/isolated-restore command with explicit admission,
   exact application write freeze, active-session checks, checksummed database/object/
   evidence manifests, immutable restore destination and no automatic restart/send.
5. Verify SDK propagation/privacy, multiprocess aggregation across actual isolated
   child processes, deployment/config validation and backup/restore orchestration using
   synthetic files and mocks only. Database tests are reserved for the lead.

No commit, push, deployment, live model/export/alert calls, real database dump/restore,
host security changes or credential creation is part of this increment.

Date: 2026-10-11. Scope: T28/T33/T57/T60 repository implementation only, preserving HEAD 4767c6b and the pre-existing schema packaging/shared file-store repairs. Deployment profile: CONTAINER_SINGLE_HOST.

## Initial audit and first increment (superseded where updated below)

1. The Compose nginx service does not receive TLS files or a built H5 bundle. Add a reproducible H5 build image and explicit read-only certificate/key mounts supplied by the operator. Do not create certificates, secrets or change host/network security.
2. The dispatcher targets default, purpose, assessment and notification queues, but the only worker has no queue selection and no Beat process exists. Add separate bounded worker services, a single scheduler and a migration release prerequisite. Do not run them in this task.
3. Add internal process liveness, bounded DB/Broker readiness with safe exception codes, aggregate Prometheus metrics, internal capabilities with honest unverified capability states, and a privacy-safe request log/trace identifier middleware. Do not claim end-to-end OpenTelemetry export or external alert delivery; both still need an approved collector/contact configuration.
4. Document release/rollback and write-frozen database/object/config-evidence backup boundaries. No database operation, outage injection, container build/start or external send is authorized in this closeout.
5. Add `backend/tests/test_closeout_runtime.py` using SimpleTestCase/mock/file assertions, and run only no-database checks. Re-run pre-existing deploy import smoke in an isolated temporary tree. Report exact counts and limitations.

## Dependencies and coordination

Runtime imports must retain backend-design, alert-design and data-design schema trees beside backend. API and workers retain the same named local object volume. Config evidence must be operator-provided and mounted read-only. Clinical runtime/configuration approvals remain independently enforced by current services. Do not change approval gates, formulas or evaluation execution.

The lead coordinates evaluation and frontend defects. Core worker has been notified that base settings/middleware may be touched. Existing runtime-schema tests remain authoritative for prior packaging repairs.

## Sources

- PRD NFR-06/07/08/09/14—18 and backend LLD sections 6—7.
- https://docs.docker.com/compose/how-tos/startup-order/ : health and service-completed dependency conditions.
- https://docs.celeryq.dev/en/stable/userguide/workers.html : explicit queues, concurrency and worker naming.
- https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/ : release, static assets and deployment validation.

## Acceptance boundary

CREATED/VERIFIED will describe repository artifacts and executed unit/import checks only. Actual Docker image build, Compose startup, TLS verification, database migration, worker/Beat operation, full metrics/collector/alert integration, production backup/restore, real devices and clinical approval remain NOT_RUN until exact environment and permission are provided.

## First-increment results

Implemented frontend image/build inputs, explicit TLS mounts, migration dependencies, one Beat and four queue workers with bounded concurrency and required memory cap. Removed the unused MinIO service from this LocalFileStore composition without deleting any existing volume. Preserved prior backend schema and object-volume fixes.

Added loopback-default internal endpoints, dependency/schema/config failure handling, two-second Redis socket limits, a three-second database connect timeout, process-local Prometheus HTTP histogram/counters and read-only task/outbox aggregates. Runtime request logs use an allowlisted formatter and locally generated trace ID. Capability health intentionally does not claim clinical/provider readiness. Production proxy TLS trust is disabled by default and requires an explicit `SJIA_TRUST_PROXY_TLS=1` under the documented private API/header-overwriting nginx topology. RequestFactory tests verify opt-in/off scheme and same-origin CSRF behavior; no production configuration was executed.

VERIFIED: `DJANGO_SETTINGS_MODULE=config.settings.development backend/.venv/bin/python deploy/smoke_runtime.py` passed 17 required schemas, migration SQL, WSGI, API URL and Celery imports with database connections forbidden by the smoke. `backend/manage.py test tests.test_closeout_runtime tests.test_deploy_runtime --noinput` passed 26 tests, explicitly skipping database setup. The original T28 successful-readiness expectation was updated to require runtime configuration; its database-backed test was not executed here.

Before runtime changes, `npm run typecheck` and 11 synthetic React/jsdom registration checks passed. Independent route/catalog inspection found all 39 UI2 paths registered and 328 design-state combinations; five stale design operation paths were reported to the lead.

At that point OpenTelemetry and multiprocess aggregation were still missing. The
following final increment supersedes those implementation gaps.

## Final source results (2026-10-11)

- Official OpenTelemetry SDK and OTLP HTTP dependencies are locked. Manual API,
  outbox producer and Celery consumer spans propagate only validated W3C traceparent.
  `tasks.0005_outbox_traceparent` adds transport metadata separate from Task input
  and its checksum. Public incoming trace headers, baggage, SQL/provider automatic
  instrumentation and exception recording are excluded. Sampling is configurable;
  export defaults OFF and requires explicit endpoint admission, rejecting ambient
  OTEL exporter/credential configuration. Operational export stayed OFF; an isolated
  real-SDK constructor check used a mocked transport and made zero send calls.
- Official Prometheus counters/histograms replace the process-local dictionaries.
  A fresh API-only tmpfs directory is set before Gunicorn starts, its children
  share that directory, and each scrape uses MultiProcessCollector with a fresh
  registry. Child-exit cleanup and new-master directory lifecycle are provided.
  DB aggregates remain separate, read-only and fail-safe. Two real isolated Python
  child processes contributed 3+5 requests; an independent scrape returned exactly
  8 requests and 2 seconds total duration, including exited-process counters.
- `deploy/observability/configure.py` renders seven operational alert rules plus
  Prometheus/Alertmanager configuration only after explicit thresholds and receiver
  file admission. The optional Compose profile publishes no host ports and scrapes
  loopback in the API namespace. No alert/contact/threshold was selected or sent.
- `deploy/recovery.py` supplies write-free plan mode and explicit operator-only
  execution. Exact project/database/physical-cluster/path admission, expiring
  approval, stopped API/Beat/queue writers, no foreign DB sessions, held SHARE table
  locks, full dump/object/evidence checksums and FileObject version checks are
  implemented. Restore refuses the original cluster/name or any populated target;
  transactional pg_restore has no --clean/--create. No service restart is performed,
  and adoption remains blocked pending deletion/revocation/configuration/effect
  reconciliation. No real database dump/restore or service stop was executed.

VERIFIED: 53 no-database tests passed using
`backend/.venv/bin/python backend/manage.py test tests.test_local_http_contract tests.test_recovery_tooling tests.test_runtime_observability tests.test_closeout_runtime tests.test_deploy_runtime --noinput`.
Coverage includes SDK parentage/privacy/context cleanup, exporter opt-in validation,
actual synthetic multiprocess aggregation, rule rendering, source/deploy contracts,
synthetic backup/restore orchestration, identity/foreign-session refusal, table-lock
statements, object/version checks, tamper/symlink rejection and no automatic restart.
`pip check` passed. Staged WSGI/API/Celery import smoke remains included in these checks.

`deploy/check_local_http.py` now verifies liveness 200 separately, requires healthy
DB/Broker/schema, and records readiness 200 or a truthful 503 only for explicitly
missing runtime configuration. Existing synthetic auth-account cleanup and frontend
proxy checks remain. Four no-DB tests verify those admission/reporting branches.
`tests.test_live_http_record.SyntheticRecordLiveHttpTests` supplies a real loopback
LiveServer login/CSRF → write intent → synthetic manual record → idempotent replay →
readback → logout denial test. It requires a Django `test_` database, uses qualified
synthetic fixtures and blocks provider transports. It was not run by this worker;
the central database owner must execute and record its outcome.

Source capabilities for this bounded T28/T57 increment are implemented. Operational
acceptance remains NOT_RUN: Docker daemon/build/Compose, actual Gunicorn/Celery/Beat
deployment, real migration (central test owner coordinates it), TLS, collector/export,
promtool/amtool validation, real alert receipt, production backup/restore, real
capacity/RTO/RPO and clinical qualification. Synthetic/mocked tests are not those
acceptances. Detailed operator procedures are `deploy/observability/README.md` and
`deploy/recovery.md`. No browser, provider or external model call was made.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.
