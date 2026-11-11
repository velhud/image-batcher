from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Dict, Any, List
from urllib.parse import parse_qs, urlparse

from app.azure_logo_workflow import AZURE_LOGO_PROMPT_WRAPPER, apply_azure_logo_preset
from app.batch_parser import parse_batch_input
from app.generation_log import read_generation_log, verify_rows
from app.models import InputAttachment, GlobalSettings, RowData, RowStatus, SessionData, RowSettings, session_storage_dir, validate_settings, validate_session, effective_settings
from app.env import load_dotenv_if_available
from app.providers.registry import ProviderRegistry
from app.queue_manager import GenerationQueue
from app.session_manager import SessionManager
from app.stats import compute_stats
from app.exporter import ImageExporter
from app.undo_manager import UndoManager
from app.batch_service import preflight
from app.generation_state import apply_generation_event

class BackendState:
    def __init__(self, *, restore: bool = True) -> None:
        load_dotenv_if_available()
        self._lock = threading.RLock()
        self.registry = ProviderRegistry()
        self.session_manager = SessionManager()
        if restore:
            self.session_manager.restore_last()
        self.session = self.session_manager.session
        self.undo_manager = UndoManager()
        self.undo_manager.push(self.session)
        self._queue_epoch = 0
        self.queue = self._create_queue()

    def _create_queue(self) -> GenerationQueue:
        self._queue_epoch += 1
        epoch = self._queue_epoch
        queue = GenerationQueue(self.registry, self.session_manager.session_output_dir(),
                                self.session.global_settings.concurrency_limit,
                                self.session.global_settings.rate_limit_rpm)
        previous = getattr(self, "queue", None)
        if previous is not None:
            queue.rate_limiter = previous.rate_limiter
            queue.rate_limiter.set_rpm(self.session.global_settings.rate_limit_rpm)
        queue.register_listener(lambda event, row_id, payload: self._handle_event(event, row_id, payload, epoch))
        return queue

    def require_idle(self) -> None:
        if not self.queue.is_idle():
            raise ValueError("Wait for active work to finish, or stop the queue before changing sessions or concurrency")

    def replace_session(self, session: SessionData, path: Path | None = None) -> None:
        self.require_idle()
        self.queue.shutdown()
        self.session = self.session_manager.session = session
        if path is not None:
            self.session_manager.last_path = path
        self.queue = self._create_queue()
        self.undo_manager = UndoManager()
        self.undo_manager.push(session)
        self.session_manager.autosave()

    def _handle_event(self, event: str, row_id: str, payload: Dict, epoch: int | None = None) -> None:
        if event == "queue_position":
            return
        with self._lock:
            if epoch is not None and epoch != self._queue_epoch:
                return
            row = self._row_by_id(row_id)
            if not row:
                return
            apply_generation_event(row, event, payload)
            self.session_manager.autosave()

    def dispatch(self, action: str, data: Dict[str, Any]) -> Any:
        if not isinstance(action, str) or not isinstance(data, dict):
            raise ValueError("RPC requires a text action and an object data payload")
        with self._lock:
            return self._dispatch(action, data)

    def _row_by_id(self, row_id: str) -> RowData | None:
        for row in self.session.rows:
            if row.id == row_id:
                return row
        return None

    def push_state(self):
        with self._lock:
            self.undo_manager.push(self.session)
            self.session_manager.autosave()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session": self.session.to_dict(),
                "providers": [p.to_dict() for p in self.registry.info_list()],
                "stats": compute_stats(self.session.rows).to_dict(),
                "restore_warning": self.session_manager.restore_error,
            }

    def _dispatch(self, action: str, data: Dict[str, Any]) -> Any:
        if action == "state":
            return self.to_dict()
            
        # --- Session Management ---
        if action == "new_session":
            self.replace_session(SessionData(id=str(uuid.uuid4()), global_settings=GlobalSettings(), rows=[]))
            return self.to_dict()

        if action == "save_session":
            path = data.get("path")
            if path:
                self.session_manager.save(Path(path))
            else:
                self.session_manager.autosave()
            return {"saved": True}

        if action == "load_session":
            path = Path(data["path"])
            self.require_idle()
            candidate = SessionManager.read(path)
            self.replace_session(candidate, path)
            return self.to_dict()

        if action in {"undo", "redo"}:
            self.require_idle()
            restored = self.undo_manager.undo(self.session) if action == "undo" else self.undo_manager.redo(self.session)
            if restored:
                self.queue.shutdown()
                self.session = self.session_manager.session = restored
                for row in restored.rows:
                    if row.status in {RowStatus.QUEUED, RowStatus.GENERATING}:
                        row.status = RowStatus.INTERRUPTED
                self.queue = self._create_queue()
                self.session_manager.autosave()
            return self.to_dict()

        # --- Row Operations ---
        if action == "add_rows":
            prompts: List[str] = data.get("prompts", [])
            rows_data: List[Dict[str, Any]] = data.get("rows", [])
            if not isinstance(prompts, list) or any(not isinstance(prompt, str) for prompt in prompts):
                raise ValueError("prompts must be an array of strings")
            if not isinstance(rows_data, list) or any(not isinstance(item, dict) or not isinstance(item.get("prompt", ""), str) for item in rows_data):
                raise ValueError("rows must be an array of objects with text prompts")
            self.push_state()
            if rows_data:
                for item in rows_data:
                    row = RowData(
                        id=str(uuid.uuid4()),
                        prompt=str(item.get("prompt", "")),
                        prompt_id=str(item.get("prompt_id", "")),
                        category_id=str(item.get("category_id", "")),
                        source_metadata=item.get("source_metadata", {}),
                    )
                    self.session.rows.append(row)
            else:
                for prompt in prompts:
                    row = RowData(id=str(uuid.uuid4()), prompt=prompt)
                    self.session.rows.append(row)
            self.push_state()
            return {"rows": [r.to_dict() for r in self.session.rows]}

        if action == "parse_batch":
            result = parse_batch_input(
                data.get("raw", ""),
                mode=data.get("mode", "lines"),
                prompt_field=data.get("prompt_field", "prompt"),
                csv_column=data.get("csv_column", "prompt"),
                prompt_append=data.get("prompt_append", ""),
            )
            return {
                "prompts": result.prompts,
                "errors": result.errors,
                "rows": [row.__dict__ for row in result.rows],
            }

        if action == "import_batch":
            result = parse_batch_input(
                data.get("raw", ""),
                mode=data.get("mode", "lines"),
                prompt_field=data.get("prompt_field", "prompt"),
                csv_column=data.get("csv_column", "prompt"),
                prompt_append=data.get("prompt_append", ""),
            )
            if result.errors:
                return {"rows": [r.to_dict() for r in self.session.rows], "errors": result.errors}
            self.push_state()
            apply_mode = data.get("apply_mode", "append")
            if apply_mode not in {"append", "replace", "fill"}:
                raise ValueError("apply_mode must be append, replace, or fill")
            if apply_mode == "replace":
                self.require_idle()
                self.session.rows.clear()
            pending_rows = list(result.rows)
            if apply_mode == "fill":
                empty_rows = [r for r in self.session.rows if not r.prompt]
                for row, parsed in zip(empty_rows, pending_rows):
                    row.prompt = parsed.prompt
                    row.prompt_id = parsed.prompt_id
                    row.category_id = parsed.category_id
                    row.source_metadata = parsed.source_metadata
                pending_rows = pending_rows[len(empty_rows):]
            for parsed in pending_rows:
                self.session.rows.append(
                    RowData(
                        id=str(uuid.uuid4()),
                        prompt=parsed.prompt,
                        prompt_id=parsed.prompt_id,
                        category_id=parsed.category_id,
                        source_metadata=parsed.source_metadata,
                    )
                )
            self.push_state()
            return {"rows": [r.to_dict() for r in self.session.rows], "errors": result.errors}

        if action == "update_row":
            # Don't push undo for every keystroke, handle debounce in frontend or specific commit action
            # For simplicity, we push state if 'final' flag is sent or specific fields change
            rid = data["row_id"]
            row = self._row_by_id(rid)
            if not row:
                raise ValueError("Row not found")
            candidate = RowData.from_dict(row.to_dict())
            row = candidate
            
            if "prompt" in data:
                row.prompt = data["prompt"]
            if "prompt_id" in data:
                row.prompt_id = str(data["prompt_id"] or "")
            if "category_id" in data:
                row.category_id = str(data["category_id"] or "")
            if "source_metadata" in data:
                row.source_metadata = data.get("source_metadata") or {}
            if "selected" in data:
                row.selected = bool(data["selected"])
            if "tags" in data:
                row.tags = data["tags"]
            if "attachments" in data:
                row.attachments = [InputAttachment.from_dict(a) for a in data.get("attachments", [])]
            
            # Row Settings
            if "settings" in data:
                # Merge logic
                new_settings = data["settings"]
                current = row.settings.to_dict()
                current.update(new_settings)
                # Remove nulls if strictly merging? Or replace? 
                # Model handles None as "inherit global".
                row.settings = RowSettings(**current)

            candidate_session = SessionData(id=self.session.id, global_settings=self.session.global_settings, rows=[row])
            validate_session(candidate_session)
            original = self._row_by_id(rid)
            self.session.rows[self.session.rows.index(original)] = row
            self.session_manager.autosave()
            return row.to_dict()

        if action == "delete_rows":
            self.require_idle()
            self.push_state()
            ids = set(data.get("row_ids", []))
            self.session.rows = [r for r in self.session.rows if r.id not in ids]
            self.push_state()
            return {"rows": [r.to_dict() for r in self.session.rows]}

        if action == "duplicate_row":
            self.push_state()
            rid = data.get("row_id")
            row = self._row_by_id(rid)
            if row:
                clone = RowData.from_dict(row.to_dict())
                clone.id = str(uuid.uuid4())
                clone.images = [] # Don't clone images
                clone.status = RowStatus.IDLE
                idx = self.session.rows.index(row) + 1
                self.session.rows.insert(idx, clone)
            self.push_state()
            return self.to_dict()

        # --- Generation ---
        if action in {"preflight_sample", "generate_sample"}:
            row = self._row_by_id(data["row_id"])
            if row is None:
                raise ValueError("The selected sample row is no longer in the session.")
            if row.status in {RowStatus.QUEUED, RowStatus.GENERATING, RowStatus.INTERRUPTED}:
                raise ValueError("Choose an idle row without an interrupted request for the sample.")
            report = preflight([row], self.session.global_settings, self.registry, sample=True)
            if action == "preflight_sample":
                return report
            if not report["valid"]:
                raise ValueError("Sample validation failed: " + "; ".join(report["rows"][0]["errors"]))
            queued = self.queue.enqueue(row, self.session.global_settings,
                                        row_index=self.session.rows.index(row) + 1, sample=True)
            return {"queued": [row.id] if queued else []}

        if action == "preflight":
            ids = set(data.get("row_ids") or [row.id for row in self.session.rows])
            return preflight([row for row in self.session.rows if row.id in ids], self.session.global_settings, self.registry)

        if action in {"generate_rows", "generate_missing", "retry_failed"}:
            ids = data.get("row_ids")
            if ids is None:
                ids = [row.id for row in self.session.rows if action != "retry_failed" or row.status in {RowStatus.ERROR, RowStatus.FILTERED}]
            rows = [self._row_by_id(rid) for rid in dict.fromkeys(ids)]
            rows = [row for row in rows if row is not None]
            if action == "retry_failed":
                rows = [row for row in rows if row.status in {RowStatus.ERROR, RowStatus.FILTERED}]
            missing_only = action == "generate_missing" or bool(data.get("missing_only", False))
            if missing_only:
                rows = [row for row in rows if row.status not in {RowStatus.QUEUED, RowStatus.GENERATING, RowStatus.INTERRUPTED}]
            report = preflight(rows, self.session.global_settings, self.registry)
            if not report["valid"]:
                raise ValueError("Batch validation failed: " + "; ".join(f"{r['prompt_id'] or r['row_id']}: {', '.join(r['errors'])}" for r in report["rows"] if r["errors"]))
            queued = []
            for row in rows:
                if self.queue.enqueue(row, self.session.global_settings, row_index=self.session.rows.index(row) + 1,
                                      missing_only=missing_only,
                                      regenerate=action == "generate_rows" and data.get("regenerate") is True):
                    queued.append(row.id)
            return {"queued": queued}

        if action == "cancel_row":
            self.queue.cancel(data["row_id"])
            return {"cancelled": True}
        if action == "stop_all":
            self.queue.cancel_all()
            return {"stopped": True}
        if action == "stop_after_current":
            self.queue.stop_after_current()
            return {"stopped_after_current": True}

        # --- Global Settings ---
        if action in {"global_settings", "apply_azure_logo_preset"}:
            current = self.session.global_settings.to_dict()
            if action == "global_settings":
                unknown = set(data) - set(current)
                if unknown:
                    raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
                current.update(data)
                candidate = GlobalSettings.from_dict(current)
            else:
                candidate = apply_azure_logo_preset(GlobalSettings.from_dict(current))
            validate_settings(candidate)
            changed_concurrency = candidate.concurrency_limit != self.queue.concurrency
            if changed_concurrency:
                self.require_idle()
            self.push_state()
            self.session.global_settings = candidate
            self.queue.rate_limiter.set_rpm(candidate.rate_limit_rpm)
            if changed_concurrency:
                self.queue.shutdown()
                self.queue = self._create_queue()
            self.push_state()
            return candidate.to_dict()

        if action == "provider_status":
            azure_missing = []
            for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_GPT_IMAGE_2_DEPLOYMENT"):
                if not os.environ.get(key):
                    azure_missing.append(key)
            return {
                "azure_openai": {
                    "configured": not azure_missing,
                    "missing": azure_missing,
                    "endpoint": os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
                    "deployment": os.environ.get("AZURE_GPT_IMAGE_2_DEPLOYMENT", ""),
                    "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "preview"),
                },
                "azure_logo_wrapper": AZURE_LOGO_PROMPT_WRAPPER,
            }

        if action == "verify_batch":
            return verify_rows(self.session.rows, self.session.global_settings)

        if action == "generation_log":
            log_path = self.session_manager.session_output_dir().parent / "generation_log.jsonl"
            return {"rows": read_generation_log(log_path), "path": str(log_path)}

        if action == "export_bundle":
            ids = data.get("row_ids")
            rows = self.session.rows if ids is None else [row for row in self.session.rows if row.id in ids]
            return ImageExporter(self.session.global_settings.naming_pattern).export_bundle(self.session, Path(data["folder"]), rows)

        if action == "export":
            folder = Path(data.get("folder", str(Path.home() / "Pictures")))
            rows = [self._row_by_id(rid) for rid in data.get("row_ids", [])]
            rows = [r for r in rows if r]
            image_id = data.get("image_id")
            if image_id is not None:
                rows = [replace(row, images=[image for image in row.images if image.id == image_id]) for row in rows]
                if not any(row.images for row in rows):
                    raise ValueError("The selected image is no longer in the session.")
            exporter = ImageExporter(self.session.global_settings.naming_pattern)
            count, _ = exporter.export_rows(rows, folder, export_metadata=True)
            return {"exported": count}

        # --- Templates ---
        if action in {"save_template", "expand_template", "apply_template"}:
            from app.models import PromptTemplate
            from app.templates import expand_template

            template = PromptTemplate.from_dict(data)
            if action == "expand_template":
                return {"prompts": expand_template(template)}
            if action == "apply_template":
                prompts = expand_template(template)
                if not prompts or any(not prompt.strip() for prompt in prompts):
                    raise ValueError("Template expansion must produce nonempty prompts.")
                self.session.rows.extend(RowData(id=str(uuid.uuid4()), prompt=prompt) for prompt in prompts)
            self.session.templates.append(template)
            self.push_state()
            return {"templates": [item.to_dict() for item in self.session.templates]}

        raise ValueError(f"Unknown action: {action}")

