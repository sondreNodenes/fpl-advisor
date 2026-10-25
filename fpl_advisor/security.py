"""Abuse controls and HTTP hardening for the public web app.

Everything here is in memory and per instance: Cloud Run runs at most a few instances, so a
client gets at most that multiple of the limits. No extra services or dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any, TypeVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

T = TypeVar("T")

# Strict policy for the app's own page: no inline script, no inline style, same-origin only.
PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
# FastAPI's /docs page loads Swagger UI from jsDelivr and starts it with an inline script.
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000",
}
DOCS_PATHS = ("/docs", "/redoc")


class JsonFormatter(logging.Formatter):
    """One JSON object per line (Cloud Logging parses ``severity`` and ``message``)."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def json_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


class RateLimiter:
    """Sliding-window limit of ``limit`` requests per ``window`` seconds per key.

    Memory is bounded: at most ``max_keys`` clients are tracked (oldest dropped first).
    """

    def __init__(self, limit: int, window: float = 60.0, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str, now: float | None = None) -> float:
        """0 if the request is allowed, otherwise seconds until it would be."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.pop(key, None) or deque()
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            retry = 0.0 if len(hits) < self.limit else hits[0] + self.window - now
            if not retry:
                hits.append(now)
            self._hits[key] = hits  # most recently seen last
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            return retry


def client_ip(scope: Scope, trust_proxy: bool) -> str:
    """The caller's IP. Behind Cloud Run, the last ``X-Forwarded-For`` entry is the one
    Google's front end added; earlier entries are client-controlled and ignored."""
    if trust_proxy:
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                last = value.decode("latin-1").split(",")[-1].strip()
                if last:
                    return last
    client = scope.get("client")
    return client[0] if client else "unknown"


class AbuseControlMiddleware:
    """Per-client rate limits by route and a request body size limit (413/429 responses)."""

    def __init__(
        self,
        app: ASGIApp,
        limits: dict[tuple[str, str], RateLimiter],
        max_body_bytes: int,
        trust_proxy: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self.app = app
        self.limits = limits
        self.max_body_bytes = max_body_bytes
        self.trust_proxy = trust_proxy
        self.log = logger or logging.getLogger(__name__)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limiter = self.limits.get((scope["method"], scope["path"]))
        if limiter is not None:
            retry = limiter.check(client_ip(scope, self.trust_proxy))
            if retry:
                self.log.warning("rate limited", extra={"fields": {"path": scope["path"]}})
                await _error(send, 429, "Too many requests, slow down", retry_after=retry)
                return

        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body_bytes):
            await _error(send, 413, "Request body too large")
            return

        # Bodies here are tiny, so read the whole body first (also when it is streamed
        # without a Content-Length) and only hand it to the app if it is within the limit.
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":  # client disconnected
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.max_body_bytes:
                await _error(send, 413, "Request body too large")
                return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class SecurityHeadersMiddleware:
    """Adds security headers and a Content-Security-Policy to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CSP if scope["path"].startswith(DOCS_PATHS) else PAGE_CSP

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"content-security-policy"
                ]
                headers += [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
                headers.append((b"content-security-policy", csp.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)


class Busy(Exception):  # noqa: N818 - reads as "raise Busy"
    """No worker slot became free in time."""


class WorkLimiter:
    """At most ``slots`` CPU-heavy jobs at once; callers wait up to ``queue_timeout`` s.

    A job that exceeds ``timeout`` returns ``TimeoutError`` to the caller, but keeps its slot
    until its thread finishes, so slow jobs cannot pile up behind the limit.
    """

    def __init__(self, slots: int, queue_timeout: float, timeout: float) -> None:
        self._slots = threading.BoundedSemaphore(slots)
        self.queue_timeout = queue_timeout
        self.timeout = timeout

    async def run(self, fn: Callable[[], T]) -> T:
        deadline = time.monotonic() + self.queue_timeout
        while not self._slots.acquire(blocking=False):
            if time.monotonic() >= deadline:
                raise Busy
            await asyncio.sleep(0.1)

        def job() -> T:
            try:
                return fn()
            finally:
                self._slots.release()

        return await asyncio.wait_for(asyncio.to_thread(job), self.timeout)


async def _error(send: Send, status: int, detail: str, retry_after: float | None = None) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if retry_after is not None:
        headers.append((b"retry-after", str(max(1, round(retry_after))).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
