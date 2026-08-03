#!/usr/bin/env python3
"""Recompute every consensus-dependent number in the paper from the artifact.

Reads only artifact/labels/campaign_labels.csv.gz and the evidence layer. No
database, no vendor API, no campaign text. Standard library only.

    python3 recompute_paper_numbers.py [--artifact ../..]

Each line prints the recomputed value next to the value printed in the paper,
with OK or MISMATCH. A clean run is all OK.
"""
import argparse
import collections
import csv
import gzip
from pathlib import Path

csv.field_size_limit(10 ** 9)

# Values as they appear in the paper. Recomputation must land on these.
PAPER = {
    "corpus": 100294, "platforms": 15,
    "fraud": 429, "suspicious": 1864, "unknown": 98001,
    "A": 599, "B": 1100, "C": 1083,
    "A_reputation": 50, "A_heuristic": 228,
    "corroborated_fraud": 389, "text_only_fraud": 40,
    "AC": 372, "AB": 100, "BC": 77,
    "comp_AC": 312, "comp_ABC": 60, "comp_AB": 40, "comp_BC": 17,
    "lo_A": 77, "lo_B": 372, "lo_C": 100,
    "thresh_ge1": 2293, "thresh_ge3": 60,
    "B_ge3": 3964, "B_ge4": 1100, "B_ge5": 204,
}

ap = argparse.ArgumentParser()
ap.add_argument("--artifact", default=str(Path(__file__).resolve().parents[2]),
                help="path to the artifact/ directory")
args = ap.parse_args()
ART = Path(args.artifact)

results = []


def check(name, got, key=None):
    exp = PAPER.get(key or name)
    ok = exp is None or got == exp
    results.append(ok)
    tag = "     " if exp is None else ("OK   " if ok else "MISMATCH")
    exp_s = "" if exp is None else f"  (paper: {exp})"
    print(f"  {tag} {name:<34} {got:>7}{exp_s}")


def read(rel):
    with gzip.open(ART / rel, "rt", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


rows = read("labels/campaign_labels.csv.gz")
I = lambda r, k: int(r[k])

print("\nCorpus")
check("campaigns", len(rows), "corpus")
check("platforms", len({r["platform"] for r in rows}), "platforms")

print("\nTiers")
tiers = collections.Counter(r["tier"] for r in rows)
check("fraud", tiers["fraud"])
check("suspicious", tiers["suspicious"])
check("unknown", tiers["unknown"])

print("\nDetector fires")
check("detector A", sum(I(r, "detector_A") for r in rows), "A")
check("detector B", sum(I(r, "detector_B") for r in rows), "B")
check("detector C", sum(I(r, "detector_C") for r in rows), "C")
check("A reputation arm", sum(I(r, "A_reputation_arm") for r in rows), "A_reputation")
check("A heuristic arm", sum(I(r, "A_heuristic_arm") for r in rows), "A_heuristic")

fraud = [r for r in rows if r["tier"] == "fraud"]

print("\nCorroboration")
corr = sum(I(r, "corroborated") for r in fraud)
check("corroborated fraud", corr, "corroborated_fraud")
check("text-only fraud", len(fraud) - corr, "text_only_fraud")

print("\nPairwise agreement (whole corpus)")
pair = lambda x, y: sum(1 for r in rows if I(r, x) and I(r, y))
check("A and C", pair("detector_A", "detector_C"), "AC")
check("A and B", pair("detector_A", "detector_B"), "AB")
check("B and C", pair("detector_B", "detector_C"), "BC")

print("\nFraud composition")
comp = collections.Counter(
    "".join(n for n, k in [("A", "detector_A"), ("B", "detector_B"),
                           ("C", "detector_C")] if I(r, k)) for r in fraud)
for combo in ["AC", "ABC", "AB", "BC"]:
    check(f"fires {combo}", comp[combo], f"comp_{combo}")

print("\nLeave-one-out (fraud remaining if a detector is removed)")
for drop, key in [("detector_A", "lo_A"), ("detector_B", "lo_B"),
                  ("detector_C", "lo_C")]:
    n = sum(1 for r in rows
            if sum(0 if k == drop else I(r, k)
                   for k in ["detector_A", "detector_B", "detector_C"]) >= 2)
    check(f"{drop} removed", n, key)

print("\nConsensus threshold sensitivity")
score = collections.Counter(I(r, "score") for r in rows)
check("score >= 1", sum(v for k, v in score.items() if k >= 1), "thresh_ge1")
check("score >= 2", sum(v for k, v in score.items() if k >= 2), "fraud")
check("score >= 3", sum(v for k, v in score.items() if k >= 3), "thresh_ge3")

print("\nDetector B question-threshold sensitivity")
b = read("evidence/detector_B_answers.csv.gz")
ny = collections.Counter(int(r["n_yes"]) for r in b)
for t in [3, 4, 5]:
    check(f"at least {t} yes", sum(v for k, v in ny.items() if k >= t), f"B_ge{t}")
check("parse abstentions", sum(1 for r in b if r["parsed_ok"] != "True"))

print("\nPer-platform fraud (paper Table I / Figure platform rates)")
tot = collections.Counter(r["platform"] for r in rows)
frd = collections.Counter(r["platform"] for r in fraud)
for p, n in sorted(frd.items(), key=lambda kv: -kv[1]):
    print(f"        {p:<16} {n:>4} of {tot[p]:>6}  ({100*n/tot[p]:.2f}%)")

print("\nIdentity graph (Detector C)")
comm = read("evidence/detector_C_communities.csv.gz")
sig = collections.Counter()
for r in comm:
    for s in (r["fired_signals"] or "").split(";"):
        if s:
            sig[s.split("_", 1)[-1]] += 1
groups = {r["community_id"] for r in comm if r["community_id"]}
check("communities", len(groups))
check("campaigns in a community", sum(1 for r in comm if r["community_id"]))
for s, n in sig.most_common():
    print(f"        signal {s:<14} {n:>6}")

print("\nReputation cache")
vt = read("evidence/virustotal_domain_cache.csv.gz")
check("domains cached", len(vt))
check("domains flagged (>=2 engines)", sum(int(r["flagged_ge2"]) for r in vt))
ipqs = read("evidence/ipqs_email_cache.csv.gz")
check("emails cached", len(ipqs))
check("emails at fraud_score >= 85", sum(int(r["flagged_ge85"]) for r in ipqs))
print("        (the four validity gates and two infrastructure gates of")
print("         Section III cut this to the reputation-arm count above)")

bad = results.count(False)
print(f"\n{len(results) - bad}/{len(results)} checks OK"
      + (f", {bad} MISMATCH" if bad else ""))
raise SystemExit(1 if bad else 0)
