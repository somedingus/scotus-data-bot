# Design note: opinion text cleaning + reporter apparatus

Scope: the deterministic `clean_text` layer over the opinion corpus (`src/clean.py`, run by the
Transform `clean` stage), and the separately-captured reporter apparatus. This note is the
design record; the enforced sources remain the code, `CONTRIBUTING.md`, `db/README.md`, and
`dictionary.md`.

## 1. Goals & constraints

The corpus is a foundational dataset for search / RAG, model training, and legal extraction.
Cleaning must therefore be:

- **Deterministic** — no LLM/statistical passes; same input → same output.
- **High-fidelity & conservative** — preserve signal, avoid compounding errors.
- **Non-destructive** — the raw source fields stay untouched in the staging DB and the verbatim
  raw mirror; cleaning only derives new columns/tables. Consistent with the project's
  data-lineage guarantees.

## 2. How cleaning fits the pipeline

The reselect stage applies a corpus-specific **fixed priority** — the first non-empty field wins
(`html_lawbox` → `xml_harvard` → `html` → `html_with_citations`) — established by a prior fidelity
review of this corpus; it does not evaluate fidelity per opinion. This is a deliberate departure
from CourtListener's general recommendation of `html_with_citations`, supported by a
measurement: within the 674-opinion corpus, 367 opinions carry both lawbox and resource.org
html, and among those the html is median 2.06× the lawbox length (≥2× in 195/367) — consistent
with, but not independently establishing, the prior fidelity review's classification of the
extra length as bundled reporter apparatus rather than opinion text. Current selection (674
chosen sources): lawbox 429 / harvard 122 / html 123 / with_citations 0.

The clean stage renders the chosen field through `clean.clean_opinion`, producing per opinion:
`clean_text` and `page_breaks` (reporter pagination as character-offset boundaries). The load
stage ships those — no raw source text; full derivation pinning requires the raw-mirror checksum
plus the pipeline code version plus `chosen_source` plus `clean_version` together (no one of
them alone identifies the build).

## 3. The cleaner (what it does and why)

- **Structure-aware stdlib `HTMLParser`** handles both source dialects — HTML-flavored records
  and Harvard XML (`<opinion><author>…`) — with no dependency (stdlib-only runtime rule).
- **Three page-marker forms are captured** as page breaks:
  (1) `<span class="star-pagination" label>`, (2) `<page-number label>` — both in open and
  self-closing form (self-closing support added in cleaner v2; zero self-closing markers were
  found in the 674 chosen source values measured) — and (3) a bracketed inline
  text form (`[*626` / `*625]`). **Bare unbracketed `*54` is deliberately KEPT** in the
  text — it is ambiguous (footnote asterisk vs. content) and, in the residue, usually
  OCR-garbled pagination, so parsing it would be lossy guessing.
- **The `page_breaks` coordinate contract.** `char_offset` is a **zero-based Unicode
  code-point index** into `clean_text`. Rows are boundary records, not spans: each marks where
  a reporter page begins, with no end offset. Multiple boundaries may legitimately share one
  offset when no rendered text falls between their markers (35 such `(opinion_id, char_offset)`
  groups in the current artifact, measured). Document order is `(char_offset, ordinal)`. Page
  lookup: among boundaries with `char_offset <= hit_offset`, order by `char_offset DESC,
  ordinal DESC` and take the first. SQLite's `instr()` is one-based — subtract 1, and exclude
  `instr() = 0` (phrase absent) rather than letting it alias to offset −1. The lookup recipe
  and a non-BMP code-point check are pinned by tests (`tests/test_load.py`,
  `test_page_lookup_contract`).
- **Known presentational loss: `<pre>` alignment.** Whitespace collapse flattens preformatted
  blocks. Two chosen sources contain `<pre>` (measured): opinion 85285 (a land-title table) and
  2093658 (an aligned damages/interest computation). Words and numbers survive; column
  relationships do not. Preserving them would change bytes and offsets, so it is deferred
  behind the prototype gate (before/after on those two opinions, user-reviewed).
