# Provider reference

Contract review date: **2026-11-10**. This document describes the adapters implemented in this repository. Official documentation was checked for the API contracts; live OpenAI, Gemini, and Azure image generation was **not executed** during this refresh. Offline tests verify request construction, responses, and failure handling. Account access, deployed model versions, output quality, latency, and billing require a live check in the account that will run the batch.

The initial application provider is **Mock**, which runs locally. Switching to a cloud provider is explicit. Saved unknown or retired model IDs fail validation rather than silently selecting another model. The Python provider catalog supplies capabilities to the clients. See [provider registry](../app/providers/registry.py), [shared validation](../app/providers/base.py), and [request-contract tests](../tests/test_provider_contracts.py).

## Implemented capabilities

These are application limits, not promises about every capability or quota offered by the underlying service.

| Provider ID | Images per row | Initial HTTP calls per row | Reference images | Output selection | Background selection |
|---|---:|---:|---:|---|---|
| `mock` | 1–8 | 0 | None | PNG, JPEG, WebP | Auto, opaque, transparent |
| `openai` | 1–10 | 1 | Up to 16 | PNG, JPEG, WebP | Auto, opaque, transparent |
| `google` | 1–4 | One per requested image | Up to 14 | Provider-controlled raster; the app exposes PNG only | None |
| `azure-openai` | 1–10 | 1 | None in this adapter | PNG, JPEG | None in this adapter |

PNG/JPEG/WebP reference files are accepted where references are supported, with a **12 MiB combined original-byte limit per row**. This is a deliberate inline-upload bound imposed by ImageAI. Files are decoded and checked using their actual bytes; a filename extension or user-supplied MIME type does not override detection. Missing, unreadable, invalid, excessive, or unsupported references fail instead of disappearing from the request. The queue preserves a private snapshot of the reference bytes used by each admitted run. See [image I/O](../app/providers/image_io.py) and [queue](../app/queue_manager.py).

All three cloud adapters reject the application's legacy fixed-seed and style controls. None exposes the application's numeric safety slider. OpenAI and Azure expose low/medium/high quality. Gemini's Avoid text is appended to the request as an instruction; it is not a native negative-prompt parameter. Mock supports a fixed seed for its deterministic preview colors but does not simulate a cloud model's quality.

## Configuration

Set the variables for the provider you use in the process environment, the source checkout’s `.env`, or a `.env` in the ImageAI data directory. Values are resolved in that order at startup. See [environment loading](../app/env.py).

| Variable | Used by | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI | API key sent as a Bearer token |
| `GEMINI_API_KEY` | Google | Gemini API key sent in `x-goog-api-key` |
| `AZURE_OPENAI_ENDPOINT` | Azure | HTTPS resource root, for example `https://your-resource.openai.azure.com`; no API path, query, or fragment |
| `AZURE_OPENAI_API_KEY` | Azure | Resource API key sent in the `api-key` header |
| `AZURE_GPT_IMAGE_2_DEPLOYMENT` | Azure | Deployment name placed in the request's `model` field |
| `AZURE_OPENAI_API_VERSION` | Azure | Defaults to `preview`; sent as the `api-version` query parameter |
| `IMAGEAI_DATA_DIR` | Application | Optional local session/output directory; defaults to `~/.imagen` |

OpenAI and Gemini endpoints are fixed in their adapters. There is no arbitrary OpenAI-compatible base-URL setting, Vertex AI integration, Azure managed-identity flow, or cloud Files API upload flow in this release. Gemini search grounding is not enabled; the old `GEMINI_ENABLE_SEARCH` setting has no effect in the current adapter.

Preflight is local and does not contact the provider. A valid report does not prove that credentials are present, a deployment exists, the account can access the model, or a paid request will succeed. Azure's Configured indicator checks the required configuration values; it does not perform a remote readiness probe. See [preflight](../app/batch_service.py).

## OpenAI

### Model catalog

The exact bounded raster catalog is:

| Model ID | Use |
|---|---|
| `gpt-image-2` | First advertised OpenAI model; used when selecting the provider's default |
| `gpt-image-2-2026-04-21` | Dated GPT Image 2 snapshot |
| `gpt-image-2.5-flare` | Explicit GPT Image 2.5 Flare choice |
| `gpt-image-2.5-sunburst` | Explicit GPT Image 2.5 Sunburst choice |
| `gpt-image-2.5-flare-2026-09-08` | Dated Flare snapshot |
| `gpt-image-2.5-sunburst-2026-09-08` | Dated Sunburst snapshot |

