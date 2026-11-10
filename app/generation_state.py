"""One event reducer for the desktop and RPC clients."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from app.models import ImageResult, RowData, RowStatus


TERMINAL_EVENTS = {"completed", "error", "filtered", "cancelled"}


def apply_generation_event(row: RowData, event: str, payload: dict) -> None:
    if event == "queue_position":
        return
    images = payload.get("images") or [{"path": p, "metadata": {}} for p in payload.get("paths", [])]
    if event in TERMINAL_EVENTS:
        job_id = payload.get("job_id")
        if job_id and job_id in row.applied_job_ids:
            return
        # A failed or cancelled replacement must not throw away the previous good set.
        if event == "completed" and images and not payload.get("keep_existing", True):
            row.images = []
        existing = {str(Path(image.file_path).resolve()) for image in row.images}
        for image in images:
            path = image.get("path", "")
            if not path or str(Path(path).resolve()) in existing:
                continue
            row.images.append(ImageResult(id=str(uuid.uuid4()), row_id=row.id, file_path=path, metadata=image.get("metadata", {})))
            existing.add(str(Path(path).resolve()))
        if payload.get("duration") is not None:
            row.last_duration = payload["duration"]
        if job_id:
            row.applied_job_ids.append(job_id)
    if event in {"queued", "queue_position"}:
        row.status = RowStatus.QUEUED
        row.error_message = payload.get("message", "")
    elif event == "started":
        row.status = RowStatus.GENERATING
        row.error_message = ""
    elif event == "completed":
        row.status = RowStatus.COMPLETED if images else RowStatus.ERROR
        row.error_message = "" if images else "Provider returned no saved images."
    elif event in {"error", "filtered", "cancelled"}:
        uncertain = payload.get("metadata", {}).get("outcome_unknown", False) or payload.get("metadata", {}).get("status") == "outcome_unknown" or payload.get("status") == "outcome_unknown"
        row.status = RowStatus.INTERRUPTED if uncertain else {"error": RowStatus.ERROR, "filtered": RowStatus.FILTERED, "cancelled": RowStatus.CANCELLED}[event]
        row.error_message = payload.get("message", "Cancelled" if event == "cancelled" else "")
    elif event == "skipped":
        row.error_message = payload.get("message", "")
        if not payload.get("preserve_status") and any(Path(image.file_path).is_file() for image in row.images):
            row.status = RowStatus.COMPLETED
    row.updated_at = time.time()
    if payload.get("warnings"):
        row.error_message = " ".join([row.error_message, *payload["warnings"]]).strip()
