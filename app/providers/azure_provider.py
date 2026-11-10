from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlencode, urlparse

from app.models import GeneratedImage, GenerationContext, GlobalSettings, ModelInfo, ProviderCapabilities
from .base import BaseProvider, ProviderGenerationError
from .http_transport import post_json
from .image_io import gpt_size, quality_name, safe_token, save_base64_image


AZURE_PROVIDER_ID = "azure-openai"
AZURE_GPT_IMAGE_2_MODEL_ID = "gpt-image-2"


class AzureOpenAIImageProvider(BaseProvider):
    id = AZURE_PROVIDER_ID
    name = "Azure OpenAI"
    max_prompt_characters = 32000

    def __init__(self) -> None:
        capabilities = ProviderCapabilities(
            max_images=10, supports_style=False, supports_negative_prompt=False,
            supports_seed=False, supports_quality=True, supports_safety=False,
            supports_attachments=False, max_attachments=0, supports_custom_size=True,
            output_formats=["png", "jpeg"], supports_background=False,
            quality_options=["low", "medium", "high"],
        )
        super().__init__([ModelInfo(AZURE_GPT_IMAGE_2_MODEL_ID, "GPT Image 2 (Azure deployment)", self.id, capabilities)],
                         rate_limit_note="Conservative image budget: start at 4 images/minute, concurrency 1; check your deployment quota.")

    def quota_cost(self, settings: GlobalSettings) -> int:
        return settings.num_images

    def validate_settings(self, settings: GlobalSettings, attachments: list[object] | None = None) -> ModelInfo | None:
        model = super().validate_settings(settings, attachments)
        gpt_size(settings)
        return model

    def generate_images(self, prompt: str, settings: GlobalSettings, output_dir: Path,
                        cancel_event: Event, attachments: list[object] | None = None,
                        context: GenerationContext | None = None) -> list[GeneratedImage]:
        self.validate_settings(settings, attachments)
        config = self._load_config()
        payload = self.build_payload(prompt, settings, config["deployment"])
        response, metadata = post_json(
            self.build_url(config["endpoint"], config["api_version"]),
            {"Content-Type": "application/json", "api-key": config["api_key"]},
            payload, cancel_event, provider="Azure",
        )
        return self._write_images(response, output_dir, settings, context,
                                  metadata["request_id"], metadata["attempt"], config["deployment"])

    def build_url(self, endpoint: str, api_version: str) -> str:
        return f"{endpoint.rstrip('/')}/openai/v1/images/generations?{urlencode({'api-version': api_version})}"

    def build_payload(self, prompt: str, settings: GlobalSettings, deployment: str) -> dict[str, Any]:
        if not prompt.strip() or len(prompt) > 32000:
            raise ProviderGenerationError("GPT Image prompts must contain 1–32,000 characters.", status="invalid_settings")
        return {
            "model": deployment, "prompt": prompt, "size": gpt_size(settings),
            "quality": quality_name(settings.quality), "n": settings.num_images,
            "output_format": settings.output_format,
        }

    quality_from_setting = staticmethod(quality_name)
    _size_string = staticmethod(gpt_size)
    _safe_token = staticmethod(safe_token)

    def _load_config(self) -> dict[str, str]:
        mapping = {
            "endpoint": "AZURE_OPENAI_ENDPOINT", "api_key": "AZURE_OPENAI_API_KEY",
            "deployment": "AZURE_GPT_IMAGE_2_DEPLOYMENT",
        }
        config = {key: os.environ.get(env, "").strip() for key, env in mapping.items()}
        missing = [mapping[key] for key, value in config.items() if not value]
        if missing:
            raise ProviderGenerationError(f"Missing Azure OpenAI configuration: {', '.join(missing)}", status="invalid_settings")
        parsed = urlparse(config["endpoint"])
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ProviderGenerationError("AZURE_OPENAI_ENDPOINT must be the HTTPS resource root, without an API path or query.", status="invalid_settings")
        config["api_version"] = os.environ.get("AZURE_OPENAI_API_VERSION", "preview").strip() or "preview"
        return config

    def _write_images(self, payload: dict[str, Any], output_dir: Path, settings: GlobalSettings,
                      context: GenerationContext | None, request_id: str, attempt: int,
                      deployment: str) -> list[GeneratedImage]:
        data = payload.get("data")
        metadata = {"provider": self.id, "model": AZURE_GPT_IMAGE_2_MODEL_ID,
                    "deployment": deployment, "size": payload.get("size") or gpt_size(settings),
                    "quality": payload.get("quality") or quality_name(settings.quality),
                    "n": settings.num_images, "requested_output_format": settings.output_format,
                    "attempt": attempt, "request_id": request_id, "azure_request_id": request_id,
                    "usage": payload.get("usage") or {}}
        if not isinstance(data, list) or not data:
            raise ProviderGenerationError("Azure did not return image data.", metadata=metadata)
        results: list[GeneratedImage] = []
        try:
            for index, item in enumerate(data):
                if not isinstance(item, dict) or not item.get("b64_json"):
                    raise ProviderGenerationError("Azure returned an image entry without base64 data.", metadata=metadata)
                stem = Path(self._output_filename(context, quality_name(settings.quality), index)).stem
                results.append(save_base64_image(item["b64_json"], output_dir, stem,
                                                 {**metadata, "run_index": (context.run_index if context else 1) + index}))
            if len(results) != settings.num_images:
                raise ProviderGenerationError(
                    f"Azure returned {len(results)} of {settings.num_images} requested images; saved results were retained.",
                    metadata=metadata,
                )
        except ProviderGenerationError as exc:
            exc.partial_images = results
            raise
        except OSError as exc:
            raise ProviderGenerationError("Could not save an Azure result.", status="write_failed", partial_images=results) from exc
        return results

    def _output_filename(self, context: GenerationContext | None, quality: str, response_index: int) -> str:
        category = safe_token(context.category_id if context else "cat00", "cat00")
        prompt = safe_token(context.prompt_id if context else "prompt0001", "prompt0001")
        run_index = (context.run_index if context else 1) + response_index
        return f"{category}_{prompt}_q-{quality}_run{run_index:02d}.png"
