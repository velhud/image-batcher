import base64
import io
import threading
from pathlib import Path

import pytest
from PIL import Image

from app.models import GenerationContext, GlobalSettings
from app.providers.azure_provider import AzureOpenAIImageProvider
from app.providers.base import ProviderFilteredError


def test_azure_payload_quality_mapping():
    provider = AzureOpenAIImageProvider()
    settings = GlobalSettings(num_images=1, quality=5)
    payload = provider.build_payload("prompt", settings, "deployment-name")

    assert provider.build_url("https://example.openai.azure.com/", "preview").endswith(
        "/openai/v1/images/generations?api-version=preview"
    )
    assert payload == {
        "model": "deployment-name",
        "prompt": "prompt",
        "size": "1024x1024",
        "quality": "medium",
        "n": 1,
        "output_format": "png",
    }
    assert provider.quality_from_setting(2) == "low"
    assert provider.quality_from_setting(8) == "high"


def test_azure_writes_base64_png_and_metadata(monkeypatch, tmp_path: Path):
    provider = AzureOpenAIImageProvider()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    monkeypatch.setenv("AZURE_GPT_IMAGE_2_DEPLOYMENT", "deployment-name")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    class Response:
        status_code = 200
        headers = {"x-request-id": "req-123"}
        text = ""

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(png_bytes).decode("ascii")}]}

    import requests

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    context = GenerationContext(
        row_id="row1",
        row_index=1,
        prompt_id="prompt0001",
        category_id="cat01",
        original_prompt="logo",
        full_prompt="logo wrapper",
    )

    images = provider.generate_images("logo wrapper", GlobalSettings(provider_id=provider.id, model_id="gpt-image-2", quality=5), tmp_path, threading.Event(), context=context)

    assert len(images) == 1
    assert images[0].file_path.name == "cat01_prompt0001_q-medium_run01.png"
    assert images[0].file_path.read_bytes() == png_bytes
    assert images[0].metadata["azure_request_id"] == "req-123"


def test_azure_content_filter_classification(monkeypatch, tmp_path: Path):
    provider = AzureOpenAIImageProvider()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    monkeypatch.setenv("AZURE_GPT_IMAGE_2_DEPLOYMENT", "deployment-name")

    class Response:
        status_code = 400
        headers = {"apim-request-id": "req-filter"}
        text = '{"error":{"code":"content_filter"}}'

    import requests

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())

    with pytest.raises(ProviderFilteredError) as exc_info:
        provider.generate_images("blocked", GlobalSettings(provider_id=provider.id, model_id="gpt-image-2"), tmp_path, threading.Event())

    assert exc_info.value.metadata["azure_request_id"] == "req-filter"
