# Architecture

ImageAI is a local batch workspace built around one Python generation core. The desktop application and CLI share validation, provider adapters, queue semantics, persisted image metadata and export behavior. The application records what it requested and what it received; it does not automatically repeat a paid request whose outcome is uncertain.

This document describes the implementation in this repository. Provider availability and account quotas are separate from the local application contract.

## Entry points and ownership

| Surface or component | Source | Responsibility |
| --- | --- | --- |
| Local RPC backend | [`app/backend_server.py`](../app/backend_server.py) | Owns session mutation, validation, queue admission, file access and export for the desktop app. Electron starts it on an available loopback port and waits for its readiness signal. |
| React/Electron | [`frontend/src`](../frontend/src), [`frontend/electron`](../frontend/electron) | Provides the primary desktop workspace and its isolated native file/clipboard bridge. The packaged app starts the Python RPC backend. |
| Headless CLI | [`app/cli.py`](../app/cli.py) | Uses the shared Python core directly. Defaults to network-free preflight; `--run` explicitly starts generation. Does not import the HTTP server or restore an old session. |
| Shared model and preflight | [`app/models.py`](../app/models.py), [`app/batch_service.py`](../app/batch_service.py) | Validates settings, derives row overrides, checks provider capabilities and reports planned images and initial request counts. |
| Queue and rate limiter | [`app/queue_manager.py`](../app/queue_manager.py), [`app/scheduler.py`](../app/scheduler.py) | Own admission, immutable jobs, concurrency, cancellation, result registration, receipts and local quota scheduling. |
| Provider boundary | [`app/providers`](../app/providers) | Converts validated settings into provider-specific requests and verifies saved raster responses. |
| Session and reducer | [`app/session_manager.py`](../app/session_manager.py), [`app/generation_state.py`](../app/generation_state.py) | Persist sessions, apply events consistently and recover verified local receipts without submitting new work. |
| Export | [`app/exporter.py`](../app/exporter.py) | Produces collision-safe flat exports and portable project folders with hashes, references, session metadata and contact sheets. |

The CLI writes exclusively beneath its explicit `--output` directory. Desktop and backend state use `~/.imagen/` unless `IMAGEAI_DATA_DIR` selects another data directory. Tests supply isolated directories.

## From imported records to an immutable job

The parser retains prompt IDs, category IDs and source metadata. A shared appended instruction becomes part of each imported prompt. The prompt wrapper is applied when a job's full provider prompt is assembled. Parsed data is validated before a batch is admitted, and provider capability validation rejects unsupported models or options rather than silently substituting them.

A row inherits global settings except for explicit row overrides. Admission takes a deep snapshot of that effective configuration, prompt, attachment records and source metadata. Later edits affect a future request, not one already waiting in the queue.

Reference files require an additional boundary: copying an attachment object does not preserve its file contents. The queue therefore reads and validates the actual raster bytes and writes an immutable copy under:

```text
<data>/<session-id>/images/<row-id>/inputs/<job-id>/
```

Reads are bounded by the application's aggregate 12 MiB inline-input limit, including if a source file grows after preflight. The copied bytes are inspected again before admission. Provider adapters receive the frozen file paths. Image metadata records each reference's copied path, original source path, SHA-256 hash and detected MIME type.

### Request identity

Each admitted job has a new `job_id`. Its image metadata records:

- The original prompt and full prompt actually sent.
- Provider, model and effective generation settings.
- Requested settings before missing-only count adjustment.
- Request time, run index, row/prompt/category IDs and source metadata.
- Frozen reference identities and output SHA-256 hashes.
- Provider request identifiers, usage and response information when returned by the adapter.

A `generation_fingerprint` hashes the prompt, generation controls, provider/model and reference bytes. It excludes the requested image count and interface/export controls. A one-image sample keeps earlier results and counts toward the target when its fingerprint matches. Increasing a target from two images to four can reuse the two matching results; changing the prompt or reference bytes produces a different identity.

The fingerprint identifies retained inputs. It is not a promise that a remote model will reproduce identical pixels, and it is not a provider-side idempotency key.

## Queue lifecycle and notifications

The queue admits at most one active or pending job per row. It registers admission and the queued notification before publishing work to a worker. Repeated admission of the same row while that work is outstanding returns `False`.

State changes append notifications under the queue lock. A single FIFO drain invokes listeners outside that lock. This preserves queued/started/terminal ordering and allows a listener to call cancellation without deadlocking the queue. Position-only events do not trigger session persistence in the primary consumers.

`is_idle()` includes outstanding jobs, queued notifications and a notification callback currently executing. A session switch must wait for the terminal state to reach its consumer, not merely for an HTTP call to return.

The RPC backend attaches a queue generation number to callbacks. Events from a retired queue cannot mutate the replacement session. Session changes and concurrency changes require an idle queue.

| Operation or outcome | Behavior |
| --- | --- |
| Cancel a pending row | Removes its admission immediately; a stale work item is skipped by job identity. The row can subsequently be deliberately admitted again. |
| Cancel active work | Sets a cooperative cancellation event. Quota waits and retry waits stop promptly; an already submitted HTTP request may still run remotely. |
| Stop all | Cancels pending jobs and requests cancellation of active jobs. |
| Stop after current | Preserves already started work; cancels pending jobs and jobs still waiting for quota. |
| Successful response | Registers the returned files and metadata. An empty or short response does not count as a completed requested batch. |
| Partial failure or cancellation | Retains every returned, saved image. A failed or cancelled replacement does not delete the prior good image set. |
| Shutdown | Stops admission, cancels outstanding work and drains work-item accounting. Returns whether all workers exited within the caller's bounded wait. |

