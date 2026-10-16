#!/bin/bash
# Se ejecuta UNA sola vez, cuando el volumen de PostgreSQL está vacío.
# Crea dos roles sin privilegios de superusuario:
#   app_owner: dueño del esquema; lo usan solamente las migraciones (Alembic).
#   app_user:  conexiones ordinarias de la aplicación; solo DML, sin DDL.
set -euo pipefail

: "${APP_OWNER_PASSWORD:?Falta APP_OWNER_PASSWORD}"
: "${APP_USER_PASSWORD:?Falta APP_USER_PASSWORD}"
: "${APP_DB_NAME:?Falta APP_DB_NAME}"

create_db() {
  local db="$1"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -c "CREATE DATABASE \"$db\" OWNER app_owner ENCODING 'UTF8' TEMPLATE template0;"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<-SQL
    REVOKE ALL ON DATABASE "$db" FROM PUBLIC;
    GRANT CONNECT ON DATABASE "$db" TO app_user;
    ALTER SCHEMA public OWNER TO app_owner;
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    GRANT USAGE ON SCHEMA public TO app_user;
    ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
    ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
      GRANT USAGE, SELECT ON SEQUENCES TO app_user;
SQL
}

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
  CREATE ROLE app_owner LOGIN PASSWORD '${APP_OWNER_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  CREATE ROLE app_user  LOGIN PASSWORD '${APP_USER_PASSWORD}'  NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ALTER ROLE app_owner SET timezone TO 'UTC';
  ALTER ROLE app_user  SET timezone TO 'UTC';
SQL

create_db "$APP_DB_NAME"

# Base de datos aislada para pruebas automatizadas (solo en desarrollo).
if [ -n "${APP_TEST_DB_NAME:-}" ]; then
  create_db "$APP_TEST_DB_NAME"
fi
