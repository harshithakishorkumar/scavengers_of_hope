"""Dump per-entry audit CSV for the social_handles migration.

For every (campaign_url, raw_value) pair from llm_contacts.social_handles_raw,
re-runs the parser and writes one CSV row showing whether the entry was kept
or dropped, plus the typed output / drop reason.

Output:
    ccs2026/03_detection/intel/social_handles_audit.csv

Columns:
    url, raw, action, platform, user_id, typed_url, drop_reason

Run:
    python3 ccs2026/db/dump_social_handles_audit.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from migrate_social_handles import parse_social_handle

OUT = Path(__file__).resolve().parent.parent / "03_detection" / "intel" / "social_handles_audit.csv"


def main():
    db = get_db()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n_kept = n_dropped = n_records = 0
    drop_reasons = {}
    platforms = {}

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "raw", "action", "platform", "user_id", "typed_url", "drop_reason"])

        cur = db.campaigns.find(
            {"llm_contacts.social_handles_raw.0": {"$exists": True}},
            {"_id": 0, "url": 1, "llm_contacts.social_handles_raw": 1},
        )
        for doc in cur:
            n_records += 1
            url = doc["url"]
            for raw in (doc.get("llm_contacts") or {}).get("social_handles_raw") or []:
                platform, uid, typed_url, drop = parse_social_handle(raw)
                if drop:
                    n_dropped += 1
                    drop_reasons[drop] = drop_reasons.get(drop, 0) + 1
                    w.writerow([url, raw, "dropped", "", "", "", drop])
                else:
                    n_kept += 1
                    platforms[platform] = platforms.get(platform, 0) + 1
                    w.writerow([url, raw, "kept", platform, uid, typed_url or "", ""])

    total = n_kept + n_dropped
    print(f"[done] {OUT}")
    print(f"  records with social_handles_raw: {n_records:,}")
    print(f"  total entries: {total:,}")
    print(f"    kept:    {n_kept:,} ({100*n_kept/max(total,1):.1f}%)")
    print(f"    dropped: {n_dropped:,} ({100*n_dropped/max(total,1):.1f}%)")
    print()
    print("kept by platform:")
    for p, n in sorted(platforms.items(), key=lambda x: -x[1]):
        print(f"  {p:14s} {n:>6,}")
    print()
    print("dropped by reason:")
    for r, n in sorted(drop_reasons.items(), key=lambda x: -x[1]):
        print(f"  {r:30s} {n:>6,}")


if __name__ == "__main__":
    main()
