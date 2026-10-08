"""Metadata readers. Each one takes a path and returns a Metadata (empty when it can't read it)."""
from __future__ import annotations

import logging
from functools import wraps

from ..core.models import Metadata

log = logging.getLogger(__name__)


def safe(fn):
    """A broken or unreadable file must never stop a scan: log it and return empty metadata."""
    @wraps(fn)
    def wrapper(path, *args, **kwargs):
        try:
            return fn(path, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - any parser error means "no metadata"
            log.debug("%s failed on %s: %s", fn.__name__, path, e)
            return Metadata()
    return wrapper
