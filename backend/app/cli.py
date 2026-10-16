"""Comandos de administración que se ejecutan en el servidor.

  python -m app.cli crear-propietario
  python -m app.cli restablecer-contrasena <usuario>

No existen credenciales predeterminadas: la contraseña se pide de forma interactiva
(sin eco) y nunca se pasa por argumentos de línea de comandos ni por variables de entorno.
"""
import getpass
import sys

from sqlalchemy import select

from app.core.db import session_factory
from app.core.security import validate_password_strength
from app.domains.auth import service
from app.domains.auth.models import User
from app.domains.auth.permissions import Role
from app.models import import_all_models


def _ask_password() -> str:
    while True:
        p1 = getpass.getpass("Contraseña (mín. 10 caracteres, letras y números/símbolos): ")
        try:
            validate_password_strength(p1)
        except ValueError as e:
            print(f"  {e}")
            continue
        if getpass.getpass("Repita la contraseña: ") != p1:
            print("  Las contraseñas no coinciden")
            continue
        return p1


def crear_propietario() -> None:
    username = input("Usuario (ej. propietario): ").strip().lower()
    full_name = input("Nombre completo: ").strip()
    password = _ask_password()
    with session_factory()() as db:
        user = service.create_user(db, username=username, full_name=full_name,
                                   role=Role.PROPIETARIO, password=password, actor_id=None,
                                   ip="cli", must_change=False)
        print(f"Propietario '{user.username}' creado (id {user.id}).")


def restablecer(username: str) -> None:
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == username.lower()))
        if user is None:
            sys.exit(f"No existe el usuario '{username}'")
        password = _ask_password()
        service.reset_password(db, user.id, password, actor_id=None, ip="cli")
        print("Contraseña temporal asignada. El usuario deberá cambiarla al iniciar sesión; "
              "sus sesiones abiertas fueron cerradas.")


def main(argv: list[str]) -> None:
    import_all_models()
    if argv[:1] == ["crear-propietario"]:
        crear_propietario()
    elif argv[:1] == ["restablecer-contrasena"] and len(argv) == 2:
        restablecer(argv[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
