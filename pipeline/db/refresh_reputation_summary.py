"""Refresh reputation.summary aggregates per campaign.

The summary counters (n_domains_checked, n_domains_flagged, etc.) went stale
after the embedded reputation.{vt,ipqs_email,ipqs_phone}_lookups arrays were
dropped on 2026-05-08. This re-computes them from the current state
collections (db.vt_results, db.ipqs_email_results, db.ipqs_phone_results).

Thresholds:
  VT  flagged: vt_malicious + vt_suspicious >= 1   (any-engine)
  IPQS flagged: result.fraud_score >= 85

Idempotent. --dry-run for preview.
"""
import argparse
import sys
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from db_util import is_shared_infra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = get_db()

    # Load VT verdicts: domain -> (vt_malicious, vt_suspicious)
    print("loading vt_results...")
    vt = {}
    for r in db.vt_results.find({}, {"domain": 1, "vt_malicious": 1, "vt_suspicious": 1, "_id": 0}):
        d = (r.get("domain") or "").lower()
        if not d: continue
        vt[d] = (r.get("vt_malicious") or 0, r.get("vt_suspicious") or 0)
    print(f"  {len(vt):,} VT-verdict domains loaded")

    # Load IPQS email verdicts: email -> fraud_score
    print("loading ipqs_email_results...")
    ipqs_email = {}
    for r in db.ipqs_email_results.find({}, {"email": 1, "result.fraud_score": 1, "_id": 0}):
        e = (r.get("email") or "").lower()
        score = ((r.get("result") or {}).get("fraud_score"))
        if e and score is not None:
            ipqs_email[e] = score
    print(f"  {len(ipqs_email):,} IPQS email verdicts loaded")

    # Load IPQS phone verdicts: digits-only phone -> fraud_score
    print("loading ipqs_phone_results...")
    import re as _re_p
    def _digits(s): return _re_p.sub(r"\D+", "", s or "")
    ipqs_phone = {}
    for r in db.ipqs_phone_results.find({}, {"phone": 1, "result.fraud_score": 1, "_id": 0}):
        p = _digits(r.get("phone"))
        score = ((r.get("result") or {}).get("fraud_score"))
        if p and len(p) >= 7 and score is not None:
            ipqs_phone[p] = score
    print(f"  {len(ipqs_phone):,} IPQS phone verdicts loaded\n")

    # Iterate campaigns, compute summary, bulk update
    print("re-computing reputation.summary per campaign...")
    cur = db.campaigns.find({}, {
        "url": 1, "_id": 1,
        "llm_contacts.organizer.domains":         1,
        "llm_contacts.organizer.emails":          1,
        "llm_contacts.organizer.phones":          1,
        "identity_signals.email_norms":           1,
        "identity_signals.phone_norms":           1,
    })

    import re as _re
    def phone_digits(s):
        return _re.sub(r"\D+", "", s or "")

    ops = []
    n_total = n_any_vt = n_any_email = n_any_phone = 0
    for doc in cur:
        n_total += 1
        org = doc.get("llm_contacts", {}).get("organizer", {})
        domains = org.get("domains") or []
        # Lookup pool: union of raw organizer emails AND canonicalized email_norms
        # so we hit IPQS results regardless of which normalization was applied
        emails_raw  = [(e or "").lower().strip() for e in (org.get("emails") or [])]
        emails_norm = doc.get("identity_signals", {}).get("email_norms") or []
        emails = list({e for e in (emails_raw + emails_norm) if e})

        # Same for phones — include both raw digits and E.164 norm
        phones_raw  = [phone_digits(p) for p in (org.get("phones") or [])]
        phones_norm = [phone_digits(p) for p in (doc.get("identity_signals", {}).get("phone_norms") or [])]
        phones = list({p for p in (phones_raw + phones_norm) if p})

        # Raw (pre-skiplist) counts — kept for audit trail.
        n_dom_checked_raw = sum(1 for d in domains if d.lower() in vt)
        n_dom_flagged_raw = sum(1 for d in domains
                                if d.lower() in vt and (vt[d.lower()][0] + vt[d.lower()][1]) >= 1)
        # Domains. Exclude shared-infra hosts (forms.gle, goo.gl, social
        # platform homepages, payment-service homepages, etc.) — VT verdicts
        # on those are about other people's abuse of the platform, not about
        # this organizer.
        identity_domains = [d for d in domains if not is_shared_infra(d)]
        n_dom_checked = sum(1 for d in identity_domains if d.lower() in vt)
        n_dom_flagged = sum(1 for d in identity_domains
                            if d.lower() in vt and (vt[d.lower()][0] + vt[d.lower()][1]) >= 1)
        # Emails (try both raw and normalized forms)
        n_em_checked = sum(1 for e in emails if e in ipqs_email)
        n_em_flagged = sum(1 for e in emails if e in ipqs_email and ipqs_email[e] >= 85)
        # Phones (digit-only comparison)
        n_ph_checked = sum(1 for p in phones if p in ipqs_phone)
        n_ph_flagged = sum(1 for p in phones if p in ipqs_phone and ipqs_phone[p] >= 85)

        any_vt = n_dom_flagged > 0
        any_em = n_em_flagged > 0
        any_ph = n_ph_flagged > 0
        if any_vt: n_any_vt += 1
        if any_em: n_any_email += 1
        if any_ph: n_any_phone += 1

        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {
            "reputation.summary.any_vt_flagged":         any_vt,
            "reputation.summary.any_vt_flagged_raw":     n_dom_flagged_raw > 0,
            "reputation.summary.any_ipqs_email_flagged": any_em,
            "reputation.summary.any_ipqs_phone_flagged": any_ph,
            "reputation.summary.n_domains_checked":      n_dom_checked,
            "reputation.summary.n_domains_flagged":      n_dom_flagged,
            "reputation.summary.n_domains_checked_raw": n_dom_checked_raw,
            "reputation.summary.n_domains_flagged_raw": n_dom_flagged_raw,
            "reputation.summary.n_emails_checked":       n_em_checked,
            "reputation.summary.n_emails_flagged":       n_em_flagged,
            "reputation.summary.n_phones_checked":       n_ph_checked,
            "reputation.summary.n_phones_flagged":       n_ph_flagged,
        }}))

    print(f"\n=== summary (refreshed) ===")
    print(f"  campaigns processed:               {n_total:,}")
    print(f"  any_vt_flagged = true:             {n_any_vt:,}")
    print(f"  any_ipqs_email_flagged = true:     {n_any_email:,}")
    print(f"  any_ipqs_phone_flagged = true:     {n_any_phone:,}")

    if not args.dry_run and ops:
        BATCH = 500
        n_matched = 0
        for i in range(0, len(ops), BATCH):
            res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
            n_matched += res.matched_count
        print(f"\n  applied to mongo:                  {n_matched:,}")


if __name__ == "__main__":
    main()