The catalog is deliberately smaller than the complete service catalog. Do not add a newly documented model without checking its options and extending request-contract tests. Official sources: [generation reference](https://developers.openai.com/api/reference/resources/images/methods/generate) and [image-generation guide](https://developers.openai.com/api/docs/guides/image-generation), reviewed 2026-11-10. Implementation: [OpenAI adapter](../app/providers/openai_provider.py).

### Requests and options

Without references, the adapter sends JSON to `https://api.openai.com/v1/images/generations`. With references, it sends JSON to `https://api.openai.com/v1/images/edits`, with the inputs in `images` as data-URL objects containing `image_url`. This is reference-conditioned generation/editing; there is no mask editor in the client. Both paths use `model`, `prompt`, `size`, `n`, `quality`, `output_format`, and `background`. The adapter expects base64 image data in `data[].b64_json`; it does not send legacy `response_format`, `style`, or `input_fidelity` options. See the [edit reference](https://developers.openai.com/api/reference/resources/images/methods/edit), reviewed 2026-11-10.

The app exposes `low`, `medium`, and `high`, mapped from stored quality values 1–3, 4–7, and 8–10. It does not expose `auto`, `xhigh`, `max`, compression controls, streaming, or vector/animated output even when a service model offers them. Prompts must contain 1–32,000 characters, including shared instructions. Preflight and queue admission check the same resolved prompt used by the request.

The standard presets are 1024×1024, 1024×1536, and 1536×1024. A custom GPT Image size must satisfy all of these conditions:

- Both dimensions are divisible by 16.
- Neither edge exceeds 3840 pixels.
- The longer edge is at most three times the shorter edge.
- Total area is 655,360–8,294,400 pixels.

These checks apply to OpenAI and the Azure GPT Image 2 adapter. The general application custom-dimension range is 64–8192 per edge; the provider constraints further narrow it. See [shared size validation](../app/providers/image_io.py) and the [official generation guide](https://developers.openai.com/api/docs/guides/image-generation).

Transparent output requires PNG or WebP. GPT Image 2 transparency is documented as a provider preview, so its availability and behavior should be checked in the target account. The app does not claim that describing a vector-style logo produces a vector file.

### Older sessions

The official [deprecations page](https://developers.openai.com/api/docs/deprecations), reviewed 2026-11-10, records DALL·E 2/3 shutdown on 2026-05-12 and GPT Image 1 shutdown on 2026-10-23. It announces 2026-12-01 shutdown for GPT Image 1.5, 1-mini, and `chatgpt-image-latest`. These IDs are excluded from this release's catalog. Open an old session, explicitly choose a supported model, inspect its effective settings, and preflight before starting new requests. Existing generated files remain reviewable.

## Google Gemini

The exact catalog is `gemini-3.1-flash-image` and `gemini-3-pro-image`; Flash is the first advertised model. The adapter calls `https://generativelanguage.googleapis.com/v1/models/{model_id}:generateContent` with `contents`, inline reference parts, and `generationConfig`. It requests `TEXT` and `IMAGE` modalities and excludes parts marked as thoughts from final images. It uses one request for each requested image and retains earlier saved results if a later request fails. See [Google adapter](../app/providers/google_provider.py), [official image guide](https://ai.google.dev/gemini-api/docs/image-generation), and [Generate Content image documentation](https://ai.google.dev/gemini-api/docs/generate-content/image-generation?hl=en), reviewed 2026-11-10.

Custom dimensions select an **aspect ratio and resolution tier**. They are not a request for exact output pixels. Both models accept the app's ratios `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, and `21:9`. Flash additionally accepts `1:4`, `4:1`, `1:8`, and `8:1`. The reduced fraction `7:3` is sent as `21:9`. Other ratios fail validation.

| Longest requested edge | Flash tier | Pro tier |
|---|---|---|
| At most 512 | `512` | `1K` |
| 513–1999 | `1K` | `1K` |
| 2000–3499 | `2K` | `2K` |
| 3500 and above | `4K` | `4K` |

Normal app sessions still enforce the shared 64–8192 custom-dimension range. Gemini controls the final dimensions and raster format. The app exposes only PNG as the format setting because it does not send a format selector; returned PNG, JPEG, or WebP bytes are detected, preserved, and saved with their correct extension. A request whose serialized JSON reaches 20,000,000 bytes is rejected before transmission, in addition to the 12 MiB reference-file bound.

Legacy `thinking_budget` must be 0 or null. Flash accepts `thinking_level` of `minimal`, `high`, or an empty value that omits the field; the default is `high`. Pro does not receive a thinking-level override. The clients do not expose a general reasoning slider. Search grounding is excluded pending a complete attribution flow.

The old `gemini-3-pro-image-preview` and `gemini-2.5-flash-image` IDs are excluded. Google's [deprecation table](https://ai.google.dev/gemini-api/docs/deprecations?hl=en), reviewed 2026-11-10, lists 2026-06-25 for the Pro preview and 2026-10-02 as the earliest shutdown date for 2.5 Flash image. An earliest shutdown date is not evidence of a specific account's observed failure. This implementation does not silently map either old ID to a new model.

## Azure OpenAI

The application model ID is `gpt-image-2`. The actual request `model` is the value of `AZURE_GPT_IMAGE_2_DEPLOYMENT`, not necessarily that literal model ID. The endpoint is:

```text
{AZURE_OPENAI_ENDPOINT}/openai/v1/images/generations?api-version={AZURE_OPENAI_API_VERSION}
```

The payload contains `model`, `prompt`, `size`, `quality`, `n`, and `output_format`. Azure uses the GPT size constraints above, low/medium/high quality, and PNG/JPEG output. It deliberately does not advertise references, transparency, or WebP. See [Azure adapter](../app/providers/azure_provider.py).

Microsoft's [image how-to](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e) documents this v1 route and deployment-name model field. At the review date, its output-format guidance conflicted with the [REST image reference](https://learn.microsoft.com/en-us/azure/foundry/reference/rest/images-create-image) on WebP support. PNG/JPEG are the conservative common subset. Azure image-edit support in official documentation is not implemented in this adapter. These exclusions are application choices pending deployment-specific verification, not claims that the Azure service can never provide the features. Both sources were reviewed 2026-11-10.

The Azure logo preset sets square 1024, one image per row, medium quality, PNG, concurrency 1, a local budget of 4 images/minute, keep-existing behavior, and the shared logo instructions. That budget is a configurable starting point, not an account quota or price estimate. See [preset implementation](../app/azure_logo_workflow.py) and Microsoft's [quota guidance](https://learn.microsoft.com/azure/foundry/openai/how-to/quota).

## Local quota, retries, and cancellation

`rate_limit_rpm` is the application's weighted per-minute budget. It is shared by workers in one queue and keyed by provider. OpenAI spends one unit per row request; Google reserves one per requested image; Azure reserves one per requested image; Mock spends none. The same configured numeric limit is applied independently to each provider key. This limiter is not account-wide, does not coordinate other application instances, and does not model token quotas, daily limits, or deployment-specific capacity. Google reserves the whole row's units before its sequential calls; retries inside the HTTP transport do not acquire additional local units. See [scheduler](../app/scheduler.py).

A row whose quota cost exceeds the entire configured window fails preflight. For example, an Azure row requesting 8 images needs an application budget of at least 8, or a smaller per-row count. A valid preflight reports initial HTTP request counts, excluding rate-limit retries; those counts are not monetary estimates.

The shared [HTTP transport](../app/providers/http_transport.py) implements:

- At most three attempts, and only after explicit HTTP 429 responses.
- `retry-after-ms`, then numeric or HTTP-date `Retry-After`; exponential fallback when neither is usable. Retry waits are cancellable.
- No automatic retry when the server asks for more than 300 seconds of delay, or after the third attempt. The reported error retains the requested delay.
- A 10-second connection timeout and a 180-second read timeout. These are Requests timeouts, not a total wall-clock deadline.
- No redirects for provider POSTs.
- No automatic resubmission after connection errors, unreadable successful responses, HTTP 408/409, or server errors. The outcome may be unknown, so the row becomes Interrupted and requires review.

Cancellation prevents pending submissions and stops quota/backoff waits. It cannot recall an already submitted synchronous HTTP request or guarantee avoidance of its charge. A successful response arriving after cancellation is saved. Multi-request rows retain earlier images when later requests fail. Replace mode discards the previous image set only after a successful replacement; failed and cancelled work keeps it. Stop After Current prevents unsubmitted work from starting and allows already submitted calls to finish.

Restarted Queued/Generating rows become Interrupted. Local receipts can recover saved results whose hashes match; they cannot retrieve a cloud result that the application never received. Generate Missing skips interrupted/uncertain runs and legacy images without trustworthy generation fingerprints. Repeated requests preserve their specifications, not a guarantee of identical pixels. See [generation state](../app/generation_state.py), [session recovery](../app/session_manager.py), and [queue reliability tests](../tests/test_queue_reliability.py).

## Verification before a release

Run the offline provider tests without making live calls:

```bash
python -m pytest -q tests/test_provider_contracts.py tests/test_azure_provider.py tests/test_queue_reliability.py
```

Preflight a cloud batch without generating it:

```bash
python -m app.cli --input examples/demo-prompts.csv --mode csv --output ./preflight-output --provider openai --model gpt-image-2
```

The CLI is a dry run unless `--run` is supplied. A live release check should separately cover one supported generation per provider, one OpenAI reference edit, the chosen Azure deployment, each advertised output format intended for release, and a small Gemini multi-image row. Confirm the actual saved MIME type/dimensions, request ID where returned, reference identity, and error behavior. Do not infer a live pass from mocked HTTP responses or a local preflight.
