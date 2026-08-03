# Non-LLM detectors

Filenames predate the reduction from five detectors to three. See
`../../README.md` for the full mapping.

| Script | Paper | Output |
|---|---|---|
| `detector_A_external.py` | Detector A, reputation arm (VirusTotal, IPQualityScore) | `outputs/detector_A_flags.csv` |
| `detector_E_heuristics.py` | Detector A, heuristic arm (16 `hr_*` patterns) | `outputs/detector_E_flags.csv` |
| `detector_D_identity.py` | **Detector C**, organizer identity graph | `outputs/detector_D_flags.csv` |

Detector A fires when either arm fires. Both output files are read by
`../../../artifact/code/consensus/combined_hardened_consensus.py`, which builds
the canonical label file.

Fire counts on the 100,294-campaign analysis set: A 599 (reputation arm 50,
heuristic arm 228, the remainder recovered by the corroboration waiver), C
1,083. Detector B fires on 1,100.

The image-fingerprinting and narrative-reuse detectors of the earlier
five-detector design are not part of this pipeline and are not shipped.
