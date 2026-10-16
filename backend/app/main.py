import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import register_error_handlers
from app.models import import_all_models

import_all_models()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    cfg = get_settings()
    app = FastAPI(
        title="Gestión Comercial Fruver — API",
        version="0.1.0",
        description="API REST del sistema de gestión comercial. Moneda: COP. Zona horaria: America/Bogota.",
        docs_url=None if cfg.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if cfg.is_production else "/api/openapi.json",
    )
    register_error_handlers(app)

    if cfg.cors_origin_list:
        app.add_middleware(
            CORSMiddleware, allow_origins=cfg.cors_origin_list, allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        if cfg.is_production:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    api = APIRouter(prefix="/api/v1")

    @api.get("/health", tags=["Operación"])
    def health():
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"estado": "ok", "base_de_datos": "ok"}

    from app.routers import include_routers
    include_routers(api)
    app.include_router(api)

    frontend = Path(cfg.frontend_dir)
    if frontend.is_dir():
        app.mount("/static", StaticFiles(directory=frontend), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(frontend / "index.html")

    return app


app = create_app()