- **Footnote bodies are KEPT.** `<div class="footnote">` bodies are original casebody content
  (one opinion carries a 14K-char footnote reproducing the Circuit judges' reasoning in
  *Hayburn's Case*), not annotations. Inline footnote ref markers (`†`/`*`/digit superscripts)
  are kept verbatim.
- **Normalization:** `\r`→`\n`, strip control chars (keep `\t`/`\n`), collapse whitespace,
  **NFC** — no ASCII folding (that lives in the FTS tokenizer:
  `unicode61 remove_diacritics 2`; the stored column stays strict NFC).
- **No OCR handling at all.** The source's errors are rendered as the source has them. The `■`
  unreadable-char glyph stays visible in `clean_text` because it marks missing text — a
  rendering decision, not a judgment. See §4.
- `clean_version` stamps every derivation; bumping it regenerates `clean_text` and all offsets
  together, so spans can never silently drift against the text they index.

## 4. Why OCR detection is not in the cleaner

The cleaner once carried a curated token list and emitted "OCR-suspect" spots, published as an
`ocr_suspects` table; the reselect stage independently carried a second list for an opinion-level
`is_ocr_dirty` flag. Both have been withdrawn.

**What went wrong.** The lists asserted *lexical* suspicion — "this word is sometimes an OCR
error" — but each published row read as an unqualified claim about a specific occurrence, and the
schema had no column to weaken it. The evidence, by strand:

- Measured: 2,433 of 2,813 published rows (86.5%) were `defendant` (1,623), `defendants` (746),
  or `bad` (64) — `SELECT lower(token), count(*) FROM ocr_suspects GROUP BY 1`.
- By construction: `defendant`/`defendants` cannot result from the modeled `f → s` confusion,
  which maps them to "desendant"/"desendants" — not words. (This rules out the modeled error
  class, not every conceivable corruption.)
- Sampled: `bad` was inspected in 4 contexts and `defendant` in 4 (first rows by rowid,
  non-random); each showed ordinary correct usage ("the rejoinder is bad"). `fame`/`bis` rest on
  their known legitimate uses in this corpus (the brig *Fame*; Latin *bis* in citations), not on
  a separate sample.
- Counterfactual arithmetic, not adjudication: removing the five entries would clear 380 of the
  441 flagged opinions.

The entries had been gathered by eyeballing "contains an `f`" rather than by deriving a token
from a correction. With no module owning the concept, the two lists also drifted apart and each
acquired its own version of the same defect.

**The structural problem.** OCR correction was deliberately deferred, but detection stayed behind
as a passenger in two stages that neither used nor owned it: `_find_ocr_suspects` ran on the
*finished* `clean_text`, and `is_ocr_dirty` influenced no source choice. Neither had a consumer
inside the pipeline. A list embedded in a host stage can only ever make a claim about a word;
distinguishing a real error from correct usage requires evidence about the occurrence —
parallel-source alignment, corpus context, human review — which is the OCR application's job.

**The rule going forward.** Evidentiary suspicion, not lexical: a damage assertion is published
only when the occurrence carries its own reason, evidence, confidence, offsets, and versioned
provenance. An abstention belongs to a detector assessment record — it is never itself a damage
claim. Until the OCR application can produce such records, the artifact asserts nothing about
OCR damage. No primary source text is discarded — the raw mirror and every retained source field
remain available, so detection can be recomputed from primary data whenever the evidence layer
exists.

## 5. Reporter apparatus and front matter

Two distinct concepts, previously conflated in this note:

**Cluster-level apparatus fields are captured separately** — raw, in a standalone
`scotus-apparatus.sqlite` (`--stage apparatus`), keyed on `cluster_id`, similar in shape to
CAP's `head_matter` vs `opinions[]` split.

**The selected opinion transcription is conservatively rendered and may itself contain
structurally tagged front matter.** The cleaner keeps all element content, so tagged material
inside a chosen source reaches `clean_text`. Census over the 674 chosen sources (measured;
categories can overlap per opinion):

| Element | Opinions | Classification |
|---|---:|---|
| `<headnotes>` | 11 | reporter apparatus, folded into `clean_text` |
| `<attorneys>` | 1 | reporter apparatus (counsel listing) |
| `<judges>` | 6 | case metadata |
| `<syllabus>` | 0 | — |
| `<opinion>` / `<author>` | 122 / 118 | retained structure (the seriatim segmentation signal) |
| `<footnote>` / `<footnotemark>` | 17 / 12 | original casebody content, kept by ruling |
| `<page-number>` | 27 | captured as page breaks |

Example: opinion 85221's headnote content is embedded mid-text at `clean_text` offset 866 (the
page-164 boundary), and
is itself OCR-garbled ("The stopping *nd delay at permitted3…"). If pure judicial-opinion text
becomes a goal, structural exclusion is prototype-gated: two or three real cases (85221, 85203,
one clean control), user-reviewed, before any corpus change — it would alter bytes and offsets.

