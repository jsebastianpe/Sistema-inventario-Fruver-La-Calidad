import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.types import Money, Quantity, Rate, UnitCost, str_enum


class SaleStatus(str, enum.Enum):
    CONFIRMADA = "confirmada"
    ANULADA = "anulada"


class PaymentMethod(str, enum.Enum):
    EFECTIVO = "efectivo"
    TRANSFERENCIA = "transferencia"
    TARJETA = "tarjeta"
    OTRO = "otro"


class Sale(Base):
    """Venta interna (NO es una factura electrónica DIAN)."""
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("total >= 0", name="total_no_negativo"),
        Index("ix_sales_sold_at", "sold_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[SaleStatus] = mapped_column(str_enum(SaleStatus, "sale_status"),
                                               default=SaleStatus.CONFIRMADA)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    customer_name: Mapped[str | None] = mapped_column(String(120))
    payment_method: Mapped[PaymentMethod] = mapped_column(str_enum(PaymentMethod, "payment_method"))
    reference: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    gross_total: Mapped[Decimal] = mapped_column(Money)      # antes de descuentos
    discount_total: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)            # cobrado, impuestos incluidos
    tax_total: Mapped[Decimal] = mapped_column(Money)        # impuesto contenido en el total
    cost_total: Mapped[Decimal] = mapped_column(Money)       # costo de la mercancía vendida
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list["SaleLine"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", lazy="selectin", order_by="SaleLine.line_no")


class SaleLine(Base):
    """Instantánea histórica: precio y costo quedan fijos aunque el producto cambie después."""
    __tablename__ = "sale_lines"
    __table_args__ = (
        UniqueConstraint("sale_id", "line_no"),
        CheckConstraint("quantity > 0", name="cantidad_positiva"),
        CheckConstraint("discount >= 0 AND discount <= gross_amount", name="descuento_valido"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_unit_id: Mapped[int] = mapped_column(ForeignKey("product_units.id"))
    product_name: Mapped[str] = mapped_column(String(120))
    unit_name: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    quantity_base: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Money)        # por presentación, con impuestos
    gross_amount: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money)
    net_amount: Mapped[Decimal] = mapped_column(Money)
    tax_rate: Mapped[Decimal] = mapped_column(Rate)
    tax_amount: Mapped[Decimal] = mapped_column(Money)
    unit_cost_base: Mapped[Decimal] = mapped_column(UnitCost)  # costo promedio al momento de vender
    cost_amount: Mapped[Decimal] = mapped_column(Money)

    sale: Mapped[Sale] = relationship(back_populates="lines")
