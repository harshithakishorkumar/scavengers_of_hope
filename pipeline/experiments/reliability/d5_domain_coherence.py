#!/usr/bin/env python3
"""
D.5 domain identity-coherence audit + gate measurement.

Concern: D.5 (shared-domain) edges in Detector C (organizer identity,
detector_D_identity.py) chain UNRELATED people who merely cite the same
third-party site (charity homepages, news articles, disease-info sites).
An earlier audit caught unicef.org / msn.com / curefip.com doing this, and
266 of 505 deterministic fraud-tier C contributions rest on D.5 alone.

What this script does
  1. Rebuilds the by_domain buckets EXACTLY as detector_D_identity.py does
     (contacts urls field -> normalize_domain -> root-blocklist -> share cap),
     and for every edge-creating bucket computes the number of DISTINCT
     organizer name token-sets (detector's own organizer_token_set()) that
     share the domain. Prints the distribution + worst offenders.
  2. Measures an identity-COHERENCE gate: a D.5 edge counts only if the
     domain is shared by <= K distinct organizer name-sets (K = 1, 2, 3).
     Because removing edges can only SPLIT clusters, the effect is computed
     surgically on the shipped detector_D_flags.csv: within each shipped
     cluster, edges are reconstructed from the same global buckets, gated
     D.5 edges removed, and connected components recomputed. Campaigns left
     in singleton components lose their C flag; surviving components get
     recomputed size / platforms / deterministic-edge status.
     The >=2-of-3 hardened consensus (combined_hardened_consensus.py logic)
     is then recomputed with everything else held fixed (B_hard, rep_hard,
     heur_hard, takedown from the canonical CSV; the A carve-out re-evaluated
     with the gated campaign-level deterministic-C signal).
  3. Dumps affected clusters (gate splits them or strips their corroboration)
     and surviving coherent-domain clusters to a JSONL for manual reading.

Validation: with NO gate the reconstruction must reproduce the canonical
numbers (C0 7,242 / C_hard 3,708 / fraud 1,078 = 619 corroborated + 459
text-only / GGF 793 / 109 takedowns in tier). The script asserts this.

Never modifies canonical files. All outputs go next to this script:
  d5_domain_coherence_report.txt
  d5_domain_coherence_domains.csv      (per-domain bucket coherence table)
  d5_gate_reading_sample.jsonl         (clusters to read, task 3)
"""
import csv, json, re, sys, collections
from pathlib import Path

csv.field_size_limit(sys.maxsize)

ROOT   = Path(__file__).resolve().parents[2]              # ccs2026/
DATA   = ROOT / "02_data_filtration" / "filtered_dataset.csv"
LLM_JSONL = ROOT / "03_detection" / "intel" / "campaign_contacts_llm.jsonl"
D_FLAGS   = ROOT / "03_detection" / "detectors" / "outputs" / "detector_D_flags.csv"
CANON     = Path(__file__).resolve().parent / "combined_hardened_consensus.csv"

OUT_TXT   = Path(__file__).resolve().parent / "d5_domain_coherence_report.txt"
OUT_DOM   = Path(__file__).resolve().parent / "d5_domain_coherence_domains.csv"
OUT_READ  = Path(__file__).resolve().parent / "d5_gate_reading_sample.jsonl"

# ---- constants copied verbatim from detector_D_identity.py --------------------
MIN_NAME_TOKENS  = 2
MAX_DOMAIN_SHARE = 10
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", re.I)
EMAIL_BLOCKLIST = {
    "[email protected]", "[email redacted]", "[email]", "(email)",
    "email", "e-mail", "your email", "via email", "your email address",
    "me", "us", "info@", "contact@", "admin@",
    "[redacted]", "(redacted)",
    "example@example.com", "your_email_here", "name@example.com",
    "test@test.com", "user@example.com", "you@example.com",
}
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
VALID_HANDLE_RES = [
    re.compile(r"^(?:bc1[ac-hj-np-z02-9]{8,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$"),
    re.compile(r"^0x[a-fA-F0-9]{40}$"),
    re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$"),
    re.compile(r"^(?:https?://)?(?:www\.)?paypal\.me/[\w\-.]+/?$", re.I),
    re.compile(r"^\$[A-Z][\w]{2,20}$"),
    re.compile(r"^@[\w.-]{3,30}$"),
]

def is_valid_email(e):
    if not isinstance(e, str): return False
    s = e.strip().lower()
    if s in EMAIL_BLOCKLIST: return False
    if not EMAIL_RE.match(s): return False
    if "[" in s or "]" in s: return False
    return True

