"""E2E harness: a temporary data_dir from fixtures and the app running on a free local port.

The clock is fixed at Friday 30 October 2026, 12:00 New York (helpers.FIXED_NOW), so days-left values
and "today" stamps are deterministic (docs/02 §2: 46 days to Princeton).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from hub.app import create_app

from helpers import FIXED_NOW


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live_server(config) -> Iterator[str]:
    """Start the app in a background thread against the fixture data_dir; yield its base URL."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, clock=lambda: FIXED_NOW), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The app did not start within 10 s")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
