#!/usr/bin/env python3
"""
Publication figure set for the CCS/NDSS paper. One consistent design language:
Okabe-Ito colorblind-safe palette, serif fonts, matched sizes, color used to
encode MEANING (green = evidence independent of the campaign's own text,
orange = textual/heuristic evidence), not decoration.

Generates (vector PDF into paper/images/ + PNG previews into /tmp scratch):
  fig_signalload.pdf     two-panel lollipop, sub-signal fire rates, color = evidence type
  fig_money.pdf          horizontal lollipop, reported $ raised by platform
  fig_behavior.pdf       paired dumbbells, Fraud vs Unknown behavior (no dual axis)
  fig_corroboration.pdf  per-platform corroborated vs text-only shares (NEW)

All consensus-dependent values are computed live from
combined_hardened_consensus.csv + filtered_dataset_v4.csv. Sub-signal fire
rates are the audited constants from paper_stats_hardened.txt.
"""
import csv, collections, statistics
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT   = Path(__file__).resolve().parents[2]
CONS   = Path(__file__).resolve().parent / "combined_hardened_consensus.csv"
CORPUS = ROOT / "02_data_filtration" / "filtered_dataset_v4.csv"
PDF    = ROOT / "paper" / "images"
PNG    = Path("/tmp/claude-312825/figset"); PNG.mkdir(parents=True, exist_ok=True)

# Okabe-Ito
GREEN, ORANGE, BLUE, SKY, GREY, VERM = "#009E73", "#E69F00", "#0072B2", "#56B4E9", "#8a8a8a", "#D55E00"
IND, TXT = "#3E8E75", "#C87137"   # independent vs textual evidence (muted tints of green/vermillion)
plt.rcParams.update({
    "font.family": "serif", "font.size": 8, "axes.spines.top": False,
    "axes.spines.right": False, "figure.dpi": 150, "savefig.bbox": "tight",
    "pdf.fonttype": 42, "axes.linewidth": 0.7,
})

def save(fig, name):
    fig.savefig(PDF / f"{name}.pdf")
    fig.savefig(PNG / f"{name}.png", dpi=160)
    plt.close(fig)
    print(f"[written] paper/images/{name}.pdf")

def lolli(ax, ys, vals, colors, fmt, lblsize=6.8, dot=34):
    for y, v, c in zip(ys, vals, colors):
        ax.hlines(y, 0, v, color=GREY, lw=0.9, alpha=0.5, zorder=1)
        ax.scatter(v, y, s=dot, color=c, edgecolors="black", linewidths=0.45, zorder=3)
        ax.annotate(fmt(v), (v, y), xytext=(4.5, 0), textcoords="offset points",
                    va="center", fontsize=lblsize)

RATES = {"USD": 1.00, "$": 1.00, "EUR": 1.05, "GBP": 1.25, "CHF": 1.10}
def fnum(x):
    try: return float(str(x).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError): return None

rows = list(csv.DictReader(open(CONS, newline="")))
I = lambda r, k: int(r[k])
fraud = [r for r in rows if I(r, "score_hard") >= 2]

# =============================================================== signalload
# Three bar panels stacked VERTICALLY in one column: every panel's row labels
# share the same left edge, so no panel's labels can visually merge with a
# neighboring panel (the failure mode of the side-by-side layout). Each panel
# keeps its own scale with the exact rate printed at each bar; panel B draws
# the 25% saturation ceiling. Minimal by design: no concentration column
# (those numbers live in the Sub-signal load prose).
# Rates: paper_stats_hardened.txt + detector_D_flags.csv edge counts,
# refreshed 2026-07-23. Fraud-tier concentrations quoted in prose, computed
# 2026-07-24: reuse 42/42, phone 122/130, email 73/87, domain 192/1,179,
# regex>=2 62/200, reputation 1/50; B questions all <=4.0% (Q1 259/6,420).
A_SIGNALS = [
    ("Off-platform routing ($\\geq$2)", 0.20, TXT),
    ("Reputation (VT/IPQS)",            0.05, IND),
    ("Payment-account reuse",           0.04, IND),
]
B_SIGNALS = [
    ("Guilt language",     15.01), ("Multi-channel",      8.40),
    ("External payment",    6.40), ("Deadline pressure",  5.08),
    ("Verification ask",    3.36), ("Allocation",         3.21),
    ("Tragedy exploit.",    2.33), ("Defensive lang.",    1.78),
    ("Fake credential",     1.19), ("Impersonation",      0.79),
]
C_SIGNALS = [
    ("Domain",          1.18), ("Phone",           0.13),
    ("Email",           0.09), ("Payment account", 0.04),
]
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(3.5, 2.72),
                                 gridspec_kw={"height_ratios": [3, 10, 4], "hspace": 0.52})