def is_valid_handle(h):
    if not isinstance(h, str): return False
    s = h.strip()
    if not s: return False
    return any(rx.match(s) for rx in VALID_HANDLE_RES)

def normalize_phone(p):
    return re.sub(r"\D", "", p or "")

def normalize_domain(s):
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0].split(":")[0]
    if s.startswith("www."): s = s[4:]
    return s

def organizer_token_set(name):
    if not isinstance(name, str): return None
    toks = re.findall(r"\w+", name.lower())
    toks = [t for t in toks if len(t) >= 2]
    if len(toks) < MIN_NAME_TOKENS: return None
    return tuple(sorted(toks))

def is_true(x): return str(x).strip().lower() in ("1", "true", "yes")
def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

DET_SIGS = {"D.2_email", "D.3_phone", "D.4_handle", "D.5_domain"}

out_lines = []
def p(s=""):
    print(s)
    out_lines.append(s)

# ================================================================ load corpus
url_platform, url_org, url_title = {}, {}, {}
with open(DATA, newline="") as f:
    for r in csv.DictReader(f):
        u = r["url"]
        url_platform[u] = r.get("platform") or "?"
        url_org[u]      = r.get("organizer") or ""
        url_title[u]    = (r.get("title") or "")[:120]
corpus_urls = set(url_platform)
p(f"corpus campaigns: {len(corpus_urls):,}")

contacts = {}
with open(LLM_JSONL) as f:
    for line in f:
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        if d.get("url") in corpus_urls: contacts[d["url"]] = d
p(f"contacts loaded (corpus): {len(contacts):,}")

# ============================================== rebuild buckets (mirror detector)
by_name, by_email, by_phone, by_handle, by_domain = (collections.defaultdict(set)
    for _ in range(5))
for u in corpus_urls:
    ts = organizer_token_set(url_org.get(u))
    if ts: by_name[ts].add(u)
    rec = contacts.get(u) or {}
    for e in (rec.get("emails") or []):
        if is_valid_email(e): by_email[e.lower().strip()].add(u)
    for ph in (rec.get("phones") or []):
        n = normalize_phone(ph)
        if len(n) >= 7: by_phone[n].add(u)
    for h in (rec.get("payment_handles") or []):
        if is_valid_handle(h): by_handle[h.strip().lower()].add(u)
    for uu in (rec.get("urls") or []):
        d = normalize_domain(uu)
        if not d or "." not in d: continue
        root = ".".join(d.split(".")[-2:])
        if root in DOMAIN_BLOCKLIST: continue
        by_domain[d].add(u)

def cap(buckets, limit):
    return {k: v for k, v in buckets.items() if len(v) <= limit}

by_domain_uncapped = dict(by_domain)          # keep for reporting cap-dropped domains
by_name   = cap(by_name,   MAX_DOMAIN_SHARE)
by_email  = cap(by_email,  MAX_DOMAIN_SHARE)
by_handle = cap(by_handle, MAX_DOMAIN_SHARE)
by_domain = cap(by_domain, MAX_DOMAIN_SHARE)
# NOTE: the shipped detector_D_flags.csv (2026-07-01 17:48) predates the
# phone-share-cap fix (added 21:33 same day), so phone buckets are NOT capped
# here.  The no-gate reconstruction below asserts this reproduces the shipped
# clusters exactly.

# ===================================================== task 1: domain coherence
dom_rows = []
for dom, urls in by_domain.items():
    if len(urls) < 2: continue                # bucket must create edges
    name_sets = {organizer_token_set(url_org.get(u)) for u in urls}
    name_sets.discard(None)
    n_anon = sum(1 for u in urls if organizer_token_set(url_org.get(u)) is None)
    plats  = {url_platform.get(u, "?") for u in urls}
    dom_rows.append(dict(domain=dom, n_campaigns=len(urls),
                         n_distinct_organizers=len(name_sets),
                         n_unnamed_campaigns=n_anon,
                         n_platforms=len(plats),
                         organizers=" | ".join(" ".join(t) for t in sorted(name_sets))))
dom_rows.sort(key=lambda r: (-r["n_distinct_organizers"], -r["n_campaigns"]))
with open(OUT_DOM, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(dom_rows[0].keys()))
    w.writeheader()
    for r in dom_rows: w.writerow(r)

