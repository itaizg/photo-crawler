# photo-crawler
System for crawling a website. It extracts images containing people with visible faces, and indexes them for image-based search. The system allows the user to provide an image of a specific character and return a list of all pages on the site that contain an image of that same character.

## Database access and tests

`docker compose up` generates the database passwords on first start into a private `db-secrets`
volume (nothing is committed or put in `.env`). The pipeline connects as `faces_writer`, search
as `faces_reader` (SELECT only), and the Airflow containers find the passwords through
`PGPASSFILE`. `docker compose down -v` removes the volumes together.

To run the integration tests against the Compose database from the host (loopback only):

```bash
export FACE_INDEX_TEST_DSN="postgresql://faces:$(docker compose exec -T postgres cat /run/db-secrets/postgres_password)@127.0.0.1:5432/faces_test"
uv run pytest -m "not model"
```
