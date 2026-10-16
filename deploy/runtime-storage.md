# Runtime packaging and local object persistence

The current backend uses `LocalFileStore`. The MinIO service in the reference Compose
file is not its storage backend. API and worker must both use
`SJIA_FILE_OBJECT_ROOT=/var/lib/sjia/objects` and the `fileobjects` named volume.
That volume survives container replacement; `docker compose down -v` destroys it.

## Existing installations

Before replacing existing containers, stop writes and back up both the database and
all existing local object directories. Copy the original relative object-key tree
from the old API/worker containers into the shared volume, checking checksums against
`file_object`. Resolve differing bytes at the same key manually; do not overwrite or
delete them automatically. Mounting a new empty volume does not migrate old files.
Resume writes only after API and worker can both read the same verified objects.

The volume is a single-host deployment arrangement. Multi-host deployments require
an approved shared filesystem or a separately implemented object-store adapter.

## Runtime assets

Runtime imports in `configurations.schemas`, `archives.inputs`, the API views,
record periods, relations, search, reminders and summaries read schemas relative to
the repository root. Keep `/app/backend-design`, `/app/alert-design` and
`/app/data-design` alongside `/app/backend`. All `*.schema.json` files in those three
directories are copied, including transitive JSON-schema references and the schemas
used in deployed-artifact checksums. `data-design/v2-schema.sql` is also required by
the archives migration; schemas inside `backend/` travel with the application.

`deploy/smoke_runtime.py` validates the directly loaded runtime schemas and imports
WSGI, API URLs and configured Celery modules. It forbids opening a database
connection and needs no provider credentials. The Dockerfile runs it during build.
`tests.test_deploy_runtime` stages the actual Dockerfile COPY sources and runs the
same smoke using production settings with synthetic configuration. This staged-tree
test does not replace an actual Docker build or a two-container integration test.

## Upload retries

An immutable write first fsyncs a unique temporary file, then publishes it using an
atomic no-clobber hard link. An existing key succeeds only when every byte matches;
different or partial bytes are a conflict. Only that call's temporary file is
removed, never a published object that another transaction could reference.

If the database transaction rolls back after publication, the next request can
reuse the complete bytes and recreate the metadata. Successful duplicate requests
reload the locked upload row and return the original file without adding pages.
Unreferenced objects from abandoned uploads may remain; deletion needs a separate,
retention-aware reconciliation policy rather than speculative rollback cleanup.

## Release composition (repository closeout, not a deployed environment)

`deploy/Dockerfile.frontend` builds the checked-in H5 lockfile and all imported
shared design/schema trees, then copies its `dist` into nginx. The API image keeps
the backend schema layout described above. `deploy/compose.yaml` now names all four
application queues (`default`, `purpose`, `assessment`, `notification`) explicitly,
with separate worker processes. One Beat schedules the existing dispatch/reconcile
jobs. Do not scale Beat above one instance for a database. Concurrency defaults to
one per queue; this is a conservative startup setting, not a measured capacity claim.
The operator must supply the approved per-worker memory limit and account for all
API/worker/Beat connections in the database budget.

The Compose release runs `python manage.py migrate --noinput` in a one-shot service.
API, workers and Beat depend on its successful exit. Therefore `docker compose up`
is a database-changing release action, not a read-only smoke test. Before running it,
obtain approval for the exact environment/release, review migration reversibility,
and complete the consistent backup below. No migration, Docker build or Compose
startup was executed during this closeout.

Required deployment inputs include the environment name, allowed hostnames, signing
key, database password, configuration-evidence directory/read-size limit, worker
memory limit and the existing TLS certificate and key paths. Use the deployment's
approved secret injection mechanism; never put real values in the repository or
copy a development `.env` into the image. TLS and configuration-evidence mounts are
read-only, use `create_host_path: false`, and fail if the supplied source file or
directory is absent. This change creates no certificate or authentication material.
External providers still need their exact approved runtime credentials/configuration;
none is inferred from a simulation account or created by this deployment file.

The unused MinIO service was removed from this single-host composition because
`LocalFileStore` never wrote to it. Existing MinIO or old named volumes must not be
deleted by this change; migrating to S3 remains a separate storage-adapter project.

## Safe backup and recovery sequence

1. Obtain the approved backup scope, maintenance window, retention policy and
   operator. Quiesce HTTP writes, stop Beat, drain or pause the four workers, and
   confirm no active writers or external in-flight effects remain. Record the exact
   release and database/schema identity. Do not terminate unrelated processes.
