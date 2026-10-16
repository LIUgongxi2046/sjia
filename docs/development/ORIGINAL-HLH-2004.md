# Independent HLH-2004 reference capability

Status: implemented software capability with synthetic verification. This is not clinical validation, diagnosis, clinical approval, real-patient acceptance or a production release. No default configuration, clinical threshold approval or automatic family publication is created.

## Original requirement and adopted source boundary

The original organizer PRD includes a separate HLH-2004 static reference path. The implementation is separate from 2016 MAS classification, MS scoring and dynamic warnings. It does not require that five criteria be reached before another warning may occur.

Primary source: [Histiocyte Society HLH-2004 protocol, Table 2, printed page 15](https://portaldeboaspraticas.iff.fiocruz.br/wp-content/uploads/2020/07/HLH-2004-protocol1.pdf), checked 2026-10-11. The compatible article-table option is [Henter et al. 2007, Table I, DOI 10.1002/pbc.21039](https://onlinelibrary.wiley.com/doi/10.1002/pbc.21039). A literature reference does not supply a clinical approval or an input/window policy.

The software requires an explicit source-table selection. Its numeric comparisons are locked to that table: Hb <90 g/L, or <100 g/L at age <28 days; platelets <100 ×10^9/L; neutrophils <1.0 ×10^9/L; fasting TG ≥3.0 mmol/L or the table's separate native-unit option ≥265 mg/dL; fibrinogen ≤1.5 g/L; ferritin ≥500 ug/L; soluble CD25 ≥2400 U/mL. It never silently converts TG units or sCD25 assay units.

The eight conditions include professionally confirmed fever, splenomegaly, at least two of three cytopenias, the TG/fibrinogen OR, specified-tissue hemophagocytosis with the table's malignancy qualifier, laboratory-reference-based NK activity, ferritin and sCD25. Fever has no invented temperature or duration definition. A professionally confirmed molecular diagnosis consistent with HLH is an alternative path; family history or a raw gene-variant flag is not a substitute.

## Versioned contracts

- `hlh-2004-rule-1`: explicit source/evidence references, source-bound numeric parameters, molecular/fever definitions, count/unknown semantics and timing policy. No active default instance.
- `hlh-input-policy-1`: complete independent bindings for 16 observations, concept IDs, native units, approved unit-mapping configuration IDs, methods, contexts, categorical encodings and semantic metadata sources.
- `hlh-2004-inputs-1`: typed qualified observations or explicit unknown states. Qualified facts carry source, fact, verification and verifier IDs, evidence reference, complete medical interval/precision, availability and verification timestamps.
- `hlh-2004-result-1`: eight criterion states with compound subconditions and fact references, molecular path, lower/upper counts, applicability and a reference-only final result.

`hlh-2004-bounds-1` is an engineering policy requiring governance. It is not attributed to the paper. The lower count is the number of met conditions; the upper count adds unknown conditions. At least-five is met when the lower bound reaches five, not met when the upper bound is below five, otherwise unknown. Molecular OR count uses the same three-valued logic. Unknown applicability remains insufficient; not-applicable context takes precedence. A not-supported reference result does not exclude illness.

Cytopenias use at least two lineages. TG and fibrinogen together still count only once. A non-fasting sample cannot establish a negative fasting-TG condition. Censored comparisons retain their interval meaning, rather than treating the reported bound as an exact value.

## Runtime and provenance

`evaluate_hlh_2004` is a pure offline evaluator. Its output always identifies itself as reference support, with `clinical_use_authorized=false`; that function cannot authenticate clinical approvals. The native adapter supplies facts only after existing environment, account, role, case/purpose permission, source/retention, configuration approval, implementation validation, verification, time, selection and mapping gates pass. The client cannot submit molecular assertions, raw evaluation values or proof objects to the assessment endpoint.

The assessment kind is `hlh`. Only that kind requires an `input_rule_id`. The existing assessment task/intent/outbox/lease/receipt machinery is reused. Configuration proofs, selected facts, compound-input metadata and typed HLH input are included in the snapshot. Results and dependencies are persisted; changed facts, report sample times, permissions or policies invalidate/supersede results under the existing gates.

Capture metadata is bound to actual supported record fields:

- Hb age comes from the configured age-at-sample concept and must share a record version and exact medical interval with Hb.
- Fasting and TG must share the same report/source and exact interval matching the report sample interval. Distinct MedicalTime database rows are preserved, because normal capture creates a separate row per field; equal timestamps do not imply a shared sample across unrelated reports.
- NK reference comes from the verified field's `reference_interval`.
- Hemophagocytosis specimen site comes from the approved categorical-value mapping, limited to the source table's tissues.

Missing/ambiguous metadata is unknown. Date/minute intervals are never collapsed to midnight/another invented exact instant. Whole-interval containment and the explicit cross-laboratory span apply. Sample-bound age/fasting metadata require exact times. These are deliberately conservative software decisions: missing age leaves Hb unknown even when the value is below both source cutoffs; an untested molecular path leaves the combined result insufficient even if all eight phenotypic conditions are known negative. The source table does not itself mandate these missingness rules. Report and report-sample MedicalTime rows are included in provenance/dependencies.

The new migration `assessment.0003_hlh_reference` adds an optional independent output and kind/separation constraints, without backfilling or rewriting existing assessments. No rule/configuration is enabled by the migration. Existing assessments use the original classification/MS computation. Historical implementation/proof fingerprints still require the usual validity checks after an implementation change.

## Support UI and family boundary

The support assessment page has a dedicated HLH-2004 tab and approved plan selection. It renders eight criteria, compound details, confirmed count, possible upper count, molecular alternative, rule/source checksums and non-diagnostic limitations. The browser adapter verifies count/path/compound consistency, source fact identities, and separation from classification/MS. Switching tabs does not reuse HLH output as an MS score.

Direct HLH details use the existing support-only API. Family output remains governed by separately approved content and release policy. No genotype detail is rendered, no guardian editing of medical cutoffs is added, and HLH is not made a prerequisite for other warnings.

## Verification evidence

- `backend/.venv/bin/python backend/manage.py test tests.test_hlh_2004 --noinput`: 33 tests passed on 2026-10-11, including native equalities, censored comparisons, all 27 cytopenia state combinations, all nine TG/Fbg combinations, 27/28-day boundary, independent molecular path, unknown bounds, provenance and timing.
- `node scripts/check-hlh-reference.mjs` (from guardian-h5): six synthetic nonvisual groups passed. They use the production schema, adapter, component, page and creation hook, including an actual hooked submit with the selected binding.
- `npm run build`: passed; the existing large-bundle warning remains.
- `node scripts/check-assessment-schemas.mjs`: all 23 assessment schemas compiled.
- `node scripts/check-response-contracts.mjs`: passed legacy adapter/hook contract regressions.
- `makemigrations --check --dry-run`: no model changes missing migrations; the local default DB was unavailable for that command’s migration-history check. The independent centralized test runner subsequently applied `assessment.0003_hlh_reference` successfully.
- `tests.test_hlh_integration`: all 11 then-existing native tests passed in centralized round 3 on 2026-10-11, within a 20-test run completing in 174.813 seconds. This verifies real synthetic configuration qualification, eight-condition persistence/readback, dependency invalidation, permission/approval rejection, family 403, and unknown metadata. The initial run exposed fixture deficiencies (missing API audit policy and inherited report times without explicit item provenance); those fixtures were corrected without weakening production guards.
- The subsequent centralized round 4 passed all 12 HLH native tests plus all 33 pure tests, within a 61-test run completing in 262.683 seconds. This includes `test_explicit_nonidentity_normalization_preserves_censored_fibrinogen_boundary`, an explicitly approved synthetic g/dL→g/L mapping with a censored ≤ boundary. It does not certify approximate TG cross-unit conversion. Final assembled regression evidence is recorded separately.

No external provider, real clinical record, commit, push or deployment was used for this work. UI DOM tests use synthetic geometry for focus handling; they do not prove browser/device pixel fidelity.