p()
p("="*76)
p("TASK 1 — organizer coherence of edge-creating domain buckets")
p("="*76)
p(f"edge-creating domain buckets (2..{MAX_DOMAIN_SHARE} campaigns): {len(dom_rows):,}")
dropped = {k: v for k, v in by_domain_uncapped.items()
           if len(v) > MAX_DOMAIN_SHARE}
p(f"buckets dropped by the detector's share cap (>{MAX_DOMAIN_SHARE}): {len(dropped):,} "
  f"(create no edges; largest: "
  + ", ".join(f"{k}={len(v)}" for k, v in sorted(dropped.items(), key=lambda kv: -len(kv[1]))[:8]) + ")")
dist = collections.Counter(r["n_distinct_organizers"] for r in dom_rows)
p()
p("distinct organizer name-sets per domain bucket:")
for k in sorted(dist):
    p(f"  {k:>2} organizers: {dist[k]:>5,} domains")
p()
p("worst 15 multi-organizer domains (edge-creating buckets):")
p(f"{'domain':<34}{'campaigns':>10}{'organizers':>11}{'platforms':>10}")
for r in dom_rows[:15]:
    p(f"{r['domain'][:33]:<34}{r['n_campaigns']:>10}{r['n_distinct_organizers']:>11}{r['n_platforms']:>10}")
    p(f"    orgs: {r['organizers'][:150]}")

# ============================================ load shipped clusters + canonical
ship = {}
with open(D_FLAGS, newline="") as f:
    for r in csv.DictReader(f):
        ship[r["url"]] = r
flagged = {u for u, r in ship.items() if r["flag"] == "1"}
clusters = collections.defaultdict(set)
for u in flagged:
    clusters[ship[u]["cluster_id"]].add(u)
p()
p(f"shipped: {len(flagged):,} flagged campaigns in {len(clusters):,} clusters")

canon = {}
with open(CANON, newline="") as f:
    for r in csv.DictReader(f):
        canon[r["url"]] = r
assert set(canon) == corpus_urls

# ================================= reconstruct edges among flagged campaigns
# edge -> set of signals; also remember the domain of each D.5 edge
def add_bucket_edges(edges, buckets, sig, domain_of=None):
    for key, urls in buckets.items():
        mem = sorted(urls & flagged)
        if len(mem) < 2: continue
        for i in range(len(mem)):
            for j in range(i+1, len(mem)):
                e = (mem[i], mem[j])
                edges.setdefault(e, set()).add(sig)
                if domain_of is not None:
                    domain_of.setdefault(e, set()).add(key)

edges = {}
d5_edge_domains = {}
add_bucket_edges(edges, by_name,   "D.1_name")
add_bucket_edges(edges, by_email,  "D.2_email")
add_bucket_edges(edges, by_phone,  "D.3_phone")
add_bucket_edges(edges, by_handle, "D.4_handle")
add_bucket_edges(edges, by_domain, "D.5_domain", d5_edge_domains)
p(f"reconstructed edges among flagged campaigns: {len(edges):,}")

# validation 1: campaign-level fired signals match the shipped column (minus D.8)
recon_sig = collections.defaultdict(set)
for (a, b), sigs in edges.items():
    recon_sig[a] |= sigs
    recon_sig[b] |= sigs
mism = 0
for u in flagged:
    shipped_sigs = {t for t in (ship[u]["fired_signals"] or "").split(";") if t}
    shipped_sigs.discard("D.8_stylometric")
    if shipped_sigs != recon_sig.get(u, set()): mism += 1
p(f"validation: campaign-level fired-signal mismatches vs shipped: {mism:,} / {len(flagged):,}")

# validation 2: every shipped cluster internally connected under reconstructed edges
def components(members, edge_ok):
    parent = {u: u for u in members}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for (a, b), sigs in edges.items():
        if a in parent and b in parent and edge_ok((a, b), sigs):
            ra, rb = find(a), find(b)
            if ra != rb: parent[ra] = rb
    comps = collections.defaultdict(set)
    for u in members: comps[find(u)].add(u)
    return list(comps.values())

# index edges per cluster once for speed
cluster_edges = collections.defaultdict(list)
for (a, b), sigs in edges.items():
    ca, cb = ship[a]["cluster_id"], ship[b]["cluster_id"]
    if ca == cb: cluster_edges[ca].append(((a, b), sigs))

