from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Forbidden
from app.core.jsonsafe import exact
from app.core.pagination import Page, PageParams, paginate
from app.core.schemas import IdempotencyKey
from app.core.timeutil import local_day_bounds, today_local
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.models import User
from app.domains.auth.permissions import P
from app.domains.catalog import service as catalog_service
from app.domains.catalog.models import Product
from app.domains.catalog.schemas import ProductCostOut, ProductOut
from app.domains.sales import service
from app.domains.settings.service import get_commercial_settings
from app.domains.sales.models import Sale, SaleLine, SaleStatus
from app.domains.sales.schemas import CancelIn, SaleIn, SaleOut

router = APIRouter(prefix="/ventas", tags=["Ventas"])


def _out(db: Session, s: Sale, cu: CurrentUser) -> SaleOut:
    out = SaleOut.model_validate(s)
    upd = {"created_by_name": db.get(User, s.created_by_id).full_name}
    if cu.can(P.COSTOS_VER):
        upd["gross_margin"] = service.gross_margin(s)
    else:
        upd["cost_total"] = None
        upd["lines"] = [ln.model_copy(update={"unit_cost_base": None, "cost_amount": None})
                        for ln in out.lines]
    return out.model_copy(update=upd)


def _can_see_all(cu: CurrentUser) -> bool:
    return cu.can(P.REPORTES_FINANCIEROS) or cu.can(P.VENTAS_ANULAR)


@router.post("/previsualizar")
def preview_sale(data: SaleIn, cu: CurrentUser = Depends(require(P.VENTAS_REGISTRAR)),
                 db: Session = Depends(get_db)):
    """Calcula precios y totales con las reglas del servidor, sin registrar nada."""
    return exact(service.preview(db, data, cu))


@router.get("/frecuentes", response_model=list[ProductCostOut | ProductOut])
def frequent_products(limite: int = Query(12, ge=1, le=30), dias: int = Query(30, ge=1, le=365),
                      cu: CurrentUser = Depends(require(P.VENTAS_REGISTRAR)), db: Session = Depends(get_db)):
    """Productos activos con precio más vendidos (por número de líneas) en los últimos `dias`."""
    lo, _ = local_day_bounds(today_local() - timedelta(days=dias - 1), today_local())
    n = func.count(SaleLine.id)
    ids = db.scalars(
        select(Product.id).join(SaleLine, SaleLine.product_id == Product.id)
        .join(Sale, Sale.id == SaleLine.sale_id)
        .where(Sale.status == SaleStatus.CONFIRMADA, Sale.sold_at >= lo, Product.is_active.is_(True),
               Product.current_price > 0)
        .group_by(Product.id).order_by(n.desc(), func.min(Product.name)).limit(limite)).all()
    cfg = get_commercial_settings(db)
    return [catalog_service.to_out(db, db.get(Product, pid), cu, cfg) for pid in ids]


@router.post("", response_model=SaleOut, status_code=201)
def create_sale(data: SaleIn, response: Response,
                idempotency_key: IdempotencyKey = Header(..., alias="Idempotency-Key"),
                cu: CurrentUser = Depends(require(P.VENTAS_REGISTRAR)), db: Session = Depends(get_db)):
    sale, created = service.create_sale(db, data, cu, idempotency_key)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return _out(db, sale, cu)


@router.get("", response_model=Page[SaleOut])
def list_sales(desde: date | None = None, hasta: date | None = None,
               estado: SaleStatus | None = None, usuario_id: int | None = None,
               producto_id: int | None = None, q: str | None = Query(None, max_length=60),
               pg: PageParams = Depends(), cu: CurrentUser = Depends(require(P.VENTAS_VER)),
               db: Session = Depends(get_db)):
    """Los empleados solo ven sus propias ventas."""
    stmt = select(Sale)
    if not _can_see_all(cu):
        stmt = stmt.where(Sale.created_by_id == cu.id)
    elif usuario_id:
        stmt = stmt.where(Sale.created_by_id == usuario_id)
    if desde or hasta:
        lo, hi = local_day_bounds(desde or date(2000, 1, 1), hasta or today_local())
        stmt = stmt.where(Sale.sold_at >= lo, Sale.sold_at < hi)
    if estado:
        stmt = stmt.where(Sale.status == estado)
    if producto_id:
        stmt = stmt.where(Sale.lines.any(SaleLine.product_id == producto_id))
    if q:
        stmt = stmt.where((Sale.reference == q) | (Sale.customer_name.ilike(f"%{q}%")))
    page = paginate(db, stmt.order_by(Sale.sold_at.desc(), Sale.id.desc()), pg)
    page["items"] = [_out(db, s, cu) for s in page["items"]]
    return page


@router.get("/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: int, cu: CurrentUser = Depends(require(P.VENTAS_VER)), db: Session = Depends(get_db)):
    s = service.get_sale(db, sale_id)
    if not _can_see_all(cu) and s.created_by_id != cu.id:
        raise Forbidden("No puede consultar ventas de otros usuarios")
    return _out(db, s, cu)


@router.post("/{sale_id}/anular", response_model=SaleOut)
def cancel_sale(sale_id: int, data: CancelIn, cu: CurrentUser = Depends(require(P.VENTAS_ANULAR)),
                db: Session = Depends(get_db)):
    return _out(db, service.cancel_sale(db, sale_id, data.reason, cu), cu)
