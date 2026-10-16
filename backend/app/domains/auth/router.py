from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.domains.auth import service
from app.domains.auth.deps import SESSION_COOKIE, CurrentUser, client_ip, get_current_user, require
from app.domains.auth.models import User
from app.domains.auth.permissions import GRANTABLE, ROLE_PERMISSIONS, P, Role, effective_permissions
from app.domains.auth.schemas import (
    ChangePasswordIn, GrantsIn, LoginIn, MeOut, ResetPasswordIn, UserCreate, UserOut, UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["Autenticación"])
users_router = APIRouter(prefix="/usuarios", tags=["Usuarios"])


def _me(current: CurrentUser) -> MeOut:
    return MeOut(usuario=UserOut.model_validate(current.user),
                 permisos=sorted(p.value for p in current.permissions),
                 csrf_token=current.session.csrf_token)


@router.post("/login", response_model=MeOut)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    sess, token = service.login(db, data.username, data.password, ip=ip,
                                user_agent=request.headers.get("user-agent"))
    cfg = get_settings()
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=cfg.is_production, samesite="strict",
        max_age=cfg.session_ttl_hours * 3600, path="/",
    )
    perms = effective_permissions(sess.user.role, [g.permission for g in sess.user.grants])
    return _me(CurrentUser(user=sess.user, session=sess, permissions=perms, ip=ip))


@router.post("/logout", status_code=204)
def logout(response: Response, current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)):
    service.logout(db, db.merge(current.session), current.ip)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
def me(current: CurrentUser = Depends(get_current_user)):
    return _me(current)


@router.post("/cambiar-contrasena", status_code=204)
def change_password(data: ChangePasswordIn, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    service.change_password(db, db.merge(current.user), current.session, data.current_password,
                            data.new_password, current.ip)


# ------------------------------------------------------------------ usuarios

@users_router.get("", response_model=list[UserOut])
def list_users(_: CurrentUser = Depends(require(P.USUARIOS)), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.full_name)).all()


@users_router.get("/roles")
def roles(_: CurrentUser = Depends(require(P.USUARIOS))):
    return {
        "roles": {r.value: sorted(p.value for p in ROLE_PERMISSIONS[r]) for r in Role},
        "delegables": sorted(p.value for p in GRANTABLE),
    }


@users_router.post("", response_model=UserOut, status_code=201)
def create_user(data: UserCreate, current: CurrentUser = Depends(require(P.USUARIOS)),
                db: Session = Depends(get_db)):
    return service.create_user(db, username=data.username, full_name=data.full_name, role=data.role,
                               password=data.new_password, actor_id=current.id, ip=current.ip)


@users_router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: UserUpdate, current: CurrentUser = Depends(require(P.USUARIOS)),
                db: Session = Depends(get_db)):
    return service.update_user(db, user_id, data.model_dump(exclude_unset=True), actor_id=current.id,
                               ip=current.ip)


@users_router.post("/{user_id}/restablecer-contrasena", response_model=UserOut)
def reset_password(user_id: int, data: ResetPasswordIn,
                   current: CurrentUser = Depends(require(P.USUARIOS)), db: Session = Depends(get_db)):
    return service.reset_password(db, user_id, data.new_password, actor_id=current.id, ip=current.ip)


@users_router.put("/{user_id}/permisos", response_model=UserOut)
def set_grants(user_id: int, data: GrantsIn, current: CurrentUser = Depends(require(P.USUARIOS)),
               db: Session = Depends(get_db)):
    return service.set_grants(db, user_id, data.permisos, actor_id=current.id, ip=current.ip)
