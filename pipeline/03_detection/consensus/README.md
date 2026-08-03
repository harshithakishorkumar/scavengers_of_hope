# Consensus

Three detectors vote. **2 or more agreeing is Fraud, exactly 1 is Suspicious, 0
is Unknown.**

Tier counts on the analysis set: Fraud 429 (0.43%), Suspicious 1,864 (1.86%),
Unknown 98,001 (97.71%).

| Script | What it does |
|---|---|
| `update_mongo_3det.py` | syncs the current three-detector outputs into Mongo, dropping the standalone narrative-reuse detector and renaming organizer identity from D to C |

The canonical label file is built by
`../../../artifact/code/consensus/combined_hardened_consensus.py`, with
`adopt_takedown_decoupling.py` applying takedown decoupling and the phone
share-cap on top. `../../../artifact/labels/campaign_labels.csv.gz` is the
result, and `../../../artifact/code/stats/recompute_paper_numbers.py` verifies
every number in the paper against it.

The four- and five-detector consensus builders of the earlier design, including
the auxiliary consensus used to train an earlier LoRA student, are not part of
this pipeline and are not shipped.
