"""Dump every domain in vt_input_domains as one row per CSV — so you can
sort/filter in Excel to decide what should still be excluded or queried.

Output: ccs2026/03_detection/intel/vt_input_domains_audit.csv
Columns:
    domain, status, is_valid, is_shared_infra, is_self_platform,
    n_campaigns, sources, sample_campaigns,
    vt_flagged, vt_malicious, vt_suspicious, vt_reputation, checked_at
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

OUT = Path(__file__).resolve().parent.parent / "03_detection" / "intel" / "vt_input_domains_audit.csv"


def main():
    db = get_db()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    by_status = {}
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["domain", "status", "is_valid", "is_shared_infra",
                    "is_self_platform", "n_campaigns", "sources",
                    "sample_campaigns",
                    "vt_flagged", "vt_malicious", "vt_suspicious",
                    "vt_reputation", "checked_at"])
        for d in db.vt_input_domains.find({}, {"_id": 0}).sort([
            ("status", 1), ("n_campaigns", -1), ("domain", 1)
        ]):
            n += 1
            by_status[d["status"]] = by_status.get(d["status"], 0) + 1
            vt = d.get("vt") or {}
            w.writerow([
                d["domain"],
                d["status"],
                d.get("is_valid", ""),
                d.get("is_shared_infra", ""),
                d.get("is_self_platform", ""),
                d.get("n_campaigns", 0),
                "|".join(d.get("sources") or []),
                "|".join(d.get("sample_campaigns") or []),
                vt.get("flagged", ""),
                vt.get("vt_malicious", ""),
                vt.get("vt_suspicious", ""),
                vt.get("vt_reputation", ""),
                vt.get("checked_at", ""),
            ])

    print(f"[done] wrote {OUT}")
    print(f"  total rows: {n:,}")
    print()
    print("by status:")
    for s, c in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {s:30s} {c:>6,}")


if __name__ == "__main__":
    main()
