"""Bounded provider requests with conservative handling of uncertain outcomes."""
from __future__ import annotations

import time
import math
from datetime import timezone
from email.utils import parsedate_to_datetime
from threading import Event
from typing import Any

import requests

from .base import ProviderCancelledError, ProviderFilteredError, ProviderGenerationError, ProviderRetryExhaustedError


def retry_delay(headers: Any, attempt: int) -> float:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    for key, divisor in (("retry-after-ms", 1000), ("retry-after", 1)):
        if key not in lowered:
            continue
        try:
            delay = float(lowered[key]) / divisor
            if math.isfinite(delay):
                return max(0.0, delay)
        except (ValueError, TypeError):
            if key == "retry-after":
                try:
                    value = parsedate_to_datetime(lowered[key])
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return max(0.0, value.timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
    return float(min(60, 2 ** attempt))


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any], cancel_event: Event,
              *, provider: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retry only explicit rate-limit rejections, never ambiguous paid POSTs."""
    for attempt in range(1, 4):
        if cancel_event.is_set():
            raise ProviderCancelledError()
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=(10, 180), allow_redirects=False)
        except requests.RequestException as exc:
            raise ProviderGenerationError(
                f"{provider} connection ended without a confirmed result. The request may have run; review before retrying.",
                status="outcome_unknown", metadata={"attempt": attempt, "exception_type": type(exc).__name__, "outcome_unknown": True},
            ) from exc
        request_id = response.headers.get("x-request-id") or response.headers.get("apim-request-id") or ""
        metadata = {"attempt": attempt, "request_id": request_id, "http_status": response.status_code}
        if provider == "Azure":
            metadata["azure_request_id"] = request_id
        if 200 <= response.status_code < 300:
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderGenerationError(f"{provider} returned an unreadable result; review before retrying.",
                                              status="outcome_unknown", metadata={**metadata, "outcome_unknown": True}) from exc
            if not isinstance(body, dict):
                raise ProviderGenerationError(f"{provider} returned a malformed result.", metadata=metadata)
            # Do not discard a successful paid response when cancellation arrives
            # during POST. Providers save it and the queue reports cancellation.
            return body, metadata
        error = response.text[:1000]
        lowered = error.lower()
        if response.status_code in {400, 403} and any(token in lowered for token in
                ("content_filter", "contentfilter", "content_policy", "safety", "moderation_blocked")):
            raise ProviderFilteredError(f"{provider} blocked the request: {error}", metadata=metadata)
        if response.status_code == 429:
            delay = retry_delay(response.headers, attempt)
            metadata["retry_after_seconds"] = delay
            # Do not wait indefinitely or retry sooner than the server asked.
            if attempt == 3 or delay > 300:
                raise ProviderRetryExhaustedError(f"{provider} rate limited the request; retry after {delay:g} seconds.", metadata=metadata)
            if cancel_event.wait(delay):
                raise ProviderCancelledError()
            continue
        if response.status_code >= 500 or response.status_code in {408, 409}:
            raise ProviderGenerationError(f"{provider} returned HTTP {response.status_code}; outcome is uncertain. Review before retrying.",
                                          status="outcome_unknown", metadata={**metadata, "outcome_unknown": True})
        raise ProviderGenerationError(f"{provider} error {response.status_code}: {error}", metadata=metadata)
    raise AssertionError("unreachable")
