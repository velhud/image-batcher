from __future__ import annotations

import dataclasses
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional


class RowStatus(str, Enum):
    IDLE = "Idle"
    QUEUED = "Queued"
    GENERATING = "Generating"
    COMPLETED = "Completed"
    ERROR = "Error"
    FILTERED = "Filtered"
    CANCELLED = "Cancelled"
    INTERRUPTED = "Interrupted"


SizePreset = Dict[str, int]


DEFAULT_SIZE_PRESETS: Dict[str, SizePreset] = {
    "Square 1024": {"width": 1024, "height": 1024},
    "Portrait": {"width": 1024, "height": 1536},
    "Landscape": {"width": 1536, "height": 1024},
}


@dataclass
class InputAttachment:
    id: str
    file_path: str
    mime_type: str = "image/png"

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "InputAttachment":
        mime_type = data.get("mime_type") or mimetypes.guess_type(data.get("file_path", ""))[0] or "image/png"
        return InputAttachment(
            id=data.get("id", str(uuid.uuid4())),
            file_path=data.get("file_path", ""),
            mime_type=mime_type,
        )


@dataclass
class ProviderCapabilities:
    max_images: int = 4
    supports_style: bool = True
    supports_negative_prompt: bool = True
    supports_seed: bool = True
    supports_quality: bool = True
    supports_safety: bool = True
    supports_attachments: bool = False
    max_attachments: int = 0
    supports_custom_size: bool = True
    output_formats: List[str] = field(default_factory=lambda: ["png"])
    supports_background: bool = False
    quality_options: List[str] = field(default_factory=list)
    size_presets: Dict[str, SizePreset] = field(
        default_factory=lambda: dict(DEFAULT_SIZE_PRESETS)
    )


@dataclass
class ModelInfo:
    id: str
    name: str
    provider_id: str
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "provider_id": self.provider_id,
            "capabilities": dataclasses.asdict(self.capabilities),
        }

    @staticmethod
    def from_dict(data: Dict) -> "ModelInfo":
        return ModelInfo(
            id=data["id"],
            name=data["name"],
            provider_id=data["provider_id"],
            capabilities=ProviderCapabilities(**data.get("capabilities", {})),
        )


@dataclass
class ProviderInfo:
    id: str
    name: str
    models: List[ModelInfo]
    rate_limit_note: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "rate_limit_note": self.rate_limit_note,
            "models": [m.to_dict() for m in self.models],
        }

    @staticmethod
    def from_dict(data: Dict) -> "ProviderInfo":
        return ProviderInfo(
            id=data["id"],
            name=data["name"],
            rate_limit_note=data.get("rate_limit_note", ""),
            models=[ModelInfo.from_dict(m) for m in data.get("models", [])],
        )


@dataclass
class PromptTemplate:
    name: str
    template: str
    variables: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "template": self.template,
            "variables": self.variables,
        }

    @staticmethod
    def from_dict(data: Dict) -> "PromptTemplate":
        return PromptTemplate(
            name=data.get("name", "Template"),
            template=data.get("template", ""),
            variables=data.get("variables", {}),
        )


@dataclass
class StatsSnapshot:
    total: int = 0
    completed: int = 0
    errors: int = 0
    average_duration: float = 0.0
    per_provider: Dict[str, int] = field(default_factory=dict)
    per_model: Dict[str, int] = field(default_factory=dict)
    per_style: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "StatsSnapshot":
        return StatsSnapshot(
            total=data.get("total", 0),
            completed=data.get("completed", 0),
            errors=data.get("errors", 0),
            average_duration=data.get("average_duration", 0.0),
            per_provider=data.get("per_provider", {}),
            per_model=data.get("per_model", {}),
            per_style=data.get("per_style", {}),
        )


@dataclass
class GlobalSettings:
    provider_id: str = "mock"
    model_id: str = "mock-standard"
    size_preset: str = "Square 1024"
    custom_size: Optional[SizePreset] = None
    num_images: int = 1
    style_preset: str = "None"
    negative_prompt: str = ""
    seed: Optional[int] = None
    random_seed: bool = True
    quality: int = 5  # 1-10
    safety: int = 2  # 0-3
    export_folder: Optional[str] = None
    naming_pattern: str = "{index}_{slug}.png"
    concurrency_limit: int = 2
    generate_behavior: str = "keep"  # keep or replace previous images
    regen_use_same_seed: bool = True
    confirm_generate_threshold: int = 300
    rate_limit_rpm: int = 60
    thinking_budget: int = 0  # For Nano Banana (2.5), 0=off, -1=dynamic
    thinking_level: str = "high"  # For Nano Banana Pro (3.0), "low" or "high"
    prompt_wrapper: str = ""
    output_format: str = "png"
    background: str = "auto"

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "GlobalSettings":
        # Older sessions include appearance preferences from the retired client.
        current = dict(data)
        current.pop("theme", None)
        current.pop("prompt_highlighting", None)
        return GlobalSettings(**current)


