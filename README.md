# ImageAI Batch Generator

A desktop and command-line workspace for structured image batches: import prompts, apply shared settings or row overrides, validate a batch, generate it through a bounded queue, and hand off images together with their provenance.

Use the **Mock provider** to run the complete workflow offline. OpenAI, Google Gemini, and Azure OpenAI are optional. ImageAI preserves the prompt, settings, reference inputs, request metadata, and file hashes associated with each saved result.

## What the release candidate adds

- **Preflight before generation:** validate the complete selected batch, model capabilities, dimensions, references, and local quota settings before admitting work.
- **Reliable queue behavior:** immutable requests and reference copies; duplicate admission protection; cancellation while queued or waiting for quota; retention of partial and late results.
- **Explicit uncertain outcomes:** interrupted requests are visible and are never automatically replayed on restart. Generate Missing skips them until the user reviews the outcome.
- **Recovery:** atomic session saves, a last-valid backup, and durable generation receipts that can recover verified outputs when a UI save was interrupted.
- **Current provider contracts:** GPT Image generation/reference edits, stable Gemini image models, and conservative Azure deployment support. Unsupported or retired selections fail explicitly.
- **Portable batch export:** relative-path session, exact image bytes, current and historical reference inputs, SHA-256 manifest, and paginated contact sheets. Existing exports are not overwritten.
- **Headless batches:** a dry-run-by-default CLI and a credential-free example.
- **ImageAI Desktop:** the Electron workspace uses native file dialogs and clipboard, with shared generation, preflight and export through the local backend.

## Install

Install **Python 3.10 or newer** and its runtime dependencies from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows, activate with `.venv\Scripts\activate`. For the test tools, use `python -m pip install -e '.[test]'`.

### Offline quick start

This validates three sample rows without generating images or creating the output directory:

```bash
imageai-batch \
  --input examples/demo-prompts.csv --mode csv \
  --output ./demo-output --provider mock --images 2
```

Add `--run` to generate six local example images, save a session, and create a portable bundle:

```bash
imageai-batch \
  --input examples/demo-prompts.csv --mode csv \
  --output ./demo-output --provider mock --images 2 --run
```

`python -m app.cli` is equivalent to `imageai-batch`. Each run requires an empty output directory so an earlier run cannot be overwritten. The command prints a JSON summary containing `status`, `images_saved`, `session_path`, and `bundle_path`. Exit codes are **0** for success, **2** for invalid input/configuration, and **1** for a failed, cancelled, or timed-out run.

## ImageAI Desktop

With the Python environment active and Node **22.12+** installed:

```bash
cd frontend
npm ci
npm run dev
```

ImageAI opens its own window and starts the Python backend automatically. Import a batch, adjust global defaults or row overrides, run **Preflight**, then generate the selected rows or use **Generate Missing**. **Open Session** and **Save Session As** use system file dialogs.

Use `npm run build` to package the desktop app. The package includes backend source and requires Python with the runtime dependencies installed. Set `IMAGEAI_PYTHON` to the absolute interpreter path when launching the packaged executable if it cannot find Python on the system path. The [verification guide](docs/VERIFICATION.md) includes a packaged-app smoke test.

Sessions and generated artifacts are stored under `~/.imagen/` by default. Set `IMAGEAI_DATA_DIR` to an absolute directory for an isolated workspace. Run one backend per data directory.

### Experimental macOS SwiftUI client

See [macos-swiftui/README.md](macos-swiftui/README.md) for the Swift toolchain, startup, and native test commands. Its source is included in this candidate, but macOS compilation and native interaction must be verified on a Mac before publishing it as supported.

## Provider setup

Set environment variables for the provider you use, or copy `.env.example` to the source checkout’s `.env`. A packaged app can read a local `.env` in its data directory. Restart ImageAI after changing provider configuration.