def panel(ax, items, colors, xmax, title):
    # minimalist: labeled bars only; no axis, no grid, no tick marks
    items = list(reversed(items))
    cols = list(reversed(colors)) if isinstance(colors, list) else colors
    ax.barh(range(len(items)), [s[1] for s in items],
            color=cols, height=0.62, edgecolor="none")
    for y, s in enumerate(items):
        ax.annotate(f"{s[1]:.2f}", (s[1], y), xytext=(3.5, 0),
                    textcoords="offset points", va="center", fontsize=6.2, color="#333333")
    ax.set_yticks(range(len(items))); ax.set_yticklabels([s[0] for s in items], fontsize=6.4)
    ax.set_xlim(0, xmax); ax.set_ylim(-0.65, len(items) - 0.35)
    ax.set_title(title, fontsize=7.2, loc="left", weight="bold", pad=2.5)
    ax.set_xticks([])
    for sp in ("top", "right", "bottom"): ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#bbbbbb")
    ax.tick_params(left=False)
panel(a1, A_SIGNALS, [s[2] for s in A_SIGNALS], 0.245, "Detector A: reputation + routing")
panel(a2, B_SIGNALS, TXT, 28.5, "Detector B: LLM questions")
panel(a3, C_SIGNALS, [IND, IND, IND, IND], 1.42, "Detector C: identity signals")
# saturation ceiling inside panel B: the level at which a question would be
# treated as too common to discriminate; no question reaches it
a2.axvline(25, color="#999999", ls="--", lw=0.9, ymin=0.02, ymax=0.98)
a2.annotate("saturation ceiling (25%)", (25, 4.5), xytext=(-4, 0),
            textcoords="offset points", rotation=90, fontsize=5.6,
            color="#888888", ha="right", va="center")
fig.text(0.5, 0.005, "Bar labels: share of the 100,294 campaigns the signal\nfires on (%); each panel has its own scale",
         ha="center", va="top", fontsize=6.6, color="#444444")
fig.legend(handles=[Patch(facecolor=IND, label="independent of campaign text"),
                    Patch(facecolor=TXT, label="textual / heuristic")],
           loc="upper center", ncol=2, frameon=False, fontsize=6.2,
           bbox_to_anchor=(0.55, 1.055), handletextpad=0.4, columnspacing=1.0)
save(fig, "fig_signalload")

# =============================================================== money
meta = {}
with open(CORPUS, newline="") as f:
    for r in csv.DictReader(f):
        meta[r["url"]] = r
def usd(m, field):
    v = fnum(m.get(field))
    return None if v is None else v * RATES.get((m.get("currency") or "").strip(), 1.0)

per = collections.defaultdict(float)
for r in fraud:
    if r["platform"] == "GoGetFunding": continue
    m = meta.get(r["url"])
    if not m: continue
    v = usd(m, "raised_amount")
    if v: per[r["platform"]] += v
top = sorted(per.items(), key=lambda kv: -kv[1])[:5]
other = sum(v for k, v in per.items() if k not in dict(top))
data = top + [("Other", other)]
data = list(reversed(data))
fig, ax = plt.subplots(figsize=(3.5, 2.1))
ax.barh(range(len(data)), [v/1e6 for _, v in data], color=BLUE, height=0.62, edgecolor="none")
for y, (_, v) in enumerate(data):
    ax.annotate(f"${v/1e6:.2f}M", (v/1e6, y), xytext=(4, 0), textcoords="offset points",
                va="center", fontsize=6.8)
ax.set_yticks(range(len(data))); ax.set_yticklabels([k for k, _ in data], fontsize=7.5)
ax.set_xlim(0, 1.62); ax.set_xlabel("Reported raised (USD-equivalent, $M)", fontsize=8)
ax.grid(axis="x", ls=":", alpha=0.35); ax.set_axisbelow(True)
save(fig, "fig_money")

# =============================================================== behavior
credible = set()
goals_by_plat = collections.defaultdict(list)
for r in rows:
    m = meta.get(r["url"])
    if not m: continue
    g = usd(m, "goal_amount")
    if g and g > 0: goals_by_plat[r["platform"]].append(g)
