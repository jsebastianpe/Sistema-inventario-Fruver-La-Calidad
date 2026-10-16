from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, Conflict, NotFound
from app.core.pagination import Page, PageParams, paginate
from app.core.schemas import FactorIn
from app.core.timeutil import now_utc
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.permissions import P
from app.domains.catalog.models import Product, Unit
from app.domains.inventory.models import StockBalance
from app.domains.market import service
from app.domains.settings.service import get_commercial_settings
from app.domains.market.models import (
    MarketProduct, MarketQuote, MarketSource, MarketSyncRun, ProductMarketMapping, QuoteFlag,
    SyncStatus, WorkerHeartbeat,
)

router = APIRouter(prefix="/mercado", tags=["Precios de mercado"])


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    url: str | None
    is_active: bool
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_status: str | None
    last_error: str | None
    latest_price_date: date | None
    sync_requested_at: datetime | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_id: int
    started_at: datetime
    finished_at: datetime | None
    status: SyncStatus
    attempt: int
    triggered_by: str
    bytes_downloaded: int | None
    records_read: int
    records_inserted: int
    records_duplicated: int
    records_invalid: int
    records_out_of_scope: int
    anomalies: int
    max_price_date: date | None
    error_message: str | None


class ExternalProductOut(BaseModel):
    id: int
    source_id: int
    external_code: str
    name: str
    price_unit_code: str | None
    unit_confirmed_at: datetime | None
    latest_price_date: date | None
    markets: int


class ConfirmUnitIn(BaseModel):
    price_unit_code: str = Field(description="Código de la unidad en que la fuente publica el precio")


class QuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    market_product_id: int
    market: str
    price_date: date
    price: Decimal
    price_min: Decimal | None
    price_max: Decimal | None
    published_at: datetime | None
    fetched_at: datetime
    variation_rate: Decimal | None
    flag: QuoteFlag


class MappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    market_product_id: int
    market: str = Field(min_length=2, max_length=120)
    conversion_factor: FactorIn = Field(
        description="precio por unidad base del producto = precio publicado × factor")
    notes: str | None = Field(None, max_length=300)


class MappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversion_factor: FactorIn | None = None
    notes: str | None = Field(None, max_length=300)
    is_active: bool | None = None


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str = ""
    market_product_id: int
    market_product_name: str = ""
    market: str
    conversion_factor: Decimal
    notes: str | None
    is_active: bool
    confirmed_by_id: int
    updated_at: datetime


class ReferenceOut(BaseModel):
    mapping_id: int
    product_id: int
    product_name: str = ""
    market_product_id: int
    market_product_name: str
    market: str
    quote_id: int | None
    price_date: date | None
    source_price: Decimal | None
    source_unit: str | None
    unit_confirmed: bool
    price_per_base_unit: Decimal | None
    days_old: int | None
    is_stale: bool
    flag: str | None
    variation_rate: Decimal | None
    usable: bool
    reason: str | None
    # Solo con costos.ver:
    unit_cost: Decimal | None = None
    cost_vs_market: Decimal | None = None


@router.get("/estado")
def status(_: CurrentUser = Depends(require(P.MERCADO_VER)), db: Session = Depends(get_db)):
    sources = db.scalars(select(MarketSource).order_by(MarketSource.id)).all()
    runs = db.scalars(select(MarketSyncRun).order_by(MarketSyncRun.id.desc()).limit(15)).all()
    hb = db.get(WorkerHeartbeat, "worker")
    return {
        "fuentes": [SourceOut.model_validate(s) for s in sources],
        "ejecuciones": [RunOut.model_validate(r) for r in runs],
        "proceso_automatico": None if hb is None else {
            "ultimo_latido": hb.last_beat_at, "iniciado": hb.started_at, "info": hb.info,
            "activo": (now_utc() - hb.last_beat_at).total_seconds() < 180},
    }


