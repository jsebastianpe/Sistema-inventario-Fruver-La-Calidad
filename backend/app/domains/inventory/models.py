import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.types import Money, Quantity, UnitCost, str_enum


class MovementType(str, enum.Enum):
    INICIAL = "inicial"
    COMPRA = "compra"
    VENTA = "venta"
    DEVOLUCION_CLIENTE = "devolucion_cliente"
    DEVOLUCION_PROVEEDOR = "devolucion_proveedor"
    MERMA = "merma"
    AJUSTE_ENTRADA = "ajuste_entrada"
    AJUSTE_SALIDA = "ajuste_salida"
    ANULACION_COMPRA = "anulacion_compra"
    ANULACION_VENTA = "anulacion_venta"
    REVERSION = "reversion"


INBOUND = {MovementType.INICIAL, MovementType.COMPRA, MovementType.DEVOLUCION_CLIENTE,
           MovementType.AJUSTE_ENTRADA, MovementType.ANULACION_VENTA}
OUTBOUND = {MovementType.VENTA, MovementType.DEVOLUCION_PROVEEDOR, MovementType.MERMA,
            MovementType.AJUSTE_SALIDA, MovementType.ANULACION_COMPRA}


class StockBalance(Base):
    """Existencia física y costo promedio ponderado vigente, por producto.

    Es la fila que se bloquea (SELECT … FOR UPDATE) en cada movimiento.
    """
    __tablename__ = "stock_balances"
    __table_args__ = (CheckConstraint("avg_cost >= 0", name="costo_no_negativo"),)

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"),
                                            primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Quantity, default=Decimal(0), server_default="0")
    avg_cost: Mapped[Decimal] = mapped_column(UnitCost, default=Decimal(0), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Lot(Base):
    """Lote recibido. Sirve para controlar vencimientos (salidas FEFO); la valoración es
    por costo promedio ponderado, no por lote."""
    __tablename__ = "lots"
    __table_args__ = (
        CheckConstraint("remaining_qty >= 0", name="saldo_no_negativo"),
        CheckConstraint("remaining_qty <= initial_qty", name="saldo_maximo"),
        Index("ix_lots_open", "product_id", "expiry_date", postgresql_where=text("remaining_qty > 0")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    code: Mapped[str | None] = mapped_column(String(40))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expiry_date: Mapped[date | None] = mapped_column(Date)
    purchase_line_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_lines.id"))
    initial_qty: Mapped[Decimal] = mapped_column(Quantity)
    remaining_qty: Mapped[Decimal] = mapped_column(Quantity)


class StockMovement(Base):
    """Movimiento de inventario. INMUTABLE: los errores se corrigen con un movimiento
    compensatorio (reverses_movement_id), nunca con UPDATE/DELETE."""
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="cantidad_positiva"),
        CheckConstraint("quantity_base <> 0", name="cantidad_base_no_cero"),
        CheckConstraint("unit_cost >= 0", name="costo_no_negativo"),
        Index("ix_stock_movements_product_time", "product_id", "created_at"),
        Index("ix_stock_movements_reference", "reference_type", "reference_id"),
        Index("uq_stock_movements_idempotency", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
        Index("uq_stock_movements_reverses", "reverses_movement_id", unique=True,
              postgresql_where=text("reverses_movement_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    movement_type: Mapped[MovementType] = mapped_column(str_enum(MovementType, "movement_type"))
    product_unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"))
    quantity: Mapped[Decimal] = mapped_column(Quantity)           # en la presentación indicada
    quantity_base: Mapped[Decimal] = mapped_column(Quantity)      # con signo: + entra, − sale
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)          # costo por unidad base aplicado
    total_cost: Mapped[Decimal] = mapped_column(Money)            # |quantity_base| × unit_cost
    balance_after: Mapped[Decimal] = mapped_column(Quantity)
    avg_cost_after: Mapped[Decimal] = mapped_column(UnitCost)
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    reverses_movement_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    allocations: Mapped[list["LotAllocation"]] = relationship(lazy="selectin")


class LotAllocation(Base):
    """Qué lotes afectó un movimiento (para revertirlo con exactitud)."""
    __tablename__ = "lot_allocations"
    __table_args__ = (CheckConstraint("quantity <> 0", name="cantidad_no_cero"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    movement_id: Mapped[int] = mapped_column(ForeignKey("stock_movements.id"), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Quantity)  # con signo, igual que el movimiento
