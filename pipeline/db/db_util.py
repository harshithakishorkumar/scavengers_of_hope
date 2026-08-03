"""Thin MongoDBActor wrapper... .find / .insert_data / .find_and_modify / .distinct).

Usage:
    from db_util import MongoDBActor, COLLECTIONS
    MongoDBActor(COLLECTIONS.CAMPAIGNS).find({"platform": "GoFundMe"})
    MongoDBActor(COLLECTIONS.LLM_CONTACTS).find_and_modify(
        key={"url": u}, data={"emails": [...]}
    )
"""
from types import SimpleNamespace

from connection import get_db

# Shared infrastructure domains: VT flags on these are noise because the
# domain is used by millions of legitimate users (Google Forms, Telegram,
# link aggregators). The badness, if any, is at the per-user path level,
# not the domain level. Detector A should NOT treat these as evidence
# against a campaign even if VirusTotal returns mal+susp >= 2.
SHARED_INFRA_DOMAINS = frozenset({
    # URL shorteners / link aggregators
    "forms.gle", "share.google", "bit.ly", "tinyurl.com", "goo.gl", "t.co",
    "ow.ly", "buff.ly", "is.gd", "tiny.cc", "rebrand.ly", "cutt.ly",
    "lnkd.in", "lnk.bio", "beacons.ai", "linktr.ee", "surl.lt", "blinq.me",
    "taplink.cc", "msha.ke", "link.me",
    # Cloud document / file hosts
    "docs.google.com", "drive.google.com", "dropbox.com", "box.com",
    "onedrive.live.com", "mega.nz", "wetransfer.com", "sendspace.com",
    # Image / video / streaming
    "imgur.com", "youtube.com", "youtu.be", "vimeo.com", "twitch.tv",
    # Pastebins / code raw
    "pastebin.com", "paste.ee", "hastebin.com", "raw.githubusercontent.com",
    "gist.github.com",
    # Surveys / scheduling / forms
    "typeform.com", "surveymonkey.com", "jotform.com", "calendly.com",
    "doodle.com", "google.com",
    # Social platforms (per-user evidence belongs in social_handles, not VT)
    "facebook.com", "fb.com", "fb.me", "instagram.com", "instagr.am",
    "twitter.com", "x.com", "tiktok.com", "linkedin.com", "discord.com",
    "discord.gg", "reddit.com", "snapchat.com", "pinterest.com",
    "whatsapp.com", "wa.me", "threads.net", "bsky.app", "mastodon.social",
    "t.me", "telegram.me", "telegram.org",
    "skype.com", "viber.com", "wechat.com", "qq.com", "kakao.com",
    "line.me", "signal.org",
    # Payment / donation platform domains: like the social platforms, the
    # actual fraud signal lives at /<handle>, not on the host. VT-ing the
    # bare domain paypal.me / venmo.com / patreon.com gives zero signal.
    "paypal.me", "paypal.com",
    "venmo.com", "cash.app", "cashapp.com",
    "wise.com", "transferwise.com",
    "patreon.com", "buymeacoffee.com", "ko-fi.com", "kofi.com",
    "donorbox.org", "givebutter.com", "fundly.com",
    "stripe.com", "square.com", "squareup.com",
    "zelle.com", "zellepay.com",
    "revolut.me", "revolut.com",
    "cashapp.me",
    # Music / podcast hosts (shared, per-user)
    "soundcloud.com", "spotify.com", "bandcamp.com", "mixcloud.com",
    "anchor.fm", "apple.co",
    # Blogging / writing hosts (shared)
    "medium.com", "wordpress.com", "blogspot.com", "tumblr.com",
    "substack.com",
    # Commerce / fundraising-adjacent (shared)
    "etsy.com", "amazon.com", "amazon.co.uk", "amazon.de",
    # Maps / directories
    "maps.google.com", "goo.gl",
})


def is_shared_infra(domain):
    """True if domain (or any of its parent domains) is shared infrastructure."""
    if not domain:
        return False
    d = domain.strip().lower().rstrip(".")
    if d in SHARED_INFRA_DOMAINS:
        return True
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in SHARED_INFRA_DOMAINS:
            return True
    return False


# The 15 crowdfunding platforms our corpus is built from. URLs that point
# back to these platforms are by definition the campaigns themselves, not
# external evidence; we don't need VT to tell us a campaign hosting
# platform is reputable.
CROWDFUNDING_PLATFORM_DOMAINS = frozenset({
    "angelink.com",
    "betterplace.org",      "betterplace-events.org",
    "chuffed.org",
    "crowdfundr.com",
    "donorschoose.org",
    "experiment.com",
    "freefunder.com",
    "gofundme.com",         "gofund.me",
    "gogetfunding.com",
    "happypot.com",
    "launchgood.com",
    "seedandspark.com",
    "spotfund.com",         "spot.fund",
    "ufandao.com",
    "whydonate.com",        "whydonate.org",
})


def is_self_platform(domain):
    """True if domain belongs to one of our 15 crowdfunding platforms."""
    if not domain:
        return False
    d = domain.strip().lower().rstrip(".")
    if d in CROWDFUNDING_PLATFORM_DOMAINS:
        return True
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in CROWDFUNDING_PLATFORM_DOMAINS:
            return True
    return False


def should_exclude_from_vt(domain):
    """Convenience: True if VT signals on this domain should be ignored
    (shared infrastructure OR our own platforms)."""
    return is_shared_infra(domain) or is_self_platform(domain)


COLLECTIONS = SimpleNamespace(
    RAW_CAMPAIGNS      = "raw_campaigns",
    FILTERED           = "filtered",
    LLM_CONTACTS       = "llm_contacts",
    VT_RESULTS         = "vt_results",
    IPQS_EMAIL_RESULTS = "ipqs_email_results",
    IPQS_PHONE_RESULTS = "ipqs_phone_results",
    CAMPAIGNS          = "campaigns",
    DETECTOR_FLAGS     = "detector_flags",
)


class MongoDBActor:
    """Thin facade over a single pymongo collection."""

    def __init__(self, collection_name):
        self.name = collection_name
        self._db = get_db()
        self._coll = self._db[collection_name]

    @property
    def collection(self):
        return self._coll

    def find(self, filter=None, projection=None, limit=None, sort=None):
        cur = self._coll.find(filter or {}, projection or None)
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(limit)
        return cur

    def find_one(self, filter=None, projection=None):
        return self._coll.find_one(filter or {}, projection or None)

    def insert_data(self, doc):
        return self._coll.insert_one(doc).inserted_id

    def insert_many(self, docs):
        return self._coll.insert_many(list(docs)).inserted_ids

    def find_and_modify(self, key, data, upsert=True):
        """Upsert: locate by `key`, $set `data` (creates if missing)."""
        return self._coll.update_one(key, {"$set": data}, upsert=upsert)

    def update_many(self, filter, update, upsert=False):
        return self._coll.update_many(filter, update, upsert=upsert)

    def distinct(self, key, filter=None):
        return self._coll.distinct(key, filter or {})

    def count(self, filter=None):
        return self._coll.count_documents(filter or {})

    def aggregate(self, pipeline, allow_disk_use=True):
        return self._coll.aggregate(pipeline, allowDiskUse=allow_disk_use)

    def drop(self):
        self._coll.drop()

    def create_index(self, keys, **kwargs):
        return self._coll.create_index(keys, **kwargs)

    def __repr__(self):
        return f"MongoDBActor(<{self.name}>)"
