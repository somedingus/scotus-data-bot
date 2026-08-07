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
| `opinions` | 1,160 | every opinion row (`type`, `author`, `ordering_key`); the 674 corpus opinions also carry `chosen_source`, `clean_text`, `clean_version` |
| `page_breaks` | 3,985 | reporter page boundaries within `clean_text`: `ordinal, page_label, char_offset, anchor`. Boundary records, not spans; `char_offset` is a zero-based code-point index; multiple labels may share one offset (35 groups) — see the lookup recipe below |
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
(`chosen_source`; a corpus-specific fixed priority applied by the reselect stage — see its
docstring for the order and the measurements behind it) through `src/clean.py`:
star-pagination markers are removed (captured in `page_breaks` instead), whitespace/Unicode is
normalized (NFC), and content — footnote bodies, captions, citations — is preserved. The
rendering is conservative in both directions: structurally tagged front matter inside a chosen
source reaches `clean_text` too (measured census: `<headnotes>` in 11 opinions, `<judges>` in 6,
`<attorneys>` in 1 — see docs/clean-text-design.md §5), and whitespace collapse flattens `<pre>`
column alignment (2 opinions).
`clean_version` tracks the cleaning logic. Raw source text is not shipped — the verbatim raw
mirror (a Release asset pinned by committed checksums) is the audit trail, and the page-break
offset spans plus `chosen_source`/`clean_version` pin the derivation.

**No OCR handling.** The text is rendered as the source has it, errors included; the `■`
unreadable-character glyph is preserved verbatim because it marks missing text in the source.
The database asserts nothing about which spans are OCR-corrupt. A previous release shipped an
`ocr_suspects` table built from a token list, but a bare token flag is a claim about a *word*,
not about an *occurrence* — 86% of its rows were ordinary correct words such as "defendant".
Detection now belongs to the OCR application, which owns detection and evaluation together and
will publish occurrence records only when each carries its own evidence and provenance.

Page lookup: offsets are zero-based code points, SQLite `instr()` is one-based (subtract 1;
`instr() = 0` means the phrase is absent), and among boundaries at or before the hit the page
is the greatest `(char_offset, ordinal)`:

```sql
-- which reporter page a phrase falls on, per opinion
WITH hit AS (
  SELECT opinion_id, instr(clean_text, 'commerce among the') - 1 AS pos
  FROM opinions
  WHERE clean_text IS NOT NULL AND instr(clean_text, 'commerce among the') > 0
)
SELECT hit.opinion_id,
       (SELECT pb.page_label FROM page_breaks pb
        WHERE pb.opinion_id = hit.opinion_id AND pb.char_offset <= hit.pos
        ORDER BY pb.char_offset DESC, pb.ordinal DESC LIMIT 1) AS page
FROM hit;
```

## Reporter apparatus (optional separate asset, pending rework)

The early reporters printed substantial front matter that is not part of any opinion — the
reporter's syllabus, procedural summary, and arguments of counsel. It lives in a separate,
optional database (`data/processed/scotus-apparatus.sqlite`, `--stage apparatus`) that
`ATTACH`es and joins on `cluster_id`. **Legacy — not a lineage-compatible or complete V2
companion asset:** its `meta` records git commit `503e3f1` and a 1,076-cluster corpus (this
database has 1,120), and its duplicate resolution predates the current dedup. Some ids may
still join technically; correctness and coverage are not guaranteed. The rework contract
(rebuild from V2 staging + the checksum-pinned raw mirror) is in docs/clean-text-design.md §5.

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

-- which source field a decision's text was rendered from
SELECT c.case_name, o.chosen_source, o.clean_version, length(o.clean_text) AS chars
FROM opinions o JOIN scotus_decisions c USING (cluster_id)
WHERE o.clean_text IS NOT NULL ORDER BY chars DESC LIMIT 10;
```
