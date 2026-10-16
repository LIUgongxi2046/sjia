# Fixed-workflow model invocation governance

These routes remain fixed extraction workflows, not autonomous agents or diagnostic systems:

- Synchronous report/therapy OCR keeps its existing `{"candidates": ...}` response; it now registers an internal task/attempt and reserves the approved runtime budget. Identical unresolved requests are rejected under the admission gate even after a prompt/schema rollout. Completed identical input replays its validated receipt only after current authorization/configuration/purpose-copy checks, without another provider call. A known completed receipt under a stale configuration is ineligible for replay; a new request must pass fresh current admission before calling again.
- `recognition-1` reserves all pages before starting and records each paid call independently.
- Archive page OCR and confirmed voice extraction use the same invocation contract, identity checks and deadline-aware pre-call marker.

## Captured evidence and boundaries

`invocation.py` fingerprints the actual system/prompt template, current validation schema, fully rendered submitted messages, and model/generation parameters. Templates and validators are captured at task admission and checked before execution and adoption. The durable pre-call marker also compares its contract and provider checksum to the task snapshot. Hashes are traceability evidence, not clinical approval or a provider/model approval grant.

The requested model is taken from the qualified provider configuration. The returned model must exactly equal that configured identifier. Deployment IDs, aliases and family-prefix matches are not implicitly accepted. A provider returning a different identifier will have its receipt retained but its candidates rejected. Supporting aliases requires a separately reviewed explicit authorization/configuration design; this patch does not invent one.

Raw provider receipts are saved before output parsing, model-identity rejection and usage-cost validation, including malformed response envelopes. Returned usage is priced using the captured configured rates. Missing or invalid usage remains unknown; reservations are not silently treated as zero. Multi-page cancellation releases only unstarted-page reservations. Started requests without known receipts remain uncertain and cannot be automatically retried. Known late receipts are recorded without adopting cancelled output.

The transport timeout supplied immediately before each call is bounded by the remaining task deadline, execution deadline, client timeout, and captured provider runtime timeout. urllib supplies socket-operation timeouts rather than a provider-enforced wall-clock cancellation guarantee. The current lease is checked immediately before sending, and heartbeat renewal stops at the execution deadline. Receipt persistence precedes heartbeat error propagation. Completion additionally requires current authority. A network timeout or cancellation does not prove that the provider stopped processing or charging; no remote cancellation/reconciliation API is claimed.

## Compatibility and operational prerequisites

- No database migration or clinical approval record is created. The provider-1 configuration schema has a backwards-compatible optional workflow binding field, described below. Extra evidence is stored in existing task JSON/receipt fields.
- Legacy queued jobs lacking the captured governance snapshot fail closed; inspect their prior requests and resubmit only when safe. Do not fill hashes or approvals into old tasks after the fact.
- Legacy OCR now requires a qualified enabled runtime/provider/field configuration and a positive reservation, just as the governed routes do. Setting only an API key/model is insufficient.
- Old `_chat`/`structure_voice_text` live evaluation shortcuts are disabled; they cannot bypass the durable invocation marker. Use the offline regression entrypoint below.
- Configured reservations are an admission estimate, not a provider-enforced monetary spending cap. Pricing, reservation sufficiency, approved aliases if needed, and provider identity compatibility need operational review before real calls.
- Worker/process death cannot be interpreted as provider cancellation. Existing lease recovery/unknown reservations remain fail-closed. No new external provider reconciliation integration, clinical dataset, efficacy result or approval has been manufactured.

## Enabling both report and therapy with the single OCR provider

The runtime schema permits only one provider entry of kind `ocr`. `provider-1` therefore accepts an optional explicit `workflow_field_config_ids` object with `report` and/or `therapy` UUID values. For example, this **illustrative fragment is not an approved configuration**:

```json
{
  "field_config_id": "11111111-1111-1111-1111-111111111111",
  "workflow_field_config_ids": {
    "report": "11111111-1111-1111-1111-111111111111",
    "therapy": "22222222-2222-2222-2222-222222222222"
  }
}
```

The default `field_config_id` is used only when it actually matches the requested workflow kind. A mapping does not grant approval: the selected field configuration and provider configuration must each pass the existing environment-specific checksum, approval and validation checks. Missing, wrong-kind, unapproved, disabled or changed bindings fail closed. No unrelated field configuration is auto-selected. Voice/archive retain their existing default field-config binding.

Operator procedure, using real authorized configuration maintainers and evidence:

1. Obtain qualified enabled `field-config-1` configurations for the required report and therapy workflows. Record their exact IDs and checksums.
2. Create a **new provider configuration version** containing the explicit mapping through the existing configuration JSON interface, `POST /api/support/configs`. The backend's ordinary `validate_payload` path recognizes the optional mapping. Do not modify an already approved payload in place.
3. Complete the existing submit, approval, validation and enable lifecycle under `/api/support/configs/{config_id}`. Normal authorization and genuine evidence requirements still apply; this change does not bypass them or supply evidence.
4. Create and approve a corresponding runtime version whose sole `ocr` provider entry references the new provider ID, then enable it through the normal gate/version workflow.
5. Recheck both routes with an authorized non-clinical test plan before any real provider or patient use. Existing configurations without an applicable approved therapy binding remain blocked until those steps are legitimately completed.

Binding selection captures both field-config ID and checksum into each new invocation, and rechecks them before execution and adoption. These operator steps were documented, not executed against any real approved configuration.

## Reproducible offline verification

From `backend/`, with project dependencies installed:

```sh
python tests/evaluate_model_contracts.py
python manage.py test tests.test_model_invocation_governance
```

The first command needs no database, blocks actual provider HTTP, emits exact contract hashes, and compares them to `tests/fixtures/model_contracts.synthetic.json`. It exits nonzero on a test failure or a hash-baseline mismatch. Updating that baseline requires review of the actual prompt/schema change; it is not a clinical release gate by itself.

The second command adds PostgreSQL persistence tests for pre-call markers, cancellation, unknown costs, late receipts, and malformed provider usage. All fixtures are synthetic. Real provider execution, clinical accuracy, diagnostic performance, real patient validation and clinical acceptance are **NOT_RUN**.

## Optional report-time contract (report-recognition-3)

The default report-recognition-2 prompt and candidate model remain unchanged.
A new qualified `provider-1` version may explicitly set `report_schema_version`
to `report-recognition-3`; ordinary approval, validation, runtime binding and
budget checks still apply. This task has not enabled such a configuration or
called a provider.

V3 separately fingerprints its prompt/schema and returns report sample/result
and per-item sample-time candidates, quoted raw text, and exact stated
precision. Missing or ambiguous associations stay null. Time candidates cannot
supply a resolved instant, invent lower precision components, or inherit a
report-level time into every item. Raw receipts and candidate metadata are
retained. Both sync execution and queued tasks select the frozen contract;
changing provider/version invalidates adoption and completed-result replay.

The report UI shows source text and precision/zone/offset without changing the
form. Each report or item time needs its own adoption action, followed by the
usual confirmation. Queued candidate confirmation strips review metadata; it
never adopts candidate time merely because an item was accepted.

Offline checks: `manage.py test tests.test_report_time_closeout --noinput`
(7 no-database tests), `node scripts/check-report-time-closeout.mjs`, and the
actual report form case in `node scripts/check-registration-dom.mjs`.
These are synthetic software checks, not model accuracy or clinical acceptance.
