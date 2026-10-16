"""Tipos de entrada validados (Decimal exacto, nunca float)."""
from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints

PositiveQty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]
NonNegQty = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]
MoneyIn = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
UnitCostIn = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]
FactorIn = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
MarginIn = Annotated[Decimal, Field(ge=0, lt=1, max_digits=5, decimal_places=4)]
TaxRateIn = Annotated[Decimal, Field(ge=0, lt=1, max_digits=5, decimal_places=4)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,80}$")]
