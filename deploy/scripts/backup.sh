#!/usr/bin/env bash
# Copia de seguridad de PostgreSQL (formato custom de pg_dump, comprimido).
#   Uso:   deploy/scripts/backup.sh            (desde la raíz del proyecto, en el servidor)
#   Cron:  15 2 * * *  cd /opt/fruver && deploy/scripts/backup.sh >> backups/backup.log 2>&1
# Variables opcionales: BACKUP_DIR (por defecto ./backups), RETENTION_DAYS (por defecto 30).
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; [ -f .env ] && . ./.env; set +a

DB="${APP_DB_NAME:-inventario}"
DIR="${BACKUP_DIR:-./backups}"
KEEP="${RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$DIR/${DB}_${STAMP}.dump"
COMPOSE=(docker compose -f compose.yaml)
[ -f compose.override.yaml ] && COMPOSE+=(-f compose.override.yaml)

mkdir -p "$DIR"
chmod 700 "$DIR"

# pg_dump se ejecuta dentro del contenedor (la base de datos no está expuesta a la red).
"${COMPOSE[@]}" exec -T db pg_dump -U app_owner -d "$DB" --format=custom --compress=6 > "$FILE.tmp"

# Verificación: el archivo debe poder listarse completo.
"${COMPOSE[@]}" exec -T db pg_restore --list < "$FILE.tmp" > /dev/null
mv "$FILE.tmp" "$FILE"
chmod 600 "$FILE"
sha256sum "$FILE" > "$FILE.sha256"

echo "$(date -u +%FT%TZ) copia creada: $FILE ($(du -h "$FILE" | cut -f1))"

# Retención local. Copie además los respaldos FUERA del servidor (ver docs/06-despliegue-y-operacion.md).
find "$DIR" -name "${DB}_*.dump*" -type f -mtime "+$KEEP" -print -delete