credible = {k for k, v in goals_by_plat.items() if len(v) >= 20 and statistics.median(v) >= 100}
ROUND = {1000, 2000, 5000, 10000, 15000, 20000, 25000, 50000, 100000}
def behavior(sub):
    goals, ratios, reached, rounds, n = [], [], 0, 0, 0
    for r in sub:
        m = meta.get(r["url"])
        if not m or r["platform"] not in credible: continue
        g = usd(m, "goal_amount"); ra = usd(m, "raised_amount")
        if not g or g <= 0: continue
        n += 1; goals.append(g)
        if fnum(m.get("goal_amount")) in ROUND: rounds += 1
        if ra is not None:
            ratios.append(min(ra/g, 10.0))
            if ra >= g: reached += 1
    return dict(reach=100*reached/len(ratios), ratio=100*statistics.median(ratios),
                rnd=100*rounds/n, goal=statistics.median(goals))
unk = [r for r in rows if I(r, "score_hard") == 0]
bf, bu = behavior(fraud), behavior(unk)

# Mixed-form figure (advisor request): CDF of goal amounts on the left,
# completion-rate bar pair on the right; scam-flagged curve is the emphasis.
import numpy as np
def goal_list(sub):
    out = []
    for r in sub:
        m = meta.get(r["url"])
        if not m or r["platform"] not in credible: continue
        g = usd(m, "goal_amount")
        if g and g > 0: out.append(g)
    return sorted(out)
gf, gu = goal_list(fraud), goal_list(unk)
fig, (axc, axb) = plt.subplots(1, 2, figsize=(3.5, 1.62),
                               gridspec_kw={"width_ratios": [1.9, 1.0], "wspace": 0.46})
import matplotlib.patheffects as _pe
for data, col, lw, z in ((gu, GREY, 1.2, 2), (gf, VERM, 1.8, 3)):
    xs = np.array(data); ys = np.arange(1, len(xs) + 1) / len(xs)
    axc.plot(xs, ys, color=col, lw=lw, zorder=z)
axc.set_xscale("log")
axc.set_xlim(50, 2e6); axc.set_ylim(0, 1.0)
axc.axvline(bf["goal"], color=VERM, ls=":", lw=0.7, alpha=0.75)
axc.axvline(bu["goal"], color=GREY, ls=":", lw=0.7, alpha=0.75)
# curves are labeled directly (no legend box: any box this wide overlaps a
# curve at this panel size); sample counts live in the caption
axc.annotate("scam-\nflagged", (120, 0.30), fontsize=6.2, color=VERM,
             weight="bold", ha="left", va="center", zorder=4,
             path_effects=[_pe.withStroke(linewidth=1.8, foreground="white")])
axc.annotate("unflagged", (1.6e4, 0.42), fontsize=6.2, color="#666666",
             weight="bold", ha="left", va="center", zorder=4,
             path_effects=[_pe.withStroke(linewidth=1.8, foreground="white")])
axc.set_xlabel("Goal amount (USD-equivalent)", fontsize=7.2)
axc.set_ylabel("CDF", fontsize=7.2)
axc.tick_params(labelsize=6.3)
axc.grid(ls=":", alpha=0.3); axc.set_axisbelow(True)
vals = [bf["reach"], bu["reach"]]
axb.bar([0, 1], vals, color=[VERM, GREY], width=0.62, edgecolor="none")
for x, v in zip((0, 1), vals):
    axb.annotate(f"{v:.1f}%", (x, v), xytext=(0, 3), textcoords="offset points",
                 ha="center", fontsize=6.8)
axb.set_xticks([0, 1]); axb.set_xticklabels(["scam-\nflagged", "unflagged"], fontsize=6.5)
axb.set_ylabel("Reached goal (%)", fontsize=7.2)
axb.set_ylim(0, 46); axb.tick_params(labelsize=6.3)
axb.grid(axis="y", ls=":", alpha=0.3); axb.set_axisbelow(True)
save(fig, "fig_behavior")

# =============================================================== corroboration data
# Per-platform corroborated / text-only split of the Fraud tier. No standalone
# figure anymore (advisor: too many uniform bar charts); the split is drawn
# directly into the platform-rate figure below as green/orange bar segments.
corr_plat = collections.defaultdict(lambda: [0, 0])   # corroborated, text-only
for r in fraud:
    p = corr_plat[r["platform"]]
    if I(r, "corroborated"): p[0] += 1
    else: p[1] += 1
