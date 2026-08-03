"""Build 4-detector auxiliary consensus (A + C + D + E) for all 102,708 v4 campaigns.

This produces the TRAINING labels for Detector B (fine-tuned Qwen 72B).
Detector B's own outputs are deliberately NOT used here.
"""
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent  # ccs2026/
V4 = ROOT / '02_data_filtration' / 'filtered_dataset_v4.csv'

DET_OUT = ROOT / '03_detection' / 'detectors' / 'outputs'
A_FILE = DET_OUT / 'hard_rule_flags_v4_plus.csv'
# Detector C now consumes Hamming-clustered output (phash_cluster_v4.py).
# We keep the .jsonl paths as a fallback for the legacy exact-match logic.
C_CLUSTERS = DET_OUT / 'phash_clusters_v4.csv'
C_FILE     = DET_OUT / 'phash_results_v4_delta.jsonl'
C_OLD      = DET_OUT / 'phash_results_full.jsonl'
D_FILE = DET_OUT / 'template_families_v4.csv'
E_FILE = DET_OUT / 'organizer_network_v4_clean.csv'

OUT = ROOT / '03_detection' / 'consensus' / 'aux_consensus_v4.csv'

print("[1/5] Loading v4 corpus...")
v4 = pd.read_csv(V4, low_memory=False, usecols=['url', 'platform'])
print(f"  {len(v4):,} campaigns")

# --- A: regex + VT + IPQS combined (detector_A_flag already computed) ---
print("[2/5] Loading A (regex + VT + IPQS)...")
a = pd.read_csv(A_FILE, low_memory=False)
a_map = a.set_index('url')['detector_A_flag'].to_dict()
print(f"  A flagged: {sum(a_map.values()):,}")

# --- C: pHash near-duplicate clusters (Hamming <=6 over 64-bit pHash) ---
# Source: phash_cluster_v4.py (which reads the .jsonl files, truncates every
# phash to a uniform 64-bit representation, runs Hamming clustering, and
# writes per-campaign cluster_id + detector_C_flag to phash_clusters_v4.csv).
# A campaign gets C=1 iff it sits in a cluster of size >= 2 (i.e. shares its
# image with at least one other campaign within Hamming <= 6 bits).
print("[3/5] Loading C (pHash Hamming-near-duplicate clusters)...")
if C_CLUSTERS.exists():
    cdf = pd.read_csv(C_CLUSTERS, low_memory=False, usecols=['url', 'detector_C_flag'])
    c_map = dict(zip(cdf['url'], cdf['detector_C_flag'].astype(int)))
    print(f"  C source: {C_CLUSTERS.name}   C flagged: {sum(c_map.values()):,}")
else:
    # Fallback: legacy exact-hash duplicate logic
    print(f"  WARN: {C_CLUSTERS.name} not found; falling back to exact-hash matching")
    phash_by_url = {}
    for jl in [C_OLD, C_FILE]:
        if not jl.exists(): continue
        with open(jl) as f:
            for line in f:
                try: rec = json.loads(line)
                except: continue
                u, h = rec.get('url'), rec.get('phash')
                if u and h and u not in phash_by_url:
                    phash_by_url[u] = h
    from collections import Counter
    hash_count = Counter(phash_by_url.values())
    c_map = {u: int(hash_count[h] >= 2) for u, h in phash_by_url.items()}
    print(f"  C hashed: {len(phash_by_url):,}   C flagged (exact-match fallback): {sum(c_map.values()):,}")

# --- D: template family size >=2 ---
print("[4/5] Loading D (template families)...")
d = pd.read_csv(D_FILE, low_memory=False)
d_map = d.set_index('url')['detector_D_flag'].to_dict()
print(f"  D flagged: {sum(d_map.values()):,}")

# --- E: organizer cluster >=5 OR cross-platform ---
print("[5/5] Loading E (organizer network)...")
e = pd.read_csv(E_FILE, low_memory=False)
e_map = e.set_index('url')['detector_E_flag'].to_dict()
print(f"  E flagged: {sum(e_map.values()):,}")

# Build auxiliary consensus
print("\nBuilding auxiliary consensus...")
rows = []
for _, r in v4.iterrows():
    u = r['url']
    fa = a_map.get(u, 0)
    fc = c_map.get(u, 0)
    fd = d_map.get(u, 0)
    fe = e_map.get(u, 0)
    score = fa + fc + fd + fe
    if score >= 2:  label = 'fraud'
    elif score == 1: label = 'suspicious'
    else:            label = 'unknown'
    rows.append({'url': u, 'platform': r['platform'],
                 'A': fa, 'C': fc, 'D': fd, 'E': fe,
                 'aux_score': score, 'aux_label': label})

out = pd.DataFrame(rows)
out.to_csv(OUT, index=False)

print(f"\n=== saved {OUT} ===")
print(f"Label distribution:")
print(out['aux_label'].value_counts())
print(f"\nScore distribution:")
print(out['aux_score'].value_counts().sort_index())
print(f"\nPer-platform label breakdown:")
print(pd.crosstab(out['platform'], out['aux_label']))
