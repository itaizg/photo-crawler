#!/usr/bin/env bash
# Stack smoke test (research R13): run the real DAG in the Compose stack on very small live data
# (depth 0, one page per site), then check the stored results. The stack must be up:
#   docker compose up -d --build --wait
set -euo pipefail
cd "$(dirname "$0")/.."

CONF='{"depth": 0, "page_cap": 1}'
echo "== running image_indexer with $CONF"
docker compose exec -T airflow-scheduler airflow dags test image_indexer --conf "$CONF" \
  > smoke-output.log 2>&1 || { tail -n 60 smoke-output.log; echo "FAIL: dag run"; exit 1; }
grep -A 30 "run summary" smoke-output.log | head -n 40 || true

sql() {
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d faces -tAc \"$1\""
}

failures=0
check() {  # check <label> <sql> <expected-test e.g. '-eq 0'>
  local value
  value=$(sql "$2" | tr -d '[:space:]')
  if [ "$value" $3 ]; then echo "PASS: $1 ($value)"; else echo "FAIL: $1 ($value)"; failures=$((failures + 1)); fi
}

check "one crawled page per site" \
  "SELECT count(*) FROM (SELECT site FROM page GROUP BY site HAVING count(*) >= 1) s" "-eq 2"
check "at least one face stored" "SELECT count(*) FROM face" "-ge 1"
check "images are JPEG and at most 1024 px" \
  "SELECT count(*) FROM image WHERE status = 'indexed' AND (greatest(width, height) > 1024 OR substring(jpeg from 1 for 3) <> '\\\\xffd8ff'::bytea)" "-eq 0"
check "every face is linked to a page" \
  "SELECT count(*) FROM face f LEFT JOIN page_image pi USING (image_id) WHERE pi.page_id IS NULL" "-eq 0"
check "every face has a 512-d embedding" \
  "SELECT count(*) FROM face WHERE vector_dims(embedding) <> 512" "-eq 0"

rm -f smoke-output.log
if [ "$failures" -gt 0 ]; then
  echo "-- failing links recorded this run (url | attempts | last error):"
  sql "SELECT url || ' | ' || attempts || ' | ' || last_error FROM fetch_attempt ORDER BY last_attempt_at DESC LIMIT 10"
  echo "SMOKE FAILED ($failures checks)"
  exit 1
fi
echo "SMOKE PASSED"
