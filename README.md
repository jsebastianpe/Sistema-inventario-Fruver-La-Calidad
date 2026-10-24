# Gestión Comercial Fruver

Sistema web de gestión comercial para un negocio colombiano de frutas, verduras y otros productos. Cubre:

* catálogo con unidades y presentaciones explícitas;
* inventario trazable con lotes y vencimientos;
* compras con costo real (reparto de transporte y mermas);
* ventas con precio histórico;
* precios mayoristas del **DANE – SIPSA**;
* precios sugeridos con aprobación;
* reportes, auditoría y usuarios con roles.

Moneda COP, zona horaria `America/Bogota`, interfaz en español.

> **Estado (2026-10-16):** implementado y verificado en desarrollo (107 pruebas automatizadas, pruebas de interfaz en navegador e integración real con SIPSA). Apto para iniciar un **piloto** tras el despliegue en un servidor. **No** está desplegado en producción y **no** ha pasado una auditoría de seguridad. Ver [estado y pendientes](docs/08-estado-verificacion-y-pendientes.md).

## Arquitectura

```
Navegador (HTML/CSS/JS) ──HTTPS──▶ Caddy ──▶ api (FastAPI + frontend) ──▶ PostgreSQL
                                              worker (precios diarios) ──┘   (sin acceso desde internet)
```

* **Backend:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL 17, APScheduler.
* **Frontend:** HTML5, CSS3 y JavaScript sin frameworks.
* **Infraestructura:** Docker Compose y Caddy (HTTPS automático).

```
backend/app/core/        configuración, BD, decimales, seguridad, errores
backend/app/domains/     auth · catalog · inventory · purchases · sales · suppliers
                         market (conector SIPSA) · pricing · reports · settings · audit
backend/app/worker/      proceso automático
backend/alembic/         migraciones versionadas
backend/tests/           pruebas automatizadas (pytest)
frontend/                interfaz
e2e/                     pruebas de interfaz en navegador real (Playwright)
deploy/                  Caddy, inicialización de PostgreSQL, respaldo/restauración
docs/                    documentación
```

## Inicio rápido (desarrollo)

```bash
cp .env.example .env                        # reemplace los secretos "cambiar-..."
cp compose.dev.yaml compose.override.yaml   # expone BD y API solo en 127.0.0.1
docker compose up -d --build
docker compose exec api python -m app.cli crear-propietario
# http://127.0.0.1:8000   ·   API: http://127.0.0.1:8000/api/docs
```

Despliegue, respaldos, restauración, rotación de secretos e incidentes: [docs/06-despliegue-y-operacion.md](docs/06-despliegue-y-operacion.md).

## Documentación

| Documento | Contenido |
|---|---|
| [01 Análisis y arquitectura](docs/01-analisis-y-arquitectura.md) | Entorno, arquitectura y decisiones técnicas |
| [02 Investigación SIPSA](docs/02-investigacion-sipsa.md) | Servicio del DANE verificado, problemas de datos, resultado real |
| [03 Plan](docs/03-plan-de-implementacion.md) | Fases y riesgos |
| [04 Decisiones pendientes](docs/04-decisiones-pendientes.md) | Preguntas al cliente y opciones provisionales |
| [05 Reglas de costos y precios](docs/05-reglas-de-costos-y-precios.md) | Fórmulas exactas e indicadores |
| [06 Despliegue y operación](docs/06-despliegue-y-operacion.md) | Instalación, servidor, actualización, respaldo, restauración, seguridad |
| [07 Manual de usuario](docs/07-manual-de-usuario.md) | Uso diario del sistema |
| [08 Estado y pendientes](docs/08-estado-verificacion-y-pendientes.md) | Funciones verificadas, limitaciones, pendientes, costos |
| [API (OpenAPI)](docs/api/openapi.json) | 67 operaciones REST |

## Aviso

Las ventas registradas son **registros internos**: el sistema no emite facturación electrónica DIAN. Los precios del SIPSA son **referencias mayoristas** y no sustituyen el costo real ni definen por sí solos el precio de venta.
