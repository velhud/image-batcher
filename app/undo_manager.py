from __future__ import annotations

import copy
from typing import List, Optional

from app.models import RowStatus, SessionData


class UndoManager:
    """Simple undo/redo stack storing SessionData snapshots."""

    def __init__(self, max_depth: int = 30) -> None:
        self.max_depth = max_depth
        self.stack: List[dict] = []
        self.redo_stack: List[dict] = []
        self._applied_jobs: dict[tuple[str, str], list[str]] = {}

    def _remember_receipts(self, session: SessionData) -> None:
        # Applying a durable receipt is not an editable operation. Undo may
        # remove an image from the session, but must never make that receipt
        # look unprocessed and resurrect the image on the next restore.
        for row in session.rows:
            key = (session.id, row.id)
            self._applied_jobs[key] = list(dict.fromkeys([
                *self._applied_jobs.get(key, []), *row.applied_job_ids,
            ]))

    def _restore(self, snapshot: dict) -> SessionData:
        session = SessionData.from_dict(copy.deepcopy(snapshot))
        self._remember_receipts(session)
        for row in session.rows:
            row.applied_job_ids = list(self._applied_jobs[(session.id, row.id)])
            if row.status in {RowStatus.QUEUED, RowStatus.GENERATING}:
                row.status = RowStatus.INTERRUPTED
                row.error_message = "Restored an earlier active state; review saved outputs before retrying."
        return session

    def push(self, session: SessionData) -> None:
        self._remember_receipts(session)
        snapshot = copy.deepcopy(session.to_dict())
        if self.stack and self.stack[-1] == snapshot:
            return
        self.stack.append(snapshot)
        if len(self.stack) > self.max_depth:
            self.stack.pop(0)
        self.redo_stack.clear()

    def undo(self, session: Optional[SessionData] = None) -> Optional[SessionData]:
        if session is not None:
            self._remember_receipts(session)
        if not self.stack:
            return None
        current = self.stack.pop()
        self.redo_stack.append(current)
        if not self.stack:
            return self._restore(current)
        return self._restore(self.stack[-1])

    def redo(self, session: Optional[SessionData] = None) -> Optional[SessionData]:
        if session is not None:
            self._remember_receipts(session)
        if not self.redo_stack:
            return None
        snapshot = self.redo_stack.pop()
        self.stack.append(snapshot)
        return self._restore(snapshot)
