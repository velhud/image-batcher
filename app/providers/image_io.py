"""Verified raster IO shared by providers. Original bytes are never re-encoded."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from app.models import DEFAULT_SIZE_PRESETS, GeneratedImage, GlobalSettings
from .base import ProviderGenerationError


MIME_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
# Inline JSON is deliberately bounded. Larger uploads need a separate Files API flow.
MAX_INLINE_BYTES = 12 * 1024 * 1024


def dimensions(settings: GlobalSettings) -> tuple[int, int]:
    values = settings.custom_size or DEFAULT_SIZE_PRESETS.get(settings.size_preset)
    if not values or any(type(values.get(key)) is not int or values[key] <= 0 for key in ("width", "height")):
        raise ProviderGenerationError("Choose a valid size preset or positive integer width and height.", status="invalid_settings")
    return values["width"], values["height"]


def gpt_size(settings: GlobalSettings) -> str:
    width, height = dimensions(settings)
    if (width % 16 or height % 16 or max(width, height) > 3840
            or max(width, height) > 3 * min(width, height)
            or not 655360 <= width * height <= 8294400):
        raise ProviderGenerationError(
            "GPT Image size requires dimensions divisible by 16, edges up to 3840, "
            "aspect ratio between 1:3 and 3:1, and 655,360–8,294,400 pixels.",
            status="invalid_settings",
        )
    return f"{width}x{height}"


def quality_name(value: int | None) -> str:
    value = 5 if value is None else value
    return "low" if value <= 3 else "high" if value >= 8 else "medium"


def inspect_image(raw: bytes) -> tuple[str, int, int]:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt = (image.format or "").lower()
            width, height = image.size
            image.verify()
        if fmt not in MIME_TYPES:
            raise ValueError(f"unsupported raster format {fmt}")
        return fmt, width, height
    except Exception as exc:
        raise ProviderGenerationError("Image bytes are not a valid PNG, JPEG, or WebP image.", status="invalid_image") from exc


def read_attachments(attachments: Iterable[Any]) -> list[dict[str, Any]]:
    results = []
    total = 0
    for attachment in attachments:
        path = Path(getattr(attachment, "file_path", ""))
        try:
            # Read a bounded amount even if the file grows after stat.
            with path.open("rb") as source:
                raw = source.read(MAX_INLINE_BYTES + 1)
        except OSError as exc:
            raise ProviderGenerationError(f"Cannot read reference image '{path.name}': {exc.strerror}.", status="invalid_settings") from exc
        total += len(raw)
        if total > MAX_INLINE_BYTES:
            raise ProviderGenerationError("Reference images exceed the app's 12 MiB total inline-upload limit.", status="invalid_settings")
        fmt, width, height = inspect_image(raw)
        results.append({
            "name": path.name, "mime_type": MIME_TYPES[fmt], "data": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest(), "width": width, "height": height,
        })
    return results


def safe_token(value: str, fallback: str = "image") -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", value or fallback).strip("-")[:120] or fallback


def save_base64_image(encoded: str, output_dir: Path, stem: str, metadata: dict[str, Any]) -> GeneratedImage:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProviderGenerationError("Provider returned invalid base64 image data.", status="invalid_image", metadata=metadata) from exc
    try:
        fmt, width, height = inspect_image(raw)
    except ProviderGenerationError as exc:
        exc.metadata = {**metadata, **exc.metadata}
        raise
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "jpg" if fmt == "jpeg" else fmt
    stem = safe_token(stem)
    fd, temporary_name = tempfile.mkstemp(prefix=".imageai-", dir=output_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        collision = 0
        while True:
            path = output_dir / f"{stem}{f'_{collision:02d}' if collision else ''}.{suffix}"
            try:
                # Atomic exclusive publication preserves any older result, even when
                # two workers happen to choose the same deterministic filename.
                os.link(temporary, path)
                break
            except FileExistsError:
                collision += 1
    finally:
        temporary.unlink(missing_ok=True)
    return GeneratedImage(path, {
        **metadata, "output_format": fmt, "mime_type": MIME_TYPES[fmt],
        "width": width, "height": height, "sha256": hashlib.sha256(raw).hexdigest(),
    })
