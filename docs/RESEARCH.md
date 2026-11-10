# Research and release direction

Research date: **2026-11-10**. Baseline examined: [`4068a2ba1e33c26f845f6e74ccb295919403cc70`](https://github.com/velhud/imageai-batch-generator/tree/4068a2ba1e33c26f845f6e74ccb295919403cc70), committed 2026-06-09. This report distinguishes baseline observations, public documentation, implemented changes, and work intentionally deferred. API and competitor documentation was reviewed; no paid provider calls or comparative image-quality benchmark was performed.

## Product direction

ImageAI's strongest existing foundation is a desktop workspace for structured image batches: CSV/JSON/JSONL inputs, row identities and overrides, templates, multiple providers, galleries, sessions, and an Azure logo workflow. The useful release goal is to make a complete batch easy to validate, run, inspect, recover, and hand over.

The resulting priority is **reliable batch execution and portable evidence of what produced each image**. This is an engineering and product judgment based on the repository's existing features and the examples below. It does not assume that adding more providers or copying a large creative suite will increase adoption.

The release should demonstrate one coherent workflow: import structured prompts, inspect the resolved request, preflight, run a small sample, generate the remaining work, review outcomes, and export an image set with its session and provenance.

## What needed correction at the baseline

| Baseline observation | Why it mattered | Implemented response |
|---|---|---|
| The OpenAI adapter exposed only `dall-e-3`, mixed legacy quality/count assumptions, did not use references, and expected base64 without establishing the matching legacy response mode. | A provider button did not reliably represent a current working request contract. | Bounded GPT Image 2/2.5 raster catalog, validated options, base64 response checking, correct size selection, and an edits path for references. |
| Gemini exposed old image model IDs, substituted a default for unknown models, and continued after attachment-read failures. | A saved batch could run a different model or lose a requested reference without a clear decision. | Stable model IDs, rejection of unknown IDs, verified reference bytes, supported aspect ratios, and explicit missing/invalid-input failures. |
| Provider loops could produce files before a later request failed or cancellation arrived. | Useful completed output could be lost from session state. | Partial-result propagation, preservation of successful responses arriving during cancellation, and replacement only after success. |
| Mutable row/settings state, repeated admission, queue waits, and persisted active statuses needed a shared lifecycle. | Running work could diverge from the user's original request or be replayed ambiguously after interruption. | Immutable admission snapshots, duplicate protection, cancellable waits, run receipts, shared event reduction, and Interrupted state. |
| Export filenames and machine-local paths were insufficient for a complete handoff. | Repeated exports could collide; another machine lacked the session's referenced files and request context. | Collision-safe publication, relative-path portable bundles, retained generation references, SHA-256 manifests, and paginated contact sheets. |
| React controls exposed unsupported options and Stop Row stopped the entire queue. | The interface promised choices and cancellation scope that did not match the adapter. | Capability-driven controls, per-row cancellation, visible errors, preflight, and portable export. |

The first two observations are directly visible in the baseline [OpenAI adapter](https://github.com/velhud/imageai-batch-generator/blob/4068a2ba1e33c26f845f6e74ccb295919403cc70/app/providers/openai_provider.py) and [Google adapter](https://github.com/velhud/imageai-batch-generator/blob/4068a2ba1e33c26f845f6e74ccb295919403cc70/app/providers/google_provider.py). The implementation now lives in [providers](../app/providers/), [queue](../app/queue_manager.py), [session management](../app/session_manager.py), [exporter](../app/exporter.py), and the primary client code. This table describes the candidate changes rather than claiming a published release already contains them.

## Provider research and decisions

### OpenAI: update the contract before expanding the catalog

The official [image-generation reference](https://developers.openai.com/api/reference/resources/images/methods/generate), [editing reference](https://developers.openai.com/api/reference/resources/images/methods/edit), and [generation guide](https://developers.openai.com/api/docs/guides/image-generation) establish a current raster generation/editing path. The release uses a bounded set of GPT Image 2 and 2.5 aliases/snapshots, preserving the first catalog entry `gpt-image-2` as the OpenAI default. Reference attachments use the edits endpoint; output bytes are inspected and preserved with their actual type and dimensions.

The [deprecations page](https://developers.openai.com/api/docs/deprecations) records DALL·E 2/3 shutdown on 2026-05-12 and GPT Image 1 shutdown on 2026-10-23, and announces December 1 shutdown for the listed 1.5/1-mini/ChatGPT image aliases. This made a refresh to another near-retirement model a poor release target. Saved IDs remain explicit; the app does not silently migrate paid generation to a different model.

The chosen UI is a supported subset: raster files, low/medium/high quality, dimensions, background, and references. Newer model options do not become application features merely because a REST schema lists them. Exact catalogs and constraints are maintained in [PROVIDERS.md](PROVIDERS.md).

### Gemini: preserve reference intent and distinguish dimensions from tiers

Google's [image-generation guide](https://ai.google.dev/gemini-api/docs/image-generation) and [Generate Content image documentation](https://ai.google.dev/gemini-api/docs/generate-content/image-generation?hl=en) support the existing Generate Content architecture. Rewriting the application around another API is unnecessary for this increment. The adapter now uses `gemini-3.1-flash-image` and `gemini-3-pro-image`, handles inline references, and ignores thought-image parts when collecting final results.

The [deprecation table](https://ai.google.dev/gemini-api/docs/deprecations?hl=en) lists retirement information for the old preview and 2.5 Flash image models. Its earliest-shutdown wording is retained: documentation is not a live observation that every account rejected a request on that day. During research, this table was available through indexed official search content while direct opening returned an unsupported document format; the Generate Content image page also redirected to a host inaccessible in the research environment.

Custom dimensions now resolve to an allowed aspect ratio and image-size tier. The app records actual returned pixels and format rather than promising exact requested dimensions. Avoid text is a prompt instruction, not a fabricated native control. Search grounding is omitted until result attribution can be represented completely in the product.

### Azure: document the deployed contract and avoid quota assumptions

Microsoft's [image how-to](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e) confirms the existing `/openai/v1/images/generations` route with a deployment name in the payload. The route itself was not the defect. Improvements concentrate on validation, response preservation, retry delays, and uncertain outcomes.

The [REST image schema](https://learn.microsoft.com/en-us/azure/foundry/reference/rest/images-create-image) and the how-to differed on WebP support at review time. The implemented Azure adapter therefore advertises PNG/JPEG, with references and background selection excluded pending specific deployment work. The logo preset retains a configurable four-images/minute local budget and concurrency one. It does not claim that this matches a user's Azure account quota, or quote a universal image price. [Microsoft's quota guidance](https://learn.microsoft.com/azure/foundry/openai/how-to/quota) informs server-delay handling; account/deployment limits must be checked separately.

All provider sources above were reviewed on 2026-11-10. The intended REST contract and offline adapter tests are distinct from a successful live call; the latter remains a release gate.

## Lessons from related open-source tools

These comparisons use project-owned documentation. They are documented capabilities, not hands-on benchmarks or assertions that one project is generally better.

| Project and primary source | Observed capability | Applied lesson |
|---|---|---|
| [InvokeAI gallery](https://invoke-ai.github.io/InvokeAI-7/users-guide/gallery/gallery-reference/) and [metadata architecture](https://invoke-ai.github.io/InvokeAI-7/development/architecture/media-metadata/) | Review, metadata recall, reference reuse, board operations, and generation/workflow metadata associated with images. | Make review and provenance part of the normal batch workflow. This release supplies artifact metadata, manifests, references, and contact sheets; a full board/comparison UI is deferred. |
| [Fooocus README](https://github.com/lllyasviel/Fooocus/blob/main/readme.md) | Prompt arrays expand variants; project status is limited LTS focused on fixes. | Variation and preview are useful, established workflows. Preserve existing templates and improve batch parsing; do not treat Fooocus as evidence of an actively expanding cloud-provider catalog. |
| [ComfyUI routes](https://docs.comfy.org/development/comfyui-server/comms_routes) and [messages](https://docs.comfy.org/development/comfyui-server/comms_messages) | Submission IDs, queues/history, upload/view endpoints, and progress/error/interruption messages. | A future local adapter needs durable remote-job identity, exact output collection, and cancellation ownership. A nominal endpoint substitution would be incomplete. |
| [lelandg/ImageAI](https://github.com/lelandg/ImageAI) | A separate desktop/CLI project with multiple cloud/local providers and broader image/video workflows. | Multi-provider support alone is not a specific product identity. Lead with structured batches and dependable handoff. This is a different repository from `velhud/imageai-batch-generator`. |
| [Batch Story Image Generator](https://github.com/Adamouization/Batch-Image-Generator/blob/main/README.md) | Structured prompt records, resumable CLI work, and output/metadata organization. | Include an explicit headless dry run and a repeatable offline demonstration alongside desktop review. |

These sources were reviewed on 2026-11-10. No external source code was imported into the patch.

## What this release implements

### A run has an immutable request and a recoverable record

The queue snapshots effective settings, prompt/category identity, source metadata, and reference bytes at admission. Each run receives an identity, generation fingerprint, and local receipt. Duplicate admission is rejected, request-rate waits can be cancelled, and multiple clients apply the same event-reduction rules. Started work without a recorded outcome becomes Interrupted after recovery. Hash-verified local receipts can restore saved artifacts without replaying provider requests. See [queue](../app/queue_manager.py), [event reducer](../app/generation_state.py), and [session manager](../app/session_manager.py).

HTTP 429 rejections receive bounded, cancellable retries using server delay headers. Ambiguous paid POST outcomes receive no automatic replay. This is an application policy that preserves the user's decision about a new request; it does not provide remote idempotency or cancel a provider job already accepted. See [transport](../app/providers/http_transport.py).

### A batch can be reviewed and moved as a unit

Portable export publishes a new directory containing `session.json`, generated images, current attachments, available frozen references for historical results, a SHA-256 manifest, and contact sheets paginated at 40 images. Paths are rewritten for movement, file collisions are avoided, and missing or altered artifacts are reported. Outputs retain original raster bytes; previews are separate. The result preserves the request specification, not a promise that future generation yields identical pixels. See [export implementation](../app/exporter.py).

### Validation is available before cloud work starts

Shared preflight reports per-row settings/reference errors and planned initial request/image counts without contacting a provider. ImageAI Desktop exposes the relevant workflow; the CLI defaults to dry run and requires `--run` for generation. Structured input validation, shared prompt append, an offline example, and an isolated mock run make the complete workflow demonstrable without credentials. See [batch service](../app/batch_service.py), [CLI](../app/cli.py), and [examples](../examples/).

Preflight is not an authentication, price, content-policy, or deployment-capacity check. The UI distinguishes local configuration from a remotely tested integration. Unsupported controls are hidden or rejected instead of implying that ignored settings affect the image.

## Deferred work and acceptance criteria

| Follow-up | Why it is separate | Evidence required before implementation is called complete |
|---|---|---|
| Provider-side asynchronous Batch API | It has submit/poll/collect states, remote IDs, and a completion window, unlike the current synchronous row operation. | Durable batch/custom IDs, restart-safe collection, duplicate suppression, partial/error files, cancellation semantics, and explicit submission versus collection UI. |
| ComfyUI local generation | Workflow-node bindings and remote-job ownership differ from a simple prompt API. | One supported workflow template, validated input bindings, persisted `prompt_id`, exact history/output correlation, and cancellation that does not interrupt another client's job. |
| Azure references/transparency/WebP | Current deployment behavior and inconsistent documentation need specific validation. | Successful request/response evidence for the target deployment, then capability and error-path tests. |
| Comparison boards, ratings, and richer metadata recall | Useful after artifacts are reliable, but larger than the portable-review slice. | A focused review workflow and clear handling of historical settings/references versus current row edits. |
| Streaming previews, vectors, animations, masks | Each changes response collection, rendering, persistence, and export expectations. | Type-aware outputs, lifecycle/error tests, appropriate viewers, and preserved provider semantics. |
| Team accounts, remote hosting, model downloads, video | These create separate operational and product scopes. | A new scope decision backed by concrete user workflows. |

The [OpenAI Batch guide](https://developers.openai.com/api/docs/guides/batch) and [batch creation reference](https://developers.openai.com/api/reference/resources/batches/methods/create), reviewed 2026-11-10, document image-generation/edit support and a 24-hour completion window. This is a credible follow-up for a batch product, but it is not implemented by placing a long wait inside the synchronous provider method.

## Verification boundaries

The code includes offline tests for provider payloads and image bytes, partial results, retry/cancellation, duplicate admission, immutable references, atomic session recovery, hash verification, portable export, CLI validation, and frontend interactions. Passing an offline contract test establishes the tested behavior of this application against the supplied response; it does not establish live provider access or current image quality. The release handoff records exact executed checks and remaining platform/browser checks separately.

Before publishing, the integration team should run the complete local suite and offline example, inspect the actual desktop clients on supported operating systems, verify a moved portable bundle, and perform a small explicitly chosen live-provider smoke test. The practical provider matrix and commands are in [PROVIDERS.md](PROVIDERS.md). No exact price, universal account quota, guaranteed recovery of an unseen cloud response, or deterministic cloud regeneration is claimed.
