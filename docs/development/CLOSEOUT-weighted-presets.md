# Versioned weighted dynamic rules and named sensitivity presets

Scope: software support for original V1 PRD §§4.3–4.5 and the approved role split:
professionals/administrators maintain and approve parameters; guardians select
approved named presets only. No clinical parameter values or clinical defaults
are supplied by this implementation. Synthetic test configurations are never
clinical presets. Literature reconciliation is evidence research, not approval.

## Contract

`alert-trigger-1` is unchanged. `alert-trigger-2` retains the same scope, windows,
conditions, publication and episode bindings, but requires:

- `sensitivity_preset`: version `dynamic-sensitivity-preset-1`, an explicit
  `preset_key`, `display_name`, and `description`. The ConfigVersion ID and
  checksum identify the actual immutable approved rule. A matching name is not
  authority to substitute another version or override parameters.
- `combination`: version `weighted-combination-1`, operator `weighted`, explicit
  members, denominator, core gate, threshold fraction, minimum known fraction,
  unknown policy, and evidence references. Every field is mandatory.
- Every condition is referenced exactly once with a positive decimal-string
  weight and explicit `core` or `general` classification. At least one core
  member is required. Weights need not sum to one; the full denominator is
  calculated from their exact values. Duplicate, missing or extra references
  are rejected.
- `denominator = all_configured_weights` and
  `unknown_policy = bounded_no_imputation` are explicit versioned semantics.
- `core_gate.operator = all` requires `minimum_met = null`;
  `core_gate.operator = at_least` requires an explicit positive minimum not
  exceeding the number of core members. No gate is inferred from the labels.
- `threshold_fraction` and `minimum_known_fraction` are explicit decimal strings
  greater than zero and at most one. They are not percentages, MS coefficients,
  publication priority, population sensitivity estimates, or fixed MAS/HLH
  criteria. The software does not select their values.

Legacy reset/retrigger policies remain on `alert-lifecycle-2` and retain their
original three-valued logical combinations. Weighted lifecycle semantics have
not been silently added. Existing six numeric variables and the separately
approved dynamic numeric extension retain their existing qualification, units,
methods and windows. The 2016 MAS and MS calculations are unchanged.

## Exact three-valued evaluation

Let D be the sum of every configured weight, M the weights of met conditions,
U the weights of unknown conditions, and K = D − U. The evaluated quantities are
exact rational numbers, with no rounded comparison:

- Lower weighted bound: M / D
- Upper weighted bound: (M + U) / D
- Known-weight fraction: K / D

Unknown conditions remain in D. Their absence from M is only the lower-bound
arithmetic; their facts remain `unknown`, never zero/negative/normal. No observed
subset is renormalized.

The weighted threshold gate is met when its lower bound reaches the approved
threshold; it is not met when even the upper bound cannot reach the threshold;
otherwise it is unknown. The coverage gate is met when K/D reaches the approved
minimum, otherwise unknown. The core gate uses definite met count and possible
met count; unknown members cannot count as known met.

The overall result is the three-valued AND of the threshold, coverage and core
gates: a definitive not-met gate makes the result not met; otherwise an unknown
gate makes the result unknown; otherwise it is met. Thus an approved lower-bound
and coverage policy may allow an unknown general member while already-known
members conclusively satisfy both gates. Required unknown core evidence cannot
be bypassed by sufficient general weight. `not_met` is a rule evaluation outcome,
never a clinical exclusion conclusion.

All input and derived rational components are bounded by the runtime's approved
`max_decimal_digits`; member count/depth use the existing runtime/input policy
limits. Invalid bounds or missing weighted arithmetic limits are rejected.

## Governance and traceability

V2 uses the same enabled configuration, source authorization, professional
verification, evidence, approval, current implementation validation, and exact
release bindings as V1. Nested weighted evidence is checked at known-at and now.
No approved preset is shipped or automatically enabled.

The evaluation task freezes the selected preset's name, ConfigVersion ID and
checksum along with the existing configuration approval/validation proofs. Each
weighted output records every member's class/weight/state, the full denominator,
met/known/unknown weights, exact lower/upper/known fractions, each gate outcome
and reason codes. The public dynamic protocol accepts this separately versioned
explanation while preserving the original logical output shape. Guardians do
not gain access to the professional task's numerical input contract.

A met result alone cannot publish. An independently approved, matching release
is required; replay still cannot publish. Current task execution and read-back
revalidate configuration state, implementation evidence and immutable snapshots.
Changing the implementation requires legitimate new validation evidence; this
work does not rewrite clinical validation artifacts.

## Verification

- Pure suite: `tests.test_dynamic_weighted_contract` (21 tests, including optional HLH signal and schedule contracts) passed.
- Combined weighted/numeric/2016/MS/legacy dynamic pure regression (61 tests)
  passed locally. No database was accessed by these `SimpleTestCase` labels.
- Added `tests.test_dynamic_weighted_integration` (14 isolated synthetic tests)
  for the coordinating frozen PostgreSQL runner. Covers real qualified task
  execution, explanation persistence, matching-release publication, unknown
  evidence, immutable preset switch/rollback, current options, revocations,
  tampering, implementation changes, replay and rejection of numerical task
  overrides. Database result is to be recorded by the coordinating runner.

A software pass is not clinical validation, a real-family acceptance result,
permission to enable a clinical preset, or production publication approval.

## Support explanation and optional HLH composition

The support result now shows the frozen approved preset identity/checksum and
exact weighted detail, without converting decimal weights or rational integers
to JavaScript numbers. Invalid results conceal both preset metadata and weighted
explanation. The read adapter rejects mismatched rule identity.

`node scripts/check-weighted-dynamic-dom.mjs` passed 7 actual schema/transport/DOM
checks, including beyond-safe-integer fractional display and unknown members.
TypeScript checking passed. No visual/device acceptance is inferred.

An explicit `hlh_reference_met` condition can refer to a separately qualified
HLH result; supported reference conditions map to met, unsupported to not met,
and missing/insufficient/inapplicable reference results remain unknown. No HLH
condition or gate is added automatically. An HLH scheduled static assessment
requires its own approved input-rule identity and watches that identity when
configuration changes trigger recalculation. Legacy scheduled static plans
continue to omit this field.
