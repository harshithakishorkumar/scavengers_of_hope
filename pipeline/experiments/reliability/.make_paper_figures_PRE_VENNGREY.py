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
IND, TXT = GREEN, VERM         # independent vs textual evidence
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
# One horizontal full-width figure covering ALL THREE detectors' signals.
# Values: paper_stats_hardened.txt + detector_D_flags.csv edge counts, 2026-07-02.
A_SIGNALS = [
    ("Redirection ($\\geq$2 phrases)", 3.35, TXT),
    ("WHOIS privacy + regex",          0.25, TXT),
    ("Hard-rule regex $\\geq$2",       0.20, TXT),
    ("IPQS phone",                     0.09, IND),
    ("VirusTotal $\\geq$2 eng.",       0.07, IND),
    ("Wallet reuse",                   0.04, IND),
]
B_SIGNALS = [
    ("Guilt language",     15.01), ("Multi-channel",      8.40),
    ("External payment",    6.40), ("Deadline pressure",  5.08),
    ("Verification ask",    3.36), ("Allocation",         3.21),
    ("Tragedy exploit.",    2.33), ("Defensive lang.",    1.78),
    ("Fake credential",     1.19), ("Impersonation",      0.79),
]
C_SIGNALS = [
    ("Domain",          1.18), ("Phone",           0.15),
    ("Email",           0.09), ("Payment account", 0.04),
]
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(7.16, 2.25),
                                 gridspec_kw={"width_ratios": [1, 1.06, 0.94], "wspace": 0.88})
def panel(ax, items, colors, xmax, title):
    # minimalist: labeled bars only; no axis, no grid, no tick marks
    items = list(reversed(items))
    cols = list(reversed(colors)) if isinstance(colors, list) else colors
    ax.barh(range(len(items)), [s[1] for s in items],
            color=cols, height=0.58, edgecolor="none")
    for y, s in enumerate(items):
        ax.annotate(f"{s[1]:.2f}" if s[1] >= 0.01 else "0", (s[1], y), xytext=(3.5, 0),
                    textcoords="offset points", va="center", fontsize=6.4, color="#333333")
    ax.set_yticks(range(len(items))); ax.set_yticklabels([s[0] for s in items], fontsize=6.6)
    ax.set_xlim(0, xmax); ax.set_ylim(-0.6, len(items) - 0.4)
    ax.set_title(title, fontsize=7.8, loc="left", weight="bold")
    ax.set_xticks([])
    for sp in ("top", "right", "bottom"): ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#bbbbbb")
    ax.tick_params(left=False)
panel(a1, A_SIGNALS, [s[2] for s in A_SIGNALS], 4.5, "Detector A: reputation + routing")
panel(a2, B_SIGNALS, TXT, 19.0, "Detector B: LLM questions")
panel(a3, C_SIGNALS, [IND, IND, IND, IND], 1.75, "Detector C: identity signals")
fig.text(0.5, -0.03, "Bar labels: share of the 100,294 campaigns the signal fires on (%)",
         ha="center", fontsize=7.2, color="#444444")
fig.legend(handles=[Patch(facecolor=IND, label="independent of campaign text"),
                    Patch(facecolor=TXT, label="textual / heuristic")],
           loc="upper center", ncol=2, frameon=False, fontsize=6.6,
           bbox_to_anchor=(0.5, 1.10))
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

# Minimal dumbbell chart: one row per metric, two dots (Fraud vs Unknown)
# on a row-local scale, values printed at the dots; no axes, no legend box.
rowsB = [
    ("Median goal",       bf["goal"],  bu["goal"],  lambda v: f"${v/1e3:.1f}k"),
    ("Reaches goal",      bf["reach"], bu["reach"], lambda v: f"{v:.1f}%"),
    ("Round-number goal", bf["rnd"],   bu["rnd"],   lambda v: f"{v:.1f}%"),
]
fig, ax = plt.subplots(figsize=(3.5, 1.55))
for y, (name, fv, uv, fmt) in enumerate(reversed(rowsB)):
    mx = 1.30 * max(fv, uv)
    xf, xu = fv/mx, uv/mx
    ax.plot([xf, xu], [y, y], color="#c9c9c9", lw=1.4, zorder=1)
    ax.scatter(xf, y, s=42, color=VERM, edgecolors="black", linewidths=0.4, zorder=3)
    ax.scatter(xu, y, s=42, color=SKY,  edgecolors="black", linewidths=0.4, zorder=3)
    # smaller value labeled to the left of its dot, larger to the right
    lo, hi = ((xf, fv), (xu, uv)) if xf <= xu else ((xu, uv), (xf, fv))
    ax.annotate(fmt(lo[1]), (lo[0], y), xytext=(-5, 0), textcoords="offset points",
                ha="right", va="center", fontsize=6.8, color="#333333")
    ax.annotate(fmt(hi[1]), (hi[0], y), xytext=(6, 0), textcoords="offset points",
                ha="left", va="center", fontsize=6.8, color="#333333")