print("corroboration data:", sorted(corr_plat.items(), key=lambda kv: -sum(kv[1])))

# =============================================================== agreement (UpSet)
# Intersection counts computed live; drawn as an UpSet-style plot (intersection
# bars over a detector-membership matrix) instead of a Venn: denser, exact, and
# it also carries the per-detector corpus totals in the row labels.
import matplotlib.patheffects as pe
AB  = sum(1 for r in rows if I(r,"A_hard") and I(r,"B_hard"))
AC  = sum(1 for r in rows if I(r,"A_hard") and I(r,"C_hard"))
BC  = sum(1 for r in rows if I(r,"B_hard") and I(r,"C_hard"))
ABC = sum(1 for r in rows if I(r,"A_hard") and I(r,"B_hard") and I(r,"C_hard"))
At  = sum(I(r,"A_hard") for r in rows); Bt = sum(I(r,"B_hard") for r in rows); Ct = sum(I(r,"C_hard") for r in rows)
combos = sorted([("AC", AC - ABC), ("ABC", ABC), ("AB", AB - ABC), ("BC", BC - ABC)],
                key=lambda t: -t[1])
DETS = ["A", "B", "C"]
DET_LBL = {"A": f"A external ({At:,})", "B": f"B LLM ({Bt:,})", "C": f"C identity ({Ct:,})"}
fig, (axb, axm) = plt.subplots(2, 1, figsize=(2.9, 1.88), sharex=True,
                               gridspec_kw={"height_ratios": [2.2, 1.0], "hspace": 0.06})
xs = list(range(len(combos)))
axb.bar(xs, [v for _, v in combos], width=0.52, color="#4a4a4a", edgecolor="none")
for x, (_, v) in zip(xs, combos):
    axb.annotate(f"{v}", (x, v), xytext=(0, 2.5), textcoords="offset points",
                 ha="center", fontsize=7.2, weight="bold")
axb.set_ylabel("Fraud-tier\ncampaigns", fontsize=6.8)
axb.set_ylim(0, 345); axb.set_xlim(-0.6, len(combos) - 0.4)
axb.tick_params(labelsize=6.2, bottom=False)
axb.grid(axis="y", ls=":", alpha=0.3); axb.set_axisbelow(True)
for x, (name, _) in zip(xs, combos):
    on = [i for i, d in enumerate(DETS) if d in name]
    axm.plot([x, x], [min(on), max(on)], color="#4a4a4a", lw=1.3, zorder=2)
    for i, d in enumerate(DETS):
        axm.scatter(x, i, s=32 if d in name else 15,
                    color="#4a4a4a" if d in name else "#d9d9d9", zorder=3)
axm.set_yticks(range(3)); axm.set_yticklabels([DET_LBL[d] for d in DETS], fontsize=6.4)
axm.set_ylim(2.55, -0.55)
axm.set_xticks(xs); axm.set_xticklabels([])
axm.tick_params(bottom=False, left=False)
for sp in ("top", "right"): axb.spines[sp].set_visible(False)
for sp in axm.spines.values(): sp.set_visible(False)
save(fig, "fig_agreement")

# =============================================================== community network
# Real Detector-C organizer communities (advisor request): the largest
# scam-contributing identity clusters from the shipped artifacts, with shared
# payment handles / emails / phones / domains drawn as hub nodes. Only the two
# handles already published in the paper are labeled; every other identity
# value stays anonymous.
import json as _json, re as _re
import networkx as _nx
D_FLAGS_P  = ROOT / "03_detection" / "detectors" / "outputs" / "detector_D_flags.csv"
CONTACTS_P = ROOT / "03_detection" / "intel" / "campaign_contacts_llm.jsonl"
clusters = collections.defaultdict(list)
with open(D_FLAGS_P, newline="") as f:
    for r in csv.DictReader(f):
        if str(r.get("flag")).strip().lower() in ("1", "true"):
            clusters[r["cluster_id"]].append(r["url"])
contacts = {}
with open(CONTACTS_P) as f:
    for line in f:
        try: d = _json.loads(line)
        except _json.JSONDecodeError: continue
        if d.get("url"): contacts[d["url"]] = d
