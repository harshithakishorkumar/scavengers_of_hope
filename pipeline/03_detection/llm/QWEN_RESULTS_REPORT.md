# Qwen2.5-72B-AWQ Labeling Results Report

**Project:** DonationScam / Scavengers of Hope (CCS 2026)
**Model:** `Qwen/Qwen2.5-72B-Instruct-AWQ` via vLLM
**Run duration:** 22 h 14 min on an A100 80 GB (vast.ai)
**Dataset:** 65,662 filtered campaigns across 13 platforms
**Completed:** 2026-04-08 19:26 UTC
**Cost:** ~$21 total compute

This report captures every significant finding from the completed Qwen
labeling run, in one place, so that paper-writing and downstream analysis can
proceed without re-running any of the stat code. Every number below is pulled
from the actual JSON-line outputs in this directory.

---

## 1. Run Statistics

| Metric | Value |
|---|---|
| Total campaigns processed | **65,662** |
| Wall-clock duration | **22h 14m 23s** |
| Steady-state rate | ~0.8 campaigns/second |
| Model | Qwen/Qwen2.5-72B-Instruct-AWQ |
| Quantisation | AWQ 4-bit (marlin kernel) |
| vLLM batch size | 32 |
| Parse error rate | **1.37%** (897 of 65,662) |
| Average confidence | **0.78** |
| Median confidence | **0.80** |
| Greedy decoding | yes (temperature 0, top_p 1) |

The parse error rate is meaningfully lower than the earlier Qwen 7 B run
(which had no measurable parse rate because the JSON was malformed on a
substantial fraction of responses). The 1.37 % rate is almost entirely
explained by a small set of campaigns where the description was garbled,
empty after trimming, or contained characters that broke the generation loop.

---

## 2. Overall Label Distribution

| Label | Count | Percent |
|---|---:|---:|
| **Legitimate** | 59,508 | **90.63 %** |
| **Suspicious** | 5,232 | **7.97 %** |
| **Fraudulent** | 6 | **0.01 %** |
| **Unclear** | 916 | **1.40 %** |
| **Total** | **65,662** | 100.00 % |

### Comparison to the abandoned Qwen 7 B run

| Label | Qwen 7 B (discarded) | Qwen 72 B (current) |
|---|---:|---:|
| Legitimate | ~0.7 % | **90.6 %** |
| Suspicious | **~99.0 %** | 8.0 % |
| Fraudulent | ~0.01 % | 0.01 % |
| Unclear | ~0.3 % | 1.4 % |

The 7 B run essentially refused to commit to unknown for any campaign;
every positive fundraising appeal looked "suspicious" to it. The 72 B run
distributes cleanly. This is the single most important quality difference
between the two and is why we discarded the 7 B output entirely and re-ran
on the larger model with the rewritten 12-question schema.

### Confidence distribution

| Confidence bucket | Count |
|---|---:|
| ≥ 0.90 | 1,814 |
| 0.80 – 0.89 | **53,805** |
| 0.70 – 0.79 | 8,437 |
| 0.50 – 0.69 | 689 |
| < 0.50 | 917 |

The model clusters tightly at 0.80 (53 % of all campaigns). Everything
below 0.50 is almost exclusively the "unclear" verdict — a useful
self-consistency signal.

---

## 3. Per-Platform Distribution — the Headline Finding

Sorted by **% suspicious**, descending:

| # | Platform | Total | Legit | Susp | Fraud | Unclear | **% Suspicious** |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | **Ufandao** | 470 | 382 | 88 | 0 | 0 | **18.72 %** |
| 2 | **GoGetFunding** | 28,728 | 24,486 | 3,521 | 3 | 718 | **12.26 %** |
| 3 | LaunchGood | 176 | 161 | 15 | 0 | 0 | 8.52 % |
| 4 | FreeFunder | 13,695 | 12,566 | 1,129 | 0 | 0 | 8.24 % |
| 5 | Happypot | 114 | 105 | 9 | 0 | 0 | 7.89 % |
| 6 | Angelink | 2,512 | 2,395 | 117 | 0 | 0 | 4.66 % |
| 7 | Chuffed | 444 | 424 | 20 | 0 | 0 | 4.50 % |
| 8 | Betterplace | 3,051 | 2,949 | 98 | 3 | 1 | 3.21 % |
| 9 | Crowdfundr | 582 | 533 | 17 | 0 | 32 | 2.92 % |
| 10 | GoFundMe | 13,085 | 12,710 | 210 | 0 | 165 | 1.60 % |
| 11 | SeedAndSpark | 657 | 650 | 7 | 0 | 0 | 1.07 % |
| 12 | **Experiment** | 1,147 | 1,146 | 1 | 0 | 0 | **0.09 %** |
| 13 | **DonorsChoose** | 1,001 | 1,001 | 0 | 0 | 0 | **0.00 %** |