@router.post("/sincronizar", status_code=202)
def request_sync(fuente: str = Query("sipsa_ciudad", max_length=40),
                 cu: CurrentUser = Depends(require(P.MERCADO_GESTIONAR)), db: Session = Depends(get_db)):
    """Encola la actualización; el proceso `worker` la ejecuta (no bloquea la aplicación)."""
    src = db.scalar(select(MarketSource).where(MarketSource.code == fuente))
    if src is None or not src.is_active:
        raise NotFound("Fuente no encontrada o inactiva")
    src.sync_requested_at = now_utc()
    src.sync_requested_by_id = cu.id
    audit(db, user_id=cu.id, action="mercado.solicitar_sincronizacion", entity="fuente",
          entity_id=src.id, ip=cu.ip)
    db.commit()
    return {"mensaje": "Actualización solicitada. El proceso automático la ejecutará en el próximo "
                       "minuto; consulte el estado para ver el resultado."}


def _external(db: Session, *, q: str | None = None, mp_id: int | None = None) -> list[ExternalProductOut]:
    stmt = (select(MarketProduct, func.max(MarketQuote.price_date), func.count(func.distinct(MarketQuote.market)))
            .join(MarketQuote, MarketQuote.market_product_id == MarketProduct.id, isouter=True)
            .group_by(MarketProduct.id).order_by(MarketProduct.name))
    if q:
        stmt = stmt.where(MarketProduct.name.ilike(f"%{q}%"))
    if mp_id:
        stmt = stmt.where(MarketProduct.id == mp_id)
    return [ExternalProductOut(id=mp.id, source_id=mp.source_id, external_code=mp.external_code,
                               name=mp.name, price_unit_code=mp.price_unit_code,
                               unit_confirmed_at=mp.unit_confirmed_at, latest_price_date=last, markets=n)
            for mp, last, n in db.execute(stmt).all()]


@router.get("/productos-externos", response_model=list[ExternalProductOut])
def external_products(q: str | None = Query(None, max_length=60),
                      _: CurrentUser = Depends(require(P.MERCADO_VER)), db: Session = Depends(get_db)):
    return _external(db, q=q)


@router.patch("/productos-externos/{mp_id}", response_model=ExternalProductOut)
def confirm_unit(mp_id: int, data: ConfirmUnitIn, cu: CurrentUser = Depends(require(P.MERCADO_GESTIONAR)),
                 db: Session = Depends(get_db)):
    mp = db.get(MarketProduct, mp_id)
    if mp is None:
        raise NotFound("Producto externo no encontrado")
    if not db.scalar(select(Unit.id).where(Unit.code == data.price_unit_code)):
        raise AppError("Unidad desconocida", code="unidad_invalida")
    old = mp.price_unit_code
    mp.price_unit_code = data.price_unit_code
    mp.unit_confirmed_by_id = cu.id
    mp.unit_confirmed_at = now_utc()
    audit(db, user_id=cu.id, action="mercado.confirmar_unidad", entity="producto_externo", entity_id=mp.id,
          details={"anterior": old, "nueva": data.price_unit_code}, ip=cu.ip)
    db.commit()
    return _external(db, mp_id=mp.id)[0]


@router.get("/mercados", response_model=list[str])
def markets(_: CurrentUser = Depends(require(P.MERCADO_VER)), db: Session = Depends(get_db)):
    return db.scalars(select(MarketQuote.market).distinct().order_by(MarketQuote.market)).all()


@router.get("/cotizaciones", response_model=Page[QuoteOut])
def quotes(market_product_id: int | None = None, mercado: str | None = Query(None, max_length=120),
           desde: date | None = None, hasta: date | None = None,
           solo_variaciones: bool = False, pg: PageParams = Depends(),
           _: CurrentUser = Depends(require(P.MERCADO_VER)), db: Session = Depends(get_db)):
    stmt = select(MarketQuote)
    if market_product_id:
        stmt = stmt.where(MarketQuote.market_product_id == market_product_id)
    if mercado:
        stmt = stmt.where(MarketQuote.market == mercado)
    if desde:
        stmt = stmt.where(MarketQuote.price_date >= desde)
    if hasta:
        stmt = stmt.where(MarketQuote.price_date <= hasta)
    if solo_variaciones:
        stmt = stmt.where(MarketQuote.flag == QuoteFlag.VARIACION_EXTRAORDINARIA)
    return paginate(db, stmt.order_by(MarketQuote.price_date.desc(), MarketQuote.id.desc()), pg)


