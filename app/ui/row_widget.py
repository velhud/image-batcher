from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
import re
import uuid
import mimetypes

from app.models import GlobalSettings, InputAttachment, RowData, RowSettings, RowStatus, effective_settings


class ClickableLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.clicked.emit()
        return super().mousePressEvent(event)


class PromptHighlighter(QtGui.QSyntaxHighlighter):
    """Minimal syntax highlighter for weighting/placeholder syntax."""

    def __init__(self, parent: QtGui.QTextDocument) -> None:
        super().__init__(parent)
        self.patterns = [
            (re.compile(r"\{[^}]+\}"), QtGui.QColor("#3366ff")),
            (re.compile(r"\([^)]+\)"), QtGui.QColor("#cc6600")),
            (re.compile(r"\[[^\]]+\]"), QtGui.QColor("#009966")),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for pattern, color in self.patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(color)
                self.setFormat(start, end - start, fmt)


class RowWidget(QtWidgets.QWidget):
    generate_requested = QtCore.Signal(str)
    regenerate_requested = QtCore.Signal(str)
    cancel_requested = QtCore.Signal(str)
    duplicate_requested = QtCore.Signal(str)
    delete_requested = QtCore.Signal(str)
    export_requested = QtCore.Signal(str)
    selection_changed = QtCore.Signal(str, bool)
    open_gallery_requested = QtCore.Signal(str)
    settings_changed = QtCore.Signal(str)

    def __init__(
        self,
        row: RowData,
        index: int,
        provider_models: Dict[str, List[str]],
        global_settings: GlobalSettings,
        parent: Optional[QtWidgets.QWidget] = None,
        *, registry=None,
    ) -> None:
        super().__init__(parent)
        self._refreshing = True
        self.registry = registry
        self.row = row
        self.provider_models = provider_models
        self.global_settings = global_settings
        self._build_ui(index)
        self.refresh(global_settings, index)

    def _build_ui(self, index: int) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Left column with index and checkbox
        left = QtWidgets.QVBoxLayout()
        idx_label = QtWidgets.QLabel(f"{index}")
        idx_label.setFixedWidth(24)
        idx_label.setAlignment(QtCore.Qt.AlignCenter)
        self.index_label = idx_label
        self.select_box = QtWidgets.QCheckBox()
        self.select_box.stateChanged.connect(self._on_select_changed)
        left.addWidget(idx_label)
        left.addWidget(self.select_box)
        left.addStretch()
        layout.addLayout(left)

        # Middle: prompt and settings
        mid = QtWidgets.QVBoxLayout()
        self.prompt_edit = QtWidgets.QTextEdit()
        self.prompt_edit.setPlaceholderText("Enter prompt...")
        self.prompt_edit.setFixedHeight(90)
        self.prompt_edit.textChanged.connect(self._on_prompt_changed)
        mid.addWidget(self.prompt_edit)

        info_row = QtWidgets.QHBoxLayout()
        self.char_count = QtWidgets.QLabel("0 chars")
        info_row.addWidget(self.char_count)
        copy_prompt_btn = QtWidgets.QPushButton("Copy prompt")
        copy_prompt_btn.clicked.connect(self._copy_prompt)
        info_row.addWidget(copy_prompt_btn)
        info_row.addStretch()
        mid.addLayout(info_row)

        self.status_label = QtWidgets.QLabel("Idle")
        mid.addWidget(self.status_label)

        self.batch_meta_label = QtWidgets.QLabel("")
        mid.addWidget(self.batch_meta_label)

        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("Tags (comma separated)")
        self.tags_edit.editingFinished.connect(self._on_tags_changed)
        mid.addWidget(self.tags_edit)

        references = QtWidgets.QHBoxLayout()
        self.reference_label = QtWidgets.QLabel()
        self.add_reference_btn = QtWidgets.QPushButton("Add reference images…")
        self.add_reference_btn.clicked.connect(self._add_references)
        self.clear_reference_btn = QtWidgets.QPushButton("Clear references")
        self.clear_reference_btn.clicked.connect(self._clear_references)
        references.addWidget(self.reference_label)
        references.addWidget(self.add_reference_btn)
        references.addWidget(self.clear_reference_btn)
        mid.addLayout(references)
        self._build_settings_panel(mid)
        self._build_actions(mid)
        layout.addLayout(mid, stretch=4)

        # Right: preview
        right = QtWidgets.QVBoxLayout()
        self.preview = ClickableLabel()
        self.preview.setFixedSize(160, 160)
        self.preview.setFrameShape(QtWidgets.QFrame.Box)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setText("No image")
        self.preview.clicked.connect(lambda: self.open_gallery_requested.emit(self.row.id))
        right.addWidget(self.preview)
        self.thumb_meta = QtWidgets.QLabel("")
        right.addWidget(self.thumb_meta)
        btns = QtWidgets.QHBoxLayout()
        copy_img_btn = QtWidgets.QPushButton("Copy image")
        copy_img_btn.clicked.connect(self._copy_image)
        open_btn = QtWidgets.QPushButton("Open in Finder")
        open_btn.clicked.connect(self._open_in_finder)
        btns.addWidget(copy_img_btn)
        btns.addWidget(open_btn)
        right.addLayout(btns)
        layout.addLayout(right, stretch=1)

    def _build_settings_panel(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        self.settings_group = QtWidgets.QGroupBox("Per-row settings")
        self.settings_group.setCheckable(True)
        self.settings_group.setChecked(False)
        form = QtWidgets.QFormLayout()

        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItem("Inherit global", "")
        for pid in self.provider_models.keys():
            self.provider_combo.addItem(pid, pid)
        self.provider_combo.currentTextChanged.connect(self._on_setting_change)
        form.addRow("Provider override", self.provider_combo)

        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.currentTextChanged.connect(self._on_setting_change)
        form.addRow("Model", self.model_combo)

        self.size_combo = QtWidgets.QComboBox()
        self.size_combo.addItems(["", "Square 1024", "Portrait", "Landscape", "Custom"])
        self.size_combo.currentTextChanged.connect(self._on_setting_change)
        form.addRow("Size", self.size_combo)
        dimensions = QtWidgets.QHBoxLayout()
        self.width_spin, self.height_spin = QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
        for spin in (self.width_spin, self.height_spin):
            spin.setRange(64, 8192)
            spin.setValue(1024)
            spin.setSingleStep(16)
            spin.valueChanged.connect(self._on_setting_change)
            dimensions.addWidget(spin)
        form.addRow("Custom width × height", dimensions)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["png", "jpeg", "webp"])
        self.format_combo.currentTextChanged.connect(self._on_setting_change)
        form.addRow("Output format", self.format_combo)
        self.background_combo = QtWidgets.QComboBox()
        self.background_combo.addItems(["auto", "opaque", "transparent"])
        self.background_combo.currentTextChanged.connect(self._on_setting_change)
        form.addRow("Background", self.background_combo)

        self.num_spin = QtWidgets.QSpinBox()
        self.num_spin.setRange(1, 10)
        self.num_spin.valueChanged.connect(self._on_setting_change)
        form.addRow("Images", self.num_spin)

        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItems(["", "None", "Photorealistic", "Illustration", "3D Render"])
        self.style_combo.currentTextChanged.connect(self._on_setting_change)
        form.addRow("Style", self.style_combo)

        self.negative_edit = QtWidgets.QTextEdit()
        self.negative_edit.setFixedHeight(50)
        self.negative_edit.textChanged.connect(self._on_setting_change)
        form.addRow("Negative prompt", self.negative_edit)

        seed_layout = QtWidgets.QHBoxLayout()
        self.seed_spin = QtWidgets.QSpinBox()
        self.seed_spin.setRange(0, 999999999)
        self.seed_spin.valueChanged.connect(self._on_setting_change)
        self.random_seed_box = QtWidgets.QCheckBox("Random")
        self.random_seed_box.setChecked(True)
        self.random_seed_box.stateChanged.connect(self._on_setting_change)
        seed_layout.addWidget(self.seed_spin)
        seed_layout.addWidget(self.random_seed_box)
        form.addRow("Seed", seed_layout)

        self.quality_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.quality_slider.setRange(1, 10)
        self.quality_slider.setValue(5)
        self.quality_slider.valueChanged.connect(self._on_setting_change)
        form.addRow("Quality", self.quality_slider)

        self.safety_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.safety_slider.setRange(0, 3)
        self.safety_slider.setValue(2)
        self.safety_slider.valueChanged.connect(self._on_setting_change)
        form.addRow("Safety", self.safety_slider)

        reset_btn = QtWidgets.QPushButton("Reset to global defaults")
        reset_btn.clicked.connect(self.reset_to_global)
        form.addRow(reset_btn)

        content = QtWidgets.QWidget()
        content.setLayout(form)
        content.setVisible(False)
        outer = QtWidgets.QVBoxLayout(self.settings_group)
        outer.addWidget(content)
        self.settings_group.toggled.connect(content.setVisible)
        parent_layout.addWidget(self.settings_group)

    def _build_actions(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        btn_row = QtWidgets.QHBoxLayout()
        self.generate_btn = QtWidgets.QPushButton("Generate")
        self.generate_btn.setProperty("class", "primary")
        self.generate_btn.clicked.connect(lambda: self.generate_requested.emit(self.row.id))
        btn_row.addWidget(self.generate_btn)

        self.generate_behavior = QtWidgets.QComboBox()
        self.generate_behavior.addItems(["Keep images", "Replace images"])
        self.generate_behavior.currentIndexChanged.connect(self._on_setting_change)
        btn_row.addWidget(self.generate_behavior)

        self.regen_btn = QtWidgets.QPushButton("Regenerate")
        self.regen_btn.clicked.connect(lambda: self.regenerate_requested.emit(self.row.id))
        btn_row.addWidget(self.regen_btn)
        self.regen_btn.setVisible(False)

        self.regen_same_seed = QtWidgets.QCheckBox("Same seed")
        self.regen_same_seed.stateChanged.connect(self._on_setting_change)
        btn_row.addWidget(self.regen_same_seed)

        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.clicked.connect(lambda: self.cancel_requested.emit(self.row.id))
        btn_row.addWidget(self.stop_btn)

        self.duplicate_btn = QtWidgets.QPushButton("Duplicate")
        self.duplicate_btn.clicked.connect(lambda: self.duplicate_requested.emit(self.row.id))
        btn_row.addWidget(self.duplicate_btn)

        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.row.id))
        btn_row.addWidget(self.delete_btn)

        self.export_btn = QtWidgets.QPushButton("Export")
        self.export_btn.clicked.connect(lambda: self.export_requested.emit(self.row.id))
        btn_row.addWidget(self.export_btn)
        parent_layout.addLayout(btn_row)

    def _on_select_changed(self, state: int) -> None:
        if self._refreshing:
            return
        self.row.selected = self.select_box.isChecked()
        self.selection_changed.emit(self.row.id, self.row.selected)

    def _on_tags_changed(self) -> None:
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        self.row.tags = tags
        self.settings_changed.emit(self.row.id)

    def _on_prompt_changed(self) -> None:
        self.row.prompt = self.prompt_edit.toPlainText()
        self.char_count.setText(f"{len(self.row.prompt)} chars")
        self.settings_changed.emit(self.row.id)

    def _on_setting_change(self) -> None:
        if self._refreshing:
            return
        sender = self.sender()
        settings = self.row.settings
        if sender is self.provider_combo:
            settings.provider_id = self.provider_combo.currentData() or None
            choices = self.provider_models.get(settings.provider_id, [])
            settings.model_id = choices[0] if settings.provider_id and choices else None
        elif sender is self.model_combo:
            settings.model_id = self.model_combo.currentData() or None
        elif sender in {self.size_combo, self.width_spin, self.height_spin}:
            settings.size_preset = self.size_combo.currentText() or None
            settings.custom_size = {"width": self.width_spin.value(), "height": self.height_spin.value()} if settings.size_preset == "Custom" else None
        elif sender is self.num_spin:
            settings.num_images = self.num_spin.value()
        elif sender is self.style_combo:
            settings.style_preset = self.style_combo.currentText() or None
        elif sender is self.negative_edit:
            settings.negative_prompt = self.negative_edit.toPlainText()
        elif sender in {self.seed_spin, self.random_seed_box}:
            settings.seed = None if self.random_seed_box.isChecked() else self.seed_spin.value()
            settings.random_seed = self.random_seed_box.isChecked()
        elif sender is self.quality_slider:
            settings.quality = self.quality_slider.value()
        elif sender is self.safety_slider:
            settings.safety = self.safety_slider.value()
        elif sender is self.generate_behavior:
            settings.keep_images = None
            settings.generate_behavior = "keep" if self.generate_behavior.currentIndex() == 0 else "replace"
        elif sender is self.regen_same_seed:
            settings.regen_use_same_seed = self.regen_same_seed.isChecked()
        elif sender is self.format_combo:
            settings.output_format = self.format_combo.currentText()
        elif sender is self.background_combo:
            settings.background = self.background_combo.currentText()
        if sender in {self.provider_combo, self.model_combo}:
            self._normalize_model_change()
        self.settings_changed.emit(self.row.id)
        if sender in {self.provider_combo, self.model_combo, self.size_combo}:
            self.refresh(self.global_settings, int(self.index_label.text()))

    def _normalize_model_change(self) -> None:
        """An explicit model choice must not retain hidden obsolete controls."""
        settings = self.row.settings
        settings.thinking_budget, settings.thinking_level = 0, "high"
        if self.registry is None:
            return
        effective = effective_settings(self.row, self.global_settings)
        model = next((model for info in self.registry.info_list() if info.id == effective.provider_id
                      for model in info.models if model.id == effective.model_id), None)
        if model is None:
            return
        caps = model.capabilities
        if not caps.supports_style:
            settings.style_preset = "None"
        if not caps.supports_negative_prompt:
            settings.negative_prompt = ""
        if not caps.supports_seed:
            settings.seed, settings.random_seed = None, True
        if not caps.supports_safety:
            settings.safety = 2
        if effective.num_images > caps.max_images:
            settings.num_images = caps.max_images
        if effective.output_format not in caps.output_formats:
            settings.output_format = caps.output_formats[0]
        if not caps.supports_background:
            settings.background = "auto"
        elif effective.output_format == "jpeg" and effective.background == "transparent":
            settings.background = "opaque"
        if effective.custom_size and not caps.supports_custom_size:
            settings.size_preset, settings.custom_size = "Square 1024", None

    def _add_references(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Reference images", "", "Images (*.png *.jpg *.jpeg *.webp)")
        existing = {item.file_path for item in self.row.attachments}
        for path in paths:
            if path not in existing:
                self.row.attachments.append(InputAttachment(str(uuid.uuid4()), path, mimetypes.guess_type(path)[0] or "image/png"))
                existing.add(path)
        self.settings_changed.emit(self.row.id)
        self.refresh(self.global_settings, int(self.index_label.text()))

    def _clear_references(self) -> None:
        self.row.attachments.clear()
        self.settings_changed.emit(self.row.id)
        self.refresh(self.global_settings, int(self.index_label.text()))

    def reset_to_global(self) -> None:
        self.row.settings = RowSettings()
        self.refresh(self.global_settings, int(self.index_label.text()))
        self.settings_changed.emit(self.row.id)

    def _copy_prompt(self) -> None:
        QtGui.QGuiApplication.clipboard().setText(self.row.prompt or "")

    def _copy_image(self) -> None:
        if not self.row.images:
            return
        img_path = Path(self.row.images[-1].file_path)
        if img_path.exists():
            pix = QtGui.QPixmap(str(img_path))
            QtGui.QGuiApplication.clipboard().setPixmap(pix)

    def _open_in_finder(self) -> None:
        if not self.row.images:
            return
        img_path = Path(self.row.images[-1].file_path)
        if img_path.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(img_path)))

    def refresh(self, global_settings: GlobalSettings, index: int) -> None:
        self._refreshing = True
        self.global_settings = global_settings
        self.index_label.setText(str(index))
        self.select_box.setChecked(self.row.selected)
        self.prompt_edit.blockSignals(True)
        self.prompt_edit.setPlainText(self.row.prompt)
        self.prompt_edit.blockSignals(False)
        self.char_count.setText(f"{len(self.row.prompt)} chars")
        self.tags_edit.setText(", ".join(self.row.tags))
        eff = effective_settings(self.row, global_settings)
        self._populate_models(eff.provider_id)
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(self.row.settings.provider_id or ""))
        self.model_combo.setCurrentIndex(self.model_combo.findData(self.row.settings.model_id or ""))
        self.size_combo.setCurrentText(self.row.settings.size_preset or eff.size_preset)
        custom = eff.custom_size or {"width": 1024, "height": 1024}
        self.width_spin.setValue(custom["width"])
        self.height_spin.setValue(custom["height"])
        self.format_combo.setCurrentText(eff.output_format)
        self.background_combo.setCurrentText(eff.background)
        self.reference_label.setText(f"{len(self.row.attachments)} reference image(s)")
        self.reference_label.setToolTip("\n".join(a.file_path for a in self.row.attachments))
        self.num_spin.setValue(self.row.settings.num_images or eff.num_images)
        self.style_combo.setCurrentText(self.row.settings.style_preset or eff.style_preset)
        self.negative_edit.setPlainText(eff.negative_prompt)
        if self.row.settings.random_seed is None:
            self.random_seed_box.setChecked(eff.random_seed)
        else:
            self.random_seed_box.setChecked(self.row.settings.random_seed)
        self.seed_spin.setValue(self.row.settings.seed or eff.seed or 0)
        self.quality_slider.setValue(self.row.settings.quality or eff.quality)
        self.safety_slider.setValue(eff.safety)
        keep = (
            self.row.settings.keep_images
            if self.row.settings.keep_images is not None
            else (
                True
                if (self.row.settings.generate_behavior or eff.generate_behavior) != "replace"
                else False
            )
        )
        self.generate_behavior.setCurrentText("Keep images" if keep else "Replace images")
        self.regen_same_seed.setChecked(self.row.settings.regen_use_same_seed if self.row.settings.regen_use_same_seed is not None else eff.regen_use_same_seed)
        self.update_status(self.row.status, self.row.error_message)
        meta_parts = []
        if self.row.category_id:
            meta_parts.append(f"Category: {self.row.category_id}")
        if self.row.prompt_id:
            meta_parts.append(f"Prompt ID: {self.row.prompt_id}")
        if self.row.images:
            meta_parts.append(f"File: {Path(self.row.images[-1].file_path).name}")
        self.batch_meta_label.setText(" • ".join(meta_parts))
        self._update_preview()
        if self.global_settings.prompt_highlighting and not hasattr(self, "_highlighter"):
            self._highlighter = PromptHighlighter(self.prompt_edit.document())
        self._apply_capabilities(eff)
        self._refreshing = False

    def _apply_capabilities(self, settings: GlobalSettings) -> None:
        if self.registry is None:
            return
        model = next((m for info in self.registry.info_list() for m in info.models if info.id == settings.provider_id and m.id == settings.model_id), None)
        if not model:
            return
        caps = model.capabilities
        self.num_spin.setMaximum(caps.max_images)
        self.style_combo.setEnabled(caps.supports_style)
        self.negative_edit.setEnabled(caps.supports_negative_prompt)
        self.seed_spin.setEnabled(caps.supports_seed)
        self.random_seed_box.setEnabled(caps.supports_seed)
        self.quality_slider.setEnabled(caps.supports_quality)
        self.safety_slider.setEnabled(caps.supports_safety)
        self.width_spin.setEnabled(caps.supports_custom_size and settings.size_preset == "Custom")
        self.height_spin.setEnabled(caps.supports_custom_size and settings.size_preset == "Custom")
        self.format_combo.clear()
        self.format_combo.addItems(caps.output_formats)
        self.format_combo.setCurrentText(settings.output_format)
        self.background_combo.setEnabled(caps.supports_background)
        self.add_reference_btn.setEnabled(caps.supports_attachments and len(self.row.attachments) < caps.max_attachments)
        self.add_reference_btn.setToolTip(f"Up to {caps.max_attachments} reference images" if caps.supports_attachments else "Not supported by this model")

    def _populate_models(self, provider_id: str) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("Inherit global", "")
        for model in self.provider_models.get(provider_id, []):
            self.model_combo.addItem(model, model)
        self.model_combo.blockSignals(False)

    def update_status(self, status: RowStatus, message: str = "") -> None:
        self.row.status = status
        self.row.error_message = message
        text = status.value
        if message:
            text = f"{text}: {message}"
        self.status_label.setText(text)
        self.regen_btn.setVisible(bool(self.row.images))
        self.export_btn.setEnabled(bool(self.row.images))
        self.stop_btn.setVisible(status in {RowStatus.GENERATING, RowStatus.QUEUED})
        self.generate_btn.setEnabled(status in {RowStatus.IDLE, RowStatus.ERROR, RowStatus.FILTERED, RowStatus.CANCELLED, RowStatus.COMPLETED, RowStatus.INTERRUPTED})

    def _update_preview(self) -> None:
        if self.row.images:
            img_path = Path(self.row.images[-1].file_path)
            if img_path.exists():
                pixmap = QtGui.QPixmap(str(img_path)).scaled(
                    self.preview.width(),
                    self.preview.height(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
                self.preview.setPixmap(pixmap)
                self.preview.setText("")
                self.thumb_meta.setText(f"{len(self.row.images)} image(s)")
                return
        self.preview.setPixmap(QtGui.QPixmap())
        self.preview.setText("No image")
        self.thumb_meta.setText("")
