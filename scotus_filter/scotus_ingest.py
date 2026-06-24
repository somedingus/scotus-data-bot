#!/usr/bin/env python3
"""Pull the clean set of genuine SCOTUS decisions for a date range from CourtListener.

WHY THIS EXISTS
---------------
CourtListener tags everything printed in the early U.S. Reports as court="scotus",
but the Dallas reporters (vols 2-4 U.S.) reprinted Pennsylvania state-court and
lower federal (circuit) cases alongside actual Supreme Court decisions. A raw
court=scotus query for 1790-1820 therefore returns ~1,076 clusters, of which a
few hundred are NOT Supreme Court cases.

THE FILTER RULE (validated against known cases)
-----------------------------------------------
  KEEP   if  U.S. reporter volume >= 5     (Cranch/Wheaton onward = pure SCOTUS)
         OR  scdb_id is non-empty          (Supreme Court Database = real SCOTUS decision)
  REVIEW otherwise (vol 1-4 AND no scdb_id) -> almost all PA / circuit reprints,
         plus a small residue of genuine-SCOTUS items SCDB does not catalog
         (administrative orders, seriatim opinion fragments). REVIEW is NOT
         auto-discarded -- it is the human-check bucket.

USAGE
-----
  python3 scotus_ingest.py [--after 1790-01-01] [--before 1820-12-31] [--outdir .]
  Set COURTLISTENER_API_TOKEN for higher rate limits (anonymous works but is throttled).

OUTPUT
------
  all_clusters.csv  every cluster with its bucket + reason
  keep.csv          confirmed/high-confidence SCOTUS
  review.csv        candidates to verify before dropping
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://www.courtlistener.com/api/rest/v4/search/"
# Require a digit AFTER "U.S." so we match reporter cites ("2 U.S. 399")
# but NOT LEXIS cites ("1810 U.S. LEXIS 350").
US_CITE = re.compile(r"^\s*(\d+)\s+U\.S\.\s+\d")


# ---- filter rule -----------------------------------------------------------

def us_volume(citations):
    for c in citations or []:
        m = US_CITE.match(c)
        if m:
            return int(m.group(1))
    return None


def first_us_cite(citations):
    for c in citations or []:
        if US_CITE.match(c):
            return c.strip()
    return (citations or [""])[0] if citations else ""


def classify(row):
    """Return (bucket, reason)."""
    scdb = (row.get("scdb_id") or "").strip()
    vol = us_volume(row.get("citation"))
    if scdb:
        return "KEEP", "has scdb_id"
    if vol is not None and vol >= 5:
        return "KEEP", "vol>=5 (Cranch/Wheaton)"
    return "REVIEW", f"vol={vol} no scdb_id"


# ---- fetch -----------------------------------------------------------------

def fetch_all(after, before, token=None, page_pause=0.5):
    """Follow cursor pagination through the whole result set."""
    params = {
        "type": "o",
        "court": "scotus",
        "filed_after": after,
        "filed_before": before,
        "order_by": "dateFiled asc",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "scotus-data-bot/1.0"}
    if token:
        headers["Authorization"] = f"Token {token}"

    rows, seen = [], set()
    page = 0
    while url:
        page += 1
        req = urllib.request.Request(url, headers=headers)
        data = None
        for attempt in range(6):                    # retry transient errors/timeouts
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:                   # throttled -> back off and retry
                    wait = int(e.headers.get("Retry-After", "30"))
                    print(f"  throttled (429), waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait + 1)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                wait = 5 * (attempt + 1)
                print(f"  network error ({e}); retry in {wait}s...", file=sys.stderr)
                time.sleep(wait)
        if data is None:
            raise RuntimeError(f"failed to fetch page {page} after retries: {url}")
        for r in data.get("results", []):
            cid = r.get("cluster_id")
            if cid in seen:
                continue
            seen.add(cid)
            rows.append(r)
        total = data.get("count")
        print(f"  page {page}: +{len(data.get('results', []))} "
              f"(have {len(rows)}/{total})", file=sys.stderr)
        url = data.get("next")
        if url:
            time.sleep(page_pause)
    return rows


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", default="1790-01-01")
    ap.add_argument("--before", default="1820-12-31")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--from-cache", action="store_true",
                    help="reprocess raw_clusters.json instead of refetching")
    args = ap.parse_args()

    cache = os.path.join(args.outdir, "raw_clusters.json")
    if args.from_cache and os.path.exists(cache):
        print(f"Loading raw clusters from cache {cache}", file=sys.stderr)
        with open(cache) as f:
            rows = json.load(f)
    else:
        token = os.environ.get("COURTLISTENER_API_TOKEN")
        print(f"Fetching court=scotus opinions {args.after}..{args.before}"
              f" ({'token' if token else 'anonymous'})", file=sys.stderr)
        rows = fetch_all(args.after, args.before, token=token)
        with open(cache, "w") as f:                 # cache raw API rows for re-processing
            json.dump(rows, f)
        print(f"Cached {len(rows)} raw clusters to {cache}", file=sys.stderr)

    recs = []
    for r in rows:
        bucket, reason = classify(r)
        recs.append({
            "cluster_id": r.get("cluster_id"),
            "caseName": r.get("caseName", ""),
            "us_cite": first_us_cite(r.get("citation")),
            "volume": us_volume(r.get("citation")),
            "scdb_id": (r.get("scdb_id") or "").strip(),
            "dateFiled": r.get("dateFiled", ""),
            "bucket": bucket,
            "reason": reason,
        })
    recs.sort(key=lambda x: (x["dateFiled"], x["cluster_id"]))

    cols = ["cluster_id", "caseName", "us_cite", "volume", "scdb_id",
            "dateFiled", "bucket", "reason"]

    def write(name, data):
        path = os.path.join(args.outdir, name)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(data)
        return path

    keep = [r for r in recs if r["bucket"] == "KEEP"]
    review = [r for r in recs if r["bucket"] == "REVIEW"]
    write("all_clusters.csv", recs)
    write("keep.csv", [{k: r[k] for k in cols} for r in keep])
    write("review.csv", [{k: r[k] for k in cols} for r in review])

    from collections import Counter
    print(f"\nTOTAL clusters : {len(recs)}")
    print(f"KEEP           : {len(keep)}")
    print(f"REVIEW         : {len(review)}")
    print("KEEP by volume :", dict(sorted(Counter(r['volume'] for r in keep).items(),
                                          key=lambda x: (x[0] is None, x[0]))))
    print("REVIEW by vol  :", dict(sorted(Counter(r['volume'] for r in review).items(),
                                          key=lambda x: (x[0] is None, x[0]))))


if __name__ == "__main__":
    main()
