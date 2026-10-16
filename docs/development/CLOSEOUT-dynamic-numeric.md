# Bounded dynamic numeric variables

## Scope

The `dynamic-input-policy-2` contract accepts separately declared numeric variables
through `DynamicNumericVariable`. This is a compatible expansion: old payloads
need no migration and receive no new defaults. The six existing numeric codes
retain their established units. The 2016 classification and MS rule contracts and
calculations are unchanged. Reserved categorical/premise names cannot be reused
as numeric variables.

This is a generic software capability. It does not define which additional blood
cell measurements are clinically appropriate, clinical cutoffs, or clinical
approval. Synthetic test concepts, units, sample values and thresholds are only
software fixtures and must not be copied into clinical configuration.

## Governed operator setup

The existing controlled configuration JSON editor exposes the generated schema.
No family-facing variable selector is added.

Each additional numeric variable must explicitly specify:

- A unique variable code and concept code, decimal value type, nonblank normalized
  unit, mapping configuration UUID, accepted contexts, and empty categorical values.
- Exactly one comparison definition, including allowed method pairs, negative
  baseline behavior, precision and rounding.
- A variable window referring to that same mapping configuration.
- Trigger/reset/retrigger conditions as appropriate, with explicit thresholds,
  direction, sample counts and timing. Absolute thresholds use the declared unit;
  relative thresholds use `%`. Incorrect units fail before input selection.

The referenced `unit-mapping-2` configuration must be enabled and possess current
approval and implementation validation. Its mapping must exactly match the
fact's concept, source revision, raw unit and method and produce the variable's
declared normalized unit. A UUID or nonblank string alone is not approval.
Existing source authorization, professional verification, known-at checks,
sampling independence, exact-value qualifiers and dependency checks still apply.

Available report fields do not automatically become dynamic inputs. Missing
facts/mappings and non-comparable facts cannot become positive signals. An
unconfigured condition is rejected. Computation does not confer publication
authority; a matching independently governed release is still required.

## Implementation validation and persistence

Backend Python and alert schema bytes are part of the implementation fingerprint.
Existing validation evidence does not carry across this change: affected
configurations need legitimate validation for the new implementation before
execution. Stale task inputs, changed approvals and revoked mappings are rejected
when work is executed. Do not rewrite previous clinical validation artifacts.

The existing configuration API, task/input snapshots and persistence columns
already store explicit string codes and mapping identities, so no database
migration or new API schema version is required.

## Software regression labels

- `tests.test_dynamic_numeric_contract`: strict contracts and the current
  `dynamic-2` computation; pure tests do not connect to a database.
- `tests.test_dynamic_numeric_integration`: isolated synthetic approved mappings,
  operator creation options, current task execution and persisted provenance,
  mapping mismatch/revocation, unknown inputs, threshold-unit checks, stale
  artifact evidence and altered task snapshots. Uses the real admission and
  execution paths in a dedicated Django test database. New numeric rules have no
  matching publication release and do not publish family alerts.

Passing these software tests is not clinical validation.
