# Data dictionary

Exact mapping from source data to the SQLite fields this project builds. Two assets:

- **Core corpus** — `scotus.sqlite`, built by [`src/load.py`](src/load.py) from the Transform
  staging database (`--stage load`).
- **Optional apparatus** — `scotus-apparatus.sqlite`, built by [`src/apparatus.py`](src/apparatus.py)
  (`--stage apparatus`); ATTACH-able, keyed on `cluster_id`. **Pending rework** — it still builds
  from a V1-era snapshot (see the note at the bottom).

Sources: the verbatim raw mirror of CourtListener REST **v4** `clusters` and `opinions` records
(one JSON per record, Release-distributed, checksum-pinned), normalized into staging by the
materialize stage; the committed human-review ledgers and reference under `dataset/`; and values
derived by the pipeline stages. SQLite is dynamically typed — the "DB type" column is the
*declared* affinity. Missing values are **NULL**, never `""`.

**Origin legend:** `direct` = copied verbatim · `mapped` = value-transformed ·
`derived` = computed from other fields (no single source) · `human` = hand-authored ledger ·
`object` = SQLite view/index over other columns.

---

## Core corpus — `scotus.sqlite`

### `clusters` (1,120 rows)

From `stg_clusters` (materialize: the raw cluster record, normalized) joined to
`stg_cluster_scope` (scope verdict) and `stg_cluster_dedup` (dedup verdict; only SCOTUS
keep-candidates have one).

| DB column | DB type | Origin | Source | Notes |
|---|---|---|---|---|
| `cluster_id` | INTEGER PK | direct | `clusters.id` | |
| `case_name` | TEXT | direct | `clusters.case_name` | |
| `case_name_full` | TEXT | direct | `clusters.case_name_full` | `""` → NULL |
| `us_cite` | TEXT | derived | `clusters.citations[]` | `"{vol} U.S. {page}"` from the first `U.S.`-reporter entry; NULL if none |
| `us_volume` | INTEGER | derived | ← `citations[]` | int-coerced volume of that entry |
| `us_page` | TEXT | derived | ← `citations[]` | page token of that entry |
| `date_filed` | TEXT | direct | `clusters.date_filed` | ISO date; Harvard-CAP records carry term placeholders (often the 15th) |
| `scdb_id` | TEXT | direct | `clusters.scdb_id` | `""` → NULL; CL's crosswalk has known holes (vols 16–18) |
| `source` | TEXT | direct | `clusters.source` | provenance code, e.g. `L`, `R`, `U`, `LRU` (`U` = Harvard CAP) |
| `citation_count` | INTEGER | direct | `clusters.citation_count` | |
| `precedential_status` | TEXT | direct | `clusters.precedential_status` | |
| `n_opinions` | INTEGER | derived | — | child-opinion count (materialize hierarchy check) |
| `is_scotus` | INTEGER | mapped | scope verdict | `'true'`/`'false'` → 1/0; CHECK-constrained |
| `scope_evidence` | TEXT | derived | scope | e.g. `scotus_only_reporter`, `scdb_id`, `human_review`, `dallas_not_in_scdb:<tells>` |
| `dedup_role` | TEXT | derived | dedup | `canonical`/`duplicate`; NULL for non-SCOTUS rows (dedup never ran on them) |
| `dup_of` | INTEGER | derived | dedup | → the canonical `clusters.cluster_id`; NULL unless duplicate |
| `dup_method` | TEXT | derived | dedup | signal that attached the duplicate: `name`, `text`, `off_page`, `grouped`, `human_review` |
| `corpus_status` | TEXT | derived | load | terminal disposition: `included` (648) / `outside_volume` (41) / `duplicate` (227) / `not_scotus` (204); composed by `load.derive_corpus_status`, CHECK-constrained, conservation-tested |

### `citations` (3,596 rows)

From `stg_clusters.citations_json` (the record's citations array, ingestion timestamps stripped
at materialize).

| DB column | DB type | Origin | Source | Notes |
|---|---|---|---|---|
| `cluster_id` | INTEGER | direct | `clusters.id` | → `clusters.cluster_id` |
| `reporter` | TEXT | direct | `citations[].reporter` | e.g. `U.S.`, `Dall.`, `Cranch`, `Wheat.` |
| `volume` | TEXT | mapped | `citations[].volume` | `str()` applied |
| `page` | TEXT | mapped | `citations[].page` | `str()` applied |
| `type` | INTEGER | direct | `citations[].type` | CourtListener citation-type enum |

Primary key `(cluster_id, reporter, volume, page)`; exact-duplicate tuples are collapsed and the
dropped count recorded in `meta.n_citation_dupes_dropped`.

### `opinions` (1,160 rows)

From `stg_opinions` (every opinion of every cluster — the full 1:many hierarchy) joined to
`stg_opinion_source` (reselect) and `stg_opinion_clean` (clean); the derived columns are NULL
outside the 674 corpus opinions.

