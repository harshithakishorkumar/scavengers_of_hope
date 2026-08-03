#!/usr/bin/env python3
"""
Adopt the takedown-decoupled consensus (2026-07-23).

Why. The July-2026 control experiment (experiments/tracking/
betterplace_control_test.py, data betterplace_control_july2026.csv) proved
that the May-crawl 404s on Betterplace fundraising_events pages are a
platform-wide feature removal, not fraud enforcement: 99/99 Fraud-tier event
pages AND 198/198 unflagged control event pages return 404, while /projects/
pages return 200 for both tiers. Takedown status is therefore not per-campaign
fraud evidence in this corpus and must not condition the consensus.

What this script changes, relative to the canonical
combined_hardened_consensus.csv (backup: *_PRE_TAKEDOWNFIX.csv):

  1. Detector A carve-out: the takedown trigger is removed. The carve-out now
     preserves a baseline A flag only when the campaign carries a gated
     deterministic Detector-C identity signal:
         A_hard' = rep_hard OR heur_hard OR (A0 AND c_deterministic')
  2. Detector C: the takedown carve-out is removed. C counts toward the
     consensus only through the evidence rule:
         C_hard' = C0 AND c_rule'
  3. Phone share-cap fix, applied at the artifact level. detector_D_identity.py
     caps every identity bucket at MAX_DOMAIN_SHARE=10 campaigns; the
     2026-07-01 audit found phone buckets escaped the cap in the shipped run
     (one 16-campaign single-organization clique). The shipped
     detector_D_flags.csv predates the committed fix, so this script rebuilds
     phone buckets from campaign_contacts_llm.jsonl with the same
     normalization and revokes deterministic status from clusters whose only
     deterministic edges are D.3_phone edges in over-cap buckets.
  4. Corroboration: takedown no longer counts as independent corroboration:
         corroborated' = rep_hard OR (C0 AND c_rule')
  5. score_hard / fraud_hard / gated tier recomputed from A', B, C'.

The takedown column is retained in the CSV as an OUTCOME field (used by the
tracking analyses); it simply no longer feeds any label.

Output: rewrites combined_hardened_consensus.csv in place and writes
adopt_takedown_decoupling_report.txt next to this script.
"""
import csv, json, re, sys, collections
from pathlib import Path

csv.field_size_limit(sys.maxsize)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANON    = HERE / "combined_hardened_consensus.csv"
BACKUP   = HERE / "combined_hardened_consensus_PRE_TAKEDOWNFIX.csv"
D_FLAGS  = ROOT / "03_detection" / "detectors" / "outputs" / "detector_D_flags.csv"
CONTACTS = ROOT / "03_detection" / "intel" / "campaign_contacts_llm.jsonl"
B_FEATS  = ROOT / "03_detection" / "llm" / "llm_features_qwen72b.csv"
TAKED    = ROOT / "experiments" / "tracking" / "takedown_status.csv"
OUT_TXT  = HERE / "adopt_takedown_decoupling_report.txt"

MAX_SHARE = 10          # mirrors detector_D_identity.py MAX_DOMAIN_SHARE
DET_EDGES = {"D.2_email", "D.3_phone", "D.4_handle", "D.5_domain"}
NONPHONE_DET = DET_EDGES - {"D.3_phone"}

out_lines = []
def p(s=""):
    print(s)
    out_lines.append(s)

def is_true(x): return str(x).strip().lower() in ("1", "true", "yes")

# ------------------------------------------------------------------ inputs
if not BACKUP.exists():
    sys.exit("refusing to run: backup combined_hardened_consensus_PRE_TAKEDOWNFIX.csv missing")

# Always transform from the frozen backup so the script is idempotent.
rows = list(csv.DictReader(open(BACKUP, newline="")))
INT_COLS = ["A0","B0","C0","score0","fraud0","A_hard","B_hard","C_hard","rep_hard",
            "heur_hard","c_rule","corroborated","score_hard","fraud_hard","takedown",
            "c_deterministic","reputation","hard_corroboration","hard_strict"]
