from __future__ import annotations

from app.models import GlobalSettings
from app.providers.azure_provider import AZURE_GPT_IMAGE_2_MODEL_ID, AZURE_PROVIDER_ID


AZURE_LOGO_PROMPT_WRAPPER = """Output exactly one logo concept only. Do not show a sheet of multiple options. Do not show a logo mockup on paper, wall, signage, app screen, business card, or packaging. Show the logo alone, centered on a plain white background. Render it as a clean flat vector-style logo concept with crisp edges and high contrast. No 3D, no bevel, no embossing, no shadow, no glow, no perspective, no photorealism. Avoid decorative textures. Keep the mark compact and balanced. The logo must be legible at small favicon size and also look good at normal presentation size. Do not add extra words, slogans, paragraphs, or random lettering unless the prompt explicitly asks for a wordmark. Avoid generic AI clichés unless the prompt explicitly asks for them: no robot heads, no human brains, no random neural-network blobs, no cliché glowing circuits, no stock sci-fi iconography. The result should look like a serious brand logo concept for a modern AI company."""


def apply_azure_logo_preset(settings: GlobalSettings) -> GlobalSettings:
    settings.provider_id = AZURE_PROVIDER_ID
    settings.model_id = AZURE_GPT_IMAGE_2_MODEL_ID
    settings.size_preset = "Square 1024"
    settings.custom_size = None
    settings.num_images = 1
    settings.output_format = "png"
    settings.background = "auto"
    settings.style_preset = "None"
    settings.negative_prompt = ""
    settings.seed = None
    settings.random_seed = True
    settings.safety = 2
    settings.quality = 5
    settings.rate_limit_rpm = 4
    settings.concurrency_limit = 1
    settings.generate_behavior = "keep"
    settings.prompt_wrapper = AZURE_LOGO_PROMPT_WRAPPER
    return settings