def cluster_components(cid, members, edge_ok):
    parent = {u: u for u in members}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for e, sigs in cluster_edges[cid]:
        if edge_ok(e, sigs):
            ra, rb = find(e[0]), find(e[1])
            if ra != rb: parent[ra] = rb
    comps = collections.defaultdict(set)
    for u in members: comps[find(u)].add(u)
    return list(comps.values())

disconnected = sum(1 for cid, mem in clusters.items()
                   if len(cluster_components(cid, mem, lambda e, s: True)) > 1)
p(f"validation: shipped clusters NOT internally connected by reconstructed edges: {disconnected:,} / {len(clusters):,}")

# ======================================================= consensus recompute
def gated_sigs(e, sigs, allowed_domains):
    """Signals of an edge after the D.5 coherence gate."""
    if "D.5_domain" not in sigs: return sigs
    if allowed_domains is None:  return sigs           # no gate
    doms = d5_edge_domains.get(e, set())
    if doms & allowed_domains:   return sigs
    rest = sigs - {"D.5_domain"}
    return rest

def run_variant(label, allowed_domains, takedown_carveout=False):
    """allowed_domains=None -> no gate (baseline reconstruction).
    takedown_carveout: a taken-down campaign keeps its shipped C0 (and, via
    the existing consensus carve-out, its C_hard) even if the gate would have
    isolated it — same style as the shipped 'Detector C rule (b) + takedown
    carve-out'."""
    takedown_urls = {u for u, cr in canon.items() if cr["takedown"] == "1"}
    C0v, cinfo = {}, {}     # url -> flag ; url -> (size, n_platforms, has_det)
    camp_det = collections.defaultdict(bool)  # campaign-level deterministic signal
    n_split = n_dropped = 0
    affected_clusters = []
    for cid, mem in clusters.items():
        def ok(e, s):
            return len(gated_sigs(e, s, allowed_domains)) > 0
        comps = cluster_components(cid, mem, ok)
        if len(comps) > 1: n_split += 1
        for comp in comps:
            if len(comp) < 2:
                for u in comp:
                    if takedown_carveout and u in takedown_urls:
                        # keep shipped C0; C_hard follows via the existing
                        # (c_rule or takedown) carve-out
                        C0v[u] = True
                        cinfo[u] = (1, 1, False)
                    else:
                        C0v[u] = False
                        n_dropped += 1
                continue
            plats = {url_platform.get(u, "?") for u in comp}
            has_det = False
            for e, s in cluster_edges[cid]:
                if e[0] in comp and e[1] in comp:
                    gs = gated_sigs(e, s, allowed_domains)
                    if not gs: continue
                    if gs & DET_SIGS:
                        has_det = True
                        camp_det[e[0]] = camp_det[e[1]] = True
            for u in comp:
                C0v[u] = True
                cinfo[u] = (len(comp), len(plats), has_det)
        if len(comps) > 1 or any(len(c) < 2 for c in comps):
            affected_clusters.append((cid, mem, comps))

    # campaign-level fired signals under the gate (edges to still-flagged campaigns)
    camp_sigs = collections.defaultdict(set)
    for e, s in edges.items():
        gs = gated_sigs(e, s, allowed_domains)
        if not gs: continue
        a, b = e
        if C0v.get(a) and C0v.get(b):
            camp_sigs[a] |= gs
            camp_sigs[b] |= gs

    res = []
    for u, cr in canon.items():
        takedown = cr["takedown"] == "1"
        C0 = C0v.get(u, False)
        if C0:
            size, nplat, has_det = cinfo[u]
            c_rule = has_det or nplat >= 2 or size >= 3
        else:
            c_rule = False
        C_hard = C0 and (c_rule or takedown)
        c_det  = C0 and bool(camp_sigs.get(u, set()) & DET_SIGS)
        rep, heur = cr["rep_hard"] == "1", cr["heur_hard"] == "1"
        A0 = cr["A0"] == "1"
        A_hard = rep or heur
        if (takedown or c_det) and A0 and not A_hard:
            A_hard = True
        B_hard = cr["B_hard"] == "1"
        score = int(A_hard) + int(B_hard) + int(C_hard)
        fraud = score >= 2
        corrob = takedown or rep or (C0 and c_rule)
        res.append(dict(url=u, platform=cr["platform"], C0=C0, C_hard=C_hard,
                        c_rule=c_rule, fraud=fraud, corrob=corrob,
                        takedown=takedown, rep=rep,
                        fraud_canon=cr["fraud_hard"] == "1",
                        corrob_canon=cr["corroborated"] == "1"))
    return dict(label=label, rows=res, n_split=n_split, n_dropped=n_dropped,
                affected=affected_clusters)

