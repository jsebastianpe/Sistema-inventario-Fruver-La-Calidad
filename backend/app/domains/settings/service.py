"""Configuración comercial (márgenes, redondeo, alertas, política de precios).

Los valores por defecto viven aquí; la BD solo guarda lo que el propietario cambió.
"""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domains.audit.service import audit
from app.domains.settings.models import BusinessSetting


class CommercialSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Precios
    default_margin: Decimal = Field(Decimal("0.30"), ge=0, lt=1)
    sales_commission_rate: Decimal = Field(Decimal("0"), ge=0, lt=1)
    rounding_step: Decimal = Field(Decimal("50"), ge=0, le=10000)
    rounding_mode: Literal["arriba", "abajo", "cercano"] = "arriba"
    price_change_alert_rate: Decimal = Field(Decimal("0.15"), gt=0, le=10)
    auto_price_update_enabled: bool = False
    auto_price_max_change_rate: Decimal = Field(Decimal("0.10"), gt=0, le=1)

    # Compras e inventario
    purchase_tax_in_cost: bool = True
    allow_negative_stock: bool = False
    expiry_alert_days: int = Field(3, ge=0, le=60)

    # Ventas
    employee_max_discount_rate: Decimal = Field(Decimal("0.05"), ge=0, le=1)

    # Mercado
    market_sync_enabled: bool = True
    stale_quote_days: int = Field(3, ge=1, le=60)
    market_variation_alert_rate: Decimal = Field(Decimal("0.20"), gt=0, le=10)

    @model_validator(mode="after")
    def _check(self):
        if self.default_margin + self.sales_commission_rate >= 1:
            raise ValueError("El margen más la comisión deben ser menores que 1 (100 %)")
        return self


def get_commercial_settings(db: Session) -> CommercialSettings:
    stored = {s.key: s.value for s in db.scalars(select(BusinessSetting))}
    known = {k: v for k, v in stored.items() if k in CommercialSettings.model_fields}
    return CommercialSettings(**known)


def update_commercial_settings(db: Session, changes: dict, *, user_id: int, ip: str | None
                               ) -> CommercialSettings:
    current = get_commercial_settings(db)
    unknown = set(changes) - set(CommercialSettings.model_fields)
    if unknown:
        raise AppError("Parámetros desconocidos", details=sorted(unknown))
    try:
        merged = CommercialSettings(**{**current.model_dump(), **changes})
    except ValidationError as e:
        raise AppError("Configuración inválida",
                       details=[{"campo": ".".join(map(str, x["loc"])), "mensaje": x["msg"]}
                                for x in e.errors()]) from e
    dumped = merged.model_dump(mode="json")
    diff = {}
    for key in changes:
        old = current.model_dump(mode="json")[key]
        if old != dumped[key]:
            row = db.get(BusinessSetting, key)
            if row is None:
                db.add(BusinessSetting(key=key, value=dumped[key], updated_by_id=user_id))
            else:
                row.value = dumped[key]
                row.updated_by_id = user_id
            diff[key] = {"anterior": old, "nuevo": dumped[key]}
    if diff:
        audit(db, user_id=user_id, action="configuracion.actualizar", entity="configuracion",
              details=diff, ip=ip)
    db.commit()
    return merged
