# Explicit patient-cluster interval capability

2026-10-11. Software capability; no clinical statistical-method approval or real clinical evaluation was performed. No new release, family notification, provider call, or live-cycle mutation is introduced.

## Supported contract

`clinical-interval-protocol-1` supports exactly `patient_cluster_percentile_v1`, algorithm `1`. Every choice is required: confidence level; two-sided/linear quantiles; metric/aggregation/unit/denominator; static/dynamic mode; development/validation partition and optional one frozen subgroup domain; whole-patient frame and sampling; B; PCG64 and seed; ordered frame; numerical dependency/version/implementation hash; explicit cluster/contribution minima; fail-on-invalid and not-computed-on-degenerate policies; pointwise coverage without multiplicity adjustment. Numeric integers must be exactly representable in JSON/JavaScript (at most 2^53−1). No 95% or other statistical default exists.

The registry covers sensitivity, missed-event fraction, PPV, alerts per assessable observation person-hour, alerts per completed negative person-hour, unassessable positive-event fraction, and detected-event mean/median first-alert difference. Ratio metrics use ratio of sums. Patient multiplicities multiply whole-cluster contributions, including all events/prompts/differences; repeated cluster draws are never deduplicated by event ID. Static/dynamic specs in the same partition use the same draw sequence but retain their own denominators. Subgroups are domains within the full partition frame, preserving zero-contribution patients and time-varying membership. Partitions are never pooled for CI. No static/dynamic difference interval is silently added.

Unknown outcomes are excluded only from the relevant known-outcome denominator; unknown-label completed periods retain real observation time. Insufficient results are not negatives. Time summaries are conditional on detected events and are not clinical care-seeking lead time. Empty original denominators, insufficient clusters/contributors, any invalid draw, nonfinite results, or a fully degenerate resampling distribution yield null bounds with a reason. No drop/redraw/independent-binomial fallback occurs.

The resource limits are engineering caps (10,000 draws, 2,000 clusters, 2,000,000 draw cells, 4,000,000 weighted operations and a 10-second calculation budget), not clinical sample-size criteria. Hitting a limit yields RESOURCE_LIMIT, not a success interval. A resource-aborted partial draw count is diagnostic, not a reproducible inferential result.

## Reports and trusted runtime

Pure v1/v2 calls without an interval argument retain their old report shape. Explicit interval calls produce `clinical-metrics-report-3` with an original point-report version and a `clinical-interval-results-1` section. New runtime admissions always use report 3, including NOT_CONFIGURED when no CI artifact is selected. A nonempty reference without an executable body never generates a CI. Unknown schema/method remains unsupported. Original report-2 saved bindings retain their legacy null-CI read path and recheck their original evidence; they do not gain a retrospectively calculated interval.

Each result carries its declared spec, point estimate, bounds, level, method, status/reasons, numerator/denominator, cluster counts, draw counts/digest and detected/missed/unknown/unassessable diagnostics. The section records original protocol JSON, exact artifact byte SHA-256 (or canonical payload hash for pure calls), canonical input hash, implementation/NumPy identity and limits. Unlisted metrics remain NOT_REQUESTED. Section completeness, required-spec completeness and background task completion are distinct: any missing or failed required CI leaves the run insufficient even though its worker task completed normally. Optional failures still make the CI section incomplete.

Runtime interval evidence is read through the existing approved-artifact path. The optional `confidence_interval_binding` plan field is required for new runtime CI: it contains literal protocol SHA-256, approval-evidence reference/SHA-256, approval-audit UUID and purpose inside the pre-frozen, independently hashed measurement-plan artifact. It is not a mutable post-freeze pointer; replacing a CI body plus revision checksum, or swapping its approval evidence, fails against those frozen values. In addition to byte hash and SourceRevision identity, it binds measurement plan, purpose, frozen-at cutoff, a committed source-approval audit event and matching write receipt before freeze, and the live source-approval evidence. Source `captured_at` alone is not treated as `approved_at`. The approving actor’s locked manage_config authority must be valid both at approval and now. Approval, retention, source status, permissions, independent labels/pairings, saved replay results, configuration roles, known-at eligibility and no-publication checks are revalidated. Evidence mutation/revocation is rejected rather than downgraded to a trusted null interval.

Admission prepares a bounded description without resampling. The worker qualifies under lock, computes outside the DB transaction, then requalifies under lock before saving. The complete result, admitted proof and task input are integrity-bound using Django signing. Reads requalify and verify that signature and every deterministic binding; they never bootstrap. The existing production secret configuration remains responsible for signing-key security/rotation. This signature is not clinical approval and does not replace any eligibility check.