def _m_out(db: Session, m: ProductMarketMapping) -> MappingOut:
    return MappingOut.model_validate(m).model_copy(update={
        "product_name": db.get(Product, m.product_id).name, "market_product_name": m.market_product.name})


@router.get("/correspondencias", response_model=list[MappingOut])
def list_mappings(product_id: int | None = None, _: CurrentUser = Depends(require(P.MERCADO_VER)),
                  db: Session = Depends(get_db)):
    stmt = select(ProductMarketMapping).order_by(ProductMarketMapping.product_id)
    if product_id:
        stmt = stmt.where(ProductMarketMapping.product_id == product_id)
    return [_m_out(db, m) for m in db.scalars(stmt)]


@router.post("/correspondencias", response_model=MappingOut, status_code=201)
def create_mapping(data: MappingIn, cu: CurrentUser = Depends(require(P.MERCADO_GESTIONAR)),
                   db: Session = Depends(get_db)):
    if db.get(Product, data.product_id) is None:
        raise NotFound("Producto no encontrado")
    if db.get(MarketProduct, data.market_product_id) is None:
        raise NotFound("Producto externo no encontrado")
    market = data.market.strip().upper()
    if not db.scalar(select(func.count()).select_from(MarketQuote).where(
            MarketQuote.market_product_id == data.market_product_id, MarketQuote.market == market)):
        raise AppError("La fuente no tiene cotizaciones de ese producto en ese mercado",
                       code="mercado_sin_datos")
    if db.scalar(select(ProductMarketMapping.id).where(
            ProductMarketMapping.product_id == data.product_id,
            ProductMarketMapping.market_product_id == data.market_product_id,
            ProductMarketMapping.market == market)):
        raise Conflict("Esa correspondencia ya existe")
    m = ProductMarketMapping(product_id=data.product_id, market_product_id=data.market_product_id,
                             market=market, conversion_factor=data.conversion_factor, notes=data.notes,
                             confirmed_by_id=cu.id)
    db.add(m)
    db.flush()
    audit(db, user_id=cu.id, action="mercado.correspondencia_crear", entity="correspondencia",
          entity_id=m.id, details=data.model_dump(), ip=cu.ip)
    db.commit()
    db.refresh(m)
    return _m_out(db, m)


@router.patch("/correspondencias/{mid}", response_model=MappingOut)
def update_mapping(mid: int, data: MappingUpdate, cu: CurrentUser = Depends(require(P.MERCADO_GESTIONAR)),
                   db: Session = Depends(get_db)):
    m = db.get(ProductMarketMapping, mid)
    if m is None:
        raise NotFound("Correspondencia no encontrada")
    diff = {}
    for k, v in data.model_dump(exclude_unset=True).items():
        if getattr(m, k) != v:
            diff[k] = {"anterior": getattr(m, k), "nuevo": v}
            setattr(m, k, v)
    if diff:
        m.confirmed_by_id = cu.id
        audit(db, user_id=cu.id, action="mercado.correspondencia_actualizar", entity="correspondencia",
              entity_id=m.id, details=diff, ip=cu.ip)
    db.commit()
    db.refresh(m)
    return _m_out(db, m)


@router.get("/referencias", response_model=list[ReferenceOut])
def references(product_id: int | None = None, cu: CurrentUser = Depends(require(P.MERCADO_VER)),
               db: Session = Depends(get_db)):
    """Última cotización por correspondencia, con su fecha y si es utilizable."""
    cfg = get_commercial_settings(db)
    stmt = select(ProductMarketMapping).where(ProductMarketMapping.is_active.is_(True))
    if product_id:
        stmt = stmt.where(ProductMarketMapping.product_id == product_id)
    out = []
    for m in db.scalars(stmt.order_by(ProductMarketMapping.product_id)):
        ref = service.reference_for_mapping(db, m, cfg.stale_quote_days)
        item = ReferenceOut(**asdict(ref), product_name=db.get(Product, m.product_id).name)
        if cu.can(P.COSTOS_VER):
            bal = db.get(StockBalance, m.product_id)
            cost = bal.avg_cost if bal and bal.avg_cost > 0 else None
            item.unit_cost = cost
            item.cost_vs_market = service.cost_vs_market(cost, ref.price_per_base_unit) if ref.usable else None
        out.append(item)
    return out
