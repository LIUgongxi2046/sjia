# T32 / T59 deterministic evaluation closeout

## Confirmed gap and boundary

The existing evaluator depends on a historical dataset version and fixture-only
capture. Before closeout, runtime evaluation always returned inventory and marked clinical
metrics NOT_RUN. The bridge below now adopts qualified saved replay captures. PRD 9.2 requires independently approved matching windows,
labels, patient partitions and a patient-correlated uncertainty method; these
cannot be invented by development.

This slice adds a portable, versioned measurement-plan/result-capture contract
and a deterministic evaluator. Explicit independent event/prompt pairings are
checked, never inferred. Unknown labels and unassessable runs remain separate
from negatives. Fixed dataset/configuration/code identities, common static and
dynamic inputs, observation bounds and known_at eligibility are required.

The pure evaluator is an engineering tool, not an approval authority. Its output
always states clinical validation is false. A production run must independently
qualify the plan/capture evidence and verify saved replay executions before
adoption. Missing confidence-interval protocol remains an explicit limitation;
no default statistical method or success threshold is selected.

## Verification / dependencies

Synthetic fixed answers cover denominators, unknown exclusion, first/repeat
prompts, same-patient matching, time-window boundaries, future facts, duplicate
identities, cross-partition leakage and mismatched versions. No live model,
patient data or clinical approval is required or created for those tests.

Runtime adoption is fail-closed. Schema availability and portable calculation
do not close real clinical acceptance or imply all T59 work is complete.

## Runtime adoption plan (before edits, 2026-10-11)

1. Add v2 paired replay steps. Separate common frozen corpus digest from each saved task's input digest and result-view digest. Each step has its own assessment/knowledge cutoff and source/fact proof list.
2. Require an independently qualified, pre-frozen measurement plan to select an explicit schedule, static criterion, dynamic criterion, assessability rule, occurrence policy and reset boundary. Implement only declared positive-step/period-boundary semantics; unsupported protocols fail closed. Do not infer a clinical CI method.
3. Bind capture SourceRevision and exact contents to existing frozen dataset/labels/partitions/periods/configuration/code. Reuse current evidence/file/patient admission, then re-read saved replay assessments via actual services. Derive used facts and absence of live publications from saved rows.
4. Adopt calculated report and qualification proofs into task execution evidence, bind hashes, and re-check on reads. Preserve the nine existing inventory results. Missing capture leaves an explicit inventory-only limitation.
5. Extend controlled run input and result schema/types/UI additively. Test arithmetic/contract negatives, qualified admission/revalidation, and actual saved replay binding with synthetic data only. Clinical validity stays false.

## Implemented development capability

- `clinical-result-capture-2` and `clinical-measurement-plan-2` require ordered, independently fixed paired steps. Common corpus digests bind complete actual candidate universes; each task keeps its own execution-input and result digest. Used facts are derived from saved snapshots, not accepted from declarations.
- The runtime reuses existing frozen dataset, purpose-copy, file, independent label, patient partition, current service/case permissions and qualified configuration admission. Plan, criteria and explicit pairing artifacts must precede freezing; actual replay tasks must follow it. Exact captured times, observation denominators, rule/code versions, saved result views, task attempts and absence of replay publication side effects are checked.
- Captures are adopted into immutable run/task evidence and requalified on read. Missing captures retain inventory-only NOT_RUN; invalid or unsupported captures are refused. The existing nine inventory results remain intact.
- The controlled support UI accepts a qualified capture reference and renders static/dynamic ratios, actual denominators, first/repeat counts, relative first-alert times, unassessable/unknown states, separate partitions/subgroups and exact task/corpus/input identities. It is read-only and never claims clinical approval.
- Supported sequence policy is explicitly `all_scheduled_steps_completed`, `first_positive_then_repeat`, `period_boundary_only`. Other cadence/reset/occurrence policies need independent specification and implementation; a generic live-episode interpretation is not inferred. A single task may produce at most one prompt. Any incomplete scheduled step excludes that mode's whole period from the assessable denominator, while every captured prompt is still structurally validated.

## Verification and acceptance separation

