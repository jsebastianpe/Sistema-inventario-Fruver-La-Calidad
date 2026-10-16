"""Precios de venta: fórmula, cambio de precio con historial y flujo de aprobación.

Fórmula (documentada en docs/05-reglas-de-costos-y-precios.md):

    precio_sin_impuesto = costo_unitario_efectivo / (1 − margen − comisión)
    precio_sugerido     = redondeo_comercial(precio_sin_impuesto × (1 + tasa_impuesto))

con 0 ≤ margen < 1, comisión ≥ 0 y margen + comisión < 1. Los precios de venta se
almacenan por unidad base e incluyen impuestos.
"""
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict
from app.core.numbers import ONE, money, rate, round_to_step
from app.core.timeutil import now_utc
from app.domains.audit.service import audit
from app.domains.catalog.models import Product
from app.domains.inventory.models import StockBalance
from app.domains.market.service import best_reference, cost_vs_market
from app.domains.pricing.models import PriceChangeSource, PriceHistory, PriceSuggestion, SuggestionStatus
from app.domains.settings.service import CommercialSettings


def effective_margin(product: Product, cfg: CommercialSettings) -> Decimal:
    """Margen objetivo: producto → categoría → valor general."""
    if product.target_margin is not None:
        return product.target_margin
    if product.category is not None and product.category.target_margin is not None:
        return product.category.target_margin
    return cfg.default_margin


def compute_price(unit_cost: Decimal, margin: Decimal, *, commission: Decimal = Decimal(0),
                  tax_rate: Decimal = Decimal(0), step: Decimal = Decimal(0),
                  mode: str = "arriba") -> tuple[Decimal, Decimal]:
    """Devuelve (precio sin redondear, precio redondeado), impuestos incluidos."""
    if unit_cost is None or unit_cost <= 0:
        raise AppError("No hay un costo válido (mayor que cero) para calcular el precio",
                       code="costo_invalido")
    if not (Decimal(0) <= margin < ONE):
        raise AppError("El margen debe estar entre 0 y 1 (sin incluir 1)", code="margen_invalido")
    if commission < 0 or margin + commission >= ONE:
        raise AppError("El margen más la comisión deben ser menores que 1", code="margen_invalido")
    if not (Decimal(0) <= tax_rate < ONE):
        raise AppError("La tasa de impuesto no es válida", code="impuesto_invalido")
    raw = unit_cost / (ONE - margin - commission) * (ONE + tax_rate)
    return money(raw), round_to_step(raw, step, mode)


def gross_margin(price_with_tax: Decimal, unit_cost: Decimal, tax_rate: Decimal) -> Decimal | None:
    """Margen bruto sobre el precio sin impuestos: (precio_neto − costo) / precio_neto."""
    if price_with_tax is None or price_with_tax <= 0:
        return None
    net = price_with_tax / (ONE + tax_rate)
    return rate((net - unit_cost) / net)


def set_price(db: Session, product: Product, new_price: Decimal, *, source: PriceChangeSource,
              justification: str, user_id: int | None, ip: str | None,
              suggestion_id: int | None = None) -> PriceHistory | None:
    """Único punto que cambia el precio vigente. Siempre deja historial y auditoría.

    No hace commit: forma parte de la transacción del llamador.
    """
    new_price = money(new_price)
    if new_price <= 0:
        raise AppError("El precio debe ser mayor que cero", code="precio_invalido")
    if not justification or not justification.strip():
        raise AppError("Debe justificar el cambio de precio", code="justificacion_requerida")
    old = product.current_price
    if old is not None and old == new_price:
        return None
    product.current_price = new_price
    entry = PriceHistory(product_id=product.id, old_price=old, new_price=new_price, source=source,
                         suggestion_id=suggestion_id, justification=justification.strip()[:500],
                         changed_by_id=user_id)
    db.add(entry)
    audit(db, user_id=user_id, action="precios.cambio", entity="producto", entity_id=product.id,
          details={"anterior": old, "nuevo": new_price, "origen": source,
                   "justificacion": justification}, ip=ip)
    return entry


# ------------------------------------------------------------------ sugerencias y aprobación

SUGGESTION_MAX_AGE_DAYS = 7      # una sugerencia más antigua debe regenerarse
COST_DRIFT_TOLERANCE = Decimal("0.01")   # 1 %: si el costo cambió más, la sugerencia es obsoleta


