"""Venta por peso, unidad y presentación: cálculos, conversiones, precio esperado e integridad."""
import threading
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domains.auth.permissions import Role
from tests.conftest import login_client, make_user
from tests.helpers import category, key, move, product, stock, unit_id


def sellable(owner, db, *, sku, name, price, base="kg", stock_qty="100", cost_="1000", **kw):
    p = product(owner, db, sku=sku, name=name, base=base, price=price, **kw)
    move(owner, p["id"], "inicial", stock_qty, cost=cost_)
    return p


def sale(client, lines, k=None, expect=201, **extra):
    r = client.post("/api/v1/ventas", json={"lines": lines, **extra}, headers={"Idempotency-Key": k or key()})
    assert r.status_code == expect, r.text
    return r.json()


def sales_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM sales")).scalar_one()


# ------------------------------------------------------------------ casos 1–3: cálculos

def test_1250_grams_at_4000_per_kg(owner, db):
    p = sellable(owner, db, sku="TOM", name="Tomate chonto", price="4000")
    s = sale(owner, [{"product_id": p["id"], "quantity": "1250", "weight_unit": "g", "expected_price": "4000"}])
    ln = s["lines"][0]
    assert s["total"] == "5000.00"
    assert ln["quantity"] == "1.2500" and ln["quantity_base"] == "1.2500" and ln["unit_name"] == "Kilogramo"
    assert stock(db, p["id"])[0] == Decimal("98.75")
    mv = db.execute(text("SELECT quantity, quantity_base FROM stock_movements WHERE movement_type='venta'")).one()
    assert mv == (Decimal("1.2500"), Decimal("-1.2500"))


def test_750_grams_at_3500_per_kg(owner, db):
    p = sellable(owner, db, sku="PAPA", name="Papa", price="3500")
    s = sale(owner, [{"product_id": p["id"], "quantity": "750", "weight_unit": "g"}])
    assert s["total"] == "2625.00" and s["lines"][0]["quantity_base"] == "0.7500"


def test_kilograms_and_pounds_are_converted(owner, db):
    p = sellable(owner, db, sku="PAPA", name="Papa", price="3500")
    s = sale(owner, [{"product_id": p["id"], "quantity": "0.75", "weight_unit": "kg"},
                     {"product_id": p["id"], "quantity": "2", "weight_unit": "lb"}])   # 1 kg
    assert [x["net_amount"] for x in s["lines"]] == ["2625.00", "3500.00"]
    assert s["total"] == "6125.00"
    assert stock(db, p["id"])[0] == Decimal("98.25")


def test_three_units_at_2000(owner, db):
    p = sellable(owner, db, sku="AGU", name="Aguacate", base="und", price="2000", stock_qty="10")
    s = sale(owner, [{"product_id": p["id"], "quantity": "3"}])
    assert s["total"] == "6000.00" and s["lines"][0]["unit_name"] == "Unidad"
    assert stock(db, p["id"])[0] == Decimal("7")


def test_package_presentation_uses_its_factor(owner, db):
    p = sellable(owner, db, sku="CEB", name="Cebolla", price="3000", presentations=[
        {"unit_id": unit_id(db, "bulto"), "name": "Bulto 25 kg", "factor_to_base": "25"}])
    bulto = next(x["id"] for x in p["presentations"] if x["name"] == "Bulto 25 kg")
    s = sale(owner, [{"product_id": p["id"], "product_unit_id": bulto, "quantity": "2"}])
    assert s["lines"][0]["unit_price"] == "75000.00" and s["total"] == "150000.00"
    assert stock(db, p["id"])[0] == Decimal("50")


# ------------------------------------------------------------------ casos 4–6: validación

@pytest.mark.parametrize("q", ["0", "-750", "0.0000"])
def test_zero_or_negative_weight_is_rejected(owner, db, q):
    p = sellable(owner, db, sku="TOM", name="Tomate", price="4000")
    sale(owner, [{"product_id": p["id"], "quantity": q, "weight_unit": "g"}], expect=422)
    assert sales_count(db) == 0