A cancellation that races a successful response does not discard the images. The client receives the saved artifacts and a cancellation status explaining that the remote request may still have been billed.

## Quotas and provider transport

The local limiter uses monotonic time and a weighted sliding window. Workers share it within one application owner, grouped by provider ID. Its quota units are:

| Provider | Units reserved for one admitted job |
| --- | --- |
| Mock | Zero; offline generation bypasses remote quota waiting. |
| OpenAI | One initial request. |
| Google Gemini | One unit per requested image, corresponding to its sequential request workflow. |
| Azure OpenAI | One unit per requested image. |

A request requiring more units than the entire configured window fails promptly with an actionable validation error. It cannot wait forever for an impossible admission. Cancellation interrupts a quota wait; changing the configured limit wakes waiters.

The RPC backend reuses the previous limiter when recreating an idle queue. Changing sessions or concurrency therefore does not clear quota history and create an accidental fresh burst.

This is **per-process scheduling**, not account-wide enforcement. Separate application processes do not coordinate their budgets, and limiter history is not persisted across process restarts. Reservations are made at job admission to provider execution; they are not an independent measurement of every remote request or provider billing event. Actual provider limits remain authoritative.

The shared HTTP transport uses bounded connect/read timeouts and disables redirects. It retries only explicit HTTP 429 rejections, honors the returned delay and permits cancellation during the wait. The attempt count and maximum accepted wait are bounded. Connection failures, unreadable successful results, server errors and other ambiguous outcomes are not automatically retried as paid POSTs.

The reducer maps an `outcome_unknown` result to `Interrupted`. Such a row is excluded from bulk missing-only/retry behavior. The user must review the result and explicitly request another generation.

The Electron client asks for explicit confirmation before regenerating an interrupted row, including from a selected or full batch. These interface confirmations do not change the queue's rule that missing-only work must never replay an interrupted request.

Provider catalogs are bounded declarations in source, not remote model discovery. Capability validation controls model options and reference support. Gemini dimensions select supported aspect ratios and resolution tiers; actual saved raster dimensions and format are recorded from the result.

## Atomic sessions and local recovery

Session saving validates a complete snapshot, writes a temporary file, flushes it and atomically replaces the destination. A valid previous version is retained as a backup. A damaged existing file is preserved separately instead of being promoted to the backup. Loading parses and validates the candidate before replacing the active session.

A session containing `Queued` or `Generating` rows is loaded with those rows marked `Interrupted`. Opening a session is not permission to send its prompts again.

### Run receipts

Before calling a provider, the queue atomically writes a run intent:

```text
<data>/<session-id>/images/<row-id>/imageai-run-<job-id>.json
```

The initial event is `started`. The same receipt is replaced with a terminal event after artifacts have been registered and before notifying the client. The receipt includes session/row/job identity, a write timestamp, requested and actual settings, context, the event payload and an explicit list of output paths with SHA-256 hashes.

If the initial intent cannot be written, provider submission does not occur. A log-writing failure after generation does not kill the worker or hide saved images; it is returned as a warning. Terminal receipt failure is also surfaced while keeping the available result payload.

Startup restoration through `SessionManager.restore_last()` reconciles local receipts for the restored session. Recovery:

1. Checks the receipt structure and session/row ownership.
2. Ignores job IDs already recorded in the row's `applied_job_ids`.
3. Requires recovered output paths to remain within the expected session output root.
4. Verifies file hashes before retaining recovered images.
5. Applies the terminal event through the same shared reducer used during normal execution.

`applied_job_ids` prevents an old receipt from restoring images that a later successful replacement or explicit removal already superseded. Recovered duplicate file paths are also suppressed. Partial verification retains only verified files and reports the missing or changed artifacts.

Undo/redo must preserve this fact independently of the editable snapshot. [`UndoManager`](../app/undo_manager.py) keeps a per-session/row ledger of consumed job IDs and merges it into restored history. Undo can remove an image reference, but it cannot make the corresponding receipt look unprocessed again. Restoring a historical queued/generating snapshot produces `Interrupted`, not a new running job.

A `started` receipt without a terminal outcome remains `Interrupted`. There is still a possible crash interval after a provider writes a local image but before a terminal receipt lists it; those files are not automatically inferred to prove a successful run. Recovery cannot query or reconstruct an unreturned remote response. It preserves the evidence available locally and never resubmits work automatically.

Explicitly opening an exported session validates the session and resolves its relative paths. It is distinct from startup receipt reconciliation against the application's own data directory.

## Portable outputs and provenance

The portable export stages a complete folder and publishes it only after its export steps succeed. It contains:

- Original generated image bytes.
- A session using relative paths.
- Current row reference attachments and the frozen references actually used by retained generated results.
- A manifest listing exported files, sizes and SHA-256 hashes, plus missing source files.
- Contact sheets paginated at 40 images per page.
- A short explanation of reopening and reproducing the retained inputs.

Reference snapshots remain useful even when the row's current attachment selection has changed. Path metadata is rewritten for the portable project. Provider results keep their actual detected raster extension rather than receiving an arbitrary `.png` name.

Flat exports use exclusive file creation and collision suffixes so an existing export is not overwritten. Run indices and provider output publication similarly preserve older results.

Manifest hashes provide an integrity check for the listed files. They do not establish provider authenticity or guarantee identical future model output. Missing source files are reported in the manifest instead of being silently presented as a complete archive.

## Qualification boundaries

The offline tests verify queue races, cancellation, partial results, persistence failures, receipt recovery, input validation, portable exports and client integration with local/fake boundaries. Browser smoke tests exercise the rendered client against a mocked RPC service. Native application lifecycle, packaged Electron distribution and real provider acceptance require the separate gates in [VERIFICATION.md](VERIFICATION.md).