ax.set_yticks(range(len(rowsB)))
ax.set_yticklabels([r[0] for r in reversed(rowsB)], fontsize=7.5)
# detached color-swatch legend so labels never mismatch a row's left/right sides
ax.legend(handles=[
    Line2D([0],[0], marker="o", color="none", markerfacecolor=VERM,
           markeredgecolor="black", markeredgewidth=0.4, markersize=7, label="Fraud-flagged"),
    Line2D([0],[0], marker="o", color="none", markerfacecolor=SKY,
           markeredgecolor="black", markeredgewidth=0.4, markersize=7, label="Unknown")],
    loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False,
    fontsize=7.2, handletextpad=0.3, columnspacing=1.6)
ax.set_xlim(-0.06, 1.06); ax.set_ylim(-0.5, len(rowsB) - 0.35)
ax.set_xticks([]); ax.tick_params(left=False)
for sp in ("top", "right", "bottom", "left"): ax.spines[sp].set_visible(False)
save(fig, "fig_behavior")

# =============================================================== corroboration (NEW)
plat = collections.defaultdict(lambda: [0, 0])   # corroborated, text-only
for r in fraud:
    p = plat[r["platform"]]
    if I(r, "corroborated"): p[0] += 1
    else: p[1] += 1
sel = [(k, c, t) for k, (c, t) in plat.items() if c + t >= 8]
sel.sort(key=lambda x: (x[1] + x[2]))
fig, ax = plt.subplots(figsize=(3.5, 2.5))
for y, (k, c, t) in enumerate(sel):
    n = c + t
    ax.barh(y, 100*c/n, color=IND, edgecolor="white", linewidth=1.0, height=0.62)
    ax.barh(y, 100*t/n, left=100*c/n, color=VERM, edgecolor="white", linewidth=1.0, height=0.62, alpha=0.92)
    ax.annotate(f"n={n:,}", (101, y), va="center", fontsize=6.8, color="black", annotation_clip=False)
    if 100*t/n >= 12:
        ax.annotate(f"{100*t/n:.0f}%", (100*c/n + 100*t/n/2, y), ha="center", va="center",
                    fontsize=6.6, color="white", weight="bold")
ax.set_yticks(range(len(sel))); ax.set_yticklabels([s[0] for s in sel], fontsize=7.5)
ax.set_xlim(0, 100); ax.set_xlabel("Share of platform's Fraud labels (%)", fontsize=8)
ax.legend(handles=[
    Line2D([0],[0], marker="s", color="w", markerfacecolor=IND, markeredgecolor="k", markersize=7, label="independently corroborated"),
    Line2D([0],[0], marker="s", color="w", markerfacecolor=VERM, markeredgecolor="k", markersize=7, label="text-only (share labeled)")],
    loc="lower left", bbox_to_anchor=(0, 1.01), ncol=1, frameon=False, fontsize=7.0, handletextpad=0.2)
save(fig, "fig_corroboration")
print("corroboration data:", [(k, c, t) for k, c, t in sel])

# =============================================================== venn (agreement)
# Region counts computed live; areas schematic, counts exact.
AB  = sum(1 for r in rows if I(r,"A_hard") and I(r,"B_hard"))
AC  = sum(1 for r in rows if I(r,"A_hard") and I(r,"C_hard"))
BC  = sum(1 for r in rows if I(r,"B_hard") and I(r,"C_hard"))
ABC = sum(1 for r in rows if I(r,"A_hard") and I(r,"B_hard") and I(r,"C_hard"))
At  = sum(I(r,"A_hard") for r in rows); Bt = sum(I(r,"B_hard") for r in rows); Ct = sum(I(r,"C_hard") for r in rows)
# Minimal Venn: white circles, shaded overlap lenses only (the Fraud tier IS
# the overlaps), labels tucked INSIDE the otherwise-empty exclusive regions so
# the bounding box stays tight. No title, no key: caption carries the message.
from matplotlib.patches import Circle
import matplotlib.patheffects as pe
HALO = [pe.withStroke(linewidth=2.6, foreground="white")]
FRAUD_TOTAL = AB + AC + BC - 2 * ABC   # = 939
fig, ax = plt.subplots(figsize=(3.2, 2.45))
ax.set_aspect("equal"); ax.axis("off")
cA, rA = (-0.72, 0.35), 1.05
cC, rC = ( 0.72, 0.35), 1.05
cB, rB = ( 0.00, -0.58), 0.74
# shaded overlap lenses (stack in the center so all-three reads darkest)
for chost, rhost, cclip, rclip in ((cA,rA,cB,rB), (cC,rC,cB,rB), (cA,rA,cC,rC)):
    lens = Circle(chost, rhost, facecolor=VERM, alpha=0.34, edgecolor="none", zorder=1)
    ax.add_patch(lens)
    lens.set_clip_path(Circle(cclip, rclip, transform=ax.transData))
for c, r in ((cA,rA), (cC,rC), (cB,rB)):
    ax.add_patch(Circle(c, r, facecolor="none", edgecolor="#3a3a3a", linewidth=1.1, zorder=3))
