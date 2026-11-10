"""Independent release-path invariants; original failures documented in review."""
import json
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import pytest
from PIL import Image

from app.generation_state import apply_generation_event
from app.models import GlobalSettings, ImageResult, RowData, RowStatus, SessionData


def test_skipped_interrupted_run_preserves_uncertainty_when_partial_image_exists(tmp_path):
    path = tmp_path / "partial.png"
    Image.new("RGB", (8, 8), "navy").save(path)
    row = RowData("row", prompt="logo", status=RowStatus.INTERRUPTED,
                  images=[ImageResult("image", "row", str(path))])
    apply_generation_event(row, "skipped", {"preserve_status": True, "message": "Review the unknown outcome before replay."})
    assert row.status == RowStatus.INTERRUPTED


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEAI_DATA_DIR", str(tmp_path / "app-data"))
    import app.backend_server as module
    state = module.BackendState(restore=False)
    yield state, module
    state.queue.shutdown()


def test_invalid_row_prompt_does_not_poison_active_state(backend):
    state, _ = backend
    state.dispatch("add_rows", {"prompts": ["valid prompt"]})
    row_id = state.session.rows[0].id
    before = state.session.to_dict()
    with pytest.raises((ValueError, TypeError)):
        state.dispatch("update_row", {"row_id": row_id, "prompt": ["invalid", "type"]})
    assert state.session.to_dict() == before
    assert state.dispatch("preflight", {})["valid"]


def test_invalid_add_rows_does_not_partially_publish_mutation(backend):
    state, _ = backend
    state.dispatch("add_rows", {"prompts": ["prior row"]})
    before_rows = [row.to_dict() for row in state.session.rows]
    with pytest.raises((ValueError, TypeError)):
        state.dispatch("add_rows", {"prompts": ["new valid row", ["invalid type"]]})
    assert [row.to_dict() for row in state.session.rows] == before_rows


def test_http_image_viewer_serves_loaded_portable_bundle_images(backend, tmp_path, monkeypatch):
    state, module = backend
    bundle = tmp_path / "portable-batch"
    bundle.mkdir()
    image_path = bundle / "image.png"
    Image.new("RGB", (8, 8), "navy").save(image_path)
    session = SessionData("portable-session", GlobalSettings(), [
        RowData("row", prompt="logo", images=[ImageResult("image", "row", "image.png")]),
    ])
    session_path = bundle / "session.json"
    session_path.write_text(json.dumps(session.to_dict()))
    state.dispatch("load_session", {"path": str(session_path)})
    monkeypatch.setattr(module, "STATE", state)
    server = HTTPServer(("127.0.0.1", 0), module.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        address = f"http://127.0.0.1:{server.server_address[1]}/images?path={quote(str(image_path), safe='')}"
        try:
            with urlopen(address, timeout=2) as response:
                status, data = response.status, response.read()
        except HTTPError as exc:
            status, data = exc.code, exc.read()
        assert status == 200, f"Portable bundle image rejected with HTTP {status}"
        assert data == image_path.read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_portable_bundle_preserves_frozen_reference_for_historical_result(tmp_path):
    from app.exporter import ImageExporter
    from app.models import InputAttachment
    import hashlib

    historic = tmp_path / "frozen-reference.png"
    current = tmp_path / "current-reference.png"
    generated = tmp_path / "generated.png"
    Image.new("RGB", (8, 8), "navy").save(historic)
    Image.new("RGB", (8, 8), "red").save(current)
    Image.new("RGB", (8, 8), "white").save(generated)
    row = RowData("row", prompt="new prompt", attachments=[InputAttachment("input", str(current))], images=[
        ImageResult("image", "row", str(generated), metadata={
            "original_prompt": "old prompt", "reference_images": [{
                "path": str(historic), "source_path": str(tmp_path / "old-original.png"),
                "sha256": hashlib.sha256(historic.read_bytes()).hexdigest(), "mime_type": "image/png",
            }],
        }),
    ])
    session = SessionData("portable-history", GlobalSettings(), [row])
    result = ImageExporter("{index}_{slug}.png").export_bundle(session, tmp_path / "exports")
    exported = Path(result["path"])
    snapshot = json.loads((exported / "session.json").read_text())
    reference = snapshot["rows"][0]["images"][0]["metadata"]["reference_images"][0]
    assert not Path(reference["path"]).is_absolute()
    stored_reference = exported / reference["path"]
    assert stored_reference.is_file(), "The original generation reference was dropped from the portable batch"
    assert stored_reference.read_bytes() == historic.read_bytes()


@pytest.mark.parametrize("bad_receipt", [[], {"schema_version": 1, "session_id": "receipt-session", "row_id": "row", "payload": []}])
def test_malformed_receipt_cannot_prevent_valid_session_restore(tmp_path, monkeypatch, bad_receipt):
    from app.session_manager import SessionManager
    monkeypatch.setenv("IMAGEAI_DATA_DIR", str(tmp_path / "store"))
    manager = SessionManager()
    manager.session = SessionData("receipt-session", GlobalSettings(), [RowData("row", prompt="valid prompt")])
    manager.autosave()
    output = manager.session_output_dir() / "row"
    output.mkdir()
    (output / "imageai-run-bad.json").write_text(json.dumps(bad_receipt))
    restored = SessionManager().restore_last()
    assert restored is not None
    assert restored.id == "receipt-session"
