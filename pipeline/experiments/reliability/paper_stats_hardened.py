#!/usr/bin/env python3
"""
Authoritative paper-facing statistics from the hardened 3-detector consensus.

Single source of truth for the CCS paper number refresh. Reads
combined_hardened_consensus.csv (canonical labels) plus the corpus, takedown
crawl, and redirection cache, and emits every consensus-dependent number the
paper reports. Output: paper_stats_hardened.txt next to this script.
"""
import csv, collections, statistics
from pathlib import Path

ROOT  = Path(__file__).resolve().parents[2]
HERE  = Path(__file__).resolve().parent
CONS  = HERE / "combined_hardened_consensus.csv"
CORPUS= ROOT / "02_data_filtration" / "filtered_dataset_v4.csv"
TAKED = ROOT / "experiments" / "tracking" / "takedown_status.csv"
REDIR = ROOT / "03_detection" / "intel" / "redirection_flags.csv"
OUT   = HERE / "paper_stats_hardened.txt"

RATES_TO_USD = {"USD": 1.00, "$": 1.00, "EUR": 1.05, "GBP": 1.25, "CHF": 1.10}
ROUND_TARGETS = {1000, 2000, 5000, 10000, 15000, 20000, 25000, 50000, 100000}

def fnum(x):
    try: return float(str(x).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError): return None

rows = []
with open(CONS, newline="") as f:
    for r in csv.DictReader(f):
        rows.append(r)
N = len(rows)
def I(r, k): return int(r[k])

out = []
def p(s=""): out.append(s)

# ------------------------------------------------------------------ tiers
fraud   = [r for r in rows if I(r,"score_hard") >= 2]
susp    = [r for r in rows if I(r,"score_hard") == 1]
unknown = [r for r in rows if I(r,"score_hard") == 0]
p("="*76); p("PAPER STATS  (hardened 3-detector consensus, canonical)"); p("="*76)
p(f"Corpus: {N:,}")
p(f"Fraud:      {len(fraud):,}  ({100*len(fraud)/N:.2f}%)")
p(f"Suspicious: {len(susp):,}  ({100*len(susp)/N:.2f}%)")
p(f"Unknown:    {len(unknown):,}  ({100*len(unknown)/N:.2f}%)")

# ------------------------------------------------------------------ detectors
A = sum(I(r,"A_hard") for r in rows); B = sum(I(r,"B_hard") for r in rows); C = sum(I(r,"C_hard") for r in rows)
rep = sum(I(r,"rep_hard") for r in rows); heur = sum(I(r,"heur_hard") for r in rows)
p("")
p(f"Detector fires: A {A:,} ({100*A/N:.2f}%)   B {B:,} ({100*B/N:.2f}%)   C {C:,} ({100*C/N:.2f}%)")
p(f"  A arms: reputation(sanitized) {rep:,}   heuristic {heur:,}   (union w/ carve-out {A:,})")
# A.5 hardened contributing fires
redir2 = 0
with open(REDIR, newline="") as f:
    for r in csv.DictReader(f):
        v = fnum(r.get("n_phrases"))
        if v and v >= 2: redir2 += 1
p(f"  A.5 redirection at n_phrases>=2 (corpus): {redir2:,} ({100*redir2/N:.2f}%)")

# ------------------------------------------------------------------ overlap / independence
Bset = [r for r in rows if I(r,"B_hard")]
ab   = sum(1 for r in Bset if I(r,"heur_hard"))
p("")
p(f"A_heuristic AND B (hardened): {ab:,} = {100*ab/len(Bset):.1f}% of B fires" if Bset else "no B")
combo = collections.Counter()
for r in fraud:
    key = ("A" if I(r,"A_hard") else "") + ("B" if I(r,"B_hard") else "") + ("C" if I(r,"C_hard") else "")
    combo[key] += 1
p("Fraud-tier detector combinations:")
for k, v in combo.most_common():
    p(f"  {k:>3}: {v:,}  ({100*v/len(fraud):.1f}%)")
for d in "ABC":
    n = sum(1 for r in fraud if I(r, f"{d}_hard"))
    p(f"  {d} contributes to {n:,} of {len(fraud):,} fraud ({100*n/len(fraud):.1f}%)")

# ------------------------------------------------------------------ corroboration stratification
corr = sum(1 for r in fraud if I(r,"corroborated"))
strict = sum(1 for r in fraud if I(r,"hard_strict"))
p("")
p(f"Fraud corroboration: independent-signal {corr:,} ({100*corr/len(fraud):.1f}%)   "
  f"text-only {len(fraud)-corr:,} ({100*(len(fraud)-corr)/len(fraud):.1f}%)   strict {strict:,}")

# ------------------------------------------------------------------ threshold sweep + leave-one-out
for t in (1, 2, 3):
    n = sum(1 for r in rows if I(r,"score_hard") >= t)
    p(f"Threshold >= {t}: {n:,} campaigns")
for drop in "ABC":
    keep = [d for d in "ABC" if d != drop]
    n = sum(1 for r in rows if all(I(r, f"{d}_hard") for d in keep))
    p(f"Leave-{drop}-out (both remaining fire): {n:,}")

# ------------------------------------------------------------------ per-platform
p(""); p(f"{'platform':<16}{'campaigns':>10}{'fraud':>8}{'rate%':>8}{'text-only':>10}{'txt%':>7}")
plat = collections.defaultdict(lambda: [0,0,0])
for r in rows:
    b = plat[r["platform"]]; b[0]+=1
    if I(r,"score_hard")>=2:
        b[1]+=1
        if not I(r,"corroborated"): b[2]+=1
