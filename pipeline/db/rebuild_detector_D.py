"""Rebuild Detector D (organizer identity network) from MongoDB.

Replaces the legacy CSV-based organizer_network_v4_clean.py. Reads canonicalized
identity signals directly out of MongoDB (already populated by
apply_llm_extraction.py) instead of the now-removed campaign_contacts JSON.

Identity signals used as union-find edges:
  1. organizer name        — exact normalized + fuzzy (rapidfuzz token_sort >= 90)
  2. email_norms           — exact (already Gmail-dot-trick normalized)
  3. phone_norms           — exact (already E.164)
  4. payment_keys          — exact (kind:value, canonicalized)
  5. domain_keys           — exact, post-skiplist  [--with-domains]
  6. regex payment handles — bare $/@ + URL forms scanned over title+description,
                              with a tightened skiplist [--with-regex-handles]
  7. organizer profile_url — platform-issued unique organizer ID
                              [--with-profile-url]

The skiplist for (5) is a two-tier filter:
  - hardcoded curated list of generic non-identifying domains
  - frequency cutoff: any in-corpus domain seen in >= FREQ_CUTOFF campaigns

Flag rule (rigid-E):
  Default:       FLAG iff (cluster_size >= 10 AND >= 2 platforms) OR size >= 20
  --loose-size:  FLAG iff (cluster_size >=  5 AND >= 2 platforms) OR size >= 20
  Always: AND cluster_platforms <= 6 (mega-clusters spanning 7+ platforms = noise)

Recommended production combo:
  --with-domains --with-regex-handles --with-profile-url --loose-size

Outputs:
  - prints before/after counts and intersections to stdout
  - if --write: $sets detector_D.{flagged, org_cluster_id, org_cluster_size,
    org_cluster_platforms, is_cross_platform, rule_version, signals_used}
    on every campaign.
"""
import argparse
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz
from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


# ---------- Name normalization (matches organizer_network_v4_clean.py) ----------

import ast

ORG_BLACKLIST_SUBSTR = [
    'making a donation and spreading',
    'name none country none',
    'donating anything you can',
    'page@betterplace.org',
    'betterplaceteam',
]


def norm_org(s):
    s = (s or '').strip()
    if not s or s.lower() in ('nan', 'none', 'null', 'n/a', 'na'):
        return ''
    if s.startswith('{') and ("'name'" in s or '"name"' in s):
        try:
            d = ast.literal_eval(s)
            if isinstance(d, dict) and d.get('name'):
                s = str(d['name'])
        except (SyntaxError, ValueError):
            m = re.search(r"['\"]name['\"]\s*:\s*['\"](.+?)['\"]\s*[,}]", s)
            if m:
                s = m.group(1).replace("\\'", "'").replace('\\"', '"')
    s = s.lower().strip()
    if not s or s in ('nan', 'none', 'null', 'n/a', 'na'):
        return ''
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', ' ', s)
    if len(s) < 3:
        return ''
    for bad in ORG_BLACKLIST_SUBSTR:
        if bad in s:
            return ''
    return s


# ---------- Domain skiplist (NEW for 5th signal) ----------

