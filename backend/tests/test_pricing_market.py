"""Precios y mercado.

IMPORTANTE: las respuestas SOAP de este archivo son SIMULADAS y solo existen dentro de las pruebas
automatizadas. No son cotizaciones reales y nunca se cargan en la base de datos de operación.
"""
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.core.timeutil import today_local
from app.domains.market import service as market
from app.domains.market.connectors.sipsa import SipsaCiudadConnector, normalize_market
from app.domains.pricing.service import compute_price, gross_margin
from app.worker import jobs
from tests.helpers import move, product

URL = "https://sipsa.prueba.invalid/servicio"


def soap(records: list[dict]) -> bytes:
    body = "".join(
        "<return>" + "".join(f"<{k}>{v}</{k}>" for k, v in r.items() if v is not None) + "</return>"
        for r in records)
    return ('<?xml version="1.0" encoding="UTF-8"?><S:Envelope xmlns:S="http://www.w3.org/2003/05/soap-envelope">'
            '<S:Body><ns2:promediosSipsaCiudadResponse xmlns:ns2="http://servicios.sipsa.co.gov.dane/">'
            f'{body}</ns2:promediosSipsaCiudadResponse></S:Body></S:Envelope>').encode()


def rec(city="BOGOTÁ, D.C.", code=27, name="Tomate de árbol", days_ago=1, price="3000", reg=None):
    d = today_local() - timedelta(days=days_ago)
    return {"ciudad": city, "codProducto": code, "producto": name, "enviado": 0,
            "fechaCaptura": f"{d.isoformat()}T00:00:00-05:00",
            "fechaCreacion": f"{(d + timedelta(days=1)).isoformat()}T14:00:00-05:00",
            "precioPromedio": price, "regId": reg or f"{code}{days_ago}{city[:3]}"}


def connector(handler, **kw) -> SipsaCiudadConnector:
    return SipsaCiudadConnector(URL, transport=httpx.MockTransport(handler), sleep=lambda s: None,
                                backoff=(0,), **kw)


def fixed(payload: bytes, status=200):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        assert request.headers["content-type"].startswith("application/soap+xml")
        assert b"promediosSipsaCiudad" in request.content
        return httpx.Response(status, content=payload)
    return handler, calls


def sync(db, handler, **kw):
    return market.run_sync(db, connector(handler, **kw), triggered_by="prueba")


# ------------------------------------------------------------------ fórmula

def test_price_formula():
    assert compute_price(Decimal(1000), Decimal("0.30"), step=Decimal(50)) == (Decimal("1428.57"), Decimal("1450.00"))
    assert compute_price(Decimal(1000), Decimal("0.30"), step=Decimal(0))[1] == Decimal("1428.57")
    assert compute_price(Decimal(1000), Decimal("0.30"), tax_rate=Decimal("0.19"), step=Decimal(50))[1] \
        == Decimal("1700.00")
    assert compute_price(Decimal(1000), Decimal("0.30"), commission=Decimal("0.05"), step=Decimal(50))[1] \
        == Decimal("1550.00")
    assert compute_price(Decimal(1000), Decimal("0.30"), step=Decimal(100), mode="abajo")[1] == Decimal("1400.00")
    # El margen bruto del precio resultante es el objetivo
    assert gross_margin(Decimal("1428.57"), Decimal(1000), Decimal(0)) == Decimal("0.3000")


@pytest.mark.parametrize("margin,commission,cost", [
    (Decimal(1), Decimal(0), Decimal(100)), (Decimal("-0.1"), Decimal(0), Decimal(100)),
    (Decimal("0.6"), Decimal("0.4"), Decimal(100)), (Decimal("0.3"), Decimal(0), Decimal(0)),
])
def test_invalid_margin_or_cost_rejected(margin, commission, cost):
    with pytest.raises(AppError):
        compute_price(cost, margin, commission=commission)


def test_margin_settings_rejected_via_api(owner):
    assert owner.patch("/api/v1/configuracion", json={"default_margin": "1.0"}).status_code == 400
    assert owner.patch("/api/v1/configuracion", json={"default_margin": "-0.2"}).status_code == 400


