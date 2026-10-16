# Imagen única para `api` y `worker`. Contexto de construcción: raíz del repositorio.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FRONTEND_DIR=/srv/frontend \
    PYTHONPATH=/srv/backend

WORKDIR /srv/backend

# Dependencias primero (capa reutilizable entre versiones del código).
# Se instalan solo las dependencias; el paquete del proyecto se usa desde /srv/backend.
COPY backend/pyproject.toml ./
RUN mkdir app && touch app/__init__.py \
 && pip install . && pip uninstall -y fruver-gestion \
 && rm -rf app build *.egg-info

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY frontend /srv/frontend

RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8000
