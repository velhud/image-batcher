from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]


def invoke(tmp_path, text, *options, mode="csv"):
    source = tmp_path / "prompts.txt"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output"
    environment = os.environ.copy()
    environment["IMAGEAI_DATA_DIR"] = str(tmp_path / "unrelated-data-directory")
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "AZURE_OPENAI_API_KEY"):
        environment.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "--input", str(source), "--mode", mode,
         "--output", str(output), *options],
        cwd=REPOSITORY, env=environment, text=True, capture_output=True, timeout=20,
    )
    assert not (tmp_path / "unrelated-data-directory").exists(), "CLI wrote outside its explicit output directory"
    return completed, json.loads(completed.stdout), output


def test_cli_defaults_to_offline_dry_run_without_writes(tmp_path):
    result, summary, output = invoke(tmp_path, "prompt_id,prompt\np1,first\np2,second\n", "--images", "2")
    assert result.returncode == 0, result.stderr
    assert summary["status"] == "ready"
    assert summary["dry_run"] is True
    assert summary["provider"] == "mock"
    assert summary["row_count"] == 2
    assert summary["planned_images"] == 4
    assert summary["provider_requests_planned"] == 0
    assert not output.exists()


def test_cli_can_preflight_remote_provider_without_credentials_or_requests(tmp_path):
    result, summary, output = invoke(tmp_path, "prompt\nfirst\n", "--provider", "openai")
    assert result.returncode == 0, result.stderr
    assert summary["provider"] == "openai"
    assert summary["provider_requests_planned"] == 1
    assert summary["dry_run"] is True
    assert not output.exists()


def test_cli_mock_run_exports_complete_portable_bundle_with_verified_hashes(tmp_path):
    result, summary, output = invoke(
        tmp_path, "prompt_id,category_id,prompt\np1,objects,first\np2,objects,second\n",
        "--provider", "mock", "--images", "2", "--prompt-append", "Use a blue background.", "--run",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert summary["status"] == "completed"
    assert summary["completed_rows"] == 2
    assert summary["images_saved"] == 4
    assert summary["provider_requests_planned"] == 0
    assert summary["provider_work_stopped"] is True
    session_path = Path(summary["session_path"])
    assert session_path == output / "session.json"
    session = json.loads(session_path.read_text())
    assert all("Use a blue background." in row["prompt"] for row in session["rows"])
    assert {row["prompt_id"] for row in session["rows"]} == {"p1", "p2"}
    bundle = Path(summary["bundle_path"])
    assert bundle.is_relative_to(output)
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["image_count"] == 4
    assert not manifest["missing"]
    assert (bundle / "contact-sheet.png").is_file()
    for item in manifest["files"]:
        relative = Path(item["path"])
        assert not relative.is_absolute()
        artifact = bundle / relative
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == item["sha256"]
    portable = json.loads((bundle / "session.json").read_text())
    assert all(not Path(image["file_path"]).is_absolute() for row in portable["rows"] for image in row["images"])
    assert all(row["status"] == "Completed" for row in portable["rows"])


@pytest.mark.parametrize("text", [
    "wrong_header\nhello\n",
    "prompt,prompt\nhello,world\n",
    "prompt,notes\nhello,one,extra\n",
    "prompt,notes\n,empty\n",
    'prompt\n"unterminated\n',
])
def test_cli_rejects_invalid_csv_before_creating_or_generating_anything(tmp_path, text):
    result, summary, output = invoke(tmp_path, text, "--run")
    assert result.returncode == 2
    assert summary["status"] == "invalid"
    assert summary["errors"]
    assert not output.exists()


def test_cli_unsupported_model_is_preflight_error_without_generation(tmp_path):
    result, summary, output = invoke(tmp_path, "prompt\nhello\n", "--model", "nonexistent-model", "--run")
    assert result.returncode == 2
    assert summary["status"] == "invalid"
    assert "not supported" in str(summary["errors"])
    assert not output.exists()


@pytest.mark.parametrize("mode,text", [
    ("json_array", '[{"prompt": null}]'),
    ("json_array", '["valid", 123]'),
    ("json_lines", '{"prompt":"valid"}\n{"prompt": 123}\n'),
])
def test_cli_structured_batches_are_validated_as_a_whole(tmp_path, mode, text):
    result, summary, output = invoke(tmp_path, text, "--run", mode=mode)
    assert result.returncode == 2
    assert summary["status"] == "invalid"
    assert not output.exists()


@pytest.mark.parametrize("mode,text", [
    ("lines", "first\nsecond\n"),
    ("json_array", '["first", {"prompt":"second", "prompt_id":"p2"}]'),
    ("json_lines", '{"prompt":"first"}\n{"prompt":"second"}\n'),
])
def test_cli_supported_input_modes_preflight(tmp_path, mode, text):
    result, summary, output = invoke(tmp_path, text, mode=mode)
    assert result.returncode == 0, result.stderr
    assert summary["row_count"] == 2
    assert not output.exists()


def test_cli_preserves_existing_output_directory(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "previous-run.txt"
    marker.write_text("preserve this")
    result, summary, _ = invoke(tmp_path, "prompt\nhello\n", "--run")
    assert result.returncode == 2
    assert "not empty" in str(summary["errors"])
    assert marker.read_text() == "preserve this"
    assert list(output.iterdir()) == [marker]


def test_cli_timeout_stops_without_replaying_unadmitted_rows(tmp_path):
    result, summary, output = invoke(tmp_path, "prompt\nhello\n", "--timeout", "0.000001", "--run")
    assert result.returncode == 1
    assert summary["status"] == "timed_out"
    assert summary["images_saved"] == 0
    assert summary["provider_work_stopped"] is True
    assert Path(summary["session_path"]).is_file()
    assert summary["failures"][0]["status"] == "Cancelled"


def test_cli_invalid_image_count_fails_preflight(tmp_path):
    result, summary, output = invoke(tmp_path, "prompt\nhello\n", "--images", "0", "--run")
    assert result.returncode == 2
    assert summary["status"] == "invalid"
    assert not output.exists()


def test_cli_demo_file_runs_offline(tmp_path):
    text = (REPOSITORY / "examples" / "demo-prompts.csv").read_text()
    result, summary, output = invoke(tmp_path, text, "--provider", "mock", "--run")
    assert result.returncode == 0, result.stderr + result.stdout
    assert summary["row_count"] == 3
    assert summary["images_saved"] == 3
    assert summary["provider_requests_planned"] == 0
