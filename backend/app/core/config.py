"""Configuración leída de variables de entorno (y del archivo .env en desarrollo)."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    app_timezone: str = "America/Bogota"

    # URLs completas (las define compose). En desarrollo local se construyen
    # a partir de las contraseñas del .env y del puerto publicado en 127.0.0.1.
    database_url: str | None = None
    migrations_database_url: str | None = None
    test_database_url: str | None = None
    test_migrations_database_url: str | None = None

    app_db_name: str = "inventario"
    app_test_db_name: str = "inventario_test"
    app_owner_password: str = ""
    app_user_password: str = ""
    db_host: str = "127.0.0.1"
    db_dev_port: int = 55432

    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    login_max_attempts: int = 5
    login_window_minutes: int = 15
    cors_origins: str = ""

    frontend_dir: str = str(ROOT_DIR / "frontend")

    market_sync_hour: int = 15
    market_sync_minute: int = 30
    sipsa_url: str = "https://appweb.dane.gov.co/sipsaWS/SrvSipsaUpraBeanService"

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in {"development", "production", "test"}:
            raise ValueError("APP_ENV debe ser development, production o test")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def _url(self, role: str, password: str, db: str) -> str:
        return f"postgresql+psycopg://{role}:{password}@{self.db_host}:{self.db_dev_port}/{db}"

    @property
    def app_db_url(self) -> str:
        return self.database_url or self._url("app_user", self.app_user_password, self.app_db_name)

    @property
    def owner_db_url(self) -> str:
        return self.migrations_database_url or self._url(
            "app_owner", self.app_owner_password, self.app_db_name
        )

    @property
    def test_app_db_url(self) -> str:
        return self.test_database_url or self._url(
            "app_user", self.app_user_password, self.app_test_db_name
        )

    @property
    def test_owner_db_url(self) -> str:
        return self.test_migrations_database_url or self._url(
            "app_owner", self.app_owner_password, self.app_test_db_name
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
