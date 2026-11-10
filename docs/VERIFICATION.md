# Release verification

Run these checks after applying the changes to the target checkout. This is the integration procedure and expected behavior; it is not a claim that every operating system, packaged application or paid-provider gate has already run. Record the actual commit, platform, commands and results in the release evidence.

## 1. Install the candidate and run Python tests

From the repository root, use Python 3.10 or newer. The full suite includes desktop tests, so install both optional groups:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[desktop,test]'
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

The default suite uses temporary state, local mock providers and patched HTTP responses. It does not require provider credentials or make paid image-generation calls.

The full suite covers parser and import validation, queue admission races, cancellation and stopping, weighted quotas, partial output retention, provider request contracts, atomic sessions, verified receipt recovery, portable exports, the shared event reducer, backend lifecycle and PySide thread delivery. The CLI tests launch actual subprocesses.

Useful focused checks when integrating individual parts are:

```bash
python -m pytest -q tests/test_queue_reliability.py tests/test_queue_manager.py tests/test_scheduler.py
python -m pytest -q tests/test_provider_contracts.py tests/test_azure_provider.py
python -m pytest -q tests/test_sessions_exports_release.py tests/test_release_integration_review.py
python -m pytest -q tests/test_backend_release.py tests/test_backend_smoke.py
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_desktop_release.py
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

## 3. Build and test the React interface

Use Node.js 22.12 or newer within the Node 22 release line, matching the web CI job and the checked-in toolchain. From the repository root:

```bash
cd frontend
npm ci
npm run typecheck
npm run build:web
npx playwright install chromium
node --test tests/ui-smoke.cjs
```

`npm run test:ui` is the packaged equivalent of the web build followed by the smoke-test command. On Linux runners that need browser system libraries, the provided CI uses `npx playwright install --with-deps chromium`.

For a web-only CI install, `npm ci --ignore-scripts` with `ELECTRON_SKIP_BINARY_DOWNLOAD=1` avoids downloading the Electron desktop binary. A later native Electron acceptance run needs a normal installation with its runtime available.

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

## 4. Verify the real desktop workflow on macOS

Use isolated state directories for these acceptance runs. The Swift package declares macOS 13 or newer and Swift tools 5.9 or newer. These checks require an actual supported Mac with the relevant developer tools; Linux browser tests do not satisfy them.

### PySide

Return to the repository root with the virtual environment active:

```bash
IMAGEAI_DATA_DIR="$imageai_verify_dir/pyside-state" python main.py
```

Use the mock provider and verify:

1. Import the example CSV, retaining prompt/category IDs. Preview shared appended text, edit the text, and confirm that applying uses the edited content.
2. Generate the batch. Images and status changes appear without UI-thread warnings or a frozen interface.
3. Change a global setting and confirm untouched rows still inherit it. Explicit row overrides remain overrides after refresh.
4. Queue enough rows to observe pending work. Stop a pending row and use stop-all; cancelled pending rows must not subsequently generate.
5. Regenerate one row with “keep” and then with “replace.” Older physical outputs must not be overwritten; a failed/cancelled replacement must preserve the earlier successful image set.
6. Save, close and reopen. Completed results remain accessible. A session closed during generation shows unresolved work as interrupted and does not restart it automatically.
7. Export a portable project and open it after relocation. Check the manifest and contact sheet against the saved images.

For an interrupted row, verify that per-row, selected-row and all-row generation ask for review, default to declining, and send no new work when declined. The undo/recovery regressions also verify that undo does not reset consumed receipt identities and that historical active states become interrupted.

Use the event-controlled regression tests for races that are too fast to observe reliably with the mock UI. A manual click sequence is not a substitute for those tests.

### SwiftUI

From the repository root, keep the Python virtual environment active so the native launcher's `python3` resolves to the installed environment:

```bash
cd macos-swiftui
swift build
swift test
IMAGEAI_DATA_DIR="$imageai_verify_dir/swift-state" swift run ImagenNative
```

For the backend-autolaunch gate, ensure another ImageAI backend is not already occupying `127.0.0.1:8765`. Verify that the native client starts or connects to the intended backend, loads a session, imports a small mock batch, generates it and displays retained images after reopen.

Load an interrupted row and decline its generation confirmation. Verify that no new `generate_rows` request is sent; an explicit accepted confirmation should be required before a fresh request.

Record which features the native client exposes. The SwiftUI client remains experimental and does not become feature-equivalent to the PySide/React surfaces merely because its package compiles.

### Electron

From `frontend/`, use a normal `npm ci` installation and the activated Python environment:

```bash
npm ci
IMAGEAI_DATA_DIR="$imageai_verify_dir/electron-state" npm run dev
```

Verify real Python backend startup, RPC state refresh, mock generation, file dialogs, per-row cancellation and portable export from the native window. Check quit/relaunch behavior with completed work and with an interrupted batch. The shell terminates its child backend on quit; unresolved remote work must not be presented as automatically resumed on restart.

A packaged Electron release has an additional gate:

```bash
npm run build
```

Launch the produced artifact from a fresh location outside the source checkout. Confirm that it can find its Python runtime/dependencies and backend resources, start the backend, generate mock images and export them. A successful `build:web` qualifies only the web assets. If the packaged artifact cannot satisfy these checks, retain source-based experimental Electron instructions rather than advertising a standalone installer.

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
