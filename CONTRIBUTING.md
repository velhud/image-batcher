# Contributing to ImageAI

ImageAI focuses on dependable structured image batches and portable results. Read [Architecture](docs/ARCHITECTURE.md) before changing generation, persistence, or export behavior, and [Provider reference](docs/PROVIDERS.md) before adding provider options.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
```

Use `IMAGEAI_DATA_DIR` for a disposable workspace during manual tests. The mock provider exercises the full local workflow without credentials. Run the CLI demo and the client checks described in [Verification](docs/VERIFICATION.md) for changes that affect those surfaces.

## Change requirements

- Keep provider capability declarations and request validation aligned. Reject unsupported explicit selections; do not silently switch models, providers, or output options.
- Preserve exact provider image bytes and associate results with the immutable request that produced them. Current row edits must not relabel historical results.
- Treat an ambiguous paid request as an uncertain outcome. Do not add automatic retries for transport failures without provider-supported idempotency and a verified contract.
- Maintain cancellation and shutdown invariants: no provider call after pending cancellation, no lost late result, no unbounded UI wait, and no stale callback mutating another session.
- Retain atomic saves, valid backup recovery, receipt idempotence, non-overwriting output publication, and export hash checks.
- Keep parsing and generation state rules in the Python core so alternate clients do not grow conflicting implementations.
- Add focused regressions for a bug's observable failure. A mocked provider test should assert the wire contract and artifact behavior, not merely repeat implementation logic.
- Use primary provider documentation with a verification date when changing models or capabilities. Live account tests are a separate gate from offline contract tests.

For frontend changes, run `npm ci --ignore-scripts`, `npm run typecheck`, `npm run build:web`, and the supplied browser suite after installing Playwright Chromium. Electron packaging must also be exercised on the affected platform. SwiftUI changes require the native Mac test/build gates.

In a pull request, explain the user-visible problem, resulting behavior, evidence of validation, and any gate still outstanding. Preserve existing session compatibility unless the change includes an explicit migration plan. Use [SECURITY.md](SECURITY.md) for reporting a security issue.
