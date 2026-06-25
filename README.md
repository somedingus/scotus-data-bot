# scotus-data-bot

Tools and data for building a clean corpus of **U.S. Supreme Court decisions, 1790–1820**, from the [CourtListener](https://www.courtlistener.com/) API.

## The problem

A raw `court=scotus` query to CourtListener for 1790–1820 returns **1,076 opinion clusters** — but not all of them are Supreme Court cases. The early *U.S. Reports* (Dallas reporters, vols 2–4) reprinted Pennsylvania state-court and lower federal (circuit) cases alongside genuine SCOTUS decisions, and CourtListener tags them all as `scotus`.

## The filter rule

Validated against known cases (e.g. *Chisholm v. Georgia*, *Ware v. Hylton*, *Calder v. Bull* all carry SCDB IDs; the Pennsylvania reprints do not):

> **KEEP** if the U.S. reporter volume is **≥ 5** (Cranch/Wheaton onward = exclusively SCOTUS) **OR** the cluster has a non-empty **`scdb_id`** (present only on cases catalogued in the Supreme Court Database).
>
> **REVIEW** otherwise (vol 1–4 *and* no `scdb_id`) — almost all Pennsylvania/circuit reprints, plus a small residue of genuine-SCOTUS items the SCDB does not catalog (administrative orders, seriatim opinion fragments). REVIEW is the **human-check** bucket, not an automatic discard.

## Results (1790–1820)

| Bucket | Count |
|--------|------:|
| KEEP   | 870   |
| REVIEW | 206   |
| **Total** | **1,076** |

Integrity: KEEP + REVIEW = 1,076; zero rule violations; KEEP spans Dallas vols 2–4 (62 genuine early-SCOTUS cases with SCDB IDs) plus the full Cranch (5–13) and Wheaton (14–18) runs.

## Files

| File | Description |
|------|-------------|
| `scotus_filter/scotus_ingest.py` | Reusable ingestion: pulls `court=scotus` for any date range, paginates with throttle/timeout backoff, applies the filter, writes the CSVs. |
| `scotus_filter/all_clusters.csv` | All 1,076 clusters with bucket + reason. |
| `scotus_filter/keep.csv` | The 870 confirmed/high-confidence SCOTUS cases. |
| `scotus_filter/review.csv` | The 206 candidates to verify before dropping. |
| `scotus_filter/bucket.py`, `scotus_filter/page_*.json` | Superseded bootstrap (manual sample used to derive the rule); kept for provenance. |

## Usage

```bash
cd scotus_filter
python3 scotus_ingest.py                          # default 1790-01-01 .. 1820-12-31
python3 scotus_ingest.py --after 1790-01-01 --before 1835-12-31
python3 scotus_ingest.py --from-cache             # reprocess cached raw data, no refetch
```

Anonymous access works but is rate-limited. The script reads a CourtListener API
token from the `COURTLISTENER_API_TOKEN` environment variable when present and
sends it as `Authorization: Token <key>`.

This project manages that token with [agentsecrets](https://github.com/The-17/agentsecrets)
(zero-knowledge: the value is injected into the child process and never printed).
The directory is linked to the `dev-secret-agent` project; run the ingest with:

```bash
agentsecrets env -- python3 scotus_ingest.py
```

Or, with the token already in your environment by any other means:

```bash
export COURTLISTENER_API_TOKEN=your_token_here
python3 scotus_ingest.py
```

The first successful run writes `scotus_filter/raw_clusters.json` (gitignored),
after which `--from-cache` reprocesses instantly without any network calls.

## Status / next steps

- [x] Filter rule derived and validated
- [x] Full 1790–1820 enumeration and classification
- [ ] Human review pass over the 206 REVIEW candidates
- [ ] Full-text retrieval for the confirmed-SCOTUS set
