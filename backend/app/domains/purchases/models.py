import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.types import Money, Quantity, Rate, UnitCost, str_enum


class PurchaseStatus(str, enum.Enum):
    BORRADOR = "borrador"
    CONFIRMADA = "confirmada"
    ANULADA = "anulada"


class AllocationMethod(str, enum.Enum):
    VALOR = "valor"   # proporcional al valor neto de cada línea
    PESO = "peso"     # proporcional a la cantidad en kg (solo productos con base de masa)


class Purchase(Base, TimestampMixin):
    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("freight >= 0 AND other_costs >= 0 AND discount >= 0", name="valores_no_negativos"),
        Index("uq_purchases_invoice", "supplier_id", "invoice_number", unique=True,
              postgresql_where=text("invoice_number IS NOT NULL AND status <> 'anulada'")),
        Index("uq_purchases_idempotency", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
        Index("ix_purchases_date", "purchase_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[PurchaseStatus] = mapped_column(
        str_enum(PurchaseStatus, "purchase_status"), default=PurchaseStatus.BORRADOR)
    purchase_date: Mapped[date] = mapped_column(Date)
    invoice_number: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    freight: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    other_costs: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    discount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))  # descuento global
    allocation_method: Mapped[AllocationMethod] = mapped_column(
        str_enum(AllocationMethod, "allocation_method"), default=AllocationMethod.VALOR)
    subtotal: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    tax_total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list["PurchaseLine"]] = relationship(
        back_populates="purchase", cascade="all, delete-orphan", lazy="selectin",
        order_by="PurchaseLine.line_no")


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    __table_args__ = (
        UniqueConstraint("purchase_id", "line_no"),
        CheckConstraint("quantity > 0", name="cantidad_positiva"),
        CheckConstraint("unit_price >= 0", name="precio_no_negativo"),
        CheckConstraint("line_discount >= 0", name="descuento_no_negativo"),
        CheckConstraint("tax_rate >= 0 AND tax_rate < 1", name="impuesto_valido"),
        CheckConstraint("waste_qty >= 0", name="merma_no_negativa"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_unit_id: Mapped[int] = mapped_column(ForeignKey("product_units.id"))
    quantity: Mapped[Decimal] = mapped_column(Quantity)            # en la presentación de compra
    unit_price: Mapped[Decimal] = mapped_column(UnitCost)          # precio por presentación
    line_discount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    tax_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal(0))
    waste_qty: Mapped[Decimal] = mapped_column(Quantity, default=Decimal(0))  # merma en recepción (base)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    lot_code: Mapped[str | None] = mapped_column(String(40))
    # Calculados (se recalculan al guardar y quedan fijos al confirmar):
    quantity_base: Mapped[Decimal] = mapped_column(Quantity, default=Decimal(0))
    usable_qty: Mapped[Decimal] = mapped_column(Quantity, default=Decimal(0))
    net_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    allocated_extra: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    total_cost: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    unit_cost_base: Mapped[Decimal] = mapped_column(UnitCost, default=Decimal(0))

    purchase: Mapped[Purchase] = relationship(back_populates="lines")
