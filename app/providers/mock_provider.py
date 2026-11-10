from __future__ import annotations

import base64
import hashlib
import io
import random
from pathlib import Path
from threading import Event

from PIL import Image, ImageDraw, ImageFont

from app.models import GeneratedImage, GenerationContext, GlobalSettings, ModelInfo, ProviderCapabilities
from .base import BaseProvider, ProviderGenerationError
from .image_io import dimensions, save_base64_image


class MockProvider(BaseProvider):
    id = "mock"
    name = "Mock Provider"

    def __init__(self) -> None:
        capabilities = ProviderCapabilities(
            max_images=8, supports_style=False, supports_negative_prompt=False,
            supports_seed=True, supports_quality=False, supports_safety=False,
            supports_attachments=False, output_formats=["png", "jpeg", "webp"],
            supports_background=True,
        )
        super().__init__([ModelInfo("mock-standard", "Mock Standard", self.id, capabilities)],
                         rate_limit_note="Offline deterministic preview images; no provider requests or charges.")

    def request_count(self, settings: GlobalSettings) -> int:
        return 0

    def quota_cost(self, settings: GlobalSettings) -> int:
        return 0

    def validate_settings(self, settings: GlobalSettings, attachments: list[object] | None = None) -> ModelInfo | None:
        model = super().validate_settings(settings, attachments)
        width, height = dimensions(settings)
        if max(width, height) > 4096 or width * height > 16777216:
            raise ProviderGenerationError("Mock images support edges up to 4096 pixels.", status="invalid_settings")
        return model

    def generate_images(self, prompt: str, settings: GlobalSettings, output_dir: Path,
                        cancel_event: Event, attachments: list[object] | None = None,
                        context: GenerationContext | None = None) -> list[GeneratedImage]:
        self.validate_settings(settings, attachments)
        paths = []
        size = dimensions(settings)
        font = ImageFont.load_default()
        try:
            for index in range(settings.num_images):
                if cancel_event.is_set():
                    break
                mode = "RGBA" if settings.background == "transparent" else "RGB"
                color = (*_random_color(prompt, index, settings.seed if not settings.random_seed else None), 0) if mode == "RGBA" else _random_color(prompt, index, settings.seed if not settings.random_seed else None)
                image = Image.new(mode, size, color=color)
                ImageDraw.Draw(image).text((20, 20), f"{prompt[:80] or 'Offline preview'}\nImage {index + 1}",
                                           fill=(255, 255, 255, 255) if mode == "RGBA" else (255, 255, 255), font=font)
                buffer = io.BytesIO()
                image.save(buffer, format=settings.output_format.upper())
                run = (context.run_index if context else 1) + index
                paths.append(save_base64_image(base64.b64encode(buffer.getvalue()).decode("ascii"), output_dir,
                                                f"mock_run{run:02d}", {
                                                    "provider": self.id, "model": "mock-standard", "run_index": run,
                                                    "seed": settings.seed if not settings.random_seed else None,
                                                    "size": f"{size[0]}x{size[1]}", "n": settings.num_images,
                                                    "background": settings.background,
                                                }))
                if cancel_event.wait(0.03):
                    break
        except ProviderGenerationError as exc:
            exc.partial_images = paths
            raise
        except OSError as exc:
            raise ProviderGenerationError("Could not save a mock image.", status="write_failed", partial_images=paths) from exc
        return paths


def _random_color(prompt: str, index: int, seed: int | None = None) -> tuple[int, int, int]:
    identity = hashlib.sha256(f"{prompt}\0{index}\0{seed}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(identity, "big"))
    return tuple(rng.randint(32, 200) for _ in range(3))
