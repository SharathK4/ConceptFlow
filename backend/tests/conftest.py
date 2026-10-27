import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("LLM_MODE", "replay")
# Tests never touch the real library, lessons or caches.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="conceptflow-tests-")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
# A free port for the studio when a test serves it (never the developer's own 8010).
with socket.socket() as _sock:
    _sock.bind(("127.0.0.1", 0))
    os.environ["PORT"] = str(_sock.getsockname()[1])


def pytest_addoption(parser):
    parser.addoption("--slow", action="store_true", help="also run the end-to-end replay test (~1 minute)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--slow"):
        return
    skip = pytest.mark.skip(reason="slow: run with --slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: end-to-end tests that render video")
