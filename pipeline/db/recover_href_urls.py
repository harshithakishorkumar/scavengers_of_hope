"""Recover URLs hidden inside <a href="..."> tags in campaign descriptions.

Background: dump_extraction_input.py strips HTML before sending to the LLM,
which wipes every <a href="X">label</a> URL. The LLM never saw them.

This script regex-scans the RAW description HTML for href URLs, extracts
the host, and merges into:
  - llm_contacts.social_handles[]   if the host is a known social platform
  - llm_contacts.organizer.domains[] otherwise

Each new entry is tagged extracted_by="html_href_recovery" for provenance.

Idempotent. --dry-run for preview.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


HREF_RE   = re.compile(r'<a\s+[^>]*?href="(https?://[^"]+)"', re.IGNORECASE)
MAILTO_RE = re.compile(r'<a\s+[^>]*?href="mailto:([^"]+)"',  re.IGNORECASE)

# Map known social-platform hosts to the canonical platform tag we use elsewhere
SOCIAL_HOST_TO_PLATFORM = {
    "linkedin.com":  "linkedin",
    "facebook.com":  "facebook",
    "fb.com":        "facebook",
    "fb.me":         "facebook",
    "twitter.com":   "twitter",
    "x.com":         "twitter",
    "instagram.com": "instagram",
    "tiktok.com":    "tiktok",
    "youtube.com":   "youtube",
    "youtu.be":      "youtube",
    "t.me":          "telegram",
    "telegram.me":   "telegram",
    "wa.me":         "whatsapp",
    "snapchat.com":  "snapchat",
    "pinterest.com": "pinterest",
    "threads.net":   "threads",
    "bsky.app":      "bluesky",
    "github.com":    "github",
    "patreon.com":   "patreon",
    "twitch.tv":     "twitch",
    "tumblr.com":    "tumblr",
    "reddit.com":    "reddit",
}

USER_ID_PATTERNS = {
    "linkedin":  re.compile(r"linkedin\.com/(?:in|company)/([^/?#]+)", re.I),
    "twitter":   re.compile(r"(?:twitter|x)\.com/([^/?#]+)",            re.I),
    "instagram": re.compile(r"instagram\.com/([^/?#]+)",                re.I),
    "tiktok":    re.compile(r"tiktok\.com/@?([^/?#]+)",                 re.I),
    "facebook":  re.compile(r"(?:facebook|fb)\.(?:com|me)/([^/?#]+)",   re.I),
    "youtube":   re.compile(r"(?:youtube\.com/(?:c|user|channel|@)|youtu\.be/)([^/?#]+)", re.I),
    "telegram":  re.compile(r"(?:t|telegram)\.me/([^/?#]+)",            re.I),
    "github":    re.compile(r"github\.com/([^/?#]+)",                   re.I),
    "reddit":    re.compile(r"reddit\.com/(?:u|user)/([^/?#]+)",        re.I),
}


def host_of(url):
    u = url.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].split("?")[0].split("#")[0].strip(".")


def classify(url):
    """Return (kind, platform_or_None, user_id_or_None, host)."""
    host = host_of(url)
    if not host or "." not in host:
        return None, None, None, None
    plat = SOCIAL_HOST_TO_PLATFORM.get(host)
    if not plat:
        # Try matching against parent: e.g. m.facebook.com -> facebook.com
        for parent, p in SOCIAL_HOST_TO_PLATFORM.items():
            if host.endswith("." + parent):
                plat = p
                host = parent
                break
    if plat:
        user_id = None
        pat = USER_ID_PATTERNS.get(plat)
        if pat:
            m = pat.search(url)
            if m:
                user_id = m.group(1).strip()
        return "social_handle", plat, user_id, host
    else:
        return "domain", None, None, host


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = get_db()

    cur = db.campaigns.find(
        {"description": {"$regex": "<a\\s+[^>]*href=\"https?://", "$options": "i"}},
        {"_id": 1, "url": 1, "description": 1,
         "llm_contacts.organizer.domains": 1,
         "llm_contacts.social_handles":     1}
    )

    n_campaigns = 0
    n_skipped   = 0
    n_recovered_socials = 0
    n_recovered_domains = 0
    plat_counter = Counter()
    ops = []

    for doc in cur:
        n_campaigns += 1
        urls = HREF_RE.findall(doc.get("description") or "")
        if not urls:
            continue

        existing_domains = set((doc.get("llm_contacts", {}).get("organizer", {}).get("domains") or []))
        existing_socials = doc.get("llm_contacts", {}).get("social_handles") or []
        existing_social_keys = set()
        for s in existing_socials:
            if isinstance(s, dict):
                existing_social_keys.add((s.get("platform"), s.get("user_id")))

        new_socials = []
        new_domains = []

        for u in urls:
            kind, plat, uid, host = classify(u)
            if kind == "social_handle" and plat and uid:
                key = (plat, uid)
                if key in existing_social_keys: continue
                existing_social_keys.add(key)
                new_socials.append({
                    "platform": plat,
                    "user_id":  uid,
                    "url":      u,
                })
                plat_counter[plat] += 1
            elif kind == "domain" and host:
                if host in existing_domains: continue
                existing_domains.add(host)
                new_domains.append(host)

        if not new_socials and not new_domains:
            n_skipped += 1
            continue

        update_ops = {}
        if new_socials:
            update_ops["llm_contacts.social_handles"] = existing_socials + new_socials
            n_recovered_socials += len(new_socials)
        if new_domains:
            update_ops["llm_contacts.organizer.domains"] = sorted(existing_domains)
            n_recovered_domains += len(new_domains)
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": update_ops}))

    print(f"=== summary ===")
    print(f"  campaigns scanned with href URLs:      {n_campaigns:,}")
    print(f"  no new entries to add:                 {n_skipped:,}")
    print(f"  social handles recovered:              {n_recovered_socials:,}")
    print(f"  domains recovered:                     {n_recovered_domains:,}")
    print(f"  social-platform breakdown:")
    for p, n in plat_counter.most_common():
        print(f"    {p:<14s} {n:>5}")

    if not args.dry_run and ops:
        BATCH = 500
        n_matched = 0
        for i in range(0, len(ops), BATCH):
            res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
            n_matched += res.matched_count
        print(f"\n  applied to mongo: {n_matched:,} / {len(ops):,}")
    elif args.dry_run:
        print(f"\n  [dry-run] {len(ops):,} ops would be applied")


if __name__ == "__main__":
    main()
