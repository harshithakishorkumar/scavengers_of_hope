"""Build a 200-sample stratified pool for manual validation, plus a 3-overlap
assignment matrix across 6 annotators.

Sampling design:
    80 from aux_consensus="fraud"        (oversampled; fraud class is 2.91% of corpus)
    60 from aux_consensus="suspicious"
    60 from aux_consensus="unknown" / "legitimate"
    -> 200 items total. Within each tier, sampled with platform proportional to
       the platform's representation in that tier.

Assignment design:
    All C(6,3) = 20 unique three-of-six annotator triples are enumerated. The
    200 items are split equally across the 20 triples (10 items per triple). For
    each item, the three triple-members are the annotators assigned to that
    item. Each annotator participates in C(5,2) = 10 triples, hence labels
    10 * 10 = 100 items. Pairwise overlap between any two annotators is the
    same (they share C(4,1) = 4 triples * 10 items = 40 items). Balanced design
    -> Cohen's kappa is well-defined for every annotator pair.

Outputs:
    samples.json       - 200 records (id, url, platform, title, description,
                         goal, raised, organizer, aux_label) ready for the
                         frontend.
    assignment.json    - {item_id: [annotator_a, annotator_b, annotator_c], ...}
    assignment.csv     - same, in flat form for the paper appendix.
    summary.txt        - sampling and assignment summary statistics.
"""
from __future__ import annotations
import json
import random
from itertools import combinations
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent  # ccs2026/
AUX  = ROOT / "03_detection" / "consensus" / "aux_consensus_v4.csv"
V4   = ROOT / "02_data_filtration" / "filtered_dataset_v4.csv"
OUT  = ROOT / "05_validation"

ANNOTATORS = ["padam", "harshita", "danish", "bhupendra", "afsah", "chrysm"]
N_FRAUD       = 80
N_SUSPICIOUS  = 60
N_UNKNOWN     = 60
SEED          = 42

random.seed(SEED)
rng = random.Random(SEED)

print(f"[1/4] Loading aux consensus ...")
aux = pd.read_csv(AUX, low_memory=False)
print(f"  aux rows: {len(aux):,}   labels: {dict(aux['aux_label'].value_counts())}")

# Aux v4 uses {fraud, suspicious, legitimate}; previous versions used "unknown".
unk_label = "legitimate" if "legitimate" in aux["aux_label"].values else "unknown"

print(f"[2/4] Loading v4 metadata ...")
v4 = pd.read_csv(V4, low_memory=False,
                 usecols=["url", "platform", "title", "description",
                          "goal_amount", "raised_amount", "organizer", "currency"])
v4 = v4.rename(columns={"raised_amount": "amount_raised", "organizer": "organizer_name"})

m = aux.merge(v4, on="url", how="left", suffixes=("", "_v4"))
print(f"  merged: {len(m):,}")


def stratified_sample(df: pd.DataFrame, n: int, label: str) -> pd.DataFrame:
    """Sample n items from df where aux_label == label, stratified by platform."""
    pool = df[df["aux_label"] == label].copy()
    if len(pool) <= n:
        return pool.sample(frac=1, random_state=SEED).reset_index(drop=True)
    # Allocate per platform proportional to platform's frequency in this tier.
    platform_counts = pool["platform"].value_counts()
    weights = platform_counts / platform_counts.sum()
    alloc = (weights * n).round().astype(int).to_dict()
    # Adjust for rounding to hit exactly n.
    diff = n - sum(alloc.values())
    if diff != 0:
        adj_target = max(alloc, key=alloc.get) if diff > 0 else min(alloc, key=lambda k: alloc[k] if alloc[k] > 0 else 9999)
        alloc[adj_target] += diff
    parts = []
    for plat, k in alloc.items():
        if k <= 0:
            continue
        sub = pool[pool["platform"] == plat]
        if len(sub) <= k:
            parts.append(sub)
        else:
            parts.append(sub.sample(n=k, random_state=SEED))
    out = pd.concat(parts).sample(frac=1, random_state=SEED).reset_index(drop=True)
    return out.head(n)


