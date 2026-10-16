"""Proceso automático (contenedor `worker`).

* Actualización diaria de precios de mercado a la hora configurada (America/Bogota).
  SIPSA publica los datos diarios desde las 2:00 p. m.; por defecto se consulta a las 3:30 p. m.
* Atiende solicitudes manuales de actualización (cada minuto).
* Reintenta fallas transitorias a los 15, 45 y 120 minutos.
* Si el proceso estuvo detenido a la hora programada, se pone al día al arrancar: si hoy ya pasó
  la hora y la fuente no se actualizó con éxito hoy, ejecuta la actualización de inmediato.
* Publica un latido cada minuto para monitorización.
"""
import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.db import session_factory
from app.core.timeutil import local_tz, now_utc
from app.models import import_all_models
from app.worker import jobs

log = logging.getLogger("app.worker")


def _run(code: str, triggered_by: str, attempt: int, scheduler: BlockingScheduler) -> None:
    with session_factory()() as db:
        outcome = jobs.sync_source(db, code, triggered_by=triggered_by, attempt=attempt)
        jobs.heartbeat(db, f"{code}: {outcome.status} — {outcome.message}")
    log.info("%s intento %s: %s (%s)", code, attempt, outcome.status, outcome.message)
    if outcome.status == "error_transitorio" and attempt <= len(jobs.RETRY_DELAYS_MIN):
        when = now_utc() + timedelta(minutes=jobs.RETRY_DELAYS_MIN[attempt - 1])
        scheduler.add_job(_run, "date", run_date=when, args=[code, "programador:reintento", attempt + 1,
                                                             scheduler],
                          id=f"retry-{code}", replace_existing=True)
        log.warning("Reintento de %s programado para %s", code, when.isoformat())


def daily(scheduler: BlockingScheduler) -> None:
    with session_factory()() as db:
        codes = jobs.active_sources(db)
    for code in codes:
        _run(code, "programador", 1, scheduler)


def poll(scheduler: BlockingScheduler) -> None:
    with session_factory()() as db:
        requests = jobs.pending_requests(db)
        jobs.heartbeat(db, "en espera" if not requests else f"atendiendo {len(requests)} solicitud(es)")
    for code, user_id in requests:
        _run(code, f"usuario:{user_id}", 1, scheduler)


def catch_up(scheduler: BlockingScheduler) -> None:
    cfg = get_settings()
    now_local = now_utc().astimezone(local_tz())
    if (now_local.hour, now_local.minute) < (cfg.market_sync_hour, cfg.market_sync_minute):
        return
    with session_factory()() as db:
        due = jobs.sources_without_success_on(db, now_local.date())
    for code in due:
        log.info("Puesta al día: %s no se actualizó hoy; se ejecuta ahora", code)
        _run(code, "programador:puesta_al_dia", 1, scheduler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import_all_models()
    cfg = get_settings()
    scheduler = BlockingScheduler(timezone=local_tz(), job_defaults={"coalesce": True, "max_instances": 1})
    scheduler.add_job(daily, CronTrigger(hour=cfg.market_sync_hour, minute=cfg.market_sync_minute,
                                         timezone=local_tz()),
                      args=[scheduler], id="mercado-diario", misfire_grace_time=6 * 3600)
    scheduler.add_job(poll, "interval", minutes=1, args=[scheduler], id="solicitudes",
                      next_run_time=now_utc())
    scheduler.add_job(catch_up, "date", run_date=now_utc() + timedelta(seconds=5), args=[scheduler],
                      id="puesta-al-dia")
    with session_factory()() as db:
        jobs.heartbeat(db, "iniciado", started=True)
    log.info("Proceso automático iniciado; actualización diaria a las %02d:%02d (%s)",
             cfg.market_sync_hour, cfg.market_sync_minute, cfg.app_timezone)
    scheduler.start()


if __name__ == "__main__":
    main()
