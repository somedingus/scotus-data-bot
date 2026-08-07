# SCOTUS corpus, U.S. Reports vols 2–18 (1791–1820)

A clean, de-duplicated, full-text database of **U.S. Supreme Court decisions** built from the
[CourtListener](https://www.courtlistener.com/) API by this project's pipeline, and reconciled
case-for-case against an authoritative per-volume reference.

## Contents

| | |
|---|---|
| Distinct SCOTUS decisions | **648** (`scotus_decisions` view) |
| Opinion texts | 674 (seriatim cases have several per decision) |
| All clusters, each with a terminal `corpus_status` | 1,120 (648 included + 41 outside_volume + 227 duplicate + 204 not_scotus) |
| Structured citations | 3,596 |
| Page-break offset spans | 3,985 |
| OCR-suspect offset spans (located, not corrected) | 2,813 |
| Full text | ~8.1M characters of deterministic `clean_text` |

**Decisions vs. opinions:** 648 is case-level (one row per decision in `scotus_decisions`);
674 is document-level — seriatim cases (each Justice writing separately) link several opinions
to one decision via `cluster_id`.

**Validation:** the corpus reconciles exactly against the committed per-volume reference
(`dataset/case_name_reference.csv`): 648 kept = 648 referenced = 648 matched, every volume
0 missing / 0 extra. Human adjudications are committed, auditable ledgers
(`dataset/scope_review.csv`, `dataset/dedup_review.csv`) with per-row rationales.

## Asset

- `scotus.sqlite.gz` — gzipped SQLite database (~5.6 MB compressed, ~13 MB unpacked) with FTS5
  full-text search over opinion text.
- `SHA256SUMS` — checksum for verification.

## Use

```bash
gunzip scotus.sqlite.gz
sqlite3 scotus.sqlite "SELECT count(*) FROM scotus_decisions;"   # -> 648
# or explore in the browser:
datasette scotus.sqlite
```

Tables: `clusters`, `citations`, `opinions`, `page_breaks`, `ocr_suspects`, `meta`, and the
views `scotus_decisions` and `duplicate_clusters`. See `db/README.md` and `dictionary.md` for
the schema and example queries.

## Provenance & license

Regenerable from source: the verbatim raw mirror is a Release asset pinned by committed
checksums, and the `meta` table records the exact build (pipeline version, timestamp, git
commit, staging lineage). Project code is MIT-licensed; the court opinions themselves are
public-domain U.S. government works.
