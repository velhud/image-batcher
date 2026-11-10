from __future__ import annotations

import copy
import secrets
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Dict, List, Optional

from app.generation_log import LOG_FILENAME, append_generation_log
from app.models import (
    GeneratedImage, GenerationContext, GlobalSettings, RowData, RowStatus,
    effective_settings, generation_prompt, validate_settings,
)
from app.providers.base import ProviderFilteredError, ProviderGenerationError
from app.providers.image_io import MAX_INLINE_BYTES, MIME_TYPES, inspect_image
from app.providers.registry import ProviderRegistry
from app.scheduler import RateLimiter
from app.storage import atomic_write_bytes


logger = logging.getLogger(__name__)
_GENERATION_FIELDS = (
    "provider_id", "model_id", "size_preset", "custom_size", "style_preset",
    "negative_prompt", "seed", "random_seed", "quality", "safety",
    "thinking_budget", "thinking_level", "prompt_wrapper", "output_format", "background",
)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generation_fingerprint(row: RowData, settings: GlobalSettings) -> str:
    """Identify image inputs independently of requested count and UI controls.

    References are identified by bytes: relocating unchanged input is harmless,
    whereas replacing its contents invalidates previously generated results.
    """
    payload = {
        "prompt": row.prompt,
        "settings": {name: getattr(settings, name) for name in _GENERATION_FIELDS},
        "attachments": [
            {"sha256": _file_hash(Path(item.file_path)), "mime_type": item.mime_type}
            for item in row.attachments
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@dataclass
class GenerationJob:
    row_id: str
    prompt: str
    settings: GlobalSettings
    provider_id: str
    output_dir: Path
    row_index: int = 1
    prompt_id: str = ""
    category_id: str = ""
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    keep_existing: bool = False
    requested_at: float = 0.0
    attachments: List[Any] = field(default_factory=list)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fingerprint: str = ""
    requested_settings: Dict[str, Any] = field(default_factory=dict)
    run_index: int = 1
    reference_images: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    claimed: bool = False
    started: bool = False


class GenerationQueue:
    """One admitted job per row, ordered notifications and explicit stopping.

    State transitions append notifications under the state lock. The FIFO drain
    calls listeners outside the lock; listener calls to enqueue/cancel are safe.
    There is no automatic retry or replay of a possibly submitted paid request.
    """

    def __init__(
        self, providers: ProviderRegistry, output_root: Path, concurrency: int, rpm: int = 60
    ) -> None:
        self.providers = providers
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        self.output_root = root.resolve(strict=True)
        self.concurrency = max(1, int(concurrency))
        self.listeners: List[Callable[[str, str, Dict], None]] = []
        self.queue: Queue[GenerationJob | None] = Queue()
        self.running = True
        self.running_jobs: Dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.workers: List[threading.Thread] = []
        self.pending_order: List[str] = []
        self.rate_limiter = RateLimiter(rpm)
        self._jobs: Dict[str, GenerationJob] = {}
        self._notifications: deque[tuple[str, str, Dict]] = deque()
        self._dispatching = False
        self._log_lock = threading.Lock()
        self._start_workers(self.concurrency)

    def _start_workers(self, count: int) -> None:
        for index in range(count):
            worker = threading.Thread(
                target=self._worker_loop, daemon=True, name=f"imageai-worker-{index + 1}"
            )
            self.workers.append(worker)
            worker.start()

    def register_listener(self, callback: Callable[[str, str, Dict], None]) -> None:
        """Callbacks receive (event, row_id, payload) outside queue locks."""
        with self.lock:
            self.listeners.append(callback)

    def is_idle(self) -> bool:
        """True only after admitted work and terminal notifications are drained."""
        with self.lock:
            return not self._jobs and not self._notifications and not self._dispatching

    def _emit_locked(self, event: str, row_id: str, payload: Optional[Dict] = None) -> None:
        self._notifications.append((event, row_id, payload or {}))

    def _positions_locked(self) -> None:
        for position, row_id in enumerate(self.pending_order, start=1):
            self._emit_locked("queue_position", row_id, {"position": position})

    def _dispatch_events(self) -> None:
        with self.lock:
            if self._dispatching:
                return
            self._dispatching = True
        try:
            while True:
                with self.lock:
                    if not self._notifications:
                        self._dispatching = False
                        return
                    event, row_id, payload = self._notifications.popleft()
                    listeners = list(self.listeners)
                for callback in listeners:
                    try:
                        callback(event, row_id, copy.deepcopy(payload))
                    except Exception:
                        logger.exception("Generation queue listener failed for %s", event)
        except BaseException:
            with self.lock:
                self._dispatching = False
            raise

    def enqueue(
        self, row: RowData, global_settings: GlobalSettings, *,
        row_index: int = 1, missing_only: bool = False, regenerate: bool = False, sample: bool = False,
    ) -> bool:
        """Admit a snapshot once; False means duplicate or skipped.

        Invalid input raises before admission so it cannot change a running row.
        A queue that has been shut down rejects all new work.
        """
        with self.lock:
            if not self.running:
                raise RuntimeError("Generation queue has been shut down.")
            if row.id in self._jobs:
                return False
            interrupted = missing_only and row.status == RowStatus.INTERRUPTED
            if interrupted:
                self._emit_locked("skipped", row.id, {
                    "message": "This run was interrupted or its provider outcome is unknown. Review it and explicitly regenerate; generate-missing will not replay it.",
                    "preserve_status": True,
                })
        if interrupted:
            self._dispatch_events()
            return False
        if not row.id or row.id in {".", ".."} or any(c in row.id for c in ("/", "\\", "\x00")):
            raise ValueError("Row ID must be a nonempty filename-safe identifier.")
        snapshot = copy.deepcopy(row)
        if not isinstance(snapshot.prompt, str):
            raise ValueError("Prompt must be text.")
        settings = effective_settings(snapshot, global_settings)
        if sample:
            settings.num_images = 1
            settings.generate_behavior = "keep"
        validate_settings(settings)
        provider = self.providers.get(settings.provider_id)
        model = provider.validate_settings(settings, snapshot.attachments)
        if regenerate and not settings.regen_use_same_seed and model and model.capabilities.supports_seed:
            settings.seed = secrets.randbelow(2 ** 31)
            settings.random_seed = False
        provider.validate_prompt(generation_prompt(snapshot.prompt, settings))
        requested_settings = settings.to_dict()
        output_dir = self.output_root / snapshot.id
        if not output_dir.resolve().is_relative_to(self.output_root):
            raise ValueError("Row output directory escapes the session directory.")
        job = GenerationJob(
            row_id=snapshot.id, prompt=snapshot.prompt, settings=settings,
            provider_id=settings.provider_id, output_dir=output_dir,
            row_index=row_index, prompt_id=snapshot.prompt_id, category_id=snapshot.category_id,
            source_metadata=snapshot.source_metadata,
            keep_existing=settings.generate_behavior != "replace", requested_at=time.time(),
            attachments=snapshot.attachments,
            requested_settings=requested_settings, run_index=self._next_run_index(snapshot, output_dir),
        )
        self._freeze_references(job)
        fingerprint = generation_fingerprint(snapshot, settings)
        job.fingerprint = fingerprint
        skipped = ""
        if missing_only:
            existing = {
                str(Path(img.file_path).resolve()): img
                for img in snapshot.images if Path(img.file_path).is_file()
            }
            if any(not img.metadata.get("generation_fingerprint") for img in existing.values()):
                skipped = (
                    "Existing images have no generation fingerprint. Review them and explicitly "
                    "regenerate if needed; generate-missing does not replay unverified legacy results."
                )
            else:
                matching = sum(
                    img.metadata.get("generation_fingerprint") == fingerprint
                    and (not img.metadata.get("sha256") or _file_hash(Path(img.file_path)) == img.metadata["sha256"])
                    for img in existing.values()
                )
                remaining = settings.num_images - matching
                if remaining <= 0:
                    skipped = "The requested number of matching images already exists; skipped."
                else:
                    settings.num_images = remaining
                    job.keep_existing = True
        with self.lock:
            # Snapshotting/validation may race another admission or shutdown.
            if not self.running:
                raise RuntimeError("Generation queue has been shut down.")
            if row.id in self._jobs:
                return False
            if skipped:
                self._emit_locked("skipped", row.id, {"message": skipped, "generation_fingerprint": fingerprint})
            else:
                self._jobs[row.id] = job
                self.pending_order.append(row.id)
                self._emit_locked("queued", row.id, {"position": len(self.pending_order), **self._identity(job)})
                self._positions_locked()
                # Work publication follows admission and the queued event.
                self.queue.put(job)
        self._dispatch_events()
        return not skipped

    def _worker_loop(self) -> None:
        while True:
            job = self.queue.get()
            try:
                if job is None:
                    return
                with self.lock:
                    if self._jobs.get(job.row_id) is not job:
                        continue  # cancelled pending entry, possibly superseded
                    job.claimed = True
                    self.running_jobs[job.row_id] = job.cancel_event
                    self.pending_order.remove(job.row_id)
                    self._positions_locked()
                self._dispatch_events()
                self._run_job(job)
            finally:
                self.queue.task_done()
                self._dispatch_events()

    def _run_job(self, job: GenerationJob) -> None:
        start = time.monotonic()
        context = self._context_for_job(job)
        images: List[GeneratedImage] = []
        event = "completed"
        message = ""
        metadata: Dict[str, Any] = {}
        try:
            provider = self.providers.get(job.provider_id)
            cost = provider.quota_cost(job.settings)
            if cost < 0:
                raise ValueError("Provider quota cost cannot be negative.")
            if job.cancel_event.is_set() or (cost > 0 and not self.rate_limiter.acquire(job.provider_id, cost, job.cancel_event)):
                event = "cancelled"
            elif job.cancel_event.is_set():
                event = "cancelled"
            else:
                provider.setup()
                with self.lock:
                    if job.cancel_event.is_set():
                        event = "cancelled"
                    else:
                        job.started = True
                if event != "cancelled":
                    # Intent is durable before the paid call. Failure to record
                    # it prevents a submission that cannot be accounted for.
                    self._write_receipt(job, "started", {**self._identity(job), "images": [], "paths": []})
                    with self.lock:
                        self._emit_locked("started", job.row_id, self._identity(job))
                    self._dispatch_events()
                    if job.cancel_event.is_set():
                        event = "cancelled"
                    else:
                        images = list(provider.generate_images(
                            prompt=context.full_prompt, settings=job.settings, output_dir=job.output_dir,
                            cancel_event=job.cancel_event, attachments=job.attachments, context=context,
                        ) or [])
                        if job.cancel_event.is_set():
                            event = "cancelled"
                        elif len(images) != job.settings.num_images:
                            event = "error"
                            message = f"Provider returned {len(images)} of {job.settings.num_images} requested images. Saved results were retained."
        except ProviderGenerationError as exc:
            images = list(getattr(exc, "partial_images", []))
            event = "cancelled" if job.cancel_event.is_set() else ("filtered" if isinstance(exc, ProviderFilteredError) else "error")
            message = str(exc)
            metadata = {**exc.metadata, "status": exc.status}
        except Exception as exc:
            event = "cancelled" if job.cancel_event.is_set() else "error"
            message = str(exc)

        # A response racing cancellation still contains paid, useful artifacts.
        payload: Dict[str, Any] = {
            **self._identity(job), "duration": time.monotonic() - start,
            "completed_at": time.time(),
            "keep_existing": job.keep_existing if event == "completed" else True,
            "metadata": metadata,
        }
        image_payloads = []
        warnings = []
        for image_index, image in enumerate(images):
            try:
                path = Path(image.file_path).resolve()
                if not path.is_relative_to(job.output_dir.resolve()) or not path.is_file():
                    raise ValueError("Provider returned an image outside its output directory or a missing file.")
                digest = _file_hash(path)
                image_metadata = {
                    **image.metadata, **context.to_metadata(), **self._identity(job),
                    "run_index": job.run_index + image_index,
                    "source_metadata": job.source_metadata, "sha256": digest,
                    "generation_settings": job.settings.to_dict(),
                }
                image_payloads.append({"path": str(path), "metadata": image_metadata, "sha256": digest})
                warning = self._log_generation(
                    context=context, settings=job.settings, status="success", output_file=str(path), metadata=image_metadata,
                )
                if warning:
                    warnings.append(warning)
            except Exception as exc:
                event = "error"
                message = f"Could not register a provider result: {exc}"
        payload["images"] = image_payloads
        payload["paths"] = [item["path"] for item in image_payloads]
        if event == "cancelled":
            message = (
                "Cancellation requested; returned images were saved. An in-flight provider request may still be billed."
                if job.started else "Cancelled before provider submission."
            )
        payload["message"] = message
        if event != "completed":
            warning = self._log_generation(
                context=context, settings=job.settings, status=metadata.get("status", event),
                output_file="", metadata={**self._identity(job), **metadata}, error=message,
            )
            if warning:
                warnings.append(warning)
        if warnings:
            payload["warnings"] = list(dict.fromkeys(warnings))
        try:
            self._write_receipt(job, event, payload)
        except Exception as exc:
            payload.setdefault("warnings", []).append(f"Run receipt could not be saved: {exc}")
        with self.lock:
            if self._jobs.get(job.row_id) is job:
                self._jobs.pop(job.row_id)
                self.running_jobs.pop(job.row_id, None)
                self._emit_locked(event, job.row_id, payload)
                self._positions_locked()

    def cancel(self, row_id: str) -> None:
        with self.lock:
            job = self._jobs.get(row_id)
            if job is not None:
                self._cancel_locked(job, "Cancelled before provider submission.")
                self._positions_locked()
        self._dispatch_events()

    def _cancel_locked(self, job: GenerationJob, message: str) -> None:
        job.cancel_event.set()
        if not job.claimed:
            self._jobs.pop(job.row_id, None)
            self.pending_order.remove(job.row_id)
            self._emit_locked("cancelled", job.row_id, {
                **self._identity(job), "message": message, "paths": [], "images": [], "keep_existing": True,
            })

    def cancel_all(self) -> None:
        """Cancel queued work and request cancellation of in-flight work."""
        with self.lock:
            for job in list(self._jobs.values()):
                self._cancel_locked(job, "Cancelled before provider submission.")
            self._positions_locked()
        self._dispatch_events()

    def stop_after_current(self) -> None:
        """Keep submitted calls; cancel queued jobs and jobs waiting for quota."""
        with self.lock:
            for job in list(self._jobs.values()):
                if not job.started:
                    self._cancel_locked(job, "Stopped before provider submission.")
            self._positions_locked()
        self._dispatch_events()

    def shutdown(self, timeout: float = 2.0) -> bool:
        """Stop admission; return whether every worker has exited.

        HTTP requests cannot be forcibly recalled. False means callers must keep
        the old session alive until its workers deliver their result. Repeated
        calls may wait again without adding duplicate termination markers.
        """
        with self.lock:
            if self.running:
                self.running = False
                for job in list(self._jobs.values()):
                    self._cancel_locked(job, "Queue stopped before provider submission.")
                self._positions_locked()
                for _ in self.workers:
                    self.queue.put(None)
        self._dispatch_events()
        deadline = time.monotonic() + max(0.0, timeout)
        current = threading.current_thread()
        for worker in self.workers:
            if worker is not current:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
        return all(not worker.is_alive() for worker in self.workers)

    @staticmethod
    def _identity(job: GenerationJob) -> Dict[str, Any]:
        return {
            "job_id": job.job_id, "generation_fingerprint": job.fingerprint,
            "requested_at": job.requested_at, "requested_settings": job.requested_settings,
            "reference_images": job.reference_images,
        }

    @staticmethod
    def _freeze_references(job: GenerationJob) -> None:
        """Preserve the bytes actually queued, including while waiting for quota."""
        remaining = MAX_INLINE_BYTES
        for index, attachment in enumerate(job.attachments):
            original = Path(attachment.file_path).resolve()
            # Earlier validation is not a byte snapshot: a file can grow or be
            # replaced before admission. Bound and revalidate the actual copy.
            with original.open("rb") as source:
                data = source.read(remaining + 1)
            if len(data) > remaining:
                raise ProviderGenerationError(
                    "Reference images exceed the app's 12 MiB total inline-upload limit.",
                    status="invalid_settings",
                )
            remaining -= len(data)
            fmt, _, _ = inspect_image(data)
            digest = hashlib.sha256(data).hexdigest()
            target = job.output_dir / "inputs" / job.job_id / f"{index:02d}-{digest[:16]}{original.suffix.lower()}"
            if not target.resolve().is_relative_to(job.output_dir.resolve()):
                raise ValueError("Reference snapshot directory escapes the row output directory.")
            atomic_write_bytes(target, data)
            attachment.file_path = str(target)
            attachment.mime_type = MIME_TYPES[fmt]
            job.reference_images.append({
                "source_path": str(original), "path": str(target),
                "sha256": digest, "mime_type": attachment.mime_type,
            })

    @staticmethod
    def _next_run_index(row: RowData, output_dir: Path) -> int:
        indices = [0]
        for image in row.images:
            try:
                indices.append(int(image.metadata.get("run_index", 0)))
            except (TypeError, ValueError):
                pass
        if output_dir.exists():
            for path in output_dir.iterdir():
                match = re.search(r"_run(\d+)(?:\D|$)", path.name)
                if match:
                    indices.append(int(match.group(1)))
        return max(indices) + 1

    def _context_for_job(self, job: GenerationJob) -> GenerationContext:
        full_prompt = generation_prompt(job.prompt, job.settings)
        return GenerationContext(
            row_id=job.row_id, row_index=job.row_index,
            prompt_id=job.prompt_id or f"prompt{job.row_index:04d}", category_id=job.category_id or "cat00",
            original_prompt=job.prompt, full_prompt=full_prompt, run_index=job.run_index, log_path=str(self.log_path),
        )

    def _write_receipt(self, job: GenerationJob, event: str, payload: Dict[str, Any]) -> None:
        receipt = {
            "schema_version": 1, "session_id": self.output_root.parent.name,
            "written_at": time.time(),
            "row_id": job.row_id, "job_id": job.job_id, "event": event,
            "generation_fingerprint": job.fingerprint,
            "requested_settings": job.requested_settings, "generation_settings": job.settings.to_dict(),
            "context": self._context_for_job(job).to_metadata(), "payload": payload,
            "files": [{"path": image["path"], "sha256": image["sha256"]} for image in payload.get("images", [])],
        }
        atomic_write_bytes(
            job.output_dir / f"imageai-run-{job.job_id}.json",
            json.dumps(receipt, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    @property
    def log_path(self) -> Path:
        return self.output_root.parent / LOG_FILENAME

    def _log_generation(
        self, *, context: GenerationContext, settings: GlobalSettings, status: str,
        output_file: str, metadata: Dict[str, Any], error: str = "",
    ) -> str | None:
        try:
            with self._log_lock:
                append_generation_log(self.log_path, {
                    "prompt_id": context.prompt_id, "category_id": context.category_id,
                    "row_id": context.row_id, "original_prompt": context.original_prompt,
                    "full_prompt": context.full_prompt, "provider": settings.provider_id,
                    "model": settings.model_id, "size": metadata.get("size") or _size_string(settings),
                    "quality": metadata.get("quality") or settings.quality,
                    "n": metadata.get("n") or settings.num_images,
                    "output_format": metadata.get("output_format") or settings.output_format,
                    "output_file": output_file, "status": status, "attempt": metadata.get("attempt"),
                    "azure_request_id": metadata.get("azure_request_id", ""),
                    "job_id": metadata.get("job_id", ""),
                    "generation_fingerprint": metadata.get("generation_fingerprint", ""), "error": error,
                })
        except Exception as exc:
            return f"Generation log could not be written: {exc}"
        return None


def _size_string(settings: GlobalSettings) -> str:
    if settings.custom_size:
        return f"{settings.custom_size.get('width', 1024)}x{settings.custom_size.get('height', 1024)}"
    from app.models import DEFAULT_SIZE_PRESETS
    preset = DEFAULT_SIZE_PRESETS.get(settings.size_preset or "", DEFAULT_SIZE_PRESETS["Square 1024"])
    return f"{preset.get('width', 1024)}x{preset.get('height', 1024)}"


def queue_row_status_update(row: RowData, status: RowStatus, message: str = "") -> None:
    row.status = status
    row.error_message = message
