# Release verification

Run these checks after applying the changes to the target checkout. This is the integration procedure and expected behavior; it is not a claim that every operating system, packaged application or paid-provider gate has already run. Record the actual commit, platform, commands and results in the release evidence.

## 1. Install the candidate and run Python tests

From the repository root, use Python 3.10 or newer. Install the test dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
```

The default suite uses temporary state, local mock providers and patched HTTP responses. It does not require provider credentials or make paid image-generation calls.

The full suite covers parser and import validation, queue admission races, cancellation and stopping, weighted quotas, partial output retention, provider request contracts, atomic sessions, verified receipt recovery, portable exports, the shared event reducer, and backend lifecycle. The CLI tests launch actual subprocesses.

Useful focused checks when integrating individual parts are:

```bash
python -m pytest -q tests/test_queue_reliability.py tests/test_queue_manager.py tests/test_scheduler.py
python -m pytest -q tests/test_provider_contracts.py tests/test_azure_provider.py
python -m pytest -q tests/test_sessions_exports_release.py tests/test_release_integration_review.py
python -m pytest -q tests/test_backend_release.py tests/test_backend_smoke.py
python -m pytest -q tests/test_cli.py
```

Do not replace the final full-suite gate with only the focused tests. Final aggregate counts belong in the recorded test output, since additional tests may be added before integration.

## 2. Exercise the complete offline CLI workflow

Create one temporary verification directory. The CLI output directory itself must be empty or not yet exist:

```bash
imageai_verify_dir="$(mktemp -d)"
python -m app.cli \
  --input examples/demo-prompts.csv \
  --mode csv \
  --output "$imageai_verify_dir/offline" \
  --provider mock \
  --images 2
```

Expected: exit code `0`, JSON with `status: "ready"`, `dry_run: true`, three rows, six planned images and zero planned provider requests. The `offline` output directory must not have been created.

Run the same plan explicitly:

```bash
python -m app.cli \
  --input examples/demo-prompts.csv \
  --mode csv \
  --output "$imageai_verify_dir/offline" \
  --provider mock \
  --images 2 \
  --run > "$imageai_verify_dir/offline-result.json"
