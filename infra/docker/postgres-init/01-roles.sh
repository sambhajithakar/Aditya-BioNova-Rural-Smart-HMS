#!/usr/bin/env bash
# Runs once on first container start (docker-entrypoint-initdb.d).
# Creates the non-owner application role (subject to row-level security)
# and the test database. Passwords come from the environment — never committed.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hms_app') THEN
        CREATE ROLE hms_app LOGIN PASSWORD '${HMS_APP_PASSWORD:-change_me}';
    END IF;
END
\$\$;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
SELECT 'CREATE DATABASE hms_test OWNER ${POSTGRES_USER}'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'hms_test')\gexec
SQL

echo "hms_app role and hms_test database ready"
