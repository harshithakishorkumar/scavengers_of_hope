"""Refine handles currently classified as platform=unknown:

  1. Expanded SLD map: catches Patreon, Tumblr, Soundcloud, Twitch, Skype,
     Bandcamp, Spotify, etc., plus Facebook subdomains (m./web./l.).
  2. Demote `unknown.com/<id>` URLs (the original LLM's "I don't know"
     placeholder) to bare-@ form so the context-disambiguator can work
     on them.
  3. Re-runs Stage 1 disambiguation on bare-@ entries to fill platforms
     where the description gives a clear keyword nearby.

Idempotent. Updates both `campaigns` and `llm_contacts` collections.
Writes a refinement audit CSV to ccs2026/03_detection/intel/unknown_refine_audit.csv.

Run:
    python3 ccs2026/db/refine_unknown_handles.py --dry-run
    python3 ccs2026/db/refine_unknown_handles.py
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
from disambiguate_bare_at import disambiguate as ctx_disambiguate

# Expanded platform mapping (additions on top of what migrate_social_handles
# already covered). Keys are SLDs (the leftmost label of the registered
# domain), values are canonical platform names.
EXTRA_PLATFORM_BY_SLD = {
    "patreon":     "patreon",
    "tumblr":      "tumblr",
    "soundcloud":  "soundcloud",
    "twitch":      "twitch",
    "skype":       "skype",
    "viber":       "viber",
    "wechat":      "wechat",
    "wordpress":   "wordpress",
    "blogspot":    "blogspot",
    "blogger":     "blogspot",
    "mixcloud":    "mixcloud",
    "bandcamp":    "bandcamp",
    "spotify":     "spotify",
    "polarsteps":  "polarsteps",
    "strava":      "strava",
    "etsy":        "etsy",
    "linktr":      "linktree",     # linktr.ee
    "dropbox":     "dropbox",
    "revolut":     "revolut",
    "tumbler":     "tumblr",       # common typo
}

EXTRA_CANONICAL_HOST = {
    "patreon":    "patreon.com",
    "tumblr":     "tumblr.com",
    "soundcloud": "soundcloud.com",
    "twitch":     "twitch.tv",
    "skype":      "skype.com",
    "viber":      "viber.com",
    "wechat":     "wechat.com",
    "wordpress":  "wordpress.com",
    "blogspot":   "blogspot.com",
    "mixcloud":   "mixcloud.com",
    "bandcamp":   "bandcamp.com",
    "spotify":    "spotify.com",
    "polarsteps": "polarsteps.com",
    "strava":     "strava.com",
    "etsy":       "etsy.com",
    "linktree":   "linktr.ee",
    "dropbox":    "dropbox.com",
    "revolut":    "revolut.me",
}

FACEBOOK_SUBDOMAIN_HOSTS = {
    "m.facebook.com", "web.facebook.com", "l.facebook.com",
    "mobile.facebook.com", "free.facebook.com",
}

PROTO_RE = re.compile(r"^https?://", re.I)


def host_of(url):
    if not url:
        return ""
    s = PROTO_RE.sub("", url).split("/", 1)[0].lower().strip(".")
    return s


def reclassify_unknown(entry):
    """Return (new_entry, action_tag) where action_tag is one of:
       'no_change', 'reclassified', 'demoted_unknown_com', 'fb_subdomain_normalized'."""
    if entry.get("platform") != "unknown":
        return entry, "no_change"

    url = entry.get("url")
    user_id = entry.get("user_id")

    # If url is None, this is a bare-@ entry already; nothing to reclassify here.
    if not url:
        return entry, "no_change"

    h = host_of(url)
    if not h:
        return entry, "no_change"

    # 1. Facebook subdomain normalisation.
    if h in FACEBOOK_SUBDOMAIN_HOSTS:
        if user_id:
            return ({"platform": "facebook", "user_id": user_id,
                     "url": f"https://facebook.com/{user_id}"},
                    "fb_subdomain_normalized")

    # 2. unknown.com placeholder -> demote to bare-@ form so disambiguation runs.
    if h == "unknown.com":
        return ({"platform": "unknown", "user_id": user_id, "url": None},
                "demoted_unknown_com")

    # 3. Expanded SLD map.
    sld = h.split(".")[0]
    plat = EXTRA_PLATFORM_BY_SLD.get(sld)
    if plat and user_id:
        canonical = EXTRA_CANONICAL_HOST.get(plat, h)
        return ({"platform": plat, "user_id": user_id,
                 "url": f"https://{canonical}/{user_id}"},
                "reclassified")

    return entry, "no_change"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit-csv",
                    default=str(Path(__file__).resolve().parent.parent
                                 / "03_detection" / "intel" / "unknown_refine_audit.csv"))
    args = ap.parse_args()

    db = get_db()
    audit_path = Path(args.audit_csv)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    actions = Counter()
    by_platform = Counter()
    n_docs = n_changed = 0
    ops_campaigns = []
    ops_contacts = []

    with open(audit_path, "w", newline="") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["url", "user_id", "before_platform", "before_url",
                     "after_platform", "after_url", "action", "stage"])

        cur = db.campaigns.find(
            {"llm_contacts.social_handles.platform": "unknown"},
            {"_id": 1, "url": 1, "description": 1,
             "llm_contacts.social_handles": 1},
        )

        for doc in cur:
            n_docs += 1
            campaign_url = doc["url"]
            desc = doc.get("description") or ""
            sh = (doc.get("llm_contacts") or {}).get("social_handles") or []

            new_sh = []
            doc_changed = False

            for entry in sh:
                before = dict(entry)
                refined, action = reclassify_unknown(entry)
                stage = ""

                # If reclassification didn't help (still unknown) and this is
                # a freshly-bare-@ entry (or always was), try context
                # disambiguation.
                if refined.get("platform") == "unknown" and not refined.get("url"):
                    plat, st = ctx_disambiguate(refined.get("user_id") or "", desc)
                    if plat:
                        from disambiguate_bare_at import CANONICAL_HOST as CTX_CANONICAL_HOST
                        refined = {"platform": plat,
                                    "user_id": refined["user_id"],
                                    "url": f"https://{CTX_CANONICAL_HOST[plat]}/{refined['user_id']}"}
                        action = "context_disambiguated_after_demote" if action == "demoted_unknown_com" else "context_disambiguated"
                        stage = st

                actions[action] += 1
                if refined != before:
                    by_platform[refined.get("platform", "unknown")] += 1
                    doc_changed = True
                    w.writerow([campaign_url, before.get("user_id"),
                                 before.get("platform"), before.get("url"),
                                 refined.get("platform"), refined.get("url"),
                                 action, stage])
                new_sh.append(refined)

            if doc_changed:
                n_changed += 1
                if not args.dry_run:
                    ops_campaigns.append(UpdateOne(
                        {"_id": doc["_id"]},
                        {"$set": {"llm_contacts.social_handles": new_sh}},
                    ))
                    ops_contacts.append(UpdateOne(
                        {"url": campaign_url},
                        {"$set": {"social_handles": new_sh}},
                    ))
                    if len(ops_campaigns) >= 500:
                        db.campaigns.bulk_write(ops_campaigns, ordered=False)
                        db.llm_contacts.bulk_write(ops_contacts, ordered=False)
                        ops_campaigns = []; ops_contacts = []

        if ops_campaigns and not args.dry_run:
            db.campaigns.bulk_write(ops_campaigns, ordered=False)
            db.llm_contacts.bulk_write(ops_contacts, ordered=False)

    print(f"campaigns scanned (with unknown handles): {n_docs:,}")
    print(f"campaigns changed:                         {n_changed:,}")
    print()
    print("actions:")
    for a, n in actions.most_common():
        print(f"  {a:40s} {n:>6,}")
    print()
    print("changed entries by new platform:")
    for p, n in by_platform.most_common():
        print(f"  {p:14s} {n:>6,}")
    print(f"\naudit CSV: {audit_path}")
    if args.dry_run:
        print("[dry-run] no DB writes performed")


if __name__ == "__main__":
    main()