def test_weight_over_stock_is_rejected_atomically(owner, db):
    a = sellable(owner, db, sku="A", name="Mango", price="5000", stock_qty="10")
    b = sellable(owner, db, sku="B", name="Mora", price="9000", stock_qty="1")
    r = sale(owner, [{"product_id": a["id"], "quantity": "500", "weight_unit": "g"},
                     {"product_id": b["id"], "quantity": "1001", "weight_unit": "g"}], expect=409)
    assert r["error"]["codigo"] == "existencias_insuficientes"
    assert stock(db, a["id"])[0] == Decimal("10") and stock(db, b["id"])[0] == Decimal("1")
    assert sales_count(db) == 0
    pv = owner.post("/api/v1/ventas/previsualizar", json={"lines": [
        {"product_id": b["id"], "quantity": "1001", "weight_unit": "g"}]}).json()
    assert pv["lines"][0]["stock_ok"] is False


def test_missing_or_inactive_product(owner, db):
    p = sellable(owner, db, sku="X", name="Lulo", price="6000")
    r = sale(owner, [{"product_id": 999999, "quantity": "1"}], expect=400)
    assert r["error"]["codigo"] == "producto_invalido"
    db.execute(text("UPDATE products SET is_active = false WHERE id = :i"), {"i": p["id"]})
    db.commit()
    r = sale(owner, [{"product_id": p["id"], "quantity": "500", "weight_unit": "g"}], expect=400)
    assert r["error"]["codigo"] == "producto_invalido"
    assert sales_count(db) == 0


def test_weight_unit_rules(owner, db):
    kg = sellable(owner, db, sku="K", name="Papa", price="3500")
    und = sellable(owner, db, sku="U", name="Piña", base="und", price="6000")
    r = sale(owner, [{"product_id": und["id"], "quantity": "500", "weight_unit": "g"}], expect=400)
    assert r["error"]["codigo"] == "unidad_invalida"                    # no se vende por peso
    r = sale(owner, [{"product_id": kg["id"], "quantity": "1", "weight_unit": "und"}], expect=400)
    assert r["error"]["codigo"] == "unidad_invalida"                    # no es unidad de masa
    r = sale(owner, [{"product_id": kg["id"], "quantity": "1", "weight_unit": "zz"}], expect=400)
    assert r["error"]["codigo"] == "unidad_invalida"
    r = sale(owner, [{"product_id": kg["id"], "quantity": "0.05", "weight_unit": "g"}], expect=400)
    assert r["error"]["codigo"] == "cantidad_invalida"                  # 0,00005 kg: más precisión que 0,0001
    assert sales_count(db) == 0


# ------------------------------------------------------------------ caso 7: cambio de precio

def test_price_change_between_load_and_confirmation(owner, db):
    from app.domains.catalog.models import Product
    from app.domains.pricing.models import PriceChangeSource
    from app.domains.pricing.service import set_price

    p = sellable(owner, db, sku="TOM", name="Tomate", price="4000")
    line = {"product_id": p["id"], "quantity": "1250", "weight_unit": "g", "expected_price": "4000"}
    set_price(db, db.get(Product, p["id"]), Decimal("4400"), source=PriceChangeSource.MANUAL,
              justification="Alza", user_id=None, ip=None)
    db.commit()

    pv = owner.post("/api/v1/ventas/previsualizar", json={"lines": [line]}).json()
    assert pv["lines"][0]["price_changed"] is True and pv["lines"][0]["price_base"] == "4400.00"
    r = sale(owner, [line], expect=409)
    assert r["error"]["codigo"] == "precio_cambiado"
    assert r["error"]["detalles"] == [{"producto_id": p["id"], "esperado": "4000", "vigente": "4400.00"}]
    assert sales_count(db) == 0 and stock(db, p["id"])[0] == Decimal("100")

    s = sale(owner, [{**line, "expected_price": "4400"}])               # el cajero acepta el nuevo precio
    assert s["total"] == "5500.00"


# ------------------------------------------------------------------ caso 8: concurrencia

