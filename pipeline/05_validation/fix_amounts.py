"""Re-extract goal_amount / amount_raised for the 200 validation items.

The original v4 scrape mis-mapped GoGetFunding's "23% Funded" and "18 Donors"
fields onto goal_amount and amount_raised respectively, leaving items like
`v159` showing $23 goal / $18 raised instead of $8,000 goal / $1,911 raised.
This script repairs those rows by parsing the description text directly using
platform-aware regex.

Idempotent: only updates rows where the parsed value differs from stored.

Usage:
    SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...
    python fix_amounts.py
"""
import os, re, sys
from supabase import create_client


# ---- platform-aware extraction patterns -------------------------------------

# GoGetFunding active campaigns
#   "US$1,911.00 raised of $8,000.00 goal"
#   "€330.00 raised of €3,000.00 goal"
GGF_RAISED_GOAL = re.compile(
    r"(?:US)?[\$€£]\s*([\d,]+(?:\.\d+)?)\s+raised\s+of\s+(?:US)?[\$€£]?\s*([\d,]+(?:\.\d+)?)\s+goal",
    re.IGNORECASE,
)

# GoGetFunding paused / inactive campaigns
#   "US$1,055.00 Donated So Far"   (raised only, no goal)
GGF_DONATED = re.compile(
    r"(?:US)?[\$€£]\s*([\d,]+(?:\.\d+)?)\s+Donated\s+So\s+Far",
    re.IGNORECASE,
)

# Spotfund / similar:
#   "$X raised toward $Y goal"
#   "$X of $Y raised"
SPOTFUND_VARIANTS = [
    re.compile(r"[\$€£]\s*([\d,]+(?:\.\d+)?)\s+raised\s+toward\s+(?:[\$€£])?\s*([\d,]+(?:\.\d+)?)\s+goal", re.I),
    re.compile(r"[\$€£]\s*([\d,]+(?:\.\d+)?)\s+of\s+[\$€£]?\s*([\d,]+(?:\.\d+)?)\s+raised", re.I),
]

# GoGetFunding donor count:
#   "...23% Funded 18 Donors..."
#   "...3 Donors..." (when no goal/percentage shown)
DONOR_PATTERNS = [
    re.compile(r"\b(\d{1,5})\s+Donors?\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,5})\s+donors?\s+have\s+contributed\b", re.IGNORECASE),
]


def parse_donors(desc: str) -> int | None:
    if not desc:
        return None
    for pat in DONOR_PATTERNS:
        m = pat.search(desc)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def parse_goal_raised(desc: str) -> tuple[float | None, float | None]:
    """Try every known pattern; return (goal, raised) or (None, None)."""
    if not desc:
        return None, None

    m = GGF_RAISED_GOAL.search(desc)
    if m:
        return _to_float(m.group(2)), _to_float(m.group(1))   # goal, raised

    for pat in SPOTFUND_VARIANTS:
        m = pat.search(desc)
        if m:
            return _to_float(m.group(2)), _to_float(m.group(1))

    m = GGF_DONATED.search(desc)
    if m:
        # Only raised is exposed; leave goal alone.
        return None, _to_float(m.group(1))

    return None, None


def main():
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not sb_url or not sb_key:
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required.")
        sys.exit(1)

    sb = create_client(sb_url, sb_key)
    items = sb.table("items").select(
        "item_id,platform,goal_amount,amount_raised,donor_count,description"
    ).execute().data
    print(f"loaded {len(items)} items")

    fixed = 0
    donors_filled = 0
    no_pattern = 0
    plat_counts: dict[str, int] = {}

    for it in items:
        desc = it.get("description") or ""
        new_goal, new_raised = parse_goal_raised(desc)
        new_donors = parse_donors(desc)

        if new_goal is None and new_raised is None and new_donors is None:
            no_pattern += 1
            continue

        update = {}
        cur_goal   = it.get("goal_amount")
        cur_raised = it.get("amount_raised")
        cur_donors = it.get("donor_count")

        if new_goal is not None and (cur_goal is None or abs(float(cur_goal) - new_goal) > 0.5):
            update["goal_amount"] = new_goal
        if new_raised is not None and (cur_raised is None or abs(float(cur_raised) - new_raised) > 0.5):
            update["amount_raised"] = new_raised
        if new_donors is not None and cur_donors != new_donors:
            update["donor_count"] = new_donors

        if not update:
            continue

        sb.table("items").update(update).eq("item_id", it["item_id"]).execute()
        fixed += 1
        plat_counts[it["platform"]] = plat_counts.get(it["platform"], 0) + 1
        if "donor_count" in update:
            donors_filled += 1
        if fixed <= 8:
            parts = []
            if "goal_amount"  in update: parts.append(f"goal {cur_goal}->{update['goal_amount']}")
            if "amount_raised" in update: parts.append(f"raised {cur_raised}->{update['amount_raised']}")
            if "donor_count" in update: parts.append(f"donors {cur_donors}->{update['donor_count']}")
            print(f"  {it['item_id']} ({it['platform']:>14}): {', '.join(parts)}")

    print(f"\nrepaired: {fixed} items")
    print(f"  by platform: {plat_counts}")
    print(f"  donor counts filled: {donors_filled}")
    print(f"no parseable pattern (left as-is): {no_pattern}")


if __name__ == "__main__":
    main()