# ------------------------------------------------------------------ conector y sincronización

def test_sync_inserts_validates_and_deduplicates(db):
    payload = soap([
        rec(days_ago=1), rec(days_ago=2, price="2800"),
        rec(city="SAN JOSÉ DE CÚCUTA", days_ago=1),                 # alias de ciudad
        rec(days_ago=1, reg="dup"),                                  # duplicado en la misma respuesta
        {**rec(code=30, name="Papa criolla"), "precioPromedio": None},   # incompleto
        rec(code=31, name="Plátano", price="abc"),                   # precio no numérico
        rec(code=32, name="Lulo", price="0"),                        # precio no positivo
        rec(days_ago=400),                                           # fuera del rango a cargar
    ])
    handler, calls = fixed(payload)
    out = sync(db, handler)
    assert out.status == "exito", out.message
    run = db.execute(text("SELECT * FROM market_sync_runs")).mappings().one()
    assert run["records_read"] == 8
    assert run["records_inserted"] == 3
    assert run["records_duplicated"] == 1
    assert run["records_invalid"] == 3
    assert run["records_out_of_scope"] == 1
    assert "faltan precioPromedio" in run["error_message"]
    markets = set(db.execute(text("SELECT market FROM market_quotes")).scalars())
    assert markets == {"BOGOTÁ, D.C.", "CÚCUTA"}
    src = db.execute(text("SELECT last_status, latest_price_date FROM market_sources")).one()
    assert src[0] == "exito" and src[1] == today_local() - timedelta(days=1)
    # Productos externos creados SIN unidad confirmada
    assert db.execute(text("SELECT count(*) FROM market_products WHERE price_unit_code IS NOT NULL")
                      ).scalar() == 0

    # Segunda ejecución con los mismos datos: idempotente
    out = sync(db, fixed(payload)[0])
    assert out.status == "sin_datos_nuevos"
    assert db.execute(text("SELECT count(*) FROM market_quotes")).scalar() == 3


def test_city_normalization():
    assert normalize_market(" san  josé de cúcuta ") == "CÚCUTA"
    assert normalize_market("Bogotá, D.C.") == "BOGOTÁ, D.C."


def test_network_errors_retry_then_fail_keeping_previous_quotes(db):
    sync(db, fixed(soap([rec(days_ago=1)]))[0])
    calls = []

    def boom(request):
        calls.append(1)
        raise httpx.ConnectTimeout("simulado", request=request)

    out = sync(db, boom, retries=2)
    assert out.status == "error_transitorio" and len(calls) == 3
    assert db.execute(text("SELECT count(*) FROM market_quotes")).scalar() == 1   # se conserva
    src = db.execute(text("SELECT last_status, last_error, last_success_at FROM market_sources")).one()
    assert src[0] == "error_transitorio" and "ConnectTimeout" in src[1] and src[2] is not None
    runs = db.execute(text("SELECT status FROM market_sync_runs ORDER BY id")).scalars().all()
    assert runs == ["exito", "error"]


def test_http_errors(db):
    h500, c500 = fixed(b"error", status=503)
    assert sync(db, h500, retries=1).status == "error_transitorio" and len(c500) == 2
    h404, c404 = fixed(b"no", status=404)
    assert sync(db, h404, retries=3).status == "error" and len(c404) == 1   # no se reintenta


def test_invalid_and_incomplete_responses(db):
    truncated = soap([rec()])[:-60]
    assert sync(db, fixed(truncated)[0], retries=0).status == "error_transitorio"
    fault = (b'<?xml version="1.0"?><S:Envelope xmlns:S="http://www.w3.org/2003/05/soap-envelope"><S:Body>'
             b'<S:Fault><S:Reason><S:Text>Servicio no disponible</S:Text></S:Reason></S:Fault></S:Body></S:Envelope>')
    out = sync(db, fixed(fault)[0])
    assert out.status == "error" and "Servicio no disponible" in out.message
    other = b'<?xml version="1.0"?><html><body>Mantenimiento</body></html>'
    assert sync(db, fixed(other)[0]).status == "error"
    assert sync(db, fixed(b"")[0], retries=0).status == "error_transitorio"
    # Entidades XML externas (XXE) se rechazan
    xxe = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
           b'<S:Envelope xmlns:S="http://www.w3.org/2003/05/soap-envelope"><S:Body>&e;</S:Body></S:Envelope>')
    assert sync(db, fixed(xxe)[0]).status == "error"
    assert db.execute(text("SELECT count(*) FROM market_quotes")).scalar() == 0


