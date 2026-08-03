"""Migrate llm_contacts.social_handles from flat strings to typed objects.

Output schema per entry:
    {"platform": "twitter" | ... | "unknown", "user_id": "name", "url": "..."}

Detection rules:
  - SLD-based platform map (handles all TLDs automatically: facebook.com,
    facebook.de, x.com -> twitter, youtu.be -> youtube, etc).
  - Strip leading @ and slashes from user_id for uniformity.
  - Drop self-referential junk (facebook.com/facebook, tiktok.com/TikTok).
  - Drop whitespace inside user_id.
  - Drop group/event/joinchat path prefixes (not user profiles).
  - Bare @handle: platform="unknown", user_id=handle, url=null.
  - Unrecognized host: platform="unknown", user_id=path, url=original string.

Migration:
  - Saves originals to llm_contacts.social_handles_raw (audit copy).
  - Replaces llm_contacts.social_handles with typed objects.
  - Updates both `campaigns` and `llm_contacts` collections.
  - Idempotent (skips if first entry is already a dict).

Run:
    python3 ccs2026/db/migrate_social_handles.py --dry-run         # preview
    python3 ccs2026/db/migrate_social_handles.py                   # apply
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


# Map SLD (second-level-domain) -> canonical platform name.
# Handles all TLDs since we key on the SLD, not the full host.
PLATFORM_BY_SLD = {
    "facebook":  "facebook",
    "fb":        "facebook",
    "instagram": "instagram",
    "instagr":   "instagram",      # instagr.am
    "twitter":   "twitter",
    "x":         "twitter",        # x.com
    "t":         "telegram",       # t.me
    "telegram":  "telegram",
    "telegra":   "telegram",       # telegra.ph
    "wa":        "whatsapp",       # wa.me
    "whatsapp":  "whatsapp",
    "tiktok":    "tiktok",
    "youtube":   "youtube",
    "youtu":     "youtube",        # youtu.be
    "linkedin":  "linkedin",
    "pinterest": "pinterest",
    "snapchat":  "snapchat",
    "discord":   "discord",        # discord.com / discord.gg
    "reddit":    "reddit",
    "github":    "github",
    "medium":    "medium",
    "vimeo":     "vimeo",
    "mastodon":  "mastodon",
    "threads":   "threads",        # threads.net
    "bsky":      "bluesky",        # bsky.app
}

CANONICAL_HOST = {
    "facebook":  "facebook.com",
    "instagram": "instagram.com",
    "twitter":   "twitter.com",
    "telegram":  "t.me",
    "whatsapp":  "wa.me",
    "tiktok":    "tiktok.com",
    "youtube":   "youtube.com",
    "linkedin":  "linkedin.com",
    "pinterest": "pinterest.com",
    "snapchat":  "snapchat.com",
    "discord":   "discord.com",
    "reddit":    "reddit.com",
    "github":    "github.com",
    "medium":    "medium.com",
    "vimeo":     "vimeo.com",
    "mastodon":  "mastodon.social",
    "threads":   "threads.net",
    "bluesky":   "bsky.app",
}

# Path prefixes that mean "not a user profile" -> drop entry.
NON_PROFILE_PREFIXES = (
    "events/", "groups/", "joinchat/", "watch", "video", "playlist",
    "live/", "marketplace/", "gaming/", "stories/", "reel/", "p/", "explore/",
    "hashtag/", "share/", "intent/", "search",
)

# Strings that mean "platform name leaked into user_id slot" -> drop.
SELF_REFERENTIAL = {
    "facebook", "fb", "facebook com", "instagram", "insta", "twitter", "x",
    "telegram", "whatsapp", "wa", "tiktok", "tik tok", "youtube", "yt",
    "linkedin", "pinterest", "pin", "snapchat", "snap", "discord", "reddit",
    "github", "mastodon", "threads", "bluesky", "bsky", "vimeo", "medium",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "tiktok.com", "linkedin.com", "snapchat.com", "discord.com", "reddit.com",
    "medium.com", "vimeo.com", "github.com", "pinterest.com",
}

# user_id that looks like a domain (has a dot and a TLD-like suffix) -> drop.
LOOKS_LIKE_DOMAIN_RE = re.compile(
    r"\.(?:com|org|net|de|co\.uk|io|me|app|gg|tv|info|biz|edu|gov|"
    r"lu|fr|it|ca|au|us|uk|in|es|nl|se|ch|at|be|jp|cn|br|ru|pl)$",
    re.IGNORECASE,
)

WHITESPACE_RE = re.compile(r"\s")
HOST_RE = re.compile(r"^[\w.\-]+$")
PROTO_RE = re.compile(r"^https?://", re.IGNORECASE)
WWW_RE = re.compile(r"^www\.", re.IGNORECASE)


def parse_social_handle(s):
    """Return (platform, user_id, url) or (None, None, None, drop_reason).

    Always returns 4 values: when drop_reason is None, the entry is kept;
    otherwise it explains why we dropped it.
    """
    if not isinstance(s, str):
        return None, None, None, "not_string"
    raw = s.strip()
    if not raw:
        return None, None, None, "empty"

    # Bare @handle case.
    if raw.startswith("@"):
        uid = raw.lstrip("@").strip()
        if not uid or len(uid) < 2 or WHITESPACE_RE.search(uid):
            return None, None, None, "bare_at_invalid"
        if uid.lower() in SELF_REFERENTIAL:
            return None, None, None, "bare_at_is_platform_name"
        return "unknown", uid, None, None

    # Strip protocol + www.
    s2 = PROTO_RE.sub("", raw)
    s2 = WWW_RE.sub("", s2)

    # Split host vs path.
    parts = s2.split("/", 1)
    host = parts[0].split("?")[0].split("#")[0].rstrip(".").lower()
    path = parts[1] if len(parts) > 1 else ""
    path = path.split("?")[0].split("#")[0].strip()

    if not host or "." not in host or not HOST_RE.match(host):
        return None, None, None, "no_valid_host"

    # SLD detection.
    host_parts = host.split(".")
    sld = host_parts[0]
    platform = PLATFORM_BY_SLD.get(sld, "unknown")

    # Drop non-profile path prefixes for known platforms.
    path_lower = path.lower()
    if platform != "unknown":
        for pref in NON_PROFILE_PREFIXES:
            if path_lower.startswith(pref):
                return None, None, None, f"non_profile_path:{pref.rstrip('/')}"

    # Extract user_id from path.
    user_id = path.lstrip("/").lstrip("@").rstrip("/")

    # Cut at first sub-slash for known platforms (we want the first segment),
    # but preserve LinkedIn category prefixes (in/, company/, school/) since
    # they disambiguate person vs company. Same for YouTube (c/, channel/, user/).
    if platform == "linkedin":
        first = user_id.split("/", 1)[0]
        if first in ("in", "company", "school", "showcase"):
            second = user_id.split("/", 2)
            if len(second) >= 2 and second[1]:
                user_id = f"{first}/{second[1].split('/')[0]}"
            else:
                return None, None, None, "linkedin_no_id_after_prefix"
        else:
            user_id = first
    elif platform == "youtube":
        first = user_id.split("/", 1)[0]
        if first in ("c", "channel", "user"):
            second = user_id.split("/", 2)
            if len(second) >= 2 and second[1]:
                user_id = f"{first}/{second[1].split('/')[0]}"
            else:
                return None, None, None, "youtube_no_id_after_prefix"
        else:
            user_id = first.lstrip("@")
    else:
        user_id = user_id.split("/", 1)[0]

    # If the LLM concatenated platform-name + "@" + actual handle into the
    # path (e.g. "TikTok@MOMWITHGIGGLES"), split on the embedded @ and keep
    # the right side as the actual handle.
    if "@" in user_id and not user_id.startswith("@"):
        parts = user_id.split("@", 1)
        if len(parts) == 2 and parts[1]:
            user_id = parts[1].split("/", 1)[0]

    if not user_id:
        return None, None, None, "no_user_id"
    if WHITESPACE_RE.search(user_id):
        return None, None, None, "whitespace_in_user_id"
    if user_id.lower() in SELF_REFERENTIAL:
        return None, None, None, "self_referential"
    # user_id that itself looks like a domain (e.g. youtube.com/www.cp-golf.com,
    # facebook.com/facebook.com) is bogus.
    if LOOKS_LIKE_DOMAIN_RE.search(user_id):
        return None, None, None, "user_id_looks_like_domain"
    if len(user_id) < 2:
        return None, None, None, "user_id_too_short"

    if platform == "unknown":
        url = "https://" + host + ("/" + path if path else "")
    else:
        canonical = CANONICAL_HOST.get(platform, host)
        url = f"https://{canonical}/{user_id}"

    return platform, user_id, url, None


def already_typed(arr):
    return arr and isinstance(arr[0], dict) and "platform" in arr[0]


def migrate_collection(db, coll_name, dry_run, limit, drops_counter, plat_counter, samples):
    coll = db[coll_name]
    # campaigns has `llm_contacts.social_handles` (nested);
    # llm_contacts has `social_handles` at the top level.
    nested = (coll_name == "campaigns")
    sh_field   = "llm_contacts.social_handles"     if nested else "social_handles"
    raw_field  = "llm_contacts.social_handles_raw" if nested else "social_handles_raw"

    cur = coll.find({}, {"_id": 1, sh_field: 1})
    if limit:
        cur = cur.limit(limit)

    n_docs = n_changed = n_already = 0
    n_in = n_out = 0
    ops = []

    for doc in cur:
        n_docs += 1
        sh = ((doc.get("llm_contacts") or {}).get("social_handles") if nested
              else doc.get("social_handles")) or []
        if not sh:
            continue
        if already_typed(sh):
            n_already += 1
            continue

        typed = []
        for s in sh:
            n_in += 1
            platform, uid, url, drop = parse_social_handle(s)
            if drop:
                drops_counter[drop] += 1
                continue
            typed.append({"platform": platform, "user_id": uid, "url": url})
            n_out += 1
            plat_counter[platform] += 1
            if len(samples[platform]) < 4:
                samples[platform].append((s, {"platform": platform, "user_id": uid, "url": url}))

        n_changed += 1
        if not dry_run:
            ops.append(UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {sh_field: typed, raw_field: sh}},
            ))
            if len(ops) >= 1000:
                coll.bulk_write(ops, ordered=False)
                ops = []

    if ops and not dry_run:
        coll.bulk_write(ops, ordered=False)

    return {"docs": n_docs, "changed": n_changed, "already_typed": n_already,
            "in": n_in, "out": n_out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, do not write to DB")
    ap.add_argument("--limit", type=int, default=0,
                    help="only scan first N docs per collection (0 = all)")
    ap.add_argument("--collection", default="both",
                    choices=("campaigns", "llm_contacts", "both"))
    args = ap.parse_args()

    db = get_db()
    targets = []
    if args.collection in ("campaigns", "both"):
        targets.append("campaigns")
    if args.collection in ("llm_contacts", "both"):
        targets.append("llm_contacts")

    drops = Counter()
    platforms = Counter()
    samples = {p: [] for p in list(PLATFORM_BY_SLD.values()) + ["unknown"]}

    for coll in targets:
        print(f"\n=== {coll} ===")
        stats = migrate_collection(db, coll, args.dry_run, args.limit, drops, platforms, samples)
        print(f"  docs scanned:     {stats['docs']:,}")
        print(f"  already typed:    {stats['already_typed']:,}")
        print(f"  docs changed:     {stats['changed']:,}")
        print(f"  entries in/out:   {stats['in']:,} -> {stats['out']:,}")

    print("\n=== drops ===")
    for k, v in drops.most_common():
        print(f"  {k:30s} {v:>6,}")

    print("\n=== platforms after typing ===")
    for p, n in platforms.most_common():
        print(f"  {p:14s} {n:>6,}")

    print("\n=== sample transformations ===")
    for plat, exs in samples.items():
        if not exs: continue
        print(f"\n  [{plat}]")
        for raw, typed in exs[:3]:
            print(f"    {raw!r}")
            print(f"      -> {typed}")

    if args.dry_run:
        print("\n[dry-run] no DB writes performed")
    else:
        print("\n[done] migration written to MongoDB")


if __name__ == "__main__":
    main()
