from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Any

from app.models import GeneratedImage, GenerationContext, GlobalSettings, ModelInfo, ProviderCapabilities
from .base import BaseProvider, ProviderGenerationError
from .http_transport import post_json
from .image_io import gpt_size, quality_name, read_attachments, save_base64_image


# Bounded raster catalog verified against the API reference on 2026-11-10.
OPENAI_MODELS = (
    ("gpt-image-2", "GPT Image 2"),
    ("gpt-image-2-2026-04-21", "GPT Image 2 (2026-04-21)"),
    ("gpt-image-2.5-flare", "GPT Image 2.5 Flare"),
    ("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst"),
    ("gpt-image-2.5-flare-2026-09-08", "GPT Image 2.5 Flare (2026-09-08)"),
    ("gpt-image-2.5-sunburst-2026-09-08", "GPT Image 2.5 Sunburst (2026-09-08)"),
)


class OpenAIProvider(BaseProvider):
    id = "openai"
    name = "OpenAI"
    max_prompt_characters = 32000

    def __init__(self) -> None:
        models = [ModelInfo(model_id, name, self.id, ProviderCapabilities(
            max_images=10, supports_style=False, supports_negative_prompt=False,
            supports_seed=False, supports_quality=True, supports_safety=False,
            supports_attachments=True, max_attachments=16, supports_custom_size=True,
            output_formats=["png", "jpeg", "webp"], supports_background=True,
            quality_options=["low", "medium", "high"],
        )) for model_id, name in OPENAI_MODELS]
        super().__init__(models, rate_limit_note=(
            "Requests/minute; actual quota depends on your account. GPT Image 2 transparency is a provider preview. "
            "Reference images use the image-edit endpoint; inline inputs total at most 12 MiB."
        ))

    def validate_settings(self, settings: GlobalSettings, attachments: list[object] | None = None) -> ModelInfo | None:
        model = super().validate_settings(settings, attachments)
        gpt_size(settings)
        return model

    def build_payload(self, prompt: str, settings: GlobalSettings) -> dict[str, Any]:
        if not prompt.strip() or len(prompt) > 32000:
            raise ProviderGenerationError("GPT Image prompts must contain 1–32,000 characters.", status="invalid_settings")
        return {
            "model": settings.model_id or OPENAI_MODELS[0][0],
            "prompt": prompt, "size": gpt_size(settings), "n": settings.num_images,
            "quality": quality_name(settings.quality),
            "output_format": settings.output_format, "background": settings.background,
        }

    def generate_images(self, prompt: str, settings: GlobalSettings, output_dir: Path,
                        cancel_event: Event, attachments: list[object] | None = None,
                        context: GenerationContext | None = None) -> list[GeneratedImage]:
        self.validate_settings(settings, attachments)
        payload = self.build_payload(prompt, settings)
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ProviderGenerationError("Missing OPENAI_API_KEY for OpenAI provider.", status="invalid_settings")
        references = read_attachments(attachments or [])
        operation = "edits" if references else "generations"
        if references:
            payload["images"] = [{"image_url": f"data:{ref['mime_type']};base64,{ref['data']}"} for ref in references]
        response, request_metadata = post_json(
            f"https://api.openai.com/v1/images/{operation}",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload, cancel_event, provider="OpenAI",
        )
        data = response.get("data")
        if not isinstance(data, list) or not data:
            raise ProviderGenerationError("OpenAI returned no image data.", metadata=request_metadata)
        metadata = {
            **request_metadata, "provider": self.id, "model": payload["model"],
            "size": response.get("size") or payload["size"], "requested_size": payload["size"],
            "quality": response.get("quality") or payload["quality"], "n": settings.num_images,
            "background": response.get("background") or payload["background"],
            "requested_output_format": settings.output_format, "operation": operation,
            "usage": response.get("usage") or {},
            "references": [{key: value for key, value in ref.items() if key != "data"} for ref in references],
        }
        results: list[GeneratedImage] = []
        try:
            for index, item in enumerate(data):
                if not isinstance(item, dict) or not item.get("b64_json"):
                    raise ProviderGenerationError("OpenAI returned an image entry without base64 data.", metadata=request_metadata)
                run = (context.run_index if context else 1) + index
                results.append(save_base64_image(item["b64_json"], output_dir, f"openai_run{run:02d}",
                                                 {**metadata, "run_index": run}))
            if len(results) != settings.num_images:
                raise ProviderGenerationError(
                    f"OpenAI returned {len(results)} of {settings.num_images} requested images; saved results were retained.",
                    metadata=request_metadata,
                )
        except ProviderGenerationError as exc:
            exc.partial_images = results
            raise
        except OSError as exc:
            raise ProviderGenerationError("Could not save an OpenAI result.", status="write_failed", partial_images=results) from exc
        return results
