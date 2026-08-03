"""
Detector A — External Reputation
=================================
Pure third-party intel: VirusTotal on extracted domains + IPQS on
extracted emails and phones. No regex, no local heuristics — those
live in Detector E now.

Fire rule (per campaign):
  flag = True if ANY of:
    • any extracted domain has VT (malicious + suspicious) >= 2
    • any extracted email has IPQS fraud_score >= 85
    • any extracted phone has IPQS fraud_score >= 85

Inputs:
  ../../02_data_filtration/filtered_dataset.csv      campaign rows
  ../intel/campaign_contacts_llm.jsonl               LLM-extracted contacts
  ../intel/vt_domain_results.jsonl                   VT cache
  ../intel/ipqs_email_results.jsonl                  IPQS email cache
  ../intel/ipqs_phone_results.jsonl                  IPQS phone cache

Output:
  outputs/detector_A_flags.csv
    columns: url, flag, vt_max_score, ipqs_email_max, ipqs_phone_max,
             reasons (semicolon-separated)
"""
from __future__ import annotations
import csv
import json
import re
from pathlib import Path

import pandas as pd

ROOT  = Path(__file__).resolve().parent.parent.parent
DATA  = ROOT / "02_data_filtration" / "filtered_dataset.csv"
INTEL = ROOT / "03_detection" / "intel"
LLM_JSONL        = INTEL / "campaign_contacts_llm.jsonl"
VT_CACHE         = INTEL / "vt_domain_results.jsonl"
IPQS_EMAIL_CACHE = INTEL / "ipqs_email_results.jsonl"
IPQS_PHONE_CACHE = INTEL / "ipqs_phone_results.jsonl"
OUT = ROOT / "03_detection" / "detectors" / "outputs" / "detector_A_flags.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

VT_THRESHOLD   = 2          # mal + susp >= this trips A
IPQS_THRESHOLD = 85         # fraud_score >= this trips A


def normalize_domain(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = s.split("?")[0]
    s = s.split(":")[0]
    if s.startswith("www."): s = s[4:]
    return s


def load_jsonl_index(path: Path, key_fn) -> dict:
    out = {}
    if not path.exists():
        print(f"  [warn] {path.name} missing — skipping")
        return out
    with path.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = key_fn(d)
            if k: out[k] = d
    return out


def load_contacts() -> dict:
    """url -> {emails:[], phones:[], urls:[], ...}"""
    out = {}
    if not LLM_JSONL.exists():
        print(f"  [error] {LLM_JSONL} missing — Detector A cannot run")
        return out
    with LLM_JSONL.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError: continue
            if d.get("url"): out[d["url"]] = d
    return out


def vt_score(vt_rec: dict) -> int:
    """VT cache schema: top-level vt_malicious + vt_suspicious."""
    if not vt_rec: return 0
    try:
        return int(vt_rec.get("vt_malicious") or 0) + int(vt_rec.get("vt_suspicious") or 0)
    except (TypeError, ValueError):
        return 0


def ipqs_score(ipqs_rec: dict) -> int:
    """IPQS cache schema: result.fraud_score (nested)."""
    if not ipqs_rec: return 0
    result = ipqs_rec.get("result") or {}
    try:
        return int(result.get("fraud_score") or 0)
    except (TypeError, ValueError):
        return 0


def main():
    print("Detector A — External Reputation (VT + IPQS only)")
    df = pd.read_csv(DATA)
    print(f"  campaigns:                 {len(df):,}")

    contacts = load_contacts()
    print(f"  contact rows:              {len(contacts):,}")

    vt = load_jsonl_index(VT_CACHE,
                          lambda d: normalize_domain(d.get("domain") or d.get("id") or ""))
    ipqs_email = load_jsonl_index(IPQS_EMAIL_CACHE,
                                   lambda d: (d.get("email") or "").lower())
    ipqs_phone = load_jsonl_index(IPQS_PHONE_CACHE,
                                   lambda d: re.sub(r"\D", "", d.get("phone") or ""))
    print(f"  VT cache:                  {len(vt):,} domains")
    print(f"  IPQS email cache:          {len(ipqs_email):,} emails")
    print(f"  IPQS phone cache:          {len(ipqs_phone):,} phones")

    n_flagged = 0
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "flag", "vt_max_score", "ipqs_email_max",
                    "ipqs_phone_max", "reasons"])
        for _, row in df.iterrows():
            url = row["url"]
            rec = contacts.get(url) or {}
            reasons = []

            # VT on extracted domains (and bare-domain URLs)
            vt_max = 0
            for u in (rec.get("urls") or []):
                d = normalize_domain(u)
                if not d: continue
                s = vt_score(vt.get(d))
                if s > vt_max: vt_max = s
            if vt_max >= VT_THRESHOLD:
                reasons.append(f"vt_mal_susp={vt_max}")

            # IPQS on extracted emails
            ipqs_e_max = 0
            for e in (rec.get("emails") or []):
                s = ipqs_score(ipqs_email.get(e.lower()))
                if s > ipqs_e_max: ipqs_e_max = s
            if ipqs_e_max >= IPQS_THRESHOLD:
                reasons.append(f"ipqs_email={ipqs_e_max}")

            # IPQS on extracted phones
            ipqs_p_max = 0
            for p in (rec.get("phones") or []):
                s = ipqs_score(ipqs_phone.get(re.sub(r"\D", "", p)))
                if s > ipqs_p_max: ipqs_p_max = s
            if ipqs_p_max >= IPQS_THRESHOLD:
                reasons.append(f"ipqs_phone={ipqs_p_max}")

            flag = bool(reasons)
            if flag: n_flagged += 1
            w.writerow([url, int(flag), vt_max, ipqs_e_max, ipqs_p_max,
                        ";".join(reasons)])

    pct = 100 * n_flagged / len(df) if len(df) else 0
    print(f"\nflagged by Detector A:       {n_flagged:,}  ({pct:.2f}%)")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
