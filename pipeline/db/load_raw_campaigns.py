"""Load 01_data_collection/final_all_platforms_dataset.csv into MongoDB.

Target collection: donationscam.raw_campaigns
Primary key: url (unique index)
Secondary indexes: platform, source_dataset, created_date

Run:
    python3 ccs2026/db/load_raw_campaigns.py
    python3 ccs2026/db/load_raw_campaigns.py --drop   # rebuild from scratch
"""
import argparse
import math
import sys
from pathlib import Path

import pandas as pd
from pymongo import ASCENDING, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

CSV = Path(__file__).resolve().parent.parent / "01_data_collection" / "final_all_platforms_dataset.csv"
COLL = "raw_campaigns"
BATCH = 2000


def clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true", help="drop collection before loading")
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        db[COLL].drop()
        print(f"[drop] {COLL}")

    print(f"[read] {CSV}")
    df = pd.read_csv(CSV, low_memory=False)
    print(f"  rows: {len(df):,}  cols: {len(df.columns)}")

    print(f"[index] url (unique), platform, source_dataset, created_date")
    db[COLL].create_index([("url", ASCENDING)], unique=True, name="url_unique")
    db[COLL].create_index([("platform", ASCENDING)], name="platform_idx")
    db[COLL].create_index([("source_dataset", ASCENDING)], name="source_idx")
    db[COLL].create_index([("created_date", ASCENDING)], name="created_idx")

    print(f"[upsert] {len(df):,} rows in batches of {BATCH}")
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
                print(f"  {i:,}/{len(df):,}  upserted={n_upserted:,} modified={n_modified:,}")
    if ops:
        r = db[COLL].bulk_write(ops, ordered=False)
        n_upserted += r.upserted_count
        n_modified += r.modified_count

    total = db[COLL].count_documents({})
    print()
    print(f"[done] collection: donationscam.{COLL}")
    print(f"  total docs:     {total:,}")
    print(f"  upserted:       {n_upserted:,}")
    print(f"  modified:       {n_modified:,}")
    print()
    print("per-platform:")
    for p in db[COLL].aggregate([
        {"$group": {"_id": "$platform", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"  {p['_id']:<20} {p['n']:>8,}")


if __name__ == "__main__":
    main()
