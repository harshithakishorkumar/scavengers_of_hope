"""Count how many distinct campaigns each crypto wallet / payment handle
appears in, then per campaign report the max reuse count of any handle it
contains.

Wallet reuse >= 3 (same address across 3+ campaigns from different
organizers) is a strong fraud signal — coordinated extraction, not
incidental sharing.

Reads:
  ../intel/campaign_contacts_llm.jsonl   payment_handles per campaign

Writes:
  ../intel/wallet_reuse_flags.csv
    url,wallet_reuse_max,reused_wallets (semicolon-separated)
  ../intel/wallet_reuse_summary.csv
    wallet,campaigns_count

Handles considered:
  • BTC, ETH, LTC, XMR wallet addresses
  • IBAN strings
  • $cashapp, @venmo, paypal.me/x tags

Excluded:
  • account_numbers labeled in description but not in the structured
    payment_handles field (those go through the regex layer in Detector E)
"""
from __future__ import annotations
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTACTS = HERE / "campaign_contacts_llm.jsonl"
OUT      = HERE / "wallet_reuse_flags.csv"
SUMMARY  = HERE / "wallet_reuse_summary.csv"

# Valid handle formats. Anything not matching one of these is dropped as noise
# (e.g., dollar amounts like "$50.00" or platform domains the LLM mislabeled).
VALID_HANDLE_PATTERNS = {
    "BTC":       re.compile(r"^(?:bc1[ac-hj-np-z02-9]{8,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$"),
    "ETH":       re.compile(r"^0x[a-fA-F0-9]{40}$"),
    "IBAN":      re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$"),
    "PayPal.me": re.compile(r"^(?:https?://)?(?:www\.)?paypal\.me/[\w\-.]+/?$", re.I),
    "CashApp":   re.compile(r"^\$[A-Z][\w]{2,20}$"),
    "Venmo":     re.compile(r"^@[\w.-]{3,30}$"),
}

# Reject patterns: anything matching is dropped before grouping.
REJECT_PATTERNS = [
    re.compile(r"^\$?\d+(?:[.,]\d{1,2})?\s*(?:usd|eur|gbp|\$)?$", re.I),   # $50.00 / 100 / $1,000
    re.compile(r"^(?:www\.)?[\w.-]+\.(?:org|com|net|io|co|us)/?$", re.I),  # bare domains
    re.compile(r"^[A-Z]{3,4}$"),                                            # currency codes
]


def classify(h: str) -> str | None:
    """Return wallet type, or None if not a valid handle."""
    s = (h or "").strip()
    if not s: return None
    if any(rx.match(s) for rx in REJECT_PATTERNS): return None
    for name, rx in VALID_HANDLE_PATTERNS.items():
        if rx.match(s): return name
    return None


def normalize(h: str) -> str:
    """Canonical form for grouping: lowercase, strip whitespace, strip trailing slash."""
    s = (h or "").strip().lower()
    if s.endswith("/"): s = s[:-1]
    return s


def main():
    if not CONTACTS.exists():
        print(f"missing: {CONTACTS}"); return

    REUSE_TRIP = 2   # fires when same wallet appears in >=2 campaigns

    # Pass 1: build wallet -> set of urls (with VALIDITY FILTER)
    by_wallet = defaultdict(set)
    by_url    = defaultdict(set)
    by_type   = defaultdict(int)
    n_rows = n_raw = n_kept = n_rejected = 0
    with CONTACTS.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError: continue
            n_rows += 1
            url = d.get("url")
            if not url: continue
            for h in (d.get("payment_handles") or []):
                n_raw += 1
                kind = classify(h)
                if kind is None:
                    n_rejected += 1
                    continue
                n_kept += 1
                by_type[kind] += 1
                norm = normalize(h)
                by_wallet[norm].add(url)
                by_url[url].add(norm)
    print(f"rows scanned:           {n_rows:,}")
    print(f"raw handles seen:       {n_raw:,}")
    print(f"  rejected as noise:    {n_rejected:,}  (dollar amounts, bare domains, etc.)")
    print(f"  kept as valid:        {n_kept:,}")
    print(f"  by type:              {dict(by_type)}")
    print(f"unique valid handles:   {len(by_wallet):,}")

    # Per-wallet reuse counts (write summary, sorted desc)
    rows = [(h, len(urls)) for h, urls in by_wallet.items()]
    rows.sort(key=lambda x: -x[1])
    with SUMMARY.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["wallet", "campaigns_count"])
        for h, n in rows:
            w.writerow([h, n])
    top_reuse = [r for r in rows if r[1] >= REUSE_TRIP]
    print(f"\nhandles reused >={REUSE_TRIP} campaigns: {len(top_reuse):,}")
    if top_reuse[:10]:
        print(f"top 10 reused (valid) handles:")
        for h, n in top_reuse[:10]:
            print(f"  {n:>5}  {h[:70]}")

    # Per-campaign max reuse
    n_flag = 0
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "wallet_reuse_max", "reused_wallets"])
        for url, handles in by_url.items():
            max_n = 0
            reused = []
            for h in handles:
                n = len(by_wallet[h])
                if n > max_n: max_n = n
                if n >= REUSE_TRIP: reused.append(f"{h}:{n}")
            if max_n >= REUSE_TRIP: n_flag += 1
            w.writerow([url, max_n, ";".join(reused)])

    print(f"\ncampaigns flagged by wallet-reuse (>={REUSE_TRIP}): {n_flag:,}")
    print(f"output:  {OUT}")
    print(f"summary: {SUMMARY}")


if __name__ == "__main__":
    main()
