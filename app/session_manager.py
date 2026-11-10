from __future__ import annotations

import json
import time
import uuid
import threading
import hashlib
from pathlib import Path
from typing import List

from app.models import (
    GlobalSettings,
    RowData,
    RowStatus,
    SessionData,
    default_session_path,
    session_storage_dir,
    validate_session,
)
from app.storage import atomic_write_bytes
from app.generation_state import apply_generation_event


class SessionManager:
    def __init__(self) -> None:
        self.session: SessionData = SessionData(
            id=str(uuid.uuid4()),
            global_settings=GlobalSettings(),
            rows=[],
        )
        self.last_path: Path = default_session_path()
        self._save_lock = threading.RLock()
        self.restore_error = ""

    def new_session(self) -> SessionData:
        self.session = SessionData(
            id=str(uuid.uuid4()),
            global_settings=GlobalSettings(),
            rows=[],
        )
        return self.session

    def add_rows(self, prompts: List[str]) -> None:
        for prompt in prompts:
            row = RowData(id=str(uuid.uuid4()), prompt=prompt, status=RowStatus.IDLE)
            self.session.rows.append(row)

    def save(self, path: Path | None = None) -> Path:
        with self._save_lock:
            path = Path(path or self.last_path)
            validate_session(self.session)
            self.session.updated_at = time.time()
            payload = self.session.to_json().encode("utf-8")
            if path.exists():
                previous = path.read_bytes()
                try:
                    validate_session(SessionData.from_dict(json.loads(previous)))
                except (ValueError, TypeError, KeyError, AttributeError):
                    # Preserve the damaged input for inspection, never promote it to backup.
                    atomic_write_bytes(path.with_suffix(path.suffix + ".invalid"), previous)
                else:
                    atomic_write_bytes(path.with_suffix(path.suffix + ".bak"), previous)
            atomic_write_bytes(path, payload)
            self.last_path = path
            return path

    def load(self, path: Path) -> SessionData:
        # Fully parse and validate before replacing the active session.
        candidate = self.read(path)
        self.session = candidate
        self.last_path = Path(path)
        return self.session

    @staticmethod
    def read(path: Path) -> SessionData:
        path = Path(path)
        try:
            candidate = SessionData.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Invalid session structure: {exc}") from exc
        validate_session(candidate)
        directory = path.parent.resolve()
        for row in candidate.rows:
            if row.status in {RowStatus.QUEUED, RowStatus.GENERATING}:
                row.status = RowStatus.INTERRUPTED
                row.error_message = "Previous run was interrupted. Review saved images before explicitly generating again."
            for item in [*row.images, *row.attachments]:
                if item.file_path and not Path(item.file_path).is_absolute():
                    item.file_path = str((directory / item.file_path).resolve())
            for image in row.images:
                references = image.metadata.get("reference_images")
                if not isinstance(references, list):
                    continue
                for reference in references:
                    relative = reference.get("path") if isinstance(reference, dict) else None
                    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
                        continue
                    resolved = (directory / relative).resolve()
                    if not resolved.is_relative_to(directory):
                        raise ValueError("A portable generation reference escapes its session folder.")
                    reference["path"] = str(resolved)
        return candidate

    def autosave(self) -> Path:
        return self.save(default_session_path())

    def restore_last(self) -> SessionData | None:
        path = default_session_path()
        self.restore_error = ""
        if path.exists():
            try:
                self.load(path)
                self._recover_outputs()
                return self.session
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.restore_error = f"Could not restore the latest session: {exc}"
                backup = path.with_suffix(path.suffix + ".bak")
                if backup.exists():
                    try:
                        recovered = self.load(backup)
                        self.last_path = path
                        self.restore_error += " Recovered the previous saved version."
                        self._recover_outputs()
                        return recovered
                    except (OSError, ValueError, TypeError, KeyError):
                        pass
        return None

    def _recover_outputs(self) -> None:
        """Recover only this session's unapplied, hash-verified local run receipts."""
        root = (session_storage_dir() / self.session.id / "images").resolve()
        if not root.is_dir():
            return
        rows = {row.id: row for row in self.session.rows}
        receipts = []
        for path in root.rglob("imageai-run-*.json"):
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(receipt, dict):
                    raise ValueError("Run receipt must be an object")
                if receipt.get("schema_version") != 1 or receipt.get("session_id") != self.session.id:
                    continue
                payload = receipt.get("payload")
                if not isinstance(payload, dict) or not isinstance(receipt.get("job_id"), str):
                    raise ValueError("Invalid run identity or payload")
                if not isinstance(payload.get("requested_at", 0), (int, float)):
                    raise ValueError("Invalid run timestamp")
                for entries in (receipt.get("files", []), payload.get("images", [])):
                    if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) for item in entries):
                        raise ValueError("Invalid run file list")
                if receipt.get("row_id") not in rows:
                    continue
                receipts.append(receipt)
            except (OSError, ValueError, TypeError):
                self.restore_error += " An unreadable run receipt was skipped."
        receipts.sort(key=lambda item: item.get("payload", {}).get("requested_at", 0))
        for receipt in receipts:
            row = rows[receipt["row_id"]]
            job_id = receipt.get("job_id")
            if not job_id or job_id in row.applied_job_ids:
                continue
            event = receipt.get("event")
            if event == "started":
                row.status = RowStatus.INTERRUPTED
                row.error_message = "A previous provider request has no recorded outcome. Review provider history before explicitly retrying."
                continue
            if event not in {"completed", "error", "filtered", "cancelled"}:
                continue
            payload = dict(receipt.get("payload", {}))
            verified = {}
            for item in receipt.get("files", []):
                try:
                    path = Path(item["path"]).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        continue
                    if hashlib.sha256(path.read_bytes()).hexdigest() == item.get("sha256"):
                        verified[str(path)] = item["sha256"]
                except (OSError, KeyError, TypeError):
                    continue
            original_images = payload.get("images", [])
            payload["images"] = [item for item in original_images if str(Path(item.get("path", "")).resolve()) in verified]
            payload["paths"] = [item["path"] for item in payload["images"]]
            payload["job_id"] = job_id
            if len(payload["images"]) != len(original_images):
                event = "error"
                payload["keep_existing"] = True
                payload["message"] = "Some recovered files were missing or had a different SHA-256 hash. Verified files were retained."
            apply_generation_event(row, event, payload)

    def session_output_dir(self) -> Path:
        base = session_storage_dir() / self.session.id
        base.mkdir(parents=True, exist_ok=True)
        images_dir = base / "images"
        images_dir.mkdir(exist_ok=True)
        return images_dir
