from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.domains.audit.models import AuditLog

_SECRET_KEYS = {"password", "contrasena", "new_password", "current_password", "token", "csrf_token"}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k in _SECRET_KEYS else _clean(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):  # enums
        return value.value
    return value


def audit(db: Session, *, user_id: int | None, action: str, entity: str | None = None,
          entity_id: Any = None, details: dict | None = None, ip: str | None = None) -> None:
    """Añade una entrada a la bitácora en la transacción actual (se confirma junto con la operación)."""
    db.add(AuditLog(
        user_id=user_id, action=action, entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        details=_clean(details) if details else None, ip=ip,
    ))
