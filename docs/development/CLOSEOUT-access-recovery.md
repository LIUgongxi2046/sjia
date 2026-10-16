# Authentication and scoped access recovery

## Observed defect and bounded correction

The local browser registration succeeded, but a later relation-list read failed
because the isolated demo lacked its synthetic access-audit policy. The UI
misreported the whole flow as an identity failure. Authentication and patient
authorization are now separate states: a verified account remains signed in
while a failed relation read blocks patient content and offers a permission
retry or logout. A successful registration is not repeated automatically.
Passwords are cleared after the authentication response is accepted.

Pending verification hides patient content and blocks record submission,
autosave and scoped result adoption. A failed verification discards local
clinical component state and says so. No clinical draft is added to persistent
browser storage. Existing opaque, actor/patient-bound request identifiers may
recover the original request from the server; recovery is not authorization and
does not permit a new submission while the original result is uncertain.

## Read-only continuity contracts

`SessionView.session_binding` is an optional, nullable, domain-separated HMAC
comparison marker derived by the server from the current session and identity.
The raw session key is never exposed. The marker is never accepted as a login
credential or permission. `expires_at` uses the persisted Django session deadline
rather than recalculating a sliding deadline merely to construct the response.
A later legitimate middleware save can change the persisted deadline; this does
not change the configured session-lifetime policy.

Relation list/detail responses add two optional read-only fields:

- `authorization_scope_fingerprint`: canonical SHA-256 of permission metadata.
  The projection includes current actor/account/epoch, patient availability,
  environment gate, relation identity/version/epoch/windows and the actor's
  patient-specific grants, including their purpose, field and record scope,
  identity, assignment, validity and revocation. It conservatively includes
  relevant relationless and other-relation grants. Clinical values, evidence
  text, credentials and patient data epochs are excluded.
- `authorization_scope_valid_until`: the next known future start, expiry or
  scheduled revocation boundary in that projection. Explicit null means no
  future temporal boundary is known. It is not a promise that permission lasts
  until that instant; unplanned revocation can happen earlier.

Actions, fingerprint and boundary are derived from one database statement.
Missing/disabled environment, unavailable patient or inactive actor produces no
apparent action capability. These are UI continuity hints: every real operation
still uses the existing server authorization policy.

## Frontend recovery boundaries

Only the same verified session, patient and unchanged known authorization scope
may preserve the mounted in-memory form across a successful recheck. Changed,
removed or unknown identity/scope resets local clinical state. Patient selection
must refresh the server session and relation scope before adopting the target.
Known expiry clears access; logout and authoritative identity/patient changes
also remove stale recovery URL identifiers. Failed logout remains visible and
retryable rather than implying that the server session has ended.

Live identity/generation checks cover record submission and draft recovery.
Additional scoped guards cover the existing observation field-configuration,
photo-upload/OCR-adoption and voice-entry pipelines, including asynchronous
helper gaps and parent candidate-adoption callbacks. A suspended operation does
not automatically replay an uncertain upload or provider mutation. Any permitted
resumption uses the existing server status/recovery contracts.

## Verification and limits

Targeted backend modules are `tests.test_relation_authorization_scope` and
`tests.test_session_continuity`; frontend regression scripts exercise real React
components with deferred synthetic responses. Final command results belong to
the delivered validation summary, not to a claim inferred from this design note.

There is no push channel for unexpected server-side revocation. New server
requests remain policy-gated; a client learns a changed scope through a fresh
relation read, while known time boundaries can be enforced locally. The
regressions establish the named flows, not an audit of every asynchronous
component in the application. Reload/navigation recovery relies on the existing
server request directory and bounded URL identifiers, not guaranteed retention
of unsaved local clinical text. No real provider, microphone or clinical data is
needed by these engineering tests.