# labels INSIDE the empty exclusive regions
ax.text(-1.12, 0.78, "A", fontsize=10.5, weight="bold", ha="center", va="center")
ax.text(-1.12, 0.52, "external +\nrouting", fontsize=6.0, ha="center", va="top", color="#777777")
ax.text( 1.12, 0.78, "C", fontsize=10.5, weight="bold", ha="center", va="center")
ax.text( 1.12, 0.52, "organizer\nidentity", fontsize=6.0, ha="center", va="top", color="#777777")
ax.text( 0.00, -0.90, "B", fontsize=10.5, weight="bold", ha="center", va="center")
ax.text( 0.00, -1.04, "behavioral LLM", fontsize=6.0, ha="center", va="top", color="#777777")
# the four overlap counts (they sum to the 939 Fraud labels)
ax.text( 0.00,  0.62, f"{AC-ABC:,}", ha="center", va="center", fontsize=10,
        weight="bold", path_effects=HALO)
ax.text(-0.46, -0.26, f"{AB-ABC:,}", ha="center", va="center", fontsize=10,
        weight="bold", path_effects=HALO)
ax.text( 0.48, -0.26, f"{BC-ABC}", ha="center", va="center", fontsize=8.5,
        weight="bold", path_effects=HALO)
ax.text( 0.00, -0.07, f"{ABC}", ha="center", va="center", fontsize=8,
        weight="bold", color="white")
ax.set_xlim(-1.80, 1.80); ax.set_ylim(-1.36, 1.44)
save(fig, "fig_agreement_venn")

# =============================================================== case study cluster
# Cluster 2330 (detector_D_flags.csv) verified 2026-07-01: 7 GoGetFunding
# campaigns, personas P1 "Care For Gaza ..." (5) / P2 "Husnaa ..." (2),
# payout handles paypal.me/msmiry + paypal.me/xohusnaaxo; 6 of 7 Fraud-tier.
P1, P2 = SKY, ORANGE
H1 = (-0.55, 0.0); H2 = (0.55, 0.0)   # payment-account hubs
CAMPS = [  # (label, xy, persona, fraud?, hubs)
    ("Eid appeal",    (-1.55,  0.95), P1, True,  (H1, H2)),
    ("Aid appeal",    (-1.95,  0.00), P1, True,  (H1,)),
    ("COVID relief",  (-1.55, -0.95), P1, True,  (H1, H2)),
    ("Ramadan 2021",  ( 0.00,  1.15), P1, True,  (H1, H2)),
    ("Ramadan appeal",( 1.55,  0.95), P1, True,  (H1, H2)),
    ("Greenhouse",    ( 1.95,  0.00), P2, True,  (H1,)),
    ("Legacy page",   ( 1.55, -0.95), P2, False, ()),
]
fig, ax = plt.subplots(figsize=(3.5, 2.55))
ax.set_aspect("equal"); ax.axis("off")
for label, (x, y), col, fr, hubs in CAMPS:
    for hx, hy in hubs:
        ax.plot([x, hx], [y, hy], color=GREY, lw=0.9, alpha=0.6, zorder=1)
# same-organizer (name-edge) link for the P2 pair
ax.plot([1.95, 1.55], [0.0, -0.95], color=GREY, lw=0.9, ls=":", alpha=0.8, zorder=1)
for hx, hy, name in (( *H1, "msmiry"), ( *H2, "xohusnaaxo")):
    ax.scatter(hx, hy, s=230, marker="s", color="#333333", zorder=3)
    ax.annotate(name, (hx, hy - 0.30), ha="center", va="center", fontsize=5.8, color="#222222", zorder=4)
for label, (x, y), col, fr, hubs in CAMPS:
    ax.scatter(x, y, s=300, color=col, edgecolors="#222222",
               linewidths=1.1 if fr else 0.9, linestyle="-" if fr else "--", zorder=3)
    off = 0.34 if y >= 0 else -0.40
    ax.annotate(label, (x, y + off), ha="center", fontsize=6.4, zorder=4)
ax.legend(handles=[
    Line2D([0],[0], marker="o", color="w", markerfacecolor=P1, markeredgecolor="k", markersize=7, label="persona 1 (5 campaigns)"),
    Line2D([0],[0], marker="o", color="w", markerfacecolor=P2, markeredgecolor="k", markersize=7, label="persona 2 (2 campaigns)"),
    Line2D([0],[0], marker="s", color="w", markerfacecolor="#333333", markersize=7, label="payment account"),
    Line2D([0],[0], color=GREY, ls=":", lw=1.2, label="shared organizer name")],
    loc="lower left", bbox_to_anchor=(-0.02, -0.16), ncol=2, frameon=False,
    fontsize=6.0, handletextpad=0.3, columnspacing=0.9)
ax.set_xlim(-2.6, 2.6); ax.set_ylim(-1.75, 1.65)
save(fig, "fig_case_cluster")

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
