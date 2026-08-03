"""Flag campaigns whose extracted emails use a disposable / throwaway domain.

Reads:
  ../intel/disposable_email_domains.txt   blocklist
  ../intel/campaign_contacts_llm.jsonl    LLM-extracted emails per campaign

Writes:
  ../intel/email_quality_flags.csv
    url,has_disposable_email,disposable_count,sample_email
"""
from __future__ import annotations
import csv
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOCKLIST = HERE / "disposable_email_domains.txt"
CONTACTS  = HERE / "campaign_contacts_llm.jsonl"
OUT       = HERE / "email_quality_flags.csv"

EMAIL_RE = re.compile(r"^([a-z0-9._%+-]+)@([a-z0-9.-]+\.[a-z]{2,})$", re.I)


def load_blocklist() -> set[str]:
    domains = set()
    if not BLOCKLIST.exists():
        return domains
    for line in BLOCKLIST.open():
        s = line.strip().lower()
        if s and not s.startswith("#"):
            domains.add(s)
    return domains


def main():
    block = load_blocklist()
    print(f"loaded {len(block)} disposable-mail domains")
    if not CONTACTS.exists():
        print(f"missing: {CONTACTS}"); return

    n = n_flag = 0
    with CONTACTS.open() as fin, OUT.open("w", newline="") as fout:
        w = csv.writer(fout)
        w.writerow(["url", "has_disposable_email", "disposable_count",
                    "sample_email"])
        for line in fin:
            try:
                d = json.loads(line)
            except json.JSONDecodeError: continue
            n += 1
            url = d.get("url") or ""
            emails = d.get("emails") or []
            count = 0; sample = ""
            for e in emails:
                m = EMAIL_RE.match((e or "").strip())
                if not m: continue
                domain = m.group(2).lower()
                if domain in block or any(domain.endswith("." + b) for b in block):
                    count += 1
                    if not sample: sample = e
            flag = count > 0
            if flag: n_flag += 1
            w.writerow([url, int(flag), count, sample])
    print(f"campaigns scanned:  {n:,}")
    print(f"with disposable:    {n_flag:,}  ({100*n_flag/n:.2f}% if n else 0)")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
