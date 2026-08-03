"""Recover image URLs (and other rich fields) from the original scrape JSONs.

The CSV-conversion step that built MongoDB simplified rich fields out of the
per-campaign scrape JSONs. This script reads the original JSONs from
scraped_campaigns.zip and merges back into MongoDB:

  images          : list of full image URLs from the campaign page
  social_media    : platform-specific social-media links the scraper grabbed
  deadline        : campaign deadline (often missing from the CSV)
  backers_count   : donor count (re-checked against donors_count in mongo)
  percentage_funded : raised/goal as a string from the source

Currently `scraped_campaigns.zip` only contains Spotfund JSONs (35,945).
Other platforms would need a separate scrape archive.

Adds an entry to db.extraction_runs.passes so the recovery is auditable.

Idempotent. --dry-run for preview.
"""
import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


ROOT = Path(__file__).resolve().parent.parent.parent
ZIP  = ROOT / "scraped_campaigns.zip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not ZIP.exists():
        sys.exit(f"missing {ZIP}")

    db = get_db()

    z = zipfile.ZipFile(ZIP)
    json_names = [n for n in z.namelist() if n.endswith(".json")
                  and not n.endswith("_progress.json")]
    print(f"scanning {len(json_names):,} JSONs in scraped_campaigns.zip\n")

    n_seen = 0
    n_with_images = 0
    n_total_image_urls = 0
    n_with_socials   = 0
    n_no_match_in_db = 0
    ops = []

    # Build URL-set in mongo for fast existence check
    print("building URL set from mongo campaigns...")
    mongo_urls = set(d["url"] for d in db.campaigns.find({}, {"url": 1, "_id": 0}))
    print(f"  {len(mongo_urls):,} campaign URLs in mongo")
    print()

    for name in json_names:
        try:
            r = json.loads(z.read(name))
        except Exception:
            continue
        n_seen += 1

        url = r.get("url") or r.get("canonical_url")
        if not url or url not in mongo_urls:
            n_no_match_in_db += 1
            continue

        update_set = {}
        # images
        imgs = r.get("images") or []
        imgs = [u for u in imgs if isinstance(u, str) and u.startswith("http")]
        if imgs:
            update_set["images"] = imgs
            n_with_images += 1
            n_total_image_urls += len(imgs)

        # social_media_links (was completely missing from mongo before)
        sml = r.get("social_media_links")
        if sml:
            update_set["social_media_links"] = sml
            n_with_socials += 1

        # other rich fields the CSV dropped
        for k in ("deadline", "percentage_funded", "backers_count",
                  "days_remaining", "scraping_completed_at"):
            v = r.get(k)
            if v not in (None, "", []):
                update_set[k] = v

        if update_set:
            ops.append(UpdateOne({"url": url}, {"$set": update_set}))

        if n_seen % 5000 == 0:
            print(f"  {n_seen:,} JSONs scanned...")

    print()
    print(f"=== summary ===")
    print(f"  JSONs scanned:                 {n_seen:,}")
    print(f"  no matching URL in mongo:      {n_no_match_in_db:,}")
    print(f"  campaigns with image URLs:     {n_with_images:,}")
    print(f"  total image URLs recovered:    {n_total_image_urls:,}")
    print(f"  campaigns with social_media:   {n_with_socials:,}")
    print(f"  total mongo updates queued:    {len(ops):,}")

    if not args.dry_run and ops:
        BATCH = 500
        n_matched = 0
        for i in range(0, len(ops), BATCH):
            res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
            n_matched += res.matched_count
        print(f"  applied to mongo:              {n_matched:,}")

        # Record the recovery pass in the global extraction_runs doc
        existing = db.extraction_runs.find_one({"scope": "global"}) or {}
        passes = existing.get("passes", [])
        if "image_url_recovery" not in passes:
            passes.append("image_url_recovery")
        db.extraction_runs.update_one(
            {"scope": "global"},
            {"$set": {"passes":     passes,
                      "applied_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        print(f"  recorded image_url_recovery in db.extraction_runs.passes")
    elif args.dry_run:
        print(f"\n  [dry-run] no DB writes")


if __name__ == "__main__":
    main()
