"""Network-free batch validation shared by both primary clients and the CLI."""
from __future__ import annotations

from app.models import RowData, GlobalSettings, effective_settings, generation_prompt, validate_settings
from app.providers.registry import ProviderRegistry


def preflight(rows: list[RowData], settings: GlobalSettings, providers: ProviderRegistry) -> dict:
    results = []
    total_images = total_requests = 0
    for row in rows:
        errors = []
        current = effective_settings(row, settings)
        try:
            validate_settings(current)
            if not row.prompt.strip():
                raise ValueError("Prompt is empty")
            provider = providers.get(current.provider_id)
            if hasattr(provider, "validate_settings"):
                provider.validate_settings(current, row.attachments)
            if hasattr(provider, "validate_prompt"):
                provider.validate_prompt(generation_prompt(row.prompt, current))
            if provider.quota_cost(current) > current.rate_limit_rpm:
                raise ValueError("The per-job quota exceeds the configured rate limit; lower images per prompt or increase RPM")
            total_images += current.num_images
            total_requests += provider.request_count(current) if hasattr(provider, "request_count") else 1
        except (ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
            errors.append(str(exc))
        results.append({"row_id": row.id, "prompt_id": row.prompt_id, "provider": current.provider_id,
                        "model": current.model_id, "requested_images": current.num_images, "errors": errors})
    return {"valid": not any(row["errors"] for row in results), "rows": results,
            "total_requests": total_requests, "total_images": total_images}
