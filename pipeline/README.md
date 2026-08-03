# Pipeline source

Source for the three-detector pipeline described in the paper. No data, no
credentials.

## Layout

| File | Paper |
|---|---|
| `03_detection/detectors/detector_A_external.py` | Detector A, reputation arm (VirusTotal + IPQualityScore) |
| `03_detection/detectors/detector_A_heuristics.py` | Detector A, heuristic arm (16 `hr_*` patterns) |
| `03_detection/llm/detector_B_label_questions.py` | Detector B (Qwen-2.5-72B-Instruct-AWQ, ten questions) |
| `03_detection/detectors/detector_C_identity.py` | Detector C (organizer identity graph) |
| `03_detection/intel/extract_contacts.py` | contact extraction feeding A and C |
| `03_detection/consensus/update_mongo_3det.py` | consensus sync |
| `../artifact/code/consensus/combined_hardened_consensus.py` | builds the canonical label file |
| `../artifact/code/consensus/adopt_takedown_decoupling.py` | takedown decoupling and the phone share-cap |

Detector A fires when either arm fires. Consensus: 2 or more detectors agreeing
is Fraud, exactly 1 is Suspicious, 0 is Unknown.

Identity-graph signals are `C.1_name`, `C.2_email`, `C.3_phone`, `C.4_handle`,
`C.5_domain`, `C.7_profile_url`, and `C.8_stylometric`. A community counts
toward the consensus only if it carries a deterministic edge; a shared name
alone never suffices.

## A note on earlier names

The taxonomy was reduced from five detectors to three during the study. Scripts
and intermediate CSVs originally carried the old letters, and everything here
has been normalized to the paper's. If you are reading older intermediates or
project history, the mapping is:

| Old | Now |
|---|---|
| Detector D, organizer identity | **Detector C** |
| Detector E, heuristic patterns | **Detector A**, heuristic arm |
| Detector C, narrative reuse | dropped from the taxonomy |
| `D.1_name` … `D.8_stylometric` | `C.1_name` … `C.8_stylometric` |

Intermediate files produced before the rename may still be called
`detector_D_flags.csv` and `detector_E_flags.csv`; they correspond to Detector C
and to Detector A's heuristic arm respectively.

## Not included, and why

These belong to the abandoned five-detector design and would mislead anyone
reading this as the paper's pipeline:

| Removed | Why |
|---|---|
| `detector_C_narrative.py` | narrative reuse / template families, dropped from the taxonomy |
| `llm_pipeline_qwen72b.py` | an earlier **twelve**-question Detector B; the shipped run uses the ten-question schema |
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