No new models/migrations are used. New content is in EvaluationRun.input_refs[0].clinical_capture_binding.confidence_interval and TaskAttempt.usage_evidence.clinical_adoption (report/proofs/hash/signature). Ephemeral `_interval_inputs` are not persisted. These records remain subject to the platform's controlled data-retention/erasure policies.

## Synthetic four-artifact example

Files in `docs/development/examples/clinical-interval-synthetic-v1.*.json` contain explicitly fictional plan, capture, independent pairings and interval protocol. The 50% level, 200 draws and seed 17 are declared software-test values, never recommended clinical parameters. The two fictional patients are both development-frame members; validation is empty. The protocol requests static/dynamic observation-hour rates. The result is 1/3 prompts/person-hour with non-null [0, 0.5] intervals for this exact test seed/build. It remains `NOT_VERIFIED_BY_PURE_EVALUATOR`, `clinical_validation=false`; UUIDs/source/task hashes and the extra approval/audit identities in the plan binding are not live approved evidence. The protocol hash in that binding is the exact example file hash; approval evidence still needs a real independently approved source before any runtime adoption.

From the repository root:

```sh
backend/.venv/bin/python backend/tests/evaluate_clinical_capture.py \
  --plan docs/development/examples/clinical-interval-synthetic-v1.plan.json \
  --capture docs/development/examples/clinical-interval-synthetic-v1.capture.json \
  --interval-protocol docs/development/examples/clinical-interval-synthetic-v1.interval-protocol.json \
  --output /tmp/sjia-explicit-interval-report.json
```

Omitting the artifact path while retaining its plan reference produces NOT_CONFIGURED. `--engine --output /tmp/sjia-interval-engine.json` exports the current exact build identity. Code or numerical version changes invalidate a bound protocol; after such a change, generating a new synthetic candidate requires explicitly updating and reviewing that fixture. Never silently rewrite an approved real protocol's implementation hash. `--schemas --output /tmp/sjia-clinical-contracts.json` exports the strict input schemas. The standalone protocol schema includes the per-metric aggregation/unit/denominator constraints.

## Evidence and remaining acceptance

Executed locally, with no database/network:
- 52 focused/preserved pure tests: estimator/denominators, signing/tamper protection, worker transaction boundaries/requalification/completion, existing v1/v2 metrics.
- Four example JSON artifacts validate; the CLI produces two non-null intervals and accurate missing-body status.
- New and legacy CI DOM/API schema tests, clinical async guards, TypeScript and frontend build pass. The existing >500 kB bundle-size advisory remains.
- Full backend-design/check_v2.py was not run because the local interpreter lacks markdown_it. Clinical schema validation and frontend Ajv compilation were executed separately.

Native PostgreSQL labels prepared for the central isolated runner: `tests.test_clinical_interval_adoption`, `tests.test_clinical_capture_adoption` and `tests.test_clinical_interval_multicluster`. They exercise actual synthetic saved static/dynamic replay services, exact approved evidence, pre-freeze approval and revocation. The freeze/audit boundaries are explicit fixtures, not the entire purpose-copy/freeze/source-approval HTTP lifecycle. The single-patient native fixture deliberately yields INSUFFICIENT_CLUSTERS. The new two-patient TransactionTestCase uses four real saved replays, real dataset/partition/label/run/task/attempt FK targets, actual unlocked interval calculation and signing, locked requalification, native attempt completion, committed persistence, two no-bootstrap reads, saved-result tampering and protocol revocation. Each patient contributes one prompt over a different actual observation duration; independent arithmetic and the explicitly declared RNG draw counts determine its non-null expected bounds. No decisive calculator/qualifier/save/read return value is mocked. Exact-key isolated-schema cleanup preserves FK constraints and precedes temporary evidence removal. This tests the runtime CI bridge and native save/read phases, not the full dataset-run dispatch/HTTP lifecycle. Round 2 finished 15 passed / 2 fixture errors from absent dataset FK targets; those targets are now materialized. The new 18-test native set awaits its source-frozen central result. Earlier 975-test evidence is not evidence for this change.

Real clinical data authorization, protocol appropriateness, statistical/clinical acceptance and any method outside this registry remain uncompleted external inputs. Generic algorithm sources are in [the implementation plan](PLAN-configurable-intervals.md).