2. While the write freeze is maintained, capture PostgreSQL and copy the complete
   local object and configuration-evidence trees. Preserve relative object keys,
   permissions and version identifiers. Produce checksummed manifests and verify
   database file references against the copied bytes. A live `pg_dump` followed by
   a later uncoordinated object copy is not a consistent whole-system backup.
3. Store the backup using the approved encrypted/restricted destination and
   retention policy. Do not put patient data or dumps in Git or a shared artifact.
4. Restore to an explicitly isolated environment with separate database, object
   directory, broker and credentials. Keep Beat and all delivery workers stopped;
   disable external provider/network sends using the approved environment controls.
5. Verify migrations, object checksums, record versions, professional verification,
   dependency/configuration evidence and current access. Replay recorded deletion,
   revocation and disabled publication decisions before any service may be adopted.
6. Compare actual elapsed time and data-loss boundary with the approved RTO/RPO.
   Resume outbound processing only after reconciliation of uncertain effects and
   explicit operational approval. Never blindly retry an unknown external send.

The existing `tests/operations/t57_isolated_restore.py` is a simulation-only recovery
harness, not an automatic production backup job. Retain its simulation guardrails.
Database downgrade is not an assumed rollback: use compatible app rollback or an
approved isolated restore after reviewing the actual migration/data state.

## Internal health, logs and metrics

The API exposes `/internal/liveness` (process only), `/internal/readiness` (DB,
migrations, broker and configured runtime), `/internal/capabilities` (honest
unverified business capabilities) and `/internal/metrics` (Prometheus). By default
only socket peers in loopback networks may read them; forwarding headers never
grant access. nginx always returns 404 for `/internal/` and the API has no published
host port. An approved collector can be given an explicit internal network through
`SJIA_INTERNAL_PROBE_NETWORKS`; changing actual network access is an operator action.

Readiness fails safely on missing runtime configuration, schema or dependency
failure and does not return exception text. It is not clinical qualification or a
successful provider test. The container startup probe intentionally uses liveness,
so support can configure the service before business capability gates are ready.
`probe_runtime.py readiness` provides the separate operational admission check.

The request telemetry logs contain an SDK-generated local trace identifier,
registered operation name, status and elapsed seconds only. They omit URLs,
request/response bodies, cookies, source images, OCR text, patient IDs and secrets.
nginx access logging is off to avoid storing resource URLs. The official Prometheus
HTTP counters/histogram aggregate all Gunicorn children of this API instance through
its private multiprocess directory. `deploy/start_api.py` initializes the directory
before import; never run the production API using an unconfigured alternate launcher.
Task status and oldest unpublished outbox age are database aggregates, with only
closed task-type/status labels. If they cannot be read, the endpoint returns 503 and
`sjia_operational_metrics_available 0`, not a false zero backlog.

OpenTelemetry SDK spans now propagate API → persisted outbox → producer → Celery
consumer, with bounded attributes, configured sampling and opt-in export. Automatic
DB/provider instrumentation is intentionally excluded to avoid SQL/URL/payload
capture. Operator-rendered alert rules/receiver configuration and consistent
backup/isolated-restore tooling are implemented. See `observability/README.md` and
`recovery.md` for exact admission, process lifecycle and recovery gates. Actual
collector/export, alert routing/receipt, deployed multiprocess scrape and real
backup/restore acceptance remain NOT_RUN. No collector URL, alert recipient,
clinical outcome or RTO/RPO has been fabricated.

### Explicit TLS proxy trust

Production defaults to `SJIA_TRUST_PROXY_TLS=0`: forwarded protocol headers are not
trusted. In the exact reference topology, nginx is the only published ingress,
nginx overwrites `X-Forwarded-Proto` with `https`, and the API is reachable only from
its approved private runtime network. After confirming all three conditions, the
operator may explicitly set `SJIA_TRUST_PROXY_TLS=1`; only then is Django's
`SECURE_PROXY_SSL_HEADER` enabled so HTTPS Origin/CSRF checks see the correct scheme.
The Compose reference requires an explicit choice and never silently sets 1.

Do not expose the API port directly or enable this option behind an untrusted
header-forwarding proxy. Do not use wildcard CSRF trusted origins. A different
proxy/topology requires its own approved configuration review. The closeout tests
use RequestFactory on/off checks only; no live TLS, system or network configuration
was changed or executed.
