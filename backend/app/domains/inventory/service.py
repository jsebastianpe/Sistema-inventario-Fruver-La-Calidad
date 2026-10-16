"""Motor de inventario.

Reglas:
* Toda modificación de existencias pasa por `_apply`, dentro de la transacción del llamador,
  con la fila de `stock_balances` bloqueada (SELECT … FOR UPDATE). Dos operaciones
  simultáneas sobre el mismo producto se serializan; nunca se pierden actualizaciones.
* Valoración: costo promedio ponderado móvil.
    entrada:  nuevo_prom = (Q·prom + q·c) / (Q + q)     (si Q ≤ 0, nuevo_prom = c)
    salida:   el promedio no cambia; la salida se valora al promedio vigente.
    reversión de una entrada (p. ej. anular compra): se retira al costo original c:
              nuevo_prom = (Q·prom − q·c) / (Q − q)     (si Q − q ≤ 0 o el resultado < 0, se conserva)
* Lotes: cada entrada crea un lote; las salidas consumen FEFO (vence primero, sale primero).
* Los movimientos son inmutables; se corrigen con un movimiento compensatorio.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, NotFound
from app.core.numbers import ZERO, cost, money, qty
from app.domains.audit.service import audit
from app.domains.catalog.models import Product, ProductUnit
from app.domains.inventory.models import (
    INBOUND, OUTBOUND, LotAllocation, Lot, MovementType, StockBalance, StockMovement,
)

MANUAL_TYPES = {
    MovementType.INICIAL, MovementType.MERMA, MovementType.AJUSTE_ENTRADA,
    MovementType.AJUSTE_SALIDA, MovementType.DEVOLUCION_CLIENTE, MovementType.DEVOLUCION_PROVEEDOR,
}


class InsufficientStock(Conflict):
    code = "existencias_insuficientes"


@dataclass
class LotSpec:
    code: str | None = None
    expiry_date: date | None = None
    purchase_line_id: int | None = None


def lock_balance(db: Session, product_id: int) -> StockBalance:
    db.execute(pg_insert(StockBalance).values(product_id=product_id)
               .on_conflict_do_nothing(index_elements=["product_id"]))
    return db.execute(
        select(StockBalance).where(StockBalance.product_id == product_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def resolve_presentation(db: Session, product: Product, product_unit_id: int | None, *,
                         purpose: str | None = None) -> ProductUnit:
    if product_unit_id is None:
        pu = next((p for p in product.presentations if p.is_base), None)
    else:
        pu = db.get(ProductUnit, product_unit_id)
    if pu is None or pu.product_id != product.id:
        raise AppError("La presentación no corresponde al producto", code="presentacion_invalida")
    if not pu.is_active:
        raise AppError(f"La presentación '{pu.name}' está inactiva", code="presentacion_invalida")
    if purpose == "compra" and not pu.for_purchase:
        raise AppError(f"La presentación '{pu.name}' no está habilitada para compras",
                       code="presentacion_invalida")
    if purpose == "venta" and not pu.for_sale:
        raise AppError(f"La presentación '{pu.name}' no está habilitada para ventas",
                       code="presentacion_invalida")
    return pu


def to_base(quantity: Decimal, pu: ProductUnit) -> Decimal:
    """Conversión en un solo paso a la unidad base, redondeada a 4 decimales."""
    if quantity is None or quantity <= 0:
        raise AppError("La cantidad debe ser mayor que cero", code="cantidad_invalida")
    base = qty(quantity * pu.factor_to_base)
    if base <= 0:
        raise AppError("La cantidad es demasiado pequeña para registrarse", code="cantidad_invalida")
    return base


def _consume_lots(db: Session, product_id: int, amount: Decimal, *,
                  preferred: list[tuple[int, Decimal]] | None = None) -> list[tuple[int, Decimal]]:
    """Descuenta `amount` de los lotes abiertos. Devuelve [(lote, cantidad_descontada)]."""
    taken: list[tuple[int, Decimal]] = []
    remaining = amount
    lots_q = (select(Lot).where(Lot.product_id == product_id, Lot.remaining_qty > 0)
              .order_by(Lot.expiry_date.asc().nulls_last(), Lot.received_at, Lot.id).with_for_update())
    lots = {lot.id: lot for lot in db.scalars(lots_q)}
    order = [lid for lid, _ in (preferred or []) if lid in lots] + \
            [lid for lid in lots if lid not in {p for p, _ in (preferred or [])}]
    caps = dict(preferred or [])
    for lid in order:
        if remaining <= 0:
            break
        lot = lots[lid]
        cap = min(lot.remaining_qty, caps.get(lid, lot.remaining_qty))
        take = min(cap, remaining)
        if take > 0:
            lot.remaining_qty -= take
            remaining -= take
            taken.append((lid, take))
    return taken


def _apply(db: Session, *, product: Product, movement_type: MovementType, quantity: Decimal,
           quantity_base: Decimal, pu: ProductUnit | None, user_id: int | None,
           unit_cost: Decimal | None = None, allow_negative: bool = False,
           reference_type: str | None = None, reference_id: int | None = None,
           reason: str | None = None, notes: str | None = None,
           lot: LotSpec | None = None, idempotency_key: str | None = None,
           reverses: StockMovement | None = None) -> StockMovement:
    """Aplica un movimiento. `quantity_base` es siempre positivo; el signo lo da el tipo."""
    if quantity_base <= 0:
        raise AppError("La cantidad debe ser mayor que cero", code="cantidad_invalida")
    inbound = movement_type in INBOUND or (
        movement_type == MovementType.REVERSION and reverses is not None and reverses.quantity_base < 0)
    bal = lock_balance(db, product.id)
    q_before, avg_before = bal.quantity, bal.avg_cost

    allocations: list[tuple[int, Decimal]] = []
    if inbound:
        c = cost(unit_cost if unit_cost is not None else avg_before)
        if c < 0:
            raise AppError("El costo unitario no puede ser negativo", code="costo_invalido")
        new_q = q_before + quantity_base
        new_avg = c if q_before <= 0 else cost((q_before * avg_before + quantity_base * c) / new_q)
        if reverses is not None and reverses.allocations:
            # Devolver exactamente a los lotes de los que salió.
            for a in reverses.allocations:
                lot_row = db.get(Lot, a.lot_id, with_for_update=True)
                back = -a.quantity
                lot_row.remaining_qty = min(lot_row.initial_qty, lot_row.remaining_qty + back)
                allocations.append((a.lot_id, back))
        else:
            spec = lot or LotSpec()
            new_lot = Lot(product_id=product.id, code=spec.code, expiry_date=spec.expiry_date,
                          purchase_line_id=spec.purchase_line_id, initial_qty=quantity_base,
                          remaining_qty=quantity_base)
            db.add(new_lot)
            db.flush()
            allocations.append((new_lot.id, quantity_base))
        signed = quantity_base
    else:
        new_q = q_before - quantity_base
        if new_q < 0 and not allow_negative:
            raise InsufficientStock(
                f"Existencias insuficientes de '{product.name}': disponible "
                f"{q_before.normalize():f} {product.base_unit.code}, solicitado "
                f"{quantity_base.normalize():f} {product.base_unit.code}",
                details={"producto_id": product.id, "disponible": str(q_before),
                         "solicitado": str(quantity_base)})
        if unit_cost is not None:  # retiro a costo específico (reversión de una entrada)
            c = cost(unit_cost)
            new_avg = avg_before
            if new_q > 0:
                candidate = (q_before * avg_before - quantity_base * c) / new_q
                if candidate >= 0:
                    new_avg = cost(candidate)
        else:
            c = avg_before
            new_avg = avg_before
        preferred = [(a.lot_id, a.quantity) for a in reverses.allocations] if reverses else None
        allocations = [(lid, -t) for lid, t in _consume_lots(db, product.id, quantity_base,
                                                              preferred=preferred)]
        signed = -quantity_base

    bal.quantity = qty(new_q)
    bal.avg_cost = new_avg
    mv = StockMovement(
        product_id=product.id, movement_type=movement_type, product_unit_id=pu.id if pu else None,
        quantity=qty(quantity), quantity_base=signed, unit_cost=c,
        total_cost=money(quantity_base * c), balance_after=bal.quantity, avg_cost_after=new_avg,
        reference_type=reference_type, reference_id=reference_id, reason=reason, notes=notes,
        reverses_movement_id=reverses.id if reverses else None, idempotency_key=idempotency_key,
        user_id=user_id,
    )
    db.add(mv)
    db.flush()
    for lid, amount in allocations:
        db.add(LotAllocation(movement_id=mv.id, lot_id=lid, quantity=amount))
    db.flush()
    return mv


def record_inbound(db: Session, **kw) -> StockMovement:
    assert kw["movement_type"] in INBOUND
    return _apply(db, **kw)


def record_outbound(db: Session, **kw) -> StockMovement:
    assert kw["movement_type"] in OUTBOUND
    return _apply(db, **kw)


def reverse(db: Session, original: StockMovement, *, movement_type: MovementType, user_id: int | None,
            reason: str, reference_type: str | None = None, reference_id: int | None = None,
            allow_negative: bool = False) -> StockMovement:
    """Compensa `original` con un movimiento de signo contrario al mismo costo unitario."""
    if db.scalar(select(exists().where(StockMovement.reverses_movement_id == original.id))):
        raise Conflict("Este movimiento ya fue revertido", code="ya_revertido")
    if original.reverses_movement_id is not None:
        raise Conflict("No se puede revertir una reversión; registre un ajuste", code="reversion_invalida")
    product = db.get(Product, original.product_id)
    pu = db.get(ProductUnit, original.product_unit_id) if original.product_unit_id else None
    return _apply(
        db, product=product, movement_type=movement_type, quantity=original.quantity,
        quantity_base=abs(original.quantity_base), pu=pu, user_id=user_id,
        unit_cost=original.unit_cost, allow_negative=allow_negative,
        reference_type=reference_type or original.reference_type,
        reference_id=reference_id or original.reference_id, reason=reason, reverses=original,
    )


# ------------------------------------------------------------- operaciones manuales

def register_manual(db: Session, *, product_id: int, movement_type: MovementType, quantity: Decimal,
                    product_unit_id: int | None, unit_cost: Decimal | None, reason: str | None,
                    notes: str | None, lot: LotSpec | None, user_id: int, ip: str | None,
                    idempotency_key: str, allow_negative: bool) -> tuple[StockMovement, bool]:
    """Devuelve (movimiento, creado). Si la clave de idempotencia ya existe, no repite la operación."""
    if movement_type not in MANUAL_TYPES:
        raise AppError("Tipo de movimiento no permitido para registro manual", code="tipo_invalido")
    previous = db.scalar(select(StockMovement).where(StockMovement.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.user_id != user_id or previous.product_id != product_id:
            raise Conflict("La clave de idempotencia ya fue usada en otra operación")
        return previous, False

    product = db.get(Product, product_id)
    if product is None:
        raise NotFound("Producto no encontrado")
    if not product.is_active:
        raise AppError("El producto está inactivo", code="producto_inactivo")
    if movement_type != MovementType.INICIAL and not (reason and reason.strip()):
        raise AppError("Debe indicar el motivo del movimiento", code="motivo_requerido")
    if movement_type == MovementType.INICIAL:
        if db.scalar(select(exists().where(StockMovement.product_id == product_id))):
            raise Conflict("El producto ya tiene movimientos; use un ajuste de entrada",
                           code="inicial_no_permitido")
        if unit_cost is None:
            raise AppError("La existencia inicial requiere el costo unitario", code="costo_requerido")
    if unit_cost is not None and movement_type not in INBOUND:
        raise AppError("El costo unitario solo se indica en entradas", code="costo_no_aplica")

    pu = resolve_presentation(db, product, product_unit_id)
    base = to_base(quantity, pu)
    # El costo se informa por la presentación usada; se convierte a costo por unidad base.
    base_cost = cost(unit_cost / pu.factor_to_base) if unit_cost is not None else None
    kw = dict(db=db, product=product, movement_type=movement_type, quantity=quantity,
              quantity_base=base, pu=pu, user_id=user_id, unit_cost=base_cost, reason=reason,
              notes=notes, idempotency_key=idempotency_key, reference_type="manual",
              allow_negative=allow_negative)
    mv = record_inbound(lot=lot, **kw) if movement_type in INBOUND else record_outbound(**kw)
    audit(db, user_id=user_id, action=f"inventario.{movement_type.value}", entity="movimiento",
          entity_id=mv.id, details={"producto_id": product_id, "cantidad_base": base,
                                    "motivo": reason}, ip=ip)
    db.commit()
    return mv, True


def reverse_manual(db: Session, movement_id: int, *, reason: str, user_id: int, ip: str | None,
                   allow_negative: bool) -> StockMovement:
    original = db.get(StockMovement, movement_id)
    if original is None:
        raise NotFound("Movimiento no encontrado")
    if original.movement_type not in MANUAL_TYPES:
        raise AppError("Los movimientos de compras y ventas se corrigen anulando el documento",
                       code="reversion_invalida")
    if not reason.strip():
        raise AppError("Debe indicar el motivo de la reversión", code="motivo_requerido")
    mv = reverse(db, original, movement_type=MovementType.REVERSION, user_id=user_id, reason=reason,
                 allow_negative=allow_negative)
    audit(db, user_id=user_id, action="inventario.reversion", entity="movimiento", entity_id=mv.id,
          details={"revierte": original.id, "motivo": reason}, ip=ip)
    db.commit()
    return mv


def balance_of(db: Session, product_id: int) -> StockBalance:
    bal = db.get(StockBalance, product_id)
    return bal or StockBalance(product_id=product_id, quantity=ZERO, avg_cost=ZERO)