def test_concurrent_sales_cannot_oversell(owner, app, db):
    p = sellable(owner, db, sku="FRE", name="Fresa", price="12000", stock_qty="1")
    barrier = threading.Barrier(2)
    codes = []

    def go():
        c = login_client(app, "duena")
        barrier.wait()
        r = c.post("/api/v1/ventas", json={"lines": [{"product_id": p["id"], "quantity": "750", "weight_unit": "g"}]},
                   headers={"Idempotency-Key": key()})
        codes.append(r.status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [201, 409], codes
    assert stock(db, p["id"])[0] == Decimal("0.25")
    assert db.execute(text("SELECT sum(remaining_qty) FROM lots WHERE product_id=:p"), {"p": p["id"]}).scalar() \
        == Decimal("0.25")


# ------------------------------------------------------------------ caso 9: falla de persistencia

def test_failure_while_recording_inventory_leaves_nothing(owner, db, monkeypatch):
    from app.domains.inventory import service as inv

    a = sellable(owner, db, sku="A", name="Mango", price="5000", stock_qty="10")
    b = sellable(owner, db, sku="B", name="Mora", price="9000", stock_qty="10")
    original = inv.record_outbound
    calls = []

    def failing(db_, **kw):
        calls.append(kw["product"].id)
        if len(calls) == 2:
            raise RuntimeError("falla simulada de la base de datos")
        return original(db_, **kw)

    monkeypatch.setattr(inv, "record_outbound", failing)
    with pytest.raises(RuntimeError):
        owner.post("/api/v1/ventas", json={"lines": [{"product_id": a["id"], "quantity": "1"},
                                                     {"product_id": b["id"], "quantity": "1"}]},
                   headers={"Idempotency-Key": key()})
    db.rollback()
    assert sales_count(db) == 0
    assert db.execute(text("SELECT count(*) FROM stock_movements WHERE movement_type='venta'")).scalar() == 0
    assert stock(db, a["id"])[0] == Decimal("10") and stock(db, b["id"])[0] == Decimal("10")


# ------------------------------------------------------------------ caso 10: reintentos

def test_retry_of_same_confirmation_discounts_once(owner, db):
    p = sellable(owner, db, sku="TOM", name="Tomate", price="4000")
    k = key()
    line = [{"product_id": p["id"], "quantity": "1250", "weight_unit": "g"}]
    s1 = sale(owner, line, k=k)
    r = owner.post("/api/v1/ventas", json={"lines": line}, headers={"Idempotency-Key": k})
    assert r.status_code == 200 and r.headers["Idempotent-Replay"] == "true" and r.json()["id"] == s1["id"]
    assert stock(db, p["id"])[0] == Decimal("98.75") and sales_count(db) == 1


# ------------------------------------------------------------------ caso 11: redondeo

def test_rounding_is_applied_once_on_the_line_amount(owner, db):
    # Precio por kg sin múltiplo exacto en gramos: 1.250 g × 3.333 $/kg = 4.166,25.
    # (Antes se redondeaba primero el precio por gramo a 3,33 y daba 4.162,50.)
    p = sellable(owner, db, sku="GUA", name="Guayaba", price="3333", presentations=[
        {"unit_id": unit_id(db, "g"), "name": "Gramo"}])
    gramo = next(x["id"] for x in p["presentations"] if x["name"] == "Gramo")
    s = sale(owner, [{"product_id": p["id"], "quantity": "1250", "weight_unit": "g"},
                     {"product_id": p["id"], "product_unit_id": gramo, "quantity": "1250"}])
    assert [x["net_amount"] for x in s["lines"]] == ["4166.25", "4166.25"]
    assert s["total"] == "8332.50"

    q = sellable(owner, db, sku="R", name="Prueba redondeo", price="2999")
    s = sale(owner, [{"product_id": q["id"], "quantity": "333", "weight_unit": "g"},     # 998,667 → 998,67
                     {"product_id": q["id"], "quantity": "0.5", "weight_unit": "g"}])    # 1,4995 → 1,50
    assert [x["net_amount"] for x in s["lines"]] == ["998.67", "1.50"]
    assert [x["quantity_base"] for x in s["lines"]] == ["0.3330", "0.0005"]
    total_db = db.execute(text("SELECT total FROM sales WHERE id=:i"), {"i": s["id"]}).scalar()
    assert total_db == Decimal("1000.17") == sum(Decimal(x["net_amount"]) for x in s["lines"])


# ------------------------------------------------------------------ casos 12–13: varias líneas y reportes

def test_multi_line_sale_and_financial_reports(owner, db):
    tom = sellable(owner, db, sku="TOM", name="Tomate", price="4000", cost_="2000")
    agu = sellable(owner, db, sku="AGU", name="Aguacate", base="und", price="2000", stock_qty="10", cost_="800")
    s = sale(owner, [{"product_id": tom["id"], "quantity": "1250", "weight_unit": "g"},
                     {"product_id": agu["id"], "quantity": "3"},
                     {"product_id": tom["id"], "quantity": "0.5", "weight_unit": "kg"}],
             payment_method="transferencia")
    assert [x["line_no"] for x in s["lines"]] == [1, 2, 3]
    assert s["total"] == "13000.00"                         # 5.000 + 6.000 + 2.000
    assert s["cost_total"] == "5900.00"                     # 1,75 × 2.000 + 3 × 800
    assert s["gross_margin"] == "7100.00" and s["payment_method"] == "transferencia"
    assert stock(db, tom["id"])[0] == Decimal("98.25") and stock(db, agu["id"])[0] == Decimal("7")

    rep = owner.get("/api/v1/reportes/ventas-por-periodo").json()
    assert Decimal(rep["totales"]["total"]) == Decimal("13000") and rep["totales"]["ventas"] == 1
    panel = owner.get("/api/v1/panel").json()
    assert Decimal(panel["ventas"]["total"]) == Decimal("13000") and panel["ventas"]["cantidad"] == 1
    assert Decimal(panel["ventas"]["margen_bruto_estimado"]) == Decimal("7100")

    # La anulación revierte inventario y sale de los reportes sin borrar el historial.
    assert owner.post(f"/api/v1/ventas/{s['id']}/anular", json={"reason": "Prueba"}).status_code == 200
    assert stock(db, tom["id"])[0] == Decimal("100") and stock(db, agu["id"])[0] == Decimal("10")
    assert owner.get("/api/v1/panel").json()["ventas"]["cantidad"] == 0
    assert db.execute(text("SELECT count(*) FROM stock_movements WHERE reference_type='venta'")).scalar() == 6


# ------------------------------------------------------------------ caso 14: permisos

def test_employee_cannot_change_prices_or_cancel(owner, app, db):
    p = sellable(owner, db, sku="TOM", name="Tomate", price="4000")
    make_user(db, "cajero", Role.EMPLEADO)
    emp = login_client(app, "cajero")
    r = emp.post(f"/api/v1/precios/productos/{p['id']}", json={"new_price": "1", "justification": "Rebaja"})
    assert r.status_code == 403
    r = emp.post("/api/v1/ventas", json={"lines": [{"product_id": p["id"], "quantity": "1", "unit_price": "1"}]},
                 headers={"Idempotency-Key": key()})
    assert r.status_code == 422                                          # el cliente no puede fijar precios
    s = sale(emp, [{"product_id": p["id"], "quantity": "1250", "weight_unit": "g", "expected_price": "4000"}])
    assert s["total"] == "5000.00" and s["cost_total"] is None          # registra, pero no ve costos
    assert emp.post(f"/api/v1/ventas/{s['id']}/anular", json={"reason": "No autorizado"}).status_code == 403
    audit = db.execute(text("SELECT count(*) FROM audit_log WHERE action='ventas.registrar'")).scalar()
    assert audit == 1


def test_anonymous_cannot_sell(client, owner, db):
    p = sellable(owner, db, sku="TOM", name="Tomate", price="4000")
    r = client.post("/api/v1/ventas", json={"lines": [{"product_id": p["id"], "quantity": "1"}]},
                    headers={"Idempotency-Key": key()})
    assert r.status_code in (401, 403)


# ------------------------------------------------------------------ accesos rápidos

def test_frequent_products(owner, app, db):
    a = sellable(owner, db, sku="A", name="Banano", price="2500")
    b = sellable(owner, db, sku="B", name="Limón", price="3000")
    c = sellable(owner, db, sku="C", name="Sin ventas", price="1000")
    for _ in range(3):
        sale(owner, [{"product_id": a["id"], "quantity": "1"}])
    sale(owner, [{"product_id": b["id"], "quantity": "1"}])
    s = sale(owner, [{"product_id": b["id"], "quantity": "1"}])
    owner.post(f"/api/v1/ventas/{s['id']}/anular", json={"reason": "No cuenta"})
    names = [x["name"] for x in owner.get("/api/v1/ventas/frecuentes").json()]
    assert names == ["Banano", "Limón"] and c["name"] not in names
    make_user(db, "cajero", Role.EMPLEADO)
    emp = [x for x in login_client(app, "cajero").get("/api/v1/ventas/frecuentes").json()]
    assert emp and "avg_cost" not in emp[0]                               # sin costos para empleados