DOMAIN_SKIPLIST_CURATED = {
    # platform self-references
    "gofundme.com", "betterplace.org", "gogetfunding.com", "seedandspark.com",
    "spotfund.com", "freefunder.com", "angelink.com", "experiment.com",
    "donorschoose.org", "ufandao.com", "my.ufandao.com", "crowdfundr.com",
    "chuffed.org", "whydonate.com", "launchgood.com", "happypot.ch",
    "goget.fund",
    # social
    "facebook.com", "fb.com", "m.facebook.com",
    "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "youtu.be", "tiktok.com", "snapchat.com", "threads.net", "mastodon.social",
    "vimeo.com", "pinterest.com", "reddit.com", "discord.com", "discord.gg",
    "t.me", "telegram.me", "telegram.org", "whatsapp.com", "wa.me",
    # mail providers
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "protonmail.com",
    "icloud.com", "live.com", "aol.com", "mail.com", "yandex.com", "gmx.com",
    "googlemail.com", "msn.com", "comcast.net", "verizon.net", "att.net",
    # hosting / site builders / blogs
    "wordpress.com", "wixsite.com", "blogspot.com", "weebly.com",
    "squarespace.com", "sites.google.com", "github.io", "tumblr.com",
    "medium.com", "substack.com", "jimdo.com", "webnode.com", "carrd.co",
    "notion.so", "linktr.ee",
    # generic giants
    "amazon.com", "google.com", "microsoft.com", "apple.com",
    # payment platforms (handles belong in payment_handles, not domain identity)
    "paypal.com", "paypal.me", "stripe.com", "venmo.com", "cashapp.com",
    "cash.app", "gopay.com", "wise.com",
    # file/photo hosting
    "drive.google.com", "dropbox.com", "imgur.com", "photos.google.com",
    "flickr.com", "icloud.com", "1drv.ms", "we.tl",
    # generic news / wiki (occasionally cited as proof, not identity)
    "wikipedia.org", "bbc.com", "bbc.co.uk", "cnn.com", "nytimes.com",
    "theguardian.com", "youtube.com", "google.org",
}

FREQ_CUTOFF = 20    # any domain seen in >= 20 distinct organizers is non-identifying


def build_dynamic_skiplist(domain_counts, cutoff=FREQ_CUTOFF):
    """Auto-add high-frequency in-corpus domains to the skiplist."""
    return {d for d, n in domain_counts.items() if n >= cutoff}


# ---------- Regex payment-handle scan (legacy + tightened skiplist) ----------

PAY_HANDLE_RE = re.compile(
    r'(?:\bcash\.app/\$?[\w.-]+'
    r'|\bpaypal\.me/[\w.-]+'
    r'|\bvenmo\.com/[\w@.-]+'
    r'|\bwise\.com/(?:pay|send)/[\w.-]+'
    r'|\$[A-Za-z][\w-]{3,30}'              # CashApp $handle (>=4 chars after $)
    r'|(?<![\w.@])@[A-Za-z][\w-]{3,30}\b'   # Venmo @handle (>=4 chars after @, not part of email)
    r')',
    re.IGNORECASE,
)

# Tightened skiplist (much larger than the legacy ~30 entries).
PAY_HANDLE_SKIPLIST = {
    # mention-all / channel pseudohandles
    '@everyone', '@here', '@channel', '@all', '@anyone', '@nobody',
    '@everybody', '@somebody',
    # pronouns / greetings
    '@home', '@work', '@school', '@me', '@us', '@you', '@we', '@they',
    '@him', '@her', '@them', '@friend', '@friends', '@family', '@team',
    '@everyone', '@buddy', '@guys', '@folks',
    # mail-provider tags (when someone wrote "ping me @gmail" etc)
    '@gmail', '@yahoo', '@hotmail', '@outlook', '@protonmail', '@icloud',
    '@live', '@aol', '@mail', '@email', '@inbox',
    # major social/brand handles (commonly mentioned, not payment identity)
    '@instagram', '@facebook', '@twitter', '@youtube', '@tiktok', '@snapchat',
    '@linkedin', '@discord', '@telegram', '@whatsapp', '@pinterest', '@reddit',
    '@vimeo', '@threads', '@mastodon', '@github', '@medium', '@substack',
    '@spotify', '@apple', '@google', '@amazon', '@microsoft', '@netflix',
    '@nike', '@adidas', '@walmart', '@target', '@costco',
    # operational / role accounts
    '@noreply', '@admin', '@support', '@help', '@info', '@hello', '@contact',
    '@team', '@hr', '@sales', '@billing', '@accounts',
    # platform self-tags
    '@gofundme', '@gogetfunding', '@betterplace', '@spotfund', '@launchgood',
    '@chuffed', '@kickstarter', '@indiegogo', '@patreon', '@kickfurther',
    # currency / amount $-words
    '$cash', '$money', '$dollars', '$dollar', '$fund', '$funds', '$donation',
    '$donations', '$amount', '$total', '$pay', '$payment', '$tip', '$gift',
    '$gifts', '$cost', '$costs', '$bill', '$bills', '$fee', '$fees', '$price',
    '$buy', '$sell', '$send', '$give', '$donate',
    # common English $-prefixed false positives
    '$home', '$house', '$car', '$life', '$love', '$help', '$hope', '$family',
    '$work', '$god', '$jesus',
}

