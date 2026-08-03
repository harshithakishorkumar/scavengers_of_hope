# Consensus

Combines detector flags into a consensus fraud label per campaign.

## Files

| File | What it is |
|---|---|
| `build_aux_consensus.py` | Builder: joins A + C + D + E → `aux_consensus_v4.csv` |
| `aux_consensus_v4.csv` | **4-detector auxiliary consensus** labels (excludes Detector B) |

## Two consensuses

1. **Auxiliary consensus (A + C + D + E, 4 detectors).** This is what `aux_consensus_v4.csv` contains. Threshold `aux_score ≥ 2` for fraud. **Used as training labels for Detector B.** Excluding B here avoids circular self-supervision.

2. **Full consensus (A + B + C + D + E, 5 detectors).** To be built once Detector B's QLoRA fine-tune completes. Threshold `score ≥ 3` for fraud. **This is what the paper reports**, and what the LightGBM classifier distills.

## Current aux_consensus_v4.csv

```
columns: url, platform, A, C, D, E, aux_score, aux_label
rows:    102,708
label distribution:
  legitimate (score 0): 79,269
  suspicious (score 1): 15,775
  fraud      (score≥2):  7,664
score distribution:
  0: 79,269   1: 15,775   2: 6,981   3: 653   4: 30
```

## Re-running

```bash
python3 build_aux_consensus.py
```

Reads from `../detectors/outputs/` and writes `aux_consensus_v4.csv` in this folder. Takes ~30 seconds.
