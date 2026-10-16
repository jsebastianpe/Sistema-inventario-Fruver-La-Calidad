from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import FactorIn, MarginIn, MoneyIn, Name, NonNegQty, TaxRateIn
from app.domains.catalog.models import UnitKind


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    kind: UnitKind
    kg_equivalent: Decimal | None
    is_active: bool


class UnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z0-9_]{1,20}$")
    name: Name
    kind: UnitKind
    kg_equivalent: FactorIn | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    target_margin: Decimal | None
    is_active: bool


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    target_margin: MarginIn | None = None


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    target_margin: MarginIn | None = None
    is_active: bool | None = None


class PresentationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: int
    name: Name
    factor_to_base: FactorIn | None = Field(
        None, description="Unidades base que contiene. Obligatorio salvo entre unidades de masa.")
    for_purchase: bool = True
    for_sale: bool = True


class PresentationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    factor_to_base: FactorIn | None = None
    for_purchase: bool | None = None
    for_sale: bool | None = None
    is_active: bool | None = None


class PresentationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    unit_id: int
    unit_code: str = ""
    name: str
    factor_to_base: Decimal
    is_base: bool
    for_purchase: bool
    for_sale: bool
    is_active: bool


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^[A-Za-z0-9._-]{1,40}$")
    barcode: str | None = Field(None, pattern=r"^[0-9A-Za-z-]{4,64}$")
    name: Name
    category_id: int
    variety: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=2000)
    base_unit_id: int
    min_stock: NonNegQty = Decimal(0)
    target_margin: MarginIn | None = None
    tax_rate: TaxRateIn = Decimal(0)
    usual_supplier_id: int | None = None
    is_perishable: bool = True
    current_price: MoneyIn | None = Field(
        None, description="Precio inicial por unidad base. Requiere permiso de aprobación de precios.")
    presentations: list[PresentationIn] = []


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{1,40}$")
    barcode: str | None = Field(None, pattern=r"^[0-9A-Za-z-]{4,64}$")
    name: Name | None = None
    category_id: int | None = None
    variety: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=2000)
    min_stock: NonNegQty | None = None
    target_margin: MarginIn | None = None
    tax_rate: TaxRateIn | None = None
    usual_supplier_id: int | None = None
    is_perishable: bool | None = None


class ProductOut(BaseModel):
    id: int
    sku: str
    barcode: str | None
    name: str
    category_id: int
    category_name: str
    variety: str | None
    description: str | None
    base_unit_id: int
    base_unit_code: str
    min_stock: Decimal
    stock: Decimal
    low_stock: bool
    current_price: Decimal | None
    tax_rate: Decimal
    usual_supplier_id: int | None
    is_perishable: bool
    is_active: bool
    presentations: list[PresentationOut]
    created_at: datetime
    updated_at: datetime


class ProductCostOut(ProductOut):
    """Vista con costos: solo para usuarios con permiso costos.ver."""
    avg_cost: Decimal
    stock_value: Decimal
    target_margin: Decimal | None
    effective_margin: Decimal
    gross_margin_at_current_price: Decimal | None
