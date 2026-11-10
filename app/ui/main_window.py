from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from app.azure_logo_workflow import apply_azure_logo_preset
from app.exporter import ImageExporter
from app.generation_log import verify_rows
from app.models import GlobalSettings, RowData, RowSettings, RowStatus, SessionData, PromptTemplate, effective_settings, validate_settings
from app.providers.registry import ProviderRegistry
from app.queue_manager import GenerationQueue
from app.session_manager import SessionManager
from app.undo_manager import UndoManager
from app.stats import compute_stats
from app.tagging import filter_rows
from app.templates import expand_template, parse_variable_block
from app.batch_service import preflight
from app.generation_state import apply_generation_event
from .batch_dialog import BatchInputDialog
from .gallery import GalleryDialog
from .row_widget import RowWidget
from .settings_dialog import AppSettingsDialog
from .styles import BASE_STYLE


class MainWindow(QtWidgets.QMainWindow):
    queue_event = QtCore.Signal(int, str, str, object)

    def __init__(self, registry: ProviderRegistry, session_manager: SessionManager) -> None:
        super().__init__()
        self._loading_ui = True
        self._queue_epoch = 0
        self.queue_event.connect(self._receive_queue_event, QtCore.Qt.QueuedConnection)
        self.registry = registry
        self.session_manager = session_manager
        self.session: SessionData = session_manager.session
        self.undo_manager = UndoManager()
        self.undo_manager.push(self.session)
        self.rate_limit_hits = 0
        self.queue = self._create_queue()
        self.row_widgets: Dict[str, RowWidget] = {}
        self.provider_models: Dict[str, List[str]] = self._build_provider_model_map()
        self.setWindowTitle("ImageAI Batch Generator")
        self.setMinimumSize(1200, 800)
        self._build_ui()
        self._setup_shortcuts()
        self._start_autosave()
        self._loading_ui = False
        self.apply_global_settings_to_ui()
        if self.session_manager.restore_error:
            self.banner.setText(self.session_manager.restore_error)
            self.banner.setVisible(True)
        if not self.session.rows:
            self.add_row()
        else:
            self._reload_rows()

    def _build_provider_model_map(self) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for info in self.registry.info_list():
            mapping[info.id] = [m.id for m in info.models]
        return mapping

    def _create_queue(self) -> GenerationQueue:
        self._queue_epoch += 1
        epoch = self._queue_epoch
        queue = GenerationQueue(
            self.registry,
            output_root=self.session_manager.session_output_dir(),
            concurrency=self.session.global_settings.concurrency_limit,
            rpm=self.session.global_settings.rate_limit_rpm,
        )
        previous = getattr(self, "queue", None)
        if previous is not None:
            queue.rate_limiter = previous.rate_limiter
            queue.rate_limiter.set_rpm(self.session.global_settings.rate_limit_rpm)
        queue.register_listener(lambda event, row_id, payload: self.queue_event.emit(epoch, event, row_id, payload))
        return queue

    def _idle_for_change(self) -> bool:
        QtCore.QCoreApplication.sendPostedEvents(self, QtCore.QEvent.MetaCall)
        if not self.queue.is_idle():
            QtWidgets.QMessageBox.information(self, "Queue is active", "Wait for active work to finish, or stop the queue before changing sessions or concurrency.")
            return False
        return True

    def _reset_queue(self) -> None:
        self.queue.shutdown()
        self.queue = self._create_queue()

    @QtCore.Slot(int, str, str, object)
    def _receive_queue_event(self, epoch: int, event: str, row_id: str, payload: dict) -> None:
        if epoch == self._queue_epoch:
            self._handle_queue_event(event, row_id, payload)


    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)
        main_layout.addLayout(self._build_top_bar())
        self.banner = QtWidgets.QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#fff3cd;color:#654d05;padding:8px;border-radius:4px;")
        self.banner.setVisible(False)
        main_layout.addWidget(self.banner)
        body = QtWidgets.QHBoxLayout()
        self.global_panel = self._build_global_panel()
        self.settings_scroll = QtWidgets.QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setMinimumWidth(300)
        self.settings_scroll.setMaximumWidth(350)
        self.settings_scroll.setWidget(self.global_panel)
        body.addWidget(self.settings_scroll)
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.rows_container = QtWidgets.QWidget()
        self.rows_layout = QtWidgets.QVBoxLayout(self.rows_container)
        self.rows_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.rows_container)
        body.addWidget(self.scroll_area, stretch=1)
        main_layout.addLayout(body, stretch=1)
        main_layout.addLayout(self._build_bottom_bar())
        self.status_bar_label = QtWidgets.QLabel()
        main_layout.addWidget(self.status_bar_label)
        self.setCentralWidget(central)
        self.setStyleSheet(BASE_STYLE)

    def _build_top_bar(self) -> QtWidgets.QHBoxLayout:
        layout = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("ImageAI")
        title.setStyleSheet("font-weight:700;font-size:20px;")
        layout.addWidget(title)
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.setMinimumWidth(140)
        for info in self.registry.info_list():
            self.provider_combo.addItem(info.name, userData=info.id)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setMinimumWidth(180)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo)
        self.rate_limit_note = QtWidgets.QLabel()
        # Provider detail belongs in the settings pane, not the narrow toolbar.
        self.rate_limit_note.setVisible(False)
        self.global_toggle = QtWidgets.QPushButton("Settings")
        self.global_toggle.setCheckable(True)
        self.global_toggle.setChecked(True)
        self.global_toggle.clicked.connect(self._toggle_global_panel)
        layout.addWidget(self.global_toggle)
        for label, callback in (("Import batch…", self._open_batch_dialog), ("Templates…", self._open_templates_dialog)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        session_button = QtWidgets.QPushButton("Session")
        session_menu = QtWidgets.QMenu(session_button)
        for label, callback in (("New session", self._new_session), ("Open…", self._open_session), ("Save…", self._save_session),
                                ("App settings…", self._open_app_settings), ("Azure logo preset", self._apply_azure_logo_preset),
                                ("Statistics…", self._open_stats), ("Undo", self._undo), ("Redo", self._redo)):
            session_menu.addAction(label, callback)
        session_button.setMenu(session_menu)
        layout.addWidget(session_button)
        self.tag_filter_edit = QtWidgets.QLineEdit()
        self.tag_filter_edit.setPlaceholderText("Filter by tag…")
        self.tag_filter_edit.textChanged.connect(self._apply_tag_filter)
        layout.addWidget(self.tag_filter_edit, stretch=1)
        return layout

    def _build_global_panel(self) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox("Batch defaults")
        form = QtWidgets.QFormLayout(panel)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows)
        self.size_combo = QtWidgets.QComboBox()
        self.size_combo.addItems(["Square 1024", "Portrait", "Landscape", "Custom"])
        self.size_combo.currentTextChanged.connect(self._global_changed)
        form.addRow("Image size", self.size_combo)
        custom_layout = QtWidgets.QHBoxLayout()
        self.width_spin, self.height_spin = QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
        for spin in (self.width_spin, self.height_spin):
            spin.setRange(64, 8192)
            spin.setSingleStep(16)
            spin.setValue(1024)
            spin.valueChanged.connect(self._global_changed)
            custom_layout.addWidget(spin)
        form.addRow("Custom width × height", custom_layout)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["png", "jpeg", "webp"])
        self.format_combo.currentTextChanged.connect(self._global_changed)
        form.addRow("Output format", self.format_combo)
        self.background_combo = QtWidgets.QComboBox()
        self.background_combo.addItems(["auto", "opaque", "transparent"])
        self.background_combo.currentTextChanged.connect(self._global_changed)
        form.addRow("Background", self.background_combo)
        self.capability_note = QtWidgets.QLabel()
        self.capability_note.setWordWrap(True)
        form.addRow(self.capability_note)


        self.num_spin = QtWidgets.QSpinBox()
        self.num_spin.setRange(1, 10)
        self.num_spin.valueChanged.connect(self._global_changed)
        form.addRow("Images per prompt", self.num_spin)

        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItems(["None", "Photorealistic", "Illustration", "3D Render"])
        self.style_combo.currentTextChanged.connect(self._global_changed)
        form.addRow("Style preset", self.style_combo)

        self.negative_edit = QtWidgets.QTextEdit()
        self.negative_edit.setFixedHeight(60)
        self.negative_edit.textChanged.connect(self._global_changed)
        form.addRow("Negative prompt", self.negative_edit)

        self.prompt_wrapper_edit = QtWidgets.QTextEdit()
        self.prompt_wrapper_edit.setFixedHeight(80)
        self.prompt_wrapper_edit.textChanged.connect(self._global_changed)
        form.addRow("Prompt wrapper", self.prompt_wrapper_edit)

        seed_layout = QtWidgets.QHBoxLayout()
        self.seed_spin = QtWidgets.QSpinBox()
        self.seed_spin.setRange(0, 999999999)
        self.seed_spin.valueChanged.connect(self._global_changed)
        self.random_seed_box = QtWidgets.QCheckBox("Random seed")
        self.random_seed_box.setChecked(True)
        self.random_seed_box.stateChanged.connect(self._global_changed)
        seed_layout.addWidget(self.seed_spin)
        seed_layout.addWidget(self.random_seed_box)
        form.addRow("Seed", seed_layout)

        self.quality_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.quality_slider.setRange(1, 10)
        self.quality_slider.setValue(5)
        self.quality_slider.valueChanged.connect(self._global_changed)
        form.addRow("Quality", self.quality_slider)

        self.safety_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.safety_slider.setRange(0, 3)
        self.safety_slider.setValue(2)
        self.safety_slider.valueChanged.connect(self._global_changed)
        form.addRow("Safety/NSFW", self.safety_slider)

        self.prompt_highlight_box = QtWidgets.QCheckBox("Enable prompt highlighting")
        self.prompt_highlight_box.stateChanged.connect(self._global_changed)
        form.addRow(self.prompt_highlight_box)

        self.generate_behavior_combo = QtWidgets.QComboBox()
        self.generate_behavior_combo.addItems(["keep", "replace"])
        self.generate_behavior_combo.currentTextChanged.connect(self._global_changed)
        form.addRow("Generate behavior", self.generate_behavior_combo)

        self.regen_same_seed_box = QtWidgets.QCheckBox("Regenerate with same seed")
        self.regen_same_seed_box.setChecked(True)
        self.regen_same_seed_box.stateChanged.connect(self._global_changed)
        form.addRow(self.regen_same_seed_box)

        apply_btn = QtWidgets.QPushButton("Apply to selected")
        apply_btn.clicked.connect(self._apply_global_to_selected)
        form.addRow(apply_btn)

        note = QtWidgets.QLabel(
            "Rows inherit these defaults unless a setting is overridden. Queued runs retain the settings captured when submitted."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return panel

    def _build_bottom_bar(self) -> QtWidgets.QHBoxLayout:
        layout = QtWidgets.QHBoxLayout()
        for label, callback in (("Add row", self.add_row), ("Preflight", self._preflight_batch),
                                ("Generate selected", self._generate_selected), ("Generate missing", self._generate_missing),
                                ("Generate all", self._generate_all), ("Stop all", lambda: self.queue.cancel_all())):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        actions = QtWidgets.QPushButton("More actions")
        menu = QtWidgets.QMenu(actions)
        for label, callback in (("Stop after current", lambda: self.queue.stop_after_current()),
                                ("Add multiple rows…", self._add_many_rows), ("Select all", self._select_all),
                                ("Clear selection", self._clear_selection), ("Select rows without images", self._select_no_image),
                                ("Select pending", self._select_pending), ("Delete selected", self._delete_selected),
                                ("Delete all", self._delete_all), ("Verify batch", self._verify_batch),
                                ("Export selected images…", self._export_selected), ("Export all images…", self._export_all)):
            menu.addAction(label, callback)
        actions.setMenu(menu)
        layout.addWidget(actions)
        portable = QtWidgets.QPushButton("Export portable batch…")
        portable.clicked.connect(self._export_bundle)
        layout.addWidget(portable)
        return layout

    def _toggle_global_panel(self) -> None:
        self.settings_scroll.setVisible(self.global_toggle.isChecked())

    def apply_global_settings_to_ui(self) -> None:
        self._loading_ui = True
        gs = self.session.global_settings
        try:
            self.size_combo.setCurrentText(gs.size_preset)
            custom = gs.custom_size or {"width": 1024, "height": 1024}
            self.width_spin.setValue(custom["width"])
            self.height_spin.setValue(custom["height"])
            self.num_spin.setValue(gs.num_images)
            self.style_combo.setCurrentText(gs.style_preset)
            self.negative_edit.setPlainText(gs.negative_prompt)
            self.prompt_wrapper_edit.setPlainText(gs.prompt_wrapper)
            self.random_seed_box.setChecked(gs.random_seed)
            self.seed_spin.setValue(gs.seed or 0)
            self.quality_slider.setValue(gs.quality)
            self.safety_slider.setValue(gs.safety)
            self.prompt_highlight_box.setChecked(gs.prompt_highlighting)
            self.generate_behavior_combo.setCurrentText(gs.generate_behavior)
            self.regen_same_seed_box.setChecked(gs.regen_use_same_seed)
            self.provider_combo.setCurrentIndex(self.provider_combo.findData(gs.provider_id))
            self._populate_models(gs.provider_id)
            index = self.model_combo.findData(gs.model_id)
            if index < 0:
                self.model_combo.addItem(f"{gs.model_id} (select a supported model)", gs.model_id)
                index = self.model_combo.count() - 1
            self.model_combo.setCurrentIndex(index)
            self._apply_capabilities()
            self.format_combo.setCurrentText(gs.output_format)
            self.background_combo.setCurrentText(gs.background)
        finally:
            self._loading_ui = False

    def _apply_capabilities(self) -> None:
        gs = self.session.global_settings
        model = next((m for info in self.registry.info_list() for m in info.models if info.id == gs.provider_id and m.id == gs.model_id), None)
        if model is None:
            self.capability_note.setText("Select a currently supported model. Saved model choices are never silently replaced.")
            return
        caps = model.capabilities
        self.num_spin.setMaximum(caps.max_images)
        self.style_combo.setEnabled(caps.supports_style)
        self.negative_edit.setEnabled(caps.supports_negative_prompt)
        self.seed_spin.setEnabled(caps.supports_seed)
        self.random_seed_box.setEnabled(caps.supports_seed)
        self.quality_slider.setEnabled(caps.supports_quality)
        self.safety_slider.setEnabled(caps.supports_safety)
        self.width_spin.setEnabled(caps.supports_custom_size and gs.size_preset == "Custom")
        self.height_spin.setEnabled(caps.supports_custom_size and gs.size_preset == "Custom")
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self.format_combo.addItems(caps.output_formats)
        self.format_combo.setCurrentText(gs.output_format)
        self.format_combo.blockSignals(False)
        self.background_combo.setEnabled(caps.supports_background)
        note = f"Reference images: up to {caps.max_attachments}. " if caps.supports_attachments else "Reference images are unavailable for this model. "
        if caps.quality_options:
            note += "Quality 1–3 = low, 4–7 = medium, 8–10 = high. "
        if gs.provider_id == "google":
            note += "Custom dimensions select aspect ratio and resolution tier; output pixels are provider controlled."
        self.capability_note.setText(note)

    def _populate_models(self, provider_id: str) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for info in self.registry.info_list():
            if info.id == provider_id:
                for m in info.models:
                    self.model_combo.addItem(m.name, userData=m.id)
                self.rate_limit_note.setText(info.rate_limit_note or "")
                break
        self.model_combo.blockSignals(False)

    def _global_changed(self) -> None:
        if self._loading_ui:
            return
        self._push_undo()
        gs = self.session.global_settings
        gs.size_preset = self.size_combo.currentText()
        gs.custom_size = {"width": self.width_spin.value(), "height": self.height_spin.value()} if gs.size_preset == "Custom" else None
        gs.output_format = self.format_combo.currentText() or "png"
        gs.background = self.background_combo.currentText() or "auto"
        gs.num_images = self.num_spin.value()
        gs.style_preset = self.style_combo.currentText()
        gs.negative_prompt = self.negative_edit.toPlainText()
        gs.prompt_wrapper = self.prompt_wrapper_edit.toPlainText()
        gs.random_seed = self.random_seed_box.isChecked()
        gs.seed = None if gs.random_seed else self.seed_spin.value()
        gs.quality = self.quality_slider.value()
        gs.safety = self.safety_slider.value()
        gs.prompt_highlighting = self.prompt_highlight_box.isChecked()
        gs.generate_behavior = self.generate_behavior_combo.currentText()
        gs.regen_use_same_seed = self.regen_same_seed_box.isChecked()
        self._apply_capabilities()
        self._refresh_all_rows()
        self.session_manager.autosave()

    def _on_provider_changed(self) -> None:
        if self._loading_ui:
            return
        gs = self.session.global_settings
        gs.provider_id = self.provider_combo.currentData()
        self._populate_models(gs.provider_id)
        gs.model_id = self.model_combo.currentData()
        self._on_model_changed()

    def _on_model_changed(self) -> None:
        if self._loading_ui:
            return
        gs = self.session.global_settings
        gs.model_id = self.model_combo.currentData()
        model = next((m for info in self.registry.info_list() for m in info.models
                      if info.id == gs.provider_id and m.id == gs.model_id), None)
        if model:
            caps = model.capabilities
            gs.num_images = min(gs.num_images, caps.max_images)
            if not caps.supports_style:
                gs.style_preset = "None"
            if not caps.supports_negative_prompt:
                gs.negative_prompt = ""
            if not caps.supports_seed:
                gs.seed, gs.random_seed = None, True
            if not caps.supports_safety:
                gs.safety = 2
            if gs.output_format not in caps.output_formats:
                gs.output_format = caps.output_formats[0]
            if not caps.supports_background:
                gs.background = "auto"
            gs.thinking_budget, gs.thinking_level = 0, "high"
        self.apply_global_settings_to_ui()
        self._refresh_all_rows()
        self.session_manager.autosave()

    def _add_row_widget(self, row: RowData, index: int) -> None:
        widget = RowWidget(row, index, self.provider_models, self.session.global_settings, registry=self.registry)
        widget.generate_requested.connect(self._generate_row)
        widget.regenerate_requested.connect(self._regenerate_row)
        widget.cancel_requested.connect(lambda rid=row.id: self.queue.cancel(rid))
        widget.duplicate_requested.connect(self._duplicate_row)
        widget.delete_requested.connect(self._delete_row)
        widget.export_requested.connect(self._export_row)
        widget.selection_changed.connect(self._on_row_selection)
        widget.open_gallery_requested.connect(self._open_gallery)
        widget.settings_changed.connect(self._on_row_settings_changed)
        self.row_widgets[row.id] = widget
        self.rows_layout.addWidget(widget)

    def _reload_rows(self) -> None:
        # clear layout
        for i in reversed(range(self.rows_layout.count())):
            item = self.rows_layout.takeAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        self.row_widgets.clear()
        for idx, row in enumerate(self.session.rows, start=1):
            self._add_row_widget(row, idx)
        self._update_status_summary()
        self._apply_tag_filter()

    def add_row(self) -> None:
        self._push_undo()
        row = RowData(id=str(uuid.uuid4()), prompt="")
        self.session.rows.append(row)
        self._add_row_widget(row, len(self.session.rows))
        self.session_manager.autosave()
        self._update_status_summary()

    def _add_many_rows(self) -> None:
        count, ok = QtWidgets.QInputDialog.getInt(self, "Add rows", "How many rows?", 10, 1, 500)
        if ok:
            self._push_undo()
            for _ in range(count):
                self.add_row()
        self._update_status_summary()

    def _select_all(self) -> None:
        for row in self.session.rows:
            row.selected = True
            self.row_widgets[row.id].select_box.setChecked(True)
        self._update_status_summary()

    def _clear_selection(self) -> None:
        for row in self.session.rows:
            row.selected = False
            self.row_widgets[row.id].select_box.setChecked(False)
        self._update_status_summary()

    def _select_no_image(self) -> None:
        for row in self.session.rows:
            has_image = bool(row.images)
            row.selected = not has_image
            self.row_widgets[row.id].select_box.setChecked(row.selected)
        self._update_status_summary()

    def _select_pending(self) -> None:
        for row in self.session.rows:
            row.selected = row.status != RowStatus.COMPLETED
            self.row_widgets[row.id].select_box.setChecked(row.selected)
        self._update_status_summary()

    def _confirm_interrupted(self, rows: List[RowData]) -> bool:
        count = sum(row.status == RowStatus.INTERRUPTED for row in rows)
        if not count:
            return True
        answer = QtWidgets.QMessageBox.question(
            self, "Review interrupted work",
            f"{count} interrupted row(s) have an uncertain provider outcome. "
            "Review saved outputs and provider history first. A new request may be billed again. "
            "Start a new request for these rows?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _generate_row(self, row_id: str, reviewed_interrupted: bool = False) -> None:
        row = self._row_by_id(row_id)
        if not row:
            return
        if not reviewed_interrupted and not self._confirm_interrupted([row]):
            return
        report = preflight([row], self.session.global_settings, self.registry)
        if not report["valid"]:
            row.status = RowStatus.ERROR
            row.error_message = "; ".join(report["rows"][0]["errors"])
            self._refresh_all_rows()
            return
        self.queue.enqueue(row, self.session.global_settings, row_index=self.session.rows.index(row) + 1)
        self._update_status_summary()

    def _regenerate_row(self, row_id: str) -> None:
        row = self._row_by_id(row_id)
        if not row:
            return
        use_same_seed = (
            row.settings.regen_use_same_seed
            if row.settings.regen_use_same_seed is not None
            else self.session.global_settings.regen_use_same_seed
        )
        if not use_same_seed:
            row.settings.random_seed = True
            row.settings.seed = None
        self._generate_row(row_id)

    def _generate_selected(self) -> None:
        rows = [row for row in self.session.rows if row.selected]
        if not self._validate_batch(rows) or not self._confirm_interrupted(rows):
            return
        for row in rows:
            if row.status in {RowStatus.IDLE, RowStatus.ERROR, RowStatus.FILTERED, RowStatus.CANCELLED, RowStatus.COMPLETED, RowStatus.INTERRUPTED}:
                self._generate_row(row.id, reviewed_interrupted=True)

    def _generate_missing(self) -> None:
        rows = [row for row in self.session.rows if row.status not in {RowStatus.QUEUED, RowStatus.GENERATING}]
        if not self._validate_batch(rows):
            return
        for row in rows:
            self.queue.enqueue(row, self.session.global_settings, row_index=self.session.rows.index(row) + 1, missing_only=True)
        self._update_status_summary()

    def _generate_all(self) -> None:
        if not self._validate_batch(self.session.rows):
            return
        if not self._confirm_interrupted(self.session.rows):
            return
        threshold = self.session.global_settings.confirm_generate_threshold or 300
        if len(self.session.rows) >= threshold:
            confirm = QtWidgets.QMessageBox.question(
                self, "Confirm large batch", f"Generate all {len(self.session.rows)} rows?"
            )
            if confirm != QtWidgets.QMessageBox.Yes:
                return
        for row in self.session.rows:
            if row.status in {RowStatus.IDLE, RowStatus.ERROR, RowStatus.FILTERED, RowStatus.CANCELLED, RowStatus.COMPLETED, RowStatus.INTERRUPTED}:
                self._generate_row(row.id, reviewed_interrupted=True)

    def _delete_row(self, row_id: str) -> None:
        if not self._idle_for_change():
            return
        row = self._row_by_id(row_id)
        if not row:
            return
        self._push_undo()
        idx = self.session.rows.index(row)
        self.session.rows.remove(row)
        widget = self.row_widgets.pop(row_id)
        widget.deleteLater()
        # reindex
        for i, r in enumerate(self.session.rows, start=1):
            self.row_widgets[r.id].index_label.setText(str(i))
        self.session_manager.autosave()
        self._update_status_summary()

    def _duplicate_row(self, row_id: str) -> None:
        row = self._row_by_id(row_id)
        if not row:
            return
        self._push_undo()
        clone = RowData(
            id=str(uuid.uuid4()),
            prompt=row.prompt,
            prompt_id=row.prompt_id,
            category_id=row.category_id,
            source_metadata=dict(row.source_metadata),
            status=RowStatus.IDLE,
            settings=RowSettings(**row.settings.to_dict()),
        )
        idx = self.session.rows.index(row) + 1
        self.session.rows.insert(idx, clone)
        self._reload_rows()
        self.session_manager.autosave()
        self._update_status_summary()

    def _delete_selected(self) -> None:
        to_delete = [r for r in self.session.rows if r.selected]
        if not to_delete:
            return
        confirm = QtWidgets.QMessageBox.question(
            self, "Delete selected", f"Delete {len(to_delete)} selected row(s)?"
        )
        if confirm == QtWidgets.QMessageBox.Yes:
            self._push_undo()
            for r in list(to_delete):
                self._delete_row(r.id)
            self._update_status_summary()

    def _delete_all(self) -> None:
        if not self._idle_for_change():
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Confirm delete",
            "Delete all rows and their images from this session? This cannot be undone.",
        )
        if confirm == QtWidgets.QMessageBox.Yes:
            self._push_undo()
            self.session.rows.clear()
            self._reload_rows()
            self.session_manager.autosave()
            self._update_status_summary()

    def _on_row_selection(self, row_id: str, selected: bool) -> None:
        row = self._row_by_id(row_id)
        if row:
            row.selected = selected

    def _on_row_settings_changed(self, row_id: str) -> None:
        self._push_undo()
        # row object already mutated by widget; refresh derived data
        widget = self.row_widgets.get(row_id)
        if widget:
            widget.refresh(self.session.global_settings, int(widget.index_label.text()))
        self.session_manager.autosave()
        self._apply_tag_filter()

    def _handle_queue_event(self, event: str, row_id: str, payload: Dict) -> None:
        if event == "queue_position":
            return
        row = self._row_by_id(row_id)
        if not row:
            return
        apply_generation_event(row, event, payload)
        widget = self.row_widgets.get(row_id)
        if widget:
            widget.refresh(self.session.global_settings, self.session.rows.index(row) + 1)
        self.session_manager.autosave()
        self._update_status_summary()

    def _validate_batch(self, rows: List[RowData]) -> bool:
        report = preflight(rows, self.session.global_settings, self.registry)
        if not report["valid"]:
            errors = [f"{r['prompt_id'] or r['row_id']}: {', '.join(r['errors'])}" for r in report["rows"] if r["errors"]]
            QtWidgets.QMessageBox.warning(self, "Batch validation", "\n".join(errors[:20]))
            return False
        return True

    def _preflight_batch(self) -> None:
        if self._validate_batch(self.session.rows):
            report = preflight(self.session.rows, self.session.global_settings, self.registry)
            QtWidgets.QMessageBox.information(self, "Batch preflight", f"{len(report['rows'])} rows ready.\n{report['total_images']} requested images in {report['total_requests']} initial API requests.\nNo provider request was sent.")

    def _export_bundle(self) -> None:
        folder = self._ask_export_folder()
        if folder:
            try:
                result = ImageExporter(self.session.global_settings.naming_pattern).export_bundle(self.session, folder)
                QtWidgets.QMessageBox.information(self, "Portable batch", f"Exported {result['exported']} images.\nMissing files: {len(result['missing'])}\n{result['path']}")
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, "Export failed", str(exc))

    def _refresh_all_rows(self) -> None:
        for idx, row in enumerate(self.session.rows, start=1):
            widget = self.row_widgets.get(row.id)
            if widget:
                widget.refresh(self.session.global_settings, idx)
        self._update_status_summary()

    def _row_by_id(self, row_id: str) -> Optional[RowData]:
        for row in self.session.rows:
            if row.id == row_id:
                return row
        return None

    def _apply_global_to_selected(self) -> None:
        gs = self.session.global_settings
        for row in self.session.rows:
            if row.selected:
                row.settings = RowSettings()
                self.row_widgets[row.id].refresh(gs, int(self.row_widgets[row.id].index_label.text()))
        self.session_manager.autosave()

    def _apply_tag_filter(self) -> None:
        tags = [t.strip() for t in self.tag_filter_edit.text().split(",") if t.strip()]
        for row in self.session.rows:
            widget = self.row_widgets.get(row.id)
            if widget:
                widget.setVisible(not tags or all(t.lower() in [x.lower() for x in row.tags] for t in tags))

    def _open_batch_dialog(self) -> None:
        dlg = BatchInputDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            prompts, mode = dlg.get_result()
            if not prompts:
                QtWidgets.QMessageBox.information(self, "No prompts", "No prompts parsed.")
                return
            self._push_undo()
            if mode.startswith("Replace"):
                if not self._idle_for_change():
                    return
                self.session.rows.clear()
            parsed_rows = list(dlg.rows)
            if mode.startswith("Fill"):
                empty_rows = [r for r in self.session.rows if not r.prompt and r.status not in {RowStatus.QUEUED, RowStatus.GENERATING}]
                for row, parsed in zip(empty_rows, parsed_rows):
                    row.prompt, row.prompt_id, row.category_id = parsed.prompt, parsed.prompt_id, parsed.category_id
                    row.source_metadata = parsed.source_metadata
                parsed_rows = parsed_rows[len(empty_rows):]
            for parsed in parsed_rows:
                row = RowData(id=str(uuid.uuid4()), prompt=parsed.prompt, prompt_id=parsed.prompt_id,
                              category_id=parsed.category_id, source_metadata=parsed.source_metadata)
                self.session.rows.append(row)
            self._reload_rows()
            self.session_manager.autosave()

    def _open_templates_dialog(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Prompt Templates")
        layout = QtWidgets.QVBoxLayout(dlg)
        name_edit = QtWidgets.QLineEdit("Template")
        layout.addWidget(QtWidgets.QLabel("Template name"))
        layout.addWidget(name_edit)
        tpl_edit = QtWidgets.QLineEdit("A {style} portrait of {subject}")
        layout.addWidget(QtWidgets.QLabel("Template string"))
        layout.addWidget(tpl_edit)
        var_edit = QtWidgets.QTextEdit("style: photorealistic, illustration\nsubject: cat, dog")
        layout.addWidget(QtWidgets.QLabel("Variables (key: comma separated values)"))
        layout.addWidget(var_edit)
        preview_btn = QtWidgets.QPushButton("Preview")
        preview_list = QtWidgets.QListWidget()
        layout.addWidget(preview_btn)
        layout.addWidget(preview_list)
        result_prompts: List[str] = []

        def do_preview() -> None:
            nonlocal result_prompts
            variables = parse_variable_block(var_edit.toPlainText())
            tpl = PromptTemplate(name=name_edit.text(), template=tpl_edit.text(), variables=variables)
            result_prompts = expand_template(tpl)
            preview_list.clear()
            for p in result_prompts:
                preview_list.addItem(p)

        preview_btn.clicked.connect(do_preview)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            if not result_prompts:
                do_preview()
            if result_prompts:
                self._push_undo()
                tpl = PromptTemplate(name=name_edit.text(), template=tpl_edit.text(), variables=parse_variable_block(var_edit.toPlainText()))
                self.session.templates.append(tpl)
                for prompt in result_prompts:
                    self.session.rows.append(RowData(id=str(uuid.uuid4()), prompt=prompt))
                self._reload_rows()
                self.session_manager.autosave()

    def _open_gallery(self, row_id: str) -> None:
        row = self._row_by_id(row_id)
        if not row or not row.images:
            return
        dlg = GalleryDialog(row.images, self)
        dlg.export_requested.connect(lambda idx: self._export_row(row_id, idx))
        dlg.export_all_requested.connect(lambda: self._export_row(row_id))
        dlg.exec()

    def _export_row(self, row_id: str, single_index: Optional[int] = None) -> None:
        row = self._row_by_id(row_id)
        if not row or not row.images:
            return
        folder = self._ask_export_folder()
        if not folder:
            return
        exporter = ImageExporter(self.session.global_settings.naming_pattern)
        rows = [row]
        if single_index is not None:
            # export only specific image
            img = row.images[single_index]
            temp_row = RowData(id=row.id, prompt=row.prompt, images=[img])
            rows = [temp_row]
        count, _ = exporter.export_rows(rows, folder, export_metadata=True)
        QtWidgets.QMessageBox.information(self, "Export", f"Exported {count} image(s) to {folder}")

    def _export_selected(self) -> None:
        rows = [r for r in self.session.rows if r.selected and r.images]
        self._export_rows(rows)

    def _export_all(self) -> None:
        rows = [r for r in self.session.rows if r.images]
        self._export_rows(rows)

    def _export_rows(self, rows: List[RowData]) -> None:
        if not rows:
            QtWidgets.QMessageBox.information(self, "Export", "No images to export.")
            return
        folder = self._ask_export_folder()
        if not folder:
            return
        exporter = ImageExporter(self.session.global_settings.naming_pattern)
        count, _ = exporter.export_rows(rows, folder, export_metadata=True)
        QtWidgets.QMessageBox.information(self, "Export", f"Exported {count} image(s) to {folder}")

    def _verify_batch(self) -> None:
        result = verify_rows(self.session.rows, self.session.global_settings)
        message = (
            f"Prompts: {result['total_prompts']}\n"
            f"Successful images: {result['successful_images']}\n"
            f"Filtered: {result['filtered_count']}\n"
            f"Failed: {result['failed_count']}\n"
            f"Missing: {result['missing_count']}\n"
            f"Matches prompt count: {result['matches_prompt_count']}"
        )
        QtWidgets.QMessageBox.information(self, "Batch verification", message)

    def _apply_azure_logo_preset(self) -> None:
        if not self._idle_for_change():
            return
        self._push_undo()
        apply_azure_logo_preset(self.session.global_settings)
        self._reset_queue()
        self.apply_global_settings_to_ui()
        self._refresh_all_rows()
        self.session_manager.autosave()
        QtWidgets.QMessageBox.information(self, "Azure Logo Preset", "Azure GPT-Image-2 logo batch defaults applied.")

    def _ask_export_folder(self) -> Optional[Path]:
        default = self.session.global_settings.export_folder or str(Path.home() / "Pictures")
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose export folder", default)
        if folder:
            self.session.global_settings.export_folder = folder
            return Path(folder)
        return None

    def _open_app_settings(self) -> None:
        if not self._idle_for_change():
            return
        dlg = AppSettingsDialog(
            self.session.global_settings.concurrency_limit,
            self.session.global_settings.export_folder,
            self.session.global_settings.theme,
            self.session.global_settings.rate_limit_rpm,
            self.session.global_settings.confirm_generate_threshold,
            self,
        )
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            (
                concurrency,
                export_folder,
                theme,
                api_key,
                gemini_key,
                rpm,
                threshold,
                azure_endpoint,
                azure_deployment,
                azure_api_key,
            ) = dlg.get_values()
            self.session.global_settings.concurrency_limit = concurrency
            if concurrency != self.queue.concurrency:
                self._reset_queue()
            if export_folder:
                self.session.global_settings.export_folder = export_folder
            self.session.global_settings.theme = theme
            self.session.global_settings.rate_limit_rpm = rpm
            self.session.global_settings.confirm_generate_threshold = threshold
            self.queue.rate_limiter.set_rpm(rpm)
            if api_key:
                os.environ["OPENAI_API_KEY"] = api_key
            if gemini_key:
                os.environ["GEMINI_API_KEY"] = gemini_key
            if azure_endpoint:
                os.environ["AZURE_OPENAI_ENDPOINT"] = azure_endpoint
            if azure_deployment:
                os.environ["AZURE_GPT_IMAGE_2_DEPLOYMENT"] = azure_deployment
            if azure_api_key:
                os.environ["AZURE_OPENAI_API_KEY"] = azure_api_key
            QtWidgets.QMessageBox.information(self, "Settings", "Settings saved.")

    def _start_autosave(self) -> None:
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.session_manager.autosave)
        timer.start(20000)

    def _setup_shortcuts(self) -> None:
        shortcuts = [
            (QtGui.QKeySequence("Ctrl+N"), self.add_row, False),
            (QtGui.QKeySequence("Ctrl+Shift+N"), self._add_many_rows, False),
            (QtGui.QKeySequence("Ctrl+Shift+G"), self._generate_selected, True),
            (QtGui.QKeySequence("Ctrl+Shift+A"), self._generate_all, True),
            (QtGui.QKeySequence("Ctrl+E"), self._export_selected, True),
            (QtGui.QKeySequence("Ctrl+Shift+E"), self._export_all, True),
            (QtGui.QKeySequence("Ctrl+A"), self._select_all, True),
            (QtGui.QKeySequence("Ctrl+Z"), self._undo, False),
            (QtGui.QKeySequence("Ctrl+Shift+Z"), self._redo, False),
        ]
        if sys.platform == "darwin":
            shortcuts = [
                (QtGui.QKeySequence("Meta+N"), self.add_row, False),
                (QtGui.QKeySequence("Meta+Shift+N"), self._add_many_rows, False),
                (QtGui.QKeySequence("Meta+Shift+G"), self._generate_selected, True),
                (QtGui.QKeySequence("Meta+Shift+A"), self._generate_all, True),
                (QtGui.QKeySequence("Meta+E"), self._export_selected, True),
                (QtGui.QKeySequence("Meta+Shift+E"), self._export_all, True),
                (QtGui.QKeySequence("Meta+A"), self._select_all, True),
                (QtGui.QKeySequence("Meta+Z"), self._undo, False),
                (QtGui.QKeySequence("Meta+Shift+Z"), self._redo, False),
            ]
        for seq, handler, block_in_text in shortcuts:
            sc = QtGui.QShortcut(seq, self)
            sc.activated.connect(lambda h=handler, b=block_in_text: self._invoke_shortcut(h, b))

    def _invoke_shortcut(self, handler, block_in_text: bool) -> None:
        if block_in_text and self._is_text_focused():
            return
        handler()

    def _is_text_focused(self) -> bool:
        widget = self.focusWidget()
        return isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.queue.cancel_all()
        QtCore.QCoreApplication.sendPostedEvents(self, QtCore.QEvent.MetaCall)
        for row in self.session.rows:
            if row.status in {RowStatus.GENERATING, RowStatus.QUEUED}:
                row.status = RowStatus.INTERRUPTED
                row.error_message = "Closed during generation; review outputs before retrying."
        self.queue.shutdown()
        self.session_manager.autosave()
        return super().closeEvent(event)

    def _push_undo(self) -> None:
        self.undo_manager.push(self.session)

    def _open_session(self) -> None:
        if not self._idle_for_change():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open session", str(Path.home()), "JSON files (*.json)")
        if path:
            try:
                candidate = self.session_manager.load(Path(path))
            except (OSError, ValueError, TypeError) as exc:
                QtWidgets.QMessageBox.warning(self, "Could not open session", str(exc))
                return
            self.session = candidate
            self._reset_queue()
            self._reload_rows()
            self.apply_global_settings_to_ui()
            self._update_status_summary()
            self.undo_manager.push(self.session)

    def _save_session(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save session", str(Path.home()), "JSON files (*.json)")
        if path:
            self.session_manager.save(Path(path))
            self._update_status_summary()

    def _new_session(self) -> None:
        if not self._idle_for_change():
            return
        confirm = QtWidgets.QMessageBox.question(self, "New session", "Clear all rows and start a new session?")
        if confirm == QtWidgets.QMessageBox.Yes:
            self._push_undo()
            self.session = self.session_manager.new_session()
            self._reset_queue()
            self._reload_rows()
            self.apply_global_settings_to_ui()
            self._update_status_summary()

    def _update_status_summary(self) -> None:
        total = len(self.session.rows)
        completed = len([r for r in self.session.rows if r.status == RowStatus.COMPLETED])
        queued = len([r for r in self.session.rows if r.status == RowStatus.QUEUED])
        generating = len([r for r in self.session.rows if r.status == RowStatus.GENERATING])
        self.status_bar_label.setText(f"{total} rows • {completed} done • {queued} queued • {generating} running")
        self.session.stats = compute_stats(self.session.rows)

    def _open_stats(self) -> None:
        stats = compute_stats(self.session.rows)
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Statistics")
        msg.setText(
            f"Total: {stats.total}\nCompleted: {stats.completed}\nErrors: {stats.errors}\n"
            f"Average duration: {stats.average_duration:.2f}s"
        )
        msg.exec()

    def _undo(self) -> None:
        if not self._idle_for_change():
            return
        restored = self.undo_manager.undo(self.session)
        if restored:
            self.session = restored
            self.session_manager.session = restored
            self._reset_queue()
            self._reload_rows()
            self.apply_global_settings_to_ui()
            self.session_manager.autosave()

    def _redo(self) -> None:
        if not self._idle_for_change():
            return
        restored = self.undo_manager.redo(self.session)
        if restored:
            self.session = restored
            self.session_manager.session = restored
            self._reset_queue()
            self._reload_rows()
            self.apply_global_settings_to_ui()
            self.session_manager.autosave()
