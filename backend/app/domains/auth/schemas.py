from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import validate_password_strength
from app.domains.auth.permissions import GRANTABLE, Role

USERNAME_PATTERN = r"^[a-zA-Z0-9._-]{3,50}$"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    role: Role
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    grants: list[str] = []

    @field_validator("grants", mode="before")
    @classmethod
    def _grants(cls, v):
        return [g if isinstance(g, str) else g.permission for g in (v or [])]


class MeOut(BaseModel):
    usuario: UserOut
    permisos: list[str]
    csrf_token: str


class _PasswordMixin(BaseModel):
    @field_validator("new_password", check_fields=False)
    @classmethod
    def _strength(cls, v: str) -> str:
        validate_password_strength(v)
        return v


class ChangePasswordIn(_PasswordMixin):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(max_length=200)


class UserCreate(_PasswordMixin):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=USERNAME_PATTERN)
    full_name: str = Field(min_length=2, max_length=120)
    role: Role
    new_password: str = Field(max_length=200, description="Contraseña temporal")


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(None, min_length=2, max_length=120)
    role: Role | None = None
    is_active: bool | None = None


class ResetPasswordIn(_PasswordMixin):
    new_password: str = Field(max_length=200, description="Contraseña temporal")


class GrantsIn(BaseModel):
    permisos: list[str]

    @field_validator("permisos")
    @classmethod
    def _valid(cls, v: list[str]) -> list[str]:
        allowed = {p.value for p in GRANTABLE}
        bad = [p for p in v if p not in allowed]
        if bad:
            raise ValueError(f"Permisos no válidos o no delegables: {', '.join(bad)}")
        return sorted(set(v))