def test_no_new_data(db):
    sync(db, fixed(soap([rec(days_ago=3)]))[0])
    out = sync(db, fixed(soap([rec(days_ago=3)]))[0])
    assert out.status == "sin_datos_nuevos"


def test_extraordinary_variation_flagged(db):
    sync(db, fixed(soap([rec(days_ago=3, price="1000"), rec(days_ago=2, price="1100"),
                         rec(days_ago=1, price="1700")]))[0])
    rows = db.execute(text("SELECT price_date, variation_rate, flag FROM market_quotes ORDER BY price_date")).all()
    assert rows[0][1] is None
    assert rows[1][1] == Decimal("0.1000") and rows[1][2] == "normal"
    assert rows[2][2] == "variacion_extraordinaria"
    assert db.execute(text("SELECT anomalies FROM market_sync_runs")).scalar() == 1


def test_concurrent_sync_is_excluded(db, owner_engine):
    src_id = db.execute(text("SELECT id FROM market_sources")).scalar()
    with owner_engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:a, :b)"), {"a": market.LOCK_NAMESPACE, "b": src_id})
        out = sync(db, fixed(soap([rec()]))[0])
        assert out.status == "omitida"
        other.execute(text("SELECT pg_advisory_unlock(:a, :b)"), {"a": market.LOCK_NAMESPACE, "b": src_id})
    assert sync(db, fixed(soap([rec()]))[0]).status == "exito"


# ------------------------------------------------------------------ correspondencias y referencias

def _setup_product_with_market(owner, db, *, days_ago=1, market_price="3000", cost_="2000",
                               confirm_unit=True):
    sync(db, fixed(soap([rec(days_ago=days_ago, price=market_price)]))[0])
    p = product(owner, db, sku="TA", name="Tomate de árbol", price="3500")
    if cost_:
        move(owner, p["id"], "inicial", "10", cost=cost_)
    mp = owner.get("/api/v1/mercado/productos-externos").json()[0]
    if confirm_unit:
        assert owner.patch(f"/api/v1/mercado/productos-externos/{mp['id']}",
                           json={"price_unit_code": "kg"}).status_code == 200
    r = owner.post("/api/v1/mercado/correspondencias", json={
        "product_id": p["id"], "market_product_id": mp["id"], "market": "Bogotá, D.C.",
        "conversion_factor": "1"})
    assert r.status_code == 201, r.text
    return p


def test_mapping_requires_existing_market_data(owner, db):
    sync(db, fixed(soap([rec()]))[0])
    p = product(owner, db, sku="X")
    mp = owner.get("/api/v1/mercado/productos-externos").json()[0]
    r = owner.post("/api/v1/mercado/correspondencias", json={
        "product_id": p["id"], "market_product_id": mp["id"], "market": "PASTO", "conversion_factor": "1"})
    assert r.status_code == 400 and r.json()["error"]["codigo"] == "mercado_sin_datos"


def test_reference_usable_only_with_confirmed_unit_and_fresh_quote(owner, employee, db):
    p = _setup_product_with_market(owner, db, confirm_unit=False)
    ref = owner.get("/api/v1/mercado/referencias", params={"product_id": p["id"]}).json()[0]
    assert ref["usable"] is False and "unidad" in ref["reason"]
    assert ref["price_per_base_unit"] is None and ref["source_price"] == "3000.00"

    mp_id = ref["market_product_id"]
    owner.patch(f"/api/v1/mercado/productos-externos/{mp_id}", json={"price_unit_code": "kg"})
    ref = owner.get("/api/v1/mercado/referencias", params={"product_id": p["id"]}).json()[0]
    assert ref["usable"] is True and ref["price_per_base_unit"] == "3000.00"
    assert ref["cost_vs_market"] == "-0.3333"          # costo 2.000 vs. mercado 3.000
    emp = employee.get("/api/v1/mercado/referencias").json()[0]
    assert emp["unit_cost"] is None and emp["cost_vs_market"] is None


