"""Enrich mongo campaigns with rich fields from the chrysm metadata CSV.

The chrysm CSV (originally on cmix10803, copied to /tmp/chrysm.csv) has 70
columns of scrape metadata that the simplified-CSV-to-mongo loader dropped.
This script promotes a *minimal* set of paper-impact fields, only for
campaigns whose URL matches a chrysm record.

Coverage is Spotfund-only (chrysm has Spotfund + Crowdfunder.co.uk +
GlobalGiving, but only Spotfund is in our corpus). About 26k of 100,294
campaigns get any new fields.

Fields added (all under top level):
  verification_status, featured_status, media_coverage,
  reputation_badges, donations, comments,
  organizer_profile, team_information, project_updates

Skipped: scraper provenance, error_message, logs, source_file,
similar_campaigns, redundant fields already in mongo.

Idempotent. --dry-run for preview.
"""
import argparse
import ast
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


CSV_PATH = "/tmp/chrysm.csv"

# Fields we actually want to import. Anything not in this list is dropped.
KEEP_FIELDS = {
    "verification_status", "featured_status", "media_coverage",
    "reputation_badges", "donations_detailed", "comments_detailed",
    "organizer_profile", "team_information", "project_updates",
}

# Where to write each on the campaign doc — slimmer, friendlier names
RENAME = {
    "donations_detailed":   "donations",
    "comments_detailed":    "comments",
}


def parse_field(raw):
    """The chrysm CSV often has Python literal-form lists/dicts as strings.
    Try to parse; on failure, keep the raw string (or drop empty values)."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in ("None", "null", "[]", "{}", "NaN", "nan"):
        return None
    if s.startswith(("[", "{")):
        try:
            return ast.literal_eval(s)
        except Exception:
            try:
                return json.loads(s)
            except Exception:
                return s   # keep as string if neither parses
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not Path(CSV_PATH).exists():
        sys.exit(f"missing {CSV_PATH}")

    csv.field_size_limit(sys.maxsize)
    db = get_db()

    # Build URL set from mongo for fast matching
    print("loading mongo URL set...")
    mongo_urls = set(d["url"] for d in db.campaigns.find({}, {"url": 1, "_id": 0}))
    print(f"  {len(mongo_urls):,} campaigns in mongo\n")

    n_total = 0
    n_match = 0
    n_skip  = 0
    field_populated = Counter()
    ops = []

    with open(CSV_PATH) as f:
        r = csv.DictReader(f)
        for row in r:
            n_total += 1
            url = (row.get("url") or "").strip()
            if not url or url not in mongo_urls:
                n_skip += 1
                continue
            n_match += 1

            update = {}
            for k in KEEP_FIELDS:
                v = parse_field(row.get(k))
                if v is None or v == [] or v == {}:
                    continue
                target_key = RENAME.get(k, k)
                update[target_key] = v
                field_populated[target_key] += 1

            if update:
                ops.append(UpdateOne({"url": url}, {"$set": update}))

    print(f"=== summary ===")
    print(f"  chrysm rows scanned:    {n_total:,}")
    print(f"  matched our mongo:      {n_match:,}")
    print(f"  not in our corpus:      {n_skip:,}")
    print(f"  campaigns with any new field: {len(ops):,}")
    print()
    print("Field-population among matched:")
    for f in sorted(field_populated.keys(), key=lambda x: -field_populated[x]):
        n = field_populated[f]
        pct = 100*n / max(n_match, 1)
        print(f"  {f:<25s}  {n:>6,}  ({pct:>5.1f}%)")

    if not args.dry_run and ops:
        BATCH = 500
        n_matched = 0
        for i in range(0, len(ops), BATCH):
            res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
            n_matched += res.matched_count
        print(f"\n  applied to mongo:       {n_matched:,}")

        # Record the pass in extraction_runs
        existing = db.extraction_runs.find_one({"scope": "global"}) or {}
        passes = existing.get("passes", [])
        if "chrysm_metadata_enrichment" not in passes:
            passes.append("chrysm_metadata_enrichment")
        db.extraction_runs.update_one(
            {"scope": "global"},
            {"$set": {"passes": passes}},
            upsert=True,
        )
    elif args.dry_run:
        print(f"\n  [dry-run] no DB writes")


if __name__ == "__main__":
    main()