# Common English word stems that are real-language false positives for bare
# @word handles (when no payment-platform URL context exists).
_COMMON_AT_WORD_FALSE_POSITIVES = {
    'sake', 'love', 'home', 'house', 'work', 'school', 'church', 'mosque',
    'temple', 'park', 'beach', 'night', 'noon', 'first', 'last', 'least',
    'most', 'best', 'worst', 'once', 'twice',
}


def norm_pay_handle(h):
    """Canonicalize a regex match. Returns '' for filtered noise."""
    s = (h or '').strip().lower()
    if not s:
        return ''
    # URL-form is unambiguous payment identity
    if 'cash.app/' in s or 'paypal.me/' in s or 'venmo.com/' in s or 'wise.com/' in s:
        s = s.rstrip('/').split('/')[-1].lstrip('$').lstrip('@')
        if not s:
            return ''
        s = '@' + s
        if len(s) < 5:
            return ''
        if s in PAY_HANDLE_SKIPLIST:
            return ''
        return 'urlhandle:' + s
    # Bare $/@ form needs stricter filtering
    if s in PAY_HANDLE_SKIPLIST:
        return ''
    # require >= 6 chars total ($foo123, @foo123) for bare form — currency words
    # and pronouns are 4-5 chars
    if len(s) < 6:
        return ''
    bare = s[1:]  # drop $ or @
    if bare in _COMMON_AT_WORD_FALSE_POSITIVES:
        return ''
    # Real payment handles tend to be unique enough that pure-alpha words are
    # suspicious: require either a digit or an underscore/dash (real handles
    # usually have one)
    if not re.search(r'[\d_-]', bare):
        return ''
    return ('cashhandle:' + s) if s.startswith('$') else ('vhandle:' + s)


# ---------- Profile URL extraction (signal #7) ----------

PROFILE_URL_RE = re.compile(
    r"['\"]profile_url['\"]\s*:\s*['\"](https?://[^'\"]+)['\"]"
)


def extract_profile_url(organizer_field):
    """Pull profile_url out of dict-form organizer strings. Returns canonical
    form (lowercase, query/fragment stripped) or '' if absent/empty."""
    if not organizer_field:
        return ''
    s = str(organizer_field)
    if 'profile_url' not in s:
        return ''
    m = PROFILE_URL_RE.search(s)
    if not m:
        return ''
    u = m.group(1).strip()
    if u.lower() in ('none', 'null', ''):
        return ''
    # Strip trailing slash, query string, fragment for matching
    u = u.split('#', 1)[0].split('?', 1)[0].rstrip('/').lower()
    # Bare-domain self-links are not identity (e.g., "https://gofundme.com")
    if u.count('/') <= 2:
        return ''
    return u


# ---------- Union-Find ----------

