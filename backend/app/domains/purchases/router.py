from datetime import date

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.jsonsafe import exact
from app.core.pagination import Page, PageParams, paginate
from app.core.schemas import IdempotencyKey
from app.domains.auth.deps import CurrentUser, require
from app.domains.auth.permissions import P
from app.domains.catalog.models import Product, ProductUnit
from app.domains.purchases import service
from app.domains.purchases.models import Purchase, PurchaseLine, PurchaseStatus
from app.domains.purchases.schemas import CancelIn, PurchaseIn, PurchaseLineOut, PurchaseOut
from app.domains.suppliers.models import Supplier

router = APIRouter(prefix="/compras", tags=["Compras"])


def _out(db: Session, p: Purchase) -> PurchaseOut:
    out = PurchaseOut.model_validate(p)
    lines = []
    for ln, lo in zip(p.lines, out.lines):
        lines.append(lo.model_copy(update={"product_name": db.get(Product, ln.product_id).name,
                                           "unit_name": db.get(ProductUnit, ln.product_unit_id).name}))
    return out.model_copy(update={"supplier_name": db.get(Supplier, p.supplier_id).name, "lines": lines})


def _replay(response: Response, created: bool, status: int = 201):
    response.status_code = status if created else 200
    if not created:
        response.headers["Idempotent-Replay"] = "true"


@router.get("", response_model=Page[PurchaseOut])
def list_purchases(proveedor_id: int | None = None, producto_id: int | None = None,
                   estado: PurchaseStatus | None = None, desde: date | None = None,
                   hasta: date | None = None, factura: str | None = None, pg: PageParams = Depends(),
                   _: CurrentUser = Depends(require(P.COMPRAS_VER)), db: Session = Depends(get_db)):
    stmt = select(Purchase)
    if proveedor_id:
        stmt = stmt.where(Purchase.supplier_id == proveedor_id)
    if producto_id:
        stmt = stmt.where(exists().where(PurchaseLine.purchase_id == Purchase.id,
                                         PurchaseLine.product_id == producto_id))
    if estado:
        stmt = stmt.where(Purchase.status == estado)
    if desde:
        stmt = stmt.where(Purchase.purchase_date >= desde)
    if hasta:
        stmt = stmt.where(Purchase.purchase_date <= hasta)
    if factura:
        stmt = stmt.where(Purchase.invoice_number == factura)
    page = paginate(db, stmt.order_by(Purchase.purchase_date.desc(), Purchase.id.desc()), pg)
    page["items"] = [_out(db, p) for p in page["items"]]
    return page


@router.get("/{purchase_id}", response_model=PurchaseOut)
def get_purchase(purchase_id: int, _: CurrentUser = Depends(require(P.COMPRAS_VER)),
                 db: Session = Depends(get_db)):
    return _out(db, service.get_purchase(db, purchase_id))


@router.post("/previsualizar")
def preview_purchase(data: PurchaseIn, _: CurrentUser = Depends(require(P.COMPRAS_REGISTRAR)),
                     db: Session = Depends(get_db)):
    """Calcula totales, reparto y costo efectivo con las reglas del servidor, sin guardar."""
    return exact(service.preview(db, data))


@router.post("", response_model=PurchaseOut, status_code=201)
def create_purchase(data: PurchaseIn, response: Response,
                    idempotency_key: IdempotencyKey | None = Header(None, alias="Idempotency-Key"),
                    cu: CurrentUser = Depends(require(P.COMPRAS_REGISTRAR)), db: Session = Depends(get_db)):
    p, created = service.create_draft(db, data, cu, idempotency_key)
    _replay(response, created)
    return _out(db, p)


@router.put("/{purchase_id}", response_model=PurchaseOut)
def update_purchase(purchase_id: int, data: PurchaseIn,
                    cu: CurrentUser = Depends(require(P.COMPRAS_REGISTRAR)), db: Session = Depends(get_db)):
    return _out(db, service.update_draft(db, purchase_id, data, cu))


@router.post("/{purchase_id}/confirmar", response_model=PurchaseOut)
def confirm_purchase(purchase_id: int, response: Response,
                     cu: CurrentUser = Depends(require(P.COMPRAS_CONFIRMAR)), db: Session = Depends(get_db)):
    p, done = service.confirm(db, purchase_id, cu)
    if not done:
        response.headers["Idempotent-Replay"] = "true"
    return _out(db, p)


@router.post("/{purchase_id}/anular", response_model=PurchaseOut)
def cancel_purchase(purchase_id: int, data: CancelIn,
                    cu: CurrentUser = Depends(require(P.COMPRAS_ANULAR)), db: Session = Depends(get_db)):
    return _out(db, service.cancel(db, purchase_id, data.reason, cu))
