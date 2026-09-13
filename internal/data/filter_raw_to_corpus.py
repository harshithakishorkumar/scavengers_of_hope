#!/usr/bin/env python3
"""
Raw collection (146,997 campaigns, 17 platforms)  ->  analysis corpus (100,294).

Two ways to get there, see README_filtering.md for the history:

  --exact   join the raw file on the archived URL list of the filtered set
            (filtered_102708_prededupe_urls.csv.gz, plus the 29 Spotfund rows in
            corpus_rows_not_in_raw_29.csv.gz that the raw file lacks), then collapse URL variants
            exactly as dedupe_url_variants.py did. Reproduces the corpus row for
            row: 146,997 -> 102,708 -> 100,294.

  --rule    re-apply the selection rules as they can be reconstructed from the
            data (the v2/v3 scripts were deleted in an April 2026 cleanup):
              stage 1  description_word_count >= 100                 -> 65,662  (exact)
              stage 2  13 platforms, description_word_count >= 50     -> +10,787 (of 10,999; 14 extra)
              stage 3  Spotfund story pages that are not the site's
                       template text and have >= 150 characters       -> 30,902  (25,972 of the
                                                                          26,018 kept, plus 4,930
                                                                          that were not kept)
              Ulule and Fairplaid never pass (client-rendered pages, <= 36 words)
            then the same URL-variant dedupe.

Usage
  python3 filter_raw_to_corpus.py --exact  raw_campaigns_17platforms_146997.csv.gz  corpus.csv
  python3 filter_raw_to_corpus.py --rule   raw_campaigns_17platforms_146997.csv.gz  corpus_by_rule.csv
"""
import re, sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
URLS = HERE / "filtered_102708_prededupe_urls.csv.gz"
SPOTFUND_TEMPLATE = "*spotfund is the easiest place"


def normalize_url(u):
    """The dedupe key used by dedupe_url_variants.py."""
    s = str(u or "").lower().strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


def dedupe(df):
    """One row per normalized URL, keeping the row with the most non-null fields."""
    df = df.assign(_norm=df["url"].map(normalize_url), _score=df.notna().sum(axis=1))
    df = df.sort_values(["_score", "url"], ascending=[False, True]).drop_duplicates("_norm", keep="first")
    return df.drop(columns=["_norm", "_score"]).sort_index()


SUPPLEMENT = HERE / "corpus_rows_not_in_raw_29.csv.gz"   # 29 Spotfund rows from the April re-scrape
                                                          # that never made it back into the raw file


def select_exact(raw):
    keep = pd.read_csv(URLS)
    v1 = set(keep.loc[keep.added_in_v4_expansion == False, "url"])
    if SUPPLEMENT.exists():
        raw = pd.concat([raw, pd.read_csv(SUPPLEMENT, low_memory=False)], ignore_index=True)
    out = raw[raw.url.isin(set(keep.url))].copy()
    out["needs_detectors"] = ~out.url.isin(v1)      # True = added in the April-19 expansion
    return out


def select_by_rule(raw):
    wc = pd.to_numeric(raw.description_word_count, errors="coerce").fillna(0)
    desc = raw.description.fillna("")
    plat = raw.platform
    stage1 = wc >= 100
    thirteen = ~plat.isin(["Spotfund", "Ulule", "Fairplaid"])
    stage2 = thirteen & (wc >= 50) & ~stage1
    stage3 = ((plat == "Spotfund") & ~desc.str.startswith(SPOTFUND_TEMPLATE) & (desc.str.len() >= 150)
              & ~raw.url.str.contains("/blog/", na=False) & ~stage1)
    out = raw[stage1 | stage2 | stage3].copy()
    out["needs_detectors"] = ~stage1[out.index]
    return out


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("--exact", "--rule"):
        sys.exit(__doc__)
    mode, src, dst = sys.argv[1:]
    raw = pd.read_csv(src, low_memory=False)
    print(f"raw: {len(raw):,} rows, {raw.platform.nunique()} platforms")
    sel = select_exact(raw) if mode == "--exact" else select_by_rule(raw)
    print(f"after selection: {len(sel):,}")
    out = dedupe(sel)
    print(f"after URL-variant dedupe: {len(out):,}")
    out.to_csv(dst, index=False)
    print("per platform:", out.platform.value_counts().to_dict())


if __name__ == "__main__":
    main()
