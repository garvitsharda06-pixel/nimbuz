"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_KEY = re.compile(r"(token|secret|password|authorization|api_key)", re.I)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")


def _redact(_logger: Any, _name: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for k in list(event.keys()):
        if _SECRET_KEY.search(k):
            event[k] = "***"
        elif isinstance(event[k], str):
            event[k] = _BEARER.sub("Bearer ***", event[k])
    return event


def setup_logging(level: str = "INFO", json: bool = False) -> None:
    logging.basicConfig(stream=sys.stderr, level=level, format="%(message)s")
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )


def get_logger(name: str = "dalal") -> Any:
    return structlog.get_logger(name)
