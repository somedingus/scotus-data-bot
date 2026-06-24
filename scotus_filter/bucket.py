#!/usr/bin/env python3
"""Bucket 1790-1820 'scotus' opinion clusters into KEEP vs REVIEW.

Rule (established empirically against known cases):
  KEEP   = first U.S. reporter volume >= 5   (Cranch/Wheaton: pure SCOTUS)
           OR scdb_id is non-empty            (Supreme Court Database = real SCOTUS decision)
  REVIEW = U.S. volume in 1-4 (Dallas) AND scdb_id empty
           -> mostly Pennsylvania + lower-federal (circuit) cases,
              plus rare genuine-SCOTUS edge cases (admin orders, opinion fragments)

Input : page_*.json  (each = the `results` array from one search page)
Output: keep.csv, review.csv, summary printed to stdout
"""
import csv
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

US_CITE = re.compile(r"^\s*(\d+)\s+U\.S\.\s")

def us_volume(citations):
    """Return the integer volume from the first 'N U.S. P' cite, else None."""
    for c in citations or []:
        m = US_CITE.match(c)
        if m:
            return int(m.group(1))
    return None

def first_us_cite(citations):
    for c in citations or []:
        if US_CITE.match(c):
            return c.strip()
    return (citations or [""])[0]

def load_rows():
    rows = []
    seen = set()
    for path in sorted(glob.glob(os.path.join(HERE, "page_*.json"))):
        with open(path) as f:
            for r in json.load(f):
                cid = r.get("cluster_id")
                if cid in seen:           # guard against page-overlap dupes
                    continue
                seen.add(cid)
                rows.append(r)
    return rows

def classify(r):
    vol = us_volume(r.get("citation"))
    scdb = (r.get("scdb_id") or "").strip()
    if scdb:
        return "KEEP", "has scdb_id"
    if vol is not None and vol >= 5:
        return "KEEP", "vol>=5 (Cranch/Wheaton)"
    return "REVIEW", f"vol={vol} no scdb_id"

def main():
    rows = load_rows()
    keep, review = [], []
    for r in rows:
        bucket, reason = classify(r)
        rec = {
            "cluster_id": r.get("cluster_id"),
            "caseName": r.get("caseName", ""),
            "us_cite": first_us_cite(r.get("citation")),
            "volume": us_volume(r.get("citation")),
            "scdb_id": (r.get("scdb_id") or "").strip(),
            "dateFiled": r.get("dateFiled", ""),
            "reason": reason,
        }
        (keep if bucket == "KEEP" else review).append(rec)

    for name, data in (("keep.csv", keep), ("review.csv", review)):
        with open(os.path.join(HERE, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0].keys()) if data else
                               ["cluster_id","caseName","us_cite","volume","scdb_id","dateFiled","reason"])
            w.writeheader()
            w.writerows(data)

    print(f"total clusters loaded : {len(rows)}")
    print(f"KEEP                  : {len(keep)}")
    print(f"REVIEW (likely drop)  : {len(review)}")
    # volume histogram of the REVIEW bucket
    from collections import Counter
    vh = Counter(r["volume"] for r in review)
    print("REVIEW by volume      :", dict(sorted(vh.items(), key=lambda x: (x[0] is None, x[0]))))

if __name__ == "__main__":
    main()