| Provider | Configuration | Implemented scope |
|---|---|---|
| Mock | None | Deterministic local images; no network or provider charges |
| OpenAI | `OPENAI_API_KEY` | GPT Image 2 and selected 2.5 models; text generation and reference-image edits; PNG/JPEG/WebP |
| Google Gemini | `GEMINI_API_KEY` | Stable Gemini 3.1 Flash Image and 3 Pro Image; text and reference-image requests |
| Azure OpenAI | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_GPT_IMAGE_2_DEPLOYMENT`; optional `AZURE_OPENAI_API_VERSION` | GPT Image 2 deployment generation; PNG/JPEG |

See [Provider reference](docs/PROVIDERS.md) for exact model IDs, limits, dimensions, quality mappings, retry behavior, and source links. Preflight validates local settings, reference files, and request shape; it does **not** prove credentials, account access, remote quota, or service availability. Catalog changes do not silently migrate an old session to a different paid model: choose a replacement explicitly.

Automatic transport retries are limited to explicit rate-limit responses. Timeouts and ambiguous server failures become an uncertain outcome rather than automatically sending another paid request. Cancelling an in-flight synchronous request cannot guarantee cancellation at the remote provider; any image returned before shutdown is retained, and uncertain work must be reviewed before a new request.

## Structured batch inputs

Desktop parsing supports plain text, numbered lists, JSON arrays, JSON Lines, and CSV. The CLI accepts `lines`, `json_array`, `json_lines`, and `csv`.

```csv
prompt_id,category_id,prompt
hero001,product,"A ceramic coffee cup, soft morning light and a pale blue background."
icon001,identity,"A geometric lighthouse symbol with balanced negative space."
```

`prompt_id`, `category_id`, and source metadata survive import and export. CSV requires the selected prompt column; malformed structured input is reported before import. Append a shared instruction through **Append to every parsed prompt**, CLI `--prompt-append`, or the backend `prompt_append` field. Editing the input invalidates an old preview.

A batch request snapshots prompts, settings, and reference bytes at queue admission. Subsequent edits do not change an already accepted request. Generate Missing checks the current request fingerprint, image hashes, and desired count. Results from an older prompt/settings/reference combination do not satisfy the new request. Legacy results without a fingerprint need explicit review rather than an inferred replay.

## Exports and portable sessions

Ordinary export copies images with collision-safe filenames and JSON metadata. The actual file extension follows the decoded provider format. Traversal paths and invalid naming patterns are rejected before copying.

**Portable export** creates a new uniquely named folder containing:

| Path | Contents |
|---|---|
| `session.json` | Session snapshot with relative file paths |
| `images/` | Generated images, preserved byte-for-byte |
| `attachments/` | Current row reference inputs |
| `references/` | Frozen reference inputs associated with historical results |
| `manifest.json` | File hashes/sizes, provenance, and missing or changed input reports |
| `contact-sheet*.png` | Up to 40 images per sheet, with recorded prompt labels |
| `README.txt` | Instructions for loading and verifying the bundle |

Move the whole folder and open its `session.json` on another installation. A missing or changed source file appears in the manifest rather than being silently substituted. The bundle preserves the evidence needed to inspect a run; cloud model output is not guaranteed to be reproducible pixel-for-pixel.

## Azure logo batches

The Azure Logo Batch preset is available in ImageAI Desktop. It selects GPT Image 2, square 1024 output, medium quality, one PNG per prompt, concurrency 1, and an initial local budget of four images per minute. This is an application default, not a statement of your Azure deployment quota.

Structured IDs are retained in readable filenames, for example `cat01_prompt0001_q-medium_run01.png`; collisions receive an additional suffix. `generation_log.jsonl` records session runs alongside durable receipts. Generate Missing counts matching saved images, and verification distinguishes successful prompt rows from actual image counts.

For category-organized Markdown prompt collections:

```bash
python scripts/prepare_logo_prompts.py \
  --source /path/to/logo-markdown-folder \
  --output /path/to/prepared-output
```

The utility produces CSV/JSON/JSONL batches, per-category CSVs, an ImageAI session, and a preparation report with source metadata.

## Development and verification

```bash
python -m pytest -q
cd frontend
npm ci --ignore-scripts
npm run typecheck
npm run build:web
npx playwright install chromium
npm run test:ui
```

The Python suite and browser scenarios use offline fixtures. The desktop smoke test launches the actual Electron package against its bundled backend. See the [verification guide](docs/VERIFICATION.md) for those checks and live provider acceptance.

CI runs Python tests and the offline demo on Linux, macOS and Windows, the browser suite on Linux, packaged Electron smoke tests on macOS and Windows, and the Swift test/build gate on macOS.

## Project map

| Area | Responsibility |
|---|---|
| `app/providers/` | Capability catalog, provider request contracts, transport, image validation |
| `app/queue_manager.py`, `app/scheduler.py` | Immutable jobs, queue lifecycle, weighted local rate limiting |
| `app/generation_state.py`, `app/batch_service.py` | Shared state reduction and preflight |
| `app/session_manager.py`, `app/storage.py` | Atomic persistence and receipt recovery |
| `app/exporter.py` | Ordinary and portable exports |
| `app/cli.py`, `app/backend_server.py` | CLI and local RPC |
| `frontend/`, `macos-swiftui/` | React/Electron and experimental native clients |
| `tests/`, `frontend/tests/` | Python, browser and packaged desktop regressions |

[Architecture](docs/ARCHITECTURE.md) explains lifecycle and persistence invariants. [Research and release direction](docs/RESEARCH.md) records the provider/competitor evidence behind this scope. [CHANGELOG.md](CHANGELOG.md) describes compatibility changes, and [CONTRIBUTING.md](CONTRIBUTING.md) lists contribution requirements. The project is licensed under [MIT](LICENSE).
