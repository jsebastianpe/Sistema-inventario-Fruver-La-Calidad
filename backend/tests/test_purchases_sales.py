import threading
from decimal import Decimal

from sqlalchemy import text

from app.core.numbers import allocate
from tests.conftest import login_client, make_user
from tests.helpers import category, key, lots_total, move, product, stock, unit_id


def supplier(client, name="Proveedor de prueba") -> int:
    r = client.post("/api/v1/proveedores", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


def setup_two_products(client, db):
    cat = category(client)
    papa = product(client, db, sku="PAPA", name="Papa", cat=cat, presentations=[
        {"unit_id": unit_id(db, "bulto"), "name": "Bulto 50 kg", "factor_to_base": "50"}])
    tomate = product(client, db, sku="TOM", name="Tomate", cat=cat)
    bulto = next(p["id"] for p in papa["presentations"] if p["name"] == "Bulto 50 kg")
    return papa, tomate, bulto


def purchase_payload(sup, papa, tomate, bulto, **over):
    body = {
        "supplier_id": sup, "purchase_date": "2026-10-16", "invoice_number": "FV-001",
        "freight": "10000", "other_costs": "2000", "discount": "0", "allocation_method": "valor",
        "lines": [
            {"product_id": papa["id"], "product_unit_id": bulto, "quantity": "2", "unit_price": "80000",
             "waste_qty": "4", "expiry_date": "2030-01-01"},
            {"product_id": tomate["id"], "quantity": "30", "unit_price": "3000", "line_discount": "5000"},
        ],
    }
    body.update(over)
    return body


# ------------------------------------------------------------------ reparto (unitario)

def test_allocate_is_exact():
    parts = allocate(Decimal("100.00"), [Decimal(1), Decimal(1), Decimal(1)])
    assert sum(parts) == Decimal("100.00") and parts.count(Decimal("33.33")) == 2
    assert allocate(Decimal("10"), [Decimal(0), Decimal(0)]) == [Decimal("5.00"), Decimal("5.00")]


# ------------------------------------------------------------------ compras

def test_purchase_totals_allocation_and_effective_cost(admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    r = admin.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto))
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "borrador"
    # Valores calculados a mano (ver docs/05):
    assert p["subtotal"] == "245000.00" and p["total"] == "257000.00"
    a, b = p["lines"]
    assert a["net_amount"] == "160000.00" and b["net_amount"] == "85000.00"
    assert a["allocated_extra"] == "7836.73" and b["allocated_extra"] == "4163.27"
    assert a["quantity_base"] == "100.0000" and a["usable_qty"] == "96.0000"
    assert a["unit_cost_base"] == "1748.299271"      # 167.836,73 / 96 kg
    assert b["unit_cost_base"] == "2972.109000"      # 89.163,27 / 30 kg

    # Un borrador NO altera existencias
    assert stock(db, papa["id"])[0] == 0

    r = admin.post(f"/api/v1/compras/{p['id']}/confirmar")
    assert r.status_code == 200 and r.json()["status"] == "confirmada"
    assert stock(db, papa["id"]) == (Decimal("96"), Decimal("1748.299271"))
    assert stock(db, tomate["id"]) == (Decimal("30"), Decimal("2972.109"))
    lot = db.execute(text("SELECT expiry_date, remaining_qty FROM lots WHERE product_id=:p"),
                     {"p": papa["id"]}).one()
    assert str(lot[0]) == "2030-01-01" and lot[1] == Decimal("96")


def test_allocation_by_weight(admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    r = admin.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto,
                                                            allocation_method="peso"))
    a, b = r.json()["lines"]
    assert a["allocated_extra"] == "9230.77" and b["allocated_extra"] == "2769.23"  # 100 kg vs 30 kg


def test_allocation_by_weight_rejects_non_mass(admin, db):
    sup = supplier(admin)
    cat = category(admin)
    pina = product(admin, db, sku="PINA", base="und", cat=cat)
    r = admin.post("/api/v1/compras", json={
        "supplier_id": sup, "purchase_date": "2026-10-16", "allocation_method": "peso", "freight": "100",
        "lines": [{"product_id": pina["id"], "quantity": "10", "unit_price": "2000"}]})
    assert r.status_code == 400 and r.json()["error"]["codigo"] == "reparto_invalido"


