"""Build a single per-campaign collection that joins:
  - filtered    (campaign metadata, title, description, organizer, ...)
  - llm_contacts   (cleaned emails/phones/payment_handles/social_handles/urls/names/locations)
  - vt_results     (domain reputation results for every domain referenced)
  - ipqs_email_results / ipqs_phone_results (fraud-score lookups)

Output: donationscam.campaigns
Each doc carries the full picture of one campaign so terminal queries do not
need to JOIN four collections.

Schema example:
  {
    url, platform, title, description, ..., needs_detectors,
    llm_contacts: { emails, phones, payment_handles[{kind,value}], ... },
    reputation: {
       vt_lookups:         [{domain, flagged, vt_malicious, vt_suspicious, vt_reputation}],
       ipqs_email_lookups: [{email,  flagged, fraud_score, disposable, valid}],
       ipqs_phone_lookups: [{phone,  flagged, fraud_score, line_type}],
       summary: {any_vt_flagged, any_ipqs_email_flagged, any_ipqs_phone_flagged,
                 n_domains_checked, n_domains_flagged,
                 n_emails_checked,  n_emails_flagged,
                 n_phones_checked,  n_phones_flagged}
    }
  }

Run:
    python3 ccs2026/db/build_campaigns_full.py
    python3 ccs2026/db/build_campaigns_full.py --drop
"""
import argparse
import re
import sys
from pathlib import Path

from pymongo import ASCENDING, UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from db_util import is_shared_infra, is_self_platform

COLL = "campaigns"
BATCH = 500
DIGITS_RE = re.compile(r"\d+")


def host_of(url):
    s = (url or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].rstrip(".")
    return s if "." in s and len(s) >= 4 else None


def email_domain(e):
    if "@" not in e:
        return None
    d = e.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return d if "." in d and len(d) >= 4 else None


