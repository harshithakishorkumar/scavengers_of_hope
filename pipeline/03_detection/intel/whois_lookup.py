"""WHOIS lookup for extracted domains. Caches results to whois_results.jsonl.

For each unique domain in campaign_contacts_llm.jsonl `urls` field, queries
a WHOIS source and records:
  • domain
  • creation_date  (when the domain was registered)
  • age_days       (days between creation_date and now)
  • registrar
  • privacy_guard  (bool: registrant info is privacy-protected)
  • raw_text       (full WHOIS response, truncated)

Resumable: skips domains already in cache.

Backend: python-whois (free public WHOIS servers). Slow (~1-2 req/sec) but
no API keys. For 10k domains expect 3-6 hours.

Install:
    pip install python-whois

Usage:
    python whois_lookup.py
"""
from __future__ import annotations
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTACTS = HERE / "campaign_contacts_llm.jsonl"
CACHE    = HERE / "whois_results.jsonl"

PRIVACY_MARKERS = [
    "redacted for privacy", "privacy service", "whoisguard", "whois guard",
    "domains by proxy", "data protected", "redacted by privacy",
    "perfect privacy", "withheld for privacy", "privacy@",
    "domains protected", "private whois", "contact privacy",
    "registrant: redacted", "registrant name: redacted",
    "gdpr masked", "non-public data has been removed",
]


def normalize_domain(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = s.split("?")[0]
    s = s.split(":")[0]
    if s.startswith("www."): s = s[4:]
    return s


def already_done() -> set[str]:
    done = set()
    if not CACHE.exists(): return done
    for line in CACHE.open():
        try:
            d = json.loads(line)
            if d.get("domain"): done.add(d["domain"])
        except json.JSONDecodeError: continue
    return done


def collect_domains() -> set[str]:
    domains = set()
    if not CONTACTS.exists():
        print(f"missing: {CONTACTS}"); return domains
    with CONTACTS.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError: continue
            for u in (d.get("urls") or []):
                dom = normalize_domain(u)
                if dom and "." in dom:
                    domains.add(dom)
    return domains


def lookup_one(domain: str):
    """Returns dict or None on failure. Uses python-whois."""
    try:
        import whois
    except ImportError:
        print("install python-whois first:  pip install python-whois")
        sys.exit(1)
    try:
        w = whois.whois(domain)
    except Exception as e:
        return {"domain": domain, "ok": False, "err": str(e)[:200]}

    out = {"domain": domain, "ok": True}
    # creation date — python-whois sometimes returns list
    cd = w.creation_date
    if isinstance(cd, list): cd = cd[0] if cd else None
    if cd:
        if isinstance(cd, str):
            try: cd = datetime.fromisoformat(cd.replace("Z", "+00:00"))
            except ValueError: cd = None
        if isinstance(cd, datetime):
            out["creation_date"] = cd.isoformat()
            now = datetime.now(timezone.utc)
            if cd.tzinfo is None: cd = cd.replace(tzinfo=timezone.utc)
            out["age_days"] = (now - cd).days

    reg = w.registrar
    if isinstance(reg, list): reg = reg[0] if reg else None
    out["registrar"] = str(reg) if reg else ""

    raw = (w.text or "").lower() if hasattr(w, "text") else str(w).lower()
    out["privacy_guard"] = any(m in raw for m in PRIVACY_MARKERS)
    out["raw_text"] = (w.text or "")[:2000] if hasattr(w, "text") else str(w)[:2000]
    return out


def main():
    domains = collect_domains()
    done = already_done()
    todo = sorted(domains - done)
    print(f"unique domains in corpus:  {len(domains):,}")
    print(f"already in cache:          {len(done):,}")
    print(f"to look up:                {len(todo):,}")
    if not todo: return

    t0 = time.time(); n = 0; n_err = 0
    with CACHE.open("a") as fh:
        for dom in todo:
            res = lookup_one(dom) or {"domain": dom, "ok": False, "err": "?"}
            fh.write(json.dumps(res) + "\n"); fh.flush()
            n += 1
            if not res.get("ok"): n_err += 1
            if n % 50 == 0:
                elapsed = time.time() - t0
                rate = n / elapsed if elapsed else 0
                eta = (len(todo) - n) / rate if rate else 0
                print(f"  [{n:>5,}/{len(todo):,}]  rate={rate:.1f}/sec  "
                      f"err={n_err}  eta={eta/60:.0f}min", flush=True)
            time.sleep(0.5)   # be polite to WHOIS servers

    print(f"\ndone. lookups={n}  errors={n_err}  time={(time.time()-t0)/60:.1f}min")
    print(f"cache: {CACHE}")


if __name__ == "__main__":
    main()
