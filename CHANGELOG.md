# Changelog

## 0.2.0 — release candidate (2026-11-10)

Prepared against `4068a2ba1e33c26f845f6e74ccb295919403cc70`. This section describes the candidate; it does not announce a published tag or completed platform/provider acceptance.

### Added

- Shared offline batch preflight with per-row errors and request/image totals.
- Dry-run-by-default `imageai-batch` CLI, structured sample batch, and Python package metadata with optional desktop/test dependencies.
- Portable batch export with relative session paths, exact image copies, current/historical references, SHA-256 manifest, and paginated contact sheets.
- Durable pre-call/terminal receipts, idempotent recovery of verified results, atomic saves, and last-valid backups.
- Interrupted status for uncertain provider outcomes and explicit review before desktop retries.
- Capability-driven output format/background/reference controls, and OpenAI reference-image edits.
- Python regression coverage, seven browser smoke scenarios, and a proposed Linux/macOS CI workflow.

### Changed

- Provider catalogs use verified GPT Image and stable Gemini model IDs. Unsupported saved IDs are retained for explicit replacement, rather than silently redirected.
- Shared HTTP transport retries explicit 429 responses with provider delays; ambiguous timeout/server outcomes are not resent automatically.
- Image files are decoded/validated, atomically published without replacement, and retain their actual format and SHA-256.
- Queue admission freezes prompts/settings/reference bytes, guards duplicates, counts matching results for missing-only generation, and preserves local quota history when the queue is recreated.
- Backend state mutation and session changes use explicit locking/lifecycle checks; clients share generation event reduction.
- React controls respect capabilities, preserve focused drafts during polling, show backend errors, and support preflight/portable export.
- PySide queue events are delivered on the GUI thread; refresh no longer creates row overrides or erases loaded settings; settings now have a usable sidebar/collapsible row layout.
- SwiftUI delegates structured parsing/import to the backend and is updated for RPC errors, selection binding, and bounded polling/startup. Native verification remains required.

### Fixed

- Weighted quota requests larger than the configured minute budget crashing the limiter.
- Pending cancellation still reaching a provider; quota-wait shutdown hanging; late/partial images losing their session references.
- Worker loss after generation-log failures and old queue callbacks affecting replacement sessions.
- Image/name collisions overwriting exports, incorrect expected image counts, and stale batch previews losing identifiers.
- Loaded portable images rejected by the local image endpoint, historical references missing from bundles, and malformed receipts blocking restore.
- Undo reopening already consumed receipts and causing removed output references to reappear on restart.
- React lockfile incompleteness blocking `npm ci`.

### Compatibility notes

Session versions 1.0 and 1.1 remain readable. New fields have defaults. Old model selections and legacy settings can require explicit replacement/reset during preflight. Legacy images without request fingerprints are preserved, but are not assumed to satisfy a new missing-only request. The previous hidden `GEMINI_ENABLE_SEARCH` option is removed; search grounding is not part of this adapter contract.

Quotas are local to one process, and the application is a local workspace. Shutdown cannot cancel work already accepted by a remote synchronous provider. Durable receipts improve crash recovery but do not provide remote job status or exactly-once billing. Build/runtime verification on target platforms and one controlled request for each provider/model being advertised remain release gates.
