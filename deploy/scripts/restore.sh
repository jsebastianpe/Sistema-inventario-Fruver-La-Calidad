#!/usr/bin/env bash
# Restauración de una copia de seguridad.
#   Prueba de restauración (NO toca la base principal):
#       deploy/scripts/restore.sh backups/inventario_X.dump inventario_prueba_restauracion
#   Restauración real sobre la base principal (detiene api y worker, pide confirmación):
#       deploy/scripts/restore.sh backups/inventario_X.dump
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; [ -f .env ] && . ./.env; set +a

DUMP="${1:?Indique el archivo .dump}"
MAIN="${APP_DB_NAME:-inventario}"
TARGET="${2:-$MAIN}"
COMPOSE=(docker compose -f compose.yaml)
[ -f compose.override.yaml ] && COMPOSE+=(-f compose.override.yaml)
psql_su() { "${COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres "$@"; }

[ -f "$DUMP" ] || { echo "No existe $DUMP"; exit 1; }
if [ -f "$DUMP.sha256" ]; then sha256sum -c "$DUMP.sha256"; fi

if [ "$TARGET" = "$MAIN" ]; then
  echo "ATENCIÓN: se REEMPLAZARÁ la base de datos '$MAIN' con $DUMP."
  echo "Se recomienda crear antes una copia del estado actual (deploy/scripts/backup.sh)."
  read -r -p "Escriba RESTAURAR para continuar: " ok
  [ "$ok" = "RESTAURAR" ] || { echo "Cancelado"; exit 1; }
  "${COMPOSE[@]}" stop api worker
fi

psql_su -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TARGET' AND pid <> pg_backend_pid();" > /dev/null
psql_su -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET\";"
psql_su -d postgres -c "CREATE DATABASE \"$TARGET\" OWNER app_owner ENCODING 'UTF8' TEMPLATE template0;"
psql_su -d "$TARGET" <<SQL
REVOKE ALL ON DATABASE "$TARGET" FROM PUBLIC;
GRANT CONNECT ON DATABASE "$TARGET" TO app_user;
ALTER SCHEMA public OWNER TO app_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
# Restaura objetos, propietarios y permisos (incluye los REVOKE de las tablas inmutables).
"${COMPOSE[@]}" exec -T db pg_restore -U postgres -d "$TARGET" --exit-on-error --single-transaction < "$DUMP"

echo "Restauración completada en '$TARGET'. Resumen:"
psql_su -d "$TARGET" -tAc "SELECT 'migración: ' || version_num FROM alembic_version;"
psql_su -d "$TARGET" -tAc "SELECT relname || ': ' || n_live_tup FROM pg_stat_user_tables ORDER BY relname;" || true

if [ "$TARGET" = "$MAIN" ]; then
  "${COMPOSE[@]}" start api worker
  echo "Servicios reiniciados. Verifique: curl -fsS https://\$PUBLIC_DOMAIN/api/v1/health"
fi
