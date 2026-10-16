# Terminal-failed original manual document correction (PRD §8.19.3)

## Bounded behavior

A failed OCR page may participate in the existing document create/grouping workflow when its original remains intact and authorized. The saved text is a human-provided manual entry, including when the original image has no readable text. Metadata-only entry may leave confirmed text empty. This path does not recover OCR content or generate a candidate, clinical record, fact, source anchor, or professional verification.

Admission keeps batch `receiving` and `cancelled` blocked. A failed page must have its exact current OCR task in terminal `failed` state, with immutable task/page/source identifiers and input digest matching. Missing tasks, live leases, any related nonterminal/uncertain task, and any running/uncertain/unfinished page or task attempt remain unavailable. Ordinary queued, processing, cancel-requested and uncertain pages remain blocked.

The normal original availability, original bytes/checksum/size, source version, patient, retention, use authorization, grants, write intent, idempotency and document version checks remain authoritative. Missing source bytes yield `FILE_OBJECT_UNAVAILABLE`; changed/corrupt bytes yield `PURPOSE_CHECKSUM_MISMATCH`. Neither is treated as a recoverable OCR failure.

## Provenance and concurrency

Native document versions retain `confirmed_by`, `confirmed_at`, the human-submitted `confirmed_text`, ordered original-page links and original/page dependencies. An edit records its reason and superseded version without rewriting earlier text. No original OCR status, attempt, raw error, source bytes, or OCR task is marked successful by a manual correction.

Document material admission locks the shared parent batch before selected pages, then related execution rows. Retry uses the same batch/page order. A retry committed first makes the page queued and correction is denied; correction committed first leaves a normal document version whose page membership survives retry. A committed document request remains replayable after retry without creating a second document. Later native candidate presentation resolves current document membership dynamically, and explicit candidate adoption uses that version.

Regrouping applies the same guard to every authoritative locked source page, including pages left unassigned and an all-unassigned/empty-output request. Omitting a page from every output cannot bypass a queued, uncertain or other unavailable state. Committed receipt recovery remains before state admission, preserving exact historical recovery after a later retry.

## Regression module

`backend/tests/test_failed_archive_correction.py` uses guarded synthetic data and generated blank image bytes in an isolated PostgreSQL test database, with `IsolatedSchemaRows` exact cleanup. Fixture creation is atomic. It covers actual HTTP intent/create/read/update/replay/recovery, actor/text provenance, no clinical/OCR side effects, missing versus corrupt original, receiving/cancelled guards, queued/running/uncertain exclusions, revoked grants/use authorization, cross-patient pages, stale versions, source changes after intent, retry preservation, later synthetic candidate adoption and two separate-connection batch-lock race directions.

The 22 methods additionally cover a prepared document intent after retry becomes queued/running, all-unassigned state admission, partial outputs hiding an active unassigned page, a prepared unassignment losing to retry, and successful failed-page unassignment with exact receipt recovery after retry.

Retry/adoption compatibility tests exercise native persistence with an explicitly synthetic pre-admission context/candidate fixture. They do not claim real provider execution, purpose-copy approval, or clinical validity. The lead owns database execution on frozen test snapshots; this change does not run or mutate production services.

## Verified 2026-10-11 (UTC)

The lead's frozen PostgreSQL archive integration run passed **65/65 tests in 90.781 seconds**, including all **22/22** tests in this module, both separate-connection correction/retry races, and regroup unassignment admission/recovery. Snapshot identifier: `23c73a64a91ad3e118c7e3a3ad98208b7965563a3d4f942a4c25f416a241d898`. Evidence log: `original-archive-closeout-round2.log`.

This is targeted synthetic software verification, not the full application acceptance run or clinical/provider validation. Production services and production data were unchanged. Python compilation also passed for the changed implementation and regression module.
