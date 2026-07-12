"""HTTP middleware: X-Request-ID, timing, access log."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging_config import request_id_var

_log = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Ensure X-Request-ID; set contextvar; log one line per request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            elapsed_s = time.perf_counter() - t0
            elapsed_ms = elapsed_s * 1000.0
            _log.info(
                "%s %s -> %s in %.1fms",
                request.method,
                request.url.path,
                status_code,
                elapsed_ms,
            )
            try:
                from .metrics_prom import observe_http_request

                observe_http_request(
                    request.method, request.url.path, status_code, elapsed_s
                )
            except Exception:
                pass
            request_id_var.reset(token)
