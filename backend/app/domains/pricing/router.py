from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, Conflict, NotFound
from app.core.numbers import money, rate
from app.core.pagination import Page, PageParams, paginate
from app.core.schemas import MoneyIn
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.models import User
from app.domains.auth.permissions import P
from app.domains.catalog.models import Product
from app.domains.pricing import service
from app.domains.pricing.models import PriceChangeSource, PriceHistory, PriceSuggestion, SuggestionStatus
from app.domains.settings.service import get_commercial_settings

router = APIRouter(prefix="/precios", tags=["Precios"])


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str = ""
    base_unit_code: str = ""
    status: SuggestionStatus
    unit_cost: Decimal
    margin: Decimal
    commission_rate: Decimal
    tax_rate: Decimal
    raw_price: Decimal
    suggested_price: Decimal
    current_price: Decimal | None
    change_rate: Decimal | None
    market_price_base: Decimal | None
    origin: str
    explanation: dict[str, Any]
    created_at: datetime
    decided_by_id: int | None
    decided_at: datetime | None
    decision_note: str | None


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str = ""
    old_price: Decimal | None
    new_price: Decimal
    source: PriceChangeSource
    suggestion_id: int | None
    justification: str
    changed_by_id: int | None
    changed_by_name: str | None = None
    changed_at: datetime


class GenerateIn(BaseModel):
    product_ids: list[int] | None = Field(None, description="Vacío = todos los productos activos")


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=3, max_length=500, description="Justificación")
    final_price: MoneyIn | None = Field(None, description="Precio distinto al sugerido (opcional)")


class ManualPriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_price: MoneyIn
    justification: str = Field(min_length=3, max_length=500)
    confirm_large_change: bool = False


def _s_out(db: Session, s: PriceSuggestion) -> SuggestionOut:
    p = db.get(Product, s.product_id)
    return SuggestionOut.model_validate(s).model_copy(
        update={"product_name": p.name, "base_unit_code": p.base_unit.code})


@router.get("/sugerencias", response_model=Page[SuggestionOut])
def list_suggestions(estado: SuggestionStatus | None = SuggestionStatus.PENDIENTE,
                     product_id: int | None = None, pg: PageParams = Depends(),
                     _: CurrentUser = Depends(require(P.PRECIOS_VER)), db: Session = Depends(get_db)):
    stmt = select(PriceSuggestion)
    if estado:
        stmt = stmt.where(PriceSuggestion.status == estado)
    if product_id:
        stmt = stmt.where(PriceSuggestion.product_id == product_id)
    page = paginate(db, stmt.order_by(PriceSuggestion.created_at.desc(), PriceSuggestion.id.desc()), pg)
    page["items"] = [_s_out(db, s) for s in page["items"]]
    return page


@router.post("/sugerencias/generar")
def generate(data: GenerateIn, cu: CurrentUser = Depends(require(P.PRECIOS_VER)),
             db: Session = Depends(get_db)):
    cfg = get_commercial_settings(db)
    stmt = select(Product).where(Product.is_active.is_(True))
    if data.product_ids:
        stmt = stmt.where(Product.id.in_(data.product_ids))
    created, skipped = [], []
    for product in db.scalars(stmt.order_by(Product.id)):
        s, why = service.generate_suggestion(db, product, cfg, origin="manual", user_id=cu.id)
        if s:
            created.append(s.id)
        else:
            skipped.append({"product_id": product.id, "producto": product.name, "motivo": why})
    audit(db, user_id=cu.id, action="precios.generar_sugerencias", details={"generadas": len(created)},
          ip=cu.ip)
    db.commit()
    return {"generadas": len(created), "ids": created, "omitidas": skipped}


def _lock_product(db: Session, product_id: int) -> Product:
    p = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product)
                  .execution_options(populate_existing=True))
    if p is None:
        raise NotFound("Producto no encontrado")
    return p


def _pending(db: Session, sid: int) -> PriceSuggestion:
    s = db.scalar(select(PriceSuggestion).where(PriceSuggestion.id == sid).with_for_update())
    if s is None:
        raise NotFound("Sugerencia no encontrada")
    if s.status != SuggestionStatus.PENDIENTE:
        raise Conflict(f"La sugerencia ya está {s.status.value}", code="estado_invalido")
    return s