def test_stale_quote_not_used(owner, db):
    p = _setup_product_with_market(owner, db, days_ago=10)
    ref = owner.get("/api/v1/mercado/referencias", params={"product_id": p["id"]}).json()[0]
    assert ref["is_stale"] is True and ref["usable"] is False and ref["days_old"] == 10
    r = owner.post("/api/v1/precios/sugerencias/generar", json={"product_ids": [p["id"]]}).json()
    s = owner.get("/api/v1/precios/sugerencias").json()["items"][0]
    assert s["market_price_base"] is None
    assert any("Sin referencia de mercado" in w for w in s["explanation"]["advertencias"])
    del r


# ------------------------------------------------------------------ sugerencias y aprobación

def test_suggestion_based_on_real_cost_and_approval_flow(owner, admin, db):
    p = _setup_product_with_market(owner, db)              # costo 2.000, margen 0,30 por defecto
    r = admin.post("/api/v1/precios/sugerencias/generar", json={"product_ids": [p["id"]]})
    assert r.status_code == 200 and r.json()["generadas"] == 1
    s = admin.get("/api/v1/precios/sugerencias").json()["items"][0]
    assert s["unit_cost"] == "2000.000000"
    assert s["suggested_price"] == "2900.00"               # 2000/0,7 = 2857,14 → 2.900
    assert s["current_price"] == "3500.00" and s["change_rate"] == "-0.1714"
    assert s["market_price_base"] == "3000.00"
    # Mercado como referencia: advierte, pero NO reemplaza al costo
    assert any("difiere" in w for w in s["explanation"]["advertencias"])

    # El administrador no aprueba precios
    assert admin.post(f"/api/v1/precios/sugerencias/{s['id']}/aprobar", json={"note": "ok ok"}).status_code == 403
    # Ni el precio cambia por generar sugerencias
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "3500.00"

    r = owner.post(f"/api/v1/precios/sugerencias/{s['id']}/aprobar", json={"note": "Ajuste por costo"})
    assert r.status_code == 200 and r.json()["status"] == "aprobada"
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "2900.00"
    h = owner.get("/api/v1/precios/historial", params={"product_id": p["id"]}).json()["items"][0]
    assert (h["old_price"], h["new_price"], h["source"]) == ("3500.00", "2900.00", "sugerencia")
    assert h["changed_by_name"] == "Duena" and h["justification"] == "Ajuste por costo"
    # No se puede aprobar dos veces
    assert owner.post(f"/api/v1/precios/sugerencias/{s['id']}/aprobar",
                      json={"note": "otra vez"}).status_code == 409


def test_stale_suggestion_rejected_when_cost_changed(owner, db):
    p = _setup_product_with_market(owner, db)
    owner.post("/api/v1/precios/sugerencias/generar", json={"product_ids": [p["id"]]})
    s = owner.get("/api/v1/precios/sugerencias").json()["items"][0]
    move(owner, p["id"], "ajuste_entrada", "10", cost="4000", motivo="nueva compra")
    r = owner.post(f"/api/v1/precios/sugerencias/{s['id']}/aprobar", json={"note": "ok ok"})
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "sugerencia_obsoleta"
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "3500.00"


def test_product_without_cost_or_quote_still_handled(owner, db):
    p = product(owner, db, sku="SIN", price="1000")
    r = owner.post("/api/v1/precios/sugerencias/generar", json={"product_ids": [p["id"]]}).json()
    assert r["generadas"] == 0 and "Sin costo" in r["omitidas"][0]["motivo"]
    move(owner, p["id"], "inicial", "5", cost="800")       # costo real, sin cotización de mercado
    r = owner.post("/api/v1/precios/sugerencias/generar", json={"product_ids": [p["id"]]}).json()
    assert r["generadas"] == 1                             # se continúa con costos reales


