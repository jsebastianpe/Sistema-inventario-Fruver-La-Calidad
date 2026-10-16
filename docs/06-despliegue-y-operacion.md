# Instalación, despliegue y operación

## 1. Componentes

| Servicio | Imagen | Función | Expuesto |
|---|---|---|---|
| `db` | postgres:17.5-alpine | Base de datos | **No** (solo red interna) |
| `api` | fruver-app | API REST + frontend; aplica migraciones al arrancar | Solo a través de Caddy |
| `worker` | fruver-app | Actualización diaria de precios de mercado, solicitudes manuales, latido | No |
| `caddy` (producción) | caddy:2.10.0-alpine | HTTPS automático (Let's Encrypt) y proxy inverso | 80/443 |

Roles de PostgreSQL, creados por `deploy/postgres/init/01-roles.sh`:

* `postgres` (superusuario): solo para inicialización, respaldos y restauración.
* `app_owner`: dueño del esquema. Lo usan únicamente las migraciones.
* `app_user`: conexiones ordinarias de la aplicación. Solo tiene DML, sin UPDATE ni DELETE sobre movimientos, auditoría ni historial de precios.

## 2. Desarrollo local

Requisitos: Docker Desktop, Python 3.12 o superior y Git.

```bash
cp .env.example .env            # y reemplace los valores "cambiar-..." por secretos aleatorios:
python -c "import secrets; print(secrets.token_urlsafe(32))"
cp compose.dev.yaml compose.override.yaml   # publica BD y API SOLO en 127.0.0.1 (no versionar)
docker compose up -d --build
docker compose exec api python -m app.cli crear-propietario   # pide usuario y contraseña (sin eco)
```

Abra <http://127.0.0.1:8000>. La documentación interactiva de la API está en <http://127.0.0.1:8000/api/docs> (solo fuera de producción) y la especificación en `docs/api/openapi.json`.

### Pruebas

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate       # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # usa EXCLUSIVAMENTE la base APP_TEST_DB_NAME (inventario_test)
pip-audit                 # vulnerabilidades conocidas en dependencias
# Pruebas de interfaz (navegador real), contra un usuario propietario de PRUEBA:
pip install -e ".[e2e]"
E2E_USER=... E2E_PASSWORD=... python ../e2e/test_smoke.py && python ../e2e/test_flow.py
```

Las pruebas automatizadas recrean el esquema con las migraciones y vacían las tablas entre una prueba y otra. **Nunca** apunte `APP_TEST_DB_NAME` a una base con información real. El código verifica que sea distinta de la base principal.

## 3. Despliegue en un servidor

Requisitos: un VPS Linux (2 vCPU y 2–4 GB de RAM es suficiente para el piloto), Docker Engine con el plugin Compose, y un dominio con un registro DNS A/AAAA que apunte al servidor.

```bash
# En el servidor
sudo mkdir -p /opt/fruver && sudo chown $USER /opt/fruver && cd /opt/fruver
git clone <repositorio> .
cp .env.example .env && chmod 600 .env
#  Edite .env:
#   APP_ENV=production
#   APP_TEST_DB_NAME=            (vacío: en producción no se crea la base de pruebas)
#   PUBLIC_DOMAIN=inventario.su-dominio.com   ACME_EMAIL=su-correo
#   POSTGRES_SUPERUSER_PASSWORD / APP_OWNER_PASSWORD / APP_USER_PASSWORD = secretos aleatorios distintos
#  NO copie compose.override.yaml al servidor.
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
docker compose -f compose.yaml -f compose.prod.yaml exec api python -m app.cli crear-propietario
```

Firewall: abra solo 22 (SSH, idealmente restringido), 80 y 443. El puerto 5432 **no** se publica.

Comprobación:

```bash
curl -fsS https://$PUBLIC_DOMAIN/api/v1/health      # {"estado":"ok","base_de_datos":"ok"}
docker compose -f compose.yaml -f compose.prod.yaml ps
docker compose -f compose.yaml -f compose.prod.yaml logs -f worker
```

En producción se aplica lo siguiente:

* Cookies de sesión con `Secure`, `HttpOnly` y `SameSite=Strict`.
* HSTS y CSP `default-src 'self'`.
* Documentación de la API deshabilitada.
* Contenedores ejecutados como un usuario sin privilegios.
* Logs rotados (10 MB × 5).

## 4. Actualización de versiones

Los datos viven en el volumen `pgdata` y no se borran al reconstruir contenedores.

```bash
cd /opt/fruver
deploy/scripts/backup.sh                       # 1. respaldo previo, siempre
git pull                                       # 2. nuevo código
docker compose -f compose.yaml -f compose.prod.yaml up -d --build   # 3. reconstruye; api aplica migraciones
curl -fsS https://$PUBLIC_DOMAIN/api/v1/health # 4. verificación
```

**Nunca** ejecute `docker compose down -v`: el parámetro `-v` borra el volumen de la base de datos.

Si algo falla, vuelva a la versión anterior (`git checkout <versión>`) y, si la migración ya modificó datos, restaure el respaldo del paso 1.

## 5. Copias de seguridad

`deploy/scripts/backup.sh` hace lo siguiente:

1. Ejecuta `pg_dump` en formato custom dentro del contenedor.
2. Verifica que el archivo sea legible (`pg_restore --list`).
3. Guarda `backups/inventario_<fecha>.dump` y su suma SHA-256, con permisos 600.
4. Borra los respaldos con más de 30 días (`RETENTION_DAYS`).

Programación diaria (cron del servidor):

```
15 2 * * *  cd /opt/fruver && deploy/scripts/backup.sh >> backups/backup.log 2>&1
```

**Copia fuera del servidor (obligatoria):** un respaldo que solo existe en el mismo servidor no protege contra la pérdida del servidor. Copie diariamente `backups/` a un almacenamiento externo, por ejemplo con `rclone` hacia un bucket con cifrado y versionado, o active además el respaldo del proveedor del VPS. Los respaldos contienen datos comerciales y hashes de contraseñas, así que deben guardarse cifrados.

## 6. Restauración

**Prueba periódica (recomendada mensualmente; no toca la base principal):**

```bash
deploy/scripts/restore.sh backups/inventario_<fecha>.dump inventario_prueba_restauracion
# Revise el resumen (versión de migración y filas por tabla) y luego:
docker compose exec -T db psql -U postgres -c 'DROP DATABASE inventario_prueba_restauracion'
```

**Restauración real:**

```bash
deploy/scripts/backup.sh                                  # respaldo del estado actual, por si acaso
deploy/scripts/restore.sh backups/inventario_<fecha>.dump # pide escribir RESTAURAR; detiene y reinicia api/worker
```

**Verificado el 2026-10-16** en el entorno de desarrollo: respaldo de 352 KB y restauración en una base separada. Coincidieron las filas de todas las tablas, el saldo total y el valor del inventario. También se conservaron los permisos (`app_user` no puede borrar movimientos) y el trigger de inmutabilidad.

## 7. Monitorización

| Qué | Cómo |
|---|---|
| Salud de la API y la BD | `GET /api/v1/health`; healthcheck de Docker en `api` y `db` |
| Proceso automático | Latido cada minuto en `worker_heartbeats`. El panel muestra "proceso automático detenido" si no hay latido en 3 minutos |
| Actualización de precios | Pantalla *Precios de mercado → Estado*: cada ejecución con registros leídos, nuevos, repetidos, inválidos, variaciones y error |
| Alertas | Panel de inicio: existencias bajas, vencimientos, fallas de actualización, cotizaciones viejas, sugerencias pendientes |
| Errores | `docker compose logs api worker`. Los errores internos se registran sin exponer detalles al usuario |

Recomendación: configure un monitor externo gratuito (por ejemplo, un servicio de *uptime*) contra `/api/v1/health`.

## 8. Proceso automático de precios

* Corre a diario a `MARKET_SYNC_HOUR:MARKET_SYNC_MINUTE` (15:30 por defecto, hora de Colombia). SIPSA publica desde las 2:00 p. m.
* Si el `worker` estuvo detenido a esa hora, se pone al día al arrancar.
* Reintenta las fallas transitorias (red, HTTP 5xx, XML incompleto) a los 15, 45 y 120 minutos. Los errores definitivos (HTTP 4xx, SOAP Fault, formato inesperado) no se reintentan.
* Es idempotente: repetir una ejecución no duplica cotizaciones. Un bloqueo consultivo de PostgreSQL impide dos ejecuciones simultáneas.
* Se puede deshabilitar desde la configuración comercial ("Actualizar precios de mercado automáticamente").

## 9. Rotación de secretos

| Secreto | Procedimiento |
|---|---|
| `APP_USER_PASSWORD` | `docker compose exec db psql -U postgres -c "ALTER ROLE app_user PASSWORD '<nuevo>'"`, actualizar `.env`, `docker compose up -d api worker` |
| `APP_OWNER_PASSWORD` | Igual, con `app_owner` |
| `POSTGRES_SUPERUSER_PASSWORD` | `ALTER ROLE postgres PASSWORD '<nuevo>'` y actualizar `.env` |
| Sesiones de usuario | Para cerrar todas: `docker compose exec db psql -U postgres -d inventario -c "UPDATE auth_sessions SET revoked_at = now() WHERE revoked_at IS NULL"` |
| Contraseña de un usuario | Pantalla *Usuarios → Contraseña temporal* (cierra sus sesiones), o `docker compose exec api python -m app.cli restablecer-contrasena <usuario>` |

Las variables de `POSTGRES_*` del contenedor solo se usan la primera vez que se crea el volumen. Después, las contraseñas se cambian con `ALTER ROLE`.

## 10. Recuperación ante incidentes

1. **Contener.** Si sospecha que una cuenta fue comprometida, desactive el usuario o cierre todas las sesiones (sección 9).
2. **Rotar** los secretos de la base de datos y las contraseñas de los usuarios afectados.
3. **Investigar** con la auditoría (*Reportes → Auditoría*: inicios de sesión, cambios de precio, anulaciones, exportaciones y cambios de configuración) y con `login_attempts`.
4. **Corregir datos** con movimientos compensatorios y anulaciones, que dejan traza, en lugar de editar la base de datos.
5. **Si el servidor se pierde:** cree un servidor nuevo (sección 3), copie el respaldo externo más reciente, ejecute `restore.sh` y apunte el DNS al nuevo servidor.
6. Documente el incidente: qué pasó, desde cuándo, qué datos se afectaron y qué se cambió.
