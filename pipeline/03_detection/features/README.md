# Features

Per-campaign numeric features used by:
1. Template detector D (SBERT embeddings)
2. Final LightGBM classifier (26 features)

## Files

| File | Size | What it is |
|---|---|---|
| `desc_embeddings_384d.npy` | 113 MB | Pre-computed SBERT embeddings (all-MiniLM-L6-v2), one row per v3-era campaign, 384-dim, L2-normalized |
| `compute_features.py` | 9 KB | Assembles the 26 features per campaign (linguistic, structural, financial, regex-derived) |
| `nlp_features.py` | 18 KB | Text-level NLP features: readability, sentiment, urgency keywords, first-person ratio, etc. |

## Feature groups (26 total, per paper §3.3.3)

- **Linguistic (10):** Flesch reading ease, lexical diversity, description word count, title word count, VADER compound, VADER negative, urgency keywords, exclamation count, caps ratio, first-person pronoun ratio
- **Structural (5):** SBERT title-description cosine, embedded URL count, email count, phone count, image count
- **Financial & metadata (6):** log goal amount, goal-is-round, funding ratio, donor presence, organizer name length, platform encoding
- **Regex-derived (5):** payment_stack_count, any_crypto_flag, any_messenger_flag, any_off_platform_url, total_hr_flags

## Embeddings

`desc_embeddings_384d.npy` covers the ~65k v3-era campaigns. For v4 (102,708), new embeddings will need to be computed for the added campaigns. The current embedding file still works for template detection via URL-joining (see `../detectors/template_detector_v4.py`).

## Running

```bash
python3 compute_features.py     # outputs features_v4.csv once implemented
python3 nlp_features.py         # text-only features; writes separate CSV
```
