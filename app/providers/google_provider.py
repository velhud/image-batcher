from __future__ import annotations

import json
import os
from math import gcd
from pathlib import Path
from threading import Event
from typing import Any

from app.models import GeneratedImage, GenerationContext, GlobalSettings, ModelInfo, ProviderCapabilities
from .base import BaseProvider, ProviderCancelledError, ProviderFilteredError, ProviderGenerationError
from .http_transport import post_json
from .image_io import dimensions, read_attachments, save_base64_image


NANO_MODEL_ID = "gemini-3.1-flash-image"
PRO_MODEL_ID = "gemini-3-pro-image"
COMMON_ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "7:3"}
FLASH_ASPECT_RATIOS = COMMON_ASPECT_RATIOS | {"1:4", "4:1", "1:8", "8:1"}


class GoogleGeminiProvider(BaseProvider):
    id = "google"
    name = "Google Gemini"

    def __init__(self) -> None:
        models = [ModelInfo(model_id, name, self.id, ProviderCapabilities(
            max_images=4, supports_style=False, supports_negative_prompt=True,
            supports_seed=False, supports_quality=False, supports_safety=False,
            supports_attachments=True, max_attachments=14, supports_custom_size=True,
            output_formats=["png"], supports_background=False,
        )) for model_id, name in (
            (NANO_MODEL_ID, "Gemini 3.1 Flash Image"), (PRO_MODEL_ID, "Gemini 3 Pro Image"),
        )]
        super().__init__(models, rate_limit_note=(
            "One request per requested image. Inline references total at most 12 MiB. "
            "Custom dimensions select a supported aspect ratio and resolution tier; the provider controls exact pixels and raster format."
        ))

    def request_count(self, settings: GlobalSettings) -> int:
        return settings.num_images

    def quota_cost(self, settings: GlobalSettings) -> int:
        return settings.num_images

    def validate_settings(self, settings: GlobalSettings, attachments: list[object] | None = None) -> ModelInfo | None:
        model = super().validate_settings(settings, attachments)
        model_id = model.id if model else NANO_MODEL_ID
        aspect = self._aspect_ratio_from_settings(settings)
        ratios = FLASH_ASPECT_RATIOS if model_id == NANO_MODEL_ID else COMMON_ASPECT_RATIOS
        # 21:9 is sent in its documented spelling instead of reduced 7:3.
        if aspect not in {"21:9" if value == "7:3" else value for value in ratios}:
            raise ProviderGenerationError("Gemini requires a supported aspect ratio, for example 1:1, 2:3, 3:2, 16:9, or 21:9.", status="invalid_settings")
        width, height = dimensions(settings)
        if max(width, height) > 16384:
            raise ProviderGenerationError("Gemini dimensions exceed the app's maximum of 16,384 pixels per edge.", status="invalid_settings")
        if settings.thinking_budget not in {None, 0}:
            raise ProviderGenerationError("Current Gemini image models do not use the legacy thinking budget; reset it to 0.", status="invalid_settings")
        if model_id == NANO_MODEL_ID and settings.thinking_level not in {"minimal", "high", ""}:
            raise ProviderGenerationError("Gemini 3.1 Flash Image thinking level must be minimal or high.", status="invalid_settings")
        return model

    def generate_images(self, prompt: str, settings: GlobalSettings, output_dir: Path,
                        cancel_event: Event, attachments: list[object] | None = None,
                        context: GenerationContext | None = None) -> list[GeneratedImage]:
        self.validate_settings(settings, attachments)
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ProviderGenerationError("Missing GEMINI_API_KEY for Google Gemini.", status="invalid_settings")
        model_id = settings.model_id or NANO_MODEL_ID
        # Prepare once so every request in this row uses the same reference bytes.
        references = read_attachments(attachments or [])
        body = self._build_request_body(prompt, settings, model_id, references=references)
        if len(json.dumps(body).encode("utf-8")) >= 20_000_000:
            raise ProviderGenerationError("Gemini inline request exceeds 20 MB; use fewer or smaller reference images.", status="invalid_settings")
        paths: list[GeneratedImage] = []
        try:
            for index in range(settings.num_images):
                if cancel_event.is_set():
                    if paths:
                        return paths
                    raise ProviderCancelledError()
                response, request_metadata = post_json(
                    self._endpoint_for_model(model_id),
                    {"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    body, cancel_event, provider="Gemini",
                )
                images = self._extract_inline_images(response)
                if not images:
                    reason = self._filter_reason(response)
                    if reason:
                        raise ProviderFilteredError(f"Gemini blocked this request: {reason}.", metadata=request_metadata)
                    raise ProviderGenerationError("Gemini returned no final image data.", metadata=request_metadata)
                for part_index, (encoded, mime_type) in enumerate(images):
                    run = (context.run_index if context else 1) + len(paths)
                    paths.append(save_base64_image(encoded, output_dir, f"gemini_run{run:02d}", {
                        **request_metadata, "provider": self.id, "model": model_id,
                        "n": settings.num_images, "request_index": index + 1, "part_index": part_index,
                        "run_index": run, "provider_mime_type": mime_type,
                        "requested_size": f"{dimensions(settings)[0]}x{dimensions(settings)[1]}",
                        "aspect_ratio": self._aspect_ratio_from_settings(settings),
                        "resolution_tier": self._image_size_from_settings(settings, model_id),
                        "usage": response.get("usageMetadata") or {},
                        "references": [{key: value for key, value in ref.items() if key != "data"} for ref in references],
                    }))
                # A successful paid response is saved even if cancel arrived in-flight.
        except ProviderGenerationError as exc:
            exc.partial_images = paths
            raise
        except OSError as exc:
            raise ProviderGenerationError("Could not save a Gemini result.", status="write_failed", partial_images=paths) from exc
        return paths

    def _build_request_body(self, prompt: str, settings: GlobalSettings, model_id: str,
                            attachments: list[Any] | None = None,
                            *, references: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not prompt.strip():
            raise ProviderGenerationError("Enter a prompt before generating.", status="invalid_settings")
        references = read_attachments(attachments or []) if references is None else references
        parts: list[dict[str, Any]] = [{"inlineData": {"mimeType": ref["mime_type"], "data": ref["data"]}} for ref in references]
        parts.append({"text": prompt})
        if settings.negative_prompt:
            parts.append({"text": f"Avoid these elements: {settings.negative_prompt}"})
        config: dict[str, Any] = {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": self._aspect_ratio_from_settings(settings),
                            "imageSize": self._image_size_from_settings(settings, model_id)},
        }
        if model_id == NANO_MODEL_ID and settings.thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": settings.thinking_level.upper()}
        body: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}
        # Search grounding requires a complete source-attribution UI. It is not
        # silently enabled through an environment flag in the raster-only client.
        return body

    @staticmethod
    def _filter_reason(response: dict[str, Any]) -> str:
        reason = response.get("promptFeedback", {}).get("blockReason")
        if reason:
            return str(reason)
        for candidate in response.get("candidates") or []:
            reason = candidate.get("finishReason", "")
            if reason in {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"}:
                return reason
        return ""

    @staticmethod
    def _extract_inline_images(response: dict[str, Any]) -> list[tuple[str, str]]:
        images = []
        for candidate in response.get("candidates") or []:
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("thought"):
                    continue
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    images.append((inline["data"], inline.get("mimeType") or inline.get("mime_type") or "image/png"))
        return images

    @staticmethod
    def _endpoint_for_model(model_id: str) -> str:
        return f"https://generativelanguage.googleapis.com/v1/models/{model_id}:generateContent"

    @staticmethod
    def _aspect_ratio_from_settings(settings: GlobalSettings) -> str:
        width, height = dimensions(settings)
        divisor = gcd(width, height)
        ratio = f"{width // divisor}:{height // divisor}"
        return "21:9" if ratio == "7:3" else ratio

    @staticmethod
    def _image_size_from_settings(settings: GlobalSettings, model_id: str = NANO_MODEL_ID) -> str:
        longest = max(dimensions(settings))
        if longest <= 512 and model_id == NANO_MODEL_ID:
            return "512"
        return "4K" if longest >= 3500 else "2K" if longest >= 2000 else "1K"

    _resolve_dimensions = staticmethod(dimensions)
