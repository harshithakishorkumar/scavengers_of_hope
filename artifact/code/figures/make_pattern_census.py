"""Appendix figure: Detector A routing-pattern census (match counts per pattern).
Data recomputed from hard_rule_flags_v5.csv joined to the canonical consensus."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# (label, family, n)  -- 100,294-campaign corpus, canonical consensus 2026-07-24
DATA = [
    ("“DM me”",           "msg", 341),
    ("“contact directly”","msg", 271),
    ("PayPal.me URL",         "pay", 267),
    ("WhatsApp",              "msg", 234),
    ("external-link mention", "msg", 213),
    ("Western Union",         "wire", 121),
    ("IBAN value",            "wire",  93),
    ("Telegram",              "msg",   46),
    ("wire transfer",         "wire",  33),
    ("Venmo URL",             "pay",   32),
    ("MoneyGram",             "wire",  28),
    ("BTC address",           "cry",   27),
    ("Cash App URL",          "pay",   12),
    ("ETH address",           "cry",   11),
    ("Wise URL",              "pay",    4),
    ("Signal",                "msg",    1),
]
# paper palette: body-figure teal/ochre plus two muted companions
FAM = {"pay":  ("#3E8E75", "Payment platform"),
       "wire": ("#C87137", "Banking / wire"),
       "cry":  ("#8A6A9F", "Cryptocurrency"),
       "msg":  ("#5B7DB1", "Off-platform contact")}

labels = [d[0] for d in DATA]
ns     = [d[2] for d in DATA]
colors = [FAM[d[1]][0] for d in DATA]
y = range(len(DATA))[::-1]

fig, ax = plt.subplots(figsize=(3.5, 2.45))
ax.barh(list(y), ns, color=colors, height=0.72)
for yi, n in zip(y, ns):
    ax.text(n + 5, yi, f"{n:,}", va="center", fontsize=5.4, color="#4a4a4a")
ax.set_yticks(list(y)); ax.set_yticklabels(labels, fontsize=5.6)
ax.set_xlim(0, 390)
ax.set_xlabel("campaigns matching the pattern (of 100,294)", fontsize=6.2)
ax.tick_params(axis="x", labelsize=5.8)
handles = [plt.Rectangle((0, 0), 1, 1, color=FAM[k][0]) for k in ("pay", "wire", "cry", "msg")]
ax.legend(handles, [FAM[k][1] for k in ("pay", "wire", "cry", "msg")],
          fontsize=5.4, frameon=False, loc="lower right", handlelength=1.1,
          handleheight=0.95, labelspacing=0.3)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.tick_params(axis="y", length=0)

fig.tight_layout()
fig.savefig("fig_pattern_census.pdf", bbox_inches="tight")
print("saved fig_pattern_census.pdf")