**Legacy asset quarantine.** The current `scotus-apparatus.sqlite` is **not a
lineage-compatible or complete V2 companion asset**: its `meta` records git commit `503e3f1`
and a 1,076-cluster corpus (V2 has 1,120), and its duplicate resolution predates the current
dedup. Some ids may still join technically; correctness and coverage are not guaranteed.
Rebuild contract when the rework happens: derive the corpus and canonical mappings from V2
staging; read apparatus from the same checksum-pinned raw mirror V2 uses (the full-record
mirror already carries the apparatus fields — no live API pull, no V1 CSV); record the
raw-mirror checksum and staging lineage; preserve every duplicate-cluster apparatus variant
with source provenance unless an explicit reconciliation rule chooses one.

Field semantics (CourtListener model `help_text` — `cl/search/models.py`, `OpinionCluster`;
the field definitions are quoted accurately, but what follows them is inference):

- **`headmatter`** — "the content before an opinion in the Harvard CaseLaw import. This
  consists of summaries, headnotes, attorneys etc for the opinion." → the **raw composite**
  pre-opinion blob.
- **`summary`** — "A summary of what happened in the case. Appears at the beginning of the case
  just after the title of the case and court information." → a **parsed component**.
- **`syllabus`** — "A summary of the issues presented in the case and the outcome."
- **`headnotes`** — "summary descriptions of the legal issues… just after the summary and
  disposition."
- **`arguments`** — "The attorney(s) and legal arguments presented as HTML text. This is
  primarily seen in older opinions…" (hence richest in this era).

These fields are semantically overlapping representations of pre-opinion matter. The reading
that `headmatter` is the raw container and the others are components carved from it is a
**hypothesis** — the model definitions do not guarantee common source provenance, containment,
or synchronization — and their exact derivation and correspondence must be reconciled
empirically. Storing all fields keeps every representation available for that reconciliation.

## 6. Prior art

Similarities with other efforts over the same data are noted below as **consistent with**, not
endorsements: none of these sources reviewed this design, and each also does things this
project deliberately does not.

| This design's choice | Source | Relationship |
|---|---|---|
| Capture star-pagination from structured labels, not display text | Eventual / Common Pile | consistent with — they report bugs "handling these star paginations" and a corrected re-fetch |
| Structure-aware HTML parsing targeting specific classes/tags | Eventual / Common Pile | consistent with — they use Selectolax; this project uses stdlib `html.parser` |
| Keep pre-opinion matter apart from opinion bodies | CAP's `casebody` schema (`head_matter` vs `opinions[]`) | consistent with, at the cluster level; see §5 for what remains inside chosen sources |
| Unicode + whitespace normalization | Eventual / Common Pile | consistent with |
| Word lists over-flag common words (the §4 critique) | Pile of Law | consistent with — their filtering analysis, which concerns content filtering rather than OCR policy |

Known divergences, stated rather than argued away: Eventual also applies regex
removals/replacements and MinHash near-deduplication, neither of which this project does (the
corpus is record-deduplicated upstream with human-reviewed ledgers and a per-volume
reconciliation; text-level near-dedup is an explicit non-goal at 674 opinions). CourtListener's
documentation generally recommends `html_with_citations`; this project's fixed source priority
departs from that for this historical corpus (measurement in §2). The FreeLaw dataset card
describes OCR/formatting post-processing without the "only obvious errors" limitation an
earlier version of this note attributed to it; that attribution is withdrawn.

Sources: Eventual, "Processing 99% of U.S. Caselaw for Under $1 in the Common Pile"
(https://www.eventual.ai/blog/processing-99-of-us-caselaw-for-under-1-in-the-common-pile) ·
free-law/Caselaw_Access_Project (https://huggingface.co/datasets/free-law/Caselaw_Access_Project) ·
Pile of Law, Henderson et al. 2022 (https://arxiv.org/abs/2207.00220) ·
CourtListener Case Law API (https://www.courtlistener.com/help/api/rest/case-law/).
(A COLD Cases citation was removed: the cited page does not document the claim it was
attached to.)
