# Operational telemetry and alert admission

These files implement collection/routing capability. Nothing starts or sends when
they are edited, imported or rendered. Clinical qualification is unchanged.

## Traces

`backend/observability/tracing.py` uses the official OpenTelemetry SDK with manual
server, producer and consumer spans. Public incoming trace headers are ignored.
Only a validated W3C `traceparent` is stored on `OutboxEvent` (migration
`tasks.0005_outbox_traceparent`) and propagated as a Celery transport header.
An outbox row keeps its original context on lease updates/retries. Task payloads,
checksums and business IDs are unchanged. Celery task signals restore context for
the worker and remove it after execution. Events created by the worker inherit
its active span. Old events without context start a new trace.

Span names and attributes contain only operation, HTTP status or fixed worker
outcome. There is no SQL/provider automatic instrumentation, request/body/header
capture, patient/resource identity, URL, baggage, tracestate, exception text or
stacktrace. The resource has only a fixed service name, without environment or
host detection. Parent-based sampling uses `SJIA_OTEL_SAMPLE_RATIO` (0..1, default
0.1). This is a technical sampling default, not a clinical acceptance setting.

Export defaults to OFF even if standard OTEL environment variables exist. After
specific collector/data-sharing approval, the operator can set
`SJIA_OTEL_EXPORT_ENABLED=1` and `SJIA_OTEL_TRACES_ENDPOINT` to the exact approved
HTTP(S) endpoint ending in `/v1/traces`. Endpoint userinfo/query/fragment and
ambient OTEL exporter/credential configuration are rejected. The bounded official
OTLP HTTP exporter uses no proxy/netrc environment lookup, has a three-second
timeout and a 512-span queue. This profile targets an approved internal collector
without per-client credentials; authenticated collector setup is a separate
operator decision. Operational export stayed OFF. One real-SDK construction test
uses a mocked transport and an empty span queue, asserting zero sends. The pinned
SDK's official urllib3 transport avoids this project's Django `requests` app name
collision; dependency-lock refreshes must retain the constructor regression.

Run Gunicorn without `--preload`: the launcher does this, and each worker builds
its own SDK provider after fork. Celery likewise constructs a provider in its
actual execution process. A failed export cannot change a business transaction;
queue overflow may drop telemetry, and telemetry is never the audit record.

## Multiprocess metrics

`deploy/start_api.py` creates a fresh private subdirectory of the API-only tmpfs
`/run/sjia-metrics` before importing Python instrumentation, then execs Gunicorn.
All children of that master inherit `PROMETHEUS_MULTIPROC_DIR`. The official
Prometheus multiprocess collector reads every child's counter/histogram file;
worker replacement preserves counts for this API instance's lifetime. Gunicorn's
`child_exit` hook marks dead processes. A new API master gets a new directory, so
PID recycling does not retain stale metrics. The container tmpfs removes old
directories on container replacement. Do not share this directory between replicas,
Celery or independent Gunicorn masters. Do not set it after importing metrics.

Scrape one endpoint per API instance, not one per Gunicorn worker. Database task
counts and outbox age are common database snapshots, not per-process counters;
do not sum duplicate snapshots if a future topology adds API replicas. A single
development Python process reports `sjia_metrics_multiprocess 0` honestly. The
production launcher reports 1. Unavailable DB snapshots yield 503 rather than
false zero backlog. Internal endpoints remain hidden by nginx.

## Alert rules and receiver

1. Copy `admission.example.json` to an operator-private file. Record approval and
   fill actual threshold budgets. All thresholds are deliberately unset. The
   receiver URL is supplied separately by the authorized operator through the
   mounted `/run/secrets/approved_alert_webhook`; do not place it in source or logs.
2. Render with `python deploy/observability/configure.py --admission APPROVED.json
   --output NEW_DIRECTORY`. The renderer refuses missing approval or thresholds
   and never sends a request. It produces Prometheus rules, scrape configuration
   and Alertmanager routing for API availability, absent metrics, wrong aggregation,
   outbox delay, HTTP errors/latency and failed/uncertain tasks.
3. Validate the files using the approved Prometheus/Alertmanager versions:
   `promtool check config prometheus.yaml`, `promtool check rules sjia.rules.yaml`
   and `amtool check-config alertmanager.yaml`. Paths inside generated config are
   deployment paths; validate with the same mounted layout.
4. Only with explicit deployment and test-recipient approval, supply pinned
   official image digests and use the `approved-observability` profile in
   `compose.observability.yaml` alongside the base Compose file. This profile is
   not enabled automatically. It publishes no host ports. Prometheus joins the
   API network namespace to scrape loopback; the internal probe allowlist is not
   broadened. The sample target must remain `127.0.0.1:8000` for that topology.
5. The operator must perform a specifically approved synthetic alert/receipt test
   and record alert latency, receiver identity and recovery notification. Rendering
   a config or passing Python tests does not prove alert delivery. Stop optional
   monitoring services before the backup tool's exclusive write-freeze admission.

No operational acceptance, production capacity, receiver, RTO or RPO is invented.
The actual Docker overlay, `promtool`, `amtool`, collector and receiver integration
were not run in this closeout.

Sources: [SDK instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/),
[Prometheus multiprocess mode](https://prometheus.github.io/client_python/multiprocess/),
[alert rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/),
[Alertmanager config](https://prometheus.io/docs/alerting/latest/configuration/).
