"""Load campaign_contacts_v4_llm_cleaned.jsonl into MongoDB.

Target collection: donationscam.llm_contacts
Primary key: url (unique index)

Schema per doc:
    url, ok, platform (joined from filtered if available),
    emails [str], phones [str],
    payment_handles [{kind, value, source?}],
    social_handles [str], urls [str],
    names [str], locations [str]

Indexes (multikey on the array fields so JOIN-style queries are fast):
    url (unique), platform, payment_handles.kind,
    emails, phones, social_handles, urls

Run:
    python3 ccs2026/db/load_llm_contacts.py
    python3 ccs2026/db/load_llm_contacts.py --drop
"""
import argparse
import json
import sys
from pathlib import Path

from pymongo import ASCENDING, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

INTEL = Path(__file__).resolve().parent.parent / "03_detection" / "intel"
JSONL = INTEL / "campaign_contacts_v4_llm_cleaned.jsonl"
COLL  = "llm_contacts"
BATCH = 2000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(JSONL))
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        db[COLL].drop()
        print(f"[drop] {COLL}")

    print(f"[index] url (unique), platform, payment_handles.kind, emails, phones, social_handles, urls")
    db[COLL].create_index([("url", ASCENDING)], unique=True, name="url_unique")
    db[COLL].create_index([("platform", ASCENDING)], name="platform_idx")
    db[COLL].create_index([("payment_handles.kind", ASCENDING)], name="payment_kind_idx")
    db[COLL].create_index([("emails", ASCENDING)], name="emails_idx")
    db[COLL].create_index([("phones", ASCENDING)], name="phones_idx")
    db[COLL].create_index([("social_handles", ASCENDING)], name="social_idx")
    db[COLL].create_index([("urls", ASCENDING)], name="urls_idx")

    print(f"[join] reading platform from filtered ...")
    platform_by_url = {d["url"]: d.get("platform")
                        for d in db["filtered"].find({}, {"url": 1, "platform": 1})}
    print(f"  {len(platform_by_url):,} URLs in filtered")

    print(f"[read] {args.input}")
    n_read = n_upserted = n_modified = 0
    ops = []
    with open(args.input) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            url = rec.get("url")
            if not url:
                continue
            n_read += 1
            doc = {
                "url":             url,
                "ok":              bool(rec.get("ok")),
                "platform":        platform_by_url.get(url),
                "emails":          rec.get("emails") or [],
                "phones":          rec.get("phones") or [],
                "payment_handles": rec.get("payment_handles") or [],
                "social_handles":  rec.get("social_handles") or [],
                "urls":            rec.get("urls") or [],
                "names":           rec.get("names") or [],
                "locations":       rec.get("locations") or [],
            }
            ops.append(UpdateOne({"url": url}, {"$set": doc}, upsert=True))
            if len(ops) >= BATCH:
                r = db[COLL].bulk_write(ops, ordered=False)
                n_upserted += r.upserted_count
                n_modified += r.modified_count
                ops = []
                if n_read % 10000 == 0:
                    print(f"  {n_read:,} read")
    if ops:
        r = db[COLL].bulk_write(ops, ordered=False)
        n_upserted += r.upserted_count
        n_modified += r.modified_count

    total = db[COLL].count_documents({})
    print()
    print(f"[done] donationscam.{COLL}")
    print(f"  total docs:        {total:,}")
    print(f"  upserted/modified: {n_upserted:,} / {n_modified:,}")
    print()
    print("coverage:")
    for f in ("emails", "phones", "payment_handles", "social_handles", "urls", "names", "locations"):
        c = db[COLL].count_documents({f: {"$exists": True, "$not": {"$size": 0}}})
        print(f"  {f:18s} {c:>6,} ({100*c/total:.1f}%)")

    print()
    print("payment_handles by kind:")
    for r in db[COLL].aggregate([
        {"$unwind": "$payment_handles"},
        {"$group": {"_id": "$payment_handles.kind", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"  {r['_id']:<14} {r['n']:>6,}")


if __name__ == "__main__":
    main()
