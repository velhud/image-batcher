import sys
import os
import tempfile
import atexit
import shutil
from pathlib import Path

import pytest

# backend_server owns a default state at import time, so isolate collection too.
_COLLECTION_STORE = tempfile.mkdtemp(prefix="imageai-test-collection-")
os.environ["IMAGEAI_DATA_DIR"] = _COLLECTION_STORE


def _cleanup_collection_store():
    module = sys.modules.get("app.backend_server")
    if module is not None:
        module.STATE.queue.shutdown()
    shutil.rmtree(_COLLECTION_STORE, ignore_errors=True)


atexit.register(_cleanup_collection_store)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEAI_DATA_DIR", str(tmp_path / "imageai-data"))

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