@dataclass
class RowSettings:
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    size_preset: Optional[str] = None
    custom_size: Optional[SizePreset] = None
    num_images: Optional[int] = None
    style_preset: Optional[str] = None
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    random_seed: Optional[bool] = None
    quality: Optional[int] = None
    safety: Optional[int] = None
    keep_images: Optional[bool] = None
    generate_behavior: Optional[str] = None
    regen_use_same_seed: Optional[bool] = None
    thinking_budget: Optional[int] = None
    thinking_level: Optional[str] = None
    output_format: Optional[str] = None
    background: Optional[str] = None

    def to_dict(self) -> Dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "RowSettings":
        return RowSettings(**data)

    def is_override(self, field_name: str) -> bool:
        return getattr(self, field_name) is not None


@dataclass
class ImageResult:
    id: str
    row_id: str
    file_path: str
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "row_id": self.row_id,
            "file_path": self.file_path,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(data: Dict) -> "ImageResult":
        return ImageResult(
            id=data["id"],
            row_id=data["row_id"],
            file_path=data["file_path"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class GeneratedImage:
    file_path: Path
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationContext:
    row_id: str
    row_index: int
    prompt_id: str
    category_id: str
    original_prompt: str
    full_prompt: str
    run_index: int = 1
    log_path: Optional[str] = None

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "row_id": self.row_id,
            "row_index": self.row_index,
            "prompt_id": self.prompt_id,
            "category_id": self.category_id,
            "original_prompt": self.original_prompt,
            "full_prompt": self.full_prompt,
            "run_index": self.run_index,
        }


@dataclass
class RowData:
    id: str
    prompt: str = ""
    prompt_id: str = ""
    category_id: str = ""
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    status: RowStatus = RowStatus.IDLE
    error_message: str = ""
    selected: bool = False
    settings: RowSettings = field(default_factory=RowSettings)
    attachments: List[InputAttachment] = field(default_factory=list)
    images: List[ImageResult] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    last_duration: Optional[float] = None
    applied_job_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "prompt_id": self.prompt_id,
            "category_id": self.category_id,
            "source_metadata": self.source_metadata,
            "status": self.status.value,
            "error_message": self.error_message,
            "selected": self.selected,
            "settings": self.settings.to_dict(),
            "attachments": [a.to_dict() for a in self.attachments],
            "images": [img.to_dict() for img in self.images],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "last_duration": self.last_duration,
            "applied_job_ids": list(self.applied_job_ids),
        }

    @staticmethod
    def from_dict(data: Dict) -> "RowData":
        return RowData(
            id=data["id"],
            prompt=data.get("prompt", ""),
            prompt_id=data.get("prompt_id", ""),
            category_id=data.get("category_id", ""),
            source_metadata=data.get("source_metadata", {}),
            status=RowStatus(data.get("status", RowStatus.IDLE.value)),
            error_message=data.get("error_message", ""),
            selected=data.get("selected", False),
            settings=RowSettings.from_dict(data.get("settings", {})),
            attachments=[InputAttachment.from_dict(a) for a in data.get("attachments", [])],
            images=[ImageResult.from_dict(img) for img in data.get("images", [])],
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            tags=data.get("tags", []),
            last_duration=data.get("last_duration"),
            applied_job_ids=list(data.get("applied_job_ids", [])),
        )