def generate_suggestion(db: Session, product: Product, cfg: CommercialSettings, *, origin: str,
                        user_id: int | None):
    """Crea (o reemplaza) la sugerencia pendiente de un producto.

    Devuelve (sugerencia | None, motivo_si_no_se_generó). El precio sugerido se basa SIEMPRE en el
    costo real (promedio ponderado); el mercado solo aporta una referencia y advertencias.
    """
    if not product.is_active:
        return None, "Producto inactivo"
    bal = db.get(StockBalance, product.id)
    unit_cost = bal.avg_cost if bal else Decimal(0)
    if unit_cost is None or unit_cost <= 0:
        return None, "Sin costo real registrado (registre una compra o existencia inicial con costo)"
    margin = effective_margin(product, cfg)
    raw, suggested = compute_price(unit_cost, margin, commission=cfg.sales_commission_rate,
                                   tax_rate=product.tax_rate, step=cfg.rounding_step,
                                   mode=cfg.rounding_mode)
    current = product.current_price
    if current is not None and current == suggested:
        return None, "El precio vigente ya coincide con el sugerido"
    change = rate((suggested - current) / current) if current else None

    warnings: list[str] = []
    ref = best_reference(db, product.id)
    market_base = ref.price_per_base_unit if ref else None
    gap = cost_vs_market(unit_cost, market_base)
    if ref is None:
        warnings.append("Sin referencia de mercado utilizable (sin correspondencia, unidad sin confirmar "
                        "o cotización desactualizada)")
    else:
        if gap is not None and abs(gap) > cfg.market_variation_alert_rate:
            warnings.append(f"El costo real difiere {gap * 100:.1f} % de la cotización mayorista "
                            f"({ref.market}, {ref.price_date.isoformat()})")
        if ref.flag == "variacion_extraordinaria":
            warnings.append("La última cotización de mercado tuvo una variación extraordinaria")
    if change is not None and abs(change) > cfg.price_change_alert_rate:
        warnings.append(f"Cambio de {change * 100:.1f} % frente al precio vigente "
                        f"(umbral {cfg.price_change_alert_rate * 100:.0f} %)")

    explanation = {
        "formula": "precio = costo / (1 − margen − comisión) × (1 + impuesto), con redondeo comercial",
        "costo_unitario": str(unit_cost), "base_costo": "costo promedio ponderado de inventario",
        "margen": str(margin), "comision": str(cfg.sales_commission_rate),
        "impuesto": str(product.tax_rate), "precio_sin_redondeo": str(raw),
        "redondeo": f"múltiplo de {cfg.rounding_step} ({cfg.rounding_mode})",
        "unidad": product.base_unit.code,
        "mercado": None if ref is None else {
            "fuente_mercado": ref.market, "producto_fuente": ref.market_product_name,
            "fecha": ref.price_date.isoformat(), "precio_por_unidad_base": str(market_base),
            "diferencia_costo_vs_mercado": None if gap is None else str(gap)},
        "advertencias": warnings,
    }
    db.execute(update(PriceSuggestion).where(
        PriceSuggestion.product_id == product.id, PriceSuggestion.status == SuggestionStatus.PENDIENTE
    ).values(status=SuggestionStatus.REEMPLAZADA))
    s = PriceSuggestion(
        product_id=product.id, unit_cost=unit_cost, margin=margin,
        commission_rate=cfg.sales_commission_rate, tax_rate=product.tax_rate, raw_price=raw,
        suggested_price=suggested, current_price=current, change_rate=change,
        market_quote_id=ref.quote_id if ref else None, market_price_base=market_base, origin=origin,
        explanation=explanation, created_by_id=user_id)
    db.add(s)
    db.flush()
    return s, None


def validate_still_valid(db: Session, s, product: Product) -> None:
    """Rechaza aprobar sugerencias basadas en datos obsoletos."""
    if (now_utc() - s.created_at).days > SUGGESTION_MAX_AGE_DAYS:
        raise Conflict(f"La sugerencia tiene más de {SUGGESTION_MAX_AGE_DAYS} días; genere una nueva",
                       code="sugerencia_obsoleta")
    bal = db.get(StockBalance, product.id)
    current_cost = bal.avg_cost if bal else Decimal(0)
    if current_cost <= 0 or abs(current_cost - s.unit_cost) / s.unit_cost > COST_DRIFT_TOLERANCE:
        raise Conflict("El costo del producto cambió desde que se generó la sugerencia; genere una nueva",
                       code="sugerencia_obsoleta")
    if product.current_price != s.current_price:
        raise Conflict("El precio vigente cambió desde que se generó la sugerencia; genere una nueva",
                       code="sugerencia_obsoleta")


def apply_automatic_policy(db: Session, s, product: Product, cfg: CommercialSettings) -> bool:
    """Aplica una sugerencia sin intervención SOLO si el propietario habilitó la política y se
    cumplen todas las protecciones. Devuelve True si cambió el precio."""
    if not cfg.auto_price_update_enabled or s is None:
        return False
    if s.current_price is None or s.change_rate is None:
        return False                                   # el primer precio siempre es manual
    if abs(s.change_rate) > cfg.auto_price_max_change_rate:
        return False
    if s.explanation.get("advertencias"):
        return False                                   # cualquier advertencia exige revisión humana
    set_price(db, product, s.suggested_price, source=PriceChangeSource.AUTOMATICA,
              justification=f"Política automática: cambio {s.change_rate * 100:.2f} % dentro del límite "
                            f"de {cfg.auto_price_max_change_rate * 100:.2f} %",
              user_id=None, ip=None, suggestion_id=s.id)
    s.status = SuggestionStatus.APROBADA
    s.decided_at = now_utc()
    s.decision_note = "Aplicada por la política automática configurada por el propietario"
    return True