def phone_digits(p):
    d = "".join(DIGITS_RE.findall(p or ""))
    return d if 7 <= len(d) <= 15 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true")
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        db[COLL].drop()
        print(f"[drop] {COLL}")

    db[COLL].create_index([("url", ASCENDING)], unique=True, name="url_unique")
    db[COLL].create_index([("platform", ASCENDING)], name="platform_idx")
    db[COLL].create_index([("reputation.summary.any_vt_flagged", ASCENDING)], name="vt_flagged_idx")
    db[COLL].create_index([("reputation.summary.any_ipqs_email_flagged", ASCENDING)], name="ipqs_email_flagged_idx")
    db[COLL].create_index([("reputation.summary.any_ipqs_phone_flagged", ASCENDING)], name="ipqs_phone_flagged_idx")
    db[COLL].create_index([("llm_contacts.payment_handles.kind", ASCENDING)], name="payment_kind_idx")

    print("[load] vt_results into memory ...")
    vt = {}
    for d in db.vt_results.find({}, {"_id": 0}):
        vt[d["domain"]] = d
    print(f"  {len(vt):,} VT records")

    print("[load] ipqs_email_results into memory ...")
    ipe = {}
    for d in db.ipqs_email_results.find({}, {"_id": 0}):
        ipe[d["email"]] = d
    print(f"  {len(ipe):,} IPQS email records")

    print("[load] ipqs_phone_results into memory ...")
    ipp = {}
    for d in db.ipqs_phone_results.find({}, {"_id": 0}):
        ipp[d["phone"]] = d
    print(f"  {len(ipp):,} IPQS phone records")

    print("[load] llm_contacts into memory ...")
    contacts = {}
    for d in db.llm_contacts.find({}, {"_id": 0}):
        contacts[d["url"]] = d
    print(f"  {len(contacts):,} contact records")

    print("[stream] filtered ...")
    n_read = 0
    n_with_vt_flag = n_with_ipqs_email_flag = n_with_ipqs_phone_flag = 0
    ops = []
    for camp in db.filtered.find({}, {"_id": 0}):
        url = camp["url"]
        n_read += 1
        c = contacts.get(url, {})
        emails  = c.get("emails")  or []
        phones  = c.get("phones")  or []
        urls    = c.get("urls")    or []
        socials = c.get("social_handles") or []
        payments = c.get("payment_handles") or []

        # Build domain set: urls + email-domains + social-handle paths + payment URL hosts
        domains = set()
        for u in urls:
            d = host_of(u)
            if d: domains.add(d)
        for e in emails:
            d = email_domain(e)
            if d: domains.add(d)
        for s in socials:
            if "/" in s:
                d = host_of(s)
                if d: domains.add(d)
        for p in payments:
            v = p.get("value") if isinstance(p, dict) else p
            if v and "/" in str(v):
                d = host_of(str(v))
                if d: domains.add(d)

        vt_lookups, ipqs_email_lookups, ipqs_phone_lookups = [], [], []
        for d in sorted(domains):
            if d in vt:
                v = vt[d]
                vt_lookups.append({
                    "domain":           d,
                    "flagged":          bool(v.get("flagged")),
                    "is_shared_infra":  is_shared_infra(d),
                    "is_self_platform": is_self_platform(d),
                    "vt_reputation":    v.get("vt_reputation"),
                    "vt_malicious":     v.get("vt_malicious"),
                    "vt_suspicious":    v.get("vt_suspicious"),
                })
        for e in emails:
            r = ipe.get(e.strip().lower())
            if r:
                res = r.get("result") or {}
                ipqs_email_lookups.append({
                    "email":       e,
                    "flagged":     bool(r.get("flagged")),
                    "fraud_score": res.get("fraud_score"),
                    "disposable":  res.get("disposable"),
                    "valid":       res.get("valid"),
                })
        for p in phones:
            d = phone_digits(p)
            if d and d in ipp:
                r = ipp[d]
                res = r.get("result") or {}
                ipqs_phone_lookups.append({
                    "phone":       d,
                    "flagged":     bool(r.get("flagged")),
                    "fraud_score": res.get("fraud_score"),
                    "line_type":   res.get("line_type"),
                })

        any_vt_raw = any(x["flagged"] for x in vt_lookups)
        any_vt     = any(x["flagged"] and not x["is_shared_infra"] and not x["is_self_platform"]
                          for x in vt_lookups)
        any_email  = any(x["flagged"] for x in ipqs_email_lookups)
        any_phone  = any(x["flagged"] for x in ipqs_phone_lookups)
        if any_vt:    n_with_vt_flag += 1
        if any_email: n_with_ipqs_email_flag += 1
        if any_phone: n_with_ipqs_phone_flag += 1

        doc = {
            **camp,
            "llm_contacts": {k: c.get(k) or [] for k in
                             ("emails","phones","payment_handles","social_handles",
                              "urls","names","locations")},
            "reputation": {
                "vt_lookups":         vt_lookups,
                "ipqs_email_lookups": ipqs_email_lookups,
                "ipqs_phone_lookups": ipqs_phone_lookups,
                "summary": {
                    "any_vt_flagged":         any_vt,
                    "any_vt_flagged_raw":     any_vt_raw,
                    "any_ipqs_email_flagged": any_email,
                    "any_ipqs_phone_flagged": any_phone,
                    "n_domains_checked":      len(vt_lookups),
                    "n_domains_flagged":      sum(1 for x in vt_lookups
                                                     if x["flagged"]
                                                     and not x["is_shared_infra"]
                                                     and not x["is_self_platform"]),
                    "n_domains_flagged_raw":  sum(1 for x in vt_lookups if x["flagged"]),
                    "n_emails_checked":       len(ipqs_email_lookups),
                    "n_emails_flagged":       sum(1 for x in ipqs_email_lookups if x["flagged"]),
                    "n_phones_checked":       len(ipqs_phone_lookups),
                    "n_phones_flagged":       sum(1 for x in ipqs_phone_lookups if x["flagged"]),
                },
            },
        }
        ops.append(UpdateOne({"url": url}, {"$set": doc}, upsert=True))
        if len(ops) >= BATCH:
            db[COLL].bulk_write(ops, ordered=False)
            ops = []
            if n_read % 10000 == 0:
                print(f"  {n_read:,} processed")
    if ops:
        db[COLL].bulk_write(ops, ordered=False)

    total = db[COLL].count_documents({})
    print()
    print(f"[done] donationscam.{COLL}")
    print(f"  total docs:                     {total:,}")
    print(f"  with any_vt_flagged:            {n_with_vt_flag:,} ({100*n_with_vt_flag/total:.2f}%)")
    print(f"  with any_ipqs_email_flagged:    {n_with_ipqs_email_flag:,} ({100*n_with_ipqs_email_flag/total:.2f}%)")
    print(f"  with any_ipqs_phone_flagged:    {n_with_ipqs_phone_flag:,} ({100*n_with_ipqs_phone_flag/total:.2f}%)")


if __name__ == "__main__":
    main()
