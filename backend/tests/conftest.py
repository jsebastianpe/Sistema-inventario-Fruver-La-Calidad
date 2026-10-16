"""Infraestructura de pruebas.

* Usa EXCLUSIVAMENTE la base de datos de pruebas (APP_TEST_DB_NAME), nunca la de producción.
* El esquema se crea con las migraciones de Alembic (así también se prueban las migraciones).
* La aplicación se conecta con el rol app_user (sin DDL), igual que en producción.
* Entre pruebas se vacían las tablas con el rol app_owner.
"""
import os

os.environ.setdefault("APP_ENV", "test")

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.db import configure_engine, session_factory

cfg = get_settings()
assert cfg.app_test_db_name and cfg.app_test_db_name != cfg.app_db_name, \
    "Las pruebas requieren una base de datos distinta de la principal"

PASSWORD = "Clave-de-prueba-123"


@pytest.fixture(scope="session")
def owner_engine():
    eng = create_engine(cfg.test_owner_db_url)
    with eng.begin() as conn:
        conn.execute(text("""
            DO $$ DECLARE r record; BEGIN
              FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
              END LOOP;
              FOR r IN SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                       WHERE n.nspname = 'public' LOOP
                EXECUTE 'DROP FUNCTION IF EXISTS public.' || quote_ident(r.proname) || ' CASCADE';
              END LOOP;
            END $$;
        """))
    alembic_cfg = Config(str(cfg_path()))
    alembic_cfg.attributes["url"] = cfg.test_owner_db_url
    command.upgrade(alembic_cfg, "head")
    yield eng
    eng.dispose()


def cfg_path():
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(scope="session")
def app(owner_engine):
    configure_engine(cfg.test_app_db_url)
    from app.main import create_app
    return create_app()


@pytest.fixture(autouse=True)
def _clean_db(owner_engine):
    yield
    with owner_engine.begin() as conn:
        # Las tablas de referencia sembradas por las migraciones se conservan.
        tables = conn.execute(text(
            "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
            "WHERE schemaname='public' AND tablename NOT IN "
            "('alembic_version', 'units', 'market_sources')")).scalar()
        if tables:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        conn.execute(text("DELETE FROM units WHERE id > 10"))
        # market_sources referencia a users, así que el TRUNCATE en cascada la vacía: se re-siembra
        # con el mismo registro que crea la migración 0003.
        conn.execute(text("TRUNCATE market_sources RESTART IDENTITY CASCADE"))
        conn.execute(text(
            "INSERT INTO market_sources (code, name, url) VALUES ('sipsa_ciudad', "
            "'DANE – SIPSA: precios mayoristas promedio diarios por ciudad (promediosSipsaCiudad)', "
            "'https://appweb.dane.gov.co/sipsaWS/SrvSipsaUpraBeanService')"))


@pytest.fixture
def db(app):
    s = session_factory()()
    yield s
    s.close()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def make_user(db, username: str, role, password: str = PASSWORD):
    from app.domains.auth import service
    return service.create_user(db, username=username, full_name=username.title(), role=role,
                               password=password, actor_id=None, ip=None, must_change=False)


def login_client(app, username: str, password: str = PASSWORD) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


@pytest.fixture
def owner(app, db):
    from app.domains.auth.permissions import Role
    make_user(db, "duena", Role.PROPIETARIO)
    return login_client(app, "duena")


@pytest.fixture
def admin(app, db):
    from app.domains.auth.permissions import Role
    make_user(db, "admin", Role.ADMINISTRADOR)
    return login_client(app, "admin")


@pytest.fixture
def employee(app, db):
    from app.domains.auth.permissions import Role
    make_user(db, "empleado", Role.EMPLEADO)
    return login_client(app, "empleado")
