import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import Money, Rate, UnitCost, str_enum


class PriceChangeSource(str, enum.Enum):
    MANUAL = "manual"            # el propietario fijó el precio directamente
    SUGERENCIA = "sugerencia"    # aprobación de una sugerencia
    AUTOMATICA = "automatica"    # política automática habilitada expresamente por el propietario


class PriceHistory(Base):
    """Historial inmutable de precios de venta (por unidad base, impuestos incluidos)."""
    __tablename__ = "price_history"
    __table_args__ = (Index("ix_price_history_product_time", "product_id", "changed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    old_price: Mapped[Decimal | None] = mapped_column(Money)
    new_price: Mapped[Decimal] = mapped_column(Money)
    source: Mapped[PriceChangeSource] = mapped_column(str_enum(PriceChangeSource, "price_change_source"))
    suggestion_id: Mapped[int | None] = mapped_column(ForeignKey("price_suggestions.id"))
    justification: Mapped[str] = mapped_column(String(500))
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuggestionStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    APROBADA = "aprobada"
    RECHAZADA = "rechazada"
    REEMPLAZADA = "reemplazada"   # una sugerencia más reciente la sustituyó


class PriceSuggestion(Base):
    __tablename__ = "price_suggestions"
    __table_args__ = (
        CheckConstraint("suggested_price > 0", name="precio_positivo"),
        CheckConstraint("margin >= 0 AND margin < 1", name="margen_valido"),
        Index("uq_price_suggestions_pending", "product_id", unique=True,
              postgresql_where=text("status = 'pendiente'")),
        Index("ix_price_suggestions_status", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[SuggestionStatus] = mapped_column(
        str_enum(SuggestionStatus, "suggestion_status"), default=SuggestionStatus.PENDIENTE)
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)       # costo efectivo usado (por unidad base)
    margin: Mapped[Decimal] = mapped_column(Rate)
    commission_rate: Mapped[Decimal] = mapped_column(Rate)
    tax_rate: Mapped[Decimal] = mapped_column(Rate)
    raw_price: Mapped[Decimal] = mapped_column(Money)          # antes de redondeo
    suggested_price: Mapped[Decimal] = mapped_column(Money)    # tras redondeo comercial
    current_price: Mapped[Decimal | None] = mapped_column(Money)
    change_rate: Mapped[Decimal | None] = mapped_column(Rate)  # (sugerido − actual) / actual
    market_quote_id: Mapped[int | None] = mapped_column(ForeignKey("market_quotes.id"))
    market_price_base: Mapped[Decimal | None] = mapped_column(Money)  # referencia, por unidad base
    origin: Mapped[str] = mapped_column(String(30))            # 'automatica' | 'manual'
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