tier    = {r["url"]: I(r, "score_hard") for r in rows}
plat_of = {r["url"]: r["platform"] for r in rows}
PLAT_COL = {"GoGetFunding": BLUE, "Betterplace": GREEN, "FreeFunder": ORANGE,
            "Spotfund": SKY, "GoFundMe": VERM, "LaunchGood": "#CC79A7",
            "Chuffed": "#DDCC77", "Crowdfundr": "#999933", "SeedAndSpark": "#882255"}
SKIP_DOM = ("gofundme", "gofund.me", "gogetfunding", "spotfund", "freefunder",
            "betterplace", "launchgood", "chuffed", "crowdfundr", "seedandspark",
            "whydonate", "angelink", "ufandao", "donorschoose", "experiment",
            "happypot", "facebook", "instagram", "twitter", "youtube", "tiktok",
            "paypal.com", "paypal.me", "venmo.com", "cash.app", "cashapp.com",
            "zellepay.com", "gmail", "google", "bit.ly", "linktr.ee", "wa.me", "t.me")
def _dom(u):
    s = (u or "").strip().lower()
    s = _re.sub(r"^https?://", "", s).split("/")[0].split("?")[0].split(":")[0]
    return s[4:] if s.startswith("www.") else s
def name_ts(u):
    m = meta.get(u) or {}
    return tuple(sorted(t for t in _re.split(r"\W+", (m.get("organizer") or "").lower()) if len(t) >= 2))

# Global identity buckets, mirroring the shipped share cap (<=10 campaigns per
# value) and the global domain-coherence rule (a domain cited by more than two
# distinct organizer name-sets anywhere in the corpus is a citation, not an
# identity). Computing these globally is what keeps citation-ring artifacts
# such as the 67-member news cluster out of the drawing.
g_bucket = collections.defaultdict(set)   # (kind, value) -> corpus urls
def _valid_email(e):
    e = (e or "").strip().lower()
    return e if ("@" in e and "." in e.split("@")[-1]) else None
for u, c in contacts.items():
    for h in (c.get("payment_handles") or []): g_bucket[("handle", h.strip().lower())].add(u)
    for e in (c.get("emails") or []):
        ev = _valid_email(e)
        if ev: g_bucket[("email", ev)].add(u)
    for p in (c.get("phones") or []):
        pn = _re.sub(r"\D", "", p or "")
        if len(pn) >= 7: g_bucket[("phone", pn)].add(u)
    for uu in (c.get("urls") or []):
        d = _dom(uu)
        if d and "." in d and not any(s in d for s in SKIP_DOM): g_bucket[("dom", d)].add(u)
def bucket_ok(kind, val):
    us = g_bucket[(kind, val)]
    if len(us) > 10: return False
    if kind == "dom" and len({name_ts(u) for u in us}) > 2: return False
    return True

c_hard = {r["url"]: I(r, "C_hard") for r in rows}

def build_community(cid):
    """Gated identity graph for one shipped cluster, reduced to the connected
    components that contain at least one Fraud-tier campaign with a C vote."""
    mem = clusters[cid]
    G = _nx.Graph()
    for u in mem: G.add_node(u, kind="camp")
    local = collections.defaultdict(set)
    for u in mem:
        c = contacts.get(u) or {}
        for h in (c.get("payment_handles") or []): local[("handle", h.strip().lower())].add(u)
        for e in (c.get("emails") or []):
            ev = _valid_email(e)
            if ev: local[("email", ev)].add(u)
        for p in (c.get("phones") or []):
            pn = _re.sub(r"\D", "", p or "")
            if len(pn) >= 7: local[("phone", pn)].add(u)
        for uu in (c.get("urls") or []):
            d = _dom(uu)
            if d and "." in d and not any(s in d for s in SKIP_DOM): local[("dom", d)].add(u)
    for (kind, val), us in local.items():
        if len(us) < 2 or not bucket_ok(kind, val): continue
        hn = f"{kind}:{val}"
        G.add_node(hn, kind=kind, val=val)
        for u in us: G.add_edge(hn, u)
    byname = collections.defaultdict(list)
    for u in mem:
        toks = name_ts(u)
        if len(toks) >= 2: byname[toks].append(u)
    for us in byname.values():
        for i in range(len(us)):
            for j in range(i + 1, len(us)):
                if not G.has_edge(us[i], us[j]): G.add_edge(us[i], us[j], name=True)
    keep = set()
    for comp in _nx.connected_components(G):
        camps = [n for n in comp if G.nodes[n]["kind"] == "camp"]
        if len(camps) >= 2 and any(tier.get(u, 0) >= 2 and c_hard.get(u) for u in camps):
            keep |= comp
    G = G.subgraph(keep).copy()
    ncamp = sum(1 for _, d in G.nodes(data=True) if d["kind"] == "camp")
    return G if ncamp >= 3 else None

