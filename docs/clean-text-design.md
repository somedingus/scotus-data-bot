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

The reselect stage picks one retained source field per opinion by fidelity
(`html_lawbox` → `xml_harvard` → `html` → `html_with_citations`); the clean stage renders that
chosen field through `clean.clean_opinion`, producing per opinion: `clean_text`,
`page_breaks` (reporter pagination as character-offset spans), and `ocr_suspect` spots
(located, never corrected). The load stage ships those — the shipped database carries no raw
source text; the raw mirror plus `chosen_source` + `clean_version` pin the derivation, and the
offset spans preserve the source structure.

## 3. The cleaner (what it does and why)

- **Structure-aware stdlib `HTMLParser`** handles both source dialects — HTML-flavored records
  and Harvard XML (`<opinion><author>…`) — with no dependency (stdlib-only runtime rule).
- **Three page-marker forms are captured** as page breaks:
  (1) `<span class="star-pagination" label>`, (2) `<page-number label>`, and (3) a bracketed
  inline text form (`[*626` / `*625]`). **Bare unbracketed `*54` is deliberately KEPT** in the
  text — it is ambiguous (footnote asterisk vs. content) and, in the residue, usually
  OCR-garbled pagination, so parsing it would be lossy guessing.
- **Footnote bodies are KEPT.** `<div class="footnote">` bodies are original casebody content
  (one opinion carries a 14K-char footnote reproducing the Circuit judges' reasoning in
  *Hayburn's Case*), not annotations. Inline footnote ref markers (`†`/`*`/digit superscripts)
  are kept verbatim.
- **Normalization:** `\r`→`\n`, strip control chars (keep `\t`/`\n`), collapse whitespace,
  **NFC** — no ASCII folding (that lives in the FTS tokenizer:
  `unicode61 remove_diacritics 2`; the stored column stays strict NFC).
- **No OCR correction.** The suspect detector LOCATES a curated, precision-first set of
  whole-word tokens plus every `■` unreadable-char glyph; `■` stays visible in `clean_text`
  (missing-text signal) and every spot ships as an offset span in the `ocr_suspects` table.
  Correction is a future stage of its own (propose → review → execute), never an in-place edit.
- `clean_version` stamps every derivation; bumping it regenerates `clean_text` and all offsets
  together, so spans can never silently drift against the text they index.

## 4. Reporter apparatus (separate asset)

The early reporters printed substantial front matter that is not part of any opinion. It is
captured raw, at the cluster level, in a standalone `scotus-apparatus.sqlite`
(`--stage apparatus`) rather than folded into opinion bodies — matching CAP's native
`head_matter` vs `opinions[]` split and avoiding 1-to-many duplication across seriatim
opinions. **Pending rework:** the stage still builds from a V1-era snapshot and predates the
current dedup; rebuild it onto the V2 staging before trusting joins against the corpus.

Field semantics (CourtListener model `help_text`, authoritative — `cl/search/models.py`,
`OpinionCluster`):

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

So `headmatter` is the raw container; the others are components carved from the same
pre-opinion matter — the container and its parsed pieces, not strict substrings. Storing all
fields keeps both representations for later reconciliation.

## 5. Prior art & best practices

Recent efforts that clean this exact data (CAP + CourtListener) independently confirm the
approach; no surveyed rule contradicts it.

- **Structured star-pagination, not display-parsed markers.** The Common Pile / Eventual
  pipeline hit bugs "handling these star paginations" and had to re-fetch corrected data; the
  `label`-attribute → `page_breaks` approach sidesteps that pitfall by design.
- **Structure-aware HTML parsing** targeting specific classes/tags (they used Selectolax; we
  use stdlib `html.parser` — same approach, no dependency).
- **Keep headmatter separate from the opinion body** (CAP's native `casebody` schema; COLD
  Cases) → the separate apparatus asset.
- **"Minimize preprocessing; correct only obvious OCR errors"** (CAP/free-law; Pile of Law).
  This project goes further — flag, don't fix: the stricter stance is deliberate for a
  foundational dataset.
- **Unicode + whitespace normalization** — adopted (NFC + collapse).

**Non-goal: text-level minhash near-dedup.** That matters for web-scale corpora full of
reprints; this corpus is already record-deduped against an scdb-anchored rule, human-reviewed
ledgers, and an exact per-volume reference reconciliation. Documented as an explicit non-goal.

Sources: Eventual, "Processing 99% of U.S. Caselaw for Under $1 in the Common Pile"
(https://www.eventual.ai/blog/processing-99-of-us-caselaw-for-under-1-in-the-common-pile) ·
COLD Cases, Harvard LIL (https://lil.law.harvard.edu/our-work/cold-cases/) ·
free-law/Caselaw_Access_Project (https://huggingface.co/datasets/free-law/Caselaw_Access_Project) ·
Pile of Law, Henderson et al. 2022 (https://arxiv.org/abs/2207.00220) ·
CourtListener Case Law API (https://www.courtlistener.com/help/api/rest/case-law/).
