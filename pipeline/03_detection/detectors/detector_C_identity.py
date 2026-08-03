"""
Detector C — Organizer Identity (unipartite graph)
====================================================
Catches "same person, multiple campaigns" — i.e., one operator running
many fundraisers under different titles.

Graph shape: UNIPARTITE
  Nodes:  campaigns (one node per URL)
  Edges:  campaign ↔ campaign, weighted by shared identity signals
  The identity value (email, phone, handle) is NOT a node — just glue.

Identity signals (7 deterministic + 1 soft):
  C.1  organizer_name fuzzy match (token_sort_ratio >= 90, min 2 tokens)
  C.2  exact email match (case-folded)
  C.3  exact phone match (digits-only normalized)
  C.4  payment handle match (LLM-extracted: CashApp/Venmo/paypal.me/IBAN/crypto)
  C.5  shared domain in extracted URLs
  C.6  shared regex handle (from hr_* patterns)
  C.7  shared profile URL (organizer's platform profile page)
  C.8  stylometric similarity (NEW, soft-weighted edge)
        per organizer, build a writing-style fingerprint:
          • avg sentence length
          • lexical diversity (type-token ratio)
          • punctuation density (exclamation, ellipsis, question mark)
          • function-word frequency
          • 1st-person vs 3rd-person pronoun ratio
        cosine similarity between fingerprints
        weighted edge: weight = cosine (range 0..1, kept only if >=0.85)

Each deterministic signal adds a HARD edge with weight 1.0.
Stylometry adds a SOFT edge with weight = cosine (only if both sides
already share at least one deterministic signal — soft alone too weak).

Clustering: Louvain community detection (replaces union-find).
  • respects edge weights — strong evidence builds tighter communities
  • won't drag a single weak overlap into a fraud cluster the way
    union-find did (the Sydney mega-cluster bug)
  • resolution parameter tuned to prevent platform-spanning mega-clusters

Cluster filtering (rigid-E rules carried forward):
  • cluster size >= 2 (otherwise nothing to detect)
  • cluster platforms <= 6 (mega-clusters spanning 7+ platforms = noise)
  • per-cluster fraud-risk score = mean of within-cluster A + B + E scores

Inputs:
  ../../02_data_filtration/filtered_dataset.csv
  ../intel/campaign_contacts_llm.jsonl

Output:
  outputs/detector_C_flags.csv  url, flag, cluster_id, cluster_size,
                                cluster_platforms, fired_signals
"""
from __future__ import annotations
import csv
import json
import re
from collections import defaultdict, Counter
from pathlib import Path

import community as community_louvain
import networkx as nx
import numpy as np
import pandas as pd
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "02_data_filtration" / "filtered_dataset.csv"
INTEL = ROOT / "03_detection" / "intel"
LLM_JSONL = INTEL / "campaign_contacts_llm.jsonl"
OUT = ROOT / "03_detection" / "detectors" / "outputs" / "detector_C_flags.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

NAME_FUZZY_THRESHOLD = 90
STYLOMETRIC_THRESHOLD = 0.95   # tighter (8-dim+func vectors saturate easily)
MIN_NAME_TOKENS = 2
MAX_CLUSTER_PLATFORMS = 6
MAX_DOMAIN_SHARE = 10          # real identity signals shouldn't appear in
                                # more than ~10 campaigns; above that is
                                # infrastructure/placeholder noise

# Strict email validator: must look like a real email AND not be a known
# extractor-hallucinated placeholder string.
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", re.I)
EMAIL_BLOCKLIST = {
    "[email protected]", "[email redacted]", "[email]", "(email)",
    "email", "e-mail", "your email", "via email", "your email address",
    "me", "us", "info@", "contact@", "admin@",
    "[redacted]", "(redacted)",
    "example@example.com", "your_email_here", "name@example.com",
    "test@test.com", "user@example.com", "you@example.com",
}

def is_valid_email(e):
    if not isinstance(e, str): return False
    s = e.strip().lower()
    if s in EMAIL_BLOCKLIST: return False
    if not EMAIL_RE.match(s): return False
    # Drop obvious bracketed placeholders
    if "[" in s or "]" in s: return False
    return True