cand = []
for cid, mem in clusters.items():
    nf = sum(1 for u in mem if tier.get(u, 0) >= 2 and c_hard.get(u))
    if nf: cand.append((nf, len(mem), cid))
cand.sort(reverse=True)
graphs = []
for _, _, cid in cand:
    if len(graphs) == 12: break
    g = build_community(cid)
    if g is not None: graphs.append((cid, g))
if not any(cid == "2330" for cid, _ in graphs):     # keep the cluster the prose describes
    g = build_community("2330")
    if g is not None: graphs[-1] = ("2330", g)
# Detail panel (the case-study community, real edges, labeled campaigns) plus
# six exemplar communities classified by the identity signal that binds them.
LABELED = {"paypal.me/msmiry": "msmiry", "paypal.me/xohusnaaxo": "xohusnaaxo"}
CASE_LBL = {"Adha-Gaza": "Eid appeal", "CFG3": "Aid appeal", "CareForGazaold": "Legacy page",
            "Ramadan-20211": "Ramadan 2021", "careforgaza-ramadan": "Ramadan appeal",
            "gaza-greenhouse": "Greenhouse", "help-gaza-in-covid19": "COVID relief"}

def draw_graph(ax, G, node_color, node_size=26, hub_size=20):
    pos = _nx.spring_layout(G, seed=7, k=0.9 / max(len(G) ** 0.5, 1))
    for a, b, dat in G.edges(data=True):
        ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]],
                color="#bdbdbd", lw=0.55, ls=":" if dat.get("name") else "-", zorder=1)
    for n, dat in G.nodes(data=True):
        x, y = pos[n]
        if dat["kind"] == "camp":
            col = node_color(n)
            ax.scatter(x, y, s=node_size, color=col if tier.get(n, 0) >= 2 else "white",
                       edgecolors=col, linewidths=0.9, zorder=3)
        elif dat["kind"] == "handle":
            ax.scatter(x, y, s=hub_size, marker="s", color="#222222", zorder=4)
            if dat["val"] in LABELED:
                ax.annotate(LABELED[dat["val"]], (x, y), xytext=(0, -7.5),
                            textcoords="offset points", ha="center",
                            fontsize=4.8, color="#222222", zorder=6,
                            path_effects=[pe.withStroke(linewidth=1.6, foreground="white")])
        else:
            ax.scatter(x, y, s=hub_size * 0.65, marker="D", color="#8a8a8a", zorder=4)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.margins(0.17)
    return pos

def classify(G):
    kinds = {d["kind"] for _, d in G.nodes(data=True)}
    camps = [n for n, d in G.nodes(data=True) if d["kind"] == "camp"]
    nplat = len({plat_of.get(u) for u in camps})
    if nplat > 1: return f"cross-platform ({nplat} platforms)", 0
    if "handle" in kinds: return "payment-handle hub", 1
    if "phone" in kinds: return "shared phone", 2
    if "email" in kinds: return "shared email", 2
    return "shared domain", 3

detail = next((G for cid, G in graphs if cid == "2330"), None) or build_community("2330")
others = [(cid, G) for cid, G in graphs if cid != "2330"]
others.sort(key=lambda t: (classify(t[1])[1],
                           -sum(1 for _, d in t[1].nodes(data=True) if d["kind"] == "camp")))
cells = others[:6]

fig = plt.figure(figsize=(7.16, 2.20))
gs = fig.add_gridspec(2, 4, width_ratios=[1.55, 1, 1, 1], hspace=0.42, wspace=0.18,
                      left=0.01, right=0.99, top=0.90, bottom=0.14)
axd = fig.add_subplot(gs[:, 0])

# ---- detail: the case-study community, personas as colors, campaigns labeled.
# Real edges and tiers from the artifacts; node positions pinned to a curated
# layout because a spring layout piles the densely name-linked persona-1
# campaigns on top of each other.
dmem = [n for n, d in detail.nodes(data=True) if d["kind"] == "camp"]
groups = collections.defaultdict(list)
for u in dmem: groups[name_ts(u)].append(u)
ordered = sorted(groups.values(), key=len, reverse=True)
persona_of = {}
for gi, us in enumerate(ordered):
    for u in us: persona_of[u] = gi
