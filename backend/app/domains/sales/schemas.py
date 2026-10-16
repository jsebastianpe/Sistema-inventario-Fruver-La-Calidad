from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import MoneyIn, PositiveQty
from app.domains.sales.models import PaymentMethod, SaleStatus


class SaleLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    product_unit_id: int | None = Field(None, description="Presentación de venta; por defecto la base")
    quantity: PositiveQty
    discount: MoneyIn = Field(Decimal(0), description="Descuento en pesos sobre la línea")


class SaleIn(BaseModel):
    """El cliente NO envía precios: el servidor aplica el precio vigente."""
    model_config = ConfigDict(extra="forbid")
    payment_method: PaymentMethod = PaymentMethod.EFECTIVO
    customer_name: str | None = Field(None, max_length=120)
    reference: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=2000)
    lines: list[SaleLineIn] = Field(min_length=1, max_length=200)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class SaleLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    line_no: int
    product_id: int
    product_unit_id: int
    product_name: str
    unit_name: str
    quantity: Decimal
    quantity_base: Decimal
    unit_price: Decimal
    gross_amount: Decimal
    discount: Decimal
    net_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    unit_cost_base: Decimal | None = None
    cost_amount: Decimal | None = None


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: SaleStatus
    sold_at: datetime
    customer_name: str | None
    payment_method: PaymentMethod
    reference: str | None
    notes: str | None
    gross_total: Decimal
    discount_total: Decimal
    total: Decimal
    tax_total: Decimal
    created_by_id: int
    created_by_name: str = ""
    cancelled_at: datetime | None
    cancel_reason: str | None
    lines: list[SaleLineOut]
    cost_total: Decimal | None = None
    gross_margin: Decimal | None = Field(None, description="Margen bruto estimado (no es utilidad neta)")
