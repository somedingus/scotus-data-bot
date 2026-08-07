# Contributing / Developer onboarding

New developer environment setup and onboarding for **scotus-data-bot**. High-level project
info is in the [README](README.md).

## 1. Prerequisites

- **Python 3.10+**, `git`, and `sqlite3` (preinstalled on macOS).
- Optional: [`gh`](https://cli.github.com/) (releases/PRs), `datasette` (installed by `make setup`).
- **A CourtListener API token** — only needed to *re-fetch* from the API (the `extract` stage).
  You do **not** need it to work on the transforms, the loader, the database, or the tests:
  the raw mirror is downloadable without one.

## 2. Setup

```bash
git clone https://github.com/jcbrown-code/scotus-data-bot.git
cd scotus-data-bot
make setup          # creates .venv and runs `pip install -e ".[dev]"`
make test           # sanity check — unit tests should pass
```

`make setup` installs the project **editable** with dev tools (pytest, ruff, datasette) and
exposes the `scotus-pipeline` console command. The `make` targets auto-detect
`.venv/bin/python`, so you never have to `activate`.

### Getting the data

The bulk data (`data/`) is **gitignored** — it isn't in the repo. What you need depends on
what you're doing:

**To use / explore the corpus** (no token) — download the prebuilt database from the latest
[Release](https://github.com/jcbrown-code/scotus-data-bot/releases/latest):

```bash
gh release download --repo jcbrown-code/scotus-data-bot --pattern 'scotus.sqlite.gz'
mkdir -p data/processed
gunzip -c scotus.sqlite.gz > data/processed/scotus.sqlite
make inspect        # works now; also `make serve`, and sqlite3/datasette queries
```

**To rebuild from source or run the full test suite** (no token either) — fetch the raw mirror
and run the stages:

```bash
python -m src.pipeline --stage fetch-mirror    # download + checksum-verify the raw mirror
python -m src.pipeline --stage materialize     # then scope, dedup, validate, reselect, clean, load
```

> **Why the distinction:** the data-quality suites rebuild from the **staging** database, which
> is derived from the raw mirror — neither ships in the corpus Release. Without them those
> tests auto-skip; the unit tests still run and `make test` passes.

## 3. How the pipeline fits together

Strict **extract → transform → load**, one stage per invocation (never chained in one job).
Every record is retained and *labeled* — the pipeline never silently drops or mutates data
(see "Data-lineage guarantees" below).

```
CourtListener API ── extract (VERBATIM, all records, full fields) ─▶ data/raw/ mirror
                                                     (Release asset, pinned by CHECKSUMS)
  │ materialize   →  data/processed/scotus-staging.sqlite   (cluster -> opinion hierarchy;
  │                                                          no decisions; missing = NULL)
  │ scope         →  stg_cluster_scope    is_scotus + evidence   [+ dataset/scope_review.csv]
  │ dedup         →  stg_cluster_dedup    canonical | duplicate  [+ dataset/dedup_review.csv]
  │ validate      →  per-volume reconciliation vs dataset/case_name_reference.csv
  │                  (writes dataset/validate_report.csv — every volume 0 miss / 0 extra)
  │ reselect      →  stg_opinion_source   chosen source-text field per opinion
  │ clean         →  stg_opinion_clean + stg_page_break   clean_text + offset spans
  ▼
  load  →  data/processed/scotus.sqlite   all 1,120 clusters with corpus_status,
           1,160 opinion rows (674 with text), FTS5, scotus_decisions view (648)
```

| Module | Responsibility |
|--------|----------------|
| `config/settings.py` | All paths + env (token, date window, corpus span). No secrets hardcoded. |
| `src/extract.py` / `src/mirror.py` | Verbatim mirror fetch; Release packaging + checksum-verified download. |
| `src/transform/` | The staged domain logic: materialize, scope, dedup, validate, reselect, clean_opinions. Pure predicates separated from I/O; each stage writes its own `stg_` table. |
| `src/clean.py` | The shared deterministic text cleaner (star-pagination → page breaks, NFC, no OCR correction). |
| `src/load.py` | The L of the ETL: staging → the shipped database. Validates row shapes before insertion. |
| `src/pipeline.py` | Orchestrator (`--stage <name>`), the `scotus-pipeline` entry point. |
| `dataset/` | **Committed**: the human-review ledgers, the per-volume reference, the validate report. |
| `data/` | **Gitignored** bulk: raw mirror, staging DB, the built `.sqlite`. |

### Human review is part of the design

Automated rules decide the bulk; a person adjudicates the residue, and the adjudications are
**committed, auditable ledgers** that override the rules (propose → review → execute):

- `dataset/scope_review.csv` — clusters kept/dropped by human review (each row's rationale
  documents the evidence).
- `dataset/dedup_review.csv` — duplicate pairs the automated gates cannot reach (stub texts
  under the shingle floor, edition-variant page numbers, one documented erroneous scdb tag).
- `dataset/case_name_reference.csv` — the authority the corpus is reconciled against, per
  U.S. Reports volume (a volume has a fixed table of contents, so it is stable ground truth;
  year counts drift on term-vs-decision-date attribution).

**Data-lineage guarantees — don't break these:**

1. **Conserve rows.** Every mirror record reaches the shipped `clusters` table; the four
   `corpus_status` values partition it exactly (648 + 41 + 227 + 204 = 1,120 — a tested
   contract). To exclude a record, *label* it — never delete it.
2. **The reason travels with the row:** `scope_evidence` (scope), `dup_of` + `dup_method`
   (dedup, incl. `human_review` for ledger folds), `corpus_status` (the terminal disposition).
3. **Ledger over rule, loudly.** Human-review ledgers are authoritative; a malformed ledger row
   or a reference to a nonexistent cluster raises — it never silently no-ops.
4. **Assert it.** Each stage ships unit tests plus data-quality tests over the real staging DB
   or built database (auto-skipped when the data is absent). Ship any pipeline change with a
   matching assertion; the exact per-volume reconciliation is a standing invariant test.
5. **Keep `scotus_decisions` a view, never a table** — the narrowing stays reversible, and
   downstream code selects from the view instead of re-deriving scope/dedup/span logic.

> **Decisions vs. opinions.** "648 decisions" is *case-level* (`scotus_decisions`, one row per
> cluster); "674 corpus opinions" is *document-level*. The extras come from seriatim cases
> where each Justice filed a separate opinion — many opinions link to one decision via
> `cluster_id`. It is not a double-count.

## 4. Development workflow

1. **Branch off `main`** (`git checkout -b feat/...` or `fix/...`); open a PR back to `main`.
2. Before pushing, run:
   ```bash
   make format     # ruff auto-format
   make lint       # ruff checks
   make test       # unit + data-quality tests
   make cov        # coverage report
   ```
3. **CI** (`.github/workflows/ci.yml`) runs ruff lint + format-check + pytest on Python
   3.10–3.12 for every PR. Keep it green.

### Testing conventions (see `tests/`)

- Small, focused, deterministic tests; **no real network or randomness** — HTTP is mocked by
  monkeypatching (see `tests/test_extract.py`), and stage tests build small synthetic staging
  DBs in `tmp_path`.
- Use `@pytest.mark.parametrize` for input/output cases.
- Data-quality tests run against the real staging DB or the built database (the `db` fixture
  in `conftest.py` builds one via `load.build_db`); they **auto-skip** when the data isn't
  present (e.g. in CI), so a fresh checkout stays green.
- Prefer outcome tests anchored on hand-picked real cases and structural invariants over
  replaying a fixture back at itself.
- Coverage target is *reasonable*, not 100%: network HTTP loops and `__main__` glue are
  intentionally left uncovered.

## 5. Common tasks

| Task | Command |
|------|---------|
| Get the raw mirror (no token) | `python -m src.pipeline --stage fetch-mirror` |
| Rebuild staging from the mirror | `python -m src.pipeline --stage materialize` (then scope … clean) |
| Rebuild the shipped database | `python -m src.pipeline --stage load` |
| Reconcile vs the reference | `python -m src.pipeline --stage validate` |
| Re-fetch from CourtListener (token) | `agentsecrets env -- python -m src.pipeline --stage extract` |
| Inspect / confirm completeness | `make inspect` |
| Explore in a browser UI | `make serve` |
| Publish a Release | `make release VERSION=v2.0.0` *(needs `gh`)* |

### Extending the corpus

- **Different date window:** set `SCOTUS_AFTER` / `SCOTUS_BEFORE` and re-run `extract`; the
  corpus span itself is `settings.CORPUS_MIN_VOLUME`/`CORPUS_MAX_VOLUME` (which also needs a
  reference list covering the new volumes).
- **New transform / rule change:** edit the stage module under `src/transform/`, add unit
  tests, re-run the stage chain, and check `--stage validate` still reconciles exactly.
- **Schema change:** edit the DDL in `src/load.py`, update `db/README.md` + `dictionary.md`,
  and add/adjust a `tests/test_load.py` assertion.

## 6. Gotchas

- **The token is never committed.** It's injected at runtime by `agentsecrets env --`; there is
  no token in the repo, and `config.settings.get_token()` reads it from the environment.
- **`data/` and `*.sqlite` are gitignored** on purpose — regenerable/large. The committed
  `dataset/` ledgers + reference + report are the git-visible audit trail.
- **Determinism.** Same inputs → byte-identical outputs, per stage. A diff in a committed
  `dataset/` artifact means the underlying data or an adjudication actually changed.
- **Names lie; text doesn't.** Captions vary wildly across records of one decision (verbose
  party lists, archaic spellings like *M'Culloch*), Harvard-CAP dates are term placeholders,
  and some records carry another print edition's page numbers. The pipeline's identity
  decisions rest on scdb ids, text overlap, and offset-anchored evidence — keep it that way.
- **The apparatus stage is V1-era** (reads `dataset/all_clusters.csv`, predates the current
  dedup) and is pending rework; don't build new work on it.

## 7. Where to look

- Big picture & results → [README.md](README.md)
- Schema & example queries → [db/README.md](db/README.md), [dictionary.md](dictionary.md)
- Why a cluster is in or out → `scope_evidence` / `corpus_status` in the database, plus the
  ledgers under `dataset/`
- The domain logic → `src/transform/` (+ each stage's tests)