@dataclass
class SessionData:
    id: str
    global_settings: GlobalSettings
    rows: List[RowData]
    version: str = "1.0"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    templates: List[PromptTemplate] = field(default_factory=list)
    stats: StatsSnapshot = field(default_factory=StatsSnapshot)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "version": self.version,
            "global_settings": self.global_settings.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "templates": [tpl.to_dict() for tpl in self.templates],
            "stats": self.stats.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @staticmethod
    def from_dict(data: Dict) -> "SessionData":
        return SessionData(
            id=data.get("id", str(uuid.uuid4())),
            global_settings=GlobalSettings.from_dict(data.get("global_settings", {})),
            rows=[RowData.from_dict(row) for row in data.get("rows", [])],
            version=data.get("version", "1.0"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            templates=[PromptTemplate.from_dict(t) for t in data.get("templates", [])],
            stats=StatsSnapshot.from_dict(data.get("stats", {})),
        )


def effective_settings(row: RowData, global_settings: GlobalSettings) -> GlobalSettings:
    merged = dataclasses.asdict(global_settings)
    row_settings = dataclasses.asdict(row.settings)
    # Map keep_images into generate_behavior if provided.
    if row_settings.get("keep_images") is not None:
        merged["generate_behavior"] = "keep" if row_settings["keep_images"] else "replace"
    if row_settings.get("generate_behavior"):
        merged["generate_behavior"] = row_settings["generate_behavior"]
    for key, value in row_settings.items():
        if key in merged and value is not None:
            merged[key] = value
    if row.settings.size_preset and row.settings.size_preset != "Custom" and row.settings.custom_size is None:
        merged["custom_size"] = None
    # Map merged dict back into GlobalSettings-like object for consumption.
    return GlobalSettings(**merged)


def session_storage_dir() -> Path:
    base = Path(os.environ.get("IMAGEAI_DATA_DIR", str(Path.home() / ".imagen"))).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_session_path() -> Path:
    return session_storage_dir() / "last_session.json"


def generation_prompt(prompt: str, settings: GlobalSettings) -> str:
    """Use the same resolved prompt for preflight and the provider request."""
    text = prompt.strip()
    wrapper = (settings.prompt_wrapper or "").strip()
    return f"{text}\n\n{wrapper}" if text and wrapper else text or wrapper


def validate_settings(settings: GlobalSettings) -> None:
    """Validate shared settings before publishing a mutation or admitting work."""
    for name, low, high in (
        ("num_images", 1, 10), ("concurrency_limit", 1, 16),
        ("rate_limit_rpm", 1, 6000), ("quality", 1, 10), ("safety", 0, 3),
        ("confirm_generate_threshold", 1, 100000),
    ):
        value = getattr(settings, name)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer from {low} to {high}")
    for name in ("provider_id", "model_id", "size_preset", "style_preset", "negative_prompt", "prompt_wrapper", "naming_pattern"):
        if not isinstance(getattr(settings, name), str):
            raise ValueError(f"{name} must be text")
    for name in ("provider_id", "model_id", "naming_pattern"):
        if not getattr(settings, name).strip():
            raise ValueError(f"{name} cannot be empty")
    for name in ("random_seed", "regen_use_same_seed"):
        if not isinstance(getattr(settings, name), bool):
            raise ValueError(f"{name} must be true or false")
    if settings.generate_behavior not in {"keep", "replace"}:
        raise ValueError("generate_behavior must be keep or replace")
    if settings.output_format not in {"png", "jpeg", "webp"}:
        raise ValueError("output_format must be png, jpeg, or webp")
    if settings.background not in {"auto", "opaque", "transparent"}:
        raise ValueError("background must be auto, opaque, or transparent")
    if settings.seed is not None and (isinstance(settings.seed, bool) or not isinstance(settings.seed, int)):
        raise ValueError("seed must be an integer or null")
    if settings.custom_size is not None:
        if not isinstance(settings.custom_size, dict) or set(settings.custom_size) != {"width", "height"}:
            raise ValueError("custom_size must contain width and height")
        for dimension in settings.custom_size.values():
            if isinstance(dimension, bool) or not isinstance(dimension, int) or not 64 <= dimension <= 8192:
                raise ValueError("Custom width and height must be integers from 64 to 8192")
    if settings.size_preset == "Custom" and settings.custom_size is None:
        raise ValueError("Custom image size requires width and height")


def validate_session(session: SessionData) -> None:
    """Session IDs become directory names; reject ambiguous or unsafe identities."""
    def valid_id(value: str) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value))
    if not valid_id(session.id):
        raise ValueError("Invalid session ID")
    if session.version not in {"1.0", "1.1"}:
        raise ValueError(f"Unsupported session version: {session.version}")
    validate_settings(session.global_settings)
    seen = set()
    for row in session.rows:
        if not valid_id(row.id) or row.id in seen:
            raise ValueError("Row IDs must be unique letters, digits, hyphens, or underscores")
        seen.add(row.id)
        if not isinstance(row.prompt, str):
            raise ValueError("Row prompts must be text")
        if not isinstance(row.source_metadata, dict) or not isinstance(row.selected, bool):
            raise ValueError("Rows require object metadata and a boolean selection")
        if not isinstance(row.tags, list) or any(not isinstance(tag, str) for tag in row.tags):
            raise ValueError("Row tags must be an array of text values")
        if not isinstance(row.applied_job_ids, list) or any(not isinstance(job_id, str) for job_id in row.applied_job_ids):
            raise ValueError("Applied job IDs must be an array of strings")
        for item in [*row.images, *row.attachments]:
            if not isinstance(item.file_path, str) or not isinstance(item.id, str):
                raise ValueError("Image and attachment identities and paths must be text")
        if any(not isinstance(image.metadata, dict) for image in row.images):
            raise ValueError("Image metadata must be an object")
        validate_settings(effective_settings(row, session.global_settings))
