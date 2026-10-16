"""Tipos de columna reutilizables."""
import enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Numeric

Money = Numeric(14, 2)
Quantity = Numeric(14, 4)
UnitCost = Numeric(18, 6)
Rate = Numeric(5, 4)


def str_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """Enum almacenado como VARCHAR + CHECK (más fácil de evolucionar que un ENUM nativo)."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=30,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )
