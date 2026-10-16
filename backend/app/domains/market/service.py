"""Sincronización de cotizaciones y consulta de referencias de mercado.

Garantías:
* Idempotente: la clave natural (fuente, producto externo, mercado, fecha) es UNIQUE y se inserta
  con ON CONFLICT DO NOTHING; repetir una ejecución no duplica datos.
* Exclusión mutua: pg_try_advisory_lock por fuente; dos ejecuciones simultáneas no se pisan.
* Trazabilidad: cada ejecución queda en market_sync_runs con conteos y error.
* Un fallo NO borra cotizaciones previas; las referencias muestran su fecha y si están desactualizadas.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.numbers import ONE, money, rate
from app.core.timeutil import now_utc, today_local
from app.domains.market.connectors.base import FetchStats, MarketConnector, SourceError
from app.domains.market.models import (
    MarketProduct, MarketQuote, MarketSource, MarketSyncRun, ProductMarketMapping, QuoteFlag, SyncStatus,
)
from app.domains.settings.service import get_commercial_settings

log = logging.getLogger("app.market")

INITIAL_DAYS = 60       # primera carga: últimos 60 días
OVERLAP_DAYS = 7        # cargas siguientes: re-lee 7 días para capturar publicaciones tardías
BATCH = 1000
LOCK_NAMESPACE = 71_001


@dataclass
class SyncOutcome:
    run_id: int | None
    status: str
    message: str


def run_sync(db: Session, connector: MarketConnector, *, triggered_by: str,
             attempt: int = 1) -> SyncOutcome:
    source = db.scalar(select(MarketSource).where(MarketSource.code == connector.code))
    if source is None or not source.is_active:
        return SyncOutcome(None, "omitida", f"La fuente '{connector.code}' no existe o está inactiva")
    # El bloqueo consultivo es por conexión: se mantiene en una conexión dedicada durante toda
    # la ejecución (la sesión ORM puede cambiar de conexión entre transacciones).
    with db.get_bind().connect() as lock_conn:
        args = {"a": LOCK_NAMESPACE, "b": source.id}
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:a, :b)"), args).scalar():
            return SyncOutcome(None, "omitida", "Ya hay una actualización en curso para esta fuente")
        try:
            return _run_locked(db, source, connector, triggered_by=triggered_by, attempt=attempt)
        finally:
            db.rollback()
            lock_conn.execute(text("SELECT pg_advisory_unlock(:a, :b)"), args)
            lock_conn.commit()


def _run_locked(db: Session, source: MarketSource, connector: MarketConnector, *, triggered_by: str,
                attempt: int) -> SyncOutcome:
    cfg = get_commercial_settings(db)
    run = MarketSyncRun(source_id=source.id, status=SyncStatus.EN_CURSO, attempt=attempt,
                        triggered_by=triggered_by)
    db.add(run)
    source.last_attempt_at = now_utc()
    source.sync_requested_at = None
    db.commit()

    since = (source.latest_price_date - timedelta(days=OVERLAP_DAYS) if source.latest_price_date
             else today_local() - timedelta(days=INITIAL_DAYS))
    stats = FetchStats()
    products: dict[str, int] = {mp.external_code: mp.id for mp in db.scalars(
        select(MarketProduct).where(MarketProduct.source_id == source.id))}
    inserted = duplicated = 0
    max_date: date | None = None
    seen: set[tuple] = set()
    batch: list[dict] = []

    def flush() -> None:
        nonlocal inserted, duplicated
        if not batch:
            return
        res = db.execute(pg_insert(MarketQuote).values(batch).on_conflict_do_nothing(
            index_elements=["source_id", "market_product_id", "market", "price_date"]
        ).returning(MarketQuote.id))
        n = len(res.fetchall())
        inserted += n
        duplicated += len(batch) - n
        batch.clear()

    try:
        for q in connector.fetch(since, stats):
            mp_id = products.get(q.external_code)
            if mp_id is None:
                mp = MarketProduct(source_id=source.id, external_code=q.external_code, name=q.name,
                                   group_name=q.group_name)
                db.add(mp)
                db.flush()
                mp_id = products[q.external_code] = mp.id
            key = (mp_id, q.market, q.price_date)
            if key in seen:                       # duplicado dentro de la misma respuesta
                duplicated += 1
                continue
            seen.add(key)
            max_date = max(max_date or q.price_date, q.price_date)
            batch.append(dict(source_id=source.id, market_product_id=mp_id, market=q.market,
                              price_date=q.price_date, price=q.price, price_min=q.price_min,
                              price_max=q.price_max, external_id=q.external_id,
                              published_at=q.published_at, sync_run_id=run.id))
            if len(batch) >= BATCH:
                flush()
        flush()
        anomalies = _flag_variations(db, run.id, cfg.market_variation_alert_rate)
    except SourceError as e:
        db.rollback()
        return _fail(db, source, run, stats, str(e), transient=e.transient)
    except Exception as e:  # noqa: BLE001 — se registra y no se propaga al programador
        db.rollback()
        log.exception("Fallo inesperado en la sincronización %s", source.code)
        return _fail(db, source, run, stats, f"Error inesperado: {type(e).__name__}", transient=False)

    run.status = SyncStatus.EXITO if inserted else SyncStatus.SIN_DATOS_NUEVOS
    run.finished_at = now_utc()
    run.bytes_downloaded = stats.bytes_downloaded
    run.records_read = stats.records_read
    run.records_inserted = inserted
    run.records_duplicated = duplicated
    run.records_invalid = stats.records_invalid
    run.records_out_of_scope = stats.records_out_of_scope
    run.anomalies = anomalies
    run.max_price_date = max_date
    if stats.invalid_samples:
        run.error_message = "Registros descartados (muestra): " + "; ".join(stats.invalid_samples)
    source.last_success_at = run.finished_at
    source.last_status = run.status.value
    source.last_error = None
    source.latest_price_date = db.scalar(
        select(func.max(MarketQuote.price_date)).where(MarketQuote.source_id == source.id))
    db.commit()
    msg = (f"{inserted} cotizaciones nuevas, {duplicated} ya existentes, "
           f"{stats.records_invalid} inválidas, {anomalies} variaciones extraordinarias")
    log.info("Sincronización %s: %s", source.code, msg)
    return SyncOutcome(run.id, run.status.value, msg)


def _fail(db: Session, source: MarketSource, run: MarketSyncRun, stats: FetchStats, message: str,
          *, transient: bool) -> SyncOutcome:
    run = db.merge(run)
    source = db.merge(source)
    run.status = SyncStatus.ERROR
    run.finished_at = now_utc()
    run.bytes_downloaded = stats.bytes_downloaded
    run.records_read = stats.records_read
    run.records_invalid = stats.records_invalid
    run.error_message = message[:2000]
    source.last_status = "error_transitorio" if transient else "error"
    source.last_error = message[:2000]
    db.commit()
    log.warning("Sincronización %s falló: %s", source.code, message)
    return SyncOutcome(run.id, "error_transitorio" if transient else "error", message)


def _flag_variations(db: Session, run_id: int, threshold: Decimal) -> int:
    """Calcula la variación frente a la cotización anterior del mismo producto y mercado."""
    db.execute(text("""
        WITH ordered AS (
            SELECT id, sync_run_id, price,
                   lag(price) OVER (PARTITION BY source_id, market_product_id, market
                                    ORDER BY price_date) AS prev
            FROM market_quotes
            WHERE (market_product_id, market) IN (
                SELECT market_product_id, market FROM market_quotes WHERE sync_run_id = :run)
        )
        UPDATE market_quotes q
           SET variation_rate = CASE WHEN o.prev > 0
                                     THEN LEAST(GREATEST(round((o.price - o.prev) / o.prev, 4), -9.9999), 9.9999)
                                END,
               flag = CASE WHEN o.prev > 0 AND abs((o.price - o.prev) / o.prev) > :th
                           THEN 'variacion_extraordinaria' ELSE 'normal' END
          FROM ordered o
         WHERE q.id = o.id AND o.sync_run_id = :run
    """), {"run": run_id, "th": threshold})
    return db.scalar(select(func.count()).select_from(MarketQuote).where(
        MarketQuote.sync_run_id == run_id, MarketQuote.flag == QuoteFlag.VARIACION_EXTRAORDINARIA)) or 0


# ------------------------------------------------------------------ referencias

@dataclass
class MarketReference:
    mapping_id: int
    product_id: int
    market_product_id: int
    market_product_name: str
    market: str
    quote_id: int | None
    price_date: date | None
    source_price: Decimal | None
    source_unit: str | None
    unit_confirmed: bool
    price_per_base_unit: Decimal | None   # solo si la unidad está confirmada
    days_old: int | None
    is_stale: bool
    flag: str | None
    variation_rate: Decimal | None
    usable: bool                           # apta para comparar con costos
    reason: str | None


def reference_for_mapping(db: Session, m: ProductMarketMapping, stale_days: int) -> MarketReference:
    q = db.scalar(select(MarketQuote).where(
        MarketQuote.market_product_id == m.market_product_id, MarketQuote.market == m.market)
        .order_by(MarketQuote.price_date.desc()).limit(1))
    mp = m.market_product
    confirmed = mp.price_unit_code is not None
    days = (today_local() - q.price_date).days if q else None
    stale = q is None or days > stale_days
    reason = None
    if q is None:
        reason = "No hay cotizaciones para este producto y mercado"
    elif not confirmed:
        reason = "La unidad del precio publicado no ha sido confirmada"
    elif stale:
        reason = f"Cotización desactualizada ({days} días; máximo {stale_days})"
    per_base = money(q.price * m.conversion_factor) if (q and confirmed) else None
    return MarketReference(
        mapping_id=m.id, product_id=m.product_id, market_product_id=mp.id, market_product_name=mp.name,
        market=m.market, quote_id=q.id if q else None, price_date=q.price_date if q else None,
        source_price=q.price if q else None, source_unit=mp.price_unit_code, unit_confirmed=confirmed,
        price_per_base_unit=per_base, days_old=days, is_stale=stale,
        flag=q.flag.value if q else None, variation_rate=q.variation_rate if q else None,
        usable=reason is None, reason=reason)


def references_for_product(db: Session, product_id: int) -> list[MarketReference]:
    cfg = get_commercial_settings(db)
    maps = db.scalars(select(ProductMarketMapping).where(
        ProductMarketMapping.product_id == product_id, ProductMarketMapping.is_active.is_(True))).all()
    return [reference_for_mapping(db, m, cfg.stale_quote_days) for m in maps]


def best_reference(db: Session, product_id: int) -> MarketReference | None:
    """La referencia utilizable más reciente; None si ninguna es fiable."""
    usable = [r for r in references_for_product(db, product_id) if r.usable]
    return max(usable, key=lambda r: r.price_date) if usable else None


def cost_vs_market(unit_cost: Decimal, market_price: Decimal | None) -> Decimal | None:
    if market_price is None or market_price <= 0 or unit_cost is None or unit_cost <= 0:
        return None
    return rate(unit_cost / market_price - ONE)