def test_purchase_tax_in_cost_is_configurable(owner, db):
    sup = supplier(owner)
    p = product(owner, db, sku="GAS", base="und")
    body = {"supplier_id": sup, "purchase_date": "2026-10-16",
            "lines": [{"product_id": p["id"], "quantity": "10", "unit_price": "1000", "tax_rate": "0.19"}]}
    r = owner.post("/api/v1/compras", json=body).json()
    assert r["tax_total"] == "1900.00" and r["total"] == "11900.00"
    assert r["lines"][0]["unit_cost_base"] == "1190.000000"      # impuesto no descontable → costo
    owner.patch("/api/v1/configuracion", json={"purchase_tax_in_cost": False})
    r = owner.post("/api/v1/compras", json=body | {"lines": body["lines"]}).json()
    assert r["lines"][0]["unit_cost_base"] == "1000.000000"


def test_purchase_validation(admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    bad = purchase_payload(sup, papa, tomate, bulto)
    bad["lines"][1]["line_discount"] = "999999"
    assert admin.post("/api/v1/compras", json=bad).status_code == 400
    bad = purchase_payload(sup, papa, tomate, bulto)
    bad["lines"][0]["waste_qty"] = "100"   # toda la compra como merma
    assert admin.post("/api/v1/compras", json=bad).status_code == 400
    bad = purchase_payload(sup, papa, tomate, bulto, discount="9999999")
    assert admin.post("/api/v1/compras", json=bad).status_code == 400
    bad = purchase_payload(sup, papa, tomate, bulto, lines=[])
    assert admin.post("/api/v1/compras", json=bad).status_code == 422


def test_purchase_duplicate_invoice_and_idempotency(admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    k = key()
    body = purchase_payload(sup, papa, tomate, bulto)
    r1 = admin.post("/api/v1/compras", json=body, headers={"Idempotency-Key": k})
    r2 = admin.post("/api/v1/compras", json=body, headers={"Idempotency-Key": k})
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    r3 = admin.post("/api/v1/compras", json=body)  # misma factura y proveedor, sin clave
    assert r3.status_code == 409
    assert db.execute(text("SELECT count(*) FROM purchases")).scalar() == 1


def test_confirm_twice_and_concurrently_only_once(admin, db, app):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    pid = admin.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto)).json()["id"]
    barrier = threading.Barrier(4)
    codes = []

    def go():
        c = login_client(app, "admin")
        barrier.wait()
        codes.append(c.post(f"/api/v1/compras/{pid}/confirmar").status_code)

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes == [200] * 4
    assert stock(db, papa["id"])[0] == Decimal("96")       # no se duplicó
    n = db.execute(text("SELECT count(*) FROM stock_movements WHERE reference_type='compra'")).scalar()
    assert n == 2


def test_cancel_confirmed_purchase_reverses_traceably(admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    move(admin, tomate["id"], "inicial", "10", cost="2000")
    pid = admin.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto)).json()["id"]
    admin.post(f"/api/v1/compras/{pid}/confirmar")
    assert stock(db, tomate["id"])[0] == Decimal("40")
    r = admin.post(f"/api/v1/compras/{pid}/anular", json={"reason": "Factura equivocada"})
    assert r.status_code == 200 and r.json()["status"] == "anulada"
    q, avg = stock(db, tomate["id"])
    assert q == Decimal("10") and avg == Decimal("2000")   # vuelve al costo previo
    assert stock(db, papa["id"])[0] == 0 and lots_total(db, papa["id"]) == 0
    types = db.execute(text("SELECT movement_type FROM stock_movements WHERE reference_type='compra' "
                            "ORDER BY id")).scalars().all()
    assert types == ["compra", "compra", "anulacion_compra", "anulacion_compra"]
    # La factura queda libre para registrarse correctamente
    r = admin.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto))
    assert r.status_code == 201


def test_cancel_purchase_rejected_if_goods_already_sold(owner, db):
    papa, tomate, bulto = setup_two_products(owner, db)
    sup = supplier(owner)
    pid = owner.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto)).json()["id"]
    owner.post(f"/api/v1/compras/{pid}/confirmar")
    move(owner, tomate["id"], "merma", "5", motivo="dañado")
    r = owner.post(f"/api/v1/compras/{pid}/anular", json={"reason": "error"})
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "anulacion_imposible"
    assert stock(db, papa["id"])[0] == Decimal("96")       # nada quedó a medias
    assert db.execute(text("SELECT status FROM purchases")).scalar() == "confirmada"


