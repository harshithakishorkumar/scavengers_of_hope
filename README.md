# Scavengers of Hope

Cross-platform measurement of fraudulent crowdfunding campaigns: 100,294
campaigns across 15 platforms, labelled by a three-detector consensus.

| Tier | Campaigns | Share |
|---|---|---|
| Fraud (2 or 3 detectors agree) | 429 | 0.43% |
| Suspicious (1 detector) | 1,864 | 1.86% |
| Unknown (0 detectors) | 98,001 | 97.71% |

Detector A fires on 599 campaigns, B on 1,100, C on 1,083.

## Layout

**`artifact/`** — the research artifact, anonymized for public release. Labels,
the per-detector evidence layer, the generator scripts, and the browser
extension. `cd artifact/code/stats && python3 recompute_paper_numbers.py`
reproduces 35 of the paper's numbers with no dependencies. Start at
[artifact/README.md](artifact/README.md).

**`pipeline/`** — full source of the pipeline that produced the results:
filtration, the three detectors, the reputation-lookup clients, consensus
construction, and the MongoDB loaders. Source only, no data and no credentials.

**`internal/`** — de-anonymized outputs shared between collaborators for
cross-pipeline reconciliation: real campaign URLs, full descriptions, and IPQS
verdicts keyed by real email addresses. See
[internal/README.md](internal/README.md) for the leave-one-out diagnostic.

> **`internal/` must be deleted before this repository is made public.** It
> contains exactly the identity values that Section VI commits to withholding.
> The public artifact is `artifact/`, which is anonymized and self-contained.

## Credentials

No API keys are in this repository, and none belong here. The pipeline reads
them from a gitignored `api_keys.json`; copy
[pipeline/03_detection/intel/api_keys.example.json](pipeline/03_detection/intel/api_keys.example.json),
fill it in, and `chmod 600` it. `DONATIONSCAM_KEYS` overrides the path.

Key order is load-bearing: the rotation state files address keys by list index,
so append new keys at the end rather than inserting them.

## Detectors

**A, External signals.** Two arms. The reputation arm checks extracted domains
against VirusTotal (tripping at 2 or more engines) and extracted emails against
IPQualityScore, behind four validity gates and two infrastructure gates. The
heuristic arm is 16 regex patterns for off-platform payment routing.

**B, Behavioral.** Qwen-2.5-72B-Instruct answers ten yes/no questions about
manipulation tactics present in the description. Fires at 4 or more `yes`.
Every question asks whether a tactic is present, never whether verifiable detail
is absent, so a sparse but honest appeal cannot accumulate `yes` answers by
omission.

**C, Organizer identity.** A graph over five deterministic signals: organizer
name, email, phone, payment account, and shared domain. Communities come from
Louvain modularity optimization. A community counts only if it carries a
deterministic identity edge; a shared name alone never suffices.

Consensus: 2 or more detectors agreeing is Fraud, exactly 1 is Suspicious, 0 is
Unknown.
