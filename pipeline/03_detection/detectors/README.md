# Non-LLM detectors

| Script | Paper | Output |
|---|---|---|
| `detector_A_external.py` | Detector A, reputation arm (VirusTotal, IPQualityScore) | `outputs/detector_A_flags.csv` |
| `detector_A_heuristics.py` | Detector A, heuristic arm (16 `hr_*` patterns) | `outputs/detector_E_flags.csv` (legacy filename) |
| `detector_C_identity.py` | Detector C, organizer identity graph | `outputs/detector_D_flags.csv` (legacy filename) |

Detector A fires when either arm fires. Both A outputs and the C output are
read by `../../../artifact/code/consensus/combined_hardened_consensus.py`,
which builds the canonical label file.

The two output filenames still carry the pre-rename letters, because the shipped
CSVs were produced before the taxonomy was normalized and renaming them would
break reproduction of the canonical labels. See `../../README.md` for the full
old-to-new mapping.

Fire counts on the 100,294-campaign analysis set: A 599 (reputation arm 50,
heuristic arm 228, the remainder recovered by the corroboration waiver), C
1,083. Detector B fires on 1,100.

The image-fingerprinting and narrative-reuse detectors of the earlier
five-detector design are not part of this pipeline and are not shipped.
