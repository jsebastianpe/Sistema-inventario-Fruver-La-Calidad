from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.permissions import P
from app.domains.settings.service import (
    CommercialSettings, get_commercial_settings, update_commercial_settings,
)

router = APIRouter(prefix="/configuracion", tags=["Configuración"])


@router.get("", response_model=CommercialSettings)
def read(_: CurrentUser = Depends(require(P.PRECIOS_VER)), db: Session = Depends(get_db)):
    return get_commercial_settings(db)


@router.patch("", response_model=CommercialSettings)
def update(changes: dict, current: CurrentUser = Depends(require(P.CONFIGURACION)),
           db: Session = Depends(get_db)):
    return update_commercial_settings(db, changes, user_id=current.id, ip=current.ip)
