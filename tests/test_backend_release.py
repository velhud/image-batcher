import json
import socket
import threading
import time
from http.server import HTTPServer

import pytest
import requests

from app import backend_server
from app.models import GeneratedImage, RowStatus
from app.providers.base import BaseProvider


@pytest.fixture
def backend(monkeypatch):
    state = backend_server.BackendState(restore=False)
    monkeypatch.setattr(backend_server, 'STATE', state)
    yield state
    state.queue.shutdown()


def test_active_session_changes_are_rejected_without_losing_outputs(backend, tmp_path):
    entered, release = threading.Event(), threading.Event()
    class BlockedProvider(BaseProvider):
        id = 'blocked'
        name = 'Blocked test provider'
        def __init__(self):
            super().__init__([])
        def generate_images(self, prompt, settings, output_dir, cancel_event, attachments=None, context=None):
            entered.set()
            assert release.wait(3)
            from PIL import Image
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / 'done.png'
            Image.new('RGB', (4,4)).save(target)
            return [GeneratedImage(target)]
    backend.registry.register(BlockedProvider())
    backend.dispatch('global_settings', {'provider_id': 'blocked', 'model_id': 'blocked-v1'})
    row_id = backend.dispatch('add_rows', {'prompts': ['test']})['rows'][0]['id']
    original_id = backend.session.id
    backend.dispatch('generate_rows', {'row_ids': [row_id]})
    assert entered.wait(2)
    try:
        with pytest.raises(ValueError, match='active work'):
            backend.dispatch('new_session', {})
        with pytest.raises(ValueError, match='active work'):
            backend.dispatch('global_settings', {'concurrency_limit': 3})
        assert backend.session.id == original_id
        assert backend.session.global_settings.concurrency_limit == 2
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while not backend.queue.is_idle() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert backend.session.rows[0].images
    assert backend.session.rows[0].status.value == 'Completed'


def test_bad_global_patch_is_atomic(backend):
    before = backend.session.global_settings.to_dict()
    with pytest.raises(ValueError):
        backend.dispatch('global_settings', {'num_images': 0, 'provider_id': 'openai'})
    assert backend.session.global_settings.to_dict() == before


def test_preflight_rejects_all_before_any_submission(backend):
    rows = backend.dispatch('add_rows', {'prompts': ['valid', '']})['rows']
    with pytest.raises(ValueError, match='validation'):
        backend.dispatch('generate_rows', {'row_ids': [row['id'] for row in rows]})
    assert backend.queue.is_idle()
    assert all(row.status.value == 'Idle' for row in backend.session.rows)


@pytest.mark.parametrize('provider', ['openai', 'azure-openai'])
def test_preflight_checks_wrapped_prompt_length_before_any_admission(backend, monkeypatch, provider):
    backend.dispatch('global_settings', {'provider_id': provider, 'model_id': 'gpt-image-2', 'prompt_wrapper': 'Shared instructions'})
    backend.dispatch('add_rows', {'prompts': ['valid', 'a' * 31999]})
    admitted = []
    monkeypatch.setattr(backend.queue, 'enqueue', lambda *args, **kwargs: admitted.append(args))
    report = backend.dispatch('preflight', {})
    assert not report['valid']
    assert not report['rows'][0]['errors']
    assert '32,000' in report['rows'][1]['errors'][0]
    with pytest.raises(ValueError, match='32,000'):
        backend.dispatch('generate_rows', {})
    assert admitted == []


def test_invalid_import_does_not_partially_replace_rows(backend):
    backend.dispatch('add_rows', {'prompts': ['keep']})
    result = backend.dispatch('import_batch', {'raw': '["valid", {"prompt":null}]', 'mode': 'json_array', 'apply_mode': 'replace'})
    assert result['errors']
    assert [row.prompt for row in backend.session.rows] == ['keep']


def test_rpc_rejects_untrusted_origin_and_bad_envelope(backend):
    server = HTTPServer(('127.0.0.1', 0), backend_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}/rpc'
    try:
        denied = requests.post(url, json={'action':'new_session'}, headers={'Origin':'https://unrelated.example'}, timeout=3)
        assert denied.status_code == 403
        assert 'Access-Control-Allow-Origin' not in denied.headers
        opaque = requests.post(url, json={'action': 'state'}, headers={'Origin': 'null'}, timeout=3)
        assert opaque.status_code == 403
        malformed = requests.post(url, json=[], timeout=3)
        assert malformed.status_code == 400
        wrong_path = requests.post(url.replace('/rpc','/other'), json={'action':'state'}, timeout=3)
        assert wrong_path.status_code == 404
        valid = requests.post(url, json={'action':'state'}, headers={'Origin':'http://localhost:5173'}, timeout=3)
        assert valid.status_code == 200
        assert valid.headers['Access-Control-Allow-Origin'] == 'http://localhost:5173'
    finally:
        server.shutdown(); server.server_close(); thread.join(2)



