#!/usr/bin/env bash
# Stack smoke test (research R13): run the real DAG in the Compose stack on very small live data
# (depth 0, one page per site), then check the stored results. The stack must be up:
#   docker compose up -d --build --wait
set -euo pipefail
cd "$(dirname "$0")/.."

sql() {
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d faces -tAc \"$1\""
}

CONF='{"depth": 0, "page_cap": 1}'
# The checks look only at pages crawled by this run and the links this run saw on them, so rows
# left by earlier runs cannot make them pass. The one exception is the orphan-face check: it
# covers the whole face table, since links are never deleted and an orphan anywhere is a bug.
# The schema exists after the first run; before that the database clock is all we need.
START=$(sql "SELECT now()::text" | tr -d '\r')
echo "== running image_indexer with $CONF (start $START)"
# A stage in which every item failed fails its task, and then the run (research R12). On live
# data that is expected for people.com, which answers the crawler with HTTP 402, so the run may
# end failed: it passes when the summary was logged and only that site's tasks (and the summary,
# which fails after logging) failed.
dag_status=0
docker compose exec -T airflow-scheduler airflow dags test image_indexer --conf "$CONF" \
  > smoke-output.log 2>&1 || dag_status=$?
if ! grep -q "run summary" smoke-output.log; then
  tail -n 60 smoke-output.log; echo "FAIL: dag run (no run summary)"; exit 1
fi
grep -A 30 "run summary" smoke-output.log | head -n 40 || true
if [ "$dag_status" -ne 0 ]; then
  unexpected=$(grep -i "fail" smoke-output.log | grep -oE "task_id=[a-z_]+" | sort -u \
    | grep -vE "task_id=(crawl_people|download_people|summarize)$" || true)
  if [ -n "$unexpected" ]; then
    tail -n 60 smoke-output.log; echo "FAIL: dag run (failed tasks: $unexpected)"; exit 1
  fi
  echo "INFO: run ended failed, only because of people.com's tasks (expected on live data)"
fi

THIS_RUN="SELECT id FROM page WHERE last_crawled_at >= '$START'"
RUN_IMAGES="SELECT image_id FROM page_image WHERE page_id IN ($THIS_RUN) AND last_seen_at >= '$START'"

failures=0
check() {  # check <label> <sql> <expected-test e.g. '-eq 0'>
  local value
  value=$(sql "$2" | tr -d '[:space:]')
  if [ "$value" $3 ]; then echo "PASS: $1 ($value)"; else echo "FAIL: $1 ($value)"; failures=$((failures + 1)); fi
}

# A site that refuses the crawler (people.com answers HTTP 402) is reported, not failed: the
# pipeline is working when it records that refusal and indexes the other site.
for site in cnn people; do
  n=$(sql "SELECT count(*) FROM page WHERE site = '$site' AND id IN ($THIS_RUN)" | tr -d '[:space:]')
  if [ "$n" -ge 1 ]; then echo "INFO: $site crawled ($n page)"; else echo "WARN: $site has no page this run (see the failing links below)"; fi
done
check "at least one site crawled this run" "SELECT count(*) FROM ($THIS_RUN) p" "-ge 1"
check "faces on this run's pages" "SELECT count(*) FROM face WHERE image_id IN ($RUN_IMAGES)" "-ge 1"
check "images are JPEG and at most 1024 px" \
  "SELECT count(*) FROM image WHERE id IN ($RUN_IMAGES) AND jpeg IS NOT NULL AND (greatest(width, height) > 1024 OR substring(jpeg from 1 for 3) <> '\\\\xffd8ff'::bytea)" "-eq 0"
check "every face is linked to a page" \
  "SELECT count(*) FROM face f LEFT JOIN page_image pi USING (image_id) WHERE pi.page_id IS NULL" "-eq 0"
check "every face on this run's pages has a 512-d embedding" \
  "SELECT count(*) FROM face WHERE image_id IN ($RUN_IMAGES) AND vector_dims(embedding) <> 512" "-eq 0"

rm -f smoke-output.log
sql "SELECT url || ' | ' || attempts || ' | ' || last_error FROM fetch_attempt WHERE last_attempt_at >= '$START' ORDER BY last_attempt_at DESC LIMIT 10" \
  | sed 's/^/failing link this run: /'
if [ "$failures" -gt 0 ]; then
  echo "SMOKE FAILED ($failures checks)"
  exit 1
fi
echo "SMOKE PASSED"
