#!/usr/bin/env python3
"""
Candidate replacement/expansion figures for the CCS paper (non-bar-chart types).
All data is read from the reproducible corroboration-stratification output.

Produces three colored figures, each as PDF (for the paper) and PNG (for review):
  1. fig_corroboration_dumbbell  - per-platform raw Fraud vs corroborated Fraud (dumbbell)
  2. fig_platform_bubble         - platform size vs fraud rate, sized by fraud count,
                                   colored by text-only share (scatter/bubble)
  3. fig_detector_overlap_venn   - A/B/C detector co-firing (schematic Venn; B nests in A)
"""
import csv, collections, math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from matplotlib import cm
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
STRAT = Path(__file__).resolve().parent / "consensus_corroboration_stratified.csv"
PDF_DIR = ROOT / "paper" / "images"                       # vector output for the paper
PNG_DIR = Path("/tmp/claude-312825/-home-C00621463-DonationScam---CCS-Remote/0feea2ce-e6d0-4599-972c-9e7d76e05234/scratchpad")
PNG_DIR.mkdir(parents=True, exist_ok=True)

# Okabe-Ito colorblind-safe palette
ORANGE, SKY, GREEN, YELLOW, BLUE, VERM, PURPLE, GREY = (
    "#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999")
plt.rcParams.update({
    "font.family": "serif", "font.size": 10, "axes.spines.top": False,
    "axes.spines.right": False, "figure.dpi": 150, "savefig.bbox": "tight",
})

def save(fig, name):
    fig.savefig(PDF_DIR / f"{name}.pdf")
    fig.savefig(PNG_DIR / f"{name}.png", dpi=150)
    plt.close(fig)
    print(f"[written] paper/images/{name}.pdf  +  {name}.png")

# ---- load per-platform aggregates --------------------------------------------
plat = collections.defaultdict(lambda: {"n":0,"fraud":0,"hard":0,"textonly":0})
reg = collections.Counter()
with open(STRAT) as f:
    for r in csv.DictReader(f):
        p = plat[r["platform"]]; p["n"] += 1
        A,B,C = r["A"]=="1", r["B"]=="1", r["C"]=="1"
        key = ("A" if A else "")+("B" if B else "")+("C" if C else "")
        if key: reg[key] += 1
        if int(r["score"]) >= 2:
            p["fraud"] += 1
            if r["hard_corroboration"]=="1": p["hard"] += 1
            else: p["textonly"] += 1
rows = [(k,v) for k,v in plat.items() if v["fraud"] > 0]

# ============================================================ FIGURE 1: DUMBBELL
rows_d = sorted(rows, key=lambda kv: kv[1]["fraud"])
fig, ax = plt.subplots(figsize=(6.4, 4.2))
ys = range(len(rows_d))
for y,(name,v) in zip(ys, rows_d):
    raw, hard = v["fraud"], v["hard"]
    ax.plot([max(hard,0.7), raw], [y, y], color=GREY, lw=2.2, zorder=1, solid_capstyle="round")
    ax.scatter(raw, y, s=60, color=VERM, zorder=3, label="all Fraud-tier" if y==0 else None)
    ax.scatter(max(hard,0.7), y, s=60, color=GREEN, zorder=3, label="corroborated" if y==0 else None)
    pct = 100*(raw-hard)/raw
    ax.annotate(f"-{pct:.0f}%", (raw, y), xytext=(6,0), textcoords="offset points",
                va="center", fontsize=7.5, color=GREY)
ax.set_yticks(list(ys)); ax.set_yticklabels([n for n,_ in rows_d], fontsize=8.5)
ax.set_xscale("log"); ax.set_xlim(0.7, 3500)
ax.set_xlabel("Fraud-tier campaigns (log scale)")
ax.set_title("Most Fraud-tier labels lose their footing without corroboration",
             fontsize=10.5, loc="left", weight="bold")
ax.legend(loc="lower right", frameon=False, fontsize=8.5)
ax.grid(axis="x", ls=":", alpha=0.4)
save(fig, "fig_corroboration_dumbbell")

# ============================================================ FIGURE 2: BUBBLE
fig, ax = plt.subplots(figsize=(6.4, 4.4))
xs = [v["n"] for _,v in rows]
ysr = [100*v["fraud"]/v["n"] for _,v in rows]
sizes = [max(30, 26*math.sqrt(v["fraud"])) for _,v in rows]
tocol = [100*v["textonly"]/v["fraud"] for _,v in rows]
cmap = matplotlib.colormaps["RdYlGn_r"]
sc = ax.scatter(xs, ysr, s=sizes, c=tocol, cmap=cmap, vmin=0, vmax=100,
                edgecolors="black", linewidths=0.6, alpha=0.9, zorder=3)
# manual label offsets (points) for crowded platforms
LBL = {"Ufandao":(6,9), "SeedAndSpark":(6,-13), "GoFundMe":(7,9), "Spotfund":(7,-13),
       "GoGetFunding":(-12,20), "Betterplace":(8,6), "FreeFunder":(8,6)}
