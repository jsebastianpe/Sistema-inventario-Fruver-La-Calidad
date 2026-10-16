from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, NotFound
from app.core.timeutil import now_utc
from app.domains.audit.service import audit
from app.domains.auth.deps import CurrentUser
from app.domains.catalog.models import Product, UnitKind
from app.domains.inventory import service as inv
from app.domains.inventory.models import MovementType, StockMovement
from app.domains.purchases import calc
from app.domains.purchases.models import Purchase, PurchaseLine, PurchaseStatus
from app.domains.purchases.schemas import PurchaseIn
from app.domains.settings.service import get_commercial_settings
from app.domains.suppliers.models import Supplier

REF = "compra"


def get_purchase(db: Session, purchase_id: int, *, lock: bool = False) -> Purchase:
    stmt = select(Purchase).where(Purchase.id == purchase_id)
    if lock:
        stmt = stmt.with_for_update()
    p = db.scalar(stmt.execution_options(populate_existing=True))
    if p is None:
        raise NotFound("Compra no encontrada")
    return p


def compute(db: Session, data: PurchaseIn):
    """Valida productos/presentaciones y calcula la compra (sin modificar nada)."""
    supplier = db.get(Supplier, data.supplier_id)
    if supplier is None or not supplier.is_active:
        raise AppError("El proveedor no existe o está inactivo", code="proveedor_invalido")
    cfg = get_commercial_settings(db)
    inputs, rows = [], []
    for i, ln in enumerate(data.lines, 1):
        product = db.get(Product, ln.product_id)
        if product is None or not product.is_active:
            raise AppError(f"Línea {i}: el producto no existe o está inactivo", code="producto_invalido")
        pu = inv.resolve_presentation(db, product, ln.product_unit_id, purpose="compra")
        tax_rate = ln.tax_rate if ln.tax_rate is not None else product.tax_rate
        base_kg = product.base_unit.kg_equivalent if product.base_unit.kind == UnitKind.MASA else None
        inputs.append(calc.LineInput(quantity=ln.quantity, unit_price=ln.unit_price,
                                     line_discount=ln.line_discount, tax_rate=tax_rate,
                                     waste_qty=ln.waste_qty, factor_to_base=pu.factor_to_base,
                                     base_kg_equivalent=base_kg, label=f"Línea {i} ({product.name})"))
        rows.append((ln, pu, tax_rate))
    result = calc.compute(inputs, freight=data.freight, other_costs=data.other_costs,
                          discount=data.discount, method=data.allocation_method.value,
                          tax_in_cost=cfg.purchase_tax_in_cost)
    return rows, result


def preview(db: Session, data: PurchaseIn) -> dict:
    rows, result = compute(db, data)
    lines = []
    for (ln, pu, tax_rate), r in zip(rows, result.lines):
        lines.append({"product_id": ln.product_id, "product_name": db.get(Product, ln.product_id).name,
                      "unit_name": pu.name, "quantity": ln.quantity, "tax_rate": tax_rate,
                      "quantity_base": r.quantity_base, "usable_qty": r.usable_qty, "net_amount": r.net_amount,
                      "allocated_extra": r.allocated_extra, "tax_amount": r.tax_amount,
                      "total_cost": r.total_cost, "unit_cost_base": r.unit_cost_base})
    return {"lines": lines, "subtotal": result.subtotal, "tax_total": result.tax_total, "total": result.total}


def _build_lines(db: Session, purchase: Purchase, data: PurchaseIn) -> None:
    """Recalcula y reemplaza las líneas del borrador."""
    rows, result = compute(db, data)
    for field in ("supplier_id", "purchase_date", "invoice_number", "notes", "freight",
                  "other_costs", "discount", "allocation_method"):
        setattr(purchase, field, getattr(data, field))
    purchase.subtotal, purchase.tax_total, purchase.total = result.subtotal, result.tax_total, result.total
    purchase.lines.clear()
    db.flush()
    for i, ((ln, pu, tax_rate), r) in enumerate(zip(rows, result.lines), 1):
        purchase.lines.append(PurchaseLine(
            line_no=i, product_id=ln.product_id, product_unit_id=pu.id, quantity=ln.quantity,
            unit_price=ln.unit_price, line_discount=ln.line_discount, tax_rate=tax_rate,
            waste_qty=ln.waste_qty, expiry_date=ln.expiry_date, lot_code=ln.lot_code,
            quantity_base=r.quantity_base, usable_qty=r.usable_qty, net_amount=r.net_amount,
            allocated_extra=r.allocated_extra, tax_amount=r.tax_amount, total_cost=r.total_cost,
            unit_cost_base=r.unit_cost_base))


def create_draft(db: Session, data: PurchaseIn, cu: CurrentUser,
                 idempotency_key: str | None) -> tuple[Purchase, bool]:
    if idempotency_key:
        prev = db.scalar(select(Purchase).where(Purchase.idempotency_key == idempotency_key))
        if prev is not None:
            if prev.created_by_id != cu.id:
                raise Conflict("La clave de idempotencia ya fue usada en otra operación")
            return prev, False
    p = Purchase(created_by_id=cu.id, idempotency_key=idempotency_key, status=PurchaseStatus.BORRADOR)
    db.add(p)
    _build_lines(db, p, data)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        if "uq_purchases_invoice" in str(e.orig):
            raise Conflict("Ya existe una compra con ese número de factura para el proveedor") from e
        if idempotency_key and "uq_purchases_idempotency" in str(e.orig):
            prev = db.scalar(select(Purchase).where(Purchase.idempotency_key == idempotency_key))
            return prev, False
        raise
    audit(db, user_id=cu.id, action="compras.crear", entity="compra", entity_id=p.id,
          details={"total": p.total, "lineas": len(p.lines)}, ip=cu.ip)
    db.commit()
    return p, True


