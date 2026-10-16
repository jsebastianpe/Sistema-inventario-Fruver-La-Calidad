"""Interfaz común para fuentes de cotizaciones de mercado.

Para añadir una fuente nueva (otro servicio autorizado o archivos oficiales):
  1. Implemente una clase con `code` y `fetch(since)`.
  2. Regístrela en `app/domains/market/connectors/__init__.py`.
  3. Cree el registro en `market_sources` (migración) con el mismo `code`.
"""
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


class SourceError(Exception):
    """Error de la fuente. `transient=True` indica que vale la pena reintentar."""

    def __init__(self, message: str, *, transient: bool):
        super().__init__(message)
        self.transient = transient


@dataclass
class RawQuote:
    external_code: str
    name: str
    market: str
    price_date: date
    price: Decimal
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    external_id: str | None = None
    published_at: datetime | None = None
    group_name: str | None = None


@dataclass
class FetchStats:
    bytes_downloaded: int = 0
    records_read: int = 0
    records_invalid: int = 0
    records_out_of_scope: int = 0
    invalid_samples: list[str] = field(default_factory=list)

    def invalid(self, why: str) -> None:
        self.records_invalid += 1
        if len(self.invalid_samples) < 20:
            self.invalid_samples.append(why)


class MarketConnector(Protocol):
    code: str

    def fetch(self, since: date, stats: FetchStats) -> Iterator[RawQuote]:
        """Devuelve las cotizaciones con fecha de referencia >= since.

        Debe lanzar SourceError ante fallas de red, respuestas inválidas o errores de la fuente.
        """
        ...
