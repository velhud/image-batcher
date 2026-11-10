"""Headless batch preflight and explicitly requested generation.

Dry-run is the default. All state written by --run lives beneath --output;
this command never imports the HTTP server or restores/replays an old session.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import threading
import time
import uuid
from io import StringIO
from pathlib import Path

from app.batch_parser import parse_batch_input
from app.batch_service import preflight
from app.env import load_dotenv_if_available
from app.exporter import ImageExporter
from app.generation_state import TERMINAL_EVENTS, apply_generation_event
from app.models import GlobalSettings, RowData, RowStatus, validate_session
from app.providers.registry import ProviderRegistry
from app.queue_manager import GenerationQueue
from app.session_manager import SessionManager


MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an ImageAI batch without network calls; add --run to generate it.",
        epilog="Example: python -m app.cli --input examples/demo-prompts.csv --mode csv --output ./demo-output --provider mock --run",
    )
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 prompt file (up to 8 MiB).")
    parser.add_argument("--mode", choices=("csv", "json_array", "json_lines", "lines"), default="csv")
    parser.add_argument("--output", type=Path, required=True, help="Empty directory for session, generated files and a portable bundle.")
    parser.add_argument("--provider", choices=("mock", "openai", "google", "azure-openai"), default="mock")
    parser.add_argument("--model", help="Explicit provider model ID; defaults to the first advertised model.")
    parser.add_argument("--images", type=int, default=1, help="Images per prompt (default: 1).")
    parser.add_argument("--prompt-append", default="", help="Instruction appended to every imported prompt.")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent rows (default: 1).")
    parser.add_argument("--rpm", type=int, help="Quota units/minute (default: 4 for Azure, 60 otherwise).")
    parser.add_argument("--timeout", type=float, default=900, help="Total run deadline in seconds (default: 900).")
    parser.add_argument("--run", action="store_true", help="Execute generation; without this flag only validate and print a plan.")
    return parser


def _check_input_shape(raw: str, mode: str) -> None:
    """Reject malformed records as a whole instead of silently dropping them."""
    if mode == "csv":
        records = list(csv.reader(StringIO(raw), strict=True))
        if not records or "prompt" not in records[0]:
            raise ValueError("CSV requires a header containing a 'prompt' column.")
        header = records[0]
        if len(header) != len(set(header)):
            raise ValueError("CSV headers must be unique.")
        prompt_index = header.index("prompt")
        for number, record in enumerate(records[1:], start=2):
            if not record:  # Ordinary blank lines are harmless.
                continue
            if len(record) != len(header):
                raise ValueError(f"CSV line {number} has {len(record)} columns; expected {len(header)}. Quote prompts containing commas.")
            if not record[prompt_index].strip():
                raise ValueError(f"CSV line {number} has an empty prompt.")
    elif mode == "json_array":
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError("A JSON array is required.")
        for number, value in enumerate(values, start=1):
            prompt = value.get("prompt") if isinstance(value, dict) else value
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"JSON item {number} must be a nonempty string or an object with a text 'prompt'.")
    elif mode == "json_lines":
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("prompt"), str) or not value["prompt"].strip():
                raise ValueError(f"JSON line {number} requires a nonempty text 'prompt'.")


def _read_rows(path: Path, mode: str, prompt_append: str) -> list[RowData]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds the 8 MiB limit; split the batch into smaller files.")
    data = path.read_bytes()
    raw = data.decode("utf-8-sig")
    _check_input_shape(raw, mode)
    parsed = parse_batch_input(raw, mode=mode, csv_column="prompt", prompt_append=prompt_append)
    if parsed.errors:
        raise ValueError("; ".join(parsed.errors))
    if not parsed.rows:
        raise ValueError("No prompts were parsed.")
    if len(parsed.rows) > MAX_ROWS:
        raise ValueError(f"Input contains more than {MAX_ROWS} prompts; split the batch.")
    source_hash = hashlib.sha256(data).hexdigest()
    return [
        RowData(
            id=str(uuid.uuid4()), prompt=item.prompt,
            prompt_id=item.prompt_id or f"prompt{number:04d}", category_id=item.category_id,
            source_metadata={**item.source_metadata, "source_file": path.name, "source_sha256": source_hash},
        )
        for number, item in enumerate(parsed.rows, start=1)
    ]


def _print_summary(summary: dict) -> None:
    print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))


def _run(rows: list[RowData], settings: GlobalSettings, registry: ProviderRegistry, output: Path, timeout: float, plan: dict) -> tuple[int, dict]:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("The output directory is not empty. Choose a new directory to preserve previous runs.")
    output.mkdir(parents=True, exist_ok=True)
    previous_data_dir = os.environ.get("IMAGEAI_DATA_DIR")
    os.environ["IMAGEAI_DATA_DIR"] = str(output / "state")
    queue = None
    started = time.monotonic()
    persistence_errors: list[str] = []
    try:
        manager = SessionManager()
        manager.session.global_settings = settings
        manager.session.rows = rows
        validate_session(manager.session)
        session_path = output / "session.json"
        manager.save(session_path)
        queue = GenerationQueue(registry, manager.session_output_dir(), settings.concurrency_limit, settings.rate_limit_rpm)
        condition = threading.Condition(threading.RLock())
        terminal_rows: set[str] = set()
        row_by_id = {row.id: row for row in rows}

        def on_event(event: str, row_id: str, payload: dict) -> None:
            if event == "queue_position":
                return
            with condition:
                row = row_by_id[row_id]
                apply_generation_event(row, event, payload)
                try:
                    manager.save(session_path)
                except (OSError, ValueError, TypeError) as exc:
                    persistence_errors.append(str(exc))
                if event in TERMINAL_EVENTS:
                    terminal_rows.add(row_id)
                condition.notify_all()
            if persistence_errors:
                queue.cancel_all()

        queue.register_listener(on_event)
        timed_out = interrupted = False
        deadline = started + timeout
        try:
            for number, row in enumerate(rows, start=1):
                if time.monotonic() >= deadline or persistence_errors:
                    timed_out = time.monotonic() >= deadline
                    break
                queue.enqueue(row, settings, row_index=number)
            with condition:
                while len(terminal_rows) != len(rows) and not persistence_errors and not timed_out:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    condition.wait(timeout=min(remaining, 0.25))
        except KeyboardInterrupt:
            interrupted = True
        finally:
            stopped = queue.shutdown(timeout=2.0)
        with condition:
            for row in rows:
                if row.status in {RowStatus.QUEUED, RowStatus.GENERATING}:
                    row.status = RowStatus.INTERRUPTED
                    row.error_message = "CLI stopped before the provider outcome was recorded. Review saved artifacts and provider history before explicitly retrying."
                elif row.status == RowStatus.IDLE:
                    row.status = RowStatus.CANCELLED
                    row.error_message = "CLI stopped before this row was admitted."
            try:
                manager.save(session_path)
            except (OSError, ValueError, TypeError) as exc:
                persistence_errors.append(str(exc))
            bundle = None
            # Avoid copying while an in-flight provider could still be writing.
            if stopped and not persistence_errors:
                bundle = ImageExporter(settings.naming_pattern).export_bundle(manager.session, output)
            failures = [
                {"row_id": row.id, "prompt_id": row.prompt_id, "status": row.status.value, "message": row.error_message}
                for row in rows if row.status != RowStatus.COMPLETED
            ]
            status = "timed_out" if timed_out else "cancelled" if interrupted else "failed" if failures or persistence_errors or not stopped else "completed"
            summary = {
                "status": status, "dry_run": False, "session_id": manager.session.id,
                "provider": settings.provider_id, "model": settings.model_id, "row_count": len(rows),
                "planned_images": plan["total_images"], "provider_requests_planned": plan["total_requests"],
                "completed_rows": sum(row.status == RowStatus.COMPLETED for row in rows),
                "images_saved": sum(len(row.images) for row in rows), "failures": failures,
                "session_path": str(session_path), "bundle_path": bundle["path"] if bundle else None,
                "provider_work_stopped": stopped, "errors": list(dict.fromkeys(persistence_errors)),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
            return (0 if status == "completed" else 1), summary
    finally:
        if queue is not None and queue.running:
            queue.shutdown(timeout=2.0)
        if previous_data_dir is None:
            os.environ.pop("IMAGEAI_DATA_DIR", None)
        else:
            os.environ["IMAGEAI_DATA_DIR"] = previous_data_dir


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("--timeout must be a positive, finite number of seconds.")
        rows = _read_rows(args.input, args.mode, args.prompt_append)
        registry = ProviderRegistry()
        provider = registry.get(args.provider)
        model = args.model or provider.available_models()[0].id
        settings = GlobalSettings(
            provider_id=args.provider, model_id=model, num_images=args.images,
            concurrency_limit=args.concurrency,
            rate_limit_rpm=args.rpm if args.rpm is not None else (4 if args.provider == "azure-openai" else 60),
        )
        plan = preflight(rows, settings, registry)
        if not plan["valid"]:
            _print_summary({"status": "invalid", "dry_run": not args.run, "errors": [
                {"prompt_id": row["prompt_id"], "messages": row["errors"]} for row in plan["rows"] if row["errors"]
            ]})
            return 2
        output = args.output.expanduser().resolve()
        if not args.run:
            _print_summary({
                "status": "ready", "dry_run": True, "provider": settings.provider_id,
                "model": settings.model_id, "row_count": len(rows),
                "planned_images": plan["total_images"], "provider_requests_planned": plan["total_requests"],
                "output": str(output), "errors": [],
            })
            return 0
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise ValueError("The output directory is not empty. Choose a new directory to preserve previous runs.")
    except (ValueError, KeyError, TypeError, OSError, csv.Error) as exc:
        _print_summary({"status": "invalid", "dry_run": not args.run, "errors": [str(exc)]})
        return 2
    try:
        load_dotenv_if_available()
        code, summary = _run(rows, settings, registry, output, args.timeout, plan)
        _print_summary(summary)
        return code
    except Exception as exc:
        _print_summary({"status": "failed", "dry_run": False, "errors": [str(exc)]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
