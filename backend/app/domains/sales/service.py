"""Ventas.

* El precio de cada línea se toma del precio vigente del producto en el servidor
  (precio por unidad base × factor de la presentación) y queda guardado como instantánea.
* Los precios de venta incluyen impuestos; el impuesto contenido es  neto − neto / (1 + tasa).
* El costo de la línea es el costo promedio ponderado vigente al momento de vender.
* Margen bruto de la línea = (neto − impuesto) − costo.  No es utilidad neta.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.numbers import ONE, ZERO, money, rate
from app.core.timeutil import now_utc
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser
from app.domains.auth.permissions import P
from app.domains.catalog.models import Product
from app.domains.inventory import service as inv
from app.domains.inventory.models import MovementType, StockMovement
from app.domains.sales.models import Sale, SaleLine, SaleStatus
from app.domains.sales.schemas import SaleIn
from app.domains.settings.service import get_commercial_settings

REF = "venta"


def get_sale(db: Session, sale_id: int, *, lock: bool = False) -> Sale:
    stmt = select(Sale).where(Sale.id == sale_id)
    if lock:
        stmt = stmt.with_for_update()
    s = db.scalar(stmt.execution_options(populate_existing=True))
    if s is None:
        raise NotFound("Venta no encontrada")
    return s


def _existing(db: Session, key: str, cu: CurrentUser) -> Sale | None:
    prev = db.scalar(select(Sale).where(Sale.idempotency_key == key))
    if prev is not None and prev.created_by_id != cu.id:
        raise Conflict("La clave de idempotencia ya fue usada en otra operación")
    return prev


def price_lines(db: Session, data: SaleIn, cu: CurrentUser, cfg) -> dict:
    """Calcula precios y totales de una venta (misma regla para previsualizar y para registrar)."""
    priced = []
    for i, ln in enumerate(data.lines, 1):
        product = db.get(Product, ln.product_id)
        if product is None or not product.is_active:
            raise AppError(f"Línea {i}: el producto no existe o está inactivo", code="producto_invalido")
        if product.current_price is None or product.current_price <= 0:
            raise AppError(f"'{product.name}' no tiene precio de venta aprobado", code="sin_precio")
        pu = inv.resolve_presentation(db, product, ln.product_unit_id, purpose="venta")
        base = inv.to_base(ln.quantity, pu)
        unit_price = money(product.current_price * pu.factor_to_base)
        gross = money(ln.quantity * unit_price)
        discount = money(ln.discount)
        if discount > gross:
            raise AppError(f"Línea {i}: el descuento supera el valor de la línea", code="descuento_invalido")
        net = gross - discount
        tax = money(net - net / (ONE + product.tax_rate))
        priced.append({"ln": ln, "product": product, "pu": pu, "base": base, "unit_price": unit_price,
                       "gross": gross, "discount": discount, "net": net, "tax": tax})
    gross_total = sum((x["gross"] for x in priced), ZERO)
    discount_total = sum((x["discount"] for x in priced), ZERO)
    disc_rate = discount_total / gross_total if gross_total > 0 else ZERO
    authorized = disc_rate <= cfg.employee_max_discount_rate or cu.can(P.VENTAS_DESCUENTO_AMPLIADO)
    return {"lines": priced, "gross_total": gross_total, "discount_total": discount_total,
            "total": gross_total - discount_total, "tax_total": sum((x["tax"] for x in priced), ZERO),
            "discount_rate": rate(disc_rate), "discount_authorized": authorized}


def preview(db: Session, data: SaleIn, cu: CurrentUser) -> dict:
    cfg = get_commercial_settings(db)
    r = price_lines(db, data, cu, cfg)
    out_lines = []
    for x in r["lines"]:
        bal = inv.balance_of(db, x["product"].id)
        out_lines.append({
            "product_id": x["product"].id, "product_name": x["product"].name, "unit_name": x["pu"].name,
            "quantity": x["ln"].quantity, "quantity_base": x["base"], "unit_price": x["unit_price"],
            "gross_amount": x["gross"], "discount": x["discount"], "net_amount": x["net"],
            "tax_amount": x["tax"], "available": bal.quantity,
            "stock_ok": cfg.allow_negative_stock or bal.quantity >= x["base"]})
    keys = ("gross_total", "discount_total", "total", "tax_total", "discount_rate", "discount_authorized")
    return {"lines": out_lines, **{k: r[k] for k in keys},
            "max_discount_rate": cfg.employee_max_discount_rate}


def create_sale(db: Session, data: SaleIn, cu: CurrentUser, idempotency_key: str) -> tuple[Sale, bool]:
    prev = _existing(db, idempotency_key, cu)
    if prev is not None:
        return prev, False
    cfg = get_commercial_settings(db)
    r = price_lines(db, data, cu, cfg)
    if not r["discount_authorized"]:
        raise Forbidden(
            f"El descuento ({r['discount_rate'] * 100:.2f} %) supera el máximo autorizado "
            f"({cfg.employee_max_discount_rate * 100:.2f} %). Solicite autorización.",
            code="descuento_no_autorizado")
    gross_total, discount_total = r["gross_total"], r["discount_total"]
    priced = r["lines"]

    sale = Sale(status=SaleStatus.CONFIRMADA, customer_name=data.customer_name,
                payment_method=data.payment_method, reference=data.reference, notes=data.notes,
                gross_total=gross_total, discount_total=discount_total, total=ZERO, tax_total=ZERO,
                cost_total=ZERO, idempotency_key=idempotency_key, created_by_id=cu.id)
    db.add(sale)
    try:
        db.flush()
    except IntegrityError:
        # Solicitud repetida que llegó al mismo tiempo: se devuelve la venta ya registrada.
        db.rollback()
        prev = _existing(db, idempotency_key, cu)
        if prev is None:
            raise
        return prev, False

    for pid in sorted({x["product"].id for x in priced}):  # orden fijo → sin interbloqueos
        inv.lock_balance(db, pid)

    tax_total = cost_total = ZERO
    for i, x in enumerate(priced, 1):
        ln, product, pu, base = x["ln"], x["product"], x["pu"], x["base"]
        unit_price, gross, discount, net, tax = x["unit_price"], x["gross"], x["discount"], x["net"], x["tax"]
        mv = inv.record_outbound(
            db, product=product, movement_type=MovementType.VENTA, quantity=ln.quantity,
            quantity_base=base, pu=pu, user_id=cu.id, reference_type=REF, reference_id=sale.id,
            allow_negative=cfg.allow_negative_stock)
        sale.lines.append(SaleLine(
            line_no=i, product_id=product.id, product_unit_id=pu.id, product_name=product.name,
            unit_name=pu.name, quantity=ln.quantity, quantity_base=base, unit_price=unit_price,
            gross_amount=gross, discount=discount, net_amount=net, tax_rate=product.tax_rate,
            tax_amount=tax, unit_cost_base=mv.unit_cost, cost_amount=mv.total_cost))
        tax_total += tax
        cost_total += mv.total_cost
    sale.total = gross_total - discount_total
    sale.tax_total = tax_total
    sale.cost_total = cost_total
    audit(db, user_id=cu.id, action="ventas.registrar", entity="venta", entity_id=sale.id,
          details={"total": sale.total, "lineas": len(priced)}, ip=cu.ip)
    db.commit()
    return sale, True


def cancel_sale(db: Session, sale_id: int, reason: str, cu: CurrentUser) -> Sale:
    """Anula una venta: devuelve la mercancía al inventario al mismo costo con que salió."""
    sale = get_sale(db, sale_id, lock=True)
    if sale.status == SaleStatus.ANULADA:
        raise Conflict("La venta ya está anulada", code="estado_invalido")
    movements = db.scalars(select(StockMovement).where(
        StockMovement.reference_type == REF, StockMovement.reference_id == sale.id,
        StockMovement.movement_type == MovementType.VENTA).order_by(StockMovement.product_id)).all()
    for mv in movements:
        inv.reverse(db, mv, movement_type=MovementType.ANULACION_VENTA, user_id=cu.id, reason=reason)
    sale.status = SaleStatus.ANULADA
    sale.cancelled_by_id = cu.id
    sale.cancelled_at = now_utc()
    sale.cancel_reason = reason
    audit(db, user_id=cu.id, action="ventas.anular", entity="venta", entity_id=sale.id,
          details={"motivo": reason, "total": sale.total}, ip=cu.ip)
    db.commit()
    return sale


def gross_margin(sale: Sale) -> Decimal:
    return money(sale.total - sale.tax_total - sale.cost_total)
