#!/bin/bash
# Runs once, on the first start of an empty Postgres volume (docker-entrypoint-initdb.d).
# `faces` (the face index) is created by the image from POSTGRES_DB.
# `airflow`: Airflow metadata. `faces_test`: loopback integration tests.
# Roles (constitution XIV): faces_writer for the pipeline, faces_reader (SELECT only) for search.
# Passwords come from the db-secrets volume written by the `db-secrets` service; never printed.
set -euo pipefail

SECRETS=/run/db-secrets
writer_pw=$(<"$SECRETS/writer_password")
reader_pw=$(<"$SECRETS/reader_password")

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
     -v writer_pw="$writer_pw" -v reader_pw="$reader_pw" <<'SQL'
CREATE DATABASE airflow;
CREATE DATABASE faces_test;
CREATE ROLE faces_writer LOGIN PASSWORD :'writer_pw';
CREATE ROLE faces_reader LOGIN PASSWORD :'reader_pw';
SQL

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d faces <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
REVOKE ALL ON DATABASE faces FROM PUBLIC;
GRANT CONNECT ON DATABASE faces TO faces_writer, faces_reader;
-- The pipeline applies the schema itself (init_schema), so the writer may create objects.
GRANT USAGE, CREATE ON SCHEMA public TO faces_writer;
GRANT USAGE ON SCHEMA public TO faces_reader;
-- Tables the writer creates are readable, and only readable, by the search role.
ALTER DEFAULT PRIVILEGES FOR ROLE faces_writer IN SCHEMA public GRANT SELECT ON TABLES TO faces_reader;
SQL
