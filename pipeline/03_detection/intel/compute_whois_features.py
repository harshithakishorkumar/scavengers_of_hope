"""Derive per-campaign WHOIS-based features from whois_results.jsonl.

For each campaign, compute:
  • domain_age_min_days   min age (days) of any extracted domain
  • has_young_domain      bool: any domain registered < 30 days before scrape
  • whois_privacy         bool: any extracted domain uses privacy guard

Reads:
  ../intel/whois_results.jsonl
  ../intel/campaign_contacts_llm.jsonl

Writes:
  ../intel/whois_features.csv
    url,domain_age_min_days,has_young_domain,whois_privacy,domains_checked
"""
from __future__ import annotations
import csv
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTACTS = HERE / "campaign_contacts_llm.jsonl"
WHOIS    = HERE / "whois_results.jsonl"
OUT      = HERE / "whois_features.csv"

YOUNG_THRESHOLD_DAYS = 30


def normalize_domain(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = s.split("?")[0]
    s = s.split(":")[0]
    if s.startswith("www."): s = s[4:]
    return s


def main():
    if not WHOIS.exists():
        print(f"missing: {WHOIS}  — run whois_lookup.py first."); return
    if not CONTACTS.exists():
        print(f"missing: {CONTACTS}"); return

    whois_by_domain = {}
    with WHOIS.open() as fh:
        for line in fh:
            try: d = json.loads(line)
            except json.JSONDecodeError: continue
            if d.get("domain"):
                whois_by_domain[d["domain"]] = d
    print(f"WHOIS records loaded: {len(whois_by_domain):,}")

    n = n_young = n_priv = 0
    with CONTACTS.open() as fin, OUT.open("w", newline="") as fout:
        w = csv.writer(fout)
        w.writerow(["url", "domain_age_min_days", "has_young_domain",
                    "whois_privacy", "domains_checked"])
        for line in fin:
            try: d = json.loads(line)
            except json.JSONDecodeError: continue
            n += 1
            url = d.get("url") or ""
            ages, priv = [], False
            checked = 0
            for u in (d.get("urls") or []):
                dom = normalize_domain(u)
                if not dom: continue
                rec = whois_by_domain.get(dom)
                if not rec or not rec.get("ok"): continue
                checked += 1
                if "age_days" in rec and rec["age_days"] is not None:
                    ages.append(int(rec["age_days"]))
                if rec.get("privacy_guard"): priv = True
            age_min = min(ages) if ages else ""
            young = bool(ages) and min(ages) < YOUNG_THRESHOLD_DAYS
            if young: n_young += 1
            if priv: n_priv += 1
            w.writerow([url, age_min, int(young), int(priv), checked])

    print(f"campaigns scanned:    {n:,}")
    print(f"young domain (<30d):  {n_young:,}")
    print(f"WHOIS privacy:        {n_priv:,}")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
