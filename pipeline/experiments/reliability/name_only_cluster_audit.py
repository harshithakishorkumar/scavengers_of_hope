#!/usr/bin/env python3
"""
D.1 organizer-name deep dive: are name-only identity clusters (Detector C's
dominant edge, 6,198 D.1 campaigns of 7,242 flagged) inflating the fraud tier?

Reads ONLY canonical artifacts (never modifies them):
  experiments/reliability/combined_hardened_consensus.csv   (canonical tier)
  03_detection/detectors/outputs/detector_D_flags.csv       (Detector C clusters)
  02_data_filtration/filtered_dataset_v4.csv                (organizers/text)
  03_detection/features/sbert_embeddings.npy + sbert_url_order.csv

Definitions (mirroring combined_hardened_consensus.py exactly):
  DET_EDGES = {D.2_email, D.3_phone, D.4_handle, D.5_domain}
  name-only cluster = flagged cluster whose UNION of member fired_signals
                      contains no deterministic edge (only D.1_name and/or
                      D.8_stylometric, the measured-no-op reinforcement edge).
  Because D.1 buckets are keyed by EXACT sorted organizer token-set and no
  other edge type exists in these clusters, every name-only cluster carries
  exactly one name token-set (asserted below).
  current c_rule: det-edge OR >=2 platforms OR >=3 members; C_hard also keeps
  any takedown campaign (carve-out). fraud_hard = A_hard+B_hard+C_hard >= 2.

Candidate gates re-scored on the canonical tier (A_hard/B_hard frozen; only
the C evidence rule for NAME-ONLY clusters changes; det-edge clusters and the
takedown carve-out are untouched in every gate):
  (a)  name-only clusters count only if cross-platform (>=2 platforms).
       Same-platform >=3-member name-only clusters no longer count.
  (b)  gate (a) AND the cluster's name token-set appears on <= 3 campaigns
       in the whole corpus (rarity gate).
  (c)  name-only clusters count only if they pass the CURRENT structural rule
       AND mean within-cluster SBERT cosine < T (different narratives under
       one name; same-story clusters are follow-ups/reposts, judged benign).
  (ac) gate (a) AND gate (c)'s cosine condition.

Outputs:
  printed report (all numbers cited in the analysis)
  name_only_cluster_audit_clusters.csv  (per-cluster metadata for spot reads)
"""
import csv, sys, collections, re
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                  # ccs2026/

CONSENSUS = HERE / "combined_hardened_consensus.csv"
D_FLAGS   = ROOT / "03_detection/detectors/outputs/detector_D_flags.csv"
CORPUS    = ROOT / "02_data_filtration/filtered_dataset_v4.csv"
EMB_NPY   = ROOT / "03_detection/features/sbert_embeddings.npy"
EMB_ORDER = ROOT / "03_detection/features/sbert_url_order.csv"
OUT_CLUST = HERE / "name_only_cluster_audit_clusters.csv"

DET_EDGES = {"D.2_email", "D.3_phone", "D.4_handle", "D.5_domain"}
COS_T_SWEEP = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85]
COS_T_CHOSEN = 0.75          # justified from spot reads (see report text)
RARITY_MAX = 3               # gate (b): name token-set on <=3 corpus campaigns

csv.field_size_limit(sys.maxsize)


def organizer_token_set(name):
    """EXACT copy of detector_D_identity.py normalization."""
    if not isinstance(name, str):
        return None
    toks = re.findall(r"\w+", name.lower())
    toks = [t for t in toks if len(t) >= 2]
    if len(toks) < 2:
        return None
    return tuple(sorted(toks))


# ---------------------------------------------------------------- load canon
cons = {}
with open(CONSENSUS, newline="") as f:
    for r in csv.DictReader(f):
        cons[r["url"]] = r
N = len(cons)

