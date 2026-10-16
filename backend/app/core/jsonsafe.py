"""Serialización JSON sin pérdida: los Decimal viajan como texto (igual que en los modelos Pydantic)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def exact(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, dict):
        return {k: exact(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [exact(v) for v in obj]
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    return obj
