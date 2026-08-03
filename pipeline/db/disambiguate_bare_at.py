"""Disambiguate bare-@ social_handles by reading the surrounding description.

For every social_handles entry where platform=="unknown" and the original raw
value was a bare @handle (no URL), this script:

  1. Locates the handle in the campaign description.
  2. Looks at a tight window (+/-60 chars), then a wider window (+/-200 chars).
  3. If exactly one platform-keyword (`instagram`, `telegram`, ...) appears in
     the window, assigns that platform and constructs the canonical URL.
  4. If multiple platforms or none, leaves the entry as unknown
     (Stage 2 LLM pass can handle these later).

Updates both `campaigns` and `llm_contacts` collections.
Writes an audit CSV to ccs2026/03_detection/intel/bare_at_audit.csv.

Run:
    python3 ccs2026/db/disambiguate_bare_at.py --dry-run
    python3 ccs2026/db/disambiguate_bare_at.py
"""
import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


# Match each platform on a few common spellings (incl. abbreviations).
# Order matters: more-specific patterns first to avoid "ig" matching inside
# "instagram" mid-token (we use \b boundaries to prevent that anyway).
PLATFORM_KEYWORDS = {
    "instagram": [r"\binstagram\b", r"\binsta\b", r"\big\b"],
    "twitter":   [r"\btwitter\b", r"\bx\.com\b"],
    "telegram":  [r"\btelegram\b", r"\btelega\b", r"\btg\b", r"\bt\.me\b"],
    "tiktok":    [r"\btik[\s\-]?tok\b"],
    "youtube":   [r"\byoutube\b", r"\byou\s?tube\b", r"\byt\b"],
    "facebook":  [r"\bfacebook\b", r"\bfb\b"],
    "linkedin":  [r"\blinkedin\b"],
    "snapchat":  [r"\bsnapchat\b", r"\bsnap\b"],
    "discord":   [r"\bdiscord\b"],
    "whatsapp":  [r"\bwhatsapp\b", r"\bwa\.me\b"],
    "pinterest": [r"\bpinterest\b"],
    "threads":   [r"\bthreads\b"],
    "bluesky":   [r"\bbluesky\b", r"\bbsky\b"],
    "github":    [r"\bgithub\b"],
    "reddit":    [r"\breddit\b", r"\br\/[\w]+\b"],
    "vimeo":     [r"\bvimeo\b"],
    "mastodon":  [r"\bmastodon\b"],
}

CANONICAL_HOST = {
    "instagram": "instagram.com", "twitter": "twitter.com",
    "telegram":  "t.me",          "tiktok":  "tiktok.com",
    "youtube":   "youtube.com",   "facebook": "facebook.com",
    "linkedin":  "linkedin.com",  "snapchat": "snapchat.com",
    "discord":   "discord.com",   "whatsapp": "wa.me",
    "pinterest": "pinterest.com", "threads":  "threads.net",
    "bluesky":   "bsky.app",      "github":   "github.com",
    "reddit":    "reddit.com",    "vimeo":    "vimeo.com",
    "mastodon":  "mastodon.social",
}


def disambiguate(user_id, description, max_window=200):
    """Return (platform, stage) or (None, reason).

    Strategy: find every occurrence of the @handle in the description, scan
    `max_window` chars on each side for platform keywords, and pick the
    keyword closest to the handle. Closest-wins handles "Twitter: @x ...
    later mention of Instagram" correctly, where simple any-keyword presence
    would be ambiguous.
    """
    if not description or not user_id:
        return None, "no_handle_in_desc"
    handle_re = re.compile(r"@?" + re.escape(user_id) + r"\b", re.IGNORECASE)
    matches = list(handle_re.finditer(description))
    if not matches:
        return None, "no_handle_in_desc"

    best = None  # (distance, platform)
    for m in matches:
        s = max(0, m.start() - max_window)
        e = min(len(description), m.end() + max_window)
        ctx = description[s:e]
        for plat, patterns in PLATFORM_KEYWORDS.items():
            for pat in patterns:
                for km in re.finditer(pat, ctx, re.IGNORECASE):
                    abs_start = s + km.start()
                    abs_end   = s + km.end()
                    dist = min(abs(m.start() - abs_end), abs(abs_start - m.end()))
                    if best is None or dist < best[0]:
                        best = (dist, plat)
    if best is None:
        return None, "no_platform_keyword"
    plat = best[1]
    stage = "tight" if best[0] <= 60 else "wide"
    return plat, stage


