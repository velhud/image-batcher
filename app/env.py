from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_dotenv_if_available() -> None:
    root = Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get("IMAGEAI_DATA_DIR") or Path.home() / ".imagen").expanduser()
    for env_path in (root / ".env", data_dir / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)
