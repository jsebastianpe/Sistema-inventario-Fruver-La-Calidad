"""Errores de negocio con mensajes en español y manejadores que no filtran detalles internos."""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class AppError(Exception):
    status_code = 400
    code = "solicitud_invalida"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None,
                 status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(AppError):
    status_code = 404
    code = "no_encontrado"


class Conflict(AppError):
    status_code = 409
    code = "conflicto"


class Forbidden(AppError):
    status_code = 403
    code = "sin_permiso"


class Unauthorized(AppError):
    status_code = 401
    code = "no_autenticado"


class TooManyRequests(AppError):
    status_code = 429
    code = "demasiados_intentos"


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"codigo": code, "mensaje": message, "detalles": details}}


_MSG = {
    "missing": "Campo obligatorio",
    "greater_than": "Debe ser mayor que {gt}",
    "greater_than_equal": "Debe ser mayor o igual que {ge}",
    "less_than": "Debe ser menor que {lt}",
    "less_than_equal": "Debe ser menor o igual que {le}",
    "string_too_short": "Debe tener al menos {min_length} caracteres",
    "string_too_long": "Debe tener como máximo {max_length} caracteres",
    "decimal_parsing": "Número inválido",
    "decimal_max_places": "Demasiados decimales (máximo {decimal_places})",
    "int_parsing": "Debe ser un número entero",
    "date_from_datetime_parsing": "Fecha inválida",
    "date_parsing": "Fecha inválida (use AAAA-MM-DD)",
    "enum": "Valor no permitido. Opciones: {expected}",
    "literal_error": "Valor no permitido. Opciones: {expected}",
    "extra_forbidden": "Campo no permitido",
    "value_error": "{error}",
    "json_invalid": "JSON inválido",
}


def _translate(err: dict) -> dict:
    ctx = err.get("ctx") or {}
    tmpl = _MSG.get(err["type"])
    try:
        msg = tmpl.format(**ctx) if tmpl else err["msg"]
    except (KeyError, IndexError):
        msg = err["msg"]
    if err["type"] == "value_error":
        msg = str(ctx.get("error", err["msg"])).removeprefix("Value error, ")
    loc = [str(p) for p in err["loc"] if p not in ("body", "query", "path")]
    return {"campo": ".".join(loc), "mensaje": msg}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [_translate(e) for e in exc.errors()]
        return JSONResponse(
            _body("validacion", "Hay datos inválidos en la solicitud", details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        msg = {404: "Recurso no encontrado", 405: "Método no permitido"}.get(
            exc.status_code, str(exc.detail)
        )
        return JSONResponse(_body("http", msg), status_code=exc.status_code)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        # No se expone el SQL ni los valores; se registra el detalle en el log del servidor.
        log.warning("Violación de integridad: %s", exc.orig.__class__.__name__)
        return JSONResponse(
            _body("integridad", "La operación viola una regla de integridad de los datos"),
            status_code=409,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("Error no controlado")
        return JSONResponse(_body("error_interno", "Ocurrió un error interno"), status_code=500)
