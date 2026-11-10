"""Offline request-contract and failure tests; no live or billable API calls."""
import base64
import io
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

from app.models import GenerationContext, GlobalSettings, InputAttachment
from app.providers.azure_provider import AzureOpenAIImageProvider
from app.providers.base import ProviderCancelledError, ProviderFilteredError, ProviderGenerationError, ProviderRetryExhaustedError
from app.providers.google_provider import GoogleGeminiProvider, NANO_MODEL_ID, PRO_MODEL_ID
from app.providers.http_transport import post_json, retry_delay
from app.providers.image_io import read_attachments, save_base64_image
from app.providers.mock_provider import MockProvider, _random_color
from app.providers.openai_provider import OpenAIProvider


def raster(fmt="PNG", color="navy"):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), color).save(buffer, format=fmt)
    return buffer.getvalue()


def encoded(fmt="PNG", color="navy"):
    return base64.b64encode(raster(fmt, color)).decode("ascii")


class Response:
    def __init__(self, body=None, status=200, headers=None, text="error"):
        self.body = body or {}
        self.status_code = status
        self.headers = {"x-request-id": "req-42", **(headers or {})}
        self.text = text

    def json(self):
        return self.body


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-azure-key")
    monkeypatch.setenv("AZURE_GPT_IMAGE_2_DEPLOYMENT", "test-deployment")
    def blocked(*args, **kwargs):
        pytest.fail("Unexpected HTTP request in an offline provider test")
    monkeypatch.setattr(requests, "post", blocked)


def settings(provider, **values):
    return GlobalSettings(**{"provider_id": provider.id, "model_id": provider.models[0].id, **values})


@pytest.mark.parametrize("fmt,suffix", [("png", ".png"), ("jpeg", ".jpg"), ("webp", ".webp")])
def test_openai_modern_generation_contract_and_original_output_bytes(monkeypatch, tmp_path, fmt, suffix):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"data": [{"b64_json": encoded(fmt.upper())}], "usage": {"total_tokens": 27}})
    monkeypatch.setattr(requests, "post", post)
    provider = OpenAIProvider()
    images = provider.generate_images("a logo", settings(provider, output_format=fmt, size_preset="Landscape"), tmp_path, threading.Event())
    url, kwargs = calls[0]
    assert url.endswith("/images/generations")
    assert kwargs["json"] == {"model": "gpt-image-2", "prompt": "a logo", "size": "1536x1024", "n": 1, "quality": "medium", "output_format": fmt, "background": "auto"}
    assert "response_format" not in kwargs["json"] and "style" not in kwargs["json"]
    assert kwargs["timeout"] == (10, 180)
    assert images[0].file_path.suffix == suffix
    assert images[0].file_path.read_bytes() == raster(fmt.upper())
    assert images[0].metadata["usage"] == {"total_tokens": 27}
    assert images[0].metadata["request_id"] == "req-42"
    assert len(images[0].metadata["sha256"]) == 64


def test_openai_reference_images_use_edit_endpoint_and_detect_actual_mime(monkeypatch, tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(raster("PNG"))
    attachment = InputAttachment("ref", str(reference), "image/jpeg")
    captured = {}
    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response({"data": [{"b64_json": encoded()}]})
    monkeypatch.setattr(requests, "post", post)
    provider = OpenAIProvider()
    images = provider.generate_images("make it blue", settings(provider), tmp_path / "outputs", threading.Event(), [attachment])
    assert captured["url"].endswith("/images/edits")
    assert captured["json"]["images"][0]["image_url"].startswith("data:image/png;base64,")
    assert images[0].metadata["operation"] == "edits"
    assert images[0].metadata["references"][0]["name"] == "reference.jpg"
    assert "data" not in images[0].metadata["references"][0]


@pytest.mark.parametrize("model_id", ["dall-e-2", "dall-e-3", "gpt-image-1", "unknown-new-model"])
def test_retired_or_unknown_models_fail_before_network(tmp_path, model_id):
    provider = OpenAIProvider()
    with pytest.raises(ProviderGenerationError, match="Select a current model"):
        provider.generate_images("test", settings(provider, model_id=model_id), tmp_path, threading.Event())


@pytest.mark.parametrize("change", [
    {"num_images": 11}, {"num_images": 0}, {"num_images": True},
    {"custom_size": {"width": 1000, "height": 1000}},
    {"custom_size": {"width": 256, "height": 256}},
    {"custom_size": {"width": 4096, "height": 4096}},
    {"custom_size": {"width": 2560, "height": 512}},
    {"style_preset": "Vivid"}, {"negative_prompt": "blur"},
    {"seed": 0, "random_seed": False}, {"safety": 0},
    {"background": "transparent", "output_format": "jpeg"}, {"output_format": "gif"},
])
def test_invalid_openai_options_fail_before_network(tmp_path, change):
    provider = OpenAIProvider()
    with pytest.raises(ProviderGenerationError):
        provider.generate_images("test", settings(provider, **change), tmp_path, threading.Event())


def test_no_images_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response({"data": []}))
    provider = OpenAIProvider()
    with pytest.raises(ProviderGenerationError, match="no image data"):
        provider.generate_images("test", settings(provider), tmp_path, threading.Event())
    assert not list(tmp_path.iterdir())


