"""Rebuild detector_A.{canonical_flagged, extended_flagged, signals_fired}
directly from the live mongo state.

Reads (per campaign):
  reputation.summary.any_vt_flagged          -> signals_fired.vt
  reputation.summary.any_ipqs_email_flagged  -> signals_fired.ipqs_email
  reputation.summary.any_ipqs_phone_flagged  -> signals_fired.ipqs_phone
  detector_A.url_level_vt_flagged            -> signals_fired.url_level_vt
  hr_flags.n_hr_hits  >= 2                   -> signals_fired.hr_strong
  redirection.flagged                        -> signals_fired.redirection

Writes:
  detector_A.canonical_flagged  = any of (vt, url_level_vt, ipqs_email,
                                          ipqs_phone, hr_strong)
  detector_A.extended_flagged   = canonical OR redirection
  detector_A.signals_fired      = the 6-field breakdown

Idempotent. --dry-run for preview.
"""
import argparse
import sys
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = get_db()

    proj = {
        "_id": 1,
        "reputation.summary.any_vt_flagged":         1,
        "reputation.summary.any_ipqs_email_flagged": 1,
        "reputation.summary.any_ipqs_phone_flagged": 1,
        "detector_A.url_level_vt_flagged":           1,
        "hr_flags.n_hr_hits":                        1,
        "redirection.flagged":                       1,
    }

    print("computing new Detector A flags from current state...")
    ops = []
    n_total = n_canon = n_ext = 0
    sig_counts = {"vt": 0, "url_level_vt": 0, "ipqs_email": 0, "ipqs_phone": 0,
                  "hr_strong": 0, "redirection": 0}

    for doc in db.campaigns.find({}, proj):
        n_total += 1
        rep = (doc.get("reputation") or {}).get("summary") or {}
        det_A_old = doc.get("detector_A") or {}

        signals_fired = {
            "vt":           bool(rep.get("any_vt_flagged")),
            "ipqs_email":   bool(rep.get("any_ipqs_email_flagged")),
            "ipqs_phone":   bool(rep.get("any_ipqs_phone_flagged")),
            "url_level_vt": bool(det_A_old.get("url_level_vt_flagged")),
            "hr_strong":    (doc.get("hr_flags") or {}).get("n_hr_hits", 0) >= 2,
            "redirection":  bool((doc.get("redirection") or {}).get("flagged")),
        }
        for k, v in signals_fired.items():
            if v:
                sig_counts[k] += 1

        canonical = any(signals_fired[k] for k in
                        ("vt", "url_level_vt", "ipqs_email", "ipqs_phone", "hr_strong"))
        extended  = canonical or signals_fired["redirection"]

        if canonical: n_canon += 1
        if extended:  n_ext += 1

        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {
            "detector_A.canonical_flagged": canonical,
            "detector_A.extended_flagged":  extended,
            "detector_A.signals_fired":     signals_fired,
        }}))

    print(f"\n=== per-signal contribution ===")
    for k, v in sig_counts.items():
        print(f"  {k:<15} {v:,}")

    print(f"\n=== Detector A consensus (refreshed) ===")
    print(f"  total campaigns:                    {n_total:,}")
    print(f"  detector_A.canonical_flagged true:  {n_canon:,}")
    print(f"  detector_A.extended_flagged true:   {n_ext:,}")

    if args.dry_run:
        print("\n[dry-run] no writes applied.")
        return

    BATCH = 500
    n_matched = 0
    for i in range(0, len(ops), BATCH):
        res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
        n_matched += res.matched_count
    print(f"\n  applied to mongo:                  {n_matched:,}")


if __name__ == "__main__":
    main()