def report_variant(v):
    r = v["rows"]
    C0n   = sum(x["C0"] for x in r)
    Chn   = sum(x["C_hard"] for x in r)
    fr    = [x for x in r if x["fraud"]]
    corr  = sum(x["corrob"] for x in fr)
    txt   = len(fr) - corr
    ggf   = [x for x in fr if x["platform"] == "GoGetFunding"]
    ggft  = sum(1 for x in ggf if not x["corrob"])
    lost      = [x for x in r if x["fraud_canon"] and not x["fraud"]]
    lost_td   = sum(1 for x in lost if x["takedown"])
    lost_rep  = sum(1 for x in lost if x["rep"])
    td_in     = sum(1 for x in fr if x["takedown"])
    # corroborated fraud that stays fraud but becomes text-only
    c2t = [x for x in r if x["fraud_canon"] and x["corrob_canon"]
           and x["fraud"] and not x["corrob"]]
    p()
    p(f"---- {v['label']} " + "-"*(60-len(v['label'])))
    p(f"  clusters split: {v['n_split']:,}   campaigns dropped from C (singletons): {v['n_dropped']:,}")
    p(f"  C0 (flagged):        {C0n:,}   (canonical 7,242)")
    p(f"  C consensus-fires:   {Chn:,}   (canonical 3,708; delta {Chn-3708:+,})")
    p(f"  fraud tier:          {len(fr):,}   (canonical 1,078; delta {len(fr)-1078:+,})")
    p(f"    corroborated:      {corr:,}   (canonical 619)")
    p(f"    text-only:         {txt:,}   (canonical 459)")
    p(f"  GGF fraud:           {len(ggf):,}   (canonical 793)   text-only {ggft:,} (canonical 397)")
    p(f"  takedowns in tier:   {td_in:,}   (canonical 109)")
    p(f"  fraud lost vs canonical: {len(lost):,}   of which takedown-corroborated {lost_td}, sanitized-reputation {lost_rep}")
    p(f"  corroborated->text-only (stay fraud): {len(c2t):,}")
    return dict(lost=lost, c2t=c2t)

# ---- baseline (no gate) must reproduce canonical --------------------------
p()
p("="*76)
p("TASK 2 — coherence gate measurement")
p("="*76)
base = run_variant("NO GATE (reconstruction check)", None)
binfo = report_variant(base)
br = base["rows"]
assert sum(x["C0"] for x in br) == 7242,  "C0 reconstruction mismatch"
assert sum(x["C_hard"] for x in br) == 3708, "C_hard reconstruction mismatch"
assert sum(x["fraud"] for x in br) == 1078, "fraud reconstruction mismatch"
assert sum(1 for x in br if x["fraud"] and x["corrob"]) == 619
assert sum(1 for x in br if x["fraud"] and x["takedown"]) == 109
p("  [OK] no-gate reconstruction reproduces the canonical consensus exactly")

# ---- D.5-only share of deterministic fraud-tier C contributions -----------
# cluster-level det signature for fraud-tier C contributions
clus_det_sigs = collections.defaultdict(set)
for e, s in edges.items():
    a, b = e
    ca = ship[a]["cluster_id"]
    if ca == ship[b]["cluster_id"]:
        clus_det_sigs[ca] |= (s & DET_SIGS)
fr_c = [x for x in br if x["fraud"] and x["C_hard"] and x["c_rule"]]
det_c  = [x for x in fr_c if clus_det_sigs[ship[x["url"]]["cluster_id"]]]
d5only = [x for x in det_c
          if clus_det_sigs[ship[x["url"]]["cluster_id"]] == {"D.5_domain"}]
p()
p(f"fraud-tier campaigns whose C contribution passes via a deterministic cluster edge: {len(det_c):,}")
p(f"  of which the cluster's ONLY deterministic edge type is D.5_domain: {len(d5only):,}")

# ---- gates -----------------------------------------------------------------
dom_norg = {r["domain"]: r["n_distinct_organizers"] for r in dom_rows}
results = {}
for K in (1, 2, 3):
    allowed = {d for d, n in dom_norg.items() if n <= K}
    v = run_variant(f"GATE K={K}  (domain kept iff <= {K} distinct organizer name-sets; "
                    f"{len(allowed):,}/{len(dom_norg):,} domains kept)", allowed)
    results[K] = (v, report_variant(v))