def test_partial_openai_response_retains_valid_images(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response({"data": [{"b64_json": encoded()}, {"b64_json": "not image data"}]}))
    provider = OpenAIProvider()
    with pytest.raises(ProviderGenerationError) as failure:
        provider.generate_images("test", settings(provider, num_images=2), tmp_path, threading.Event())
    assert len(failure.value.partial_images) == 1
    assert failure.value.partial_images[0].file_path.is_file()


def test_successful_response_is_saved_after_inflight_cancel(monkeypatch, tmp_path):
    event = threading.Event()
    def post(*args, **kwargs):
        event.set()
        return Response({"data": [{"b64_json": encoded()}]})
    monkeypatch.setattr(requests, "post", post)
    provider = OpenAIProvider()
    images = provider.generate_images("test", settings(provider), tmp_path, event)
    assert len(images) == 1 and images[0].file_path.is_file()


@pytest.mark.parametrize("failure", [requests.ReadTimeout("lost response"), requests.ConnectionError("connection ended")])
def test_transport_ambiguity_does_not_retry(monkeypatch, failure):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        raise failure
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ProviderGenerationError) as error:
        post_json("https://example.invalid", {}, {}, threading.Event(), provider="OpenAI")
    assert len(calls) == 1
    assert error.value.status == "outcome_unknown"
    assert error.value.metadata["outcome_unknown"]


@pytest.mark.parametrize("code", [500, 502, 503, 504, 408, 409])
def test_uncertain_http_status_does_not_retry(monkeypatch, code):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(status=code)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ProviderGenerationError) as error:
        post_json("https://example.invalid", {}, {}, threading.Event(), provider="OpenAI")
    assert len(calls) == 1 and error.value.status == "outcome_unknown"


class FakeEvent:
    def __init__(self, cancel_on_wait=False):
        self.delays = []
        self.cancel_on_wait = cancel_on_wait
    def is_set(self):
        return False
    def wait(self, delay):
        self.delays.append(delay)
        return self.cancel_on_wait


def test_only_explicit_429_retries_honor_server_delay(monkeypatch):
    responses = iter([Response(status=429, headers={"retry-after-ms": "750"}), Response({"data": []})])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(responses))
    event = FakeEvent()
    _, metadata = post_json("https://example.invalid", {}, {}, event, provider="Azure")
    assert event.delays == [0.75] and metadata["attempt"] == 2


