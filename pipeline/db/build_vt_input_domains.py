"""Build a single MongoDB collection that is the canonical source of truth
for which domains need VT lookups, which already have results, and which
should be skipped.

Output: donationscam.vt_input_domains
One doc per unique domain. Schema:
  {
    domain:           "example.com",
    status:           "done" | "pending" | "excluded_invalid" |
                       "excluded_shared_infra" | "excluded_self_platform",
    is_shared_infra:  bool,
    is_self_platform: bool,
    is_valid:         bool,
    n_campaigns:      int,                  # how many campaigns reference it
    sample_campaigns: [str, str, str],      # first 3 URLs for audit
    sources:          ["urls"|"emails"|"social_handles"|"payment_handles"],
    vt:               { flagged, vt_malicious, vt_suspicious, vt_reputation,
                        checked_at }   # populated only when status=done
  }

Also rewrites:
  ccs2026/03_detection/intel/domains_pending_for_vt.txt   (clean ready-to-query list)
  ccs2026/03_detection/intel/domains_invalid.txt          (audit of dropped junk)

Run:
    python3 ccs2026/db/build_vt_input_domains.py
    python3 ccs2026/db/build_vt_input_domains.py --drop
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pymongo import ASCENDING, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from db_util import is_shared_infra, is_self_platform

INTEL = Path(__file__).resolve().parent.parent / "03_detection" / "intel"
PENDING_OUT = INTEL / "domains_pending_for_vt.txt"
INVALID_OUT = INTEL / "domains_invalid.txt"

COLL = "vt_input_domains"

# A real registrable hostname: at least one dot, lowercase letters/digits/hyphens
# in each label, total <= 253 chars, TLD is 2+ alpha chars.
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
DIGITS_RE = re.compile(r"\d+")


def normalize_host(s):
    if not isinstance(s, str):
        return None
    s = s.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].strip().rstrip(".")
    return s


def is_valid_host(s):
    return bool(s) and bool(HOSTNAME_RE.match(s))


def email_domain(e):
    if not isinstance(e, str) or "@" not in e:
        return None
    return normalize_host(e.rsplit("@", 1)[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        db[COLL].drop()
        print(f"[drop] {COLL}")
    db[COLL].create_index([("domain", ASCENDING)], unique=True, name="domain_unique")
    db[COLL].create_index([("status", ASCENDING)], name="status_idx")
    db[COLL].create_index([("is_shared_infra", ASCENDING)], name="shared_infra_idx")
    db[COLL].create_index([("is_self_platform", ASCENDING)], name="self_platform_idx")

    # 1. Walk every campaign, derive every domain referenced, with sources.
    print("[scan] campaigns -> derive domains ...")
    domains = defaultdict(lambda: {"sources": set(), "campaigns": [], "n_campaigns": 0})
    sources_count = Counter()

    for camp in db.campaigns.find({}, {"_id": 0, "url": 1, "llm_contacts": 1}):
        camp_url = camp.get("url", "")
        c = camp.get("llm_contacts") or {}

        candidates = []
        for u in c.get("urls") or []:
            d = normalize_host(u)
            if d: candidates.append((d, "urls"))
        for e in c.get("emails") or []:
            d = email_domain(e)
            if d: candidates.append((d, "emails"))
        for s in c.get("social_handles") or []:
            url = s.get("url") if isinstance(s, dict) else None
            if url:
                d = normalize_host(url)
                if d: candidates.append((d, "social_handles"))
        for p in c.get("payment_handles") or []:
            v = p.get("value") if isinstance(p, dict) else p
            if v and "/" in str(v):
                d = normalize_host(str(v))
                if d: candidates.append((d, "payment_handles"))

        for d, src in candidates:
            entry = domains[d]
            entry["sources"].add(src)
            sources_count[src] += 1
            if camp_url and len(entry["campaigns"]) < 3:
                entry["campaigns"].append(camp_url)
            entry["n_campaigns"] += 1

    print(f"  unique domains derived: {len(domains):,}")
    for src, n in sources_count.most_common():
        print(f"    {src:20s} {n:,} references")

    # 2. Load existing vt_results into a dict for joining.
    print("[load] vt_results ...")
    vt_by_domain = {}
    for r in db.vt_results.find({}, {"_id": 0,
                                       "domain": 1, "flagged": 1,
                                       "vt_malicious": 1, "vt_suspicious": 1,
                                       "vt_reputation": 1, "checked_at": 1,
                                       "is_shared_infra": 1, "is_self_platform": 1}):
        vt_by_domain[(r.get("domain") or "").strip().lower()] = r
    print(f"  {len(vt_by_domain):,} VT results in mongo")

    # 3. Classify each domain into final status, write upserts.
    print("[classify] determining status per domain ...")
    status_counts = Counter()
    ops = []
    for d, info in domains.items():
        valid = is_valid_host(d)
        si    = is_shared_infra(d)
        sp    = is_self_platform(d)
        vt    = vt_by_domain.get(d)

        if not valid:
            status = "excluded_invalid"
        elif si:
            status = "excluded_shared_infra"
        elif sp:
            status = "excluded_self_platform"
        elif vt:
            status = "done"
        else:
            status = "pending"
        status_counts[status] += 1

        doc = {
            "domain":           d,
            "status":           status,
            "is_shared_infra":  si,
            "is_self_platform": sp,
            "is_valid":         valid,
            "n_campaigns":      info["n_campaigns"],
            "sample_campaigns": info["campaigns"],
            "sources":          sorted(info["sources"]),
        }
        if vt:
            doc["vt"] = {
                "flagged":        bool(vt.get("flagged")),
                "vt_malicious":   vt.get("vt_malicious"),
                "vt_suspicious":  vt.get("vt_suspicious"),
                "vt_reputation":  vt.get("vt_reputation"),
                "checked_at":     vt.get("checked_at"),
            }
        ops.append(UpdateOne({"domain": d}, {"$set": doc}, upsert=True))
        if len(ops) >= 1000:
            db[COLL].bulk_write(ops, ordered=False); ops = []
    if ops:
        db[COLL].bulk_write(ops, ordered=False)

    # 4. Write text files: pending list + invalid audit.
    pending = sorted(d for d, info in domains.items()
                     if is_valid_host(d) and d not in vt_by_domain
                     and not is_shared_infra(d) and not is_self_platform(d))
    invalid = sorted(d for d in domains if not is_valid_host(d))

    PENDING_OUT.write_text("\n".join(pending) + ("\n" if pending else ""))
    INVALID_OUT.write_text("\n".join(invalid) + ("\n" if invalid else ""))

    total = sum(status_counts.values())
    print()
    print(f"[done] donationscam.{COLL}")
    print(f"  total domains:                {total:,}")
    for s, n in status_counts.most_common():
        print(f"    {s:30s} {n:>6,} ({100*n/total:.1f}%)")
    print()
    print(f"  -> {PENDING_OUT.name}: {len(pending):,} domains ready for next VT round")
    print(f"  -> {INVALID_OUT.name}: {len(invalid):,} junk domains for audit")


if __name__ == "__main__":
    main()