def test_employee_cannot_purchase(employee, admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    assert employee.post("/api/v1/compras", json=purchase_payload(sup, papa, tomate, bulto)).status_code == 403
    assert employee.get("/api/v1/compras").status_code == 403


# ------------------------------------------------------------------ ventas

def priced_product(owner, db, sku="BAN", price="2500", stock_qty="20", cost_="1000", tax=None, **kw):
    extra = {"tax_rate": tax} if tax else {}
    p = product(owner, db, sku=sku, price=price, presentations=[
        {"unit_id": unit_id(db, "lb"), "name": "Libra"}], **extra, **kw)
    move(owner, p["id"], "inicial", stock_qty, cost=cost_)
    return p


def sale(client, lines, k=None, expect=201, **extra):
    r = client.post("/api/v1/ventas", json={"lines": lines, **extra},
                    headers={"Idempotency-Key": k or key()})
    assert r.status_code == expect, r.text
    return r.json()


def test_sale_reduces_stock_and_snapshots_price(owner, db):
    p = priced_product(owner, db)
    libra = next(x["id"] for x in p["presentations"] if x["name"] == "Libra")
    s = sale(owner, [{"product_id": p["id"], "quantity": "2"},                       # 2 kg
                     {"product_id": p["id"], "product_unit_id": libra, "quantity": "3"}])  # 1,5 kg
    assert s["lines"][0]["unit_price"] == "2500.00" and s["lines"][1]["unit_price"] == "1250.00"
    assert s["total"] == "8750.00"
    assert s["cost_total"] == "3500.00"              # 3,5 kg × 1.000
    assert s["gross_margin"] == "5250.00"
    assert stock(db, p["id"])[0] == Decimal("16.5")

    # Cambiar el precio NO altera la venta anterior
    db.execute(text("SELECT 1"))
    r = owner.get(f"/api/v1/ventas/{s['id']}")
    from app.domains.catalog.models import Product
    from app.domains.pricing.models import PriceChangeSource
    from app.domains.pricing.service import set_price
    prod = db.get(Product, p["id"])
    set_price(db, prod, Decimal("4000"), source=PriceChangeSource.MANUAL, justification="Alza",
              user_id=None, ip=None)
    db.commit()
    r = owner.get(f"/api/v1/ventas/{s['id']}").json()
    assert r["total"] == "8750.00" and r["lines"][0]["unit_price"] == "2500.00"
    s2 = sale(owner, [{"product_id": p["id"], "quantity": "1"}])
    assert s2["total"] == "4000.00"


def test_sale_tax_contained_in_price(owner, db):
    p = priced_product(owner, db, sku="GAS", price="1190", tax="0.19")
    s = sale(owner, [{"product_id": p["id"], "quantity": "10"}])
    assert s["total"] == "11900.00" and s["tax_total"] == "1900.00"
    assert s["gross_margin"] == "0.00"              # 10.000 neto − 10.000 costo


def test_sale_idempotency_including_concurrent_retries(owner, app, db):
    p = priced_product(owner, db)
    k = key()
    s1 = sale(owner, [{"product_id": p["id"], "quantity": "1"}], k=k)
    s2 = sale(owner, [{"product_id": p["id"], "quantity": "1"}], k=k, expect=200)
    assert s1["id"] == s2["id"]

    k2 = key()
    barrier = threading.Barrier(5)
    ids = []

    def go():
        c = login_client(app, "duena")
        barrier.wait()
        r = c.post("/api/v1/ventas", json={"lines": [{"product_id": p["id"], "quantity": "1"}]},
                   headers={"Idempotency-Key": k2})
        ids.append((r.status_code, r.json().get("id")))

    threads = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({i for _, i in ids}) == 1, ids
    assert sorted(c for c, _ in ids) == [200, 200, 200, 200, 201]
    assert stock(db, p["id"])[0] == Decimal("18")       # 20 − 1 − 1


def test_sale_insufficient_stock_is_atomic(owner, db):
    a = priced_product(owner, db, sku="A", stock_qty="5")
    b = priced_product(owner, db, sku="B", stock_qty="1")
    r = sale(owner, [{"product_id": a["id"], "quantity": "2"}, {"product_id": b["id"], "quantity": "3"}],
             expect=409)
    assert r["error"]["codigo"] == "existencias_insuficientes"
    assert stock(db, a["id"])[0] == Decimal("5")        # la primera línea tampoco se aplicó
    assert db.execute(text("SELECT count(*) FROM sales")).scalar() == 0


def test_product_without_price_cannot_be_sold(admin, db):
    p = product(admin, db, sku="NP")
    move(admin, p["id"], "inicial", "5", cost="10")
    r = sale(admin, [{"product_id": p["id"], "quantity": "1"}], expect=400)
    assert r["error"]["codigo"] == "sin_precio"


def test_discount_authorization(owner, app, db):
    p = priced_product(owner, db)
    make_user(db, "cajero", __import__("app.domains.auth.permissions", fromlist=["Role"]).Role.EMPLEADO)
    emp = login_client(app, "cajero")
    # 5 % permitido (2.500 × 2 = 5.000; 250 = 5 %)
    sale(emp, [{"product_id": p["id"], "quantity": "2", "discount": "250"}])
    r = sale(emp, [{"product_id": p["id"], "quantity": "2", "discount": "1000"}], expect=403)
    assert r["error"]["codigo"] == "descuento_no_autorizado"
    sale(owner, [{"product_id": p["id"], "quantity": "2", "discount": "1000"}])
    # Descuento mayor que la línea
    sale(owner, [{"product_id": p["id"], "quantity": "1", "discount": "3000"}], expect=400)


def test_client_cannot_send_prices(owner, db):
    p = priced_product(owner, db)
    r = owner.post("/api/v1/ventas", json={"lines": [{"product_id": p["id"], "quantity": "1",
                                                      "unit_price": "1"}]},
                   headers={"Idempotency-Key": key()})
    assert r.status_code == 422


def test_cancel_sale_restores_stock_cost_and_lots(owner, db):
    p = priced_product(owner, db)
    s = sale(owner, [{"product_id": p["id"], "quantity": "4"}])
    move(owner, p["id"], "ajuste_entrada", "10", cost="2000", motivo="x")   # cambia el promedio
    q, avg = stock(db, p["id"])
    assert q == Decimal("26") and avg == Decimal("1384.615385")
    r = owner.post(f"/api/v1/ventas/{s['id']}/anular", json={"reason": "Cliente desistió"})
    assert r.status_code == 200 and r.json()["status"] == "anulada"
    q, avg = stock(db, p["id"])
    assert q == Decimal("30")
    # (26 × 1384,615385 + 4 × 1000) / 30 = 1333,3333336… → 1333,333334 (6 decimales)
    assert avg == Decimal("1333.333334")
    assert lots_total(db, p["id"]) == Decimal("30")
    assert owner.post(f"/api/v1/ventas/{s['id']}/anular", json={"reason": "otra"}).status_code == 409


def test_employee_sales_visibility(owner, app, db):
    from app.domains.auth.permissions import Role
    p = priced_product(owner, db)
    make_user(db, "e1", Role.EMPLEADO)
    make_user(db, "e2", Role.EMPLEADO)
    e1, e2 = login_client(app, "e1"), login_client(app, "e2")
    s = sale(e1, [{"product_id": p["id"], "quantity": "1"}])
    assert s["cost_total"] is None and s["lines"][0]["cost_amount"] is None
    assert e2.get(f"/api/v1/ventas/{s['id']}").status_code == 403
    assert e2.get("/api/v1/ventas").json()["total"] == 0
    assert owner.get("/api/v1/ventas").json()["total"] == 1
    assert e1.post(f"/api/v1/ventas/{s['id']}/anular", json={"reason": "x x"}).status_code == 403


# ------------------------------------------------------------------ previsualización (sin efectos)

def test_sale_preview_matches_registration_and_changes_nothing(owner, db):
    p = priced_product(owner, db)
    body = {"lines": [{"product_id": p["id"], "quantity": "2", "discount": "100"}]}
    pv = owner.post("/api/v1/ventas/previsualizar", json=body).json()
    assert pv["total"] == "4900.00" and pv["discount_authorized"] is True and pv["lines"][0]["stock_ok"] is True
    assert db.execute(text("SELECT count(*) FROM sales")).scalar() == 0
    assert stock(db, p["id"])[0] == Decimal("20")
    s = sale(owner, body["lines"])
    assert s["total"] == pv["total"] and s["tax_total"] == pv["tax_total"]
    big = owner.post("/api/v1/ventas/previsualizar", json={"lines": [{"product_id": p["id"], "quantity": "999"}]}).json()
    assert big["lines"][0]["stock_ok"] is False


def test_purchase_preview_matches_draft_and_changes_nothing(admin, db):
    papa, tomate, bulto = setup_two_products(admin, db)
    sup = supplier(admin)
    body = purchase_payload(sup, papa, tomate, bulto)
    pv = admin.post("/api/v1/compras/previsualizar", json=body).json()
    assert db.execute(text("SELECT count(*) FROM purchases")).scalar() == 0
    draft = admin.post("/api/v1/compras", json=body).json()
    assert pv["total"] == draft["total"] == "257000.00"
    assert [x["unit_cost_base"] for x in pv["lines"]] == [x["unit_cost_base"] for x in draft["lines"]]
