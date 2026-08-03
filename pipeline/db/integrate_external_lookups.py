"""Integrate Harshita's verified VT/IPQS lookups into MongoDB and our state files.

Three integrations:

1. URL-level VT verdicts -> per-campaign field on campaigns collection:
     reputation.url_level_vt = [
        {url, domain, engines_flagged, total_engines, is_malicious}
     ]
     reputation.summary.any_url_level_vt_flagged: bool

2. URL-level VT-flagged URLs -> direct fraud evidence pointers on the
   matching campaigns:
     reputation.url_level_flagged_urls = [{url, engines_flagged, total_engines}]

3. IPQS-checked emails -> append her 284 verdicts to our state file
   (ipqs_email_results.jsonl) with provenance "url_level_pipeline" so future
   dedupe sees them as already-done. Optionally mirror onto matching
   campaigns under:
     reputation.email_ipqs_extra = [{email, fraud_score, ...}]

Idempotent. --dry-run reports counts without DB writes.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

ROOT     = Path(__file__).resolve().parent.parent.parent
EXTERNAL_DIR = ROOT / "harshita"
INTEL    = ROOT / "ccs2026" / "03_detection" / "intel"


def host_of(u):
    u = (u or "").lower().strip()
    u = re.sub(r"^https?://", "", u)
    return u.split("/")[0].split("?")[0].split("#")[0].strip(".")


def load_jsonl(p):
    if not p.exists(): return []
    out = []
    for l in open(p):
        try: out.append(json.loads(l))
        except: pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=== Loading Harshita's data ===")
    urls_unknown  = load_jsonl(EXTERNAL_DIR / "urls_unknown.jsonl")
    urls_priority = load_jsonl(EXTERNAL_DIR / "urls_priority.jsonl")
    urls_social   = load_jsonl(EXTERNAL_DIR / "urls_social.jsonl")
    flagged_vt    = load_jsonl(EXTERNAL_DIR / "step3_vt_flagged.jsonl")
    flagged_api   = load_jsonl(EXTERNAL_DIR / "step3_api_flagged.jsonl")
    emails_org    = load_jsonl(EXTERNAL_DIR / "emails_org.jsonl")

    all_url_records = urls_unknown + urls_priority + urls_social

    # ---------- Build per-campaign URL-VT aggregations ----------
    # Each Harshita URL record may belong to multiple campaigns.
    per_campaign_urlvt    = defaultdict(list)
    per_campaign_flagged  = defaultdict(list)

    n_url_records_with_vt = 0
    seen_url_per_campaign = defaultdict(set)
    for r in all_url_records:
        if not r.get("checked"): continue
        vt = r.get("vt_result") or {}
        if not vt: continue
        n_url_records_with_vt += 1

        url = r.get("value")
        d   = host_of(url)
        is_flagged = bool(vt.get("is_malicious"))

        for camp_url in (r.get("campaigns") or []):
            key = (camp_url, url)
            if key in seen_url_per_campaign[camp_url]:
                continue
            seen_url_per_campaign[camp_url].add(url)

            entry = {
                "url":              url,
                "domain":           d,
                "engines_flagged":  vt.get("engines_flagged", 0),
                "total_engines":    vt.get("total_engines", 0),
                "is_malicious":     is_flagged,
            }
            per_campaign_urlvt[camp_url].append(entry)
            if is_flagged:
                per_campaign_flagged[camp_url].append({
                    "url": url,
                    "engines_flagged": vt.get("engines_flagged", 0),
                    "total_engines":   vt.get("total_engines", 0),
                })

    # Cross-check: also fold step3_vt/api_flagged into per_campaign_flagged
    for r in flagged_vt + flagged_api:
        vt = r.get("vt_result") or {}
        if not (vt and vt.get("is_malicious")): continue
        url = r.get("value")
        for camp_url in (r.get("campaigns") or []):
            existing_flagged_urls = {f["url"] for f in per_campaign_flagged[camp_url]}
            if url not in existing_flagged_urls:
                per_campaign_flagged[camp_url].append({
                    "url": url,
                    "engines_flagged": vt.get("engines_flagged", 0),
                    "total_engines":   vt.get("total_engines", 0),
                })

    print(f"  url-level VT records in her data:    {n_url_records_with_vt:,}")
    print(f"  campaigns with at least one URL VT:  {len(per_campaign_urlvt):,}")
    print(f"  campaigns with at least one flagged: {len(per_campaign_flagged):,}")

    # ---------- Build per-campaign IPQS-email aggregations ----------
    per_campaign_emailipqs = defaultdict(list)
    her_ipqs_email_records = []   # for appending to state file

    for r in emails_org:
        if not r.get("checked"): continue
        ipqs = r.get("ipqs_result")
        if not ipqs: continue
        email = (r.get("value") or "").lower()
        if not email: continue

        # build a normalized state-file row
        her_ipqs_email_records.append({
            "email":       email,
            "checked_at":  datetime.now(timezone.utc).isoformat(),
            "ipqs_status": "ok",
            "fraud_score":   ipqs.get("fraud_score"),
            "is_valid":      ipqs.get("is_valid"),
            "is_disposable": ipqs.get("is_disposable"),
            "recent_abuse":  ipqs.get("recent_abuse"),
            "spam_trap":     ipqs.get("spam_trap"),
            "catch_all":     ipqs.get("catch_all"),
            "raw":           ipqs,
            "source":        "url_level_pipeline",
        })

        for camp_url in (r.get("campaigns") or []):
            per_campaign_emailipqs[camp_url].append({
                "email":         email,
                "fraud_score":   ipqs.get("fraud_score"),
                "is_disposable": ipqs.get("is_disposable"),
                "recent_abuse":  ipqs.get("recent_abuse"),
            })

    print(f"  IPQS-email records in her data:      {len(her_ipqs_email_records):,}")
    print(f"  campaigns with at least one IPQS:    {len(per_campaign_emailipqs):,}")

    # ---------- 1. Append her IPQS verdicts to our state file (idempotent) ----------
    state_file = INTEL / "ipqs_email_results.jsonl"
    existing_emails = set()
    if state_file.exists():
        for l in open(state_file):
            try: existing_emails.add((json.loads(l).get("email") or "").lower())
            except: pass
    to_append = [r for r in her_ipqs_email_records if r["email"] not in existing_emails]

    print(f"\n=== State file merge (ipqs_email_results.jsonl) ===")
    print(f"  existing entries:  {len(existing_emails):,}")
    print(f"  new from Harshita: {len(to_append):,}")

    if not args.dry_run and to_append:
        with open(state_file, "a") as fout:
            for r in to_append:
                fout.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  appended -> {state_file}")
    elif args.dry_run:
        print(f"  [dry-run] would append")

    # ---------- 2. Update MongoDB campaigns ----------
    db = get_db()

    all_camps = set(per_campaign_urlvt.keys()) | set(per_campaign_flagged.keys()) | set(per_campaign_emailipqs.keys())
    print(f"\n=== Mongo campaigns to update ===")
    print(f"  total unique campaigns:  {len(all_camps):,}")

    ops = []
    n_set_urlvt = n_set_flagged = n_set_emailipqs = 0
    for url in all_camps:
        update = {}
        if per_campaign_urlvt.get(url):
            update["reputation.url_level_vt"] = per_campaign_urlvt[url]
            n_flagged_here = sum(1 for e in per_campaign_urlvt[url] if e["is_malicious"])
            update["reputation.summary.n_url_level_checked"] = len(per_campaign_urlvt[url])
            update["reputation.summary.n_url_level_flagged"] = n_flagged_here
            update["reputation.summary.any_url_level_vt_flagged"] = n_flagged_here > 0
            n_set_urlvt += 1
        if per_campaign_flagged.get(url):
            update["reputation.url_level_flagged_urls"] = per_campaign_flagged[url]
            n_set_flagged += 1
        if per_campaign_emailipqs.get(url):
            update["reputation.email_ipqs_extra"] = per_campaign_emailipqs[url]
            n_set_emailipqs += 1
        if update:
            ops.append(UpdateOne({"url": url}, {"$set": update}))

    print(f"  campaigns getting url_level_vt:        {n_set_urlvt:,}")
    print(f"  campaigns getting url_level_flagged_urls:  {n_set_flagged:,}")
    print(f"  campaigns getting email_ipqs_extra:    {n_set_emailipqs:,}")

    if not args.dry_run and ops:
        # batch
        BATCH = 500
        n_written = 0; n_matched = 0
        for i in range(0, len(ops), BATCH):
            res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
            n_matched += res.matched_count
        print(f"  bulk_write matched: {n_matched:,} / {len(ops):,}")
    elif args.dry_run:
        print(f"  [dry-run] would write {len(ops):,} ops")

    # ---------- 3. Verify with quick mongosh-style queries ----------
    if not args.dry_run:
        print(f"\n=== Post-write verification ===")
        c1 = db.campaigns.count_documents({"reputation.url_level_vt.0": {"$exists": True}})
        c2 = db.campaigns.count_documents({"reputation.url_level_flagged_urls.0": {"$exists": True}})
        c3 = db.campaigns.count_documents({"reputation.summary.any_url_level_vt_flagged": True})
        c4 = db.campaigns.count_documents({"reputation.email_ipqs_extra.0": {"$exists": True}})
        print(f"  campaigns w/ url_level_vt:               {c1:,}")
        print(f"  campaigns w/ url_level_flagged_urls:         {c2:,}")
        print(f"  campaigns w/ any_url_level_vt_flagged:   {c3:,}")
        print(f"  campaigns w/ email_ipqs_extra:           {c4:,}")


if __name__ == "__main__":
    main()
