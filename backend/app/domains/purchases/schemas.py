from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import MoneyIn, NonNegQty, PositiveQty, TaxRateIn, UnitCostIn
from app.domains.purchases.models import AllocationMethod, PurchaseStatus


class PurchaseLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    product_unit_id: int | None = Field(None, description="Presentación de compra; por defecto la base")
    quantity: PositiveQty
    unit_price: UnitCostIn = Field(description="Precio por presentación, antes de impuestos")
    line_discount: MoneyIn = Decimal(0)
    tax_rate: TaxRateIn | None = Field(None, description="Por defecto, la tasa del producto")
    waste_qty: NonNegQty = Field(Decimal(0), description="Merma detectada al recibir, en unidad base")
    expiry_date: date | None = None
    lot_code: str | None = Field(None, max_length=40)


class PurchaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: int
    purchase_date: date
    invoice_number: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=2000)
    freight: MoneyIn = Decimal(0)
    other_costs: MoneyIn = Decimal(0)
    discount: MoneyIn = Decimal(0)
    allocation_method: AllocationMethod = AllocationMethod.VALOR
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=200)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class PurchaseLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_no: int
    product_id: int
    product_name: str = ""
    product_unit_id: int
    unit_name: str = ""
    quantity: Decimal
    unit_price: Decimal
    line_discount: Decimal
    tax_rate: Decimal
    waste_qty: Decimal
    expiry_date: date | None
    lot_code: str | None
    quantity_base: Decimal
    usable_qty: Decimal
    net_amount: Decimal
    allocated_extra: Decimal
    tax_amount: Decimal
    total_cost: Decimal
    unit_cost_base: Decimal


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    supplier_id: int
    supplier_name: str = ""
    status: PurchaseStatus
    purchase_date: date
    invoice_number: str | None
    notes: str | None
    freight: Decimal
    other_costs: Decimal
    discount: Decimal
    allocation_method: AllocationMethod
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    created_by_id: int
    confirmed_by_id: int | None
    confirmed_at: datetime | None
    cancelled_by_id: int | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    lines: list[PurchaseLineOut]
