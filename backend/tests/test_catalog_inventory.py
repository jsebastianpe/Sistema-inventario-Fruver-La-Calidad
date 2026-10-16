import threading
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import session_factory
from tests.helpers import category, key, lots_total, move, product, stock, unit_id


# ------------------------------------------------------------------ catálogo

def test_create_search_and_deactivate_product(admin, db):
    p = product(admin, db, sku="PAPA-01", name="Papa pastusa", variety="Pastusa")
    assert p["stock"] == "0.0000" and p["presentations"][0]["is_base"] is True

    r = admin.get("/api/v1/productos", params={"q": "pastusa"})
    assert r.json()["total"] == 1
    r = admin.get("/api/v1/productos", params={"q": "papa-01"})
    assert r.json()["total"] == 1

    r = admin.post(f"/api/v1/productos/{p['id']}/desactivar")
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert admin.get("/api/v1/productos").json()["total"] == 0
    assert admin.get("/api/v1/productos", params={"activo": False}).json()["total"] == 1
    # No se elimina físicamente
    assert db.execute(text("SELECT count(*) FROM products")).scalar() == 1


def test_duplicate_sku_rejected(admin, db):
    cat = category(admin)
    product(admin, db, sku="X1", cat=cat)
    r = admin.post("/api/v1/productos", json={"sku": "x1", "name": "Otro", "category_id": cat,
                                              "base_unit_id": unit_id(db, "kg")})
    assert r.status_code == 409


def test_presentations_and_conversions(admin, db):
    kg = unit_id(db, "kg")
    p = product(admin, db, sku="PAPA", presentations=[
        {"unit_id": unit_id(db, "bulto"), "name": "Bulto 50 kg", "factor_to_base": "50"},
        {"unit_id": unit_id(db, "bulto"), "name": "Bulto 25 kg", "factor_to_base": "25"},
        {"unit_id": unit_id(db, "lb"), "name": "Libra"},
        {"unit_id": unit_id(db, "g"), "name": "Gramo"},
    ])
    f = {pr["name"]: Decimal(pr["factor_to_base"]) for pr in p["presentations"]}
    assert f == {"Kilogramo": 1, "Bulto 50 kg": 50, "Bulto 25 kg": 25, "Libra": Decimal("0.5"),
                 "Gramo": Decimal("0.001")}
    # Un empaque sin factor explícito se rechaza: no se suponen pesos.
    r = admin.post(f"/api/v1/productos/{p['id']}/presentaciones",
                   json={"unit_id": unit_id(db, "caja"), "name": "Caja"})
    assert r.status_code == 400 and r.json()["error"]["codigo"] == "factor_requerido"
    # Entre unidades de masa, un factor contradictorio se rechaza.
    r = admin.post(f"/api/v1/productos/{p['id']}/presentaciones",
                   json={"unit_id": unit_id(db, "lb"), "name": "Libra 2", "factor_to_base": "0.4536"})
    assert r.status_code == 400

    pu = {pr["name"]: pr["id"] for pr in p["presentations"]}
    move(admin, p["id"], "inicial", "2", cost="60000", pu=pu["Bulto 50 kg"])   # 100 kg a 1.200/kg
    q, avg = stock(db, p["id"])
    assert q == Decimal("100") and avg == Decimal("1200")
    move(admin, p["id"], "merma", "3", pu=pu["Libra"], motivo="Deterioro")      # 1,5 kg
    move(admin, p["id"], "merma", "250", pu=pu["Gramo"], motivo="Deterioro")    # 0,25 kg
    q, avg = stock(db, p["id"])
    assert q == Decimal("98.25") and avg == Decimal("1200")


def test_employee_cannot_see_costs_or_manage(employee, admin, db):
    p = product(admin, db, sku="MANGO")
    move(admin, p["id"], "inicial", "10", cost="3000")
    r = employee.get(f"/api/v1/productos/{p['id']}")
    assert r.status_code == 200
    assert "avg_cost" not in r.json() and "stock_value" not in r.json()
    assert "avg_cost" in admin.get(f"/api/v1/productos/{p['id']}").json()
    assert employee.post("/api/v1/categorias", json={"name": "X"}).status_code == 403
    mv = employee.get("/api/v1/inventario/movimientos").json()["items"][0]
    assert mv["unit_cost"] is None and mv["total_cost"] is None
    # Puede registrar mermas pero no ajustes.
    move(employee, p["id"], "merma", "1", motivo="Golpeado")
    move(employee, p["id"], "ajuste_entrada", "1", expect=403)


