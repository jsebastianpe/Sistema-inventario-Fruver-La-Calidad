from fastapi.testclient import TestClient
from sqlalchemy import text

from app.domains.auth.permissions import Role
from tests.conftest import PASSWORD, login_client, make_user


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok"


def test_endpoints_require_authentication(client):
    for method, url in [("get", "/api/v1/auth/me"), ("get", "/api/v1/usuarios"),
                        ("get", "/api/v1/configuracion"), ("patch", "/api/v1/configuracion")]:
        r = getattr(client, method)(url)
        assert r.status_code == 401, url
        assert r.json()["error"]["codigo"] == "no_autenticado"


def test_login_sets_httponly_cookie_and_hashes_password(app, db, client):
    make_user(db, "ana", Role.EMPLEADO)
    stored = db.execute(text("SELECT password_hash FROM users WHERE username='ana'")).scalar()
    assert stored.startswith("$argon2id$") and PASSWORD not in stored

    r = client.post("/api/v1/auth/login", json={"username": "ANA", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    body = r.json()
    assert body["usuario"]["role"] == "empleado"
    assert "usuarios.gestionar" not in body["permisos"]

    # El token de sesión no se guarda en claro.
    token = client.cookies.get("sid")
    assert db.execute(text("SELECT count(*) FROM auth_sessions WHERE token_hash = :t"),
                      {"t": token}).scalar() == 0


def test_wrong_password_generic_message(app, db, client):
    make_user(db, "ana", Role.EMPLEADO)
    r1 = client.post("/api/v1/auth/login", json={"username": "ana", "password": "mala-123456"})
    r2 = client.post("/api/v1/auth/login", json={"username": "noexiste", "password": "x"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["mensaje"] == r2.json()["error"]["mensaje"]


def test_login_rate_limit(app, db, client):
    make_user(db, "ana", Role.EMPLEADO)
    for _ in range(5):
        assert client.post("/api/v1/auth/login",
                           json={"username": "ana", "password": "mala-123456"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"username": "ana", "password": PASSWORD})
    assert r.status_code == 429  # bloqueado incluso con la contraseña correcta


def test_csrf_required_for_mutations(owner):
    token = owner.headers.pop("X-CSRF-Token")
    r = owner.patch("/api/v1/configuracion", json={"default_margin": "0.25"})
    assert r.status_code == 403 and r.json()["error"]["codigo"] == "csrf_invalido"
    owner.headers["X-CSRF-Token"] = token
    assert owner.patch("/api/v1/configuracion", json={"default_margin": "0.25"}).status_code == 200


def test_logout_revokes_session(owner):
    assert owner.get("/api/v1/auth/me").status_code == 200
    sid = owner.cookies.get("sid")
    assert owner.post("/api/v1/auth/logout").status_code == 204
    # Reutilizar la cookie robada/antigua tampoco funciona.
    owner.cookies.set("sid", sid)
    assert owner.get("/api/v1/auth/me").status_code == 401


def test_role_permissions_enforced_in_backend(owner, admin, employee):
    assert employee.get("/api/v1/usuarios").status_code == 403
    assert admin.get("/api/v1/usuarios").status_code == 403
    assert owner.get("/api/v1/usuarios").status_code == 200
    # Configuración: solo el propietario la modifica.
    assert admin.patch("/api/v1/configuracion", json={"default_margin": "0.2"}).status_code == 403
    assert employee.get("/api/v1/configuracion").status_code == 403


def test_user_management_and_forced_password_change(app, owner):
    r = owner.post("/api/v1/usuarios", json={
        "username": "pedro", "full_name": "Pedro Pérez", "role": "empleado",
        "new_password": "Temporal-2026"})
    assert r.status_code == 201, r.text
    assert r.json()["must_change_password"] is True

    pedro = login_client(app, "pedro", "Temporal-2026")
    # Con contraseña temporal solo puede consultar su perfil y cambiarla.
    assert pedro.get("/api/v1/configuracion").status_code == 403
    r = pedro.post("/api/v1/auth/cambiar-contrasena",
                   json={"current_password": "Temporal-2026", "new_password": "Nueva-clave-99"})
    assert r.status_code == 204
    me = pedro.get("/api/v1/auth/me").json()
    assert me["usuario"]["must_change_password"] is False


def test_weak_password_rejected(owner):
    r = owner.post("/api/v1/usuarios", json={
        "username": "pedro", "full_name": "Pedro", "role": "empleado", "new_password": "123"})
    assert r.status_code == 422
    assert r.json()["error"]["detalles"][0]["campo"] == "new_password"


def test_cannot_remove_last_owner(owner):
    me = owner.get("/api/v1/auth/me").json()["usuario"]
    r = owner.patch(f"/api/v1/usuarios/{me['id']}", json={"role": "administrador"})
    assert r.status_code == 409


def test_deactivated_user_loses_access(app, db, owner):
    u = make_user(db, "temporal", Role.EMPLEADO)
    c = login_client(app, "temporal")
    assert owner.patch(f"/api/v1/usuarios/{u.id}", json={"is_active": False}).status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401


def test_explicit_grant(app, db, owner):
    u = make_user(db, "admin2", Role.ADMINISTRADOR)
    c = login_client(app, "admin2")
    assert c.patch("/api/v1/configuracion", json={"rounding_step": "100"}).status_code == 403
    r = owner.put(f"/api/v1/usuarios/{u.id}/permisos", json={"permisos": ["configuracion.gestionar"]})
    assert r.status_code == 200
    assert c.patch("/api/v1/configuracion", json={"rounding_step": "100"}).status_code == 200
    # La gestión de usuarios no es delegable.
    r = owner.put(f"/api/v1/usuarios/{u.id}/permisos", json={"permisos": ["usuarios.gestionar"]})
    assert r.status_code == 422


def test_settings_validation(owner):
    r = owner.patch("/api/v1/configuracion", json={"default_margin": "1"})
    assert r.status_code == 400
    r = owner.patch("/api/v1/configuracion", json={"default_margin": "0.6",
                                                   "sales_commission_rate": "0.5"})
    assert r.status_code == 400
    r = owner.patch("/api/v1/configuracion", json={"campo_inventado": 1})
    assert r.status_code == 400


def test_audit_log_is_append_only_for_app_role(owner, db):
    rows = db.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars().all()
    assert "auth.inicio_sesion" in rows
    import pytest
    from sqlalchemy.exc import ProgrammingError
    with pytest.raises(ProgrammingError):
        db.execute(text("DELETE FROM audit_log"))
    db.rollback()
