"""Load VT and IPQS result JSONLs into MongoDB.

Three collections:
    vt_results          one doc per domain looked up
    ipqs_email_results  one doc per email looked up
    ipqs_phone_results  one doc per phone looked up

Primary key in each: the lookup target (domain / email / phone) with unique index.
The original JSONL records are stored as-is plus a derived `flagged` boolean
that captures the threshold logic each detector cares about, so downstream
queries can do {flagged: true} without re-reading the raw fields.

Run:
    python3 ccs2026/db/load_reputation.py
    python3 ccs2026/db/load_reputation.py --drop
"""
import argparse
import json
import sys
from pathlib import Path

from pymongo import ASCENDING, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

INTEL = Path(__file__).resolve().parent.parent / "03_detection" / "intel"

VT_THRESHOLD       = 2     # malicious + suspicious >= 2
IPQS_THRESHOLD     = 85    # fraud_score >= 85

SOURCES = {
    "vt_results": {
        "path":      INTEL / "vt_domain_results.jsonl",
        "key_field": "domain",
        "flagged":   lambda r: (r.get("vt_malicious") or 0) + (r.get("vt_suspicious") or 0) >= VT_THRESHOLD,
    },
    "ipqs_email_results": {
        "path":      INTEL / "ipqs_email_results.jsonl",
        "key_field": "email",
        "flagged":   lambda r: (r.get("result") or {}).get("fraud_score", 0) >= IPQS_THRESHOLD,
    },
    "ipqs_phone_results": {
        "path":      INTEL / "ipqs_phone_results.jsonl",
        "key_field": "phone",
        "flagged":   lambda r: (r.get("result") or {}).get("fraud_score", 0) >= IPQS_THRESHOLD,
    },
}


def load_collection(db, coll, cfg, drop):
    if drop:
        db[coll].drop()
        print(f"[drop] {coll}")
    db[coll].create_index([(cfg["key_field"], ASCENDING)], unique=True, name=f"{cfg['key_field']}_unique")
    db[coll].create_index([("flagged", ASCENDING)], name="flagged_idx")
    db[coll].create_index([("checked_at", ASCENDING)], name="checked_at_idx")

    if not cfg["path"].exists():
        print(f"[skip] {cfg['path']} not found")
        return

    print(f"[read] {cfg['path']}")
    n_read = 0
    ops = []
    with open(cfg["path"]) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            key = rec.get(cfg["key_field"])
            if not key:
                continue
            n_read += 1
            doc = dict(rec)
            doc["flagged"] = bool(cfg["flagged"](rec))
            ops.append(UpdateOne({cfg["key_field"]: key}, {"$set": doc}, upsert=True))
            if len(ops) >= 1000:
                db[coll].bulk_write(ops, ordered=False)
                ops = []
    if ops:
        db[coll].bulk_write(ops, ordered=False)

    total = db[coll].count_documents({})
    flagged = db[coll].count_documents({"flagged": True})
    print(f"  read={n_read:,}  total={total:,}  flagged={flagged:,} ({100*flagged/max(total,1):.1f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()
    db = get_db()
    for coll, cfg in SOURCES.items():
        print()
        print(f"=== {coll} ===")
        load_collection(db, coll, cfg, args.drop)


if __name__ == "__main__":
    main()