def test_admin_cannot_set_margin_or_price(admin, owner, db):
    cat = category(admin)
    r = admin.post("/api/v1/productos", json={"sku": "A", "name": "A", "category_id": cat,
                                              "base_unit_id": unit_id(db, "kg"), "target_margin": "0.3"})
    assert r.status_code == 403
    r = admin.post("/api/v1/productos", json={"sku": "A", "name": "A", "category_id": cat,
                                              "base_unit_id": unit_id(db, "kg"), "current_price": "100"})
    assert r.status_code == 403
    p = product(owner, db, sku="B", cat=cat, price="2500", target_margin="0.35")
    assert p["current_price"] == "2500.00"
    hist = db.execute(text("SELECT old_price, new_price, source FROM price_history")).all()
    assert hist == [(None, Decimal("2500.00"), "manual")]


# ------------------------------------------------------------------ inventario

def test_invalid_quantities_rejected(admin, db):
    p = product(admin, db, sku="Q")
    for bad in ["0", "-5", "abc", "1.123456"]:
        r = admin.post("/api/v1/inventario/movimientos",
                       json={"product_id": p["id"], "tipo": "ajuste_entrada", "cantidad": bad,
                             "motivo": "x"}, headers={"Idempotency-Key": key()})
        assert r.status_code == 422, bad
    # Salida mayor que la existencia
    r = move(admin, p["id"], "merma", "1", expect=409)
    assert r["error"]["codigo"] == "existencias_insuficientes"
    # Motivo obligatorio en mermas
    r = admin.post("/api/v1/inventario/movimientos",
                   json={"product_id": p["id"], "tipo": "merma", "cantidad": "1"},
                   headers={"Idempotency-Key": key()})
    assert r.status_code == 400
    # Sin clave de idempotencia
    r = admin.post("/api/v1/inventario/movimientos",
                   json={"product_id": p["id"], "tipo": "ajuste_entrada", "cantidad": "1", "motivo": "x"})
    assert r.status_code == 422


def test_weighted_average_cost(admin, db):
    p = product(admin, db, sku="TOMATE")
    move(admin, p["id"], "inicial", "10", cost="2000")
    move(admin, p["id"], "ajuste_entrada", "30", cost="3000", motivo="Recepción sin factura")
    q, avg = stock(db, p["id"])
    assert q == Decimal("40") and avg == Decimal("2750")      # (10·2000 + 30·3000)/40
    move(admin, p["id"], "merma", "4", motivo="Podrido")
    q, avg = stock(db, p["id"])
    assert q == Decimal("36") and avg == Decimal("2750")      # la salida no cambia el promedio


def test_idempotent_movement(admin, db):
    p = product(admin, db, sku="IDEM")
    k = key()
    move(admin, p["id"], "inicial", "5", cost="100", k=k)
    r = admin.post("/api/v1/inventario/movimientos",
                   json={"product_id": p["id"], "tipo": "inicial", "cantidad": "5",
                         "costo_unitario": "100"}, headers={"Idempotency-Key": k})
    assert r.status_code == 200 and r.headers["Idempotent-Replay"] == "true"
    assert stock(db, p["id"])[0] == Decimal("5")


def test_movements_are_immutable_and_reversal_is_traceable(admin, db):
    p = product(admin, db, sku="REV")
    move(admin, p["id"], "inicial", "10", cost="1000")
    m = move(admin, p["id"], "merma", "4", motivo="Error de digitación")
    assert stock(db, p["id"])[0] == Decimal("6")

    r = admin.post(f"/api/v1/inventario/movimientos/{m['id']}/revertir", json={"motivo": "Se digitó mal"})
    assert r.status_code == 201
    rev = r.json()
    assert rev["reverses_movement_id"] == m["id"] and rev["quantity_base"] == "4.0000"
    assert stock(db, p["id"])[0] == Decimal("10")
    assert lots_total(db, p["id"]) == Decimal("10")
    # No se puede revertir dos veces
    r = admin.post(f"/api/v1/inventario/movimientos/{m['id']}/revertir", json={"motivo": "otra vez"})
    assert r.status_code == 409
    # Ni el rol de la aplicación ni nadie puede editar/borrar movimientos
    from sqlalchemy.exc import ProgrammingError
    with pytest.raises(ProgrammingError):
        db.execute(text("UPDATE stock_movements SET quantity = 1"))
    db.rollback()
    with pytest.raises(ProgrammingError):
        db.execute(text("DELETE FROM stock_movements"))
    db.rollback()