@router.post("/sugerencias/{sid}/aprobar", response_model=SuggestionOut)
def approve(sid: int, data: DecisionIn, cu: CurrentUser = Depends(require(P.PRECIOS_APROBAR)),
            db: Session = Depends(get_db)):
    s = _pending(db, sid)
    product = _lock_product(db, s.product_id)
    service.validate_still_valid(db, s, product)
    final = money(data.final_price) if data.final_price is not None else s.suggested_price
    if final <= 0:
        raise AppError("El precio debe ser mayor que cero", code="precio_invalido")
    just = data.note if final == s.suggested_price else \
        f"{data.note} (ajustado manualmente desde el sugerido {s.suggested_price})"
    service.set_price(db, product, final, source=PriceChangeSource.SUGERENCIA, justification=just,
                      user_id=cu.id, ip=cu.ip, suggestion_id=s.id)
    s.status = SuggestionStatus.APROBADA
    s.decided_by_id, s.decided_at, s.decision_note = cu.id, service.now_utc(), data.note
    db.commit()
    return _s_out(db, s)


@router.post("/sugerencias/{sid}/rechazar", response_model=SuggestionOut)
def reject(sid: int, data: DecisionIn, cu: CurrentUser = Depends(require(P.PRECIOS_APROBAR)),
           db: Session = Depends(get_db)):
    s = _pending(db, sid)
    s.status = SuggestionStatus.RECHAZADA
    s.decided_by_id, s.decided_at, s.decision_note = cu.id, service.now_utc(), data.note
    audit(db, user_id=cu.id, action="precios.rechazar_sugerencia", entity="sugerencia", entity_id=s.id,
          details={"nota": data.note}, ip=cu.ip)
    db.commit()
    return _s_out(db, s)


@router.post("/productos/{product_id}", response_model=HistoryOut | None)
def set_manual_price(product_id: int, data: ManualPriceIn,
                     cu: CurrentUser = Depends(require(P.PRECIOS_APROBAR)), db: Session = Depends(get_db)):
    product = _lock_product(db, product_id)
    cfg = get_commercial_settings(db)
    if product.current_price and not data.confirm_large_change:
        change = abs(rate((data.new_price - product.current_price) / product.current_price))
        if change > cfg.price_change_alert_rate:
            raise Conflict(
                f"El cambio es de {change * 100:.1f} %, superior al umbral de "
                f"{cfg.price_change_alert_rate * 100:.0f} %. Confirme explícitamente el cambio.",
                code="variacion_requiere_confirmacion", details={"variacion": str(change)})
    entry = service.set_price(db, product, data.new_price, source=PriceChangeSource.MANUAL,
                              justification=data.justification, user_id=cu.id, ip=cu.ip)
    # Una sugerencia pendiente queda superada por el cambio manual.
    db.execute(service.update(PriceSuggestion).where(
        PriceSuggestion.product_id == product.id, PriceSuggestion.status == SuggestionStatus.PENDIENTE
    ).values(status=SuggestionStatus.REEMPLAZADA))
    db.commit()
    return _h_out(db, entry) if entry else None


def _h_out(db: Session, h: PriceHistory) -> HistoryOut:
    u = db.get(User, h.changed_by_id) if h.changed_by_id else None
    return HistoryOut.model_validate(h).model_copy(update={
        "product_name": db.get(Product, h.product_id).name,
        "changed_by_name": u.full_name if u else "Sistema"})


@router.get("/historial", response_model=Page[HistoryOut])
def history(product_id: int | None = None, pg: PageParams = Depends(),
            _: CurrentUser = Depends(require(P.PRECIOS_VER)), db: Session = Depends(get_db)):
    stmt = select(PriceHistory)
    if product_id:
        stmt = stmt.where(PriceHistory.product_id == product_id)
    page = paginate(db, stmt.order_by(PriceHistory.changed_at.desc(), PriceHistory.id.desc()), pg)
    page["items"] = [_h_out(db, h) for h in page["items"]]
    return page
