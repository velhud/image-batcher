from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import tempfile
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageOps

from app.generation_log import LOG_FILENAME, verify_rows
from app.models import RowData, SessionData
from app.storage import atomic_write_json


def slugify(text: str, limit: int = 32) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)
    return cleaned[:limit] or "prompt"


def _safe_name(name: str) -> str:
    if not name or name in {".", ".."} or Path(name).is_absolute() or any(ch in name for ch in '/\\\x00'):
        raise ValueError("Export naming pattern must produce a filename, without directories")
    if len(name.encode("utf-8")) > 220:
        raise ValueError("Export filename is too long; shorten the naming pattern")
    return name


def _copy_unique(source: Path, destination: Path) -> Path:
    """Exclusive creation protects previous exports even on repeated/concurrent runs."""
    index = 1
    candidate = destination
    while True:
        try:
            stream = candidate.open("xb")
            break
        except FileExistsError:
            index += 1
            candidate = destination.with_name(f"{destination.stem}_{index:02d}{destination.suffix}")
    try:
        with stream, source.open("rb") as original:
            shutil.copyfileobj(original, stream)
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise
    return candidate


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


def _unused_metadata_path(folder: Path, name: str) -> Path:
    candidate = folder / name
    number = 1
    while candidate.exists():
        number += 1
        candidate = folder / f"{Path(name).stem}_{number:02d}{Path(name).suffix}"
    return candidate