for k,(n,fr,txt) in sorted(plat.items(), key=lambda kv: -kv[1][1]):
    rate = 100*fr/n
    p(f"{k:<16}{n:>10,}{fr:>8,}{rate:>8.2f}{txt:>10,}{(100*txt/fr if fr else 0):>7.1f}")
ggf_f = plat["GoGetFunding"][1]
p(f"GoGetFunding share of fraud tier: {ggf_f:,}/{len(fraud):,} = {100*ggf_f/len(fraud):.2f}%")

# ------------------------------------------------------------------ takedown crawl
tk = {}
with open(TAKED, newline="") as f:
    for r in csv.DictReader(f):
        tk[r["url"]] = r["category"]
tiermap = {r["url"]: I(r,"score_hard") for r in rows}
checked_fraud   = [u for u in tk if tiermap.get(u, -1) >= 2]
checked_susp    = [u for u in tk if tiermap.get(u, -1) == 1]
checked_unknown = [u for u in tk if tiermap.get(u, -1) == 0]
platmap = {r["url"]: r["platform"] for r in rows}
def bp_event(u):
    # Betterplace shut down its fundraising_events feature platform-wide;
    # the July-2026 control run (betterplace_control_july2026.csv) shows 100%
    # 404 for flagged AND unflagged event pages, so these 404s are not
    # per-campaign takedowns and are reported separately.
    return platmap.get(u) == "Betterplace" and "/fundraising_events/" in u
p("")
p(f"Reachability crawl (May 2026 sample, re-tiered to the decoupled labels):")
for label, checked in (("fraud", checked_fraud), ("suspicious", checked_susp), ("unknown", checked_unknown)):
    td   = [u for u in checked if tk[u] == "taken_down"]
    bp   = [u for u in td if bp_event(u)]
    gen  = [u for u in td if not bp_event(u)]
    exch = [u for u in checked if not bp_event(u)]
    live = sum(1 for u in checked if tk[u] == "live")
    p(f"  {label:<10} checked {len(checked):,}  live {live:,}  404 {len(td):,} "
      f"(BP-event {len(bp):,}, other {len(gen):,})  "
      f"404-rate excl BP events {100*len(gen)/len(exch) if exch else 0:.2f}% ({len(gen)}/{len(exch):,})")
    if gen:
        p(f"    non-BP-event 404s by platform: {dict(collections.Counter(platmap[u] for u in gen))}")

# ------------------------------------------------------------------ money + behavior
meta = {}
with open(CORPUS, newline="") as f:
    for r in csv.DictReader(f):
        meta[r["url"]] = r
def usd(r, field):
    v = fnum(r.get(field))
    if v is None: return None
    return v * RATES_TO_USD.get((r.get("currency") or "").strip(), 1.00)

# credible-amount platforms: median goal (fraud+unknown with goal>0) >= $100
goals_by_plat = collections.defaultdict(list)
for r in rows:
    m = meta.get(r["url"]);
    if not m: continue
    g = usd(m, "goal_amount")
    if g and g > 0: goals_by_plat[r["platform"]].append(g)
credible = {k for k, v in goals_by_plat.items() if len(v) >= 20 and statistics.median(v) >= 100}
p("")
p(f"Credible-amount platforms (median goal >= $100, n>=20): {sorted(credible)}")

fr_money = []
per_plat_money = collections.defaultdict(float)
for r in fraud:
    m = meta.get(r["url"])
    if not m or r["platform"] == "GoGetFunding": continue   # GGF amount field = donor count
    v = usd(m, "raised_amount")
    if v is not None:
        fr_money.append(v); per_plat_money[r["platform"]] += v
p(f"Fraud money (USD-eq, GGF excluded): N reporting {len(fr_money):,}, total ${sum(fr_money):,.0f}, "
  f"median ${statistics.median(fr_money):,.0f}, max ${max(fr_money):,.0f}" if fr_money else "no money data")
p("  per-platform totals: " + ", ".join(f"{k} ${v:,.0f}" for k, v in sorted(per_plat_money.items(), key=lambda kv: -kv[1]) if v > 0))

def behavior(sub):
    goals, ratios, reached, rounds = [], [], 0, 0
    n_g = 0
    for r in sub:
        m = meta.get(r["url"])
        if not m or r["platform"] not in credible: continue
        g = usd(m, "goal_amount"); ra = usd(m, "raised_amount")
        if not g or g <= 0: continue
        n_g += 1; goals.append(g)
        if fnum(m.get("goal_amount")) in ROUND_TARGETS: rounds += 1
        if ra is not None:
            ratios.append(min(ra/g, 10.0))
            if ra >= g: reached += 1
    return dict(n=n_g, med_goal=statistics.median(goals) if goals else 0,
                round_pct=100*rounds/n_g if n_g else 0,
                med_ratio=100*statistics.median(ratios) if ratios else 0,
                reached_pct=100*reached/len(ratios) if ratios else 0)
bf, bu = behavior(fraud), behavior(unknown)
p("")
p(f"Behavior (credible platforms): FRAUD n={bf['n']:,} med_goal=${bf['med_goal']:,.0f} "
  f"round={bf['round_pct']:.1f}% med_raised/goal={bf['med_ratio']:.1f}% reached={bf['reached_pct']:.1f}%")
p(f"Behavior (credible platforms): UNKNOWN n={bu['n']:,} med_goal=${bu['med_goal']:,.0f} "
  f"round={bu['round_pct']:.1f}% med_raised/goal={bu['med_ratio']:.1f}% reached={bu['reached_pct']:.1f}%")

report = "\n".join(out)
print(report)
OUT.write_text(report + "\n")
print(f"\n[written] {OUT}")