for (name,v),x,y in zip(rows, xs, ysr):
    dx,dy = LBL.get(name, (6,6))
    ax.annotate(name, (x,y), xytext=(dx,dy), textcoords="offset points", fontsize=7.5)
# verified-intake platforms (zero fraud) as reference band
ax.axhline(0, color=GREEN, lw=1.2, ls="--", alpha=0.7)
ax.annotate("verified-intake platforms: 0 fraud",
            (min(xs), 0), xytext=(0,5), textcoords="offset points",
            fontsize=7.5, color=GREEN, weight="bold")
ax.set_xscale("log")
ax.set_xlabel("Platform size (filtered campaigns, log scale)")
ax.set_ylabel("Fraud-tier rate (%)")
ax.set_title("Openness drives fraud rate; corroboration quality varies by platform",
             fontsize=10.5, loc="left", weight="bold")
cb = fig.colorbar(sc, ax=ax, pad=0.02)
cb.set_label("share of platform's Fraud that is text-only (%)", fontsize=8.5)
ax.grid(True, ls=":", alpha=0.35)
save(fig, "fig_platform_bubble")

# ============================================================ FIGURE 3: VENN (A/B/C)
A_only,B_only,C_only = reg["A"],reg["B"],reg["C"]
AB,AC,BC,ABC = reg["AB"],reg["AC"],reg["BC"],reg["ABC"]
A_tot=A_only+AB+AC+ABC; B_tot=B_only+AB+BC+ABC; C_tot=C_only+AC+BC+ABC
fig, ax = plt.subplots(figsize=(7.0, 5.2))
ax.set_aspect("equal"); ax.axis("off")
ax.set_xlim(-3.0, 3.4); ax.set_ylim(-2.3, 2.1)
# circles: radius ~ sqrt(total); B small, mostly inside A, grazing the A-C lens
def rad(n): return 0.0132*math.sqrt(n)
rA,rB,rC = rad(A_tot), rad(B_tot), rad(C_tot)
cA=(-0.85, 0.10); cC=(1.05, 0.10); cB=(-0.30, -0.45)
for c,r,col in [(cA,rA,SKY),(cC,rC,ORANGE),(cB,rB,PURPLE)]:
    ax.add_patch(Circle(c, r, facecolor=col, alpha=0.40, edgecolor=col, lw=2.0, zorder=2))
# detector titles (outside the circles, no collisions)
ax.text(-2.0, 1.35, "Detector A", ha="center", fontsize=10, color=BLUE, weight="bold")
ax.text(-2.0, 1.12, "external + text heuristics", ha="center", fontsize=8, color=BLUE)
ax.text( 2.1, 1.35, "Detector C", ha="center", fontsize=10, color=VERM, weight="bold")
ax.text( 2.1, 1.12, "organizer identity", ha="center", fontsize=8, color=VERM)
ax.text(-1.15, -1.28, "Detector B", ha="center", fontsize=10, color=PURPLE, weight="bold")
ax.text(-1.15, -1.50, "behavioral LLM", ha="center", fontsize=8, color=PURPLE)
# big regions labeled in place
ax.text(-1.45, 0.75, f"A only\n{A_only:,}", ha="center", va="center", fontsize=10)
ax.text( 1.70, 0.10, f"C only\n{C_only:,}", ha="center", va="center", fontsize=10)
ax.text(-0.55, -0.45, f"A & B\n{AB:,}", ha="center", va="center", fontsize=9.5, weight="bold", color=VERM)
# small regions in a compact colored note (avoids center clutter)
note = (f"A & C: {AC:,}      all three: {ABC:,}\n"
        f"B & C: {BC:,}          B only: {B_only:,}")
ax.text(1.15, -1.55, note, ha="center", va="center", fontsize=8.5,
        bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=GREY, alpha=0.9))
# headline takeaway
ax.text(0.0, -2.15,
        f"Detector B fires {B_tot:,} times; {AB+ABC:,} ({100*(AB+ABC)/B_tot:.0f}%) also fire Detector A. "
        f"The A&B agreement behind the Fraud tier is largely one text axis, not two independent views.",
        ha="center", fontsize=8, style="italic")
ax.text(-2.95, 1.85, "circle sizes / overlaps schematic; counts exact", fontsize=6.5, color=GREY, style="italic")
ax.set_title("Detector B overlaps Detector A; only Detector C is a separate population",
             fontsize=10.5, loc="left", weight="bold", pad=14)
save(fig, "fig_detector_overlap_venn")
print("\nregion counts:", dict(reg))
print(f"2-set A/B: A-not-B={A_tot-AB-ABC:,}  A&B(all)={AB+ABC:,}  B-not-A={B_only+BC:,}  "
      f"=> B is {100*(AB+ABC)/B_tot:.0f}% inside A")
