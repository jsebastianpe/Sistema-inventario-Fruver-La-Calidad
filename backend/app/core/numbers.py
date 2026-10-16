"""Aritmética decimal exacta para dinero y cantidades. Nunca se usa float.

Escalas (coinciden con las columnas NUMERIC de la base de datos):
  dinero        2 decimales  (COP)
  cantidad      4 decimales  (en la unidad base del producto)
  costo unitario 6 decimales (permite costos por gramo sin perder precisión)
  tasa          4 decimales  (márgenes, impuestos: 0.3500 = 35 %)
"""
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

MONEY = Decimal("0.01")
QTY = Decimal("0.0001")
COST = Decimal("0.000001")
RATE = Decimal("0.0001")
ZERO = Decimal("0")
ONE = Decimal("1")


def money(v: Decimal | int | str) -> Decimal:
    return Decimal(v).quantize(MONEY, ROUND_HALF_UP)


def qty(v: Decimal | int | str) -> Decimal:
    return Decimal(v).quantize(QTY, ROUND_HALF_UP)


def cost(v: Decimal | int | str) -> Decimal:
    return Decimal(v).quantize(COST, ROUND_HALF_UP)


def rate(v: Decimal | int | str) -> Decimal:
    return Decimal(v).quantize(RATE, ROUND_HALF_UP)


def round_to_step(value: Decimal, step: Decimal, mode: str = "arriba") -> Decimal:
    """Redondeo comercial a un múltiplo de `step` (p. ej. 50 COP).

    mode: 'arriba' (techo), 'abajo' (piso) o 'cercano' (mitad hacia arriba).
    """
    value = money(value)  # primero a centavos: evita que residuos de la división (1699,9999…) salten de múltiplo
    if step <= 0:
        return value
    units = value / step
    rounding = {"arriba": ROUND_CEILING, "abajo": ROUND_FLOOR, "cercano": ROUND_HALF_UP}[mode]
    return money(units.quantize(ONE, rounding) * step)


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Reparte `total` (dinero) proporcionalmente a `weights`.

    La suma de las partes es exactamente `total`: la diferencia de redondeo se
    asigna a la parte de mayor peso. Si todos los pesos son cero, reparte por igual.
    """
    if not weights:
        return []
    total = money(total)
    w_sum = sum(weights, ZERO)
    if w_sum == 0:
        weights = [ONE] * len(weights)
        w_sum = Decimal(len(weights))
    parts = [money(total * w / w_sum) for w in weights]
    diff = total - sum(parts, ZERO)
    if diff:
        idx = max(range(len(weights)), key=lambda i: weights[i])
        parts[idx] += diff
    return parts
