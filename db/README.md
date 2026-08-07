# Database

A single SQLite file (`data/processed/scotus.sqlite`) built by the load stage
(`python -m src.pipeline --stage load`, `src/load.py`) from the Transform staging database.
FTS5 full-text search over the corpus opinion text. SQLite-only.

## Build & inspect

```bash
python -m src.pipeline --stage load       # rebuild from data/processed/scotus-staging.sqlite
make inspect                              # human-readable completeness report
datasette data/processed/scotus.sqlite    # browse/query/visualize in the browser
sqlite3 data/processed/scotus.sqlite      # ad-hoc SQL
```

## Schema

| Table / view | Rows | Notes |
|---|---|---|
| `clusters` | 1,120 | every cluster, with the stage verdicts (`is_scotus` + `scope_evidence`, `dedup_role` + `dup_of` + `dup_method`) and the terminal `corpus_status` |
| `citations` | 3,596 | structured parallel cites (`reporter, volume, page, type`) |
| `opinions` | 1,160 | every opinion row (`type`, `author`, `ordering_key`); the 674 corpus opinions also carry `chosen_source`, `is_ocr_dirty`, `clean_text`, `clean_version` |
| `page_breaks` | 3,985 | reporter page boundaries within `clean_text`: `ordinal, page_label, char_offset, anchor` |
| `ocr_suspects` | 2,813 | OCR-suspect spots as offset spans into `clean_text`: `ordinal, char_offset, token` — input to the future OCR-correction stage |
| `meta` | — | build provenance (version, timestamp, git commit, staging lineage) + all counts |
| `scotus_decisions` (view) | **648** | the corpus: `corpus_status = 'included'` |
| `duplicate_clusters` (view) | 227 | each duplicate joined to its canonical's name and cite |
| `opinions_fts` | — | FTS5 over `opinions.clean_text` (diacritic-folded tokenizer for recall) |

### `corpus_status` — the terminal disposition

Every cluster carries exactly one of four values, derived from the stage verdicts and
enforced by DDL CHECKs; the counts sum to the full population (a tested contract):

| `corpus_status` | Count | Meaning |
|---|---:|---|
| `included` | 648 | canonical SCOTUS decision in U.S. Reports vols 2–18 — the corpus |
| `outside_volume` | 41 | canonical SCOTUS decision outside the corpus span (the vol-19 staging buffer) |
| `duplicate` | 227 | a second record of a decision represented by its canonical cluster |
| `not_scotus` | 204 | not a U.S. Supreme Court decision (Dallas-era state/circuit cases) |

Downstream analysis should select from `scotus_decisions` (or filter
`corpus_status = 'included'`) and never re-derive scope, dedup, or volume logic.

### Cleaned text and offset spans

`clean_text` is a deterministic render of each corpus opinion's **chosen source field**
(`chosen_source`; picked by the reselect stage for fidelity) through `src/clean.py`:
star-pagination markers are removed (captured in `page_breaks` instead), whitespace/Unicode is
normalized (NFC), and content — footnote bodies, captions, citations — is preserved. **No OCR
is corrected**: suspect spots are located, not fixed, in `ocr_suspects` (`char_offset` indexes
into `clean_text`). `clean_version` tracks the cleaning logic. Raw source text is not shipped —
the verbatim raw mirror (a Release asset pinned by committed checksums) is the audit trail,
and the offset spans plus `chosen_source`/`clean_version` pin the derivation.

```sql
-- reconstruct which reporter page a search hit falls on
SELECT o.opinion_id, max(pb.page_label) AS page
FROM opinions o JOIN page_breaks pb ON pb.opinion_id = o.opinion_id
WHERE pb.char_offset <= instr(o.clean_text, 'commerce among the') GROUP BY o.opinion_id;
```

## Reporter apparatus (optional separate asset, pending rework)

The early reporters printed substantial front matter that is not part of any opinion — the
reporter's syllabus, procedural summary, and arguments of counsel. It lives in a separate,
optional database (`data/processed/scotus-apparatus.sqlite`, `--stage apparatus`) that
`ATTACH`es and joins on `cluster_id`. **Caveat:** the apparatus stage still builds from a
V1-era snapshot (`dataset/all_clusters.csv`) and its duplicate resolution predates the current
dedup; it is scheduled for a rework onto the V2 staging before the numbers in it can be
trusted against this database.

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

-- read a decision's text (note the archaic caption spelling M'Culloch)
SELECT o.clean_text FROM opinions o JOIN clusters c USING (cluster_id)
WHERE c.case_name LIKE '%ulloch%' AND o.clean_text IS NOT NULL;

-- trace a duplicate record to its canonical decision
SELECT cluster_id, case_name, canonical_case_name, canonical_us_cite
FROM duplicate_clusters LIMIT 10;

-- OCR-suspect spots for one opinion, with surrounding context
SELECT s.char_offset, s.token,
       substr(o.clean_text, max(1, s.char_offset - 30), 70) AS context
FROM ocr_suspects s JOIN opinions o USING (opinion_id)
WHERE o.opinion_id = 84800 ORDER BY s.ordinal;
```