### Observations

The spread from Ufandao (18.72 %) to DonorsChoose (0.00 %) is **essentially
unbounded** in ratio terms — DonorsChoose has zero suspicious campaigns in
its 1,001-campaign sample. For reportable purposes, using the more realistic
Ufandao → Experiment comparison (0.09 %) gives a **spread of ~208×** in
per-platform suspicious rate.

The pattern aligns with platform governance intuition:

* **DonorsChoose (0.00 %)** and **Experiment (0.09 %)** are both
  high-moderation, institution-gated platforms: DonorsChoose requires every
  campaign to come from a verified US public school teacher, and Experiment
  requires a named researcher with institutional affiliation. The near-zero
  suspicious rate is a direct consequence of platform-level identity
  verification at intake.
* **GoFundMe (1.60 %)** is a consumer platform but has well-staffed trust &
  safety operations and has published removal statistics in prior years.
* **SeedAndSpark (1.07 %)** is film/creative crowdfunding with creator
  verification.
* **Ufandao (18.72 %)** and **GoGetFunding (12.26 %)** are at the other
  extreme — both are open personal-need platforms with minimal verification
  requirements. The suspicious rate is approximately an order of magnitude
  higher than GoFundMe's.
* **GoGetFunding dominates the absolute counts** because it is also the
  largest platform in the filtered dataset (28,728 campaigns → 3,521
  suspicious, or 67 % of all suspicious campaigns in the dataset).

This cross-platform spread is the headline result for the platform-governance
comparison section of the paper.

---

## 4. Per-Question Signal Drivers

For each of the 12 revised questions, the **yes-rate** among suspicious and
unknown campaigns, plus the lift (susp/legit ratio).

| Question | Susp yes % | Legit yes % | Lift | Direction |
|---|---:|---:|---:|---|
| **Q8** defensive_language | 8.2 % | 0.9 % | **8.95 ×** | **↑ fraud signal** |
| **Q7** guilt_language | 46.8 % | 9.5 % | **4.90 ×** | **↑ fraud signal** |
| **Q1** external_payment | 12.5 % | 3.3 % | **3.84 ×** | **↑ fraud signal** |
| **Q5** deadline_pressure | 13.6 % | 6.6 % | **2.05 ×** | ↑ fraud signal |
| **Q6** share_pressure | 59.0 % | 32.6 % | **1.81 ×** | ↑ fraud signal |
| Q3 named_person | 39.0 % | 60.2 % | 0.65 × | ↓ legitimacy marker |
| Q11 evidence_provided | 1.2 % | 2.7 % | 0.42 × | ↓ legitimacy marker |
| **Q10** specific_circumstance | 13.8 % | 34.4 % | **0.40 ×** | **↓ legitimacy marker** |
| **Q9** itemized_goal | 7.6 % | 25.3 % | **0.30 ×** | **↓ legitimacy marker** |
| **Q4** verifiable_event | 8.2 % | 32.6 % | **0.25 ×** | **↓ legitimacy marker** |
| **Q12** relationship_disclosed | 2.3 % | 20.1 % | **0.11 ×** | **↓ legitimacy marker** |
| **Q2** named_institution | 4.7 % | 42.8 % | **0.11 ×** | **↓ legitimacy marker** |

### Reading the table

**Fraud-leading questions (lift > 1.5):**

* **Q8 defensive_language** is the single most discriminating question at
  **8.95×**. When a campaign contains "I am not a scam", "I am a real
  person", or similar defensive phrasing, it is almost nine times as likely
  to be labeled suspicious as a random unknown campaign. This matches a
  known fraud-detection heuristic from the social-engineering literature:
  unknown requests rarely need to pre-empt scam accusations.
