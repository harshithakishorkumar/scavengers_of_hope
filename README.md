# Scavengers of Hope — pipeline outputs for cross-checking

This bundle contains the canonical corpus, consensus labels, Detector B answer
sheet, and reputation-arm verdicts from Padam's pipeline, so a parallel run can
be reconciled against it line by line. Join everything on `url`.

All CSVs are gzipped. `pandas.read_csv()` reads `.csv.gz` directly.

## The headline numbers

| | |
|---|---|
| Raw collection | 146,997 campaigns, 17 platforms |
| After filtration | **100,294 campaigns, 15 platforms** |
| Detector A fires | 599 |
| Detector B fires | 1,100 |
| Detector C fires | 1,083 |
| **Fraud** (score ≥ 2) | **429** (0.43%) |
| Suspicious (score = 1) | 1,864 (1.86%) |
| Unknown (score = 0) | 98,001 (97.71%) |

## Read this before debugging Detector B

Detector B is **not** where a low fraud count comes from. Leave-one-out on the
consensus:

| Detector removed | Fraud remaining |
|---|---|
| A removed | 77 |
| **B removed** | **372** |
| C removed | 100 |

Fraud composition (which detectors fired on each of the 429):

| Combination | Count |
|---|---|
| A + C | 312 |
| A + B + C | 60 |
| A + B | 40 |
| B + C | 17 |

So B participates in 117 of 429 labels, but deleting B entirely only costs 57,
because most B-labelled campaigns are already carried by A + C. **Deleting C
costs 329 and lands on exactly 100.** A run producing ~100 fraud labels has a
Detector C (organizer-identity graph) problem, not a Detector B problem. Check
the identity-graph arm first: edge construction, the share-cap, and the
deterministic-evidence rule.

## Contents

### `data/`
- **`filtered_dataset.csv.gz`** — the 100,294-campaign corpus, all 17 source
  columns including `description`. This is the exact input every detector ran on.
- **`corpus_index.csv.gz`** — `url, platform, description_word_count` only. Use
  this for a fast corpus-level diff without loading 160 MB of descriptions.
- **`consensus_labels.csv.gz`** — per-campaign detector fires and final tier.
  Columns: `A_hard`, `B_hard`, `C_hard` (the shipped hardened fires),
  `score_hard`, `fraud_hard`, `gated_tier` (`fraud`/`suspicious`/`unknown`),
  plus the A sub-arms (`rep_hard`, `heur_hard`), the C evidence rule (`c_rule`),
  `corroborated`, and the pre-hardening fires (`A0`, `B0`, `C0`).

### `detector_B/`
- **`PROMPT.txt`** — the exact system message, all ten question texts, the user
  template, and the JSON output schema. Model was
  **`Qwen/Qwen2.5-72B-Instruct-AWQ`** via vLLM, descriptions truncated at
  12,000 characters.
- **`detector_B_answers.csv.gz`** — all ten answers per campaign
  (`yes`/`no`/`unclear`), plus `n_yes` and `fires_ge4`. 100,251 of 100,294
  campaigns have answers; 17 failed strict JSON validation and abstain.
- **`fire_rates.csv`** — per-question yes rates, the `n_yes` distribution, and
  the fire count at every threshold.

**Fire rule: a campaign fires when at least 4 of 10 answers are `yes`.**
`unclear` does not count.

Per-question yes rates on the 100,294 corpus:

| Question | Yes | Rate |
|---|---|---|
| q1_external_payment | 6,420 | 6.40% |
| q2_deadline_pressure | 5,092 | 5.08% |
| q3_guilt_language | 15,051 | 15.01% |
| q4_defensive_language | 1,790 | 1.79% |
| q5_impersonation_no_consent | 796 | 0.79% |
| q6_tragedy_exploitation | 2,332 | 2.33% |
| q7_allocation_overpromise | 3,218 | 3.21% |
| q8_fake_credential | 1,190 | 1.19% |
| q9_external_verification | 3,365 | 3.36% |
| q10_multi_channel | 8,421 | 8.40% |

Threshold sensitivity: ≥1 fires 31,086; ≥2 fires 11,270; ≥3 fires 3,964;
**≥4 fires 1,100**; ≥5 fires 204; ≥6 fires 44.

Because the count is so threshold-sensitive, a different model that is slightly
more conservative on one or two high-rate questions (q3, q10, q1) will land well
below 1,100 without being wrong anywhere in particular. Diff your per-question
rates against the table above: the gap will concentrate in one or two questions.
There is no need to re-run inference to find that out.

### `reputation/`
- **`vt_domain_verdicts.csv.gz`** — 10,258 domains with VirusTotal engine
  counts. `flagged_ge1` = 274 domains, `flagged_ge2` = 98. **The pipeline trips
  at ≥2 engines.**
- **`ipqs_email_verdicts.csv.gz`** — 5,641 emails with the IPQS response fields
  and `flagged_ge85` (`fraud_score` ≥ 85).
- **`campaign_reputation_summary.csv.gz`** — per-campaign rollup of what was
  checked and what flagged.
- **`detector_A_flags.csv.gz`** — combined Detector A verdict per campaign with
  the reasons string.
- **`hard_rule_flags.csv.gz`** — the 16 `hr_*` regex sub-signals per campaign
  plus `regex_strength`.

**The gates matter more than the raw lookups.** Raw IPQS at `fraud_score` ≥ 85
hits 3,301 of 5,641 emails, which is obviously not a 58% fraud rate. Detector A
survives that because of four validity gates (deliverable mailbox, not a generic
role address, valid TLD, domain outside a 97-entry generic-provider list) plus
two infrastructure gates (a skiplist for shared services and link shorteners, and
a citation gate dropping any domain referenced by more than one distinct
organizer). After gating, the reputation sub-arm contributes only **50**
campaigns; the heuristic sub-arm contributes 228. Ungated, the vendor's
unverifiable-mailbox band swamps everything.

## On GlobalGiving

GlobalGiving is not in this corpus and never was. It is absent from the raw
collection of 146,997 campaigns across 17 platforms, so this is a collection-time
omission, not a filtration decision. The two platforms dropped *during*
filtration were Ulule (3,594) and Fairplaid (203), whose descriptions render
client-side and could not be retrieved as text.

## Corpus size reconciliation

Two adjustments account for most of a ~104k vs 100,294 gap:

1. **URL-variant deduplication** collapsed 2,414 records (mostly FreeFunder
   `www` vs non-`www`). Pre-dedupe the corpus is 102,708.
2. **Ulule and Fairplaid** were dropped at filtration (3,797 combined).

`data/corpus_index.csv.gz` will show exactly which URLs differ.
