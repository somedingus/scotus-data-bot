# Database

A single SQLite file (`data/processed/scotus.sqlite`) built by `src/load.py` from the
staging files. FTS5 full-text search over opinion text. The same schema loads into
Postgres via `--target postgres --dsn …` (tsvector + GIN instead of FTS5).

## Build & inspect

```bash
python -m src.load --target sqlite --db data/processed/scotus.sqlite   # or: make db
make inspect                              # human-readable completeness report
datasette data/processed/scotus.sqlite    # browse/query/visualize in the browser
sqlite3 data/processed/scotus.sqlite      # ad-hoc SQL
```

## Schema

| Table / view | Rows | Notes |
|---|---|---|
| `clusters` | 1,076 | every cluster, with `bucket` (KEEP/REVIEW), `dedup_role`, `dup_of` |
| `citations` | many per cluster | structured parallel cites (`reporter, volume, page, type`) |
| `opinions` | ~690 | per opinion: `raw_html` + `plain_text`, `type`, `author`, `char_count` |
| `review_dispositions` | 205 | human adjudication of the non-SCOTUS REVIEW bucket |
| `meta` | — | build provenance (version, timestamp, date range, counts, git commit) |
| `scotus_decisions` (view) | **663** | canonical decisions: `bucket='KEEP' AND dedup_role='canonical'` |
| `opinions_fts` | — | FTS5 index over `opinions.plain_text` |

`clusters.dup_of` and `opinions.cluster_id`/`citations.cluster_id` reference `clusters.cluster_id`.

## Example queries

```sql
-- every decision, oldest first
SELECT date_filed, case_name, us_cite FROM scotus_decisions ORDER BY date_filed;

-- full-text search (FTS5)
SELECT c.case_name, c.us_cite
FROM opinions_fts f
JOIN opinions o ON o.opinion_id = f.rowid
JOIN clusters c ON c.cluster_id = o.cluster_id
WHERE opinions_fts MATCH 'commerce clause';

-- read an opinion's text
SELECT plain_text FROM opinions o JOIN clusters c USING (cluster_id)
WHERE c.case_name LIKE 'McCulloch%';

-- trace a dropped duplicate to its canonical record
SELECT d.cluster_id, d.case_name, k.case_name AS canonical
FROM clusters d JOIN clusters k ON k.cluster_id = d.dup_of
WHERE d.dedup_role = 'duplicate' LIMIT 10;
```
