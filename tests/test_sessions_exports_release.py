import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from app import storage
from app.batch_parser import parse_batch_input
from app.exporter import ImageExporter
from app.generation_log import verify_rows
from app.generation_state import apply_generation_event
from app.models import GlobalSettings, ImageResult, InputAttachment, RowData, RowSettings, RowStatus, SessionData, effective_settings
from app.session_manager import SessionManager


def picture(path, color='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (32, 32), color).save(path)
    return path


def test_atomic_save_failure_preserves_previous_bytes(tmp_path, monkeypatch):
    manager = SessionManager()
    manager.add_rows(['original'])
    destination = manager.save(tmp_path / 'session.json')
    before = destination.read_bytes()
    manager.session.rows[0].prompt = 'new draft'
    replace = storage.os.replace
    def fail_final(source, target):
        if Path(target) == destination:
            raise OSError('simulated replace failure')
        return replace(source, target)
    monkeypatch.setattr(storage.os, 'replace', fail_final)
    with pytest.raises(OSError, match='simulated'):
        manager.save(destination)
    assert destination.read_bytes() == before
    assert json.loads(destination.read_text())['rows'][0]['prompt'] == 'original'
    assert not list(tmp_path.glob('*.tmp'))


def test_corrupt_latest_recovers_valid_backup(tmp_path, monkeypatch):
    monkeypatch.setenv('IMAGEAI_DATA_DIR', str(tmp_path))
    manager = SessionManager()
    manager.add_rows(['previous good'])
    destination = manager.autosave()
    manager.session.rows[0].prompt = 'latest'
    manager.autosave()
    destination.write_text('{truncated')
    other = SessionManager()
    assert other.restore_last().rows[0].prompt == 'previous good'
    assert 'Recovered' in other.restore_error
    other.autosave()
    assert destination.with_suffix('.json.invalid').read_text() == '{truncated'


def test_failed_load_does_not_replace_active_session(tmp_path):
    manager = SessionManager()
    manager.add_rows(['stay'])
    current = manager.session
    path = tmp_path / 'bad.json'
    path.write_text('[]')
    with pytest.raises(ValueError):
        manager.load(path)
    assert manager.session is current


def test_running_rows_restore_as_interrupted_without_submission(tmp_path):
    manager = SessionManager()
    manager.add_rows(['keep me'])
    manager.session.rows[0].status = RowStatus.GENERATING
    path = manager.save(tmp_path / 'session.json')
    restored = manager.load(path)
    assert restored.rows[0].status == RowStatus.INTERRUPTED
    assert 'explicitly' in restored.rows[0].error_message


def test_receipt_recovery_is_owned_hash_checked_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('IMAGEAI_DATA_DIR', str(tmp_path))
    manager = SessionManager()
    manager.add_rows(['recover this'])
    row = manager.session.rows[0]
    row.status = RowStatus.GENERATING
    manager.autosave()
    root = manager.session_output_dir() / row.id
    path = picture(root / 'result.png')
    image = {'path': str(path), 'metadata': {'provider': 'mock'}, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    payload = {'job_id': 'job1', 'requested_at': 1, 'images': [image], 'paths': [str(path)], 'keep_existing': True}
    receipt = {'schema_version': 1, 'session_id': manager.session.id, 'row_id': row.id, 'job_id': 'job1',
               'event': 'completed', 'payload': payload, 'files': [{'path': str(path), 'sha256': image['sha256']}]}
    (root / 'imageai-run-job1.json').write_text(json.dumps(receipt))
    other = SessionManager()
    recovered = other.restore_last()
    assert recovered.rows[0].status == RowStatus.COMPLETED
    assert len(recovered.rows[0].images) == 1
    assert recovered.rows[0].applied_job_ids == ['job1']
    other.autosave()
    assert len(SessionManager().restore_last().rows[0].images) == 1
    # Deliberately removed images must not reappear from an already applied receipt.
    other.session.rows[0].images.clear()
    other.autosave()
    assert not SessionManager().restore_last().rows[0].images


def test_recovery_rejects_hash_mismatch_and_unowned_path(tmp_path, monkeypatch):
    monkeypatch.setenv('IMAGEAI_DATA_DIR', str(tmp_path))
    manager = SessionManager()
    manager.add_rows(['recover'])
    row = manager.session.rows[0]
    manager.autosave()
    root = manager.session_output_dir() / row.id
    inside = picture(root / 'changed.png')
    outside = picture(tmp_path / 'outside.png')
    items = [{'path': str(inside), 'sha256': '0' * 64}, {'path': str(outside), 'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()}]
    receipt = {'schema_version': 1, 'session_id': manager.session.id, 'row_id': row.id, 'job_id': 'bad', 'event': 'completed',
               'payload': {'images': [{'path': item['path'], 'metadata': {}} for item in items]}, 'files': items}
    (root / 'imageai-run-bad.json').write_text(json.dumps(receipt))
    restored = SessionManager().restore_last().rows[0]
    assert restored.status == RowStatus.ERROR
    assert not restored.images


def test_export_never_overwrites_variants_or_existing_files(tmp_path):
    sources = [picture(tmp_path / f'img-{i}.png', color) for i, color in enumerate(['red', 'green', 'blue'])]
    row = RowData('r1', prompt='same prompt', images=[ImageResult(str(i), 'r1', str(path)) for i, path in enumerate(sources)])
    exporter = ImageExporter('{index}_{slug}.png')
    count, paths = exporter.export_rows([row], tmp_path / 'out')
    assert count == 3 and len(set(paths)) == 3
    before = {path: path.read_bytes() for path in paths}
    _, second_paths = exporter.export_rows([row], tmp_path / 'out')
    assert not set(paths).intersection(second_paths)
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize('pattern', ['../escape.png', '/tmp/escape.png', 'nested/file.png', r'..\escape.png', '{unknown}.png'])
def test_bad_export_plan_writes_nothing(tmp_path, pattern):
    source = picture(tmp_path / 'source.png')
    row = RowData('r1', prompt='hello', images=[ImageResult('i', 'r1', str(source))])
    with pytest.raises(ValueError):
        ImageExporter(pattern).export_rows([row], tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_portable_bundle_survives_move_and_original_removal(tmp_path):
    source = picture(tmp_path / 'source.png')
    reference = picture(tmp_path / 'reference.png', 'blue')
    row = RowData('r1', prompt='hello', images=[ImageResult('i', 'r1', str(source))], attachments=[InputAttachment('a', str(reference))])
    session = SessionData('s1', GlobalSettings(), [row])
    result = ImageExporter('{index}_{slug}.png').export_bundle(session, tmp_path / 'out')
    root = Path(result['path'])
    manifest = json.loads((root / 'manifest.json').read_text())
    for entry in manifest['files']:
        path = root / entry['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
    moved = tmp_path / 'moved'
    root.rename(moved)
    source.unlink(); reference.unlink()
    restored = SessionManager.read(moved / 'session.json')
    assert Path(restored.rows[0].images[0].file_path).is_file()
    assert Path(restored.rows[0].attachments[0].file_path).is_file()
    assert (moved / 'contact-sheet.png').is_file()
    assert session.rows[0].images[0].file_path == str(source)


def test_changed_original_is_reported_not_exported(tmp_path):
    source = picture(tmp_path / 'source.png')
    row = RowData('r1', prompt='test', images=[ImageResult('i', 'r1', str(source), {'sha256': '0' * 64})])
    result = ImageExporter('{index}.png').export_bundle(SessionData('s1', GlobalSettings(), [row]), tmp_path / 'out')
    assert result['exported'] == 0
    assert result['missing'][0]['reason'] == 'SHA-256 mismatch'


def test_partial_replace_and_uncertain_outcome_keep_old_images(tmp_path):
    original = picture(tmp_path / 'old.png')
    partial = picture(tmp_path / 'partial.png', 'green')
    row = RowData('r1', images=[ImageResult('old', 'r1', str(original))])
    apply_generation_event(row, 'error', {'job_id': 'j1', 'keep_existing': False, 'metadata': {'outcome_unknown': True}, 'images': [{'path': str(partial), 'metadata': {}}]})
    assert row.status == RowStatus.INTERRUPTED
    assert len(row.images) == 2
    apply_generation_event(row, 'skipped', {'preserve_status': True})
    assert row.status == RowStatus.INTERRUPTED


def test_row_preset_clears_inherited_custom_dimensions():
    global_settings = GlobalSettings(size_preset='Custom', custom_size={'width': 2048, 'height': 2048})
    row = RowData('r1', settings=RowSettings(size_preset='Portrait'))
    assert effective_settings(row, global_settings).custom_size is None


def test_batch_verification_counts_images_and_expected_set(tmp_path):
    path = picture(tmp_path / 'one.png')
    row = RowData('r1', status=RowStatus.COMPLETED, images=[ImageResult('i1', 'r1', str(path))])
    report = verify_rows([row], GlobalSettings(num_images=2))
    assert report['image_count'] == 1
    assert report['requested_image_count'] == 2
    assert report['successful_prompts'] == 0
    assert not report['matches_prompt_count']


def test_csv_missing_prompt_column_cannot_generate_ids():
    report = parse_batch_input('id,text\n001,hello', mode='csv', csv_column='prompt')
    assert report.errors and report.prompts == []


def test_bom_csv_and_invalid_json_prompts():
    assert parse_batch_input('\ufeffprompt\nhello', mode='csv', csv_column='prompt').prompts == ['hello']
    report = parse_batch_input('[{"prompt": null}, {"prompt": 42}]', mode='json_array')
    assert report.errors and report.prompts == []