class ImageExporter:
    def __init__(self, naming_pattern: str) -> None:
        self.naming_pattern = naming_pattern

    def export_rows(self, rows: List[RowData], target_folder: Path, export_metadata: bool = True) -> Tuple[int, List[Path]]:
        target_folder = Path(target_folder)
        planned = []
        # Validate the entire plan before copying any files.
        for idx, row in enumerate(rows, start=1):
            for img_index, image in enumerate(row.images):
                source = Path(image.file_path)
                try:
                    name = self.naming_pattern.format(index=idx, image_index=img_index, slug=slugify(row.prompt), row_id=slugify(row.id[:8]))
                except (KeyError, ValueError, IndexError, AttributeError) as exc:
                    raise ValueError(f"Invalid export naming pattern: {exc}") from exc
                _safe_name(name)
                if image.metadata.get("provider") == "azure-openai":
                    name = source.name
                elif source.suffix:
                    name = str(Path(name).with_suffix(source.suffix.lower()))
                planned.append((idx, row, img_index, image, source, _safe_name(name)))
        target_folder.mkdir(parents=True, exist_ok=True)
        exported = []
        metadata = []
        for idx, row, img_index, image, source, name in planned:
            if not source.is_file():
                continue
            path = _copy_unique(source, target_folder / name)
            exported.append(path)
            metadata.append({"row_index": idx, "row_id": row.id, "prompt_id": row.prompt_id,
                             "category_id": row.category_id, "prompt": row.prompt, "file": path.name,
                             "image_index": img_index, "sha256": _hash(path), "metadata": image.metadata})
        if export_metadata and metadata:
            atomic_write_json(_unused_metadata_path(target_folder, "export_metadata.json"), metadata)
            atomic_write_json(_unused_metadata_path(target_folder, "export_summary.json"), verify_rows(rows))
            log_path = _find_generation_log(rows)
            if log_path:
                _copy_unique(log_path, target_folder / LOG_FILENAME)
        return len(exported), exported

    def export_bundle(self, session: SessionData, target_folder: Path, rows: List[RowData] | None = None) -> dict:
        """Publish a self-contained batch directory only after every export step succeeds."""
        target_folder = Path(target_folder)
        target_folder.mkdir(parents=True, exist_ok=True)
        selected = list(session.rows if rows is None else rows)
        snapshot = SessionData.from_dict(copy.deepcopy(session.to_dict()))
        selected_ids = {row.id for row in selected}
        snapshot.rows = [row for row in snapshot.rows if row.id in selected_ids]
        snapshot.global_settings.export_folder = None
        stage = Path(tempfile.mkdtemp(prefix=".imageai-export-", dir=target_folder))
        name = f"imageai-{session.id[:8]}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        destination = target_folder / name
        artifacts, missing, previews, path_map = [], [], [], {}
        try:
            for row_number, row in enumerate(snapshot.rows, start=1):
                kept_images = []
                for number, image in enumerate(row.images, start=1):
                    source = Path(image.file_path)
                    if not source.is_file():
                        missing.append({"row_id": row.id, "kind": "image", "name": source.name})
                        continue
                    if image.metadata.get("sha256") and _hash(source) != image.metadata["sha256"]:
                        missing.append({"row_id": row.id, "kind": "image", "name": source.name, "reason": "SHA-256 mismatch"})
                        continue
                    relative = Path("images") / f"{row_number:04d}_{slugify(row.prompt_id or row.id)}_{number:03d}{source.suffix.lower()}"
                    (stage / relative).parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, stage / relative)
                    path_map[str(source)] = relative.as_posix()
                    image.file_path = relative.as_posix()
                    image.metadata = copy.deepcopy(image.metadata)
                    kept_images.append(image)
                    artifacts.append({"path": relative.as_posix(), "kind": "image", "row_id": row.id,
                                      "image_id": image.id, "sha256": _hash(stage / relative),
                                      "bytes": (stage / relative).stat().st_size})
                    previews.append((stage / relative, f"{row.prompt_id or row_number} · {number}", image.metadata.get("full_prompt") or row.prompt))
                    # Include the actual frozen references used by this result, even
                    # when the row's current reference selection has since changed.
                    references = image.metadata.get("reference_images")
                    for reference in references if isinstance(references, list) else []:
                        path = reference.get("path") if isinstance(reference, dict) else None
                        if not isinstance(path, str) or not path:
                            missing.append({"row_id": row.id, "kind": "generation_reference", "name": "Unknown reference"})
                            continue
                        original = Path(path)
                        if not original.is_file():
                            missing.append({"row_id": row.id, "kind": "generation_reference", "name": original.name})
                            continue
                        if str(original) in path_map:
                            continue
                        digest = _hash(original)
                        if reference.get("sha256") and digest != reference["sha256"]:
                            missing.append({"row_id": row.id, "kind": "generation_reference", "name": original.name, "reason": "SHA-256 mismatch"})
                            continue
                        ref_relative = Path("references") / f"{digest[:24]}{original.suffix.lower()}"
                        (stage / ref_relative).parent.mkdir(exist_ok=True)
                        if not (stage / ref_relative).exists():
                            shutil.copyfile(original, stage / ref_relative)
                            artifacts.append({"path": ref_relative.as_posix(), "kind": "generation_reference", "row_id": row.id,
                                              "sha256": digest, "bytes": (stage / ref_relative).stat().st_size})
                        path_map[str(original)] = ref_relative.as_posix()
                row.images = kept_images
                kept_references = []
                for number, reference in enumerate(row.attachments, start=1):
                    source = Path(reference.file_path)
                    if not source.is_file():
                        missing.append({"row_id": row.id, "kind": "attachment", "name": source.name})
                        # Keep a clearly missing relative reference so generation cannot silently omit it.
                        reference.file_path = f"attachments/missing_{row_number:04d}_{number:03d}{source.suffix.lower()}"
                        kept_references.append(reference)
                        continue
                    relative = Path("attachments") / f"{row_number:04d}_{number:03d}{source.suffix.lower()}"
                    (stage / relative).parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, stage / relative)
                    path_map[str(source)] = relative.as_posix()
                    reference.file_path = relative.as_posix()
                    kept_references.append(reference)
                    artifacts.append({"path": relative.as_posix(), "kind": "attachment", "row_id": row.id,
                                      "sha256": _hash(stage / relative), "bytes": (stage / relative).stat().st_size})
                row.attachments = kept_references
            data = _portable_metadata(snapshot.to_dict(), path_map)
            atomic_write_json(stage / "session.json", data)
            artifacts.append({"path": "session.json", "kind": "session", "sha256": _hash(stage / "session.json"), "bytes": (stage / "session.json").stat().st_size})
            sheets = _contact_sheets(previews, stage)
            for sheet in sheets:
                artifacts.append({"path": sheet.name, "kind": "contact_sheet", "sha256": _hash(sheet), "bytes": sheet.stat().st_size})
            manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                        "session_id": session.id, "row_count": len(snapshot.rows),
                        "image_count": len(previews), "missing": missing, "files": artifacts,
                        "reproduction_note": "Request settings and source images are preserved; provider output pixels are not guaranteed deterministic."}
            atomic_write_json(stage / "manifest.json", manifest)
            (stage / "README.txt").write_text(
                "ImageAI portable batch\n\nOpen session.json from ImageAI to review or explicitly regenerate.\n"
                "manifest.json records relative paths, SHA-256 hashes, and missing source files.\n"
                "Contact sheets are paginated at 40 images; originals remain in images/.\n"
                "A retained request is reproducible input, not a guarantee of identical generated pixels.\n", encoding="utf-8")
            os.replace(stage, destination)
            return {"path": str(destination), "exported": len(previews), "missing": missing,
                    "manifest_path": str(destination / "manifest.json"),
                    "contact_sheet": str(destination / sheets[0].name) if sheets else None}
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def _portable_metadata(value, paths: dict, key: str = ""):
    if isinstance(value, dict):
        return {k: _portable_metadata(v, paths, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable_metadata(v, paths, key) for v in value]
    if isinstance(value, str):
        if value in paths:
            return paths[value]
        if (key.endswith("_path") or key in {"source", "path", "output_file"}) and Path(value).is_absolute():
            return Path(value).name
    return value


def _contact_sheets(previews: list, folder: Path) -> list[Path]:
    sheets = []
    for offset in range(0, len(previews), 40):
        page = previews[offset:offset + 40]
        cell_w, cell_h, columns = 280, 330, 4
        canvas = Image.new("RGB", (columns * cell_w, math.ceil(len(page) / columns) * cell_h), "#f5f5f3")
        draw = ImageDraw.Draw(canvas)
        for i, (path, label, prompt) in enumerate(page):
            x, y = (i % columns) * cell_w, (i // columns) * cell_h
            try:
                with Image.open(path) as original:
                    original = ImageOps.exif_transpose(original).convert("RGBA")
                    original.thumbnail((256, 248))
                    canvas.paste(original, (x + (cell_w - original.width) // 2, y + 8), original)
            except (OSError, ValueError):
                draw.text((x + 12, y + 100), "Preview unavailable", fill="#a33")
            draw.text((x + 12, y + 262), label, fill="#111")
            lines = textwrap.wrap(prompt.replace("\n", " "), width=35)[:3]
            for line_no, line in enumerate(lines):
                draw.text((x + 12, y + 280 + line_no * 14), line, fill="#444")
        path = folder / ("contact-sheet.png" if offset == 0 else f"contact-sheet-{offset // 40 + 1:03d}.png")
        canvas.save(path, "PNG")
        sheets.append(path)
    return sheets


def _find_generation_log(rows: List[RowData]) -> Path | None:
    for row in rows:
        for image in row.images:
            for parent in list(Path(image.file_path).parents)[:6]:
                candidate = parent / LOG_FILENAME
                if candidate.is_file():
                    return candidate
    return None
