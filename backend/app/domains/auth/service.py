from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound, TooManyRequests, Unauthorized
from app.core.security import (
    hash_password, needs_rehash, new_token, token_hash, validate_password_strength, verify_password,
)
from app.core.timeutil import now_utc
from app.domains.audit.service import audit
from app.domains.auth.models import AuthSession, LoginAttempt, User, UserPermissionGrant
from app.domains.auth.permissions import Role

GENERIC_LOGIN_ERROR = "Usuario o contraseña incorrectos"


def _recent_failures(db: Session, *, username: str | None = None, ip: str | None = None) -> int:
    cfg = get_settings()
    since = now_utc() - timedelta(minutes=cfg.login_window_minutes)
    q = select(LoginAttempt).where(LoginAttempt.created_at >= since)
    q = q.where(LoginAttempt.username == username) if username else q.where(LoginAttempt.ip == ip)
    last_ok = db.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.success.is_(True), LoginAttempt.created_at >= since,
            (LoginAttempt.username == username) if username else (LoginAttempt.ip == ip),
        )
    )
    if last_ok:
        q = q.where(LoginAttempt.created_at > last_ok)
    return db.scalar(select(func.count()).select_from(q.where(LoginAttempt.success.is_(False))
                                                      .subquery())) or 0


def login(db: Session, username: str, password: str, *, ip: str | None,
          user_agent: str | None) -> tuple[AuthSession, str]:
    """Devuelve (sesión, token en claro). El token en claro solo viaja en la cookie."""
    cfg = get_settings()
    username = username.strip().lower()
    if (_recent_failures(db, username=username) >= cfg.login_max_attempts
            or (ip and _recent_failures(db, ip=ip) >= cfg.login_max_attempts * 4)):
        raise TooManyRequests(
            f"Demasiados intentos fallidos. Espere {cfg.login_window_minutes} minutos e intente de nuevo"
        )

    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        db.add(LoginAttempt(username=username, ip=ip, success=False))
        db.commit()
        raise Unauthorized(GENERIC_LOGIN_ERROR, code="credenciales_invalidas")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    now = now_utc()
    token = new_token()
    sess = AuthSession(
        user_id=user.id, token_hash=token_hash(token), csrf_token=new_token(),
        expires_at=now + timedelta(hours=cfg.session_ttl_hours), last_seen_at=now,
        ip=ip, user_agent=(user_agent or "")[:255],
    )
    db.add(sess)
    db.add(LoginAttempt(username=username, ip=ip, success=True))
    user.last_login_at = now
    audit(db, user_id=user.id, action="auth.inicio_sesion", entity="usuario", entity_id=user.id, ip=ip)
    db.commit()
    db.refresh(sess)
    return sess, token


def logout(db: Session, sess: AuthSession, ip: str | None) -> None:
    sess.revoked_at = now_utc()
    audit(db, user_id=sess.user_id, action="auth.cierre_sesion", entity="usuario",
          entity_id=sess.user_id, ip=ip)
    db.commit()


def revoke_user_sessions(db: Session, user_id: int, *, except_session_id: int | None = None) -> None:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_session_id:
        q = q.where(AuthSession.id != except_session_id)
    db.execute(q.values(revoked_at=now_utc()))


def change_password(db: Session, user: User, sess: AuthSession, current: str, new: str,
                    ip: str | None) -> None:
    if not verify_password(user.password_hash, current):
        raise AppError("La contraseña actual no es correcta", code="contrasena_incorrecta")
    if current == new:
        raise AppError("La nueva contraseña debe ser distinta de la actual")
    user.password_hash = hash_password(new)
    user.must_change_password = False
    user.password_changed_at = now_utc()
    revoke_user_sessions(db, user.id, except_session_id=sess.id)
    audit(db, user_id=user.id, action="auth.cambio_contrasena", entity="usuario", entity_id=user.id,
          ip=ip)
    db.commit()


# ---------------------------------------------------------------- gestión de usuarios

def _active_owners(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role == Role.PROPIETARIO, User.is_active.is_(True))) or 0


def create_user(db: Session, *, username: str, full_name: str, role: Role, password: str,
                actor_id: int | None, ip: str | None, must_change: bool = True) -> User:
    username = username.strip().lower()
    validate_password_strength(password)
    if db.scalar(select(User.id).where(User.username == username)):
        raise Conflict("Ya existe un usuario con ese nombre de usuario")
    user = User(username=username, full_name=full_name.strip(), role=role,
                password_hash=hash_password(password), must_change_password=must_change,
                password_changed_at=now_utc())
    db.add(user)
    db.flush()
    audit(db, user_id=actor_id, action="usuarios.crear", entity="usuario", entity_id=user.id,
          details={"username": username, "rol": role}, ip=ip)
    db.commit()
    return user


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Usuario no encontrado")
    return user


def update_user(db: Session, user_id: int, changes: dict, *, actor_id: int, ip: str | None) -> User:
    user = get_user(db, user_id)
    demotes_owner = user.role == Role.PROPIETARIO and (
        changes.get("is_active") is False
        or (changes.get("role") not in (None, Role.PROPIETARIO))
    )
    if demotes_owner and user.is_active and _active_owners(db) <= 1:
        raise Conflict("Debe existir al menos un propietario activo")
    if user.id == actor_id and changes.get("is_active") is False:
        raise Conflict("No puede desactivar su propio usuario")
    diff = {}
    for k, v in changes.items():
        if v is not None and getattr(user, k) != v:
            diff[k] = {"anterior": getattr(user, k), "nuevo": v}
            setattr(user, k, v)
    if "is_active" in diff and not user.is_active:
        revoke_user_sessions(db, user.id)
    if diff:
        audit(db, user_id=actor_id, action="usuarios.actualizar", entity="usuario",
              entity_id=user.id, details=diff, ip=ip)
    db.commit()
    return user


def reset_password(db: Session, user_id: int, new_password: str, *, actor_id: int | None,
                   ip: str | None) -> User:
    """Recuperación de acceso: un propietario asigna una contraseña temporal.

    El usuario debe cambiarla al iniciar sesión y se cierran todas sus sesiones abiertas.
    """
    user = get_user(db, user_id)
    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.password_changed_at = now_utc()
    revoke_user_sessions(db, user.id)
    audit(db, user_id=actor_id, action="usuarios.restablecer_contrasena", entity="usuario",
          entity_id=user.id, ip=ip)
    db.commit()
    return user


def set_grants(db: Session, user_id: int, perms: list[str], *, actor_id: int, ip: str | None) -> User:
    user = get_user(db, user_id)
    if user.role == Role.PROPIETARIO:
        raise AppError("El propietario ya tiene todos los permisos")
    before = sorted(g.permission for g in user.grants)
    user.grants = [UserPermissionGrant(permission=p, granted_by_id=actor_id) for p in perms]
    audit(db, user_id=actor_id, action="usuarios.permisos", entity="usuario", entity_id=user.id,
          details={"anterior": before, "nuevo": perms}, ip=ip)
    db.commit()
    db.refresh(user)
    return user