- Local pure tests: `tests.test_clinical_metrics_closeout` + `tests.test_clinical_metrics_v2`: 26/26 pass.
- Local `npm run typecheck`: pass. `node guardian-h5/scripts/check-clinical-metrics.mjs`: six DOM/contract groups pass, including exact ratio denominators, null values, partition separation, different execution hashes and rejection of clinical/CI approval claims.
- Coordinated DB label `tests.test_clinical_capture_adoption`: five tests submitted. These use actual qualified temporary evidence and actual static/dynamic replay workers/readbacks. The already-frozen dataset boundary is represented explicitly by namespace inputs; this tests adoption, not the separate purpose-copy/freeze API. All four original positive/tampered/revoked binding tests pass in immutable round14 (`.qa-work/closeout/target-round14.log`,16/16 overall). A subsequent exact per-step policy-role binding regression makes five tests; the final rerun is pending.
- Clinical acceptance is separate: actual authorized external clinical data, independently approved matching windows/outcome labels/criteria, population-specific thresholds and patient-correlated confidence-interval method are not supplied by engineering. Reports always retain `clinical_validation=false`. Legacy reports retain null intervals; the later explicit CI capability is documented in `CONFIGURABLE-INTERVALS.md`, with no default method and no clinical-method approval. These external acceptance inputs are not counted as development test passes.

### Exact predeclared configuration roles

Each scheduled point now names both rule IDs and both paths' runtime, window and verification policy IDs, plus the dynamic input-rule ID. Missing roles are rejected; an actual saved task cannot substitute another approved configuration after the plan freezes. Population, therapy, clinical-finding, late-result, mapping, reset/retrigger and condition-level verification roles are transitively fixed by those exact immutable config payloads. Every task-used config proof must match the frozen ID/checksum set and implementation artifact. The test suite separately rejects a missing role, selection of another allowed rule, and a saved task swapping its window policy.

The strict independent-pairings schema and reproducible fictional three-artifact example are in `backend-design/clinical-explicit-pairings-v2.schema.json` and `docs/development/examples/README-clinical-v2.md`. All example artifacts validate and reproduce declared-only metrics without database or network access.

### Final bounded applicability guard

A completed static task may report `not_applicable`, including an MS result with a null threshold decision. The current qualified plan does not specify how to assign those cases to a clinical denominator. Runtime adoption therefore rejects them with `CLINICAL_APPLICABILITY_POLICY_UNSUPPORTED`; it does not count them as negative. Actual insufficient results remain explicit unassessable states, while applicable completed negative results remain measurable. A real synthetic static replay with a known-false premise tests this refusal.

Immutable round17 passes all five exact saved-replay/policy-binding tests. The final applicability guard adds one actual replay refusal and one focused contract test, bringing the module to seven tests; these await the final full-suite snapshot.

## Final evaluation verification

Immutable full-round2 passes all seven final capture/adoption/applicability tests, including actual qualified static and dynamic replay results, configuration-role swaps, revoked proofs and completed/non-applicable refusal. Evidence: `.qa-work/closeout/full-round2.log`. The aggregate run has707 tests, five unrelated legacy correction fixture failures, one unrelated live-HTTP fixture error and three external clinical-input skips; it is not a whole-project pass. Evaluation development checks are26 pure tests, seven adoption/contract tests and six UI DOM/contract groups, plus TypeScript checking and the reproducible three-schema example. External clinical and device acceptance remain separate.


## Final assembled engineering result — 2026-10-11

The immutable final default Django suite ran 735 tests in 407.181 seconds:
732 passed, 0 failures/errors, 3 explicit external clinical-input skips (original
T27 real-case tests). Referenced historical/manual/device/provider/clinical
acceptance checks are not thereby counted as executed. Application and test
source bytes match the final tested snapshot. The 14 selected nonvisual frontend
scripts, TypeScript/production build and 18 offline model-contract checks pass.
T26 database-content erasure remains a separate unimplemented owner-decision
boundary; no all-features-complete or clinical-acceptance claim is made.

## Explicit interval engineering update

The configured candidate capability, bounded execution, versioned report-3 section, pre-freeze approval binding and saved-result revalidation are documented in [CONFIGURABLE-INTERVALS.md](CONFIGURABLE-INTERVALS.md). The generic claim that interval arithmetic remains unimplemented is superseded for `patient_cluster_percentile_v1` only. Earlier counts above are historical evidence and do not validate this new slice. Actual clinical acceptance remains separate.
