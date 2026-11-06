"""Protection for a local app against cross-site requests and DNS rebinding (DECISIONS #26).

The app has no login: anything that can reach 127.0.0.1:<port> can change the owner's data. Two
attacks get there from an ordinary web page the owner visits:

- **Cross-site requests**: a page on evil.example posts a form to http://127.0.0.1:8765/... Browsers
  send an ``Origin`` header on such POSTs (and a ``Referer`` on most), so writes must come from the
  app's own origin.
- **DNS rebinding**: evil.example re-points its own name at 127.0.0.1, so the browser treats the app
  as same-origin with the attacker. The request then carries ``Host: evil.example``, so only the
  loopback names on the bound port are accepted.

Rejections are plain text and never reach a route, so they show no data and write nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("hub.security")

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]], Awaitable[None]]

LOOPBACK_NAMES = ("127.0.0.1", "localhost")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def allowed_hosts(port: int) -> frozenset[str]:
    """The Host header values the app answers to: the loopback names on the port it is bound to."""
    return frozenset(f"{name}:{port}" for name in LOOPBACK_NAMES)


def _origin_of(url: str) -> str | None:
    """``scheme://host:port`` of a URL (Referer), or None if it is not an absolute http(s) URL."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{parts.hostname}:{port or default}"


def check_request(method: str, headers: dict[str, str], port: int) -> tuple[int, str] | None:
    """Return (status, reason) to reject the request, or None to let it through."""
    hosts = allowed_hosts(port)
    host = headers.get("host", "")
    if host not in hosts:
        return 400, "Unknown host: this app only answers on 127.0.0.1 or localhost."
    if method not in WRITE_METHODS:
        return None
    own = {f"http://{h}" for h in hosts}
    origin = headers.get("origin")
    if origin is not None:
        return None if origin in own else (403, "Cross-site request refused: the Origin is not this app.")
    referer = headers.get("referer")
    if referer and _origin_of(referer) in own:
        return None
    return 403, "Cross-site request refused: no Origin or Referer from this app."


class LocalOriginMiddleware:
    """ASGI middleware applying :func:`check_request` to every HTTP request before any route runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        server = scope.get("server") or ("", 0)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rejection = check_request(scope["method"], headers, int(server[1] or 0))
        if rejection is None:
            await self.app(scope, receive, send)
            return
        status, reason = rejection
        log.warning("refused %s %s: %s", scope["method"], scope.get("path", ""), reason)
        body = reason.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
