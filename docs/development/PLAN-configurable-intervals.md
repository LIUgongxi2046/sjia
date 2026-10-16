# Explicit configurable interval engineering (CI-D01–06)

2026-10-11. Engineering capability only. All development and verification here use synthetic captures; no real clinical run, provider, publication, notification, or clinical-method approval. Existing run/evidence/permission gates remain mandatory. This implementation does not choose a protocol for actual patients.

## Contract and completion conditions

- Strict `clinical-interval-protocol-1`: every statistical decision is required. One supported method, `patient_cluster_percentile_v1`, version 1; PCG64, linear two-sided pointwise percentile intervals. No statistical defaults or independent-event fallback.
- Registry covers sensitivity, missed fraction, PPV, observation/negative-person-hour alert rates, unassessable event fraction, and detected-event mean/median alert difference. Each spec fixes mode, partition, optional frozen subgroup domain, aggregation, unit, and denominator policy. Zero-contribution patients remain in partition frame. Whole clusters retain multiplicity.
- `clinical-interval-results-1` is a separate versioned section in report 3. Old reports 1/2 retain their shape. All required specs must calculate for interval completion; task execution completion alone is not statistical completion.
- Bounded resampling outside runtime DB transactions. Admission/read recheck original evidence; execution rechecks again before saving. Saved interval/report integrity uses Django signing over the frozen admission/input/report binding. Reads never bootstrap or trust an unsigned mutable interval body.
- Captured CI bytes, SHA-256, source revision, plan/ref, purpose, pre-freeze recorded source approval and its live evidence are bound. Literal CI/approval hashes, approval audit identity and purpose live inside the pre-frozen measurement-plan bytes; mutable post-freeze references are insufficient. Approver authority is rechecked at approval time and now. Revocation, permission changes and mutations fail closed. Unknown methods/versions remain unsupported, not falsely completed.

## Verification matrix

Pure tests first: strict missing/invalid decisions; independent enumerated draw oracle; repeated clusters/multiple events; ratio-of-sums counterexample; unknown/insufficient/zero denominators; 0/1/few contribution clusters; all success/failure and constant-time degeneracy; invalid draws cannot be dropped; paired draws/different denominators; separated partitions and time-varying subgroup domains; reordered input/seed/code/protocol changes; resource ceilings; synthetic non-null positive path; no database/network. DOM/schema checks: calculated, absent, unsupported, degenerate, partially incomplete, old report compatibility. Native PostgreSQL integration (central owner, after source freeze): artifact mismatch and late/missing approval, revocation at execution/read, saved result mutation, bounded outside-transaction execution and no repeated read computation. The native positive case uses two real synthetic patients and four saved replays, a real unlocked bounded calculation, signed native persistence, and rereads with RNG construction forbidden. Actual observation durations and a declared seed-specific draw-count oracle independently check the non-null bounds. TransactionTestCase cleanup must use exact primary keys in an empty isolated test schema, never disabled FK checks. Unrun tests are not passes.

## Algorithm sources and limits

Generic mechanics only, not evidence that this estimator is appropriate for this application's clinical data:
- Cluster case resampling retains all observations in a sampled cluster: https://journal.r-project.org/articles/RJ-2023-015/
- Percentile bootstrap and explicit method/confidence/resampling parameters: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html
- Linear empirical quantile uses index q(n−1): https://numpy.org/doc/stable/reference/generated/numpy.quantile.html
- Reproduction depends on RNG, build/environment, version and call sequence, not seed alone: https://numpy.org/doc/stable/reference/random/compatibility.html

Actual clinical/statistical owners must still approve the target estimands, design, correlation structure, denominators, thresholds and exact pre-frozen method. A future approved method outside this registry requires another implementation.