class UF:
    def __init__(self, n):
        self.p = list(range(n))
    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-domains", action="store_true",
                    help="add organizer.domains (post-skiplist) as a 5th identity edge")
    ap.add_argument("--with-regex-handles", action="store_true",
                    help="scan title+description for $handle/@handle/paypal.me URLs "
                         "as a 6th identity edge (with tightened skiplist)")
    ap.add_argument("--with-profile-url", action="store_true",
                    help="add organizer.profile_url as a 7th identity edge "
                         "(platform-issued unique ID)")
    ap.add_argument("--loose-size", action="store_true",
                    help="loosen cluster-size gate from 10 -> 5 for cross-platform clusters")
    ap.add_argument("--write", action="store_true",
                    help="write detector_D back to MongoDB (default: dry-run)")
    args = ap.parse_args()

    db = get_db()

    print(f"=== Pulling identity signals from mongo (with-domains={args.with_domains}) ===")
    t0 = time.time()
    proj = {
        "_id":             0,
        "url":             1,
        "platform":        1,
        "organizer":       1,
        "identity_signals.email_norms":   1,
        "identity_signals.phone_norms":   1,
        "identity_signals.payment_keys":  1,
        "identity_signals.domain_keys":   1,
        "llm_contacts.organizer.domains": 1,
    }
    if args.with_regex_handles:
        proj["title"]       = 1
        proj["description"] = 1
    docs = list(db.campaigns.find({}, proj))
    print(f"  campaigns: {len(docs):,}  (load: {time.time()-t0:.1f}s)")

    # --- index docs by ordinal id for union-find ---
    n = len(docs)
    idx_of = {d["url"]: i for i, d in enumerate(docs)}

    # --- domain skiplist (compute even if --with-domains is off, for reporting) ---
    domain_counts = Counter()
    for d in docs:
        seen = set()
        # prefer the canonicalized identity_signals.domain_keys when present
        keys = (d.get("identity_signals") or {}).get("domain_keys") or []
        if not keys:
            keys = (d.get("llm_contacts") or {}).get("organizer", {}).get("domains") or []
        for k in keys:
            k = (k or "").lower().strip()
            if k and k not in seen:
                seen.add(k)
                domain_counts[k] += 1

    dynamic_skip = build_dynamic_skiplist(domain_counts, FREQ_CUTOFF)
    full_skip = DOMAIN_SKIPLIST_CURATED | dynamic_skip
    print(f"  unique organizer domains corpus-wide: {len(domain_counts):,}")
    print(f"  curated skiplist:                     {len(DOMAIN_SKIPLIST_CURATED):,}")
    print(f"  dynamic skiplist (freq >= {FREQ_CUTOFF}):       {len(dynamic_skip):,}")
    print(f"  total skiplist:                       {len(full_skip):,}")
    print(f"  top dynamic-skip entries:")
    for d, c in sorted(((d, domain_counts[d]) for d in dynamic_skip),
                       key=lambda x: -x[1])[:10]:
        marker = " (curated)" if d in DOMAIN_SKIPLIST_CURATED else ""
        print(f"     {c:>5}  {d}{marker}")

    # --- build signal -> [doc indices] ---
    print("\n=== Building signal -> campaigns index ===")
    sig_to_idx = defaultdict(set)
    n_org = n_email = n_phone = n_pay = n_dom_kept = n_dom_skipped = 0
    n_rhandle_kept = n_rhandle_dropped = 0
    n_profile_url = 0

    for i, d in enumerate(docs):
        org_key = norm_org(str(d.get("organizer") or ""))
        if org_key:
            sig_to_idx[("org", org_key)].add(i)
            n_org += 1
        sig = d.get("identity_signals") or {}
        for e in (sig.get("email_norms") or []):
            sig_to_idx[("email", e)].add(i)
            n_email += 1
        for p in (sig.get("phone_norms") or []):
            sig_to_idx[("phone", p)].add(i)
            n_phone += 1
        for pk in (sig.get("payment_keys") or []):
            sig_to_idx[("payment", pk)].add(i)
            n_pay += 1
        if args.with_domains:
            keys = sig.get("domain_keys") or []
            if not keys:
                keys = (d.get("llm_contacts") or {}).get("organizer", {}).get("domains") or []
            for k in keys:
                k = (k or "").lower().strip()
                if not k:
                    continue
                if k in full_skip:
                    n_dom_skipped += 1
                    continue
                if len(k) < 4:
                    n_dom_skipped += 1
                    continue
                sig_to_idx[("domain", k)].add(i)
                n_dom_kept += 1
        if args.with_regex_handles:
            text = f"{d.get('title','') or ''} {d.get('description','') or ''}"
            seen = set()
            for h in PAY_HANDLE_RE.findall(text):
                nh = norm_pay_handle(h)
                if not nh:
                    n_rhandle_dropped += 1
                    continue
                if nh in seen:
                    continue
                seen.add(nh)
                sig_to_idx[("rhandle", nh)].add(i)
                n_rhandle_kept += 1
        if args.with_profile_url:
            pu = extract_profile_url(d.get("organizer"))
            if pu:
                sig_to_idx[("profile", pu)].add(i)
                n_profile_url += 1

    print(f"  org_name edges contributing:    {n_org:,}")
    print(f"  email contributing:             {n_email:,}")
    print(f"  phone contributing:             {n_phone:,}")
    print(f"  payment_handle contributing:    {n_pay:,}")
    if args.with_domains:
        print(f"  domain kept (post-skip):        {n_dom_kept:,}")
        print(f"  domain skipped (skiplist):      {n_dom_skipped:,}")
    if args.with_regex_handles:
        print(f"  regex-handles kept:             {n_rhandle_kept:,}")
        print(f"  regex-handles dropped (skip):   {n_rhandle_dropped:,}")
    if args.with_profile_url:
        print(f"  profile_url edges contributing: {n_profile_url:,}")

    # --- union-find: exact-match edges ---
    print("\n=== Union-find: exact-match edges ===")
    uf = UF(n)
    edges_exact = 0
    for sig, idxs in sig_to_idx.items():
        if len(idxs) < 2:
            continue
        first = next(iter(idxs))
        for j in idxs:
            if j != first:
                uf.union(first, j)
                edges_exact += 1
    print(f"  exact edges: {edges_exact:,}")

    # --- union-find: fuzzy organizer-name edges (rigid-E) ---
    print("\n=== Union-find: fuzzy name edges (token_sort >= 90, min 2 tokens) ===")
    NAME_MIN_LEN = 5
    NAME_MIN_TOKENS = 2
    FUZZY_THRESH = 90
    buckets = defaultdict(list)
    for i, d in enumerate(docs):
        name = norm_org(str(d.get("organizer") or ""))
        if not name or len(name) < NAME_MIN_LEN:
            continue
        if len(name.split()) < NAME_MIN_TOKENS:
            continue
        buckets[name[0]].append((i, name))

    fuzz_edges = 0
    for letter, rows in buckets.items():
        m = len(rows)
        for a in range(m):
            ia, na = rows[a]
            for b in range(a + 1, m):
                ib, nb = rows[b]
                if na == nb:
                    continue
                if fuzz.token_sort_ratio(na, nb) >= FUZZY_THRESH:
                    uf.union(ia, ib)
                    fuzz_edges += 1
    print(f"  fuzzy edges: {fuzz_edges:,}")

    # --- cluster stats ---
    print("\n=== Computing cluster stats ===")
    root_of = [uf.find(i) for i in range(n)]
    cluster_size = Counter(root_of)
    cluster_platforms = defaultdict(set)
    for i, d in enumerate(docs):
        cluster_platforms[root_of[i]].add(d.get("platform") or "")

    # --- flag rule (rigid-E) ---
    SIZE_HI = 20
    SIZE_LO = 5 if args.loose_size else 10
    PLAT_LO = 2
    PLAT_NOISE = 6
    print(f"\n=== Flag rule: size>={SIZE_LO} & plat>={PLAT_LO}  OR  size>={SIZE_HI};  plat<={PLAT_NOISE} ===")

    flagged_idx = []
    cross_platform_count = 0
    for i in range(n):
        r = root_of[i]
        sz = cluster_size[r]
        pl = len(cluster_platforms[r])
        cross = pl >= 2
        passes = ((sz >= SIZE_LO and pl >= PLAT_LO) or (sz >= SIZE_HI)) and (pl <= PLAT_NOISE)
        if passes:
            flagged_idx.append(i)
            if cross:
                cross_platform_count += 1

    print(f"\n=== Results ===")
    print(f"  total flagged: {len(flagged_idx):,}")
    print(f"  cross-platform flagged: {cross_platform_count:,}")
    print(f"  distinct flagged clusters: "
          f"{len({root_of[i] for i in flagged_idx}):,}")

    # --- intersections ---
    flagged_urls = {docs[i]['url'] for i in flagged_idx}
    print("\n=== Intersections with other detectors ===")
    for sig_name, sig_query in [
        ("A_canonical", {"detector_A.canonical_flagged": True}),
        ("B",           {"detector_B.flagged":           True}),
        ("C",           {"detector_C.flagged":           True}),
        ("redirection", {"redirection.flagged":          True}),
    ]:
        q = {"url": {"$in": list(flagged_urls)}}
        q.update(sig_query)
        c = db.campaigns.count_documents(q)
        print(f"  D and {sig_name:<13s}: {c:,}")

    # also D-alone (no A canonical, no B, no C)
    q_alone = {
        "url": {"$in": list(flagged_urls)},
        "detector_A.canonical_flagged": {"$ne": True},
        "detector_B.flagged":           {"$ne": True},
        "detector_C.flagged":           {"$ne": True},
    }
    print(f"  D alone (no A/B/C):  {db.campaigns.count_documents(q_alone):,}")

    # --- write to mongo ---
    if not args.write:
        print(f"\n[DRY-RUN]  Pass --write to apply detector_D updates to mongo.")
        return

    signals_used = ["name_exact_fuzzy", "email", "phone", "payment_handle"]
    if args.with_domains:        signals_used.append("domain_skiplisted")
    if args.with_regex_handles:  signals_used.append("regex_handle_skiplisted")
    if args.with_profile_url:    signals_used.append("profile_url")
    rule_parts = [
        "rigid_E_v4",
        ("size5" if args.loose_size else "size10"),
        "+".join(signals_used),
    ]
    rule_version = "__".join(rule_parts)

    print(f"\n=== Writing detector_D to mongo (rule_version={rule_version}) ===")
    cluster_id_map = {}    # root -> sequential id
    next_cluster_id = 0
    ops = []
    for i, d in enumerate(docs):
        r = root_of[i]
        if r not in cluster_id_map:
            cluster_id_map[r] = next_cluster_id
            next_cluster_id += 1
        cid = cluster_id_map[r]
        sz = cluster_size[r]
        pl = len(cluster_platforms[r])
        passes = ((sz >= SIZE_LO and pl >= PLAT_LO) or (sz >= SIZE_HI)) and (pl <= PLAT_NOISE)
        ops.append(UpdateOne(
            {"url": d["url"]},
            {"$set": {
                "detector_D.flagged":               bool(passes),
                "detector_D.org_cluster_id":        int(cid),
                "detector_D.org_cluster_size":      int(sz),
                "detector_D.org_cluster_platforms": int(pl),
                "detector_D.is_cross_platform":     bool(pl >= 2),
                "detector_D.rule_version":          rule_version,
                "detector_D.signals_used":          signals_used,
            }}
        ))

    BATCH = 500
    matched = 0
    for i in range(0, len(ops), BATCH):
        res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
        matched += res.matched_count
    print(f"  matched: {matched:,} / {len(ops):,}")

    final_flagged = db.campaigns.count_documents({"detector_D.flagged": True})
    print(f"\n  detector_D.flagged in mongo after write: {final_flagged:,}")


if __name__ == "__main__":
    main()