def test_generate_missing_excludes_interrupted_rows_before_validation(backend):
    rows = backend.dispatch("add_rows", {"prompts": ["ready", ""]})["rows"]
    backend.session.rows[1].status = RowStatus.INTERRUPTED
    result = backend.dispatch("generate_missing", {"row_ids": [row["id"] for row in rows]})
    assert result["queued"] == [rows[0]["id"]]
    assert backend.session.rows[1].status == RowStatus.INTERRUPTED


def test_regeneration_seed_and_replacement_are_per_run(backend, monkeypatch):
    row_id = backend.dispatch("add_rows", {"prompts": ["shape"]})["rows"][0]["id"]
    backend.dispatch("update_row", {"row_id": row_id, "settings": {
        "seed": 42, "random_seed": False, "regen_use_same_seed": False, "generate_behavior": "replace",
    }})
    monkeypatch.setattr("app.queue_manager.secrets.randbelow", lambda limit: 1234)
    action = {"row_ids": [row_id]}
    for expected_seed, extra in ((42, {}), (1234, {"regenerate": True})):
        assert backend.dispatch("generate_rows", {**action, **extra})["queued"] == [row_id]
        deadline = time.monotonic() + 4
        while not backend.queue.is_idle() and time.monotonic() < deadline:
            time.sleep(0.005)
        row = backend.session.rows[0]
        assert row.status == RowStatus.COMPLETED
        assert len(row.images) == 1
        assert row.images[0].metadata["seed"] == expected_seed
        assert row.settings.seed == 42


def test_template_application_adds_only_current_expansion_and_can_be_undone(backend):
    data = {"name": "Shape", "template": "A {shape}", "variables": {"shape": ["square", "triangle"]}}
    assert backend.dispatch("expand_template", data)["prompts"] == ["A square", "A triangle"]
    backend.dispatch("apply_template", {**data, "variables": {"shape": ["hexagon"]}})
    assert [row.prompt for row in backend.session.rows] == ["A hexagon"]
    assert backend.session.templates[0].variables == {"shape": ["hexagon"]}
    backend.dispatch("undo", {})
    assert backend.session.rows == []
    assert backend.session.templates == []



def test_legacy_session_loads_without_retaining_unused_appearance_settings(backend, tmp_path):
    older = backend.session.to_dict()
    older["version"] = "1.0"
    older["global_settings"].update(theme="light", prompt_highlighting=True, num_images=2)
    path = tmp_path / "old-session.json"
    path.write_text(json.dumps(older), encoding="utf-8")

    current = backend.dispatch("load_session", {"path": str(path)})["session"]
    assert current["global_settings"]["num_images"] == 2
    assert "theme" not in current["global_settings"]
    assert "prompt_highlighting" not in current["global_settings"]
    assert json.loads(path.read_text(encoding="utf-8")) == older



def test_one_image_sample_counts_toward_target_without_replacing_existing_images(backend):
    row_id = backend.dispatch("add_rows", {"prompts": ["A shape"]})["rows"][0]["id"]
    backend.dispatch("global_settings", {"num_images": 3, "prompt_wrapper": "On white", "generate_behavior": "replace"})
    report = backend.dispatch("preflight_sample", {"row_id": row_id})
    assert report["valid"] and report["total_images"] == 1 and report["total_requests"] == 0
    assert report["rows"][0]["request"]["prompt"] == "A shape\n\nOn white"
    assert not backend.session.rows[0].images

    sample_ids = []
    for action, data, expected in (
        ("generate_sample", {"row_id": row_id}, 1),
        ("generate_sample", {"row_id": row_id}, 2),
        ("generate_missing", {"row_ids": [row_id]}, 3),
    ):
        assert backend.dispatch(action, data)["queued"] == [row_id]
        deadline = time.monotonic() + 4
        while not backend.queue.is_idle() and time.monotonic() < deadline:
            time.sleep(0.005)
        images = backend.session.rows[0].images
        assert len(images) == expected
        assert [image.id for image in images[:len(sample_ids)]] == sample_ids
        sample_ids = [image.id for image in images]
        assert images[-1].metadata["generation_settings"]["num_images"] == 1
        assert backend.session.global_settings.num_images == 3

    backend.session.rows[0].status = RowStatus.INTERRUPTED
    with pytest.raises(ValueError, match="interrupted"):
        backend.dispatch("generate_sample", {"row_id": row_id})



def test_loopback_backend_binds_without_resolving_a_hostname(monkeypatch):
    def unexpected_lookup(*args, **kwargs):
        pytest.fail("Loopback startup must not depend on reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", unexpected_lookup)
    with backend_server.LoopbackHTTPServer(("127.0.0.1", 0), backend_server.Handler) as server:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.socket.getsockname()[1] > 0
