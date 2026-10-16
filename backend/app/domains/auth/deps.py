"""Dependencias de autenticación y autorización para los endpoints."""
import hmac
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import Forbidden, Unauthorized
from app.core.security import token_hash
from app.core.timeutil import now_utc
from app.domains.auth.models import AuthSession, User
from app.domains.auth.permissions import P, effective_permissions

SESSION_COOKIE = "sid"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class CurrentUser:
    user: User
    session: AuthSession
    permissions: frozenset[P]
    ip: str | None

    @property
    def id(self) -> int:
        return self.user.id

    def can(self, perm: P) -> bool:
        return perm in self.permissions


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthorized("Debe iniciar sesión")
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    now = now_utc()
    cfg = get_settings()
    if (
        sess is None
        or sess.revoked_at is not None
        or sess.expires_at <= now
        or sess.last_seen_at + timedelta(minutes=cfg.session_idle_minutes) <= now
        or not sess.user.is_active
    ):
        raise Unauthorized("La sesión expiró o no es válida. Inicie sesión de nuevo")

    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, sess.csrf_token):
            raise Forbidden("Falta el token de protección CSRF o no es válido", code="csrf_invalido")

    if now - sess.last_seen_at > timedelta(minutes=1):
        sess.last_seen_at = now
        db.commit()

    user = sess.user
    perms = effective_permissions(user.role, [g.permission for g in user.grants])
    return CurrentUser(user=user, session=sess, permissions=perms, ip=client_ip(request))


def require(*perms: P):
    """Exige TODOS los permisos indicados."""
    def dep(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current.user.must_change_password and current.session is not None:
            raise Forbidden("Debe cambiar su contraseña antes de continuar",
                            code="cambio_contrasena_requerido")
        missing = [p.value for p in perms if p not in current.permissions]
        if missing:
            raise Forbidden("No tiene permiso para realizar esta operación", details=missing)
        return current
    return dep
