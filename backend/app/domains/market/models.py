import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.types import Money, Rate, UnitCost, str_enum


class MarketSource(Base, TimestampMixin):
    """Fuente de cotizaciones (p. ej. 'sipsa_ciudad'). El código identifica el conector."""
    __tablename__ = "market_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    url: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    latest_price_date: Mapped[date | None] = mapped_column(Date)
    # Solicitud manual de actualización: la atiende el proceso `worker`, no la petición web.
    sync_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class MarketProduct(Base):
    """Producto tal como lo nombra la fuente externa. La unidad de precio debe confirmarse."""
    __tablename__ = "market_products"
    __table_args__ = (UniqueConstraint("source_id", "external_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("market_sources.id"))
    external_code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(150))
    group_name: Mapped[str | None] = mapped_column(String(100))
    # Unidad a la que corresponde el precio publicado ('kg', 'und', …). NULL = por confirmar.
    price_unit_code: Mapped[str | None] = mapped_column(String(20))
    unit_confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    unit_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuoteFlag(str, enum.Enum):
    NORMAL = "normal"
    VARIACION_EXTRAORDINARIA = "variacion_extraordinaria"


class MarketQuote(Base):
    """Cotización mayorista tal como la publicó la fuente (no es el costo ni el precio de venta)."""
    __tablename__ = "market_quotes"
    __table_args__ = (
        UniqueConstraint("source_id", "market_product_id", "market", "price_date"),
        CheckConstraint("price > 0", name="precio_positivo"),
        Index("ix_market_quotes_lookup", "market_product_id", "market", "price_date"),
        Index("ix_market_quotes_date", "price_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("market_sources.id"))
    market_product_id: Mapped[int] = mapped_column(ForeignKey("market_products.id"))
    market: Mapped[str] = mapped_column(String(120))        # ciudad o central normalizada
    price_date: Mapped[date] = mapped_column(Date)          # fecha de referencia (captura)
    price: Mapped[Decimal] = mapped_column(Money)
    price_min: Mapped[Decimal | None] = mapped_column(Money)
    price_max: Mapped[Decimal | None] = mapped_column(Money)
    external_id: Mapped[str | None] = mapped_column(String(40))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # fechaCreacion
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("market_sync_runs.id"))
    variation_rate: Mapped[Decimal | None] = mapped_column(Rate)  # vs. cotización anterior
    flag: Mapped[QuoteFlag] = mapped_column(str_enum(QuoteFlag, "quote_flag"), default=QuoteFlag.NORMAL)

    market_product: Mapped[MarketProduct] = relationship(lazy="joined")


class ProductMarketMapping(Base, TimestampMixin):
    """Correspondencia revisada por una persona entre un producto interno y uno externo.

    conversion_factor: cuántas unidades base del producto interno equivalen a la unidad en que
    la fuente publica el precio. Ej.: fuente en kg, producto en kg → 1. Producto en 'und' y
    fuente en kg con 1 und ≈ 0,25 kg → precio_base = precio_fuente × 0,25 (factor = 0,25 kg/und).
    Se expresa como: precio por unidad base = precio fuente × factor.
    """
    __tablename__ = "product_market_mappings"
    __table_args__ = (
        UniqueConstraint("product_id", "market_product_id", "market"),
        CheckConstraint("conversion_factor > 0", name="factor_positivo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    market_product_id: Mapped[int] = mapped_column(ForeignKey("market_products.id"))
    market: Mapped[str] = mapped_column(String(120))
    conversion_factor: Mapped[Decimal] = mapped_column(UnitCost)
    notes: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    confirmed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    market_product: Mapped[MarketProduct] = relationship(lazy="joined")


class SyncStatus(str, enum.Enum):
    EN_CURSO = "en_curso"
    EXITO = "exito"
    SIN_DATOS_NUEVOS = "sin_datos_nuevos"
    ERROR = "error"


class MarketSyncRun(Base):
    """Bitácora de cada ejecución del proceso de actualización (monitorización)."""
    __tablename__ = "market_sync_runs"
    __table_args__ = (Index("ix_market_sync_runs_source_time", "source_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("market_sources.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SyncStatus] = mapped_column(str_enum(SyncStatus, "sync_status"))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    triggered_by: Mapped[str] = mapped_column(String(60))  # 'programador' o 'usuario:<id>'
    bytes_downloaded: Mapped[int | None] = mapped_column(BigInteger)
    records_read: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_duplicated: Mapped[int] = mapped_column(Integer, default=0)
    records_invalid: Mapped[int] = mapped_column(Integer, default=0)
    records_out_of_scope: Mapped[int] = mapped_column(Integer, default=0)
    anomalies: Mapped[int] = mapped_column(Integer, default=0)
    max_price_date: Mapped[date | None] = mapped_column(Date)
    error_message: Mapped[str | None] = mapped_column(Text)


class WorkerHeartbeat(Base):
    """Latido del proceso automático, para saber si está vivo y qué hizo por última vez."""
    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    info: Mapped[str | None] = mapped_column(Text)
