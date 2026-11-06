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


def _bound_socket() -> socket.socket:
    """A listening-ready socket on a free local port, kept open and handed to uvicorn.

    Probing for a free port and closing it before uvicorn binds leaves a gap in which another xdist
    worker can take the same port; passing the bound socket closes that gap (DECISIONS #38).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


@pytest.fixture
def live_server(config) -> Iterator[str]:
    """Start the app in a background thread against the fixture data_dir; yield its base URL."""
    sock = _bound_socket()
    port = int(sock.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, clock=lambda: FIXED_NOW), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The app did not start within 10 s")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
