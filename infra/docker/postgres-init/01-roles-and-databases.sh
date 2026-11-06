#!/bin/bash
# Runs once on first container start. Creates the least-privilege runtime role and a test database.
# hela_owner runs migrations; hela_app is what the API connects as (no DDL, no audit UPDATE/DELETE).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE ROLE hela_app LOGIN PASSWORD '${HELA_PG_APP_PASSWORD}';
  CREATE DATABASE hela_test OWNER hela_owner;
  GRANT CONNECT ON DATABASE hela TO hela_app;
  GRANT CONNECT ON DATABASE hela_test TO hela_app;
SQL

for db in hela hela_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<-SQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    GRANT USAGE ON SCHEMA public TO hela_app;
SQL
done