| DB column | DB type | Origin | Source | Notes |
|---|---|---|---|---|
| `opinion_id` | INTEGER PK | direct | `opinions.id` | |
| `cluster_id` | INTEGER | direct | parent cluster | → `clusters.cluster_id`; NOT NULL |
| `type` | TEXT | direct | `opinions.type` | e.g. `010combined`, `020lead`, `030concurrence`, `040dissent` |
| `author` | TEXT | direct | `opinions.author_str` | `""` → NULL |
| `is_ocr_extracted` | INTEGER | mapped | `opinions.extracted_by_ocr` | boolean → 0/1 |
| `ordering_key` | INTEGER | direct | `opinions.ordering_key` | |
| `chosen_source` | TEXT | derived | reselect | which retained source field the text derives from, by fidelity priority `source_html_lawbox` → `source_xml_harvard` → `source_html` → `source_html_with_citations` |
| `clean_text` | TEXT | derived | clean | deterministic render of the chosen source via `src/clean.py`; no OCR handling — the source's errors are rendered as-is |
| `clean_version` | INTEGER | derived | clean | algorithm version of the cleaner; a bump plus re-running reselect → clean → load regenerates `clean_text` and all offsets (the constant alone regenerates nothing) |

### `page_breaks` (3,985 rows)

Reporter page boundaries within `clean_text`, derived by `clean.clean_opinion`. Boundary
records, not spans (a start offset, no end); `char_offset` is a zero-based Unicode code-point
index; multiple boundaries may share one offset when no rendered text falls between their
markers (35 such groups, measured); document order is `(char_offset, ordinal)`. Lookup recipe
and coordinate caveats (`instr()` is one-based): `db/README.md`.

| DB column | DB type | Origin | Notes |
|---|---|---|---|
| `opinion_id` | INTEGER | direct | → `opinions.opinion_id`; part of PK |
| `ordinal` | INTEGER | derived | 1-based sequence within the opinion; part of PK |
| `page_label` | TEXT | direct | the reporter page number from the star-pagination marker |
| `char_offset` | INTEGER | derived | index into `clean_text` where that page begins (valid for the row's `clean_version`) |
| `anchor` | TEXT | derived | first ~6 words after the break — human/cross-version relocation aid |

### No OCR metadata

The database carries no claim about which spans are OCR-corrupt — no `ocr_suspects` table and no
`is_ocr_dirty` column. An earlier release shipped both, produced by hardcoded token lists inside
the cleaner and the reselect stage. A bare token flag can only assert that a *word* is sometimes
misread, never that a given *occurrence* is wrong, and the published table bore that out:
86.5% of its rows were the tokens "defendant", "defendants", or "bad" (measured); the first two
cannot arise from the modeled long-s confusion at all, and sampled contexts of the rest showed
correct usage (sample sizes and method: docs/clean-text-design.md §4).

Detection now belongs to the OCR application, which owns detection and evaluation together and
publishes occurrence records only when each carries its own evidence, confidence, offsets, and
versioned provenance. No primary source text is discarded in the interim: the raw mirror and every retained source
field remain available, so detection can be recomputed from primary data at any time.
`opinions.is_ocr_extracted` is unaffected — that is CourtListener's own provenance field
recording how *they* produced the text, not a judgment about its quality.

### `meta`, views, FTS

| Object | Kind | Definition |
|---|---|---|
| `meta` | table | `(key, value)`: `pipeline_version`, `built_at`, `git_commit`, the staging lineage (`staging_etl_job_id`, …), and all `n_*` counts — the four `n_clusters_*` partition counts are tested to sum to `n_clusters_total` |
| `scotus_decisions` | view | `SELECT * FROM clusters WHERE corpus_status = 'included'` — the 648-decision corpus; the handoff contract for downstream analysis |
| `duplicate_clusters` | view | each duplicate joined to its canonical's `case_name`/`us_cite` |
| `opinions_fts` | FTS5 index | over `opinions.clean_text` (`content='opinions'`, corpus rows only; diacritic-folded tokenizer) |

### Human-authored inputs (committed under `dataset/`)

| File | Role |
|---|---|
| `scope_review.csv` | scope's review ledger: cluster dispositions (keep/drop) a person adjudicated, authoritative over the automated rule; each row carries its rationale |
| `dedup_review.csv` | dedup's review ledger: same-decision pairs the automated gates cannot reach, with per-row evidence |
| `case_name_reference.csv` | the authoritative per-volume case list the validate stage reconciles against (key on `rep_vol`; the `us_vol` column is a different, unused numbering) |
| `validate_report.csv` | the committed per-volume reconciliation report (all volumes 0 missing / 0 extra) |

---

## Optional apparatus — `scotus-apparatus.sqlite` (legacy; not a lineage-compatible or complete V2 companion asset)

Carries the reporters' front matter (syllabus, summary, headmatter, arguments of counsel) at the
cluster level, stored raw. **The current asset was built at git commit `503e3f1` against the
1,076-cluster V1 corpus (its own `meta`), from the V1-era snapshot `dataset/all_clusters.csv`,
with duplicate resolution that predates the current dedup.** Some ids may still join
technically; correctness and coverage against the V2 `clusters` table are not guaranteed. The
rework contract — rebuild from V2 staging plus the checksum-pinned raw mirror, with recorded
lineage and per-duplicate provenance — is in docs/clean-text-design.md §5.
Tables: `cluster_text` (one row per cluster × apparatus kind), `cluster_meta`
(`case_name_full`, `attorneys`, `judges`), `meta` (build pin). Committed coverage snapshot:
`dataset/apparatus_manifest.csv`.