PCOLS = [SKY, GREY, "#bbbbbb"]
CASE_POS = {"Adha-Gaza": (-1.55, 0.95), "CFG3": (-1.95, 0.00),
            "help-gaza-in-covid19": (-1.55, -0.95), "Ramadan-20211": (0.00, 1.15),
            "careforgaza-ramadan": (1.55, 0.95), "gaza-greenhouse": (1.95, 0.00),
            "CareForGazaold": (1.55, -0.95)}
HUB_POS = {"paypal.me/msmiry": (-0.55, 0.10), "paypal.me/xohusnaaxo": (0.55, 0.10)}
dpos, spare = {}, [(0.0, -1.25), (0.75, -1.25), (-0.75, -1.25)]
for n, dat in detail.nodes(data=True):
    if dat["kind"] == "camp":
        dpos[n] = CASE_POS.get(n.rstrip("/").rsplit("/", 1)[-1]) or spare.pop(0)
    else:
        dpos[n] = HUB_POS.get(dat.get("val")) or spare.pop(0)
for a, b, dat in detail.edges(data=True):
    axd.plot([dpos[a][0], dpos[b][0]], [dpos[a][1], dpos[b][1]],
             color="#bdbdbd", lw=0.65, ls=":" if dat.get("name") else "-", zorder=1)
for n, dat in detail.nodes(data=True):
    x, y = dpos[n]
    if dat["kind"] == "camp":
        col = PCOLS[min(persona_of.get(n, 2), 2)]
        axd.scatter(x, y, s=115, color=col if tier.get(n, 0) >= 2 else "white",
                    edgecolors=col, linewidths=1.2, zorder=3)
        lbl = CASE_LBL.get(n.rstrip("/").rsplit("/", 1)[-1])
        if lbl:
            axd.annotate(lbl, (x, y + (0.30 if y >= 0 else -0.34)), ha="center",
                         va="bottom" if y >= 0 else "top", fontsize=5.4,
                         color="#222222", zorder=6,
                         path_effects=[pe.withStroke(linewidth=1.8, foreground="white")])
    elif dat["kind"] == "handle":
        axd.scatter(x, y, s=80, marker="s", color="#222222", zorder=4)
        if dat["val"] in LABELED:
            axd.annotate(LABELED[dat["val"]], (x, y - 0.26), ha="center", va="top",
                         fontsize=5.2, color="#222222", zorder=6,
                         path_effects=[pe.withStroke(linewidth=1.8, foreground="white")])
    else:
        axd.scatter(x, y, s=42, marker="D", color="#8a8a8a", zorder=4)
axd.set_xlim(-2.65, 2.65); axd.set_ylim(-1.85, 1.72)
axd.set_xticks([]); axd.set_yticks([])
for sp in axd.spines.values(): sp.set_visible(False)
axd.set_title(f"payment-handle community, GoGetFunding (n={len(dmem)})",
              fontsize=5.8, color="#333333", pad=2.5)
axd.legend(handles=[
    Line2D([0], [0], marker="o", color="none", markerfacecolor=SKY, markeredgecolor=SKY,
           markersize=5, label=f"persona 1 ({len(ordered[0])} campaigns)"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=GREY, markeredgecolor=GREY,
           markersize=5, label=f"persona 2 ({len(ordered[1]) if len(ordered) > 1 else 0} campaigns)")],
    loc="upper left", bbox_to_anchor=(-0.04, 0.055), frameon=False, fontsize=5.0,
    handletextpad=0.2, labelspacing=0.25, borderaxespad=0.0)

# ---- six classified exemplars
for (cid, G), axc in zip(cells, (fig.add_subplot(gs[r, c]) for r in (0, 1) for c in (1, 2, 3))):
    camps = [n for n, d in G.nodes(data=True) if d["kind"] == "camp"]
    draw_graph(axc, G, lambda n: PLAT_COL.get(plat_of.get(n, ""), "#777777"))
    axc.set_title(f"{classify(G)[0]}, n={len(camps)}", fontsize=5.2, color="#444444", pad=2.0)

present = {plat_of.get(n) for _, G in cells for n, d in G.nodes(data=True) if d["kind"] == "camp"}
legend_items = [Line2D([0], [0], marker="o", color="none", markerfacecolor=c,
                       markeredgecolor=c, markersize=5, label=p)
                for p, c in PLAT_COL.items() if p in present]
