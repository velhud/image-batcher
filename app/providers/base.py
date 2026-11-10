from __future__ import annotations

import abc
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional

from app.models import GeneratedImage, GenerationContext, GlobalSettings, ModelInfo


class ProviderGenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str = "failed",
        metadata: Optional[Dict[str, Any]] = None,
        partial_images: Optional[List[GeneratedImage]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.metadata = metadata or {}
        # A later request can fail after earlier images have already been saved.
        self.partial_images = list(partial_images or [])


class ProviderFilteredError(ProviderGenerationError):
    def __init__(self, message: str, *, metadata: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, status="filtered", metadata=metadata)


class ProviderRetryExhaustedError(ProviderGenerationError):
    def __init__(self, message: str, *, metadata: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, status="retry_exhausted", metadata=metadata)


class ProviderCancelledError(ProviderGenerationError):
    def __init__(self, message: str = "Cancelled before the next provider request.") -> None:
        super().__init__(message, status="cancelled")


class BaseProvider(abc.ABC):
    id: str = "base"
    name: str = "Base Provider"
    max_prompt_characters: int | None = None

    def __init__(self, models: List[ModelInfo], rate_limit_note: str = "") -> None:
        self.models = models
        self.rate_limit_note = rate_limit_note

    @abc.abstractmethod
    def generate_images(
        self,
        prompt: str,
        settings: GlobalSettings,
        output_dir: Path,
        cancel_event: Event,
        attachments: Optional[List[object]] = None,
        context: GenerationContext | None = None,
    ) -> List[GeneratedImage]:
        """Generate images for a prompt. Should respect cancel_event when possible."""

    def quota_cost(self, settings: GlobalSettings) -> int:
        return 1

    def request_count(self, settings: GlobalSettings) -> int:
        """Initial HTTP calls per row, excluding explicit rate-limit retries."""
        return 1

    def validate_prompt(self, prompt: str) -> None:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ProviderGenerationError("Prompt must contain text.", status="invalid_settings")
        if self.max_prompt_characters is not None and len(prompt) > self.max_prompt_characters:
            raise ProviderGenerationError(
                f"{self.name} prompts, including shared instructions, cannot exceed {self.max_prompt_characters:,} characters.",
                status="invalid_settings",
            )

    def validate_settings(self, settings: GlobalSettings, attachments: Optional[List[object]] = None) -> ModelInfo | None:
        """Reject unsupported choices before a paid request; never change models silently."""
        if not self.models:  # Small third-party/test providers may have no catalog.
            return None
        model_id = settings.model_id or self.models[0].id
        model = next((item for item in self.models if item.id == model_id), None)
        if model is None:
            raise ProviderGenerationError(
                f"Model '{model_id}' is not supported by {self.name}. Select a current model in generation settings.",
                status="invalid_settings",
            )
        caps = model.capabilities
        if type(settings.num_images) is not int or not 1 <= settings.num_images <= caps.max_images:
            raise ProviderGenerationError(f"{model.name} supports 1–{caps.max_images} images per row.", status="invalid_settings")
        if settings.style_preset not in (None, "", "None") and not caps.supports_style:
            raise ProviderGenerationError("This model has no style control; describe the style in the prompt.", status="invalid_settings")
        if settings.negative_prompt and not caps.supports_negative_prompt:
            raise ProviderGenerationError("This model has no negative-prompt control; put the constraints in the prompt.", status="invalid_settings")
        if not settings.random_seed and settings.seed is not None and not caps.supports_seed:
            raise ProviderGenerationError("This model does not support a fixed seed.", status="invalid_settings")
        if settings.safety != 2 and not caps.supports_safety:
            raise ProviderGenerationError("This model does not expose the app's numeric safety control.", status="invalid_settings")
        if caps.supports_quality and (type(settings.quality) is not int or not 1 <= settings.quality <= 10):
            raise ProviderGenerationError("Quality must be an integer from 1 to 10.", status="invalid_settings")
        output_format = getattr(settings, "output_format", "png")
        if output_format not in caps.output_formats:
            raise ProviderGenerationError(f"{model.name} supports these output formats: {', '.join(caps.output_formats)}.", status="invalid_settings")
        background = getattr(settings, "background", "auto")
        if background not in {"auto", "opaque", "transparent"}:
            raise ProviderGenerationError("Background must be auto, opaque, or transparent.", status="invalid_settings")
        if background != "auto" and not caps.supports_background:
            raise ProviderGenerationError("This model does not expose background selection.", status="invalid_settings")
        if background == "transparent" and output_format == "jpeg":
            raise ProviderGenerationError("Transparent backgrounds require PNG or WebP output.", status="invalid_settings")
        if settings.custom_size and not caps.supports_custom_size:
            raise ProviderGenerationError("This model requires one of its size presets.", status="invalid_settings")
        refs = attachments or []
        if refs and (not caps.supports_attachments or len(refs) > caps.max_attachments):
            raise ProviderGenerationError(f"{model.name} accepts up to {caps.max_attachments} reference images.", status="invalid_settings")
        if refs:
            from .image_io import read_attachments

            read_attachments(refs)
        return model

    def setup(self) -> None:
        """Optional heavy initialization."""
        return

    def teardown(self) -> None:
        return

    def available_models(self) -> List[ModelInfo]:
        return self.models