dflags = {}
clusters = collections.defaultdict(list)                # cid -> [urls]
cluster_sig = collections.defaultdict(set)              # cid -> union signals
cluster_meta = {}                                       # cid -> (size, plats)
with open(D_FLAGS, newline="") as f:
    for r in csv.DictReader(f):
        dflags[r["url"]] = r
        if r["flag"] == "1":
            cid = r["cluster_id"]
            clusters[cid].append(r["url"])
            cluster_sig[cid].update(t for t in r["fired_signals"].split(";") if t)
            cluster_meta[cid] = (int(float(r["cluster_size"])),
                                 int(float(r["cluster_platforms"])))

# corpus: organizer names, platform, text
name_freq = collections.Counter()                       # token_set -> n campaigns
name_platforms = collections.defaultdict(set)           # token_set -> platforms
org_of, title_of, desc_of, plat_of = {}, {}, {}, {}
with open(CORPUS, newline="") as f:
    for r in csv.DictReader(f):
        u = r["url"]
        org_of[u] = r.get("organizer") or ""
        title_of[u] = r.get("title") or ""
        desc_of[u] = (r.get("description") or "")[:600]
        plat_of[u] = r.get("platform") or "?"
        ts = organizer_token_set(r.get("organizer"))
        if ts:
            name_freq[ts] += 1
            name_platforms[ts].add(r.get("platform") or "?")

# embeddings
emb = np.load(EMB_NPY, mmap_mode="r")
emb_row = {}
with open(EMB_ORDER, newline="") as f:
    rd = csv.reader(f)
    next(rd)
    for i, row in enumerate(rd):
        emb_row[row[0]] = i

# ------------------------------------------------- sanity: reproduce c_rule
cluster_has_det = {cid: bool(sigs & DET_EDGES) for cid, sigs in cluster_sig.items()}

def c_rule_base(u):
    d = dflags.get(u)
    if not d or d["flag"] != "1":
        return False
    cid = d["cluster_id"]
    size, plats = cluster_meta[cid]
    return cluster_has_det[cid] or plats >= 2 or size >= 3

mismatch = sum(1 for u, r in cons.items() if int(r["c_rule"]) != int(c_rule_base(u)))
print(f"[sanity] c_rule recomputation mismatches vs canonical CSV: {mismatch} of {N:,}")
assert mismatch == 0, "c_rule recomputation does not match canonical file"

base_fraud = sum(int(r["fraud_hard"]) for r in cons.values())
base_takedown_in_tier = sum(1 for r in cons.values()
                            if int(r["fraud_hard"]) and int(r["takedown"]))
print(f"[sanity] canonical fraud_hard = {base_fraud:,}   takedowns in tier = {base_takedown_in_tier}")

# ================================================== PART 1: collision analysis
print("\n" + "=" * 74)
print("PART 1 — NAME COLLISION ANALYSIS")
print("=" * 74)

sizes = collections.Counter(name_freq.values())
tot_ts = len(name_freq)
multi = sum(1 for ts, n in name_freq.items() if n > 1)
multi_plat = sum(1 for ts in name_freq if len(name_platforms[ts]) > 1)
print(f"distinct organizer name token-sets (>=2 tokens): {tot_ts:,}")
print(f"  token-sets on >1 campaign:  {multi:,} ({100*multi/tot_ts:.2f}%)")
print(f"  token-sets on >1 PLATFORM:  {multi_plat:,} ({100*multi_plat/tot_ts:.2f}%)")
print("bucket-size distribution (campaigns per token-set):")
big = 0
for s in sorted(sizes):
    if s <= 10:
        print(f"    size {s:>3}: {sizes[s]:,}")
    else:
        big += sizes[s]
print(f"    size >10: {big:,}  (detector share-cap drops these buckets)")

# name-only clusters
name_only = {cid for cid, sigs in cluster_sig.items()
             if "D.1_name" in sigs and not (sigs & DET_EDGES)}
det_clusters = set(cluster_sig) - name_only
print(f"\nflagged clusters total: {len(cluster_sig):,}  "
      f"(name-only: {len(name_only):,}, with det edge: {len(det_clusters):,})")