def best_window(description, user_id, size=60):
    """Tiny snippet around first match for the audit CSV."""
    if not description or not user_id:
        return ""
    m = re.search(r"@?" + re.escape(user_id) + r"\b", description, re.IGNORECASE)
    if not m:
        return ""
    s = max(0, m.start() - size)
    e = min(len(description), m.end() + size)
    return description[s:e].replace("\n", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit-csv",
                    default=str(Path(__file__).resolve().parent.parent
                                 / "03_detection" / "intel" / "bare_at_audit.csv"))
    args = ap.parse_args()

    db = get_db()
    audit_path = Path(args.audit_csv)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    n_docs = n_changed = 0
    n_bare = 0
    resolved = Counter()
    unresolved = Counter()
    by_platform = Counter()

    ops_campaigns = []
    ops_contacts = []

    with open(audit_path, "w", newline="") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["url", "user_id", "before_platform", "after_platform",
                     "stage", "context"])

        cur = db.campaigns.find(
            {"llm_contacts.social_handles.platform": "unknown"},
            {"_id": 1, "url": 1, "description": 1,
             "llm_contacts.social_handles": 1,
             "llm_contacts.social_handles_raw": 1},
        )

        for doc in cur:
            url = doc["url"]
            desc = doc.get("description") or ""
            sh = (doc.get("llm_contacts") or {}).get("social_handles") or []
            raw = (doc.get("llm_contacts") or {}).get("social_handles_raw") or []

            new_sh = []
            changed = False
            for entry, original in zip(sh, raw + [None] * max(0, len(sh) - len(raw))):
                if entry.get("platform") != "unknown":
                    new_sh.append(entry)
                    continue
                # Bare-@ test: original was a string starting with @
                is_bare_at = isinstance(original, str) and original.strip().startswith("@")
                if not is_bare_at or not entry.get("user_id"):
                    new_sh.append(entry)
                    continue

                n_bare += 1
                uid = entry["user_id"]
                plat, stage = disambiguate(uid, desc)
                ctx = best_window(desc, uid)
                if plat:
                    resolved[stage] += 1
                    by_platform[plat] += 1
                    new_entry = {
                        "platform": plat,
                        "user_id":  uid,
                        "url":      f"https://{CANONICAL_HOST[plat]}/{uid}",
                    }
                    new_sh.append(new_entry)
                    changed = True
                    w.writerow([url, uid, "unknown", plat, stage, ctx])
                else:
                    unresolved[stage] += 1
                    new_sh.append(entry)
                    w.writerow([url, uid, "unknown", "unknown", stage, ctx])

            if changed:
                n_changed += 1
                if not args.dry_run:
                    ops_campaigns.append(UpdateOne(
                        {"_id": doc["_id"]},
                        {"$set": {"llm_contacts.social_handles": new_sh}},
                    ))
                    ops_contacts.append(UpdateOne(
                        {"url": url},
                        {"$set": {"social_handles": new_sh}},
                    ))
                    if len(ops_campaigns) >= 500:
                        db.campaigns.bulk_write(ops_campaigns, ordered=False)
                        db.llm_contacts.bulk_write(ops_contacts, ordered=False)
                        ops_campaigns = []; ops_contacts = []
            n_docs += 1

        if ops_campaigns and not args.dry_run:
            db.campaigns.bulk_write(ops_campaigns, ordered=False)
            db.llm_contacts.bulk_write(ops_contacts, ordered=False)

    print(f"campaigns scanned (with any unknown handle): {n_docs:,}")
    print(f"bare-@ entries seen:                         {n_bare:,}")
    print(f"resolved (got a platform):                   {sum(resolved.values()):,}")
    print(f"  by stage:")
    for s, n in resolved.most_common():
        print(f"    {s:25s} {n:>5,}")
    print(f"  by platform:")
    for p, n in by_platform.most_common():
        print(f"    {p:14s} {n:>5,}")
    print(f"unresolved (still unknown):                  {sum(unresolved.values()):,}")
    print(f"  by reason:")
    for r, n in unresolved.most_common():
        print(f"    {r:25s} {n:>5,}")
    print(f"\naudit CSV: {audit_path}")
    if args.dry_run:
        print("[dry-run] no DB writes performed")


if __name__ == "__main__":
    main()
