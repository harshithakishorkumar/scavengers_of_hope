# Detectors A, C, D, E

Four non-LLM detectors. Detector B lives in `../llm/` and `../training/`.

## Scripts

| Script | Detector | Input | Output |
|---|---|---|---|
| `hard_rule_v4.py` | A (regex) | `02_data_filtration/filtered_dataset_v4.csv` | `outputs/hard_rule_flags_v4.csv` |
| `rebuild_detector_A.py` | A (regex + VT + IPQS) | `outputs/hard_rule_flags_v4.csv`, `intel/vt_*`, `intel/ipqs_*` | `outputs/hard_rule_flags_v4_plus.csv` |
| `phash_v4.py` | C (pHash) | `filtered_dataset_v4.csv` (fetches og:images) | `outputs/phash_results_v4_delta.jsonl` |
| `template_detector_v4.py` | D (SBERT templates) | `filtered_dataset_v4.csv`, `features/desc_embeddings_384d.npy` | `outputs/template_families_v4.csv` |
| `organizer_network_v4_clean.py` | E (organizer graph) | `filtered_dataset_v4.csv`, `intel/campaign_contacts_v4_delta.json` | `outputs/organizer_network_v4_clean.csv` |

## Firing rates (v4, 102,708 campaigns)

| Detector | Firing | % |
|---|---|---|
| A | 6,120 | 6.0% |
| C | 9,321 | 9.1% |
| D | 14,171 | 13.8% |
| E | 2,204 | 2.1% |

Firing rates are lower than the paper's old v3 numbers because the v4 dataset has more legitimate campaigns from newly-added platforms (Spotfund, etc.).

## outputs/

Contains the detector result CSVs and JSONLs. These are the actual labels the consensus step reads. Per-file details:

- `hard_rule_flags_v4_plus.csv` — per-campaign: regex flags + A_vt_bad_domain + A_ipqs_bad_email + A_ipqs_bad_phone + detector_A_flag
- `template_families_v4.csv` — per-campaign: template_family_id, family_size, cross_platform_family, template_similarity_max, detector_D_flag
- `organizer_network_v4_clean.csv` — per-campaign: organizer, org_cluster_id, org_cluster_size, org_cluster_platforms, detector_E_flag
- `phash_results_v4_delta.jsonl` — per-campaign: url, platform, phash, og_image (only for successfully-fetched images; ~30k rows)
- `phash_results_full.jsonl` — older phash run covering ~61k v3-era URLs
