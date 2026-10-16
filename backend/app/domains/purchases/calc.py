"""Cálculo de una compra (función pura, sin base de datos).

Por línea:
    bruto_i      = cantidad_i × precio_unitario_i
    neto_i       = bruto_i − descuento_línea_i                       (≥ 0)
    impuesto_i   = neto_i × tasa_i
Compra:
    subtotal     = Σ neto_i
    impuestos    = Σ impuesto_i
    total        = subtotal + impuestos + transporte + otros_costos − descuento_global   (≥ 0)
Reparto de costos adicionales (transporte + otros − descuento global):
    método 'valor': proporcional a neto_i
    método 'peso' : proporcional a la cantidad en kg de cada línea (todas deben ser de masa)
    La suma de las partes es exactamente el monto a repartir (el residuo de redondeo va a la
    línea de mayor peso). Si todos los pesos son 0, se reparte por igual.
Costo efectivo:
    costo_línea_i      = neto_i + reparto_i (+ impuesto_i si el impuesto NO es descontable)
    cantidad_útil_i    = cantidad_base_i − merma_recepción_i         (> 0)
    costo_unitario_i   = costo_línea_i / cantidad_útil_i             (por unidad base)
La merma detectada al recibir encarece la cantidad aprovechable; no entra al inventario.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import AppError
from app.core.numbers import ZERO, allocate, cost, money, qty


@dataclass
class LineInput:
    quantity: Decimal
    unit_price: Decimal
    line_discount: Decimal
    tax_rate: Decimal
    waste_qty: Decimal          # en unidad base
    factor_to_base: Decimal
    base_kg_equivalent: Decimal | None   # None si la unidad base no es de masa
    label: str = ""


@dataclass
class LineResult:
    quantity_base: Decimal
    usable_qty: Decimal
    net_amount: Decimal
    allocated_extra: Decimal
    tax_amount: Decimal
    total_cost: Decimal
    unit_cost_base: Decimal


@dataclass
class PurchaseResult:
    lines: list[LineResult]
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


def compute(lines: list[LineInput], *, freight: Decimal, other_costs: Decimal, discount: Decimal,
            method: str, tax_in_cost: bool) -> PurchaseResult:
    if not lines:
        raise AppError("La compra debe tener al menos una línea", code="compra_sin_lineas")
    nets, taxes, bases = [], [], []
    for i, ln in enumerate(lines, 1):
        name = ln.label or f"línea {i}"
        if ln.quantity <= 0:
            raise AppError(f"{name}: la cantidad debe ser mayor que cero", code="cantidad_invalida")
        if ln.unit_price < 0 or ln.line_discount < 0:
            raise AppError(f"{name}: precio y descuento no pueden ser negativos", code="valor_invalido")
        gross = money(ln.quantity * ln.unit_price)
        net = gross - money(ln.line_discount)
        if net < 0:
            raise AppError(f"{name}: el descuento supera el valor de la línea", code="descuento_invalido")
        base = qty(ln.quantity * ln.factor_to_base)
        if ln.waste_qty < 0 or ln.waste_qty >= base:
            raise AppError(f"{name}: la merma de recepción debe ser menor que la cantidad recibida "
                           f"({base.normalize():f})", code="merma_invalida")
        nets.append(net)
        taxes.append(money(net * ln.tax_rate))
        bases.append(base)

    extras = money(freight) + money(other_costs) - money(discount)
    if method == "peso":
        if any(ln.base_kg_equivalent is None for ln in lines):
            raise AppError("El reparto por peso exige que todos los productos se manejen en unidades "
                           "de masa; use el reparto por valor", code="reparto_invalido")
        weights = [b * ln.base_kg_equivalent for b, ln in zip(bases, lines)]
    elif method == "valor":
        weights = nets
    else:
        raise AppError("Método de reparto desconocido", code="reparto_invalido")
    shares = allocate(extras, weights)

    subtotal = sum(nets, ZERO)
    tax_total = sum(taxes, ZERO)
    total = subtotal + tax_total + extras
    if total < 0:
        raise AppError("El descuento global supera el valor de la compra", code="descuento_invalido")

    results = []
    for ln, net, tax, base, share in zip(lines, nets, taxes, bases, shares):
        line_cost = net + share + (tax if tax_in_cost else ZERO)
        if line_cost < 0:
            raise AppError(f"{ln.label or 'Una línea'}: el descuento repartido deja un costo negativo",
                           code="descuento_invalido")
        usable = base - qty(ln.waste_qty)
        results.append(LineResult(quantity_base=base, usable_qty=usable, net_amount=net,
                                  allocated_extra=share, tax_amount=tax, total_cost=money(line_cost),
                                  unit_cost_base=cost(line_cost / usable)))
    return PurchaseResult(lines=results, subtotal=money(subtotal), tax_total=money(tax_total),
                          total=money(total))
