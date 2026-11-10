import json
import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6 import QtCore, QtWidgets

from app.models import RowStatus, RowSettings
from app.providers.registry import ProviderRegistry
from app.session_manager import SessionManager
from app.ui.batch_dialog import BatchInputDialog
from app.ui.main_window import MainWindow


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setenv('IMAGEAI_DATA_DIR', str(tmp_path))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    manager = SessionManager()
    manager.add_rows(['A geometric bird'])
    window = MainWindow(ProviderRegistry(), manager)
    yield app, window, manager
    window.close()
    app.processEvents()
    window.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def test_refresh_does_not_materialize_overrides(desktop):
    _, window, manager = desktop
    row = manager.session.rows[0]
    expected = RowSettings().to_dict()
    assert row.settings.to_dict() == expected
    manager.session.global_settings.num_images = 3
    for _ in range(3):
        window._refresh_all_rows()
    assert row.settings.to_dict() == expected
    assert window.row_widgets[row.id].num_spin.value() == 3


def test_global_population_preserves_loaded_settings(desktop):
    _, window, manager = desktop
    settings = manager.session.global_settings
    settings.num_images = 3
    settings.size_preset = 'Portrait'
    settings.prompt_wrapper = 'An instruction that must not disappear'
    before = settings.to_dict()
    window.apply_global_settings_to_ui()
    assert settings.to_dict() == before


def test_global_model_capabilities_are_scoped_to_provider(desktop):
    _, window, manager = desktop
    settings = manager.session.global_settings
    settings.provider_id, settings.model_id = 'openai', 'gpt-image-2'
    settings.output_format, settings.background, settings.safety = 'webp', 'transparent', 3
    window.apply_global_settings_to_ui()
    window.provider_combo.setCurrentIndex(window.provider_combo.findData('azure-openai'))
    assert settings.provider_id == 'azure-openai'
    assert settings.model_id == 'gpt-image-2'
    assert settings.output_format == 'png'
    assert settings.background == 'auto'
    assert settings.safety == 2


def test_worker_completion_reaches_gui_thread_and_persists(desktop):
    app, window, manager = desktop
    row = manager.session.rows[0]
    gui_thread = threading.get_ident()
    observed = []
    original = window._handle_queue_event
    def handler(event, row_id, payload):
        observed.append(threading.get_ident())
        return original(event, row_id, payload)
    window._handle_queue_event = handler
    window._generate_row(row.id)
    deadline = time.monotonic() + 5
    while row.status != RowStatus.COMPLETED and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert row.status == RowStatus.COMPLETED
    assert observed and set(observed) == {gui_thread}
    saved = json.loads(manager.last_path.read_text())
    assert saved['rows'][0]['images']
    assert manager.session.rows[0].applied_job_ids


def test_batch_apply_reparses_latest_content_and_retains_ids(desktop):
    _, window, _ = desktop
    dialog = BatchInputDialog(window)
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData('csv'))
    dialog.text_edit.setPlainText('prompt_id,category_id,prompt\np1,c1,First prompt')
    dialog._on_preview()
    dialog.text_edit.setPlainText('prompt_id,category_id,prompt\np2,c2,Changed prompt')
    dialog._apply()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.rows[0].prompt_id == 'p2'
    assert dialog.rows[0].category_id == 'c2'
    assert dialog.rows[0].prompt == 'Changed prompt'
    dialog.deleteLater()


def test_batch_invalid_csv_cannot_close_as_accepted(desktop):
    _, window, _ = desktop
    dialog = BatchInputDialog(window)
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData('csv'))
    dialog.text_edit.setPlainText('id,text\n1,Wrong field')
    dialog._apply()
    assert dialog.result() != QtWidgets.QDialog.Accepted
    assert dialog.errors
    dialog.deleteLater()


@pytest.mark.parametrize('action', ['row', 'all', 'selected'])
def test_declining_interrupted_retry_enqueues_nothing(desktop, monkeypatch, action):
    _, window, manager = desktop
    row = manager.session.rows[0]
    row.status = RowStatus.INTERRUPTED
    row.selected = True
    admitted = []
    monkeypatch.setattr(window.queue, 'enqueue', lambda *args, **kwargs: admitted.append(args))
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *args, **kwargs: QtWidgets.QMessageBox.No)
    if action == 'row':
        window._generate_row(row.id)
    elif action == 'all':
        window._generate_all()
    else:
        window._generate_selected()
    assert admitted == []
    assert row.status == RowStatus.INTERRUPTED
