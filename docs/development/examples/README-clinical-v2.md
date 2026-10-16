# Synthetic replay capture example

These three files contain fictional software-only inputs. Every UUID, task,
source reference, checksum, patient, event and label is an illustrative value.
They are not saved application tasks, approved evidence or clinical judgments.
The runtime bridge must reject these files unless separate genuine qualified
sources and exact saved replay bindings exist; this example grants none.

## Reproduce the arithmetic only

From the repository root, with the existing backend environment:

```sh
backend/.venv/bin/python backend/tests/evaluate_clinical_capture.py \
  --plan docs/development/examples/clinical-synthetic-v2.plan.json \
  --capture docs/development/examples/clinical-synthetic-v2.capture.json \
  --output /tmp/sjia-synthetic-clinical-report.json
```

The report is explicitly `CALCULATED_FROM_DECLARED_CAPTURE`,
`NOT_VERIFIED_BY_PURE_EVALUATOR`, and `clinical_validation=false`.
It contains three observation periods, distinct static/dynamic task hashes,
first/repeat prompts, an unknown-label period and separate patient partitions.
No database, provider, original clinical case file or network is used.

## Qualified runtime adoption

1. Fix the plan, criteria, schedule, labels, event matching windows, subgroup
   assignments and explicit prompt pairings independently before freezing the
   dataset. Assign each evidence artifact its real approved SourceRevision URI.
2. Validate against `clinical-measurement-plan-v2.schema.json`,
   `clinical-capture-v2.schema.json` and
   `clinical-explicit-pairings-v2.schema.json` in `backend-design/`.
3. Each schedule point explicitly fixes static/dynamic rule IDs and their runtime,
   window and verification policy roles, plus the dynamic input-rule ID. Do not
   choose alternatives after observing replay outputs.
4. Use UTC `Z` timestamps consistently. The pairing artifact must equal the
   canonical Event/Prompt JSON shape in the capture, including exact IDs, times,
   outcomes and matches. Canonicalize timestamps to UTC before serialization;
   do not switch equivalent `+00:00` and `Z` strings between artifacts.
5. Freeze the exact dataset version, independent labels, patient partition,
   observation periods, all task-used configurations and implementation digest.
   The common corpus digest covers the union of actual candidate facts for each
   paired step. Each path retains its own task-input and saved-result digest.
6. Execute actual static/dynamic tasks in replay mode after freezing. Bind their
   exact saved task IDs, rule IDs, used facts, output digests and cutoff times.
   Register the resulting capture as approved `evaluation_capture` evidence for
   `clinical_evaluation` only through the existing controlled source process.
7. Supply the real capture URI in the optional `clinical_capture_ref` field of
   the controlled evaluation run. Admission, execution and reads recheck all
   proofs. Nine inventory results remain available alongside the calculated
   clinical-metrics report. Missing capture means inventory-only NOT_RUN.

Only an explicitly qualified all-scheduled-steps/first-positive-then-repeat/
period-boundary-reset plan is supported. Live episode repeat/reset semantics
and patient-correlated confidence intervals are not inferred. A calculated
report never grants clinical release approval.

## Explicit interval software example

The additional four-file `clinical-interval-synthetic-v1.*.json` example declares a fully explicit candidate protocol and generates non-null synthetic intervals. See [CONFIGURABLE-INTERVALS.md](../CONFIGURABLE-INTERVALS.md) for the command, frozen numerical identity and limitations. The original three-artifact example above remains unchanged and does not infer an interval method. None of these fictional files authorizes runtime or clinical use.
