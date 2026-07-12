"""Structured logging for API and worker (JSON or plain; request id via context)."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from . import config

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _JsonLogFilter(logging.Filter):
    """Injects request_id from context into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _PlainLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True



class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    """Idempotent: configures root handler once."""
    root = logging.getLogger()
    if getattr(root, "_rcv_configured", False):
        return

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    if config.LOG_JSON:
        handler.setFormatter(_JsonFormatter())
        handler.addFilter(_JsonLogFilter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s req=%(request_id)s %(message)s"
            )
        )
        handler.addFilter(_PlainLogFilter())
    root.handlers.clear()
    root.addHandler(handler)
    setattr(root, "_rcv_configured", True)
