# From the raw collection to the analysis corpus

Files in this folder for the filtering question:

| File | What it is |
|---|---|
| `raw_campaigns_17platforms_146997.csv.gz` | the collection before any filtering: 146,997 campaigns, 17 platforms, one row per URL (already de-duplicated by exact URL at collection time). Same 16 columns as the corpus. |
| `raw_dataset_summary.json` | per-platform counts of the raw file |
| `filtered_102708_prededupe_urls.csv.gz` | the URLs that survived filtering (102,708), with `added_in_v4_expansion` = whether the row came in at the April-19 expansion (True) or the original 100-word filter (False) |
| `filtered_100294_final_urls.csv.gz` | the URLs of the final corpus (100,294) after URL-variant dedupe |
| `corpus_rows_not_in_raw_29.csv.gz` | 29 Spotfund rows (April re-scrape, query-string URLs) that are in the corpus but were never folded back into the raw file; `--exact` appends them |
| `filtered_dataset.csv.gz` | the final corpus itself (already shared in August) |
| `filter_raw_to_corpus.py` | reproduces the corpus from the raw file, exactly (`--exact`) or by re-applying the rules (`--rule`) |
| `dedupe_url_variants.py` | the original dedupe step, unchanged (it rewrites pipeline files in place; use the copy of its logic in `filter_raw_to_corpus.py` unless you want that) |

## What was applied, in order

| Stage | Date | Rule | Rows |
|---|---|---|---|
| raw | Mar 24 | 17 platforms, exact-URL dedupe at collection time | 146,997 |
| + April Spotfund re-scrape rows missing from the raw file | Apr | 29 rows, shipped separately | 147,026 |
| 1. length filter (v1) | Apr 7 | `description_word_count >= 100` | 65,662 |
| 2. expansion (v2–v4) | Apr 19 | lower threshold on the 13 platforms whose pages carry full text: rows with 50–99 words; Spotfund handled separately (its pages carry a short story, median 30 words, and 23k of them hold only the site's template sentence) | +37,046 = 102,708 |
| 3. URL-variant dedupe | Apr 30 | `www.` / scheme / trailing-slash variants of the same campaign collapsed, keeping the most complete row (2,414 rows, almost all FreeFunder) | 100,294 |

Ulule (3,594) and Fairplaid (203) contribute nothing: their descriptions are client-rendered and the scrape kept at most 36 words, so every row fails the length rule. They are dropped as platforms, not per campaign.

There was never a language filter. Betterplace (German) and Happypot (French) rows are in the corpus. An older README in the pipeline says "English (langdetect)"; that is wrong and is corrected here.

## How exactly this can be reproduced

- Stage 1 is exact: `description_word_count >= 100` gives the 65,662 rows (16 rows in the raw file pass the rule but are not in the corpus; they were added to the raw file after the filter ran).
- Stage 3 is exact: `dedupe_url_variants.py` is the original script.
- Stage 2 is reconstructed. The scripts that produced v2, v3 and v4 were deleted in the April 2026 cleanup, so the rule is inferred from which rows were kept:
  - 13 platforms: `description_word_count >= 50` reproduces 10,787 of the 10,999 added rows; 14 rows pass but were not kept, 212 kept rows are below 50 words (down to 35). No single threshold on words or characters separates them, so those rows most likely came from an intermediate version with a slightly different rule.
  - Spotfund: "story page, not the template sentence, at least 150 characters" reproduces 25,972 of the 26,018 kept rows but also passes 4,930 rows that were not kept. Nothing in the data separates those 4,930 (same status mix, same source batch); the kept set most likely reflects which pages had been scraped when the filter ran.

So: `--rule` gives you the procedure as words and reproduces the corpus to within ~5%; `--exact` gives you the corpus row for row. For a thesis, describe stage 2 as "descriptions of at least 50 words on 13 platforms; Spotfund story pages with real text" and cite the URL list as the frozen selection.

```
python3 filter_raw_to_corpus.py --exact raw_campaigns_17platforms_146997.csv.gz corpus.csv
# raw: 146,997 -> after selection: 102,708 -> after dedupe: 100,294
```
