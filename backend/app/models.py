"""Importa todos los modelos para que SQLAlchemy y Alembic conozcan el esquema completo."""


def import_all_models() -> None:
    from app.domains.audit import models as _audit  # noqa: F401
    from app.domains.auth import models as _auth  # noqa: F401
    from app.domains.catalog import models as _catalog  # noqa: F401
    from app.domains.inventory import models as _inventory  # noqa: F401
    from app.domains.market import models as _market  # noqa: F401
    from app.domains.pricing import models as _pricing  # noqa: F401
    from app.domains.purchases import models as _purchases  # noqa: F401
    from app.domains.sales import models as _sales  # noqa: F401
    from app.domains.settings import models as _settings  # noqa: F401
    from app.domains.suppliers import models as _suppliers  # noqa: F401
