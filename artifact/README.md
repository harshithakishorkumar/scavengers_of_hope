# Scavengers of Hope — research artifact

Artifact for *Scavengers of Hope: Exploring Fraudulent Behavior in Crowdfunding
Campaigns*. It reproduces every consensus-dependent number in the paper from
released files alone, with no database, no vendor account, and no network
access.

## Quick check

```sh
cd code/stats
python3 recompute_paper_numbers.py
```

Standard library only, no dependencies, about ten seconds. It prints each
recomputed value beside the value printed in the paper and exits non-zero on any
mismatch. A clean run reports **35/35 checks OK**, covering the tier counts,
per-detector fire counts, the A sub-arms, corroboration strata, pairwise
agreement, fraud composition, leave-one-out, and both sensitivity sweeps.

## What is here

**1. Labels — `labels/campaign_labels.csv.gz`**

One row per campaign in the analysis set (100,294 rows). Carries the three
per-detector flags, the consensus score and tier, and the corroboration stratum.
Every tier count, overlap, and sensitivity number in Sections IV and V
recomputes from this file alone.

| Column | Meaning |
|---|---|
| `campaign_id` | opaque identifier (see Anonymization) |
| `platform` | one of 15 |
| `detector_A` / `detector_B` / `detector_C` | hardened per-detector fire |
| `score` | number of detectors that fired, 0 to 3 |
| `tier` | `fraud` (score >= 2), `suspicious` (1), `unknown` (0) |
| `corroborated` | label rests on a non-text signal |
| `A_reputation_arm` / `A_heuristic_arm` | which arm of A fired |
| `C_evidence_rule` | community carried a deterministic identity edge |
| `takedown_observed` | campaign was gone at the May 2026 recrawl; decoupled from labels, reported only as an outcome |

**2. Evidence — `evidence/`**

The layer behind each flag, so any single label can be traced to what produced
it.

| File | Contents |
|---|---|
| `detector_A_pattern_hits.csv.gz` | the 16 `hr_*` heuristic patterns per campaign, plus `regex_strength` |
| `detector_B_answers.csv.gz` | all ten question answers per campaign, `n_yes`, and `parsed_ok` |
| `detector_C_communities.csv.gz` | community id, size, platform count, and which identity signals fired |
| `virustotal_domain_cache.csv.gz` | 10,258 cached VirusTotal domain responses |
| `ipqs_email_cache.csv.gz` | 5,641 cached IPQualityScore email responses, keyed by hashed address |

Two derivations are worth stating because the evidence files are raw detector
output and the labels are gated:

- `detector_C` = community `flagged` **AND** `C_evidence_rule`. The communities
  file is the raw identity graph; the evidence rule (a deterministic edge, not a
  shared name alone) is the gate. Verified to hold on all 100,294 rows.
- `detector_A`'s reputation arm is **not** simply `flagged_ge85` in the IPQS
  cache. Raw IPQS at `fraud_score` >= 85 hits 3,301 of 5,641 addresses; the four
  validity gates and two infrastructure gates of Section III cut the reputation
  arm to 50 campaigns. The cache ships ungated on purpose, so the gates can be
  audited rather than taken on trust.

**3. Generators — `code/`**

`code/stats/recompute_paper_numbers.py` is the self-contained checker described
above. `code/consensus/`, `code/detectors/`, and `code/figures/` hold the
scripts that produced the numbers and figures in the paper. Those read the
internal MongoDB corpus and are included as the record of how results were
generated, not as turnkey scripts; the checker is the reproducible path.

**4. Extension and service — `extension_and_service/`**

The Chrome MV3 extension and the FastAPI lookup service behind it. See its own
`README.md` and `INSTALL.md`.

## Anonymization

Per Section VI, the public artifact replaces campaign URLs and organizer names
with opaque identifiers, and extracted identity values (emails, phones, payment
accounts) do not ship publicly; they travel only in the private disclosure
package sent to platforms.

`campaign_id` is a salted SHA-256 of the URL, truncated to 12 hex characters;
`email_id` in the IPQS cache is the same construction over the lowercased
address. The salt is held privately and is not in this repository, so the
identifiers cannot be reversed by hashing candidate URLs. They are stable across
every file here, so the label file and evidence files join on `campaign_id`.

Domains in the VirusTotal cache ship in the clear. They are internet
infrastructure rather than personal identifiers, the VirusTotal verdicts on them
are already public, and withholding them would make the reputation arm
unauditable.

Campaign text does not ship. Detector B's per-question answers are released
instead, which is what the labels depend on.

## What cannot be recomputed here

Re-running Detector B end to end needs campaign descriptions and a
Qwen-2.5-72B-Instruct deployment, and rebuilding the identity graph needs the
extracted identity values. Both inputs are withheld for the reasons above. The
released evidence is the output of those stages, which is sufficient to audit
every label and recompute every number in the paper.