print(f"flagged campaigns total: {sum(len(v) for v in clusters.values()):,}  "
      f"(in name-only clusters: {sum(len(clusters[c]) for c in name_only):,})")

# assert one token-set per name-only cluster & compute cluster name freq
cluster_ts = {}
hetero = 0
for cid in name_only:
    tss = {organizer_token_set(org_of.get(u)) for u in clusters[cid]}
    tss.discard(None)
    if len(tss) != 1:
        hetero += 1
        cluster_ts[cid] = max(tss, key=lambda t: name_freq[t]) if tss else None
    else:
        cluster_ts[cid] = next(iter(tss))
print(f"name-only clusters with >1 distinct token-set (expected 0): {hetero}")

def passes_current(cid):
    size, plats = cluster_meta[cid]
    return plats >= 2 or size >= 3            # name-only ⇒ no det edge

no_pass = {cid for cid in name_only if passes_current(cid)}
no_pass_xplat = {cid for cid in no_pass if cluster_meta[cid][1] >= 2}
no_pass_same3 = no_pass - no_pass_xplat
print(f"\nname-only clusters PASSING current evidence rule: {len(no_pass):,}")
print(f"    via cross-platform (>=2 plats):        {len(no_pass_xplat):,}")
print(f"    same-platform, >=3 members only:       {len(no_pass_same3):,}")

def tier_members(cids):
    mem = [u for c in cids for u in clusters[c]]
    fr = [u for u in mem if int(cons[u]["fraud_hard"])]
    piv = [u for u in fr
           if int(cons[u]["A_hard"]) + int(cons[u]["B_hard"]) == 1
           and int(cons[u]["C_hard"])]
    return mem, fr, piv

for label, cids in [("ALL name-only passing", no_pass),
                    ("  cross-platform", no_pass_xplat),
                    ("  same-platform >=3", no_pass_same3)]:
    mem, fr, piv = tier_members(cids)
    print(f"{label}: members {len(mem):,} | in fraud tier {len(fr):,} "
          f"| C-pivotal fraud (A+B=1, would drop w/o C) {len(piv):,}")

# ============================================ PART 2: narrative similarity
print("\n" + "=" * 74)
print("PART 2 — WITHIN-CLUSTER SBERT SIMILARITY (name-only clusters with >=1")
print("         fraud-tier member)")
print("=" * 74)

def mean_pairwise_cos(urls):
    idx = [emb_row[u] for u in urls if u in emb_row]
    if len(idx) < 2:
        return None
    V = np.asarray(emb[sorted(idx)], dtype=np.float32)
    S = V @ V.T
    n = len(idx)
    return float((S.sum() - np.trace(S)) / (n * (n - 1)))

fraud_no = [cid for cid in name_only
            if any(int(cons[u]["fraud_hard"]) for u in clusters[cid])]
rows_out = []
cos_of = {}
for cid in fraud_no:
    mc = mean_pairwise_cos(clusters[cid])
    cos_of[cid] = mc
    size, plats = cluster_meta[cid]
    ts = cluster_ts.get(cid)
    fr = [u for u in clusters[cid] if int(cons[u]["fraud_hard"])]
    piv = [u for u in fr if int(cons[u]["A_hard"]) + int(cons[u]["B_hard"]) == 1]
    rows_out.append(dict(
        cluster_id=cid, size=size, platforms=plats,
        passes_current=int(passes_current(cid)),
        cross_platform=int(plats >= 2),
        name="|".join(ts) if ts else "",
        name_corpus_freq=name_freq[ts] if ts else "",
        name_n_platforms=len(name_platforms[ts]) if ts else "",
        mean_cos=f"{mc:.4f}" if mc is not None else "",
        n_fraud=len(fr), n_c_pivotal=len(piv),
        n_takedown=sum(int(cons[u]["takedown"]) for u in clusters[cid]),
        member_platforms=";".join(sorted({plat_of.get(u, "?") for u in clusters[cid]})),
        urls=" ".join(sorted(clusters[cid])),
    ))
