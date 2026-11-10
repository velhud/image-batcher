from __future__ import annotations

import json
import re
import csv
from dataclasses import dataclass, field
from io import StringIO
from typing import Dict, List, Optional

from app.prompt_append import append_prompt_text


@dataclass
class BatchPromptRow:
    prompt: str
    prompt_id: str = ""
    category_id: str = ""
    source_metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class BatchParseResult:
    prompts: List[str]
    errors: List[str]
    rows: List[BatchPromptRow] = field(default_factory=list)


def parse_batch_input(
    raw: str,
    mode: str = "lines",
    prompt_field: str = "prompt",
    csv_column: Optional[str] = None,
    prompt_append: str = "",
) -> BatchParseResult:
    if not isinstance(raw, str):
        return BatchParseResult([], ["Input must be text"])
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return BatchParseResult(prompts=[], errors=["Input is empty"])
    if mode == "lines":
        prompts = [append_prompt_text(line, prompt_append) for line in raw.splitlines() if line.strip()]
        return BatchParseResult(prompts=prompts, errors=[], rows=[BatchPromptRow(prompt=p) for p in prompts])
    if mode == "numbered":
        prompts: List[str] = []
        errors: List[str] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            match = re.match(r"^\s*\d+\s*[\.\)\-]\s*(.+)$", line)
            if match:
                prompts.append(append_prompt_text(match.group(1), prompt_append))
            else:
                errors.append(f"Could not parse numbered line: {line}")
        return BatchParseResult(prompts=prompts, errors=errors, rows=[BatchPromptRow(prompt=p) for p in prompts])
    if mode == "json_array":
        try:
            data = json.loads(raw)
        except Exception as exc:
            return BatchParseResult(prompts=[], errors=[f"JSON error: {exc}"])
        prompts = []
        rows_out: List[BatchPromptRow] = []
        errors: List[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    prompt = append_prompt_text(item, prompt_append)
                    prompts.append(prompt)
                    rows_out.append(BatchPromptRow(prompt=prompt))
                elif isinstance(item, dict) and isinstance(item.get(prompt_field), str) and item[prompt_field].strip():
                    prompt = append_prompt_text(item[prompt_field], prompt_append)
                    prompts.append(prompt)
                    rows_out.append(_row_from_mapping(item, prompt_field, prompt, prompt_append=prompt_append))
                else:
                    errors.append(f"Unsupported item: {item}")
        else:
            errors.append("JSON array expected")
        return BatchParseResult(prompts=prompts, errors=errors, rows=rows_out)
    if mode == "json_lines":
        prompts: List[str] = []
        rows_out: List[BatchPromptRow] = []
        errors: List[str] = []
        for idx, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception as exc:
                errors.append(f"Line {idx}: {exc}")
                continue
            if isinstance(obj, dict) and isinstance(obj.get(prompt_field), str) and obj[prompt_field].strip():
                prompt = append_prompt_text(obj[prompt_field], prompt_append)
                prompts.append(prompt)
                rows_out.append(_row_from_mapping(obj, prompt_field, prompt, prompt_append=prompt_append))
            else:
                errors.append(f"Line {idx}: '{prompt_field}' must be nonempty text")
        return BatchParseResult(prompts=prompts, errors=errors, rows=rows_out)
    if mode == "csv":
        prompts: List[str] = []
        rows_out: List[BatchPromptRow] = []
        errors: List[str] = []
        try:
            reader = csv.reader(StringIO(raw), strict=True)
            rows = list(reader)
            if not rows:
                return BatchParseResult([], ["CSV empty"])
            headers = [value.strip() for value in rows[0]]
            has_header = bool(headers) and (csv_column is None or not csv_column.isdigit())
            body = rows[1:] if has_header else rows
            col_idx = None
            if headers and csv_column:
                if csv_column.isdigit():
                    col_idx = int(csv_column)
                elif csv_column in headers:
                    col_idx = headers.index(csv_column)
            if col_idx is None:
                if has_header:
                    requested = csv_column or prompt_field
                    if requested not in headers:
                        return BatchParseResult([], [f"CSV column '{requested}' was not found; available columns: {', '.join(headers)}"])
                    col_idx = headers.index(requested)
                else:
                    col_idx = 0
            if has_header and len(set(headers)) != len(headers):
                return BatchParseResult([], ["CSV header contains duplicate column names"])
            for idx, row in enumerate(body, start=1):
                if col_idx >= len(row):
                    errors.append(f"Line {idx}: column {col_idx} missing")
                    continue
                value = row[col_idx].strip()
                if value:
                    prompt = append_prompt_text(value, prompt_append)
                    prompts.append(prompt)
                    if has_header:
                        mapping = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                        rows_out.append(_row_from_mapping(mapping, csv_column or "prompt", prompt, prompt_append=prompt_append))
                    else:
                        rows_out.append(BatchPromptRow(prompt=prompt))
        except Exception as exc:
            errors.append(f"CSV parse error: {exc}")
        return BatchParseResult(prompts=prompts, errors=errors, rows=rows_out)
    return BatchParseResult(prompts=[], errors=[f"Unknown mode {mode}"])


def _row_from_mapping(mapping: Dict, prompt_field: str, prompt: str, *, prompt_append: str = "") -> BatchPromptRow:
    source_metadata = {str(k): str(v) for k, v in mapping.items() if k != prompt_field}
    if prompt_append.strip():
        source_metadata["base_prompt"] = str(mapping.get(prompt_field, ""))
        source_metadata["prompt_append"] = prompt_append.strip()
    return BatchPromptRow(
        prompt=prompt,
        prompt_id=str(mapping.get("prompt_id", "") or mapping.get("id", "") or ""),
        category_id=str(mapping.get("category_id", "") or mapping.get("category", "") or ""),
        source_metadata=source_metadata,
    )