STATE = BackendState()

def _json_response(handler: BaseHTTPRequestHandler, body: Dict, status: int = 200) -> None:
    data = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    _set_cors_headers(handler)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

def _origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return True
    configured = os.environ.get("IMAGEAI_ALLOWED_ORIGINS")
    if configured is not None:
        return origin in {item.strip() for item in configured.split(",") if item.strip()}
    return origin in {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"}


def _set_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = handler.headers.get("Origin")
    if origin and _origin_allowed(origin):
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        if not _origin_allowed(self.headers.get("Origin")):
            self.send_error(403, "Origin is not allowed")
            return
        self.send_response(200)
        _set_cors_headers(self)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/images":
            self.send_error(404)
            return

        query = parse_qs(parsed.query or "")
        raw_path = query.get("path", [None])[0]
        if not raw_path:
            self.send_error(400, "missing path")
            return

        file_path = Path(raw_path)
        with STATE._lock:
            allowed = {Path(item.file_path).resolve() for row in STATE.session.rows for item in [*row.images, *row.attachments] if item.file_path}
        if file_path.resolve() not in allowed:
            self.send_error(403)
            return

        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return

        data = file_path.read_bytes()
        mime, _ = mimetypes.guess_type(file_path.name)
        self.send_response(200)
        _set_cors_headers(self)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            # Client disconnected
            pass

    def do_POST(self) -> None:
        if not _origin_allowed(self.headers.get("Origin")):
            _json_response(self, {"ok": False, "error": "Origin is not allowed"}, 403)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            _json_response(self, {"ok": False, "error": "Content-Type must be application/json"}, 415)
            return
        if urlparse(self.path).path != "/rpc":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(self, {"ok": False, "error": "Invalid Content-Length"}, 400)
            return
        if length < 1 or length > 16 * 1024 * 1024:
            _json_response(self, {"ok": False, "error": "Request body must be 1 byte to 16 MiB"}, 413)
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            _json_response(self, {"error": "invalid json"}, 400)
            return
        if not isinstance(payload, dict):
            _json_response(self, {"ok": False, "error": "RPC payload must be an object"}, 400)
            return
        action = payload.get("action")
        data = payload.get("data", {})

        try:
            result = STATE.dispatch(action, data)
            _json_response(self, {"ok": True, "data": result})
        except (ValueError, KeyError, TypeError) as exc:
            _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[!] ERROR in {action}: {exc}")
            _json_response(self, {"ok": False, "error": str(exc)}, 500)


class LoopbackHTTPServer(HTTPServer):
    def server_bind(self) -> None:
        # HTTPServer resolves a host name during bind. The desktop backend
        # already uses a numeric loopback address and needs no DNS lookup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def run_server(port: int = 8765) -> None:
    server = LoopbackHTTPServer(("127.0.0.1", port), Handler)
    print(f"IMAGEAI_READY {server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        STATE.queue.shutdown()
        STATE.session_manager.autosave()
        server.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Image Batcher local backend.")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port. Use 0 to select an available port.")
    run_server(parser.parse_args().port)
