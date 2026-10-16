"""Ayudas para construir datos DE PRUEBA vía la API (solo en la base de datos de pruebas)."""
import uuid
from decimal import Decimal

from sqlalchemy import text


def key() -> str:
    return uuid.uuid4().hex


def unit_id(db, code: str) -> int:
    return db.execute(text("SELECT id FROM units WHERE code = :c"), {"c": code}).scalar_one()


def category(client, name="Frutas (prueba)") -> int:
    r = client.post("/api/v1/categorias", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def product(client, db, *, sku="P1", name="Producto de prueba", base="kg", cat=None,
            price=None, presentations=None, min_stock="0", **extra) -> dict:
    payload = {"sku": sku, "name": name, "category_id": cat or category(client, f"Cat {sku}"),
               "base_unit_id": unit_id(db, base), "min_stock": min_stock,
               "presentations": presentations or [], **extra}
    if price is not None:
        payload["current_price"] = str(price)
    r = client.post("/api/v1/productos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def move(client, product_id, tipo, cantidad, *, cost=None, motivo="Prueba", pu=None, k=None,
         expect=201, **extra):
    body = {"product_id": product_id, "tipo": tipo, "cantidad": str(cantidad), "motivo": motivo, **extra}
    if cost is not None:
        body["costo_unitario"] = str(cost)
    if pu is not None:
        body["product_unit_id"] = pu
    r = client.post("/api/v1/inventario/movimientos", json=body, headers={"Idempotency-Key": k or key()})
    assert r.status_code == expect, r.text
    return r.json()


def stock(db, product_id) -> tuple[Decimal, Decimal]:
    row = db.execute(text("SELECT quantity, avg_cost FROM stock_balances WHERE product_id=:p"),
                     {"p": product_id}).one()
    return row[0], row[1]


def lots_total(db, product_id) -> Decimal:
    return db.execute(text("SELECT coalesce(sum(remaining_qty),0) FROM lots WHERE product_id=:p"),
                      {"p": product_id}).scalar_one()
