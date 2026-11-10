"""Event-controlled regression tests for admission, stopping and recovery evidence."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from app.models import GeneratedImage, GlobalSettings, ImageResult, InputAttachment, RowData
from app.providers.base import BaseProvider, ProviderGenerationError
from app.providers.registry import ProviderRegistry
from app.queue_manager import GenerationQueue, generation_fingerprint
from app.scheduler import RateLimiter


class ProbeProvider(BaseProvider):
    id = "probe"
    name = "Probe"

    def __init__(self, *, blocked=False, behavior=None, cost=1):
        super().__init__([])
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.behavior = behavior
        self.cost = cost
        if not blocked:
            self.release.set()

    def quota_cost(self, settings):
        return self.cost

    def generate_images(self, prompt, settings, output_dir, cancel_event, attachments=None, context=None):
        call = {
            "prompt": prompt, "settings": settings, "attachments": attachments or [],
            "context": context, "cancel_event": cancel_event, "output_dir": output_dir,
        }
        self.calls.append(call)
        self.entered.set()
        assert self.release.wait(3), "test provider was not released"
        if self.behavior:
            return self.behavior(call)
        return self.write_images(call, settings.num_images)

    @staticmethod
    def write_images(call, count):
        call["output_dir"].mkdir(parents=True, exist_ok=True)
        images = []
        for index in range(count):
            path = call["output_dir"] / f"test_run{call['context'].run_index + index:02d}.png"
            Image.new("RGB", (8, 8), (20, 40, 60)).save(path)
            images.append(GeneratedImage(path, {"provider": "probe"}))
        return images


class Events:
    def __init__(self):
        self.items = []
        self.condition = threading.Condition()

    def __call__(self, event, row_id, payload):
        with self.condition:
            self.items.append((event, row_id, payload))
            self.condition.notify_all()

    def wait(self, event, row_id, timeout=3):
        with self.condition:
            assert self.condition.wait_for(
                lambda: any(e == event and r == row_id for e, r, _ in self.items), timeout
            ), f"missing {event} for {row_id}: {self.items}"
            return next(p for e, r, p in self.items if e == event and r == row_id)


@contextmanager
def managed_queue(tmp_path, provider, *, concurrency=1, rpm=60):
    registry = ProviderRegistry()
    registry.register(provider)
    queue = GenerationQueue(registry, tmp_path / "session" / "images", concurrency, rpm)
    events = Events()
    queue.register_listener(events)
    try:
        yield queue, events
    finally:
        provider.release.set()
        assert queue.shutdown(timeout=3)
        assert queue.queue.unfinished_tasks == 0


def settings(**kwargs):
    return GlobalSettings(provider_id="probe", model_id="probe-model", **kwargs)


def test_simultaneous_duplicate_admission_submits_once_and_orders_events(tmp_path):
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider, concurrency=4) as (queue, events):
        barrier = threading.Barrier(12)
        accepted = []
        lock = threading.Lock()

        def enqueue():
            barrier.wait()
            result = queue.enqueue(RowData(id="row", prompt="one request"), settings())
            with lock:
                accepted.append(result)

        threads = [threading.Thread(target=enqueue) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()
        assert sum(accepted) == 1
        assert provider.entered.wait(3)
        assert len(provider.calls) == 1
        provider.release.set()
        events.wait("completed", "row")
        sequence = [event for event, row_id, _ in events.items if row_id == "row"]
        assert sequence.index("queued") < sequence.index("started") < sequence.index("completed")
        assert sequence.count("completed") == 1


def test_pending_cancel_is_immediate_and_same_row_can_be_reenqueued(tmp_path):
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(RowData(id="first", prompt="block"), settings())
        assert provider.entered.wait(3)
        row = RowData(id="next", prompt="old")
        queue.enqueue(row, settings())
        queue.cancel("next")
        cancelled = events.wait("cancelled", "next")
        row.prompt = "new"
        assert queue.enqueue(row, settings())
        provider.release.set()
        completed = events.wait("completed", "next")
        assert [call["prompt"] for call in provider.calls] == ["block", "new"]
        assert completed["job_id"] != cancelled["job_id"]
        assert "next" not in queue.pending_order


def test_cancel_all_stops_pending_and_keeps_images_returned_by_inflight_request(tmp_path):
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(RowData(id="active", prompt="active"), settings(generate_behavior="replace"))
        assert provider.entered.wait(3)
        for row_id in ("next1", "next2"):
            queue.enqueue(RowData(id=row_id, prompt=row_id), settings())
        queue.cancel_all()
        for row_id in ("next1", "next2"):
            assert not events.wait("cancelled", row_id)["images"]
        provider.release.set()
        payload = events.wait("cancelled", "active")
        assert len(provider.calls) == 1
        assert len(payload["images"]) == 1
        assert Path(payload["paths"][0]).is_file()
        assert payload["keep_existing"] is True
        assert "may still be billed" in payload["message"]


def test_stop_after_current_keeps_started_job_but_cancels_pending(tmp_path):
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(RowData(id="active", prompt="active"), settings())
        assert provider.entered.wait(3)
        queue.enqueue(RowData(id="next", prompt="next"), settings())
        queue.stop_after_current()
        events.wait("cancelled", "next")
        assert not provider.calls[0]["cancel_event"].is_set()
        provider.release.set()
        events.wait("completed", "active")
        assert len(provider.calls) == 1


def test_cancel_interrupts_quota_wait_before_any_provider_call(tmp_path):
    provider = ProbeProvider()
    with managed_queue(tmp_path, provider, rpm=1) as (queue, events):
        queue.rate_limiter.acquire("probe")
        waiting = threading.Event()
        acquire = queue.rate_limiter.acquire

        def observing_acquire(*args, **kwargs):
            waiting.set()
            return acquire(*args, **kwargs)

        queue.rate_limiter.acquire = observing_acquire
        queue.enqueue(RowData(id="row", prompt="waiting"), settings())
        assert waiting.wait(3)
        start = time.monotonic()
        queue.cancel("row")
        events.wait("cancelled", "row", timeout=1)
        assert time.monotonic() - start < 1
        assert not provider.calls
        assert not any(e == "started" for e, _, _ in events.items)


def test_queue_listener_can_cancel_reentrantly_without_deadlocking(tmp_path):
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider) as (queue, events):
        callback_entered = threading.Event()

        def cancel_on_queued(event, row_id, payload):
            if event == "queued":
                queue.cancel(row_id)
                callback_entered.set()

        queue.register_listener(cancel_on_queued)
        thread = threading.Thread(target=lambda: queue.enqueue(RowData(id="row", prompt="cancel"), settings()), daemon=True)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert callback_entered.wait(1)
        provider.release.set()
        events.wait("cancelled", "row")


def test_jobs_snapshot_mutable_settings_metadata_and_reference_file_bytes(tmp_path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "red").save(reference)
    original_bytes = reference.read_bytes()
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(RowData(id="first", prompt="block"), settings())
        assert provider.entered.wait(3)
        row = RowData(
            id="second", prompt="original", source_metadata={"nested": {"name": "original"}},
            attachments=[InputAttachment("ref", str(reference))],
        )
        global_settings = settings(custom_size={"width": 64, "height": 64})
        queue.enqueue(row, global_settings)
        row.prompt = "edited"
        row.source_metadata["nested"]["name"] = "edited"
        global_settings.custom_size["width"] = 128
        row.attachments[0].file_path = "does-not-exist"
        Image.new("RGB", (8, 8), "blue").save(reference)
        provider.release.set()
        result = events.wait("completed", "second")
        call = provider.calls[-1]
        assert call["prompt"] == "original"
        assert call["settings"].custom_size["width"] == 64
        assert Path(call["attachments"][0].file_path).read_bytes() == original_bytes
        metadata = result["images"][0]["metadata"]
        assert metadata["source_metadata"]["nested"]["name"] == "original"
        assert metadata["reference_images"][0]["sha256"] == hashlib.sha256(original_bytes).hexdigest()


def test_missing_only_fills_exact_remaining_count_for_matching_settings(tmp_path):
    provider = ProbeProvider()
    row = RowData(id="row", prompt="same")
    global_settings = settings(num_images=3, generate_behavior="replace")
    existing = tmp_path / "existing.png"
    Image.new("RGB", (8, 8)).save(existing)
    row.images = [ImageResult("old", row.id, str(existing), {"generation_fingerprint": generation_fingerprint(row, global_settings)})]
    with managed_queue(tmp_path, provider) as (queue, events):
        assert queue.enqueue(row, global_settings, missing_only=True)
        payload = events.wait("completed", row.id)
        assert provider.calls[0]["settings"].num_images == 2
        assert payload["requested_settings"]["num_images"] == 3
        assert payload["keep_existing"] is True
        assert len(payload["images"]) == 2


def test_missing_only_does_not_count_outputs_from_an_edited_prompt(tmp_path):
    provider = ProbeProvider()
    row = RowData(id="row", prompt="original")
    global_settings = settings(num_images=2)
    existing = tmp_path / "existing.png"
    Image.new("RGB", (8, 8)).save(existing)
    fingerprint = generation_fingerprint(row, global_settings)
    row.images = [ImageResult("old", row.id, str(existing), {"generation_fingerprint": fingerprint})]
    row.prompt = "changed"
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(row, global_settings, missing_only=True)
        result = events.wait("completed", row.id)
        assert provider.calls[0]["settings"].num_images == 2
        assert result["generation_fingerprint"] != fingerprint


def test_empty_success_response_is_error_not_completed(tmp_path):
    provider = ProbeProvider(behavior=lambda call: [])
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(RowData(id="row", prompt="empty response"), settings())
        result = events.wait("error", "row")
        assert "0 of 1" in result["message"]
        assert not any(e == "completed" for e, _, _ in events.items)


def test_partial_outputs_survive_later_provider_failure_and_receipt_precedes_event(tmp_path):
    def partial_failure(call):
        partial = ProbeProvider.write_images(call, 1)
        raise ProviderGenerationError("Second request outcome unknown", status="outcome_unknown", partial_images=partial)

    provider = ProbeProvider(behavior=partial_failure)
    with managed_queue(tmp_path, provider) as (queue, events):
        verified = threading.Event()

        def verify_receipt(event, row_id, payload):
            if event != "error":
                return
            path = queue.output_root / row_id / f"imageai-run-{payload['job_id']}.json"
            receipt = json.loads(path.read_text())
            assert receipt["event"] == "error"
            assert receipt["session_id"] == "session"
            assert receipt["files"][0]["sha256"] == hashlib.sha256(Path(receipt["files"][0]["path"]).read_bytes()).hexdigest()
            verified.set()

        queue.register_listener(verify_receipt)
        queue.enqueue(RowData(id="row", prompt="partial"), settings(num_images=2, generate_behavior="replace"))
        result = events.wait("error", "row")
        assert verified.wait(3)
        assert len(result["images"]) == 1
        assert result["keep_existing"] is True
        assert result["metadata"]["status"] == "outcome_unknown"
        assert len(provider.calls) == 1


def test_logging_failure_does_not_drop_outputs_or_kill_worker(tmp_path, monkeypatch):
    def fail_log(*args, **kwargs):
        raise OSError("disk full for log")

    monkeypatch.setattr("app.queue_manager.append_generation_log", fail_log)
    provider = ProbeProvider()
    with managed_queue(tmp_path, provider) as (queue, events):
        for row_id in ("one", "two"):
            queue.enqueue(RowData(id=row_id, prompt=row_id), settings())
            result = events.wait("completed", row_id)
            assert len(result["images"]) == 1
            assert "disk full" in result["warnings"][0]
        assert len(provider.calls) == 2


def test_unrecordable_intent_prevents_provider_submission(tmp_path, monkeypatch):
    provider = ProbeProvider()
    with managed_queue(tmp_path, provider) as (queue, events):
        monkeypatch.setattr(queue, "_write_receipt", lambda *args: (_ for _ in ()).throw(OSError("no receipt space")))
        queue.enqueue(RowData(id="row", prompt="do not submit"), settings())
        result = events.wait("error", "row")
        assert "no receipt space" in result["message"]
        assert not provider.calls


def test_oversized_quota_cost_fails_promptly_and_worker_accepts_next_job(tmp_path):
    provider = ProbeProvider(cost=2)
    with managed_queue(tmp_path, provider, rpm=1) as (queue, events):
        queue.enqueue(RowData(id="too-large", prompt="too large"), settings())
        result = events.wait("error", "too-large", timeout=1)
        assert "above the configured limit" in result["message"]
        assert not provider.calls
        provider.cost = 1
        queue.enqueue(RowData(id="fits", prompt="fits"), settings())
        events.wait("completed", "fits")


def test_shutdown_reports_inflight_work_and_drains_task_accounting(tmp_path):
    provider = ProbeProvider(blocked=True)
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(RowData(id="active", prompt="active"), settings())
        assert provider.entered.wait(3)
        queue.enqueue(RowData(id="pending", prompt="pending"), settings())
        assert queue.shutdown(timeout=0.01) is False
        events.wait("cancelled", "pending")
        with pytest.raises(RuntimeError, match="shut down"):
            queue.enqueue(RowData(id="new", prompt="new"), settings())
        provider.release.set()
        assert queue.shutdown(timeout=3) is True
        assert queue.queue.unfinished_tasks == 0
        assert not queue.running_jobs
        assert not queue.pending_order
        assert len(provider.calls) == 1
        events.wait("cancelled", "active")


def test_is_idle_waits_for_terminal_listener_to_finish(tmp_path):
    provider = ProbeProvider()
    with managed_queue(tmp_path, provider) as (queue, events):
        entered = threading.Event()
        released = threading.Event()

        def slow_terminal(event, row_id, payload):
            if event == "completed":
                entered.set()
                assert released.wait(3)

        queue.register_listener(slow_terminal)
        try:
            queue.enqueue(RowData(id="row", prompt="row"), settings())
            assert entered.wait(3)
            assert not queue.is_idle()
        finally:
            released.set()


def test_rerun_advances_run_index_past_existing_files(tmp_path):
    provider = ProbeProvider()
    with managed_queue(tmp_path, provider) as (queue, events):
        output = queue.output_root / "row"
        output.mkdir(parents=True)
        old = output / "cat_prompt_q-medium_run07.png"
        old.write_bytes(b"old image bytes")
        queue.enqueue(RowData(id="row", prompt="again"), settings())
        result = events.wait("completed", "row")
        assert provider.calls[0]["context"].run_index == 8
        assert old.read_bytes() == b"old image bytes"
        assert result["images"][0]["metadata"]["run_index"] == 8


def test_reference_content_and_prompt_affect_fingerprint_but_count_does_not(tmp_path):
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"first")
    row = RowData(id="row", prompt="a", attachments=[InputAttachment("ref", str(reference))])
    first = generation_fingerprint(row, settings(num_images=1))
    assert generation_fingerprint(row, settings(num_images=4)) == first
    reference.write_bytes(b"second")
    assert generation_fingerprint(row, settings()) != first
    row.prompt = "b"
    assert generation_fingerprint(row, settings()) != first


def test_rate_limiter_rejects_impossible_cost_and_does_not_spend_cancelled_quota():
    limiter = RateLimiter(rpm=1)
    with pytest.raises(ValueError, match="quota units"):
        limiter.acquire("probe", 2)
    cancelled = threading.Event()
    cancelled.set()
    assert limiter.acquire("probe", cancel_event=cancelled) is False
    assert not limiter.events["probe"]


def test_rate_limiter_uses_monotonic_clock_and_wakes_after_live_limit_increase():
    limiter = RateLimiter(rpm=1, clock=lambda: 123.0)
    assert limiter.acquire("probe")
    started = threading.Event()
    done = threading.Event()

    def acquire():
        started.set()
        assert limiter.acquire("probe")
        done.set()

    thread = threading.Thread(target=acquire, daemon=True)
    thread.start()
    assert started.wait(1)
    limiter.set_rpm(2)
    assert done.wait(1)
    thread.join(timeout=1)
    assert list(limiter.events["probe"]) == [123.0, 123.0]


def test_missing_only_does_not_replay_interrupted_or_unknown_provider_outcome(tmp_path):
    from app.models import RowStatus

    provider = ProbeProvider()
    with managed_queue(tmp_path, provider) as (queue, events):
        row = RowData(id="uncertain", prompt="may have been billed", status=RowStatus.INTERRUPTED)
        assert queue.enqueue(row, settings(), missing_only=True) is False
        payload = events.wait("skipped", row.id)
        assert payload["preserve_status"] is True
        assert "explicitly regenerate" in payload["message"]
        assert not provider.calls
        # A separate deliberate generation remains possible after review.
        assert queue.enqueue(row, settings(), missing_only=False) is True
        events.wait("completed", row.id)
        assert len(provider.calls) == 1


def test_missing_only_detects_changed_output_bytes(tmp_path):
    provider = ProbeProvider()
    row = RowData(id="row", prompt="same")
    global_settings = settings(num_images=2)
    existing = tmp_path / "existing.png"
    Image.new("RGB", (8, 8)).save(existing)
    row.images = [ImageResult("old", row.id, str(existing), {
        "generation_fingerprint": generation_fingerprint(row, global_settings),
        "sha256": hashlib.sha256(existing.read_bytes()).hexdigest(),
    })]
    existing.write_bytes(b"damaged after generation")
    with managed_queue(tmp_path, provider) as (queue, events):
        queue.enqueue(row, global_settings, missing_only=True)
        events.wait("completed", row.id)
        assert provider.calls[0]["settings"].num_images == 2


def test_offline_mock_jobs_do_not_acquire_paid_quota_even_at_one_rpm(tmp_path, monkeypatch):
    registry = ProviderRegistry()
    queue = GenerationQueue(registry, tmp_path / "images", concurrency=1, rpm=1)
    events = Events()
    queue.register_listener(events)

    def unexpected_quota(*args, **kwargs):
        pytest.fail("Offline mock work attempted to acquire provider quota")

    monkeypatch.setattr(queue.rate_limiter, "acquire", unexpected_quota)
    try:
        for row_id in ("first", "second"):
            assert queue.enqueue(RowData(id=row_id, prompt=row_id), GlobalSettings())
        events.wait("completed", "first")
        events.wait("completed", "second")
        assert registry.get("mock").quota_cost(GlobalSettings()) == 0
    finally:
        assert queue.shutdown(timeout=3)
        assert queue.queue.unfinished_tasks == 0


def test_reference_snapshot_rejects_growth_after_provider_validation(tmp_path, monkeypatch):
    from app.queue_manager import MAX_INLINE_BYTES

    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8)).save(reference)
    provider = ProbeProvider()
    row = RowData(id="row", prompt="reference", attachments=[InputAttachment("ref", str(reference))])

    def grow_after_validation(settings, attachments):
        with reference.open("ab") as stream:
            stream.truncate(MAX_INLINE_BYTES + 4096)

    monkeypatch.setattr(provider, "validate_settings", grow_after_validation)
    with managed_queue(tmp_path, provider) as (queue, events):
        with pytest.raises(ProviderGenerationError, match="12 MiB"):
            queue.enqueue(row, settings())
        assert queue.is_idle()
        assert not provider.calls


def test_reference_snapshot_revalidates_replaced_bytes_before_admission(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8)).save(reference)
    provider = ProbeProvider()
    row = RowData(id="row", prompt="reference", attachments=[InputAttachment("ref", str(reference))])

    def replace_after_validation(settings, attachments):
        reference.write_bytes(b"no longer an image")

    monkeypatch.setattr(provider, "validate_settings", replace_after_validation)
    with managed_queue(tmp_path, provider) as (queue, events):
        with pytest.raises(ProviderGenerationError, match="valid PNG"):
            queue.enqueue(row, settings())
        assert queue.is_idle()
        assert not provider.calls
