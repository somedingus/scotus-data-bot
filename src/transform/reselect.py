"""Transform · reselect: choose the source-text field per opinion.

materialize retained every candidate transcription field per opinion. This stage
applies a corpus-specific FIXED priority -- the first non-empty field wins; no
per-opinion fidelity evaluation happens here. The order was established by a prior
review of this corpus and is a deliberate departure from CourtListener's general
recommendation of ``html_with_citations``. The result is a pointer, not a copy:
every source field stays in stg_opinions.

Priority, with the measurements behind it (current corpus: 674 chosen -- lawbox 429 /
harvard 122 / html 123 / with_citations 0):
- ``html_lawbox``: opinion-scoped in this corpus's review; first when present.
- ``xml_harvard``: opinion-scoped apart from structurally tagged front matter that the
  cleaner renders too (census over chosen sources: <headnotes> in 11 opinions,
  <judges> in 6, <attorneys> in 1 -- see docs/clean-text-design.md section 5);
  preferred over html because scope beats surface quality.
- ``html`` (resource.org): bundles reporter apparatus (syllabus, arguments, other
  opinions) into the body. Measured over the 674 corpus opinions: 367 have both
  lawbox and html; among those, the html is median 2.06x the lawbox length (>=2x in
  195 of 367) -- consistent with, but not independently establishing, the prior
  review's bundling classification. A last resort.
- ``html_with_citations``: CL's derived pick; a final fallback (currently unreached).

It works per opinion row, so it is neutral to the combined-vs-split representation: a
cluster's combined row and its per-justice split rows each get a source, and ``type``
is carried through so the distinction stays queryable (the "keep both, typed" decision
for the ~12 dual-representation clusters). Segmenting inline seriatim is a later stage.

Judging OCR damage is not this stage's concern: the priority order already encodes the
fidelity trade-off, and an opinion-level "dirty" flag influenced no choice here. The OCR
application owns detection and forms its own view of which text is degraded.
"""

import sqlite3
from typing import NamedTuple

from config import settings

# Source-text fields in preference order (see module docstring): opinion-only + clean
# first, apparatus-bundled html last, CL's derived pick as the final fallback.
SOURCE_PRIORITY = (
    "source_html_lawbox",
    "source_xml_harvard",
    "source_html",
    "source_html_with_citations",
)


def select_source(opinion: dict) -> str | None:
    """Choose one opinion's source field by priority."""
    for field in SOURCE_PRIORITY:
        if opinion.get(field):
            return field
    return None  # no source text at all (should not occur in the corpus)


class OpinionSource(NamedTuple):
    opinion_id: int
    cluster_id: int
    type: str
    chosen_source: str | None


def build_selections(opinions: list[dict]) -> list[OpinionSource]:
    """Apply the source choice to every opinion (pure; no I/O)."""
    return [
        OpinionSource(
            opinion_id=opinion["opinion_id"],
            cluster_id=opinion["cluster_id"],
            type=opinion.get("type") or "",
            chosen_source=select_source(opinion),
        )
        for opinion in opinions
    ]


def read_corpus_opinions(staging_db_path: str) -> list[dict]:
    """Opinions in the final corpus (canonical KEEP clusters, vols 2-18), as dicts.

    Includes both the combined and per-justice split rows of the dual-representation
    clusters -- the choice is per row, nothing is dropped here."""
    columns = "o.opinion_id, o.cluster_id, o.type, " + ", ".join(f"o.{f}" for f in SOURCE_PRIORITY)
    conn = sqlite3.connect(staging_db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {columns} FROM stg_opinions o "
            "JOIN stg_cluster_dedup d USING (cluster_id) "
            "WHERE d.dedup_role = 'canonical' AND d.us_volume BETWEEN 2 AND 18 "
            "ORDER BY o.opinion_id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


_SOURCE_TABLE_COLUMNS = ("opinion_id", "cluster_id", "type", "chosen_source")


def write_source_table(staging_db_path: str, selections: list[OpinionSource]) -> None:
    """Write the derived stg_opinion_source table (clean rebuild; idempotent)."""
    conn = sqlite3.connect(staging_db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS stg_opinion_source")
        conn.execute(
            "CREATE TABLE stg_opinion_source ("
            "opinion_id INTEGER PRIMARY KEY, cluster_id INTEGER, type TEXT, "
            "chosen_source TEXT)"
        )
        conn.executemany(
            "INSERT INTO stg_opinion_source "
            f"({', '.join(_SOURCE_TABLE_COLUMNS)}) VALUES (?, ?, ?, ?)",
            [(s.opinion_id, s.cluster_id, s.type, s.chosen_source) for s in selections],
        )
        conn.commit()
    finally:
        conn.close()


def run_reselect(staging_db_path: str = settings.STAGING_DB_PATH) -> list[OpinionSource]:
    """Read corpus opinions, choose a source for each, write stg_opinion_source."""
    opinions = read_corpus_opinions(staging_db_path)
    selections = build_selections(opinions)
    write_source_table(staging_db_path, selections)
    return selections