def update_draft(db: Session, purchase_id: int, data: PurchaseIn, cu: CurrentUser) -> Purchase:
    p = get_purchase(db, purchase_id, lock=True)
    if p.status != PurchaseStatus.BORRADOR:
        raise Conflict("Solo se pueden modificar compras en borrador", code="estado_invalido")
    _build_lines(db, p, data)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("Ya existe una compra con ese número de factura para el proveedor") from e
    audit(db, user_id=cu.id, action="compras.editar", entity="compra", entity_id=p.id,
          details={"total": p.total}, ip=cu.ip)
    db.commit()
    return p


def confirm(db: Session, purchase_id: int, cu: CurrentUser) -> tuple[Purchase, bool]:
    """Confirma en UNA transacción: entradas de inventario, costo promedio y lotes.

    Idempotente: confirmar una compra ya confirmada no repite movimientos.
    """
    p = get_purchase(db, purchase_id, lock=True)  # serializa confirmaciones concurrentes
    if p.status == PurchaseStatus.CONFIRMADA:
        return p, False
    if p.status != PurchaseStatus.BORRADOR:
        raise Conflict("La compra está anulada", code="estado_invalido")
    # Recalcular con los datos actuales (p. ej. si cambió la configuración de impuestos).
    data = PurchaseIn(
        supplier_id=p.supplier_id, purchase_date=p.purchase_date, invoice_number=p.invoice_number,
        notes=p.notes, freight=p.freight, other_costs=p.other_costs, discount=p.discount,
        allocation_method=p.allocation_method,
        lines=[dict(product_id=ln.product_id, product_unit_id=ln.product_unit_id, quantity=ln.quantity,
                    unit_price=ln.unit_price, line_discount=ln.line_discount, tax_rate=ln.tax_rate,
                    waste_qty=ln.waste_qty, expiry_date=ln.expiry_date, lot_code=ln.lot_code)
               for ln in p.lines])
    _build_lines(db, p, data)
    db.flush()

    # Bloqueo en orden de producto para evitar interbloqueos entre transacciones.
    for pid in sorted({ln.product_id for ln in p.lines}):
        inv.lock_balance(db, pid)
    for ln in p.lines:
        product = db.get(Product, ln.product_id)
        pu = inv.resolve_presentation(db, product, ln.product_unit_id)
        inv.record_inbound(
            db, product=product, movement_type=MovementType.COMPRA, quantity=ln.quantity,
            quantity_base=ln.usable_qty, pu=pu, user_id=cu.id, unit_cost=ln.unit_cost_base,
            reference_type=REF, reference_id=p.id,
            lot=inv.LotSpec(code=ln.lot_code, expiry_date=ln.expiry_date, purchase_line_id=ln.id),
            notes=f"Compra #{p.id}" + (f" factura {p.invoice_number}" if p.invoice_number else ""))
    p.status = PurchaseStatus.CONFIRMADA
    p.confirmed_by_id = cu.id
    p.confirmed_at = now_utc()
    audit(db, user_id=cu.id, action="compras.confirmar", entity="compra", entity_id=p.id,
          details={"total": p.total, "subtotal": p.subtotal}, ip=cu.ip)
    db.commit()
    return p, True


def cancel(db: Session, purchase_id: int, reason: str, cu: CurrentUser) -> Purchase:
    """Anula. Si estaba confirmada, retira la mercancía con movimientos compensatorios
    al mismo costo de entrada. Si ya se vendió parte, la anulación se rechaza y debe
    registrarse una devolución a proveedor o un ajuste."""
    p = get_purchase(db, purchase_id, lock=True)
    if p.status == PurchaseStatus.ANULADA:
        raise Conflict("La compra ya está anulada", code="estado_invalido")
    if p.status == PurchaseStatus.CONFIRMADA:
        movements = db.scalars(select(StockMovement).where(
            StockMovement.reference_type == REF, StockMovement.reference_id == p.id,
            StockMovement.movement_type == MovementType.COMPRA).order_by(StockMovement.product_id)).all()
        cfg = get_commercial_settings(db)
        try:
            for mv in movements:
                inv.reverse(db, mv, movement_type=MovementType.ANULACION_COMPRA, user_id=cu.id,
                            reason=reason, allow_negative=cfg.allow_negative_stock)
        except inv.InsufficientStock as e:
            db.rollback()
            raise Conflict(
                "No se puede anular: parte de la mercancía de esta compra ya salió del inventario. "
                "Registre una devolución a proveedor o un ajuste autorizado.",
                code="anulacion_imposible", details=e.details) from e
    previous = p.status
    p.status = PurchaseStatus.ANULADA
    p.cancelled_by_id = cu.id
    p.cancelled_at = now_utc()
    p.cancel_reason = reason
    audit(db, user_id=cu.id, action="compras.anular", entity="compra", entity_id=p.id,
          details={"estado_anterior": previous, "motivo": reason, "total": p.total}, ip=cu.ip)
    db.commit()
    return p