def test_reject_and_history(owner, db):
    p = _setup_product_with_market(owner, db)
    owner.post("/api/v1/precios/sugerencias/generar", json={"product_ids": [p["id"]]})
    s = owner.get("/api/v1/precios/sugerencias").json()["items"][0]
    r = owner.post(f"/api/v1/precios/sugerencias/{s['id']}/rechazar", json={"note": "Mantener precio"})
    assert r.json()["status"] == "rechazada"
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "3500.00"
    rows = owner.get("/api/v1/precios/sugerencias", params={"estado": "rechazada"}).json()
    assert rows["total"] == 1 and rows["items"][0]["decision_note"] == "Mantener precio"


def test_manual_price_requires_confirmation_for_large_change(owner, admin, db):
    p = product(owner, db, sku="M", price="1000")
    assert admin.post(f"/api/v1/precios/productos/{p['id']}",
                      json={"new_price": "1050", "justification": "x x x"}).status_code == 403
    r = owner.post(f"/api/v1/precios/productos/{p['id']}", json={"new_price": "1500", "justification": "Alza"})
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "variacion_requiere_confirmacion"
    r = owner.post(f"/api/v1/precios/productos/{p['id']}",
                   json={"new_price": "1500", "justification": "Alza fuerte", "confirm_large_change": True})
    assert r.status_code == 200 and r.json()["new_price"] == "1500.00"
    assert owner.post(f"/api/v1/precios/productos/{p['id']}",
                      json={"new_price": "1500", "justification": "igual"}).json() is None


def test_automatic_policy_off_by_default_and_guarded(owner, db):
    p = product(owner, db, sku="AUTO", price="1450")
    move(owner, p["id"], "inicial", "10", cost="1000")
    move(owner, p["id"], "ajuste_entrada", "10", cost="1100", motivo="x")   # promedio 1.050 → 1.500
    jobs.refresh_suggestions(db)
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "1450.00"  # no se aplicó

    # Habilitada: el cambio (3,45 %) está dentro del 10 %, pero hay advertencia de "sin mercado" → no aplica
    owner.patch("/api/v1/configuracion", json={"auto_price_update_enabled": True})
    jobs.refresh_suggestions(db)
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "1450.00"
    s = owner.get("/api/v1/precios/sugerencias").json()["items"][0]
    assert s["status"] == "pendiente" and s["suggested_price"] == "1500.00"


def test_automatic_policy_applies_small_safe_change(owner, db):
    p = _setup_product_with_market(owner, db, market_price="2100", cost_="2000")
    db.execute(text("UPDATE products SET current_price = 2850 WHERE id = :p"), {"p": p["id"]})
    db.commit()
    owner.patch("/api/v1/configuracion", json={"auto_price_update_enabled": True})
    out = jobs.refresh_suggestions(db)
    assert out["aplicadas"] == 1
    assert owner.get(f"/api/v1/productos/{p['id']}").json()["current_price"] == "2900.00"
    h = owner.get("/api/v1/precios/historial", params={"product_id": p["id"]}).json()["items"][0]
    assert h["source"] == "automatica" and h["changed_by_name"] == "Sistema"


# ------------------------------------------------------------------ worker y permisos

def test_manual_sync_request_is_processed_by_worker(owner, employee, db):
    assert employee.post("/api/v1/mercado/sincronizar").status_code == 403
    assert owner.post("/api/v1/mercado/sincronizar").status_code == 202
    assert jobs.pending_requests(db) == [("sipsa_ciudad", 1)]
    handler, _ = fixed(soap([rec()]))
    out = jobs.sync_source(db, "sipsa_ciudad", triggered_by="usuario:1",
                           connector_factory=lambda code: connector(handler))
    assert out.status == "exito"
    db.expire_all()
    assert jobs.pending_requests(db) == []
    st = owner.get("/api/v1/mercado/estado").json()
    assert st["fuentes"][0]["last_status"] == "exito"
    assert st["ejecuciones"][0]["triggered_by"] == "usuario:1"


def test_sync_respects_disabled_setting(owner, db):
    owner.patch("/api/v1/configuracion", json={"market_sync_enabled": False})
    out = jobs.sync_source(db, "sipsa_ciudad", triggered_by="prueba",
                           connector_factory=lambda c: connector(fixed(soap([rec()]))[0]))
    assert out.status == "omitida"
