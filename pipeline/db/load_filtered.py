"""Load 02_data_filtration/filtered_dataset_v4.csv into MongoDB.

Target collection: donationscam.filtered
Schema: same 16 fields as raw_campaigns + needs_detectors (bool).
This is the post-dedupe, post-filter corpus the detectors run against.

Run:
    python3 ccs2026/db/load_filtered.py
    python3 ccs2026/db/load_filtered.py --drop
"""
import argparse
import math
import sys
from pathlib import Path

import pandas as pd
from pymongo import ASCENDING, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

CSV = Path(__file__).resolve().parent.parent / "02_data_filtration" / "filtered_dataset_v4.csv"
COLL = "filtered"
BATCH = 2000


def clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        db[COLL].drop()
        print(f"[drop] {COLL}")

    print(f"[read] {CSV}")
    df = pd.read_csv(CSV, low_memory=False)
    print(f"  rows: {len(df):,}  cols: {len(df.columns)}")

    db[COLL].create_index([("url", ASCENDING)], unique=True, name="url_unique")
    db[COLL].create_index([("platform", ASCENDING)], name="platform_idx")
    db[COLL].create_index([("needs_detectors", ASCENDING)], name="needs_detectors_idx")

    n_upserted = 0
    n_modified = 0
    ops = []
    for i, row in enumerate(df.itertuples(index=False), 1):
        doc = {k: clean(v) for k, v in row._asdict().items()}
        url = doc.get("url")
        if not url:
            continue
        ops.append(UpdateOne({"url": url}, {"$set": doc}, upsert=True))
        if len(ops) >= BATCH:
            r = db[COLL].bulk_write(ops, ordered=False)
            n_upserted += r.upserted_count
            n_modified += r.modified_count
            ops = []
            if i % 10000 == 0:
                print(f"  {i:,}/{len(df):,}")
    if ops:
        r = db[COLL].bulk_write(ops, ordered=False)
        n_upserted += r.upserted_count
        n_modified += r.modified_count

    total = db[COLL].count_documents({})
    nd = db[COLL].count_documents({"needs_detectors": True})
    print()
    print(f"[done] donationscam.{COLL}")
    print(f"  total:                {total:,}")
    print(f"  needs_detectors=True: {nd:,}")
    print(f"  upserted/modified:    {n_upserted:,} / {n_modified:,}")


if __name__ == "__main__":
    main()
