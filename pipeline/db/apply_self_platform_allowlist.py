"""Mark VT lookups for our own 15 crowdfunding platforms as `is_self_platform`,
exclude them from `any_vt_flagged`, and strip them from the VT input list
so future lookups don't waste quota on domains we already trust.

For every vt_results doc:
  - set is_self_platform: bool

For every campaigns doc:
  - tag each vt_lookups entry with is_self_platform
  - recompute reputation.summary.any_vt_flagged so it requires at least
    one flagged domain that is NEITHER shared_infra NOR self_platform
  - reuse any_vt_flagged_raw (already populated by the shared-infra patch)

Also rewrites ccs2026/03_detection/intel/domains_clean_all.txt to drop
self-platform domains, leaving the original at *_orig.txt for audit.

Run:
    python3 ccs2026/db/apply_self_platform_allowlist.py --dry-run
    python3 ccs2026/db/apply_self_platform_allowlist.py
"""
import argparse
import sys
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from db_util import (CROWDFUNDING_PLATFORM_DOMAINS,
                      is_self_platform, is_shared_infra)

DOMAINS_TXT = Path(__file__).resolve().parent.parent / "03_detection" / "intel" / "domains_clean_all.txt"


def patch_vt_results(db, dry_run):
    print(f"\n=== vt_results ===")
    n = n_self = 0
    ops = []
    for d in db.vt_results.find({}, {"_id": 1, "domain": 1}):
        n += 1
        sp = is_self_platform(d.get("domain", ""))
        if sp:
            n_self += 1
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"is_self_platform": sp}}))
        if len(ops) >= 1000 and not dry_run:
            db.vt_results.bulk_write(ops, ordered=False); ops = []
    if ops and not dry_run:
        db.vt_results.bulk_write(ops, ordered=False)
    print(f"  total domains:        {n:,}")
    print(f"  marked self_platform: {n_self:,}")


def patch_campaigns(db, dry_run):
    print(f"\n=== campaigns ===")
    n_docs = n_was_flagged = n_now_flagged = n_demoted = 0
    domains_demoted = {}
    ops = []
    for doc in db.campaigns.find(
        {"reputation.vt_lookups.0": {"$exists": True}},
        {"_id": 1, "url": 1, "reputation.vt_lookups": 1, "reputation.summary": 1},
    ):
        n_docs += 1
        rep = doc.get("reputation") or {}
        lookups = rep.get("vt_lookups") or []
        summary = rep.get("summary") or {}
        was_flagged = bool(summary.get("any_vt_flagged"))
        if was_flagged:
            n_was_flagged += 1

        new_lookups = []
        n_meaningful = 0
        for lk in lookups:
            d = lk.get("domain", "")
            sp = is_self_platform(d)
            si = bool(lk.get("is_shared_infra")) or is_shared_infra(d)
            new_lk = {**lk, "is_shared_infra": si, "is_self_platform": sp}
            new_lookups.append(new_lk)
            if lk.get("flagged") and not si and not sp:
                n_meaningful += 1

        any_meaningful = n_meaningful > 0
        if any_meaningful:
            n_now_flagged += 1
        if was_flagged and not any_meaningful:
            n_demoted += 1
            for lk in lookups:
                if lk.get("flagged") and is_self_platform(lk.get("domain", "")):
                    domains_demoted[lk["domain"]] = domains_demoted.get(lk["domain"], 0) + 1

        new_summary = {
            **summary,
            "any_vt_flagged":    any_meaningful,
            "n_domains_flagged": n_meaningful,
        }
        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {"reputation.vt_lookups": new_lookups,
                       "reputation.summary":   new_summary}},
        ))
        if len(ops) >= 500 and not dry_run:
            db.campaigns.bulk_write(ops, ordered=False); ops = []
    if ops and not dry_run:
        db.campaigns.bulk_write(ops, ordered=False)

    print(f"  campaigns with VT lookups: {n_docs:,}")
    print(f"  was any_vt_flagged=true:   {n_was_flagged:,}")
    print(f"  now any_vt_flagged=true:   {n_now_flagged:,}")
    print(f"  newly demoted (only self-platform flag): {n_demoted:,}")
    if domains_demoted:
        print(f"\n  self-platform domains responsible for demotions:")
        for d, n in sorted(domains_demoted.items(), key=lambda x: -x[1]):
            print(f"    {d:30s} {n:>4} campaigns")


def strip_domains_txt(dry_run):
    print(f"\n=== domains_clean_all.txt ===")
    if not DOMAINS_TXT.exists():
        print(f"  {DOMAINS_TXT} not found, skipping")
        return
    lines = [l.strip() for l in DOMAINS_TXT.read_text().splitlines() if l.strip()]
    keep = [l for l in lines if not is_self_platform(l)]
    dropped = [l for l in lines if is_self_platform(l)]
    print(f"  total domains:    {len(lines):,}")
    print(f"  drop (self-plat): {len(dropped):,}  -> {dropped[:10]}{'...' if len(dropped)>10 else ''}")
    print(f"  keep:             {len(keep):,}")
    if not dry_run:
        bak = DOMAINS_TXT.with_suffix(".txt.orig")
        if not bak.exists():
            bak.write_text("\n".join(lines) + "\n")
            print(f"  backed up original to {bak.name}")
        DOMAINS_TXT.write_text("\n".join(keep) + ("\n" if keep else ""))
        print(f"  rewrote {DOMAINS_TXT.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"[allowlist] {len(CROWDFUNDING_PLATFORM_DOMAINS)} crowdfunding-platform domains")
    db = get_db()
    patch_vt_results(db, args.dry_run)
    patch_campaigns(db, args.dry_run)
    strip_domains_txt(args.dry_run)
    if args.dry_run:
        print("\n[dry-run] no DB or file writes performed")
    else:
        print("\n[done] vt_results, campaigns, and domains_clean_all.txt updated")


if __name__ == "__main__":
    main()
