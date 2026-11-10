from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.models import GlobalSettings, RowData, RowStatus, effective_settings


LOG_FILENAME = "generation_log.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def append_generation_log(log_path: Path, row: Dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": utc_now_iso(), **row}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_generation_log(log_path: Path) -> List[Dict[str, Any]]:
    if not log_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"status": "invalid_log_line", "error": line[:500]})
    return rows


def verify_rows(rows: Iterable[RowData], settings: GlobalSettings | None = None) -> Dict[str, Any]:
    row_list = list(rows)
    successes: List[str] = []
    filtered: List[str] = []
    failed: List[str] = []
    missing: List[str] = []
    skipped: List[str] = []
    actual_images = 0
    requested_images = 0

    for idx, row in enumerate(row_list, start=1):
        prompt_id = row.prompt_id or f"prompt{idx:04d}"
        existing_count = len({str(Path(img.file_path).resolve()) for img in row.images if Path(img.file_path).is_file()})
        desired_count = effective_settings(row, settings or GlobalSettings()).num_images
        actual_images += existing_count
        requested_images += desired_count
        has_existing_image = existing_count >= desired_count
        if row.status == RowStatus.COMPLETED and has_existing_image:
            successes.append(prompt_id)
        elif row.status == RowStatus.FILTERED:
            filtered.append(prompt_id)
        elif row.status == RowStatus.ERROR:
            failed.append(prompt_id)
        elif has_existing_image:
            successes.append(prompt_id)
        elif row.status == RowStatus.CANCELLED:
            skipped.append(prompt_id)
        else:
            missing.append(prompt_id)

    total = len(row_list)
    success_count = len(successes)
    return {
        "total_prompts": total,
        "successful_images": success_count,
        "successful_prompts": success_count,
        "image_count": actual_images,
        "requested_image_count": requested_images,
        "filtered_count": len(filtered),
        "failed_count": len(failed),
        "missing_count": len(missing),
        "skipped_count": len(skipped),
        "matches_prompt_count": success_count == total,
        "successful_prompt_ids": successes,
        "filtered_prompt_ids": filtered,
        "failed_prompt_ids": failed,
        "missing_prompt_ids": missing,
        "skipped_prompt_ids": skipped,
    }
