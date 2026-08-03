"""Re-extract goal_amount / amount_raised / currency for validation items
from the *original* filtered_dataset_v4.csv source descriptions, with
broader currency-symbol support than fix_amounts.py.

Why this exists in addition to fix_amounts.py: the first cleaning pass
stripped the GoGetFunding "$X raised of $Y goal" widget text out of the
description column in Supabase, AND the original fix_amounts.py only
matched $/€/£. Items denominated in PHP (₱), JPY (¥), INR (₹), KRW (₩),
ILS (₪), THB (฿), VND (₫) etc.\\ were left with the buggy scrape (goal
holding the percentage, raised holding the donor count). This script
reads the original (uncleaned) description from filtered_dataset_v4.csv,
runs an extended regex, and updates Supabase.
"""
import os, re, sys
from pathlib import Path
import pandas as pd
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
V4   = ROOT / "02_data_filtration" / "filtered_dataset_v4.csv"

# Map currency-symbol → ISO code so we can store both consistently.
SYMBOL_TO_CODE = {
    "$": None,        # ambiguous; only override if symbol is more specific
    "US$": "USD",  "CA$": "CAD",  "AU$": "AUD",  "NZ$": "NZD",
    "HK$": "HKD",  "SG$": "SGD",
    "€": "EUR",  "£": "GBP",
    "¥": "JPY",       # also CNY but JPY is more common in our scrape
    "₱": "PHP",  "₹": "INR",  "₩": "KRW",  "₪": "ILS",
    "฿": "THB",  "₫": "VND",  "₦": "NGN",  "₴": "UAH",
}
SYMBOL_RE = "(?:US\\$|CA\\$|AU\\$|NZ\\$|HK\\$|SG\\$|\\$|€|£|¥|₱|₹|₩|₪|฿|₫|₦|₴)"

GGF_RAISED_GOAL = re.compile(
    rf"({SYMBOL_RE})\s*([\d,]+(?:\.\d+)?)\s+raised\s+of\s+(?:{SYMBOL_RE})?\s*([\d,]+(?:\.\d+)?)\s+goal",
    re.IGNORECASE,
)
GGF_DONATED = re.compile(
    rf"({SYMBOL_RE})\s*([\d,]+(?:\.\d+)?)\s+Donated\s+So\s+Far",
    re.IGNORECASE,
)
DONORS_RE = re.compile(r"\b(\d{1,5})\s+Donors?\b", re.IGNORECASE)


def _to_float(s):
    return float(s.replace(",", ""))


def parse(desc: str):
    if not desc:
        return None, None, None, None
    m = GGF_RAISED_GOAL.search(desc)
    if m:
        sym = m.group(1)
        return _to_float(m.group(3)), _to_float(m.group(2)), SYMBOL_TO_CODE.get(sym), None
    m = GGF_DONATED.search(desc)
    raised = _to_float(m.group(2)) if m else None
    sym    = m.group(1) if m else None
    return None, raised, SYMBOL_TO_CODE.get(sym) if sym else None, None


def main():
    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    print("loading source v4 CSV...")
    v4 = pd.read_csv(V4, low_memory=False, usecols=["url", "description", "currency"])
    desc_by_url     = dict(zip(v4["url"].astype(str), v4["description"].fillna("").astype(str)))
    currency_by_url = dict(zip(v4["url"].astype(str), v4["currency"].fillna("").astype(str)))

    items = sb.table("items").select(
        "item_id,url,goal_amount,amount_raised,currency,donor_count"
    ).execute().data
    print(f"loaded {len(items)} items from Supabase")

    fixed = 0
    fixed_currency = 0
    samples = []
    for it in items:
        url = it["url"]
        raw = desc_by_url.get(url, "")
        if not raw:
            continue
        new_goal, new_raised, sym_code, _ = parse(raw)

        # Donor count from raw text (more reliable than the cleaned text).
        m_d = DONORS_RE.search(raw)
        new_donors = int(m_d.group(1)) if m_d else None

        update = {}
        cur_goal   = float(it.get("goal_amount")   or 0)
        cur_raised = float(it.get("amount_raised") or 0)
        cur_donors = it.get("donor_count")

        if new_goal is not None and abs(cur_goal - new_goal) > 0.5:
            update["goal_amount"] = new_goal
        if new_raised is not None and abs(cur_raised - new_raised) > 0.5:
            update["amount_raised"] = new_raised
        if new_donors is not None and cur_donors != new_donors:
            update["donor_count"] = new_donors

        # Pick the right ISO currency code. Prefer regex-derived; fall back to
        # the v4 CSV's "currency" column.
        new_currency = sym_code or currency_by_url.get(url) or it.get("currency")
        if new_currency and new_currency != it.get("currency"):
            update["currency"] = new_currency
            fixed_currency += 1

        if not update:
            continue

        sb.table("items").update(update).eq("item_id", it["item_id"]).execute()
        fixed += 1
        if len(samples) < 10:
            parts = []
            for k in ("goal_amount", "amount_raised", "currency", "donor_count"):
                if k in update:
                    parts.append(f"{k}: {it.get(k)}->{update[k]}")
            samples.append((it["item_id"], ", ".join(parts)))

    print(f"\nrepaired {fixed} items   (currency code overridden on {fixed_currency})")
    for iid, parts in samples:
        print(f"  {iid}: {parts}")


if __name__ == "__main__":
    main()
