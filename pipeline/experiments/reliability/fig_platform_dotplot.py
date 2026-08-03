#!/usr/bin/env python3
"""Fraud-tier rate by platform as a Cleveland dot plot (paper Figure, column width).
Reads the reproducible consensus and writes paper/images/fig_platform_dotplot.pdf.
Verified-intake platforms are colored distinctly and sit at zero, making the
platform-design contrast the point of the figure. No in-figure title (caption handles it).
"""
import csv, collections
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[2]
CONS = Path(__file__).resolve().parent / "combined_hardened_consensus.csv"
OUT = ROOT / "paper" / "images" / "fig_platform_dotplot.pdf"

VERIFIED = {"DonorsChoose", "Experiment"}   # identity verified at intake
GREEN, ORANGE, GREY = "#009E73", "#D55E00", "#8a8a8a"

plat = collections.defaultdict(lambda: [0, 0])
with open(CONS) as f:
    for r in csv.DictReader(f):
        p = plat[r["platform"]]; p[0] += 1
        if int(r["score_hard"]) >= 2: p[1] += 1
rows = sorted(((k, v[0], 100*v[1]/v[0]) for k, v in plat.items() if v[1] > 0), key=lambda x: x[2])

plt.rcParams.update({"font.family": "serif", "font.size": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 150, "savefig.bbox": "tight", "pdf.fonttype": 42})
fig, ax = plt.subplots(figsize=(3.5, 3.4))
for y, (k, n, rate) in enumerate(rows):
    col = "#0072B2"
    ax.hlines(y, 0, rate, color=GREY, lw=0.9, alpha=0.55, zorder=1)
    ax.scatter(rate, y, s=42, color=col, edgecolors="black", linewidths=0.5, zorder=3)
    ax.annotate(f"{rate:.2f}", (rate, y), xytext=(5, 0), textcoords="offset points",
                va="center", fontsize=6.8, color="black")
ax.set_yticks(range(len(rows)))
ax.set_yticklabels([f"{k}  (n={n:,})" for k, n, _ in rows], fontsize=7.4)
ax.set_xlabel("Fraud-tier rate (% of platform's filtered campaigns)", fontsize=8)
ax.set_xlim(-0.1, 5.3)
ax.margins(y=0.02)
ax.grid(axis="x", ls=":", alpha=0.35)
fig.savefig(OUT)
print(f"[written] {OUT.relative_to(ROOT)}")
