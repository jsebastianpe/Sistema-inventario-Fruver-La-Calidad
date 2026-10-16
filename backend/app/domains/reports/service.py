"""Reportes. Cada reporte declara su permiso, sus columnas y notas sobre la naturaleza de los datos.

Convenciones de los indicadores (ver docs/05-reglas-de-costos-y-precios.md):
* "Valor del inventario" = Σ existencia × costo promedio ponderado → ESTIMACIÓN de gestión.
* "Margen bruto estimado" = ventas sin impuestos − costo de la mercancía vendida (al costo promedio
  del momento de la venta). NO es utilidad neta: no descuenta arriendo, nómina, servicios, etc.
* Las ventas y compras anuladas se excluyen.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, Forbidden
from app.core.numbers import ZERO, money, rate
from app.core.timeutil import local_day_bounds, local_tz, now_utc, today_local
from app.domains.audit.models import AuditLog
from app.domains.auth.deps import CurrentUser
from app.domains.auth.models import User
from app.domains.auth.permissions import P
from app.domains.catalog.models import Category, Product, Unit
from app.domains.inventory.models import Lot, MovementType, StockBalance, StockMovement
from app.domains.market import service as market
from app.domains.market.models import (
    MarketProduct, MarketQuote, MarketSource, ProductMarketMapping, WorkerHeartbeat,
)
from app.domains.pricing.models import PriceHistory, PriceSuggestion, SuggestionStatus
from app.domains.purchases.models import Purchase, PurchaseLine, PurchaseStatus
from app.domains.sales.models import Sale, SaleLine, SaleStatus
from app.domains.settings.service import get_commercial_settings
from app.domains.suppliers.models import Supplier


@dataclass
class Col:
    key: str
    title: str
    kind: str = "texto"   # texto | numero | dinero | porcentaje | fecha | cantidad


@dataclass
class Report:
    title: str
    columns: list[Col]
    rows: list[dict]
    filters: dict
    notes: list[str] = field(default_factory=list)
    totals: dict = field(default_factory=dict)


@dataclass
class Params:
    desde: date
    hasta: date
    product_id: int | None = None
    category_id: int | None = None
    supplier_id: int | None = None
    mercado: str | None = None


def default_period() -> tuple[date, date]:
    today = today_local()
    return today.replace(day=1), today


def _sold_at_local_date():
    return cast(func.timezone(str(local_tz()), Sale.sold_at), Date)


def _product_filters(stmt, p: Params, product_col=Product.id):
    if p.product_id:
        stmt = stmt.where(product_col == p.product_id)
    if p.category_id:
        stmt = stmt.where(Product.category_id == p.category_id)
    if p.supplier_id:
        stmt = stmt.where(Product.usual_supplier_id == p.supplier_id)
    return stmt


# ------------------------------------------------------------------ reportes

def stock_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    costs = cu.can(P.COSTOS_VER)
    stmt = (select(Product, Category.name, Unit.code, StockBalance)
            .join(Category, Category.id == Product.category_id).join(Unit, Unit.id == Product.base_unit_id)
            .join(StockBalance, StockBalance.product_id == Product.id, isouter=True)
            .where(Product.is_active.is_(True)).order_by(Category.name, Product.name))
    rows, total_value = [], ZERO
    for prod, cat, unit, bal in db.execute(_product_filters(stmt, p)).all():
        q = bal.quantity if bal else ZERO
        row = {"sku": prod.sku, "producto": prod.name, "categoria": cat, "unidad": unit, "existencia": q,
               "minimo": prod.min_stock, "bajo_minimo": "Sí" if prod.min_stock > 0 and q <= prod.min_stock else "",
               "precio_venta": prod.current_price}
        if costs:
            avg = bal.avg_cost if bal else ZERO
            value = money(max(q, ZERO) * avg)
            row.update(costo_promedio=avg, valor_estimado=value)
            total_value += value
        rows.append(row)
    cols = [Col("sku", "SKU"), Col("producto", "Producto"), Col("categoria", "Categoría"),
            Col("unidad", "Unidad"), Col("existencia", "Existencia", "cantidad"),
            Col("minimo", "Mínimo", "cantidad"), Col("bajo_minimo", "Bajo mínimo"),
            Col("precio_venta", "Precio venta (unidad base)", "dinero")]
    notes = ["Existencias físicas registradas en el sistema a la fecha de generación."]
    totals = {}
    if costs:
        cols += [Col("costo_promedio", "Costo promedio", "dinero"), Col("valor_estimado", "Valor estimado", "dinero")]
        notes.append("Valor estimado = existencia × costo promedio ponderado (estimación de gestión).")
        totals = {"valor_estimado": total_value}
    return Report("Existencias actuales", cols, rows, {}, notes, totals)


def movements_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    costs = cu.can(P.COSTOS_VER)
    stmt = (select(StockMovement, Product.name, Unit.code, User.full_name)
            .join(Product, Product.id == StockMovement.product_id).join(Unit, Unit.id == Product.base_unit_id)
            .join(User, User.id == StockMovement.user_id, isouter=True)
            .where(StockMovement.created_at >= lo, StockMovement.created_at < hi)
            .order_by(StockMovement.created_at, StockMovement.id))
    rows = []
    for mv, name, unit, user in db.execute(_product_filters(stmt, p, StockMovement.product_id)).all():
        row = {"fecha": mv.created_at.astimezone(local_tz()).strftime("%Y-%m-%d %H:%M"), "producto": name,
               "tipo": mv.movement_type.value, "cantidad": mv.quantity_base, "unidad": unit,
               "saldo": mv.balance_after, "documento": f"{mv.reference_type or ''} {mv.reference_id or ''}".strip(),
               "motivo": mv.reason or "", "usuario": user or "Sistema", "id": mv.id}
        if costs:
            row.update(costo_unitario=mv.unit_cost, costo_total=mv.total_cost)
        rows.append(row)
    cols = [Col("fecha", "Fecha y hora"), Col("producto", "Producto"), Col("tipo", "Tipo"),
            Col("cantidad", "Cantidad (base, con signo)", "cantidad"), Col("unidad", "Unidad"),
            Col("saldo", "Saldo después", "cantidad"), Col("documento", "Documento"), Col("motivo", "Motivo"),
            Col("usuario", "Usuario"), Col("id", "ID movimiento", "numero")]
    if costs:
        cols += [Col("costo_unitario", "Costo unitario", "dinero"), Col("costo_total", "Costo total", "dinero")]
    return Report("Movimientos de inventario", cols, rows, _f(p), ["Entradas positivas, salidas negativas."])


def purchases_by_supplier(db: Session, cu: CurrentUser, p: Params) -> Report:
    stmt = (select(Supplier.name, func.count(Purchase.id), func.sum(Purchase.subtotal),
                   func.sum(Purchase.tax_total), func.sum(Purchase.freight + Purchase.other_costs),
                   func.sum(Purchase.discount), func.sum(Purchase.total))
            .join(Supplier, Supplier.id == Purchase.supplier_id)
            .where(Purchase.status == PurchaseStatus.CONFIRMADA, Purchase.purchase_date >= p.desde,
                   Purchase.purchase_date <= p.hasta)
            .group_by(Supplier.name).order_by(func.sum(Purchase.total).desc()))
    if p.supplier_id:
        stmt = stmt.where(Purchase.supplier_id == p.supplier_id)
    if p.product_id:
        stmt = stmt.where(Purchase.lines.any(PurchaseLine.product_id == p.product_id))
    rows = [{"proveedor": n, "compras": c, "subtotal": s, "impuestos": t, "costos_adicionales": e,
             "descuentos": d, "total": tot} for n, c, s, t, e, d, tot in db.execute(stmt).all()]
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ("subtotal", "impuestos", "costos_adicionales",
                                                             "descuentos", "total")}
    cols = [Col("proveedor", "Proveedor"), Col("compras", "N.º compras", "numero"),
            Col("subtotal", "Subtotal", "dinero"), Col("impuestos", "Impuestos", "dinero"),
            Col("costos_adicionales", "Transporte y otros", "dinero"), Col("descuentos", "Descuentos", "dinero"),
            Col("total", "Total", "dinero")]
    return Report("Compras por proveedor", cols, rows, _f(p), ["Solo compras confirmadas (excluye borradores y anuladas)."],
                  totals)


def sales_by_period(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    day = _sold_at_local_date()
    stmt = (select(day, func.count(Sale.id), func.sum(Sale.gross_total), func.sum(Sale.discount_total),
                   func.sum(Sale.total), func.sum(Sale.tax_total), func.sum(Sale.cost_total))
            .where(Sale.status == SaleStatus.CONFIRMADA, Sale.sold_at >= lo, Sale.sold_at < hi)
            .group_by(day).order_by(day))
    if p.product_id:
        stmt = stmt.where(Sale.lines.any(SaleLine.product_id == p.product_id))
    costs = cu.can(P.COSTOS_VER)
    rows = []
    for d, n, gross, disc, total, tax, cost in db.execute(stmt).all():
        row = {"fecha": d.isoformat(), "ventas": n, "bruto": gross, "descuentos": disc, "total": total,
               "impuestos": tax, "ventas_sin_impuestos": total - tax}
        if costs:
            margin = total - tax - cost
            row.update(costo_vendido=cost, margen_bruto=margin,
                       margen_pct=rate(margin / (total - tax)) if total - tax > 0 else None)
        rows.append(row)
    keys = ["bruto", "descuentos", "total", "impuestos", "ventas_sin_impuestos"] + \
           (["costo_vendido", "margen_bruto"] if costs else [])
    totals = {k: sum((r[k] for r in rows), ZERO) for k in keys}
    totals["ventas"] = sum(r["ventas"] for r in rows)
    cols = [Col("fecha", "Fecha", "fecha"), Col("ventas", "N.º ventas", "numero"), Col("bruto", "Bruto", "dinero"),
            Col("descuentos", "Descuentos", "dinero"), Col("total", "Total cobrado", "dinero"),
            Col("impuestos", "Impuestos incluidos", "dinero"),
            Col("ventas_sin_impuestos", "Ventas sin impuestos", "dinero")]
    notes = ["Solo ventas confirmadas. Fechas en hora de Colombia (America/Bogota).",
             "Registro interno: no constituye facturación electrónica."]
    if costs:
        cols += [Col("costo_vendido", "Costo mercancía vendida", "dinero"),
                 Col("margen_bruto", "Margen bruto estimado", "dinero"),
                 Col("margen_pct", "Margen bruto %", "porcentaje")]
        notes.append("Margen bruto estimado = ventas sin impuestos − costo promedio de lo vendido. "
                     "NO es utilidad neta: no incluye gastos operativos.")
    return Report("Ventas por período", cols, rows, _f(p), notes, totals)


def rotation(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    days = max((p.hasta - p.desde).days + 1, 1)
    sold = (select(SaleLine.product_id, func.sum(SaleLine.quantity_base).label("q"),
                   func.sum(SaleLine.net_amount).label("v"), func.count(func.distinct(SaleLine.sale_id)).label("n"))
            .join(Sale, Sale.id == SaleLine.sale_id)
            .where(Sale.status == SaleStatus.CONFIRMADA, Sale.sold_at >= lo, Sale.sold_at < hi)
            .group_by(SaleLine.product_id).subquery())
    stmt = (select(Product.name, Unit.code, sold.c.q, sold.c.v, sold.c.n, StockBalance.quantity)
            .join(sold, sold.c.product_id == Product.id).join(Unit, Unit.id == Product.base_unit_id)
            .join(StockBalance, StockBalance.product_id == Product.id, isouter=True)
            .order_by(sold.c.q.desc()))
    rows = []
    for name, unit, q, v, n, stock in db.execute(_product_filters(stmt, p)).all():
        daily = q / days
        rows.append({"producto": name, "unidad": unit, "cantidad_vendida": q, "ventas": n, "valor": v,
                     "promedio_diario": daily.quantize(Decimal("0.01")), "existencia": stock or ZERO,
                     "cobertura_dias": ((stock or ZERO) / daily).quantize(Decimal("0.1")) if daily > 0 else None})
    cols = [Col("producto", "Producto"), Col("unidad", "Unidad"), Col("cantidad_vendida", "Cantidad vendida", "cantidad"),
            Col("ventas", "N.º ventas", "numero"), Col("valor", "Valor vendido", "dinero"),
            Col("promedio_diario", "Promedio diario", "cantidad"), Col("existencia", "Existencia actual", "cantidad"),
            Col("cobertura_dias", "Cobertura estimada (días)", "numero")]
    return Report("Productos de mayor rotación", cols, rows, _f(p),
                  [f"Período de {days} días. Cobertura = existencia actual / promedio diario vendido (estimación)."])


def waste_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    costs = cu.can(P.COSTOS_VER)
    mv = (select(StockMovement.product_id, func.sum(-StockMovement.quantity_base).label("q"),
                 func.sum(StockMovement.total_cost).label("c"), func.count().label("n"))
          .where(StockMovement.movement_type == MovementType.MERMA, StockMovement.created_at >= lo,
                 StockMovement.created_at < hi, StockMovement.id.not_in(
                     select(StockMovement.reverses_movement_id).where(StockMovement.reverses_movement_id.is_not(None))))
          .group_by(StockMovement.product_id).subquery())
    rec = (select(PurchaseLine.product_id, func.sum(PurchaseLine.waste_qty).label("q"))
           .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
           .where(Purchase.status == PurchaseStatus.CONFIRMADA, Purchase.purchase_date >= p.desde,
                  Purchase.purchase_date <= p.hasta, PurchaseLine.waste_qty > 0)
           .group_by(PurchaseLine.product_id).subquery())
    stmt = (select(Product.name, Unit.code, mv.c.q, mv.c.c, mv.c.n, rec.c.q)
            .join(Unit, Unit.id == Product.base_unit_id)
            .join(mv, mv.c.product_id == Product.id, isouter=True)
            .join(rec, rec.c.product_id == Product.id, isouter=True)
            .where((mv.c.q.is_not(None)) | (rec.c.q.is_not(None)))
            .order_by(func.coalesce(mv.c.c, 0).desc()))
    rows, total = [], ZERO
    for name, unit, q, c, n, rq in db.execute(_product_filters(stmt, p)).all():
        row = {"producto": name, "unidad": unit, "registros": n or 0, "merma_inventario": q or ZERO,
               "merma_recepcion": rq or ZERO}
        if costs:
            row["costo_merma_inventario"] = c or ZERO
            total += c or ZERO
        rows.append(row)
    cols = [Col("producto", "Producto"), Col("unidad", "Unidad"), Col("registros", "Registros", "numero"),
            Col("merma_inventario", "Merma en inventario", "cantidad"),
            Col("merma_recepcion", "Merma en recepción", "cantidad")]
    notes = ["Merma en inventario: registrada como movimiento (excluye las revertidas).",
             "Merma en recepción: detectada al recibir compras; su costo ya está incluido en el costo unitario."]
    if costs:
        cols.append(Col("costo_merma_inventario", "Costo merma inventario", "dinero"))
    return Report("Mermas registradas", cols, rows, _f(p), notes, {"costo_merma_inventario": total} if costs else {})


def cost_evolution(db: Session, cu: CurrentUser, p: Params) -> Report:
    _need_product(p)
    stmt = (select(Purchase.purchase_date, Supplier.name, PurchaseLine.usable_qty, PurchaseLine.unit_cost_base,
                   Purchase.id)
            .join(Purchase, Purchase.id == PurchaseLine.purchase_id).join(Supplier, Supplier.id == Purchase.supplier_id)
            .where(PurchaseLine.product_id == p.product_id, Purchase.status == PurchaseStatus.CONFIRMADA,
                   Purchase.purchase_date >= p.desde, Purchase.purchase_date <= p.hasta)
            .order_by(Purchase.purchase_date, Purchase.id))
    rows = [{"fecha": d.isoformat(), "proveedor": s, "cantidad": q, "costo_unitario_efectivo": c, "compra": pid}
            for d, s, q, c, pid in db.execute(stmt).all()]
    bal = db.get(StockBalance, p.product_id)
    cols = [Col("fecha", "Fecha", "fecha"), Col("proveedor", "Proveedor"), Col("cantidad", "Cantidad útil", "cantidad"),
            Col("costo_unitario_efectivo", "Costo unitario efectivo", "dinero"), Col("compra", "Compra", "numero")]
    return Report(f"Evolución de costos — {db.get(Product, p.product_id).name}", cols, rows, _f(p),
                  ["Costo efectivo por unidad base de cada compra confirmada (incluye transporte, "
                   "descuentos y merma de recepción)."],
                  {"costo_promedio_vigente": bal.avg_cost if bal else None})


def market_evolution(db: Session, cu: CurrentUser, p: Params) -> Report:
    stmt = (select(MarketQuote, MarketProduct.name, MarketProduct.price_unit_code)
            .join(MarketProduct, MarketProduct.id == MarketQuote.market_product_id)
            .where(MarketQuote.price_date >= p.desde, MarketQuote.price_date <= p.hasta)
            .order_by(MarketProduct.name, MarketQuote.market, MarketQuote.price_date))
    if p.product_id:
        maps = db.scalars(select(ProductMarketMapping).where(ProductMarketMapping.product_id == p.product_id)).all()
        if not maps:
            return Report("Evolución de precios de mercado", [], [], _f(p),
                          ["El producto no tiene correspondencias con productos de la fuente externa."])
        stmt = stmt.where(func.row(MarketQuote.market_product_id, MarketQuote.market).in_(
            [func.row(m.market_product_id, m.market) for m in maps]))
    elif p.mercado:
        stmt = stmt.where(MarketQuote.market == p.mercado.upper())
    else:
        raise AppError("Indique un producto o un mercado", code="filtro_requerido")
    rows = [{"fecha": q.price_date.isoformat(), "producto_fuente": n, "mercado": q.market, "precio": q.price,
             "unidad_fuente": u or "por confirmar", "variacion": q.variation_rate,
             "alerta": "Variación extraordinaria" if q.flag.value != "normal" else ""}
            for q, n, u in db.execute(stmt.limit(5000)).all()]
    cols = [Col("fecha", "Fecha de referencia", "fecha"), Col("producto_fuente", "Producto (fuente)"),
            Col("mercado", "Mercado"), Col("precio", "Precio mayorista", "dinero"),
            Col("unidad_fuente", "Unidad"), Col("variacion", "Variación", "porcentaje"), Col("alerta", "Alerta")]
    return Report("Evolución de precios de mercado", cols, rows, _f(p),
                  ["Fuente: DANE – SIPSA. Precios mayoristas de referencia; no son precios minoristas.",
                   "La unidad del precio solo se muestra cuando fue confirmada por un usuario."])


def price_history_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    stmt = (select(PriceHistory, Product.name, User.full_name).join(Product, Product.id == PriceHistory.product_id)
            .join(User, User.id == PriceHistory.changed_by_id, isouter=True)
            .where(PriceHistory.changed_at >= lo, PriceHistory.changed_at < hi)
            .order_by(PriceHistory.changed_at.desc()))
    rows = [{"fecha": h.changed_at.astimezone(local_tz()).strftime("%Y-%m-%d %H:%M"), "producto": n,
             "anterior": h.old_price, "nuevo": h.new_price,
             "variacion": rate((h.new_price - h.old_price) / h.old_price) if h.old_price else None,
             "origen": h.source.value, "justificacion": h.justification, "usuario": u or "Sistema"}
            for h, n, u in db.execute(_product_filters(stmt, p, PriceHistory.product_id)).all()]
    cols = [Col("fecha", "Fecha"), Col("producto", "Producto"), Col("anterior", "Precio anterior", "dinero"),
            Col("nuevo", "Precio nuevo", "dinero"), Col("variacion", "Variación", "porcentaje"),
            Col("origen", "Origen"), Col("justificacion", "Justificación"), Col("usuario", "Usuario")]
    return Report("Historial de precios de venta", cols, rows, _f(p), ["Precios por unidad base, impuestos incluidos."])


def gross_margin_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    stmt = (select(Product.name, Category.name, func.sum(SaleLine.quantity_base),
                   func.sum(SaleLine.net_amount - SaleLine.tax_amount), func.sum(SaleLine.cost_amount))
            .join(Sale, Sale.id == SaleLine.sale_id).join(Product, Product.id == SaleLine.product_id)
            .join(Category, Category.id == Product.category_id)
            .where(Sale.status == SaleStatus.CONFIRMADA, Sale.sold_at >= lo, Sale.sold_at < hi)
            .group_by(Product.name, Category.name).order_by(func.sum(SaleLine.net_amount - SaleLine.tax_amount
                                                                     - SaleLine.cost_amount).desc()))
    rows = []
    for name, cat, q, net, cost in db.execute(_product_filters(stmt, p, SaleLine.product_id)).all():
        m = net - cost
        rows.append({"producto": name, "categoria": cat, "cantidad": q, "ventas_sin_impuestos": net,
                     "costo": cost, "margen_bruto": m, "margen_pct": rate(m / net) if net > 0 else None})
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ("ventas_sin_impuestos", "costo", "margen_bruto")}
    if totals["ventas_sin_impuestos"] > 0:
        totals["margen_pct"] = rate(totals["margen_bruto"] / totals["ventas_sin_impuestos"])
    cols = [Col("producto", "Producto"), Col("categoria", "Categoría"), Col("cantidad", "Cantidad", "cantidad"),
            Col("ventas_sin_impuestos", "Ventas sin impuestos", "dinero"), Col("costo", "Costo vendido", "dinero"),
            Col("margen_bruto", "Margen bruto estimado", "dinero"), Col("margen_pct", "Margen %", "porcentaje")]
    return Report("Margen bruto estimado por producto", cols, rows, _f(p),
                  ["Margen bruto estimado = ventas sin impuestos − costo promedio de lo vendido.",
                   "NO es utilidad neta: no descuenta gastos operativos (arriendo, nómina, servicios, transporte de "
                   "reparto, etc.)."], totals)


def cost_vs_market_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    cfg = get_commercial_settings(db)
    stmt = select(ProductMarketMapping).where(ProductMarketMapping.is_active.is_(True))
    if p.product_id:
        stmt = stmt.where(ProductMarketMapping.product_id == p.product_id)
    rows = []
    for m in db.scalars(stmt):
        ref = market.reference_for_mapping(db, m, cfg.stale_quote_days)
        bal = db.get(StockBalance, m.product_id)
        cost = bal.avg_cost if bal and bal.avg_cost > 0 else None
        prod = db.get(Product, m.product_id)
        rows.append({
            "producto": prod.name, "unidad": prod.base_unit.code, "costo_promedio": cost,
            "producto_fuente": ref.market_product_name, "mercado": ref.market,
            "fecha_mercado": ref.price_date.isoformat() if ref.price_date else None,
            "precio_mercado_base": ref.price_per_base_unit,
            "diferencia": market.cost_vs_market(cost, ref.price_per_base_unit) if ref.usable else None,
            "calidad_dato": "Utilizable" if ref.usable else ref.reason})
    cols = [Col("producto", "Producto"), Col("unidad", "Unidad base"), Col("costo_promedio", "Costo real promedio", "dinero"),
            Col("producto_fuente", "Producto (fuente)"), Col("mercado", "Mercado"),
            Col("fecha_mercado", "Fecha cotización", "fecha"),
            Col("precio_mercado_base", "Precio mayorista por unidad base", "dinero"),
            Col("diferencia", "Costo vs. mercado", "porcentaje"), Col("calidad_dato", "Calidad del dato")]
    return Report("Costos reales vs. referencias de mercado", cols, rows, {},
                  ["Diferencia = costo real / precio mayorista − 1. Positivo: se compra por encima del mercado.",
                   "Solo se compara cuando la unidad está confirmada y la cotización no está desactualizada "
                   f"(máximo {cfg.stale_quote_days} días)."])


def audit_report(db: Session, cu: CurrentUser, p: Params) -> Report:
    lo, hi = local_day_bounds(p.desde, p.hasta)
    stmt = (select(AuditLog, User.full_name).join(User, User.id == AuditLog.user_id, isouter=True)
            .where(AuditLog.created_at >= lo, AuditLog.created_at < hi).order_by(AuditLog.id.desc()).limit(5000))
    rows = [{"fecha": a.created_at.astimezone(local_tz()).strftime("%Y-%m-%d %H:%M:%S"), "usuario": u or "Sistema",
             "accion": a.action, "entidad": a.entity or "", "id_entidad": a.entity_id or "", "ip": a.ip or "",
             "detalle": "" if a.details is None else str(a.details)[:500]}
            for a, u in db.execute(stmt).all()]
    cols = [Col("fecha", "Fecha"), Col("usuario", "Usuario"), Col("accion", "Acción"), Col("entidad", "Entidad"),
            Col("id_entidad", "ID"), Col("ip", "IP"), Col("detalle", "Detalle")]
    return Report("Auditoría", cols, rows, _f(p), ["Máximo 5.000 registros por consulta."])


def _need_product(p: Params) -> None:
    if not p.product_id:
        raise AppError("Seleccione un producto", code="filtro_requerido")


def _f(p: Params) -> dict:
    return {k: v for k, v in {"desde": p.desde, "hasta": p.hasta, "producto": p.product_id,
                              "categoria": p.category_id, "proveedor": p.supplier_id,
                              "mercado": p.mercado}.items() if v}


REGISTRY: dict[str, tuple[Callable, tuple[P, ...]]] = {
    "existencias": (stock_report, (P.REPORTES_OPERATIVOS,)),
    "movimientos": (movements_report, (P.REPORTES_OPERATIVOS,)),
    "rotacion": (rotation, (P.REPORTES_OPERATIVOS,)),
    "mermas": (waste_report, (P.REPORTES_OPERATIVOS,)),
    "compras-por-proveedor": (purchases_by_supplier, (P.REPORTES_FINANCIEROS, P.COMPRAS_VER)),
    "ventas-por-periodo": (sales_by_period, (P.REPORTES_FINANCIEROS,)),
    "margen-bruto": (gross_margin_report, (P.REPORTES_FINANCIEROS, P.COSTOS_VER)),
    "evolucion-costos": (cost_evolution, (P.COSTOS_VER,)),
    "evolucion-mercado": (market_evolution, (P.MERCADO_VER,)),
    "historial-precios": (price_history_report, (P.PRECIOS_VER,)),
    "costo-vs-mercado": (cost_vs_market_report, (P.COSTOS_VER, P.MERCADO_VER)),
    "auditoria": (audit_report, (P.AUDITORIA,)),
}


def run(db: Session, cu: CurrentUser, name: str, p: Params) -> Report:
    if name not in REGISTRY:
        raise AppError("Reporte desconocido", code="reporte_desconocido", status_code=404)
    fn, perms = REGISTRY[name]
    missing = [x.value for x in perms if not cu.can(x)]
    if missing:
        raise Forbidden("No tiene permiso para este reporte", details=missing)
    if p.hasta < p.desde:
        raise AppError("La fecha final es anterior a la inicial", code="rango_invalido")
    if (p.hasta - p.desde).days > 731:
        raise AppError("El rango máximo es de dos años", code="rango_invalido")
    return fn(db, cu, p)


# ------------------------------------------------------------------ panel y alertas

def dashboard(db: Session, cu: CurrentUser, desde: date, hasta: date) -> dict:
    cfg = get_commercial_settings(db)
    lo, hi = local_day_bounds(desde, hasta)
    today = today_local()
    out: dict = {"periodo": {"desde": desde, "hasta": hasta}}

    out["productos_activos"] = db.scalar(select(func.count()).select_from(Product).where(Product.is_active.is_(True)))
    low = db.execute(select(Product.id, Product.name, StockBalance.quantity, Product.min_stock, Unit.code)
                     .join(StockBalance, StockBalance.product_id == Product.id)
                     .join(Unit, Unit.id == Product.base_unit_id)
                     .where(Product.is_active.is_(True), Product.min_stock > 0,
                            StockBalance.quantity <= Product.min_stock)
                     .order_by(StockBalance.quantity / Product.min_stock)).all()
    out["bajo_minimo"] = {"total": len(low), "items": [
        {"product_id": i, "producto": n, "existencia": q, "minimo": m, "unidad": u} for i, n, q, m, u in low[:10]]}
    exp = db.execute(select(Lot.id, Product.name, Lot.expiry_date, Lot.remaining_qty, Unit.code)
                     .join(Product, Product.id == Lot.product_id).join(Unit, Unit.id == Product.base_unit_id)
                     .where(Lot.remaining_qty > 0, Lot.expiry_date.is_not(None),
                            Lot.expiry_date <= today + timedelta(days=cfg.expiry_alert_days))
                     .order_by(Lot.expiry_date)).all()
    out["por_vencer"] = {"total": len(exp), "dias_alerta": cfg.expiry_alert_days, "items": [
        {"lot_id": i, "producto": n, "vence": d, "dias": (d - today).days, "cantidad": q, "unidad": u}
        for i, n, d, q, u in exp[:10]]}

    if cu.can(P.COSTOS_VER):
        out["valor_inventario_estimado"] = money(db.scalar(select(func.coalesce(func.sum(
            case((StockBalance.quantity > 0, StockBalance.quantity), else_=0) * StockBalance.avg_cost), 0))) or 0)

    if cu.can(P.REPORTES_FINANCIEROS):
        n, total, tax, cost = db.execute(select(func.count(Sale.id), func.coalesce(func.sum(Sale.total), 0),
                                                func.coalesce(func.sum(Sale.tax_total), 0),
                                                func.coalesce(func.sum(Sale.cost_total), 0))
                                         .where(Sale.status == SaleStatus.CONFIRMADA, Sale.sold_at >= lo,
                                                Sale.sold_at < hi)).one()
        out["ventas"] = {"cantidad": n, "total": money(total)}
        if cu.can(P.COSTOS_VER):
            out["ventas"]["margen_bruto_estimado"] = money(total - tax - cost)
        pn, ptotal = db.execute(select(func.count(Purchase.id), func.coalesce(func.sum(Purchase.total), 0))
                                .where(Purchase.status == PurchaseStatus.CONFIRMADA, Purchase.purchase_date >= desde,
                                       Purchase.purchase_date <= hasta)).one()
        out["compras"] = {"cantidad": pn, "total": money(ptotal)}

    top = db.execute(select(Product.name, Unit.code, func.sum(SaleLine.quantity_base))
                     .join(Sale, Sale.id == SaleLine.sale_id).join(Product, Product.id == SaleLine.product_id)
                     .join(Unit, Unit.id == Product.base_unit_id)
                     .where(Sale.status == SaleStatus.CONFIRMADA, Sale.sold_at >= lo, Sale.sold_at < hi)
                     .group_by(Product.name, Unit.code).order_by(func.sum(SaleLine.quantity_base).desc()).limit(5)).all()
    out["mayor_movimiento"] = [{"producto": n, "unidad": u, "cantidad": q} for n, u, q in top]

    if cu.can(P.MERCADO_VER):
        mapped = select(ProductMarketMapping.market_product_id, ProductMarketMapping.market).where(
            ProductMarketMapping.is_active.is_(True))
        changes = db.execute(select(MarketProduct.name, MarketQuote.market, MarketQuote.price_date,
                                    MarketQuote.price, MarketQuote.variation_rate)
                             .join(MarketProduct, MarketProduct.id == MarketQuote.market_product_id)
                             .where(MarketQuote.flag == "variacion_extraordinaria",
                                    MarketQuote.price_date >= today - timedelta(days=7),
                                    func.row(MarketQuote.market_product_id, MarketQuote.market).in_(mapped))
                             .order_by(MarketQuote.price_date.desc()).limit(10)).all()
        out["cambios_mercado"] = [{"producto_fuente": n, "mercado": m, "fecha": d, "precio": p, "variacion": v}
                                  for n, m, d, p, v in changes]
        src = db.scalar(select(MarketSource).order_by(MarketSource.id).limit(1))
        hb = db.get(WorkerHeartbeat, "worker")
        out["actualizacion"] = None if src is None else {
            "fuente": src.name, "estado": src.last_status, "ultimo_exito": src.last_success_at,
            "ultimo_intento": src.last_attempt_at, "ultima_fecha_datos": src.latest_price_date,
            "error": src.last_error,
            "proceso_activo": bool(hb and (now_utc() - hb.last_beat_at).total_seconds() < 180)}

    if cu.can(P.PRECIOS_VER):
        out["sugerencias_pendientes"] = db.scalar(select(func.count()).select_from(PriceSuggestion).where(
            PriceSuggestion.status == SuggestionStatus.PENDIENTE))
    return out


def _q(v: Decimal) -> str:
    """Cantidad legible en formato colombiano (coma decimal, sin ceros sobrantes)."""
    s = f"{v.normalize():f}"
    ent, _, dec = s.partition(".")
    ent = f"{int(ent):,}".replace(",", ".") if ent.lstrip("-").isdigit() else ent
    return f"{ent},{dec}" if dec else ent


def alerts(db: Session, cu: CurrentUser) -> list[dict]:
    d = dashboard(db, cu, *default_period())
    out = []
    for i in d["bajo_minimo"]["items"]:
        out.append({"tipo": "existencia_baja", "nivel": "advertencia",
                    "mensaje": f"{i['producto']}: {_q(i['existencia'])} {i['unidad']} (mínimo {_q(i['minimo'])})"})
    for i in d["por_vencer"]["items"]:
        lvl = "critica" if i["dias"] < 0 else "advertencia"
        txt = f"vencido hace {-i['dias']} días" if i["dias"] < 0 else f"vence en {i['dias']} días"
        out.append({"tipo": "vencimiento", "nivel": lvl,
                    "mensaje": f"{i['producto']}: lote {txt} ({_q(i['cantidad'])} {i['unidad']})"})
    act = d.get("actualizacion")
    if act:
        if act["estado"] and act["estado"].startswith("error"):
            out.append({"tipo": "mercado", "nivel": "critica",
                        "mensaje": f"La última actualización de precios de mercado falló: {act['error']}"})
        if not act["proceso_activo"]:
            out.append({"tipo": "proceso", "nivel": "critica",
                        "mensaje": "El proceso automático no está activo (sin latido en los últimos 3 minutos)"})
        if act["ultima_fecha_datos"] and (today_local() - act["ultima_fecha_datos"]).days > 3:
            out.append({"tipo": "mercado", "nivel": "advertencia",
                        "mensaje": f"Las cotizaciones de mercado más recientes son del {act['ultima_fecha_datos']}"})
    if d.get("sugerencias_pendientes"):
        out.append({"tipo": "precios", "nivel": "info",
                    "mensaje": f"{d['sugerencias_pendientes']} sugerencia(s) de precio pendientes de revisión"})
    for c in d.get("cambios_mercado", [])[:5]:
        out.append({"tipo": "variacion_mercado", "nivel": "info",
                    "mensaje": f"{c['producto_fuente']} en {c['mercado']}: variación de "
                               f"{(c['variacion'] or 0) * 100:.0f} % el {c['fecha']}"})
    return out
