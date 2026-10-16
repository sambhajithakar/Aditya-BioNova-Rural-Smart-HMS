#!/usr/bin/env bash
# Provision local PostgreSQL roles/databases for Aditya BioNova Smart HMS.
# Repeatable & idempotent. Requires a running PostgreSQL reachable via
# PGHOST/PGPORT/PGUSER (defaults: 127.0.0.1:5433, superuser "hms").
#
# Creates:
#   role  hms_app  (non-owner application role; subject to row-level security)
#   db    hms_dev  (development)
#   db    hms_test (automated tests; dropped/recreated by the test suite)
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5433}"
PGUSER="${PGUSER:-hms}"
PSQL="psql -h $PGHOST -p $PGPORT -U $PGUSER -v ON_ERROR_STOP=1"

APP_ROLE_PASSWORD="${HMS_APP_PASSWORD:-change_me}"

$PSQL -d postgres <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hms_app') THEN
        CREATE ROLE hms_app LOGIN PASSWORD '${APP_ROLE_PASSWORD}';
    ELSE
        ALTER ROLE hms_app LOGIN PASSWORD '${APP_ROLE_PASSWORD}';
    END IF;
END
\$\$;
SELECT 'role hms_app ready';
SQL

for db in hms_dev hms_test; do
  exists=$($PSQL -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'")
  if [ "$exists" != "1" ]; then
    $PSQL -d postgres -c "CREATE DATABASE $db OWNER hms"
  fi
  echo "database $db ready"
done