```

Expected: exit code `0`, three completed rows, six saved images, `provider_work_stopped: true`, a saved `session.json`, and a portable `imageai-*` folder with originals, manifest and contact sheet.

Verify every manifest entry rather than relying only on a success message:

```bash
python - "$imageai_verify_dir/offline-result.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text())
assert result["status"] == "completed", result
assert result["completed_rows"] == 3
assert result["images_saved"] == 6
assert result["provider_requests_planned"] == 0
assert result["provider_work_stopped"] is True
assert Path(result["session_path"]).is_file()
bundle = Path(result["bundle_path"]).resolve()
manifest = json.loads((bundle / "manifest.json").read_text())
assert manifest["image_count"] == 6
assert manifest["missing"] == []
for item in manifest["files"]:
    relative = Path(item["path"])
    assert not relative.is_absolute()
    artifact = (bundle / relative).resolve()
    assert artifact.is_relative_to(bundle)
    assert artifact.is_file()
    assert artifact.stat().st_size == item["bytes"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == item["sha256"]
assert (bundle / "contact-sheet.png").is_file()
print("Verified six images and every portable manifest entry.")
PY
```

Open the contact sheet and the exported `session.json` in ImageAI. Verify that prompts, IDs, image references and settings are present. Copy the portable folder to a different directory, then open its session there; images should resolve from their relative paths.

A second `--run` targeting the same populated output directory must return validation exit code `2` without replacing the earlier files. Use a fresh directory for another run. The CLI intentionally has no automatic resume/replay option.

Other expected exit codes are `2` for malformed input or unsupported settings, and `1` for generation failure, cancellation or timeout. On an unresolved submitted request, inspect the saved interrupted state and receipts before deliberately requesting another generation.

## 3. Test the desktop renderer

Use Node.js 22.12 or newer within the Node 22 release line, matching the renderer CI job and the checked-in toolchain. From the repository root:

```bash
cd frontend
npm ci
npx playwright install chromium
npm run test:ui
```

`npm run test:ui` builds the desktop renderer and runs its smoke tests with a simulated desktop bridge. On Linux runners that need browser system libraries, the provided CI uses `npx playwright install --with-deps chromium`.

For the renderer test CI install, `npm ci --ignore-scripts` with `ELECTRON_SKIP_BINARY_DOWNLOAD=1` avoids downloading the Electron desktop binary. A packaged Electron acceptance run needs a normal installation with its runtime available.

The seven browser tests use Playwright Chromium and the Node test runner. They serve the actual compiled frontend but intercept RPC calls with fixtures; they do not start Python or contact an image provider.

| Browser smoke case | Observable expected result |
| --- | --- |
| Provider-dependent controls | Switching provider exposes supported quality/count/format controls. JPEG disables transparency. Editing custom width sends that exact dimension to settings. |
| Stop one row | Sends `cancel_row` for the selected row; does not send `stop_all`. |
| Failed preflight | Displays the row-specific error and makes no generation request. |
| Portable export | Sends the export request and displays the returned output location, exported count and missing-file count. |
| Shared batch append | Preview uses the current appended text. Editing it invalidates the old preview; import uses the newly previewed value. |
| HTTP conflict | Preserves the backend's explanation of the rejected change rather than replacing it with a generic status-code message. |
| Interrupted work | Displays the interrupted state; declining the explicit retry confirmation sends no generation request. |

The export browser test verifies the UI/RPC contract. The Python and CLI tests verify that actual export files, relative paths and hashes are correct.

## 4. Verify the packaged desktop workflow

Use an isolated `IMAGEAI_DATA_DIR` for interactive tests. With Python dependencies installed and Node 22 active:

```bash
cd frontend
npm ci
npm run build -- --dir --publish never --config.directories.output=.desktop-package
export IMAGEAI_PYTHON="$(command -v python)"
export IMAGEAI_ELECTRON_EXECUTABLE="$(find "$PWD/.desktop-package" -type f -path '*/Contents/MacOS/ImageAI Batch Generator' -print -quit)"
npm run test:desktop
```

On Windows, set `IMAGEAI_PYTHON` to the Python interpreter and `IMAGEAI_ELECTRON_EXECUTABLE` to the full path of `.desktop-package/win-unpacked/ImageAI Batch Generator.exe`. The smoke test starts the packaged app from a different directory and runs the real Python backend. It supplies deterministic file selections at the OS-dialog boundary and verifies the decoded image at the clipboard boundary.

For interactive acceptance, launch the packaged executable with the same Python interpreter. In the desktop window:

1. Import a structured CSV, review the resolved preflight requests, generate one Mock sample, then generate the missing images. Inspect saved prompts, image sizes and file hashes in Result Details.
2. Change global settings and verify untouched rows inherit them. Use per-row settings to keep or replace images and to override the regeneration seed where supported.
3. Stop a row or stop after current work. Check terminal statuses and use Generate Missing to resume eligible work. Decline an interrupted retry and confirm no new request starts.
4. Use the native reference picker to attach multiple files on a compatible provider. Clear them before returning to Mock.
5. Open a generated image in the gallery. Copy it, reveal it in the file manager, export just that image, then export the row.
6. Save As, start a new session, export a portable batch, and open a relocated `session.json` through the native file picker. Verify the reopened images and manifest. Restart the app and verify session restoration. Stop the app-owned backend in an isolated test and use Reconnect to restore the workspace.

The package includes the Python backend source. Install the Python runtime and project dependencies separately before testing an executable from another location.

## 5. Minimum real-provider acceptance

The automated suite uses fake HTTP responses. Before advertising a paid provider path as locally qualified, run the corresponding small acceptance case on the target account. Configure the account/deployment through the existing environment settings and use one prompt, one requested image and concurrency one for each initial case. Choose a model that the repository advertises and the account actually permits.

Create the one-row input, then use a fresh output directory for the chosen CLI generation test:

```bash
python - "$imageai_verify_dir/provider-smoke.csv" <<'PY'
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(
    "prompt_id,prompt\nlive-01,A simple blue circle on a white background.\n",
    encoding="utf-8",
)
PY

python -m app.cli \
  --input "$imageai_verify_dir/provider-smoke.csv" \
  --mode csv \
  --output "$imageai_verify_dir/provider-openai" \
  --provider openai \
  --images 1 \
  --concurrency 1 \
  --run
```

Select `google` or `azure-openai` for their respective generation checks. The CLI selects the provider's first advertised model unless `--model` is given. Reference-edit and format/background acceptance cases use the desktop interface, where those controls are exposed.

| Adapter/path | Minimum case | Required observations |
| --- | --- | --- |
| OpenAI generation | Generate one image with an allowed model. | One saved, decodable raster; completed status; requested model/options and returned request metadata retained. Actual format/extension and dimensions match the recorded response. |
| OpenAI reference editing | Attach one small PNG and explicitly request a visible edit. | The edit endpoint accepts the request; a result is saved; metadata includes the frozen reference hash and edit operation. Export retains that actual reference. |
| Google generation | Generate one image using an allowed catalog model and supported aspect ratio. | Result is a final image, with detected raster format/dimensions and requested aspect/resolution recorded. The UI does not imply unsupported seed or quality controls. |
| Google reference editing | Attach one small PNG and request a visible alteration. | The result is retained with reference provenance. Requested count and actual saved count are reported honestly. |
| Azure deployment generation | Use the configured resource root, deployment and API version for one image. | Request reaches the intended deployment; one raster is saved; deployment, request ID when returned, quality and output format are retained. |
| Azure repeat without overwrite | Deliberately generate the same row once more. | The prior file bytes remain unchanged and the new output has a distinct run/collision-safe filename. |

For every format or transparency mode advertised for the release, perform an additional one-image check of that specific option. For example, inspect actual PNG alpha when testing transparent output rather than accepting the background setting alone. Only mark combinations that actually passed as qualified for that account/deployment.

Do not deliberately create live throttling or transport failures to test recovery. The fake-provider tests cover explicit 429 waits, failed connections, ambiguous server outcomes, malformed responses, partial completion and cancellation races. If a real acceptance request nevertheless ends with an unknown outcome, retain its request identity and interrupted state, review the provider's available record, and explicitly decide whether to send a new request. Bulk missing-only generation must not replay that row.

## 6. Record integration evidence

Retain the exact candidate commit and the outputs from Python tests, frontend type checking, web build and browser tests. For each platform or paid-provider gate, record:

- Platform/runtime and whether this was source execution or a packaged artifact.
- Provider/model or Azure deployment, requested size/count/format and actual result properties.
- Session/job/request identities needed to inspect the run.
- Expected and observed behavior, including retained partial outputs or an unknown outcome.
- Manifest verification result and any missing artifacts.

Mark unrun gates as unrun. Offline contract tests establish application behavior under their controlled inputs; they do not prove live account access, provider availability, current account quotas, macOS packaging or pixel-identical regeneration.
