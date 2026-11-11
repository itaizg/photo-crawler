# Contract: `image_indexer` DAG trigger parameters

These are set in the Airflow UI ("Trigger DAG w/ config") or from the CLI. Any value left unset uses the default from `src/face_index/config.py`, the single home for defaults (constitution VI, Convention over Configuration). Airflow `Param` JSON-schema validation rejects invalid values before the run starts.

| Param | JSON schema | Default | Meaning | Spec |
|---|---|---|---|---|
| `depth` | `{"type": "integer", "minimum": 0, "maximum": 10}` | `1` | Link hops from each home page; 0 = home pages only. Capped at 10 (it matches the `page.depth` check, and depth 3 already reaches most of a news site) | FR-002, FR-005 |
| `page_cap` | `{"type": ["integer", "null"], "minimum": 1}` | `50` | Maximum pages fetched successfully per site; `null` = no cap | FR-005a |
| `page_rate` | `{"type": "number", "minimum": 0.1, "maximum": 10}` | `1.0` | Page requests per second, per host | FR-003 |
| `image_rate` | `{"type": "number", "minimum": 0.1, "maximum": 20}` | `5.0` | Image requests per second, per host | FR-003 |

The upper bounds on the rates are a safety guard against typos, such as `100` meant as `1.00`. They aren't tuning knobs. The lower bound, one request every 10 seconds, keeps a tiny rate from reserving a host's shared slot far into the future, which would also slow later runs.

## DAG-level settings (not parameters)

| Setting | Value | Spec |
|---|---|---|
| `schedule` | `None` (manual trigger) | Brief: "Triggering the DAG" |
| `max_active_runs` | `1` (new triggers wait as `queued`) | FR-022 |
| `catchup` | `False` | n/a |
| Pools | `http_cnn` = 1, `http_people` = 1, `face_cpu` = `FACE_WORKERS` (default 2), created by `airflow-init` | FR-022a |
| Task `retries` | `1`, `retry_delay` 1 min; for infrastructure errors only, since item errors never fail a task | FR-023 |
| Re-running a task by hand | Clear it **with downstream** (Airflow's default), so `plan_batches` and `detect` pick up anything it stages; otherwise those rows wait for the next run | FR-022a, [research R3](../research.md#r3-dag-shape-concurrency-and-collisions-fr-022-fr-022a-fr-022b) |

## Examples

```bash
# defaults
docker compose exec airflow-scheduler airflow dags trigger image_indexer

# home pages only, slow and polite
docker compose exec airflow-scheduler airflow dags trigger image_indexer \
  --conf '{"depth": 0, "page_rate": 0.5}'

# full crawl at depth 1, no page cap
docker compose exec airflow-scheduler airflow dags trigger image_indexer \
  --conf '{"depth": 1, "page_cap": null}'
```
