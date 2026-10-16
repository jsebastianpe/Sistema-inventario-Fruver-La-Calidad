from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Forbidden
from app.core.pagination import Page, PageParams, paginate
from app.core.schemas import IdempotencyKey, PositiveQty, UnitCostIn
from app.core.timeutil import local_day_bounds, today_local
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.models import User
from app.domains.auth.permissions import P
from app.domains.catalog.models import Product
from app.domains.inventory import service
from app.domains.inventory.models import Lot, MovementType, StockMovement
from app.domains.settings.service import get_commercial_settings

router = APIRouter(prefix="/inventario", tags=["Inventario"])


class MovementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    tipo: Literal["inicial", "merma", "ajuste_entrada", "ajuste_salida",
                  "devolucion_cliente", "devolucion_proveedor"]
    cantidad: PositiveQty
    product_unit_id: int | None = Field(None, description="Presentación; por defecto la unidad base")
    costo_unitario: UnitCostIn | None = Field(
        None, description="Costo por presentación (solo entradas). Si se omite se usa el costo promedio.")
    motivo: str | None = Field(None, max_length=200)
    notas: str | None = Field(None, max_length=2000)
    fecha_vencimiento: date | None = None
    lote: str | None = Field(None, max_length=40)


class ReverseIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=200)


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str = ""
    movement_type: MovementType
    product_unit_id: int | None
    quantity: Decimal
    quantity_base: Decimal
    balance_after: Decimal
    reference_type: str | None
    reference_id: int | None
    reason: str | None
    notes: str | None
    reverses_movement_id: int | None
    user_id: int | None
    user_name: str | None = None
    created_at: datetime
    # Solo con permiso costos.ver:
    unit_cost: Decimal | None = None
    total_cost: Decimal | None = None
    avg_cost_after: Decimal | None = None


class LotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str = ""
    code: str | None
    received_at: datetime
    expiry_date: date | None
    initial_qty: Decimal
    remaining_qty: Decimal
    days_to_expiry: int | None = None


def _mv_out(db: Session, mv: StockMovement, cu: CurrentUser, names: dict | None = None) -> MovementOut:
    out = MovementOut.model_validate(mv)
    product = db.get(Product, mv.product_id)
    user = db.get(User, mv.user_id) if mv.user_id else None
    upd = {"product_name": product.name, "user_name": user.full_name if user else "Sistema"}
    if not cu.can(P.COSTOS_VER):
        upd.update(unit_cost=None, total_cost=None, avg_cost_after=None)
    return out.model_copy(update=upd)


@router.post("/movimientos", response_model=MovementOut, status_code=201)
def register_movement(data: MovementIn, response: Response,
                      idempotency_key: IdempotencyKey = Header(..., alias="Idempotency-Key"),
                      cu: CurrentUser = Depends(require(P.INVENTARIO_VER)), db: Session = Depends(get_db)):
    needed = P.INVENTARIO_MERMAS if data.tipo == "merma" else P.INVENTARIO_AJUSTAR
    if not cu.can(needed):
        raise Forbidden("No tiene permiso para registrar este tipo de movimiento", details=[needed.value])
    if data.costo_unitario is not None and not cu.can(P.COSTOS_VER):
        raise Forbidden("No tiene permiso para registrar costos")
    cfg = get_commercial_settings(db)
    lot = service.LotSpec(code=data.lote, expiry_date=data.fecha_vencimiento)
    mv, created = service.register_manual(
        db, product_id=data.product_id, movement_type=MovementType(data.tipo), quantity=data.cantidad,
        product_unit_id=data.product_unit_id, unit_cost=data.costo_unitario, reason=data.motivo,
        notes=data.notas, lot=lot, user_id=cu.id, ip=cu.ip, idempotency_key=idempotency_key,
        allow_negative=cfg.allow_negative_stock)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return _mv_out(db, mv, cu)


@router.post("/movimientos/{movement_id}/revertir", response_model=MovementOut, status_code=201)
def reverse_movement(movement_id: int, data: ReverseIn,
                     cu: CurrentUser = Depends(require(P.INVENTARIO_AJUSTAR)), db: Session = Depends(get_db)):
    cfg = get_commercial_settings(db)
    mv = service.reverse_manual(db, movement_id, reason=data.motivo, user_id=cu.id, ip=cu.ip,
                                allow_negative=cfg.allow_negative_stock)
    return _mv_out(db, mv, cu)


@router.get("/movimientos", response_model=Page[MovementOut])
def list_movements(product_id: int | None = None, tipo: MovementType | None = None,
                   desde: date | None = None, hasta: date | None = None,
                   referencia_tipo: str | None = Query(None, max_length=30),
                   referencia_id: int | None = None,
                   pg: PageParams = Depends(), cu: CurrentUser = Depends(require(P.INVENTARIO_VER)),
                   db: Session = Depends(get_db)):
    stmt = select(StockMovement)
    if product_id:
        stmt = stmt.where(StockMovement.product_id == product_id)
    if tipo:
        stmt = stmt.where(StockMovement.movement_type == tipo)
    if referencia_tipo:
        stmt = stmt.where(StockMovement.reference_type == referencia_tipo)
    if referencia_id:
        stmt = stmt.where(StockMovement.reference_id == referencia_id)
    if desde or hasta:
        lo, hi = local_day_bounds(desde or date(2000, 1, 1), hasta or today_local())
        stmt = stmt.where(StockMovement.created_at >= lo, StockMovement.created_at < hi)
    page = paginate(db, stmt.order_by(StockMovement.id.desc()), pg)
    page["items"] = [_mv_out(db, m, cu) for m in page["items"]]
    return page


@router.get("/lotes", response_model=Page[LotOut])
def list_lots(product_id: int | None = None, vence_en_dias: int | None = Query(None, ge=0, le=365),
              solo_abiertos: bool = True, pg: PageParams = Depends(),
              _: CurrentUser = Depends(require(P.INVENTARIO_VER)), db: Session = Depends(get_db)):
    stmt = select(Lot)
    if product_id:
        stmt = stmt.where(Lot.product_id == product_id)
    if solo_abiertos:
        stmt = stmt.where(Lot.remaining_qty > 0)
    if vence_en_dias is not None:
        stmt = stmt.where(Lot.expiry_date.is_not(None),
                          Lot.expiry_date <= today_local() + timedelta(days=vence_en_dias))
    page = paginate(db, stmt.order_by(Lot.expiry_date.asc().nulls_last(), Lot.id), pg)
    today = today_local()
    page["items"] = [
        LotOut.model_validate(lot).model_copy(update={
            "product_name": db.get(Product, lot.product_id).name,
            "days_to_expiry": (lot.expiry_date - today).days if lot.expiry_date else None})
        for lot in page["items"]]
    return page