for r in rows:
    for k in INT_COLS: r[k] = int(r[k])
N = len(rows)

d_rows = list(csv.DictReader(open(D_FLAGS, newline="")))
d_by_url = {r["url"]: r for r in d_rows}
cluster_members = collections.defaultdict(list)
for r in d_rows:
    if is_true(r.get("flag")):
        cluster_members[r.get("cluster_id")].append(r["url"])

def fired(dr):
    return {t.strip() for t in (dr.get("fired_signals") or "").replace(";", ",").split(",") if t.strip()}

# ------------------------------------------------------------ phone buckets
phones_by_url = {}
by_phone = collections.defaultdict(set)
with open(CONTACTS) as f:
    for line in f:
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        u = d.get("url")
        if not u: continue
        ns = []
        for ph in (d.get("phones") or []):
            n = re.sub(r"\D", "", ph or "")
            if len(n) >= 7:
                ns.append(n); by_phone[n].add(u)
        phones_by_url[u] = ns
overcap = {n for n, us in by_phone.items() if len(us) > MAX_SHARE}
p(f"phone buckets: {len(by_phone):,}; over-cap (>{MAX_SHARE}): {len(overcap)} "
  f"{sorted((len(by_phone[n]) for n in overcap), reverse=True)}")

def phone_edge_undercap(url, cluster_urls):
    """Campaign has a D.3 edge from an under-cap bucket shared inside its cluster."""
    for n in phones_by_url.get(url, []):
        if n in overcap: continue
        if len(by_phone[n] & set(cluster_urls)) >= 2:
            return True
    return False

# Clusters whose deterministic basis dies with the phone cap: no member has a
# non-phone deterministic edge, and no member retains an under-cap phone edge.
affected_clusters = set()
for cid, members in cluster_members.items():
    det_members = [u for u in members if fired(d_by_url[u]) & DET_EDGES]
    if not det_members: continue
    if any(fired(d_by_url[u]) & NONPHONE_DET for u in members): continue
    if any(phone_edge_undercap(u, members) for u in members): continue
    affected_clusters.add(cid)
p(f"clusters losing deterministic basis to the phone cap: {len(affected_clusters)} "
  f"{sorted((cid, len(cluster_members[cid])) for cid in affected_clusters)}")

def campaign_loses_det(url):
    dr = d_by_url.get(url)
    if dr is None: return False
    return dr.get("cluster_id") in affected_clusters

# ------------------------------------------------------------ transformation
tally = collections.Counter()
for r in rows:
    u = r["url"]
    lose = campaign_loses_det(u)
    c_det = 0 if lose else r["c_deterministic"]
    c_rule = 0 if lose else r["c_rule"]
    if lose and (r["c_deterministic"] or r["c_rule"]): tally["phone_cap_det_revoked"] += 1

    A_new = 1 if (r["rep_hard"] or r["heur_hard"] or (r["A0"] and c_det)) else 0
    C_new = 1 if (r["C0"] and c_rule) else 0
    corr  = 1 if (r["rep_hard"] or (r["C0"] and c_rule)) else 0
    score = A_new + r["B_hard"] + C_new

    if r["A_hard"] and not A_new: tally["A_fires_dropped"] += 1
    if r["C_hard"] and not C_new: tally["C_fires_dropped"] += 1
    if A_new and not (r["rep_hard"] or r["heur_hard"]): tally["A_carveout_saves"] += 1

    r["c_deterministic"], r["c_rule"] = c_det, c_rule
    r["A_hard"], r["C_hard"], r["corroborated"] = A_new, C_new, corr
    r["score_hard"], r["fraud_hard"] = score, int(score >= 2)
    r["gated_tier"] = "fraud" if score >= 2 else ("suspicious" if score == 1 else "unknown")

p(f"tallies: {dict(tally)}")

