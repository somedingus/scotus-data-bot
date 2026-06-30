# SCOTUS corpus ETL — common tasks.
# Network stages need a token: prefix with `agentsecrets env --` (e.g. `make ingest`).

PY ?= python3
DB ?= data/processed/scotus.sqlite
DSN ?=

.PHONY: ingest clusters db test inspect serve dist pg clean help

help:
	@echo "make ingest   - full pipeline (clusters + text + load)   [needs token]"
	@echo "make clusters - reprocess cached clusters, no network     (--from-cache)"
	@echo "make db       - build the SQLite database from staging files"
	@echo "make test     - run unit + data-quality tests"
	@echo "make inspect  - print a human-readable completeness report"
	@echo "make serve    - open the database in Datasette (browser UI)"
	@echo "make dist     - gzip the DB + write SHA256SUMS (release artifact)"
	@echo "make pg DSN=postgres://... - load the same schema into Postgres"

ingest:
	agentsecrets env -- $(PY) -m src.pipeline --stage all --validate

clusters:
	$(PY) -m src.pipeline --stage clusters --from-cache --validate

db:
	$(PY) -m src.load --target sqlite --db $(DB)

test:
	$(PY) -m pytest tests/ -v

inspect:
	sqlite3 $(DB) < db/inspect.sql

serve:
	datasette $(DB)

dist:
	gzip -kf $(DB)
	cd $(dir $(DB)) && shasum -a 256 $(notdir $(DB)).gz > SHA256SUMS
	@echo "artifact: $(DB).gz  (+ SHA256SUMS)"

pg:
	$(PY) -m src.load --target postgres --dsn $(DSN)

clean:
	rm -f $(DB) $(DB).gz data/processed/*.csv
