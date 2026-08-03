"""Tag VT-flagged domains that are shared infrastructure (forms.gle, t.me,
link shorteners, etc) and recompute campaign-level VT summary so those
flags don't propagate to Detector A as evidence.

For every campaign doc:
  1. Walk reputation.vt_lookups, set is_shared_infra: bool per entry.
  2. Recompute reputation.summary.any_vt_flagged to require at least one
     non-shared-infra flagged domain.
  3. Recompute n_domains_flagged similarly.
  4. Save the original any_vt_flagged as any_vt_flagged_raw for audit.

Same `is_shared_infra` field added on vt_results collection so direct
queries (db.vt_results.find({flagged: true, is_shared_infra: false})) work.

Run:
    python3 ccs2026/db/apply_shared_infra_allowlist.py --dry-run
    python3 ccs2026/db/apply_shared_infra_allowlist.py
"""
import argparse
import sys
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from db_util import is_shared_infra, SHARED_INFRA_DOMAINS


def patch_vt_results(db, dry_run):
    print(f"\n=== vt_results ===")
    n_total = n_shared = 0
    ops = []
    for d in db.vt_results.find({}, {"_id": 1, "domain": 1}):
        n_total += 1
        shared = is_shared_infra(d.get("domain", ""))
        if shared:
            n_shared += 1
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"is_shared_infra": shared}}))
        if len(ops) >= 1000 and not dry_run:
            db.vt_results.bulk_write(ops, ordered=False); ops = []
    if ops and not dry_run:
        db.vt_results.bulk_write(ops, ordered=False)
    print(f"  total domains:        {n_total:,}")
    print(f"  marked shared_infra:  {n_shared:,}")


def patch_campaigns(db, dry_run):
    print(f"\n=== campaigns ===")
    n_docs = 0
    n_was_flagged = n_now_flagged = n_demoted = 0
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
        n_flagged_meaningful = 0
        for lk in lookups:
            shared = is_shared_infra(lk.get("domain", ""))
            new_lk = {**lk, "is_shared_infra": shared}
            new_lookups.append(new_lk)
            if lk.get("flagged") and not shared:
                n_flagged_meaningful += 1

        any_flagged_meaningful = n_flagged_meaningful > 0
        if any_flagged_meaningful:
            n_now_flagged += 1
        if was_flagged and not any_flagged_meaningful:
            n_demoted += 1
            for lk in lookups:
                if lk.get("flagged") and is_shared_infra(lk.get("domain", "")):
                    d = lk.get("domain")
                    domains_demoted[d] = domains_demoted.get(d, 0) + 1

        new_summary = {
            **summary,
            "any_vt_flagged_raw": was_flagged,
            "any_vt_flagged":     any_flagged_meaningful,
            "n_domains_flagged":  n_flagged_meaningful,
        }

        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {
                "reputation.vt_lookups": new_lookups,
                "reputation.summary":    new_summary,
            }},
        ))
        if len(ops) >= 500 and not dry_run:
            db.campaigns.bulk_write(ops, ordered=False); ops = []

    if ops and not dry_run:
        db.campaigns.bulk_write(ops, ordered=False)

    print(f"  campaigns with VT lookups: {n_docs:,}")
    print(f"  was any_vt_flagged=true:   {n_was_flagged:,}")
    print(f"  now any_vt_flagged=true:   {n_now_flagged:,}")
    print(f"  demoted (flag was only shared-infra): {n_demoted:,}")
    if domains_demoted:
        print(f"\n  shared-infra domains responsible for demotions:")
        for d, n in sorted(domains_demoted.items(), key=lambda x: -x[1]):
            print(f"    {d:30s} {n:>4} campaigns")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"[allowlist] {len(SHARED_INFRA_DOMAINS)} shared-infrastructure domains")
    db = get_db()
    patch_vt_results(db, args.dry_run)
    patch_campaigns(db, args.dry_run)

    if args.dry_run:
        print("\n[dry-run] no DB writes performed")
    else:
        print("\n[done] vt_results + campaigns patched in place")


if __name__ == "__main__":
    main()
