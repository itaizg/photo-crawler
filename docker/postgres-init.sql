-- Runs once, on the first start of an empty Postgres volume.
-- `faces` (the face index) is created by the image from POSTGRES_DB.
-- `airflow`: Airflow metadata. `faces_test`: loopback integration tests.
CREATE DATABASE airflow;
CREATE DATABASE faces_test;

\connect faces
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

\connect faces_test
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