def test_trigger_blocks_changes_even_for_schema_owner(owner_engine, admin, db):
    p = product(admin, db, sku="TRG")
    move(admin, p["id"], "inicial", "1", cost="1")
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="inmutables"):
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE stock_movements SET reason = 'x'"))


def test_fefo_lots(admin, db):
    p = product(admin, db, sku="FEFO")
    move(admin, p["id"], "inicial", "5", cost="100", fecha_vencimiento="2030-01-20", lote="A")
    move(admin, p["id"], "ajuste_entrada", "5", cost="100", fecha_vencimiento="2030-01-10", lote="B",
         motivo="x")
    move(admin, p["id"], "merma", "7", motivo="x")
    rows = dict(db.execute(text("SELECT code, remaining_qty FROM lots WHERE product_id=:p"),
                           {"p": p["id"]}).all())
    assert rows == {"B": Decimal("0"), "A": Decimal("3")}  # el que vence primero sale primero
    r = admin.get("/api/v1/inventario/lotes", params={"product_id": p["id"]})
    assert [lot["code"] for lot in r.json()["items"]] == ["A"]


def test_concurrent_outbound_never_oversells(app, admin, db):
    """Dos (o más) salidas simultáneas sobre la misma existencia: solo pasan las que caben."""
    p = product(admin, db, sku="CONC")
    move(admin, p["id"], "inicial", "10", cost="500")

    from app.domains.auth.models import User
    from app.domains.catalog.models import Product
    from app.domains.inventory import service
    from app.domains.inventory.models import MovementType
    admin_id = db.execute(text("SELECT id FROM users WHERE username='admin'")).scalar()

    barrier = threading.Barrier(6)
    results = []

    def worker():
        s = session_factory()()
        try:
            barrier.wait()
            prod = s.get(Product, p["id"])
            service.record_outbound(s, product=prod, movement_type=MovementType.MERMA,
                                    quantity=Decimal(3), quantity_base=Decimal(3), pu=None,
                                    user_id=admin_id, reason="concurrencia")
            s.commit()
            results.append("ok")
        except service.InsufficientStock:
            s.rollback()
            results.append("rechazada")
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 3 and results.count("rechazada") == 3
    q, _ = stock(db, p["id"])
    assert q == Decimal("1")
    total_moves = db.execute(text("SELECT sum(quantity_base) FROM stock_movements WHERE product_id=:p"),
                             {"p": p["id"]}).scalar()
    assert total_moves == q            # el saldo coincide con la suma de movimientos
    assert lots_total(db, p["id"]) == q
    del User


def test_concurrent_inbound_no_lost_updates(admin, db):
    p = product(admin, db, sku="CONC2")
    from app.domains.catalog.models import Product
    from app.domains.inventory import service
    from app.domains.inventory.models import MovementType
    barrier = threading.Barrier(8)

    def worker(i):
        s = session_factory()()
        barrier.wait()
        prod = s.get(Product, p["id"])
        service.record_inbound(s, product=prod, movement_type=MovementType.AJUSTE_ENTRADA,
                               quantity=Decimal(1), quantity_base=Decimal(1), pu=None, user_id=None,
                               unit_cost=Decimal(1000 + i * 100), reason="c")
        s.commit()
        s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    q, avg = stock(db, p["id"])
    assert q == Decimal("8")
    assert avg == Decimal("1350")  # promedio exacto de 1000..1700 sin importar el orden


def test_low_stock_filter(admin, db):
    p = product(admin, db, sku="LOW", min_stock="5")
    product(admin, db, sku="OK", min_stock="0")
    move(admin, p["id"], "inicial", "3", cost="10")
    r = admin.get("/api/v1/productos", params={"bajo_minimo": True})
    assert [x["sku"] for x in r.json()["items"]] == ["LOW"]
    assert r.json()["items"][0]["low_stock"] is True


def test_suppliers_crud(admin, employee):
    r = admin.post("/api/v1/proveedores", json={"name": "Distribuidora Prueba", "tax_id": "900123456-7",
                                                "phone": "300 123 4567", "email": "ventas@ejemplo.com"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert admin.post("/api/v1/proveedores", json={"name": "Otro", "tax_id": "900123456-7"}).status_code == 409
    assert admin.post("/api/v1/proveedores", json={"name": "X", "email": "no-es-correo"}).status_code == 422
    assert employee.get("/api/v1/proveedores").json()["total"] == 1
    assert employee.post("/api/v1/proveedores", json={"name": "Y"}).status_code == 403
    r = admin.patch(f"/api/v1/proveedores/{sid}", json={"is_active": False})
    assert r.json()["is_active"] is False
