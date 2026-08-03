"""Reversible cleanup of llm_contacts.organizer.domains.

The raw LLM contact-extraction stored the crowdfunding PLATFORM's own domains
(gofundme.com, betterplace.org, ...) and shared social / email-provider infra
(facebook.com, gmail.com, ...) as "organizer domains". Those are never
organizer identity, and on the live extension path they caused detector C to
fire on nearly every campaign (every GoFundMe matched via gofundme.com).

This script removes ONLY those unambiguous non-identity domains, preserving
every genuine organizer domain, and backs up the original list to
llm_contacts.organizer.domains_raw so the change is fully reversible.

Usage:
    python clean_organizer_domains.py            # dry-run (report only, no writes)
    python clean_organizer_domains.py --apply    # back up to domains_raw + strip junk
    python clean_organizer_domains.py --restore  # restore originals from domains_raw
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db          # noqa: E402
from db_util import is_shared_infra     # noqa: E402
from pymongo import UpdateOne           # noqa: E402

# The crowdfunding platforms themselves — their own domains are never identity.
PLATFORM_DOMAINS = {
    "angelink.com", "betterplace.org", "chuffed.org", "crowdfundr.com",
    "donorschoose.org", "experiment.com", "freefunder.com", "gofundme.com",
    "gofundme.org", "gogetfunding.com", "happypot.ch", "launchgood.com",
    "ufandao.com", "my.ufandao.com", "seedandspark.com", "spotfund.com",
    "whydonate.com",
}

# Email providers — as a *website* domain these are never organizer identity.
EMAIL_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "icloud.com", "me.com", "aol.com", "gmx.de",
    "gmx.net", "web.de", "proton.me", "protonmail.com", "mail.com", "yandex.com",
}

# Shared website-builders, link aggregators, other funding/petition/payment
# platforms, and social sites that slip past is_shared_infra. In their stored
# (registrable-domain) form the unique subdomain was already collapsed away,
# so these are non-identifying — never a single organizer's identity.
EXTRA_NON_IDENTITY = {
    "wixsite.com", "weebly.com", "blogspot.com", "tumblr.com", "medium.com",
    "substack.com", "carrd.co", "notion.site", "godaddysites.com", "square.site",
    "myshopify.com", "bigcartel.com", "strava.com", "linktr.ee", "beacons.ai",
    "eventbrite.com", "meetup.com", "change.org", "justgiving.com",
    "kickstarter.com", "indiegogo.com", "patreon.com", "venmo.com", "cash.app",
    "paypal.com", "paypal.me", "buymeacoffee.com", "gofund.me",
}


def _norm(host: str) -> str:
    return (host or "").lower().strip().removeprefix("www.")


def is_platform(host: str) -> bool:
    h = _norm(host)
    return any(h == p or h.endswith("." + p) for p in PLATFORM_DOMAINS)


def is_junk(host: str) -> bool:
    """Unambiguous non-identity: empty, a platform domain, an email provider,
    or anything the shared-infra skiplist already covers (social, CDNs, ...)."""
    h = _norm(host)
    if not h or "." not in h:
        return True
    return (is_platform(h) or h in EMAIL_PROVIDERS
            or h in EXTRA_NON_IDENTITY or is_shared_infra(h))


def restore(db) -> None:
    cur = db.campaigns.find(
        {"llm_contacts.organizer.domains_raw": {"$exists": True}},
        {"_id": 1, "llm_contacts.organizer.domains_raw": 1},
    )
    ops, n = [], 0
    for d in cur:
        raw = ((d.get("llm_contacts") or {}).get("organizer") or {}).get("domains_raw")
        ops.append(UpdateOne(
            {"_id": d["_id"]},
            {"$set":   {"llm_contacts.organizer.domains": raw},
             "$unset": {"llm_contacts.organizer.domains_raw": ""}},
        ))
        if len(ops) >= 1000:
            db.campaigns.bulk_write(ops); n += len(ops); ops = []
    if ops:
        db.campaigns.bulk_write(ops); n += len(ops)
    print(f"restored {n:,} docs from domains_raw (backup field removed)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply",   action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--restore", action="store_true", help="undo: restore from domains_raw")
    args = ap.parse_args()
    db = get_db()

    if args.restore:
        restore(db)
        return

    cur = db.campaigns.find(
        {"llm_contacts.organizer.domains.0": {"$exists": True}},
        {"_id": 1, "llm_contacts.organizer.domains": 1},
    )
    removed_counter, kept_counter = Counter(), Counter()
    n_docs = n_entries = n_removed = n_emptied = 0
    ops = []
    for d in cur:
        n_docs += 1
        doms = ((d.get("llm_contacts") or {}).get("organizer") or {}).get("domains") or []
        n_entries += len(doms)
        kept    = [x for x in doms if not is_junk(x)]
        removed = [x for x in doms if is_junk(x)]
        for x in removed:
            removed_counter[_norm(x)] += 1
        for x in kept:
            kept_counter[_norm(x)] += 1
        n_removed += len(removed)
        if not kept:
            n_emptied += 1
        if args.apply and removed:
            # Pipeline update: back up raw ONCE (idempotent via $ifNull), set cleaned list.
            ops.append(UpdateOne({"_id": d["_id"]}, [
                {"$set": {
                    "llm_contacts.organizer.domains_raw": {
                        "$ifNull": ["$llm_contacts.organizer.domains_raw",
                                    "$llm_contacts.organizer.domains"]},
                    "llm_contacts.organizer.domains": kept,
                }},
            ]))
            if len(ops) >= 1000:
                db.campaigns.bulk_write(ops); ops = []
    if args.apply and ops:
        db.campaigns.bulk_write(ops)

    print(f"docs with organizer.domains:  {n_docs:,}")
    print(f"total domain entries:         {n_entries:,}")
    print(f"removed (junk, non-identity): {n_removed:,}")
    print(f"kept   (genuine organizer):   {n_entries - n_removed:,}")
    print(f"docs that lose all domains:   {n_emptied:,}")
    print("\ntop REMOVED (junk) domains:")
    for dom, c in removed_counter.most_common(15):
        print(f"  {c:5d}  {dom}")
    print("\ntop KEPT (genuine organizer) domains:")
    for dom, c in kept_counter.most_common(15):
        print(f"  {c:5d}  {dom}")
    print("\n" + ("APPLIED — originals backed up to domains_raw; undo with --restore"
                  if args.apply else "DRY-RUN — re-run with --apply to write changes"))


if __name__ == "__main__":
    main()