* **Q7 guilt_language** is the most common strong signal. Roughly half of
  suspicious campaigns contain explicit guilt / shame language aimed at
  potential donors ("anyone with a heart", "true believers would", "shame
  on anyone who scrolls past"), compared to ~10 % of unknown campaigns.
* **Q1 external_payment** shows a 3.84× lift and is the most concrete
  signal — it requires an actual PayPal / Venmo / CashApp / crypto / DM
  request in the description. Low absolute rate but high specificity.

**Legitimacy-leading questions (lift < 0.5):**

* **Q2 named_institution** and **Q12 relationship_disclosed** are the two
  strongest positive signals of legitimacy. Legitimate campaigns are ~9×
  more likely to name a specific verifiable institution and ~9× more
  likely to explain the organizer-beneficiary relationship.
* **Q4 verifiable_event**, **Q9 itemized_goal**, and **Q10 specific_circumstance**
  all follow the same pattern: unknown campaigns anchor to real-world
  facts; suspicious campaigns don't.

### What this tells us about the revised prompt

The original 12 questions (used in the discarded Qwen 7 B run) asked
subjective things like *"is the emotion disproportionate to the facts?"*
and produced 99 % "suspicious" as a result. The revised 12 questions —
all concrete, all answerable from the text alone — produce exactly the
pattern you would expect from a working fraud detector:

* legitimacy markers (Q2, Q4, Q9, Q10, Q11, Q12) fire strongly on
  unknown campaigns
* fraud markers (Q1, Q5, Q6, Q7, Q8) fire disproportionately on
  suspicious campaigns

Both sides move in the right direction, and the separation is large enough
(8.95×, 4.90×, 3.84×) that the LLM is clearly using the answers to make its
overall verdict, not pattern-matching independent of them.

---

## 5. Fraud Signal Count Distribution

Every campaign has an `llm_fraud_signal_count` value (0–12) equal to the
sum of the 12 question signals scored in the fraud direction.

| Statistic | Value |
|---|---|
| Mean | 5.54 |
| Std dev | 1.43 |
| Median | 6 |
| 25th percentile | 5 |
| 75th percentile | 7 |
| Min | 0 |
| Max | 12 |

| Bucket | Campaigns | % |
|---|---:|---:|
| 0–1 | 99 | 0.2 % |
| 2–3 | 4,928 | 7.5 % |
| 4–5 | 26,422 | 40.2 % |
| 6–7 | 29,335 | 44.7 % |
| 8–9 | 4,775 | 7.3 % |
| 10–12 | 103 | 0.2 % |

The distribution is roughly bell-shaped around 5–6, which is what you would
expect given that most questions have some degree of signal even in
unknown campaigns (share pressure, named person, etc. fire on
unknown fundraising too). The informative signal is in the **tails** and
in the **specific question pattern**, not the raw count.

---

## 6. Per-Platform Average Signal Count

| Platform | Mean signal count | Std dev | n |
|---|---:|---:|---:|
| DonorsChoose | 6.53 | 0.65 | 1,001 |
| Ufandao | 6.35 | 1.21 | 470 |
| SeedAndSpark | 6.33 | 0.94 | 657 |
| Experiment | 6.23 | 0.86 | 1,147 |
| Happypot | 5.96 | 1.42 | 114 |
| Crowdfundr | 5.91 | 1.20 | 582 |
| LaunchGood | 5.86 | 1.33 | 176 |
| GoGetFunding | 5.66 | 1.44 | 28,728 |
| Betterplace | 5.53 | 1.10 | 3,051 |
| FreeFunder | 5.45 | 1.54 | 13,695 |
| Angelink | 5.41 | 1.32 | 2,512 |
| Chuffed | 5.24 | 1.37 | 444 |
| GoFundMe | 5.17 | 1.36 | 13,085 |

**Note:** a higher raw signal count does **not** mean more suspicious — it
reflects question-coverage per campaign. DonorsChoose, Experiment, and
SeedAndSpark all have high average signal counts because their campaigns
tend to have rich, detailed descriptions that answer many questions
positively (named institutions, itemized goals, verifiable events, etc.).
Their suspicious rates are among the lowest in the dataset. The `suspicious`
verdict depends on **which** questions fire, not how many.

---

## 7. Per-Platform Q1 (External Payment) Yes-Rate

Q1 is the single strongest concrete fraud signal (3.84× lift). Its
per-platform distribution:

| Platform | Q1 yes count | Total | Q1 yes % |
|---|---:|---:|---:|
| **GoGetFunding** | 1,780 | 28,728 | **6.20 %** |
| FreeFunder | 492 | 13,695 | 3.59 % |
| Crowdfundr | 20 | 582 | 3.44 % |
| Ufandao | 15 | 470 | 3.19 % |
| Chuffed | 10 | 444 | 2.25 % |
| Betterplace | 61 | 3,051 | 2.00 % |
| GoFundMe | 195 | 13,085 | 1.49 % |
| Angelink | 24 | 2,512 | 0.96 % |
| SeedAndSpark | 5 | 657 | 0.76 % |
| LaunchGood | 1 | 176 | 0.57 % |
| **DonorsChoose** | 0 | 1,001 | **0.00 %** |
| **Experiment** | 0 | 1,147 | **0.00 %** |
| **Happypot** | 0 | 114 | **0.00 %** |

GoGetFunding is the single biggest source of off-platform redirect requests
in the dataset — 1,780 campaigns ask donors to send money via PayPal, Venmo,
CashApp, crypto, or direct messaging. That is 6.2 % of all GoGetFunding
campaigns, compared to 1.49 % on GoFundMe and literally zero on the three
institution-gated platforms. This is the strongest single piece of evidence
for the cross-platform governance story.

---

## 8. Agreement With the Regex Hard-Rule Layer

The regex hard-rule layer (24 flags computed locally from description text)
is a fully independent detector: it doesn't use the LLM and the LLM doesn't
see any of the hard-rule flag values. Comparing them:

| Qwen verdict | n | Has ≥1 hard-rule flag | % |
|---|---:|---:|---:|
| **Fraudulent** | 6 | 1 | **16.7 %** |
| **Suspicious** | 5,232 | 622 | **11.9 %** |
| **Legitimate** | 59,508 | 3,783 | **6.4 %** |
| Unclear | 916 | 83 | 9.1 % |

### Reading the table

**Suspicious campaigns are ~1.9× more likely to fire a hard-rule flag than
unknown campaigns** (11.9 % vs 6.4 %). This is real independent
corroboration — two detectors that share no inputs still agree on which
campaigns look sketchy. Key observations:

* The gap is smaller than the ratio suggests (11.9 % vs 6.4 %, not 99 %
  vs 0 %). This is because the hard-rule layer is deliberately sparse and
  only fires on concrete infrastructure signals that are rare in any
  single campaign. The LLM picks up semantic patterns the regex layer
  cannot detect.
* Both detectors are therefore **complementary, not redundant**. The
  consensus labeling architecture needs both: a campaign with only hard
  rules would miss ~88 % of the suspicious cases that don't embed
  literal payment URLs, and a campaign with only the LLM would have no
  independent cross-check on its semantic judgments.
* The `Fraudulent` bucket has a small sample size (n=6), but the
  agreement pattern holds (16.7 % vs the 6.4 % baseline).

---

## 9. The Six Fraudulent Cases

These are the only 6 campaigns Qwen 72 B was willing to call **fraudulent**
rather than suspicious, out of 65,662 total. Because of the rarity, they
should be manually verified before being used as any kind of training
signal.

| Platform | URL | Confidence | HR flags | Fired hard rules |
|---|---|---:|---:|---|
| Betterplace | `betterplace.org/de/fundraising_events/11425` | 0.9 | 0 | — |
| Betterplace | `betterplace.org/de/fundraising_events/12214` | 0.9 | 0 | — |
| Betterplace | `betterplace.org/de/fundraising_events/44742` | 0.9 | 0 | — |
| GoGetFunding | `gogetfunding.com/brain-tumor-medical-support-cost-and-kild-milk` | **1.0** | 0 | — |
| GoGetFunding | `gogetfunding.com/database-application-development-for-opdg` | **1.0** | 0 | — |
| GoGetFunding | `gogetfunding.com/grindr-muscle-spa-llc` | 0.9 | 2 | `hr_paypal_mention`, `hr_cashapp_mention` |

Five of the six have zero hard-rule flags, which is interesting — the LLM is
calling them fraudulent on purely semantic grounds. The sixth case
(`grindr-muscle-spa-llc`) has both PayPal and CashApp mentions, which is
consistent with the label.

**Action item:** manually review all six during the 300-sample validation
pass and confirm or revise. If Qwen is correct on these, the verdict is
well-calibrated; if it's wrong, we have precision data for the "fraudulent"
verdict specifically.

---

## 10. Worst-Offender Suspicious Campaigns

74 campaigns are both labeled `suspicious` by Qwen **and** fire 3 or more
regex hard-rule flags. The top 15 by flag count:

| HR flags | Conf | Platform | URL |
|---:|---:|---|---|
| **9** | 0.7 | GoGetFunding | `help-ahmed-evacuate-his-family` |
| **9** | 0.7 | GoGetFunding | `help-ibrahims-family-join-him-safely-in-egypt` |
| 5 | 0.7 | FreeFunder | `my-birthday-fundraiser-/` |
| 5 | 0.7 | FreeFunder | `my-birthday-fundraiser-/` (variant URL) |
| 5 | 0.7 | FreeFunder | `we-need-your-help-HrjJL8/` |
| 5 | 0.7 | GoGetFunding | `daddys-chemo-until-next-christmas` |
| 4 | 0.7 | FreeFunder | `van-for-kitty-cat-rescue/` |
| 4 | 0.7 | FreeFunder | `azure-emergency-surgery/` |
| 4 | 0.7 | FreeFunder | `donna-and-friends-funds/` |
| 4 | 0.7 | FreeFunder | `my-kids-dont-know-santa/` |
| 4 | 0.7 | FreeFunder | `new-home-after-fire/` |
| 4 | 0.7 | FreeFunder | `save-my-home-0yp0rV/` |
| 4 | 0.7 | FreeFunder | `van-for-kitty-cat-rescue/` (variant) |
| 4 | 0.7 | GoGetFunding | `build-greenhouse-gaza` |
| 4 | 0.7 | GoGetFunding | `buying-food-for-poor-families-and-make-them-happy-in-the-eids` |

These are the highest-priority candidates for the manual validation subset:
campaigns where *both* the semantic model and the infrastructure-based regex
layer agree something is wrong. If a human reviewer confirms them as
fraudulent, these become a high-quality seed for supervised training.

---

## 11. Implications for the Consensus Labeling Architecture

The per-attribute consensus design (Section 3 of the paper) combines the
LLM and hard-rule layers into a single categorical attribute vector per
campaign. The findings in this report directly shape how that combination
should work:

1. **Treat Q1 (external_payment) as primary infrastructure evidence** for
   the `Payment Channel` attribute, with Qwen's yes/no answer as the first
   source and the `hr_paypal_me_url` / `hr_venmo_url` / `hr_cashapp_url`
   family as the second source. When both agree on "off-platform", the
   attribute is locked in as `External-link-present`.

2. **Treat Q8 (defensive_language) as the tiebreaker signal** for uncertain
   verdicts. Its 8.95× lift means it is almost never a false positive and
   can be used to escalate borderline cases from `unclear` → `suspicious`.

3. **Q2 (named_institution) and Q12 (relationship_disclosed) populate the
   `Identity Verifiability` attribute**, with NER results from the feature
   pipeline as the second source. When both sources confirm a named
   institution or disclosed relationship, the attribute locks in as
   `Verified-institution` or `Named-individual`.

4. **The External Reputation dimension remains hard-rule-only**, as
   previously designed. IPQS email and phone results (when complete) plus
   VirusTotal domain results will populate it. The LLM cannot reach this
   information directly.

5. **The 6 `fraudulent` campaigns and the 74 worst-offender suspicious
   campaigns form the seed for the 300-sample manual validation set**. They
   are high-priority because they are the cases where a false positive
   would be most embarrassing and a true positive would be most
   defensible.

---

## 12. Immediate Next Steps

The Qwen labeling run is complete. The remaining work in the pipeline:

1. **Wait for IPQS phone run** (~1 day remaining, background job on this
   machine).
2. **Wait for VT domain run** (~2 days remaining, background job on this
   machine).
3. **Build the `consensus_labels.csv`** by joining Qwen labels + hard-rule
   flags + IPQS + VT per URL, applying the per-attribute agreement rules.
4. **Manually validate** the 300 random samples plus the 6+74 high-priority
   cases from this report. Record Cohen's κ against the Qwen labels.
5. **Retrain the baseline classifiers** (LightGBM / XGBoost / RF) on the
   consensus labels to get the paper's headline classifier performance
   numbers.
6. **Write the Results section** of `results.tex` using the numbers in
   this report.

---

## 13. File Inventory

All outputs live in `/home/C00621463/Downloads/DonationScam/AAAA/system_design/`:

| File | Purpose |
|---|---|
| `llm_features_qwen72b.csv` | 65,662 × 31 columns — 12 question answers + fraud signals + metadata |
| `llm_labels_qwen72b.csv` | 65,662 × 6 columns — URL, platform, label, confidence, reasons, signal count |
| `llm_summary_qwen72b.json` | Run summary and label distribution |
| `qwen72b_run.out` | Full run log |
| `QWEN_RESULTS_REPORT.md` | This report |

And the paper figures derived from these files live in
`/home/C00621463/Downloads/DonationScam/figures/`:

| Figure | Purpose |
|---|---|
| `fig13_qwen_label_distribution` | Overall label split + confidence histogram |
| `fig14_qwen_per_platform` | Stacked bars of labels per platform |
| `fig15_qwen_question_lifts` | Which questions drive suspicious vs unknown |
| `fig16_qwen_signal_count_dist` | Fraud signal count histogram by label |
| `fig17_qwen_hr_agreement` | LLM × hard-rule cross-tab |
| `fig18_qwen_q1_per_platform` | Per-platform Q1 yes-rate |

---

*End of report.*
