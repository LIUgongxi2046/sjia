# S12-B summary snapshot display and raw-value plot

2026-10-11 development closeout. The plot is a descriptive display of the exact summary inputs, not a clinical trend qualification or risk assessment.

## Immutable input and compatibility

- New summary snapshots capture `source_definition_revision_id`, the registered `SourceRevision` contract. This is not a physical report/file/document identity. Separate report and record versions retain separate point identities and may share a source definition.
- `trend_projection` is derived solely from the hash-checked stored summary snapshots. No live analyte, record, mapping, or newly saved observation is read to enrich an old summary.
- New output identifies `summary-raw-trend-1`, its snapshot checksum and `clinical_interpretation=false`. Legacy clients may omit the optional projection; old snapshots without source-definition metadata keep explicit non-comparability.
- Changed epochs/configuration/visibility invalidate the summary as before. A narrowed field grant hides both items and the entire projection, including provenance. Revoked relationships and inactive patients are denied by the actual read path.

## Plot and connection rules

Each exact concept/field-code pair has raw numeric points and separately declared adjacent segments. A known finite exact (`eq`) value with a unit and resolved minute/second medical instant may be positioned. Partial/unknown/unresolved medical times are retained in the raw table, never replaced by save time.

Connections require strict medical-time ordering with equal precision, nonempty identical raw units/methods/source-definition revisions/source organizations/source types, and complete plottable endpoints. Duplicate times and unknown/censored values break adjacent segments. An unpositioned time anywhere in a series disables all connections because its location between known samples is unknown. No implicit unit conversion, interpolation, missing-value imputation, medical threshold or trend alarm is added.

Mixed or unknown raw units do not share a numerical axis. The UI falls back to the raw table; unsafe numerical display coordinates do the same. A single point stays a single point. All source links address fixed record versions. Original source-time endpoints and per-row time details are visible.

## Display structure and evidence

The original blue summary cover, unique record counts and record-kind sections read only from saved items. Unknowns, source status, professional verification and incomplete provenance remain explicit. A missing/invalid summary never produces fabricated values, a curve, or a record count from live data.

Focused coverage: `tests.test_summary_snapshot_trends`, existing `tests.test_summary_async_closeout` and `tests.test_T25`; `check-summary-trend-contract.mjs`, `check-summary-snapshot-presentation-dom.mjs`, and the existing summary polling/recovery checks. Actual final-source totals and synthetic browser observations are recorded separately in the dated validation report; this document does not assert clinical effectiveness.