# ------------------------------------------------------------------ write CSV
fieldnames = list(rows[0].keys())
with open(CANON, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r in rows: w.writerow(r)
p(f"[written] {CANON}")

# ================================================================== report
old = {r["url"]: r for r in csv.DictReader(open(BACKUP, newline=""))}
fraud   = [r for r in rows if r["score_hard"] >= 2]
susp    = [r for r in rows if r["score_hard"] == 1]
unknown = [r for r in rows if r["score_hard"] == 0]
p(); p("=" * 76); p("TAKEDOWN-DECOUPLED CONSENSUS (canonical)"); p("=" * 76)
p(f"Corpus {N:,}   Fraud {len(fraud):,} ({100*len(fraud)/N:.2f}%)   "
  f"Suspicious {len(susp):,} ({100*len(susp)/N:.2f}%)   Unknown {len(unknown):,} ({100*len(unknown)/N:.2f}%)")

A_n = sum(r["A_hard"] for r in rows); B_n = sum(r["B_hard"] for r in rows); C_n = sum(r["C_hard"] for r in rows)
rep_n = sum(r["rep_hard"] for r in rows); heur_n = sum(r["heur_hard"] for r in rows)
p(f"Fires: A {A_n:,} ({100*A_n/N:.2f}%)  [rep {rep_n}, heur {heur_n}, carve-out saves {tally['A_carveout_saves']}]   "
  f"B {B_n:,} ({100*B_n/N:.2f}%)   C {C_n:,} ({100*C_n/N:.2f}%)")

ab = sum(1 for r in rows if r["A_hard"] and r["B_hard"])
ac = sum(1 for r in rows if r["A_hard"] and r["C_hard"])
bc = sum(1 for r in rows if r["B_hard"] and r["C_hard"])
heur_b = sum(1 for r in rows if r["heur_hard"] and r["B_hard"])
rep_b  = sum(1 for r in rows if r["rep_hard"] and r["B_hard"])
p(f"Corpus overlaps: A∩B {ab}   A∩C {ac}   B∩C {bc}   Aheur∩B {heur_b} ({100*heur_b/B_n:.1f}% of B)   Arep∩B {rep_b}")

combo = collections.Counter()
for r in fraud:
    combo[("A" if r["A_hard"] else "") + ("B" if r["B_hard"] else "") + ("C" if r["C_hard"] else "")] += 1
p("Fraud composition: " + "   ".join(f"{k}:{v} ({100*v/len(fraud):.1f}%)" for k, v in combo.most_common()))
for d in ("A_hard", "B_hard", "C_hard"):
    n = sum(1 for r in fraud if r[d])
    p(f"  {d[0]} contributes to {n}/{len(fraud)} fraud ({100*n/len(fraud):.1f}%)")

corr_n = sum(1 for r in fraud if r["corroborated"])
p(f"Corroboration: independent {corr_n}/{len(fraud)} ({100*corr_n/len(fraud):.1f}%)   "
  f"text-only {len(fraud)-corr_n} ({100*(len(fraud)-corr_n)/len(fraud):.1f}%)")

for t in (1, 2, 3):
    p(f"Threshold >= {t}: {sum(1 for r in rows if r['score_hard'] >= t):,}")
p(f"Leave-A-out {sum(1 for r in rows if r['B_hard'] and r['C_hard'])}   "
  f"Leave-B-out {ac}   Leave-C-out {ab}")

floor = sum(1 for r in rows if (r["rep_hard"] or r["heur_hard"]) + r["B_hard"] + r["C_hard"] >= 2)
p(f"Construction-free floor (no A carve-out): {floor}")

p(); p(f"{'platform':<14}{'campaigns':>10}{'unknown':>9}{'susp':>7}{'fraud':>7}{'rate%':>8}{'text-only':>10}")
plat = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
for r in rows:
    b = plat[r["platform"]]; b[0] += 1
    t = r["score_hard"]
    if t >= 2:
        b[3] += 1
        if not r["corroborated"]: b[4] += 1
    elif t == 1: b[2] += 1
    else: b[1] += 1
for k, (n, u_, s_, f_, txt) in sorted(plat.items(), key=lambda kv: (-100*kv[1][3]/kv[1][0], -kv[1][0])):
    p(f"{k:<14}{n:>10,}{u_:>9,}{s_:>7,}{f_:>7,}{100*f_/n:>8.2f}{txt:>10,}")

# ---------------------------------------------------------------- transitions
old_fraud = {u for u, r in old.items() if int(r["score_hard"]) >= 2}
new_fraud = {r["url"] for r in fraud}
dem = sorted(old_fraud - new_fraud); pro = sorted(new_fraud - old_fraud)
p(); p(f"Demoted from Fraud: {len(dem)}   Promoted into Fraud: {len(pro)}")
dem_rows = [r for r in rows if r["url"] in set(dem)]
p("  demoted by platform: " + str(collections.Counter(r['platform'] for r in dem_rows).most_common()))
p(f"  demoted carrying May-crawl takedown: {sum(1 for r in dem_rows if r['takedown'])}")
p("  demoted landing tier: " + str(collections.Counter(r['gated_tier'] for r in dem_rows).most_common()))
phone_dem = [r for r in dem_rows if campaign_loses_det(r["url"])]
p(f"  demoted via phone-cap fix: {len(phone_dem)}")

# ---------------------------------------------------------------- takedown outcomes
tk = {}
for r in csv.DictReader(open(TAKED, newline="")):
    tk[r["url"]] = r["category"]
tiermap = {r["url"]: r["score_hard"] for r in rows}
platmap = {r["url"]: r["platform"] for r in rows}
def is_bp_event(u): return platmap.get(u) == "Betterplace" and "/fundraising_events/" in u
p(); p("May-2026 reachability crawl, re-tiered to the decoupled labels:")
for name, cond in (("fraud", lambda s: s >= 2), ("susp", lambda s: s == 1), ("unknown", lambda s: s == 0)):
    chk = [u for u in tk if u in tiermap and cond(tiermap[u])]
    td  = [u for u in chk if tk[u] == "taken_down"]
    bp  = [u for u in td if is_bp_event(u)]
    gen = [u for u in td if not is_bp_event(u)]
    p(f"  {name:<8} checked {len(chk):,}   404 {len(td)}   BP-event {len(bp)}   genuine {len(gen)} "
      f"{collections.Counter(platmap[u] for u in gen).most_common()}")
    ex_chk = [u for u in chk if not is_bp_event(u)]
    ex_td  = [u for u in ex_chk if tk[u] == "taken_down"]
    p(f"           excl-BP-events: checked {len(ex_chk):,}   404 {len(ex_td)} ({100*len(ex_td)/len(ex_chk) if ex_chk else 0:.2f}%)")

# ---------------------------------------------------------------- B sensitivity
qcols = ["q1_external_payment","q2_deadline_pressure","q3_guilt_language",
         "q4_defensive_language","q5_impersonation_no_consent","q6_tragedy_exploitation",
         "q7_allocation_overpromise","q8_fake_credential","q9_external_verification","q10_multi_channel"]
counts = collections.Counter()
n_ok = 0
with open(B_FEATS, newline="") as f:
    for r in csv.DictReader(f):
        if str(r.get("ok")).lower() not in ("true", "1"): continue
        n_ok += 1
        y = sum(1 for c in qcols if str(r.get(c, "")).lower() == "yes")
        counts[y] += 1
p(); p(f"Detector B fire-rule sensitivity (parsed campaigns {n_ok:,}):")
for t in (3, 4, 5):
    n = sum(v for k, v in counts.items() if k >= t)
    p(f"  >= {t}/10 yes: {n:,} ({100*n/N:.2f}% of corpus)")

# ---------------------------------------------------------------- case cluster
msmiry = [u for u, d in phones_by_url.items() if False]  # placeholder no-op
case = []
with open(CONTACTS) as f:
    for line in f:
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        blob = json.dumps(d).lower()
        if "msmiry" in blob or "xohusnaaxo" in blob:
            case.append(d.get("url"))
p(); p(f"Case-study cluster (msmiry/xohusnaaxo) campaigns: {len(case)}")
for u in case:
    p(f"  tier={tiermap.get(u)}  {u}")

OUT_TXT.write_text("\n".join(out_lines) + "\n")
print(f"\n[report] {OUT_TXT}")
