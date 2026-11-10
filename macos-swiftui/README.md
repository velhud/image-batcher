# ImageAI native macOS client — experimental

This SwiftUI executable is an experimental client for the same Python backend used by the React application. It provides provider/model selection, prompt editing, batch import, generation/stop controls, current row state, and a preview of the latest saved image. The backend remains responsible for parsing, validation, provider requests, persistence, and recovery.

**Validation status:** this upgrade was prepared in a Linux environment without Swift or the macOS SDK. The Swift sources and XCTest cases have not been compiled or executed here. A successful macOS build, test run, and the manual checks below are required before shipping the native executable. The Python and React test results do not validate SwiftUI behavior.

## Requirements and startup

Use macOS 13 or later and a Swift 5.9 or later toolchain with the macOS SDK. Install the Python backend dependencies in the repository's virtual environment. Run these commands from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
swift build --package-path macos-swiftui
swift test --package-path macos-swiftui
IMAGEAI_PROJECT_ROOT="$PWD" IMAGEAI_PYTHON="$PWD/.venv/bin/python" \
  swift run --package-path macos-swiftui ImagenNative
```

The app uses `http://127.0.0.1:8765/rpc`. It first checks for a working ImageAI backend. If one is reachable, it uses that process. Otherwise it starts `python -m app.backend_server` with the repository as the working directory.

`IMAGEAI_PROJECT_ROOT` selects the repository explicitly. Without it, the launcher searches up to six ancestors of the current directory and the source file path embedded at build time. `IMAGEAI_PYTHON` selects an absolute Python executable path. Otherwise the launcher uses `.venv/bin/python` inside the repository when available, then `python3` from `PATH`. Set both variables for a relocated checkout or a built executable outside the source tree.

For a separately managed backend, start it from the repository root before opening the native app:

```sh
.venv/bin/python -m app.backend_server
```

State refresh runs while the view is active and visible: every 0.5 seconds during queued/generating work and every two seconds while idle. After five consecutive failed refreshes the app displays an error and stops polling until **Reload** or reactivation. Each state request has a bounded timeout. Generation and other mutations are never automatically retried. Closing or deactivating the native view stops its polling; it does not terminate the shared backend or cancel its jobs. Use **Stop All** to request cancellation.

## What changed

- Provider and model pickers now write actual catalog IDs. Switching models clears unsupported settings and caps image count using the backend's capabilities.
- Batch preview and application call `parse_batch` and `import_batch`. Quoted/multiline CSV, IDs, categories, source metadata, prompt append text, and append/replace/fill behavior use the shared backend contract. Changing the inputs requires another preview before Apply.
- Prompt edits remain visible as drafts. **Save Prompt**, generation, and batch import save them explicitly. Empty edits can be saved. Fill-empty import first saves drafts so it uses the text visible in the app.
- All RPC operations check HTTP status and the `ok` envelope. Generation, deletion, stop, settings, and import failures surface an error instead of appearing successful.
- Active rows are excluded from new bulk generation requests. Interrupted rows show an unknown-outcome message and require explicit confirmation before a fresh single or bulk generation request.
- Latest image previews, image counts, and **Open image** use saved local output paths. Recovery warnings and row errors are visible.

This is not native feature parity. The PySide and React clients remain the interfaces for attachment editing, detailed per-row generation settings, richer preflight, and release workflow tools. Existing per-row settings and attachments stay in the backend; this native client does not edit them. A retired model in a saved row override must be corrected through a client that exposes that override.

## Required macOS acceptance checks

Use the **Mock** provider for these checks so they require no paid image requests.

| Check | Expected result |
| --- | --- |
| Build and tests | `swift build --package-path macos-swiftui` and `swift test --package-path macos-swiftui` exit successfully. The contract suite exercises RPC errors, successful responses, exact batch payloads, metadata decoding, nullable setting resets, model selection, and Interrupted review state. |
| Startup and recovery | Launch with and without a separately running backend. State appears in both cases. Stop the backend, observe the bounded refresh error, restart it, and use Reload to reconnect. |
| Provider/model selection | Pick a provider and a current model, then Reload. Both selections persist. Return to Mock before generating. |
| Prompt editing | Edit a prompt, save it, and Reload. Clear the prompt completely, save, and Reload; it stays empty. Type a new draft and Generate; the saved request uses that draft. |
| Shared CSV import | Preview the sample below as CSV, with `prompt` as the column. The comma and newline remain within one prompt, and `logo-1` / `brand` appear. Apply and verify the same metadata in the backend session. |
| Apply modes | On a disposable session, verify Append adds rows, Fill fills empty rows then appends excess input, and Replace replaces existing rows. Changing the mode after preview requires another Preview. A parse error prevents Apply. |
| Queue and cancellation | Generate a Mock row and observe status refresh and its image preview without Reload. Select multiple rows and generate. Stop All during active work and check the terminal states. |
| Interrupted generation | Load a fixture session containing an Interrupted row. Single, selected, and all-row generation require confirmation. Cancel sends no generation request. Explicit confirmation permits a new request. |
| Error visibility | Trigger a backend validation failure or stop the backend before a mutation. An error is shown. Failed settings saves keep the settings sheet open. Failed imports require a fresh preview before another attempt. |
| View lifecycle | Deactivate or close the native view and verify state polling stops. Reactivate it and verify polling resumes. The backend continues already queued jobs. |

CSV sample:

```csv
prompt_id,category_id,prompt,campaign
logo-1,brand,"A blue, round mark
with a white border",winter
```

Record the macOS version, Xcode/Swift version, `swift build` and `swift test` output, and manual-check results with the release evidence. Do not mark this client ready for native distribution until those checks pass.