# same gates WITH the takedown carve-out (matches shipped consensus style)
results_tc = {}
for K in (2, 3):
    allowed = {d for d, n in dom_norg.items() if n <= K}
    v = run_variant(f"GATE K={K} + TAKEDOWN CARVE-OUT", allowed, takedown_carveout=True)
    results_tc[K] = (v, report_variant(v))

# which takedown fraud does the naive K=2 gate lose, and on which domains?
def cluster_d5_domains(cid):
    doms = set()
    for e, s in cluster_edges[cid]:
        if "D.5_domain" in s: doms |= d5_edge_domains.get(e, set())
    return doms

p()
p("takedown-corroborated fraud lost by NAIVE K=2 (no carve-out):")
for x in results[2][1]["lost"]:
    if not x["takedown"]: continue
    cid = ship[x["url"]]["cluster_id"]
    p(f"  {x['url']}")
    p(f"      cluster {cid} size {ship[x['url']]['cluster_size']}  org='{url_org.get(x['url'],'')[:45]}'  cluster D.5 domains: {sorted(cluster_d5_domains(cid))[:4]}")

for K in (2, 3):
    p()
    p(f"sanitized-reputation fraud lost at K={K} (carve-out variant):")
    for x in results_tc[K][1]["lost"]:
        if not x["rep"]: continue
        cid = ship[x["url"]]["cluster_id"]
        p(f"  {x['url']}")
        p(f"      cluster {cid} size {ship[x['url']]['cluster_size']}  org='{url_org.get(x['url'],'')[:45]}'  cluster D.5 domains: {sorted(cluster_d5_domains(cid))[:4]}")

# ---- which corroborated fraud become text-only (K=2) ------------------------
p()
for K in (2, 3):
    v, info = results[K]
    p(f"K={K}: corroborated->text-only examples (fraud that stays but loses corroboration):")
    for x in info["c2t"][:10]:
        cid = ship[x["url"]]["cluster_id"]
        p(f"  {x['url']}   cluster {cid}  org='{url_org.get(x['url'],'')[:40]}'")
    p(f"K={K}: fraud lost vs canonical, by platform: " +
      str(collections.Counter(x['platform'] for x in info['lost']).most_common()))

# =================================== task 3: dump clusters for manual reading
# affected = clusters where the K=2 gate removed at least one D.5 edge
K2_allowed = {d for d, n in dom_norg.items() if n <= 2}
def cluster_domains(cid):
    doms = set()
    for e, s in cluster_edges[cid]:
        if "D.5_domain" in s: doms |= d5_edge_domains.get(e, set())
    return doms

read_rows = []
for cid, mem in clusters.items():
    doms = cluster_domains(cid)
    if not doms: continue
    gated  = {d for d in doms if d not in K2_allowed}
    kept   = doms - gated
    # outcome under K=2
    v2rows = {x["url"]: x for x in results[2][0]["rows"]}
    changed = any(v2rows[u]["C_hard"] != (canon[u]["C_hard"] == "1") or
                  v2rows[u]["fraud"] != (canon[u]["fraud_hard"] == "1") or
                  v2rows[u]["corrob"] != (canon[u]["corroborated"] == "1")
                  for u in mem)
    read_rows.append(dict(
        cluster_id=cid, size=len(mem),
        platforms=sorted({url_platform[u] for u in mem}),
        domains_gated=sorted(gated), domains_kept=sorted(kept),
        consensus_changed=changed,
        det_sigs=sorted(clus_det_sigs[cid]),
        n_fraud_canon=sum(1 for u in mem if canon[u]["fraud_hard"] == "1"),
        members=[dict(url=u, organizer=url_org.get(u, ""), title=url_title.get(u, ""),
                      fraud_canon=canon[u]["fraud_hard"], takedown=canon[u]["takedown"])
                 for u in sorted(mem)]))
read_rows.sort(key=lambda r: (-int(r["consensus_changed"]), -len(r["domains_gated"]), -r["size"]))
with open(OUT_READ, "w") as f:
    for r in read_rows:
        f.write(json.dumps(r) + "\n")
p()
p(f"clusters with any D.5 edge: {len(read_rows):,}  "
  f"(gated at K=2: {sum(1 for r in read_rows if r['domains_gated']):,}; "
  f"consensus-changed: {sum(1 for r in read_rows if r['consensus_changed']):,})")
p(f"[written] {OUT_READ}")
p(f"[written] {OUT_DOM}")

OUT_TXT.write_text("\n".join(out_lines) + "\n")
print(f"[written] {OUT_TXT}")