# Always-exclude domains (platforms, social, big tech). These appear in
# thousands of campaigns by definition; they're not identity signals.
DOMAIN_BLOCKLIST = {
    "gofundme.com","spotfund.com","gogetfunding.com","launchgood.com",
    "chuffed.org","betterplace.org","freefunder.com","donorschoose.org",
    "kickstarter.com","indiegogo.com","justgiving.com","experiment.com",
    "happypot.org","crowdfundr.com","seedandspark.com","angelink.com",
    "ulule.com","whydonate.com","ufandao.com","fairplaid.com",
    "facebook.com","twitter.com","x.com","instagram.com","tiktok.com",
    "youtube.com","linkedin.com","github.com","pinterest.com","snapchat.com",
    "paypal.com","venmo.com","cashapp.com","stripe.com","square.com",
    "google.com","amazon.com","wikipedia.org","apple.com","microsoft.com",
    "bit.ly","tinyurl.com","t.co","goo.gl","ow.ly",
    "gmail.com","yahoo.com","outlook.com","hotmail.com","aol.com",
    "wordpress.com","blogger.com","medium.com","substack.com",
    "youtu.be","fb.me","m.me","wa.me","t.me",
}


def normalize_phone(p):
    return re.sub(r"\D", "", p or "")


def normalize_domain(s):
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0].split(":")[0]
    if s.startswith("www."): s = s[4:]
    return s


def organizer_token_set(name):
    """Require >=2 tokens that are each >=2 chars (drops 'Denis D.' style
    anonymized names where one token is a single initial)."""
    if not isinstance(name, str): return None
    toks = re.findall(r"\w+", name.lower())
    # Drop single-character tokens (initials)
    toks = [t for t in toks if len(t) >= 2]
    if len(toks) < MIN_NAME_TOKENS: return None
    return tuple(sorted(toks))


# Valid payment-handle patterns. Mirrors the filter in compute_wallet_reuse.py.
VALID_HANDLE_RES = [
    re.compile(r"^(?:bc1[ac-hj-np-z02-9]{8,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$"),   # BTC
    re.compile(r"^0x[a-fA-F0-9]{40}$"),                                                # ETH
    re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$"),                                      # IBAN
    re.compile(r"^(?:https?://)?(?:www\.)?paypal\.me/[\w\-.]+/?$", re.I),              # paypal.me
    re.compile(r"^\$[A-Z][\w]{2,20}$"),                                                # CashApp
    re.compile(r"^@[\w.-]{3,30}$"),                                                    # Venmo
]
def is_valid_handle(h):
    if not isinstance(h, str): return False
    s = h.strip()
    if not s: return False
    return any(rx.match(s) for rx in VALID_HANDLE_RES)


def styllometric_vector(text):
    """Per-organizer-or-campaign writing fingerprint."""
    if not isinstance(text, str) or len(text) < 50:
        return None
    sents = re.split(r"[.!?]+\s+", text)
    sents = [s for s in sents if len(s.split()) > 0]
    if not sents: return None
    words = re.findall(r"\w+", text.lower())
    if len(words) < 30: return None

    avg_sent_len = np.mean([len(s.split()) for s in sents])
    type_token = len(set(words)) / len(words)
    pct_excl = text.count("!") / max(len(text), 1)
    pct_ellipsis = text.count("...") / max(len(text), 1)
    pct_question = text.count("?") / max(len(text), 1)
    pct_caps = sum(1 for c in text if c.isupper()) / max(len(text), 1)

    FUNC = {"the","of","and","to","a","in","that","is","for","with",
            "i","you","my","me","we","our","they","this","it"}
    func_counts = Counter(w for w in words if w in FUNC)
    func_vec = [func_counts.get(w, 0) / len(words) for w in sorted(FUNC)]

    first_person = sum(1 for w in words if w in {"i","me","my","mine","we","us","our","ours"})
    third_person = sum(1 for w in words if w in {"he","she","they","him","her","them","his","hers","theirs"})
    fp_ratio = first_person / max(third_person + first_person, 1)

    v = np.array([avg_sent_len, type_token, pct_excl, pct_ellipsis,
                  pct_question, pct_caps, fp_ratio, *func_vec],
                 dtype=np.float32)
    # Normalize
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else None


