# Pipeline source

Source for the three-detector pipeline described in the paper. No data, no
credentials.

## Filenames do not match the paper's detector letters

The taxonomy was reduced from five detectors to three during the study, and the
scripts kept their original filenames. Read this table before anything else.

| File | Paper | Status |
|---|---|---|
| `03_detection/detectors/detector_A_external.py` | Detector A, **reputation arm** (VirusTotal + IPQualityScore) | live |
| `03_detection/detectors/detector_E_heuristics.py` | Detector A, **heuristic arm** (16 `hr_*` patterns) | **live** |
| `03_detection/llm/detector_B_label_questions.py` | Detector B (Qwen-2.5-72B-Instruct-AWQ, ten questions) | live |
| `03_detection/detectors/detector_D_identity.py` | **Detector C** (organizer identity graph) | live |
| `03_detection/intel/extract_contacts.py` | contact extraction feeding A and C | live |
| `03_detection/consensus/update_mongo_3det.py` | consensus sync, current taxonomy | live |
| `../artifact/code/consensus/combined_hardened_consensus.py` | builds the canonical label file | live |
| `../artifact/code/consensus/adopt_takedown_decoupling.py` | applies takedown decoupling and the phone share-cap | live |

Two names cause the most confusion:

- **`detector_E_heuristics.py` is not dead.** Detector A in the paper is the OR
  of two arms, and this file is the heuristic one. Its output
  (`detector_E_flags.csv`) is read by the canonical consensus builder.
- **`detector_D_identity.py` is the paper's Detector C.** The old Detector C was
  narrative reuse, which was dropped from the taxonomy; the old Detector D,
  organizer identity, was renamed C. `update_mongo_3det.py` performs exactly
  that rename.

## Not included, and why

These were removed rather than shipped, because they belong to the abandoned
five-detector design and would mislead anyone reading this as the paper's
pipeline:

| Removed | Why |
|---|---|
| `detector_C_narrative.py` | narrative reuse / template families, dropped from the taxonomy |
| `llm_pipeline_qwen72b.py` | an earlier **twelve**-question Detector B; the shipped run uses the ten-question schema in `detector_B_label_questions.py` |
| `build_consensus_v4.py` | five-detector consensus |
| `build_aux_consensus.py` | four-detector auxiliary consensus, used only to train an earlier LoRA student |
| `update_mongo_consensus.py`, `update_mongo_4det.py` | five- and four-detector Mongo syncs |

## Detector B specifics

`03_detection/llm/detector_B_label_questions.py` runs
`Qwen/Qwen2.5-72B-Instruct-AWQ` over campaign descriptions and emits ten yes/no
answers per campaign. The full question text, system message, and JSON output
schema are in `03_detection/llm/detector_B_prompt.txt`.

A campaign fires when at least 4 of 10 answers are `yes`. `unclear` does not
count, and a response failing strict JSON validation abstains.

The corpus is steeply sensitive around that threshold, so a different model
lands on a very different count: at least 3 fires on 3,964 campaigns, at least
4 on 1,100, at least 5 on 204.

## Credentials

Keys load from a gitignored `api_keys.json` through
`03_detection/intel/api_keyring.py`. Copy `api_keys.example.json`, fill it in,
and `chmod 600` it; `DONATIONSCAM_KEYS` overrides the path. Key order is
load-bearing because the rotation state files address keys by list index, so
append new keys at the end.
