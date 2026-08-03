"""
Detector A (heuristic arm) — Heuristic Signals
================================
All local-only signals derived from campaign description + extracted artifacts.
No third-party API calls (those live in Detector A).

Six sub-signals:
  E.1  hr_pattern_count >= 2         16 hr_* regex patterns on description
  E.2  redirection_flagged           off-platform URL chain
  E.3  disposable_email              extracted email uses throwaway domain
  E.4  wallet_reuse_max >= 3         same wallet address across >=3 campaigns
  E.5  has_young_domain              extracted domain registered < 30 days ago
  E.6  whois_privacy + >=1 hr_*      privacy guard alone is weak; combo only

Fire rule (per campaign):
  flag = True if ANY of {E.1, E.2, E.3, E.4, E.5, E.6} trips.

Inputs (all under ../intel/, all optional — missing ones silently skipped):
  campaign_contacts_llm.jsonl    (required, source of extracted artifacts)
  redirection_flags.csv           (optional, from existing redirection scanner)
  email_quality_flags.csv         (from compute_email_quality.py)
  wallet_reuse_flags.csv          (from compute_wallet_reuse.py)
  whois_features.csv              (from compute_whois_features.py)

Plus hr_* regex patterns applied in-line on filtered_dataset.csv descriptions.

Output:
  outputs/detector_E_flags.csv
    columns: url, flag, hr_pattern_count, redirection, disposable_email,
             wallet_reuse_max, has_young_domain, whois_privacy,
             fired_signals (semicolon-separated)
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
CONTACTS    = INTEL / "campaign_contacts_llm.jsonl"
REDIRECTION = INTEL / "redirection_flags.csv"
EMAIL_QUAL  = INTEL / "email_quality_flags.csv"
WALLET      = INTEL / "wallet_reuse_flags.csv"
WHOIS_FEAT  = INTEL / "whois_features.csv"
OUT = ROOT / "03_detection" / "detectors" / "outputs" / "detector_E_flags.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

WALLET_REUSE_TRIP = 2        # E.4 fires at this many shared campaigns
                              # (lowered from 3 — same wallet across 2 different
                              # organizers' campaigns is already coordination-shaped)
HR_PATTERN_TRIP   = 2        # E.1 fires at this many distinct hr_* patterns


# ---------- 16 hr_* regex patterns (Move-1 tightened set) -------------------
HR_PATTERNS = {
    "hr_paypal_me":          re.compile(r"\bpaypal\.me/[\w\-.]+", re.I),
    "hr_venmo_handle":       re.compile(r"(?<![\w])@[\w.-]{3,30}\b.{0,80}\bvenmo\b|\bvenmo[: ]+@?[\w.-]{3,30}", re.I),
    "hr_cashapp_tag":        re.compile(r"\$[A-Z][\w]{2,20}\b.{0,80}\bcash\s?app\b|\bcash\s?app[: ]+\$?[\w]{3,20}", re.I),
    "hr_zelle":              re.compile(r"\bzelle\b.{0,40}\b(?:\d{10}|[\w.+-]+@[\w.-]+)", re.I),
    "hr_iban_literal":       re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b"),
    "hr_btc_literal":        re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{8,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"),
    "hr_eth_literal":        re.compile(r"\b0x[a-fA-F0-9]{40}\b"),
    "hr_wire_transfer":      re.compile(r"\bwire\s+transfer\b|\bwire\s+the\s+(?:money|funds|amount)\b", re.I),
    "hr_western_union":      re.compile(r"\bwestern\s*union\b", re.I),
    "hr_moneygram":          re.compile(r"\bmoneygram\b", re.I),
    "hr_dm_for_payment":     re.compile(r"\b(?:dm|pm|message|email)\s+me\b[^.]{0,40}\b(?:donate|payment|account|details|info)\b", re.I),
    "hr_telegram_pivot":     re.compile(r"\btelegram\b.{0,40}@[\w.]{3,30}|t\.me/[\w.]{3,30}", re.I),
    "hr_whatsapp_pivot":     re.compile(r"\bwhatsapp\b.{0,40}(?:\+?\d{7,15}|@[\w.]{3,30})", re.I),
    "hr_signal_pivot":       re.compile(r"\bsignal\b.{0,40}\+?\d{7,15}", re.I),
    "hr_send_directly":      re.compile(r"\b(?:send|prefer)\b[^.]{0,40}\b(?:directly|outside)\b[^.]{0,40}\b(?:account|paypal|venmo|cash\s?app|zelle)\b", re.I),
    "hr_contact_for_info":   re.compile(r"\bcontact\s+(?:me|us)\b[^.]{0,40}\b(?:account|payment|details|info|verify|proof)\b", re.I),
}


def load_csv_index(path: Path, key="url") -> dict:
    if not path.exists():
        print(f"  [info] {path.name} missing — skipping that signal")
        return {}
    out = {}
    with path.open() as fh:
        rdr = csv.DictReader(fh)
        for row in rdr:
            k = row.get(key)
            if k: out[k] = row
    return out


def count_hr_patterns(text: str) -> int:
    if not text: return 0
    n = 0
    for rx in HR_PATTERNS.values():
        if rx.search(text):
            n += 1
    return n


def main():
    print("Detector A (heuristic arm) — Heuristic Signals")
    df = pd.read_csv(DATA)
    print(f"  campaigns:               {len(df):,}")

    redir = load_csv_index(REDIRECTION)
    email_q = load_csv_index(EMAIL_QUAL)
    wallet = load_csv_index(WALLET)
    whois_f = load_csv_index(WHOIS_FEAT)
    print(f"  redirection flags:       {len(redir):,}")
    print(f"  email-quality rows:      {len(email_q):,}")
    print(f"  wallet-reuse rows:       {len(wallet):,}")
    print(f"  WHOIS feature rows:      {len(whois_f):,}")

    fire_counts = {f"E.{i}": 0 for i in range(1, 7)}
    n_flag = 0

    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "flag", "hr_pattern_count", "redirection",
                    "disposable_email", "wallet_reuse_max",
                    "has_young_domain", "whois_privacy", "fired_signals"])
        for _, row in df.iterrows():
            url = row["url"]
            desc = row.get("description")
            title = row.get("title")
            desc = "" if not isinstance(desc, str) else desc
            title = "" if not isinstance(title, str) else title
            text = title + "\n" + desc

            hr_n = count_hr_patterns(text)

            r = redir.get(url) or {}
            redir_flag = bool(int(r.get("redirection_flagged",
                                        r.get("flagged", "0")) or 0))

            eq = email_q.get(url) or {}
            disposable = bool(int(eq.get("has_disposable_email", "0") or 0))

            wl = wallet.get(url) or {}
            wallet_max = int(wl.get("wallet_reuse_max", "0") or 0)

            wf = whois_f.get(url) or {}
            young = bool(int(wf.get("has_young_domain", "0") or 0))
            privacy = bool(int(wf.get("whois_privacy", "0") or 0))

            fired = []
            if hr_n >= HR_PATTERN_TRIP: fired.append("E.1"); fire_counts["E.1"] += 1
            if redir_flag:              fired.append("E.2"); fire_counts["E.2"] += 1
            if disposable:              fired.append("E.3"); fire_counts["E.3"] += 1
            if wallet_max >= WALLET_REUSE_TRIP: fired.append("E.4"); fire_counts["E.4"] += 1
            if young:                   fired.append("E.5"); fire_counts["E.5"] += 1
            if privacy and hr_n >= 1:   fired.append("E.6"); fire_counts["E.6"] += 1

            flag = bool(fired)
            if flag: n_flag += 1
            w.writerow([url, int(flag), hr_n, int(redir_flag), int(disposable),
                        wallet_max, int(young), int(privacy), ";".join(fired)])

    print(f"\nDetector E sub-signal fires:")
    for k, v in fire_counts.items():
        print(f"  {k}: {v:,}")
    pct = 100*n_flag/len(df) if len(df) else 0
    print(f"\nflagged by Detector A (heuristic arm) (ANY signal): {n_flag:,}  ({pct:.2f}%)")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
