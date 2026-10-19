"""Conversion helpers between MongoDB documents and JSON-safe structures."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

from bson import ObjectId


def oid(value: Any) -> ObjectId | None:
    """Return an ``ObjectId`` for ``value`` or ``None`` when it is not valid.

    Accepts ``ObjectId`` instances and 24-char hex strings.  Returning ``None``
    instead of raising keeps route handlers free of try/except noise while still
    rejecting malformed ids from the URL.
    """
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str):
        value = value.strip()
        if ObjectId.is_valid(value):
            return ObjectId(value)
    return None


def to_jsonable(value: Any) -> Any:
    """Recursively convert Mongo types into JSON-serialisable primitives."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def slugify(value: str) -> str:
    """ASCII, lowercase, dash-separated slug suitable for file names."""
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return cleaned or "item"
