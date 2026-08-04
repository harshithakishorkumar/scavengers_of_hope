# Tracking bundle: external channels of Fraud-tier campaigns

Every off-platform channel a **Fraud-tier** campaign exposes, joined to the
intel already held on it. Built to answer what can be measured outside the
crowdfunding platforms themselves.

## Scope

**Fraud tier only: the 429 campaigns where two or more independent detectors
agree.** Suspicious-tier campaigns are excluded. A Suspicious label means one
detector fired, which the paper does not treat as fraud, and a single-detector
signal is not a basis for investigating an identifiable person.

Even here the labels are research output, not confirmed fraud: 9.3% of Fraud
labels (40 of 429) rest on text agreement alone, and one platform's review has
already confirmed false positives in that stratum. Every value is a lead to
verify, never an accusation.

## Files

| File | Rows |
|---|---|
| `channels_unique.csv` | 609 distinct channels, ranked. **Start here.** |
| `channels_by_campaign.csv` | 873 channel-campaign pairs with the campaign's evidence |

Across 395 campaigns; the rest expose no external channel.

| Channel | Distinct |
|---|---|
| Domains | 207 |
| Social handles | 145 |
| Phone numbers | 107 |
| Payment identifiers | 94 |
| Email addresses | 56 |

## Start with `n_organizers`

The single most useful column. It counts how many **distinct organizer names**
use the same value. One account or phone number appearing under several
different names is one operator running multiple personas, which is the pattern
no individual platform can see and the paper's central finding.

**34 values have `n_organizers` > 1.** Sort by it descending and work down.
The strongest are an IBAN under three organizer names across four campaigns,
two phone numbers under three names each, and `paypal.me/msmiry` under two
names across eight campaigns.

Then: **149 values recur across more than one campaign**, 4 span more than one
platform, and **184 already carry** VirusTotal, IPQualityScore, or Chainabuse
results in the `intel` column.

## What to do per channel

**Payment identifiers (94).** PayPal.me and Cash App pages resolve publicly:
whether the account still exists, and what display name it shows, tests whether
the payee matches the campaign organizer. A mismatch, or one account serving
several organizer names, is the finding. BTC and ETH addresses are fully
traceable on-chain; Chainabuse returned no prior reports for any of them, so
clustering and exchange attribution are open ground. IBANs are not publicly
resolvable, but reuse across organizers still tells you something.

**Social handles (145).** Whether the account exists, its creation date, and
whether it fronts more than one campaign. Deleted or newly created accounts
around campaign launch are the signal.

**Domains (207).** Mostly unenriched beyond a VirusTotal verdict. WHOIS
registration age, registrar, hosting, and certificate history would show
whether infrastructure clusters across campaigns. Domains cited by more than
one organizer were removed as shared references rather than operator
infrastructure.

**Phones (107) and emails (56).** Reverse lookup and breach-corpus presence.
Reuse across organizer names is more informative than any single lookup.

## Columns

| Column | Meaning |
|---|---|
| `entity_type` | `paypal.me`, `Venmo`, `Cash App`, `IBAN`, `BTC address`, `ETH address`, `email`, `phone`, `domain`, `social:<platform>` |
| `entity` | the value |
| `n_campaigns`, `n_platforms`, `n_organizers` | how widely it recurs |
| `platforms`, `tiers`, `cross_platform` | where it appears |
| `intel` | VirusTotal engines, IPQS fraud score, or Chainabuse reports, where looked up |

`channels_by_campaign.csv` adds `campaign_url`, `consensus_score`,
`detectors_fired`, `organizer`, and `title`.

## Gates applied

Payment identifiers must match the strict formats the identity detector uses,
so bare words like "paypal" are excluded. Dropped: Cloudflare's
email-protection placeholder, generic platform domains, addresses on the
crowdfunding platforms' own domains, path fragments the extractor returns
instead of an account name, and domains cited by more than one organizer.
Identity values shared across organizers are deliberately kept, since that
sharing is the signal.

## Handling

Real payment accounts, emails, phone numbers, and social accounts belonging to
identifiable people, attached to probabilistic labels. Stays in this private
repository; not part of the anonymous artifact.