rows_out.sort(key=lambda r: (r["mean_cos"] == "", r["mean_cos"]))
with open(OUT_CLUST, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
    w.writeheader()
    w.writerows(rows_out)

vals = np.array([cos_of[c] for c in fraud_no if cos_of[c] is not None])
print(f"name-only clusters w/ >=1 fraud-tier member: {len(fraud_no):,} "
      f"({len(vals):,} with computable similarity)")
if len(vals):
    qs = np.percentile(vals, [0, 10, 25, 50, 75, 90, 100])
    print("mean-pairwise-cosine distribution: "
          + "  ".join(f"p{p}={v:.3f}" for p, v in zip([0,10,25,50,75,90,100], qs)))
    hist_edges = [0, .3, .5, .6, .7, .75, .8, .9, 1.01]
    for lo, hi in zip(hist_edges, hist_edges[1:]):
        n = int(((vals >= lo) & (vals < hi)).sum())
        print(f"    [{lo:.2f},{hi:.2f}): {n}")
print(f"[written] per-cluster metadata -> {OUT_CLUST}")

# ===================================================== PART 3: candidate gates
print("\n" + "=" * 74)
print("PART 3 — GATES RE-SCORED ON THE CANONICAL TIER")
print("=" * 74)

def gate_rule(cid, mode, cos_t=COS_T_CHOSEN):
    """Does this cluster confer C evidence under the gate?"""
    size, plats = cluster_meta[cid]
    if cid not in name_only:                       # det-edge clusters untouched
        return cluster_has_det[cid] or plats >= 2 or size >= 3
    cur = plats >= 2 or size >= 3
    if mode == "current":
        return cur
    if mode == "a":                                # cross-platform only
        return plats >= 2
    if mode == "b":                                # (a) + name rarity
        ts = cluster_ts.get(cid)
        return plats >= 2 and ts is not None and name_freq[ts] <= RARITY_MAX
    if mode == "c":                                # current + low narrative sim
        mc = cos_of.get(cid, mean_pairwise_cos(clusters[cid]))
        return cur and mc is not None and mc < cos_t
    if mode == "ac":                               # (a) + low narrative sim
        mc = cos_of.get(cid, mean_pairwise_cos(clusters[cid]))
        return plats >= 2 and mc is not None and mc < cos_t
    if mode == "none":                             # name-only never counts
        return False
    raise ValueError(mode)

def score_gate(mode, cos_t=COS_T_CHOSEN):
    out = dict(fraud=0, corr=0, text=0, ggf=0, ggf_text=0, td_in_tier=0,
               lost=[], lost_td=0, lost_rep=0, kept_no=set(), removed_no=set())
    for u, r in cons.items():
        A, B, C0 = int(r["A_hard"]), int(r["B_hard"]), int(r["C0"])
        td, rep = int(r["takedown"]), int(r["rep_hard"])
        d = dflags.get(u)
        flagged = bool(d and d["flag"] == "1")
        rule = gate_rule(d["cluster_id"], mode, cos_t) if flagged else False
        C = int(C0 and (rule or td))               # takedown carve-out kept
        fraud = (A + B + C) >= 2
        corr = td or rep or (C0 and rule)
        if fraud:
            out["fraud"] += 1
            out["corr" if corr else "text"] += 1
            out["td_in_tier"] += td
            if r["platform"] == "GoGetFunding":
                out["ggf"] += 1
                if not corr:
                    out["ggf_text"] += 1
        if int(r["fraud_hard"]) and not fraud:
            out["lost"].append(u)
            out["lost_td"] += td
            out["lost_rep"] += rep
        if flagged and d["cluster_id"] in name_only and passes_current(d["cluster_id"]):
            (out["kept_no"] if rule else out["removed_no"]).add(d["cluster_id"])
    return out

BASE = dict(fraud=base_fraud,
            corr=sum(1 for r in cons.values() if int(r["fraud_hard"]) and int(r["corroborated"])),
            text=sum(1 for r in cons.values() if int(r["fraud_hard"]) and not int(r["corroborated"])),
            ggf=sum(1 for r in cons.values() if int(r["fraud_hard"]) and r["platform"] == "GoGetFunding"))
print(f"baseline: fraud {BASE['fraud']:,} = {BASE['corr']:,} corroborated + "
      f"{BASE['text']:,} text-only | GGF {BASE['ggf']:,} | takedowns {base_takedown_in_tier}")

def report(tag, g):
    print(f"\nGATE {tag}: fraud {g['fraud']:,} (delta {g['fraud']-BASE['fraud']:+,}) = "
          f"{g['corr']:,} corr ({g['corr']-BASE['corr']:+,}) + "
          f"{g['text']:,} text-only ({g['text']-BASE['text']:+,})")
    print(f"    GGF {g['ggf']:,} ({g['ggf']-BASE['ggf']:+,}, text-only {g['ggf_text']:,}) | "
          f"takedowns in tier {g['td_in_tier']} (lost {g['lost_td']}) | "
          f"rep-corroborated fraud lost {g['lost_rep']}")
    print(f"    name-only passing clusters: kept {len(g['kept_no'])}, removed {len(g['removed_no'])} "
          f"| campaigns demoted from tier {len(g['lost'])}")

g_cur = score_gate("current")
assert g_cur["fraud"] == base_fraud and not g_cur["lost"], "gate replication broken"
print("[sanity] mode=current reproduces the canonical tier exactly")

GA = score_gate("a");  report("(a)  cross-platform-only", GA)
GB = score_gate("b");  report(f"(b)  (a) + name freq<= {RARITY_MAX}", GB)
for t in COS_T_SWEEP:
    gc = score_gate("c", t)
    print(f"    [sweep c, T={t:.2f}] fraud {gc['fraud']:,} ({gc['fraud']-BASE['fraud']:+,}) "
          f"td-lost {gc['lost_td']} rep-lost {gc['lost_rep']}")
GC = score_gate("c");  report(f"(c)  current + cos< {COS_T_CHOSEN}", GC)
GAC = score_gate("ac"); report(f"(ac) cross-platform + cos< {COS_T_CHOSEN}", GAC)
GN = score_gate("none"); report("(d)  name-only never counts", GN)

# which rep-corroborated fraud does each gate lose?
for tag, g in [("a", GA), ("c", GC), ("d", GN)]:
    lost_rep = [u for u in g["lost"] if int(cons[u]["rep_hard"])]
    if lost_rep:
        print(f"gate ({tag}) rep-corroborated losses: {lost_rep}")

# cross-platform name-only fraud contributors that gates (a)/(b) KEEP
keep_x = [u for u, r in cons.items()
          if int(r["fraud_hard"]) and dflags[u]["flag"] == "1"
          and dflags[u]["cluster_id"] in no_pass_xplat
          and int(r["A_hard"]) + int(r["B_hard"]) == 1]
print("\ncross-platform name-only C-pivotal fraud (kept by gates a/b):")
for u in keep_x:
    r = cons[u]
    print(f"  cid={dflags[u]['cluster_id']} td={r['takedown']} rep={r['rep_hard']} {u}")

# demoted-campaign lists for spot reading
for tag, g in [("a", GA), ("b", GB), ("c", GC), ("ac", GAC), ("d", GN)]:
    by_cl = collections.Counter(dflags[u]["cluster_id"] for u in g["lost"])
    print(f"\ngate ({tag}) demotes {len(g['lost'])} campaigns from "
          f"{len(by_cl)} clusters; top clusters: "
          + ", ".join(f"{c}(n={n})" for c, n in by_cl.most_common(8)))