print(f"[3/4] Stratified sampling ...")
fraud_s = stratified_sample(m, N_FRAUD, "fraud")
susp_s  = stratified_sample(m, N_SUSPICIOUS, "suspicious")
unkn_s  = stratified_sample(m, N_UNKNOWN, unk_label)
pool = pd.concat([fraud_s, susp_s, unkn_s]).reset_index(drop=True)
pool["item_id"] = [f"v{i:03d}" for i in range(len(pool))]
print(f"  pool size: {len(pool)}")
print(f"  pool by aux_label: {dict(pool['aux_label'].value_counts())}")
print(f"  pool by platform: {dict(pool['platform'].value_counts())}")


def make_assignment(item_ids: list[str], annotators: list[str]) -> dict[str, list[str]]:
    """Distribute items across all C(N,3) triples evenly."""
    triples = list(combinations(range(len(annotators)), 3))
    assert len(triples) == 20, f"expected 20 triples, got {len(triples)}"
    items_per_triple, rem = divmod(len(item_ids), len(triples))
    assignment = {}
    rng.shuffle(triples)
    idx = 0
    for t_i, triple in enumerate(triples):
        # Items in this triple's bucket
        k = items_per_triple + (1 if t_i < rem else 0)
        annos = [annotators[i] for i in triple]
        for _ in range(k):
            if idx >= len(item_ids):
                break
            assignment[item_ids[idx]] = sorted(annos)
            idx += 1
    return assignment


print(f"[4/4] Computing 3-overlap assignment ...")
shuffled_ids = pool["item_id"].tolist()
rng.shuffle(shuffled_ids)
assignment = make_assignment(shuffled_ids, ANNOTATORS)

# Sanity check: each annotator should get 100 items
per_annotator: dict[str, int] = {a: 0 for a in ANNOTATORS}
for _item, trio in assignment.items():
    for a in trio:
        per_annotator[a] += 1
print(f"  per-annotator counts: {per_annotator}")
assert all(c == 100 for c in per_annotator.values()), "uneven assignment"


# ---- write outputs ---------------------------------------------------------
def _scrub(v):
    """JSON-safe coercion: NaN/inf -> None, numpy scalars -> Python scalars."""
    import math
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):  # numpy scalar
        try:
            v = v.item()
        except Exception:
            pass
    return v


samples_records = []
for _, r in pool.iterrows():
    samples_records.append({
        "item_id":       r["item_id"],
        "url":           r["url"],
        "platform":      r["platform"],
        "title":         (r.get("title") if isinstance(r.get("title"), str) else "") or "",
        "description":   (r.get("description") if isinstance(r.get("description"), str) else "") or "",
        "goal_amount":   _scrub(r.get("goal_amount")),
        "amount_raised": _scrub(r.get("amount_raised")),
        "currency":      (r.get("currency") if isinstance(r.get("currency"), str) else "") or "",
        "organizer_name":(r.get("organizer_name") if isinstance(r.get("organizer_name"), str) else "") or "",
        "aux_label":     r["aux_label"],
        "aux_score":     int(r["aux_score"]),
    })

(OUT / "samples.json").write_text(json.dumps(samples_records, indent=2, default=str))
(OUT / "assignment.json").write_text(json.dumps(assignment, indent=2))

flat = pd.DataFrame([
    {"item_id": iid, "annotator": a, "aux_label": pool.set_index("item_id").loc[iid, "aux_label"]}
    for iid, trio in assignment.items() for a in trio
])
flat.to_csv(OUT / "assignment.csv", index=False)

with (OUT / "summary.txt").open("w") as fp:
    fp.write(f"=== validation pool ===\n")
    fp.write(f"total: {len(pool)}\n")
    fp.write(f"by aux_label: {dict(pool['aux_label'].value_counts())}\n")
    fp.write(f"by platform: {dict(pool['platform'].value_counts())}\n\n")
    fp.write(f"=== assignment (3-overlap) ===\n")
    fp.write(f"annotators: {ANNOTATORS}\n")
    fp.write(f"per-annotator counts: {per_annotator}\n")
    fp.write(f"all C(6,3)=20 triples covered: True\n")

print(f"\nwrote:")
print(f"  {OUT / 'samples.json'}")
print(f"  {OUT / 'assignment.json'}")
print(f"  {OUT / 'assignment.csv'}")
print(f"  {OUT / 'summary.txt'}")