legend_items += [
    Line2D([0], [0], marker="o", color="none", markerfacecolor="white",
           markeredgecolor="#555555", markersize=5, label="below Fraud tier"),
    Line2D([0], [0], marker="s", color="none", markerfacecolor="#222222",
           markersize=4.5, label="payment handle"),
    Line2D([0], [0], marker="D", color="none", markerfacecolor="#8a8a8a",
           markersize=4, label="email / phone / domain"),
    Line2D([0], [0], color="#bdbdbd", ls=":", lw=1.0, label="shared organizer name"),
]
fig.legend(handles=legend_items, loc="lower center", ncol=min(len(legend_items), 8),
           frameon=False, fontsize=5.4, bbox_to_anchor=(0.56, -0.035),
           handletextpad=0.25, columnspacing=0.7)
save(fig, "fig_community_network")

# =============================================================== platform rates (bar)
# Each platform's Fraud-tier rate, with the bar split by evidence stratum
# (green = independently corroborated labels, orange = text-only labels),
# reusing the paper-wide evidence palette and absorbing the retired
# standalone corroboration chart.
pr = collections.defaultdict(lambda: [0, 0])
for r in rows:
    b = pr[r["platform"]]; b[0] += 1
    if I(r, "score_hard") >= 2: b[1] += 1
sel2 = sorted(((k, n, f) for k, (n, f) in pr.items() if f), key=lambda t: 100 * t[2] / t[1])
fig, ax = plt.subplots(figsize=(3.5, 1.65))
for y, (k, n, f) in enumerate(sel2):
    rate = 100 * f / n
    c_, t_ = corr_plat.get(k, [f, 0])
    r_corr = rate * c_ / f
    ax.barh(y, r_corr, color=IND, height=0.62, edgecolor="none")
    if t_:
        ax.barh(y, rate - r_corr, left=r_corr, color=TXT, height=0.62, edgecolor="none")
    ax.annotate(f"{rate:.2f}%  ({f}/{n:,})", (rate, y), xytext=(4, 0),
                textcoords="offset points", va="center", fontsize=6.4, color="#333333")
ax.set_yticks(range(len(sel2))); ax.set_yticklabels([k for k, _, _ in sel2], fontsize=7.3)
ax.set_xlim(0, 5.7)
ax.set_xlabel("Share of platform's campaigns in the Fraud tier (%)", fontsize=7.4)
ax.grid(axis="x", ls=":", alpha=0.35); ax.set_axisbelow(True)
ax.legend(handles=[Patch(facecolor=IND, label="independently corroborated"),
                   Patch(facecolor=TXT, label="text-only labels")],
          loc="lower right", frameon=False, fontsize=6.2, handlelength=1.2,
          bbox_to_anchor=(1.0, 0.02))
save(fig, "fig_platform_rates")

# =============================================================== persistence strip
import re as _re
yr = collections.defaultdict(list)
fr_plat = {r["url"]: r["platform"] for r in fraud_idx.items()} if False else None
fraud_plat = {r["url"]: r["platform"] for r in fraud}
for m in meta.values():
    p = fraud_plat.get(m["url"])
    if p not in ("Betterplace", "Spotfund"): continue
    mt = _re.match(r"(\d{4})", (m.get("created_date") or "").strip())
    if mt and 2000 < int(mt.group(1)) <= 2026:
        yr[p].append(int(mt.group(1)))
fig, ax = plt.subplots(figsize=(3.5, 1.30))
# Spotfund's created_date field records capture time (all 2026-04), not
# creation, so only Betterplace carries reliable dates; one row.
cnt = collections.Counter(yr["Betterplace"])
for year, n in sorted(cnt.items()):
    for k in range(n):
        ax.scatter(year + (k % 4) * 0.16 - 0.24, 0.13 * (k // 4),
                   s=11, color=BLUE, edgecolors="none", alpha=0.85)
ax.set_yticks([0.25]); ax.set_yticklabels(["Betterplace"], fontsize=7.5)
ax.set_xlim(2009.3, 2026.2); ax.set_ylim(-0.18, 0.72)
ax.set_xticks(range(2010, 2026, 3)); ax.tick_params(axis="x", labelsize=7)
ax.set_xlabel("Creation year (one dot per Fraud-flagged campaign)", fontsize=7.5)
ax.grid(axis="x", ls=":", alpha=0.3); ax.set_axisbelow(True)
save(fig, "fig_persistence")
