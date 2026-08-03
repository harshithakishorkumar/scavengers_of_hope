"""Build the full 5-detector consensus (A + B + C + D + E) for all 102,708 v4 campaigns.

This is the *production* consensus that drives the LightGBM training labels.
It includes Detector B (the LoRA-fine-tuned Llama-3.1-8B), unlike
`build_aux_consensus.py` which deliberately excludes B to avoid circular
self-supervision when training B itself.

Inputs:
    A: 03_detection/detectors/outputs/hard_rule_flags_v4_plus.csv  (detector_A_flag)
    B: 03_detection/llm/llm_labels_llama8b_ft_v4.csv               (B_flag)  *** new ***
    C: 03_detection/detectors/outputs/phash_clusters_v4.csv        (detector_C_flag)
    D: 03_detection/detectors/outputs/template_families_v4.csv    (detector_D_flag)
    E: 03_detection/detectors/outputs/organizer_network_v4_clean.csv (detector_E_flag)

Output (paper-wide consensus rule, score = sum of 5 binary flags):
    score >= 3  ->  fraud
    score 1-2   ->  suspicious
    score == 0  ->  unknown
"""
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent  # ccs2026/
V4   = ROOT / "02_data_filtration" / "filtered_dataset_v4.csv"
DET  = ROOT / "03_detection" / "detectors" / "outputs"
LLM  = ROOT / "03_detection" / "llm" / "llm_labels_llama8b_ft_v4.csv"

A_FILE = DET / "hard_rule_flags_v4_plus.csv"
C_FILE = DET / "phash_clusters_v4.csv"
D_FILE = DET / "template_families_v4_filtered.csv"  # post-filter (organizer-distinctness + paren-clone gate)
E_FILE = DET / "organizer_network_v4_clean.csv"

OUT = ROOT / "03_detection" / "consensus" / "consensus_v4.csv"


def load_flag(path: Path, flag_col: str) -> dict:
    df = pd.read_csv(path, low_memory=False)
    return dict(zip(df["url"].astype(str), df[flag_col].astype(int)))


print("[1/6] Loading v4 corpus ...")
v4 = pd.read_csv(V4, low_memory=False, usecols=["url", "platform"])
print(f"  v4: {len(v4):,} campaigns")

print("[2/6] Loading A (hard rules + VT + IPQS) ...")
a_map = load_flag(A_FILE, "detector_A_flag")
print(f"  A flagged: {sum(a_map.values()):,}")

print("[3/6] Loading B (Llama-3.1-8B LoRA, 12-question + label) ...")
if not LLM.exists():
    raise FileNotFoundError(
        f"{LLM} does not exist yet. Wait for vast.ai LoRA training/inference "
        f"to finish, then re-run this script. (B_flag is required for the full "
        f"5-detector consensus; if you need labels without B, use "
        f"build_aux_consensus.py.)"
    )
b_df = pd.read_csv(LLM, low_memory=False)
b_map = dict(zip(b_df["url"].astype(str), b_df["B_flag"].astype(int)))
print(f"  B flagged: {sum(b_map.values()):,}")

print("[4/6] Loading C (pHash near-duplicates, Hamming <=6) ...")
c_map = load_flag(C_FILE, "detector_C_flag")
print(f"  C flagged: {sum(c_map.values()):,}")

print("[5/6] Loading D (template families, HDBSCAN + cosine gate) ...")
d_map = load_flag(D_FILE, "detector_D_flag")
print(f"  D flagged: {sum(d_map.values()):,}")

print("[6/6] Loading E (organizer network) ...")
e_map = load_flag(E_FILE, "detector_E_flag")
print(f"  E flagged: {sum(e_map.values()):,}")

print("\nbuilding 5-detector consensus...")
rows = []
for _, r in v4.iterrows():
    u = r["url"]
    fa = a_map.get(u, 0)
    fb = b_map.get(u, 0)
    fc = c_map.get(u, 0)
    fd = d_map.get(u, 0)
    fe = e_map.get(u, 0)
    score = fa + fb + fc + fd + fe
    if score >= 3:    label = "fraud"
    elif score >= 2:  label = "suspicious"
    else:             label = "unknown"
    rows.append({
        "url":      u,
        "platform": r["platform"],
        "A": fa, "B": fb, "C": fc, "D": fd, "E": fe,
        "consensus_score": score,
        "consensus_label": label,
    })

out = pd.DataFrame(rows)
out.to_csv(OUT, index=False)

# ---- summary -----------------------------------------------------------------
print(f"\n=== saved {OUT} ===")
print("Label distribution (5-detector consensus):")
print(out["consensus_label"].value_counts())
print("\nScore distribution:")
print(out["consensus_score"].value_counts().sort_index())
print("\nPer-platform fraud counts:")
ct = pd.crosstab(out["platform"], out["consensus_label"])
print(ct)

# Per-detector contribution to fraud labels (for paper appendix)
fraud_only = out[out["consensus_label"] == "fraud"]
print(f"\n=== Detector contribution within {len(fraud_only):,} fraud-labelled campaigns ===")
for det in ["A", "B", "C", "D", "E"]:
    n = int(fraud_only[det].sum())
    pct = 100.0 * n / max(len(fraud_only), 1)
    print(f"  {det}: {n:>6,}  ({pct:5.1f}%)")

summary = {
    "total_campaigns": int(len(out)),
    "label_dist":      out["consensus_label"].value_counts().to_dict(),
    "score_dist":      out["consensus_score"].value_counts().sort_index().to_dict(),
    "per_detector_in_fraud":
        {d: int(fraud_only[d].sum()) for d in ["A", "B", "C", "D", "E"]},
}
(OUT.parent / "consensus_v4_summary.json").write_text(json.dumps(summary, indent=2, default=str))
print(f"\nsummary written to {OUT.parent / 'consensus_v4_summary.json'}")
