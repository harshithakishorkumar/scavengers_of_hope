# 02 — Data Filtration

Quality filters applied to the raw collection to produce the analysis dataset.

## Filters applied

Corrected 2026-09-13 after checking the file against the raw collection (the
earlier text here claimed an English-only langdetect filter and a 100-word
rule for every row; neither matches the file).

1. **v1 (Apr 7):** `description_word_count >= 100` on the raw collection -> 65,662 rows (exact).
2. **Expansion (Apr 19, v2-v4):** rows with 50-99 words on the 13 platforms whose
   pages carry full text (+10,999), and Spotfund story pages with real text
   (not the site's template sentence, >= ~150 characters; +26,047) -> 102,708.
   The v2/v3/v4 scripts were deleted in the April cleanup; the rule is
   reconstructed and reproduces the added rows to within ~5%. The exact
   selection is frozen as a URL list (see
   `export_scavengers_of_hope/internal/data/filter_raw_to_corpus.py --exact`).
3. **Dedupe (Apr 30):** URL variants (`www.`, scheme, trailing slash) collapsed by
   `dedupe_url_variants.py` -> 100,294.

There is no language filter: Betterplace (German) and Happypot (French) are in.
Ulule and Fairplaid drop out entirely (client-rendered pages, <= 36 words).

## Files

| File | Size | Campaigns |
|---|---|---|
| `filtered_dataset_v4.csv` | 173 MB | **102,708** (current) |

### Column schema

`platform, url, title, description, campaign_status, raised_amount, goal_amount, donors_count, organizer, organizer_url, campaign_url, currency, start_date, end_date, scrape_timestamp, image_urls, location`

## Version history

- v1 (Apr 7): 65,662 campaigns — initial filter on 13 platforms
- v2 (Apr 19): 95k campaigns — added 2 platforms
- v3 (Apr 19): 98k campaigns — refined filters
- **v4 (Apr 19): 102,708 campaigns (post-Spotfund expansion)**
- v4 after URL-variant deduplication: **100,294 campaigns across 15 platforms, the analysis set the paper reports**

v1, v2, v3 deleted in the Apr 2026 cleanup; only v4 is retained.

## Downstream

All detectors in `03_detection/` consume `filtered_dataset_v4.csv`.
