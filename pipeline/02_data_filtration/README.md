# 02 — Data Filtration

Quality filters applied to the raw collection to produce the analysis dataset.

## Filters applied

1. **Language:** English-language descriptions (langdetect)
2. **Length:** description ≥ 100 words
3. **Completeness:** non-empty title, description, organizer
4. **Dedupe:** by URL

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
