"""Apply LLM disambiguation results from the H100 social-handle bundle.

Reads:
  ccs2026/03_detection/intel/social_handles_disambiguated.jsonl
    (one JSON line per campaign: {url, social_handles: [...]})

Updates both `campaigns` and `llm_contacts` collections in place.
Idempotent. Has --dry-run.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

INPUT = Path(__file__).resolve().parent.parent / "03_detection" / "intel" / "social_handles_disambiguated.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(INPUT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not Path(args.input).exists():
        sys.exit(f"missing {args.input}")

    db = get_db()
    n_lines = n_written = 0
    plat_counts = Counter()
    ops_camp = []
    ops_lc = []
    for line in open(args.input):
        if not line.strip(): continue
        n_lines += 1
        rec = json.loads(line)
        url = rec.get("url")
        sh  = rec.get("social_handles") or []
        if not url or not sh:
            continue
        for e in sh:
            if isinstance(e, dict):
                plat_counts[e.get("platform", "unknown")] += 1
        if not args.dry_run:
            ops_camp.append(UpdateOne(
                {"url": url},
                {"$set": {"llm_contacts.social_handles": sh}},
            ))
            ops_lc.append(UpdateOne(
                {"url": url},
                {"$set": {"social_handles": sh}},
            ))
            if len(ops_camp) >= 500:
                db.campaigns.bulk_write(ops_camp, ordered=False)
                db.llm_contacts.bulk_write(ops_lc, ordered=False)
                ops_camp = []; ops_lc = []
        n_written += 1

    if not args.dry_run and ops_camp:
        db.campaigns.bulk_write(ops_camp, ordered=False)
        db.llm_contacts.bulk_write(ops_lc, ordered=False)

    print(f"campaigns in input file:  {n_lines:,}")
    print(f"campaigns updated:        {n_written:,}")
    print()
    print("platform breakdown after disambig:")
    for p, n in plat_counts.most_common():
        print(f"  {p:14s} {n:>5,}")
    if args.dry_run:
        print("\n[dry-run] no DB writes performed")


if __name__ == "__main__":
    main()
