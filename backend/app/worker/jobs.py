"""Trabajos del proceso automático. Funciones puras respecto del programador (se prueban solas)."""
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.timeutil import local_tz, now_utc
from app.domains.catalog.models import Product
from app.domains.market import service as market
from app.domains.market.connectors import get_connector
from app.domains.market.connectors.base import MarketConnector
from app.domains.market.models import MarketSource, WorkerHeartbeat
from app.domains.pricing import service as pricing
from app.domains.settings.service import get_commercial_settings

log = logging.getLogger("app.worker")

RETRY_DELAYS_MIN = (15, 45, 120)   # reintentos ante fallas transitorias de la fuente


def sync_source(db: Session, code: str, *, triggered_by: str, attempt: int = 1,
                connector_factory: Callable[[str], MarketConnector] = get_connector) -> market.SyncOutcome:
    cfg = get_commercial_settings(db)
    if not cfg.market_sync_enabled:
        return market.SyncOutcome(None, "omitida", "La actualización de mercado está deshabilitada")
    outcome = market.run_sync(db, connector_factory(code), triggered_by=triggered_by, attempt=attempt)
    if outcome.status in ("exito", "sin_datos_nuevos"):
        refresh_suggestions(db)
    return outcome


def refresh_suggestions(db: Session) -> dict:
    """Regenera sugerencias con los costos reales vigentes y, si el propietario lo habilitó,
    aplica la política automática con sus protecciones."""
    cfg = get_commercial_settings(db)
    created = applied = 0
    for product in db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.id)):
        s, _ = pricing.generate_suggestion(db, product, cfg, origin="automatica", user_id=None)
        if s is not None:
            created += 1
            if pricing.apply_automatic_policy(db, s, product, cfg):
                applied += 1
    db.commit()
    log.info("Sugerencias: %s generadas, %s aplicadas automáticamente", created, applied)
    return {"generadas": created, "aplicadas": applied}


def pending_requests(db: Session) -> list[tuple[str, int | None]]:
    rows = db.execute(select(MarketSource.code, MarketSource.sync_requested_by_id).where(
        MarketSource.is_active.is_(True), MarketSource.sync_requested_at.is_not(None))).all()
    return [(c, u) for c, u in rows]


def active_sources(db: Session) -> list[str]:
    return list(db.scalars(select(MarketSource.code).where(MarketSource.is_active.is_(True))))


def sources_without_success_on(db: Session, day) -> list[str]:
    """Fuentes activas cuya última actualización exitosa no ocurrió en `day` (fecha local)."""
    out = []
    for s in db.scalars(select(MarketSource).where(MarketSource.is_active.is_(True))):
        if s.last_success_at is None or s.last_success_at.astimezone(local_tz()).date() < day:
            out.append(s.code)
    return out


def heartbeat(db: Session, info: str, *, started: bool = False) -> None:
    values = {"name": "worker", "last_beat_at": now_utc(), "info": info[:500]}
    if started:
        values["started_at"] = now_utc()
    stmt = pg_insert(WorkerHeartbeat).values(**values)
    db.execute(stmt.on_conflict_do_update(index_elements=["name"],
                                          set_={k: v for k, v in values.items() if k != "name"}))
    db.commit()