def test_cancel_during_retry_wait_stops_before_second_post(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(status=429, headers={"Retry-After": "15"})
    monkeypatch.setattr(requests, "post", post)
    event = FakeEvent(cancel_on_wait=True)
    with pytest.raises(ProviderCancelledError):
        post_json("https://example.invalid", {}, {}, event, provider="Azure")
    assert calls == [1] and event.delays == [15]


def test_rate_retry_budget_is_bounded(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(status=429, headers={"Retry-After": "0"})
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ProviderRetryExhaustedError):
        post_json("https://example.invalid", {}, {}, FakeEvent(), provider="Azure")
    assert len(calls) == 3


def test_long_retry_after_does_not_retry_early(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response(status=429, headers={"Retry-After": "3600"}))
    event = FakeEvent()
    with pytest.raises(ProviderRetryExhaustedError) as error:
        post_json("https://example.invalid", {}, {}, event, provider="Azure")
    assert event.delays == []
    assert error.value.metadata["retry_after_seconds"] == 3600


def test_retry_after_http_date(monkeypatch):
    monkeypatch.setattr("app.providers.http_transport.time.time", lambda: 0)
    assert retry_delay({"Retry-After": "Thu, 01 Jan 1970 00:00:20 GMT"}, 1) == 20


def test_provider_preflight_rejects_missing_reference_before_request(tmp_path):
    provider = GoogleGeminiProvider()
    with pytest.raises(ProviderGenerationError, match="Cannot read reference"):
        provider.generate_images("test", settings(provider), tmp_path, threading.Event(), [InputAttachment("id", str(tmp_path / "missing.png"))])


def test_reference_count_is_checked_before_files_are_opened(tmp_path):
    provider = OpenAIProvider()
    with pytest.raises(ProviderGenerationError, match="up to 16"):
        provider.validate_settings(settings(provider), [InputAttachment(str(i), "missing.png") for i in range(17)])


def test_google_stable_models_request_contract_and_thought_images_ignored(monkeypatch, tmp_path):
    captured = {}
    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response({"candidates": [{"content": {"parts": [
            {"thought": True, "inlineData": {"data": encoded(color="red"), "mimeType": "image/png"}},
            {"inlineData": {"data": encoded(color="navy"), "mimeType": "image/png"}},
        ]}}], "usageMetadata": {"totalTokenCount": 9}})
    monkeypatch.setattr(requests, "post", post)
    provider = GoogleGeminiProvider()
    images = provider.generate_images("test", settings(provider, size_preset="Portrait"), tmp_path, threading.Event())
    assert provider.models[0].id == "gemini-3.1-flash-image"
    assert provider.models[1].id == "gemini-3-pro-image"
    assert "/v1/models/gemini-3.1-flash-image:generateContent" in captured["url"]
    config = captured["json"]["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert config["imageConfig"] == {"aspectRatio": "2:3", "imageSize": "1K"}
    assert "thinkingBudget" not in config.get("thinkingConfig", {})
    assert len(images) == 1 and images[0].file_path.read_bytes() == raster(color="navy")
    assert images[0].metadata["usage"] == {"totalTokenCount": 9}


def test_google_partial_response_survives_later_failure(monkeypatch, tmp_path):
    responses = iter([Response({"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded(), "mimeType": "image/png"}}]}}]}), Response(status=503)])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(responses))
    provider = GoogleGeminiProvider()
    with pytest.raises(ProviderGenerationError) as error:
        provider.generate_images("test", settings(provider, num_images=2), tmp_path, threading.Event())
    assert error.value.status == "outcome_unknown"
    assert len(error.value.partial_images) == 1


def test_google_cancellation_stops_additional_requests_but_keeps_response(monkeypatch, tmp_path):
    event = threading.Event()
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        event.set()
        return Response({"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded()}}]}}]})
    monkeypatch.setattr(requests, "post", post)
    provider = GoogleGeminiProvider()
    images = provider.generate_images("test", settings(provider, num_images=2), tmp_path, event)
    assert calls == [1] and len(images) == 1


def test_google_filtered_response_has_distinct_status(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response({"promptFeedback": {"blockReason": "SAFETY"}}))
    provider = GoogleGeminiProvider()
    with pytest.raises(ProviderFilteredError):
        provider.generate_images("test", settings(provider), tmp_path, threading.Event())


def test_google_unknown_model_is_not_silently_substituted(tmp_path):
    provider = GoogleGeminiProvider()
    with pytest.raises(ProviderGenerationError, match="Select a current model"):
        provider.generate_images("test", settings(provider, model_id="gemini-3-pro-image-preview"), tmp_path, threading.Event())


def test_google_ultrawide_ratio_uses_documented_spelling():
    provider = GoogleGeminiProvider()
    value = settings(provider, custom_size={"width": 2520, "height": 1080})
    provider.validate_settings(value)
    assert provider._aspect_ratio_from_settings(value) == "21:9"
    assert provider.request_count(settings(provider, num_images=4)) == 4
    assert provider.quota_cost(settings(provider, num_images=4)) == 4


def test_azure_repeated_outputs_never_overwrite(monkeypatch, tmp_path):
    responses = iter([Response({"data": [{"b64_json": encoded(color="navy")}]}, headers={"apim-request-id": "az1"}), Response({"data": [{"b64_json": encoded(color="red")}]})])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(responses))
    provider = AzureOpenAIImageProvider()
    first = provider.generate_images("logo", settings(provider), tmp_path, threading.Event())[0]
    second = provider.generate_images("logo", settings(provider), tmp_path, threading.Event())[0]
    assert first.file_path != second.file_path
    assert first.file_path.read_bytes() == raster(color="navy")
    assert second.file_path.read_bytes() == raster(color="red")


def test_atomic_publication_is_exclusive_under_parallel_writers(tmp_path):
    def write(color):
        return save_base64_image(encoded(color=color), tmp_path, "same-name", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, ["red", "navy", "white", "black"]))
    assert len({item.file_path for item in results}) == 4
    assert not list(tmp_path.glob(".imageai-*"))
    assert {item.file_path.read_bytes() for item in results} == {raster(color=color) for color in ["red", "navy", "white", "black"]}


def test_invalid_image_bytes_are_never_published(tmp_path):
    with pytest.raises(ProviderGenerationError):
        save_base64_image(base64.b64encode(b"not an image").decode(), tmp_path, "bad", {})
    assert not list(tmp_path.iterdir())


def test_mock_does_not_change_process_random_state():
    before = random.getstate()
    assert _random_color("same", 1, 0) == _random_color("same", 1, 0)
    assert _random_color("same", 1, 0) != _random_color("same", 1, 1)
    assert random.getstate() == before


def test_mock_honors_named_dimensions_and_export_format(tmp_path):
    provider = MockProvider()
    images = provider.generate_images("offline", settings(provider, size_preset="Landscape", output_format="jpeg"), tmp_path, threading.Event())
    with Image.open(images[0].file_path) as image:
        assert image.size == (1536, 1024) and image.format == "JPEG"
    assert provider.request_count(settings(provider)) == 0
