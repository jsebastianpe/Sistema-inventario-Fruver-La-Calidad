import enum
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.types import Money, Quantity, Rate, UnitCost, str_enum


class UnitKind(str, enum.Enum):
    MASA = "masa"            # kg, g, libra: equivalencia fija a kg
    CONTEO = "conteo"        # unidad, docena
    EMPAQUE = "empaque"      # caja, bulto, canastilla: su contenido depende del producto


class Unit(Base, TimestampMixin):
    __tablename__ = "units"
    __table_args__ = (
        CheckConstraint("(kind = 'masa') = (kg_equivalent IS NOT NULL)", name="masa_tiene_equivalencia"),
        CheckConstraint("kg_equivalent IS NULL OR kg_equivalent > 0", name="equivalencia_positiva"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[UnitKind] = mapped_column(str_enum(UnitKind, "unit_kind"))
    # Solo para unidades de masa: cuántos kg equivale 1 unidad (kg=1, g=0.001, libra=0.5).
    kg_equivalent: Mapped[Decimal | None] = mapped_column(UnitCost)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("target_margin IS NULL OR (target_margin >= 0 AND target_margin < 1)",
                        name="margen_valido"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    target_margin: Mapped[Decimal | None] = mapped_column(Rate)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("target_margin IS NULL OR (target_margin >= 0 AND target_margin < 1)",
                        name="margen_valido"),
        CheckConstraint("tax_rate >= 0 AND tax_rate < 1", name="impuesto_valido"),
        CheckConstraint("min_stock >= 0", name="stock_minimo_no_negativo"),
        CheckConstraint("current_price IS NULL OR current_price >= 0", name="precio_no_negativo"),
        Index("ix_products_name_lower", text("lower(name)")),
        Index("uq_products_barcode", "barcode", unique=True,
              postgresql_where=text("barcode IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    barcode: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    variety: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    base_unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    min_stock: Mapped[Decimal] = mapped_column(Quantity, default=Decimal(0), server_default="0")
    # Precio de venta vigente POR UNIDAD BASE, impuestos incluidos.
    current_price: Mapped[Decimal | None] = mapped_column(Money)
    target_margin: Mapped[Decimal | None] = mapped_column(Rate)
    tax_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal(0), server_default="0")
    usual_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    is_perishable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    category: Mapped[Category] = relationship(lazy="joined")
    base_unit: Mapped[Unit] = relationship(lazy="joined")
    presentations: Mapped[list["ProductUnit"]] = relationship(
        back_populates="product", lazy="selectin", order_by="ProductUnit.factor_to_base")


class ProductUnit(Base, TimestampMixin):
    """Presentación de un producto y su conversión EXPLÍCITA a la unidad base.

    Ej.: papa (base kg) → "Bulto 50 kg" factor 50; "Bulto 25 kg" factor 25; "Libra" factor 0.5.
    Las cantidades siempre se convierten en un solo paso: cantidad × factor.
    """
    __tablename__ = "product_units"
    __table_args__ = (
        UniqueConstraint("product_id", "name"),
        CheckConstraint("factor_to_base > 0", name="factor_positivo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    name: Mapped[str] = mapped_column(String(60))
    factor_to_base: Mapped[Decimal] = mapped_column(UnitCost)
    is_base: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    for_purchase: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    for_sale: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    product: Mapped[Product] = relationship(back_populates="presentations")
    unit: Mapped[Unit] = relationship(lazy="joined")