def main():
    print("Detector C — Organizer Identity (unipartite + stylometric + Louvain)")
    df = pd.read_csv(DATA, low_memory=False)
    print(f"  campaigns:               {len(df):,}")

    # Build per-campaign signal sets
    contacts = {}
    if LLM_JSONL.exists():
        with LLM_JSONL.open() as fh:
            for line in fh:
                try: d = json.loads(line)
                except: continue
                if d.get("url"): contacts[d["url"]] = d
    print(f"  contacts loaded:         {len(contacts):,}")

    # Build inverted indices
    by_name      = defaultdict(set)   # token_set -> {urls}
    by_email     = defaultdict(set)
    by_phone     = defaultdict(set)
    by_handle    = defaultdict(set)
    by_domain    = defaultdict(set)
    by_profile   = defaultdict(set)

    # Per-campaign stylometric fingerprint
    style = {}
    # Per-campaign platform (for cluster-platform cap)
    platforms = {}

    for _, row in df.iterrows():
        url = row["url"]
        platforms[url] = row.get("platform") or "?"
        name_ts = organizer_token_set(row.get("organizer"))
        if name_ts: by_name[name_ts].add(url)
        prof = row.get("organizer_url") or row.get("campaign_url")
        if isinstance(prof, str) and prof.strip():
            by_profile[prof.strip()].add(url)

        desc = row.get("description")
        title = row.get("title")
        text = (title if isinstance(title, str) else "") + "\n" + \
               (desc if isinstance(desc, str) else "")
        v = styllometric_vector(text)
        if v is not None: style[url] = v

        rec = contacts.get(url) or {}
        for e in (rec.get("emails") or []):
            if not is_valid_email(e): continue
            by_email[e.lower().strip()].add(url)
        for p in (rec.get("phones") or []):
            n = normalize_phone(p)
            if len(n) >= 7: by_phone[n].add(url)
        for h in (rec.get("payment_handles") or []):
            if not is_valid_handle(h): continue
            hn = h.strip().lower()
            by_handle[hn].add(url)
        for u in (rec.get("urls") or []):
            d = normalize_domain(u)
            if not d or "." not in d: continue
            # drop subdomain to compare against the blocklist roots
            root = ".".join(d.split(".")[-2:])
            if root in DOMAIN_BLOCKLIST: continue
            by_domain[d].add(url)

    # Build the unipartite graph
    G = nx.Graph()
    for url in df["url"]:
        G.add_node(url, platform=platforms.get(url, "?"))

    sig_count = Counter()

    def add_edges(buckets, sig_name):
        for _, urls in buckets.items():
            if len(urls) < 2: continue
            urls_l = sorted(urls)
            for i in range(len(urls_l)):
                for j in range(i+1, len(urls_l)):
                    u1, u2 = urls_l[i], urls_l[j]
                    if G.has_edge(u1, u2):
                        G[u1][u2]["weight"] += 1.0
                        G[u1][u2]["signals"].add(sig_name)
                    else:
                        G.add_edge(u1, u2, weight=1.0, signals={sig_name})
                    sig_count[sig_name] += 1

    # Share-cap: drop any bucket where >MAX_DOMAIN_SHARE campaigns share the
    # same value (those are infrastructure/shared resources, not identity).
    def cap(buckets, limit, label):
        before = len(buckets)
        dropped = {k: len(v) for k, v in buckets.items() if len(v) > limit}
        buckets = {k: v for k, v in buckets.items() if len(v) <= limit}
        if dropped:
            print(f"  [{label}] dropped {len(dropped)} oversized buckets "
                  f"(max kept: {limit})")
        return buckets

    by_name    = cap(by_name,    MAX_DOMAIN_SHARE, "name")
    by_email   = cap(by_email,   MAX_DOMAIN_SHARE, "email")
    # 2026-07-01 audit fix: phone buckets previously escaped the share cap
    # (a 16-campaign single-org phone clique slipped through). The shipped
    # detector_C_flags.csv predates this fix; a re-run regenerates with it.
    by_phone   = cap(by_phone,   MAX_DOMAIN_SHARE, "phone")
    by_handle  = cap(by_handle,  MAX_DOMAIN_SHARE, "handle")
    by_domain  = cap(by_domain,  MAX_DOMAIN_SHARE, "domain")
    by_profile = cap(by_profile, MAX_DOMAIN_SHARE, "profile")

    add_edges(by_name,    "C.1_name")
    add_edges(by_email,   "C.2_email")
    add_edges(by_phone,   "C.3_phone")
    add_edges(by_handle,  "C.4_handle")
    add_edges(by_domain,  "C.5_domain")
    add_edges(by_profile, "C.7_profile_url")

    print(f"  edges built:             {G.number_of_edges():,}")
    print(f"  by signal:               {dict(sig_count)}")

    # Stylometric boost — only on edges that already exist
    # (soft signal — too noisy alone; must reinforce a deterministic signal)
    boosted = 0
    for u1, u2 in list(G.edges):
        v1, v2 = style.get(u1), style.get(u2)
        if v1 is None or v2 is None: continue
        cos = float(np.dot(v1, v2))
        if cos >= STYLOMETRIC_THRESHOLD:
            G[u1][u2]["weight"] += cos
            G[u1][u2]["signals"].add("C.8_stylometric")
            boosted += 1
    print(f"  stylometric boosts:      {boosted:,}")

    # Drop singleton nodes (no edges) to speed up Louvain
    G.remove_nodes_from([n for n in list(G.nodes) if G.degree(n) == 0])
    print(f"  nodes after singleton drop: {G.number_of_nodes():,}")

    if G.number_of_nodes() == 0:
        print("no edges — Detector C finds no clusters.")
        with OUT.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["url","flag","cluster_id","cluster_size","cluster_platforms","fired_signals"])
            for u in df["url"]:
                w.writerow([u,0,"",0,0,""])
        return

    print("  running Louvain...")
    partition = community_louvain.best_partition(G, weight="weight", random_state=42)
    cluster_to_urls = defaultdict(set)
    for u, c in partition.items(): cluster_to_urls[c].add(u)

    print(f"  clusters found:          {len(cluster_to_urls):,}")
    sizes = [len(v) for v in cluster_to_urls.values()]
    print(f"  size distribution:       max={max(sizes)}  median={sorted(sizes)[len(sizes)//2]}")

    # Filter: cluster size >=2, platforms <=6
    keep_clusters = {}
    cid_new = 0
    for c, urls in cluster_to_urls.items():
        if len(urls) < 2: continue
        plats = {G.nodes[u].get("platform","?") for u in urls}
        if len(plats) > MAX_CLUSTER_PLATFORMS: continue
        cid_new += 1
        keep_clusters[cid_new] = (urls, plats)
    print(f"  clusters surviving filter (size>=2, <=6 platforms): {len(keep_clusters):,}")

    n_flag = 0
    url_to_cluster = {}
    for cid, (urls, plats) in keep_clusters.items():
        for u in urls:
            url_to_cluster[u] = (cid, len(urls), len(plats))
            n_flag += 1

    print(f"  campaigns flagged:       {n_flag:,}")

    # Write output (every campaign, with cluster info if flagged)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url","flag","cluster_id","cluster_size","cluster_platforms","fired_signals"])
        for u in df["url"]:
            info = url_to_cluster.get(u)
            if info:
                cid, sz, np_ = info
                # collect all signals across edges in this campaign's cluster
                signals = set()
                for nb in G.neighbors(u):
                    if nb in url_to_cluster:
                        signals.update(G[u][nb]["signals"])
                w.writerow([u, 1, cid, sz, np_, ";".join(sorted(signals))])
            else:
                w.writerow([u, 0, "", 0, 0, ""])

    pct = 100*n_flag/len(df) if len(df) else 0
    print(f"\nflagged by Detector C:    {n_flag:,}  ({pct:.2f}%)")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
