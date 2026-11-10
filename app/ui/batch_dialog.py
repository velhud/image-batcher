from __future__ import annotations

from typing import List, Tuple

from PySide6 import QtCore, QtWidgets

from app.batch_parser import BatchParseResult, BatchPromptRow, parse_batch_input


class BatchInputDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Batch Input")
        self.prompts: List[str] = []
        self.errors: List[str] = []
        self.rows: List[BatchPromptRow] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("One prompt per line", userData="lines")
        self.mode_combo.addItem("Numbered list", userData="numbered")
        self.mode_combo.addItem("JSON array", userData="json_array")
        self.mode_combo.addItem("JSON Lines", userData="json_lines")
        self.mode_combo.addItem("CSV", userData="csv")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)

        self.prompt_field_edit = QtWidgets.QLineEdit("prompt")
        layout.addWidget(QtWidgets.QLabel("Prompt field name (for JSON objects)"))
        layout.addWidget(self.prompt_field_edit)

        self.csv_column_edit = QtWidgets.QLineEdit("prompt")
        layout.addWidget(QtWidgets.QLabel("CSV column (name or index)"))
        layout.addWidget(self.csv_column_edit)

        self.text_edit = QtWidgets.QTextEdit()
        layout.addWidget(self.text_edit)

        layout.addWidget(QtWidgets.QLabel("Append to every parsed prompt"))
        self.prompt_append_edit = QtWidgets.QTextEdit()
        self.prompt_append_edit.setFixedHeight(70)
        layout.addWidget(self.prompt_append_edit)

        self.preview_btn = QtWidgets.QPushButton("Preview")
        self.preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(self.preview_btn)

        self.preview_list = QtWidgets.QListWidget()
        layout.addWidget(self.preview_list, stretch=1)

        apply_layout = QtWidgets.QHBoxLayout()
        self.apply_mode_combo = QtWidgets.QComboBox()
        self.apply_mode_combo.addItem("Append new rows")
        self.apply_mode_combo.addItem("Replace all rows")
        self.apply_mode_combo.addItem("Fill empty rows then append")
        apply_layout.addWidget(self.apply_mode_combo)
        ok_btn = QtWidgets.QPushButton("Apply")
        ok_btn.clicked.connect(self._apply)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        apply_layout.addWidget(ok_btn)
        apply_layout.addWidget(cancel_btn)
        layout.addLayout(apply_layout)

        self.error_label = QtWidgets.QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

    def _on_preview(self) -> None:
        mode = self.mode_combo.currentData()
        prompt_field = self.prompt_field_edit.text().strip() or "prompt"
        csv_col = self.csv_column_edit.text().strip()
        result = parse_batch_input(
            self.text_edit.toPlainText(),
            mode=mode,
            prompt_field=prompt_field,
            csv_column=csv_col,
            prompt_append=self.prompt_append_edit.toPlainText(),
        )
        self.prompts = result.prompts
        self.rows = result.rows
        self.errors = result.errors
        self.preview_list.clear()
        for idx, prompt in enumerate(result.prompts, start=1):
            self.preview_list.addItem(f"{idx}. {prompt}")
        if result.errors:
            self.error_label.setText("; ".join(result.errors))
        else:
            self.error_label.setText(f"Parsed {len(self.prompts)} prompts.")

    def _apply(self) -> None:
        # Reparse the current controls so a stale preview can never be imported.
        self._on_preview()
        if self.errors or not self.prompts:
            return
        self.accept()

    def _on_mode_changed(self) -> None:
        is_csv = self.mode_combo.currentData() == "csv"
        self.csv_column_edit.setVisible(is_csv)
        self.prompt_field_edit.setVisible(not is_csv)

    def get_result(self) -> Tuple[List[str], str]:
        """Returns prompts and apply mode string."""
        return self.prompts, self.apply_mode_combo.currentText()
