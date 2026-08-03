"""dedupe_url_variants.py — collapse duplicate URL variants across the entire pipeline.

Concrete examples we found:
  https://freefunder.com/campaign/got-scammed/   ←  same campaign, different URL
  https://www.freefunder.com/campaign/got-scammed/  ←  variant
The two rows have identical content (same raised, donors, description, title) but
different URL formatting. Treating them as separate campaigns inflates the corpus
size, double-counts fraud labels, and (worst) lets duplicate rows leak across the
LightGBM train/test split.

Normalization rule:
    lowercase, strip http(s)://, strip leading www., strip trailing /

Files this script rewrites in place (each backed up to *_PRE_DEDUPE.csv first):
    02_data_filtration/filtered_dataset_v4.csv                            (the corpus source)
    03_detection/detectors/outputs/hard_rule_flags_v4_plus.csv            (Detector A)
    03_detection/detectors/outputs/hard_rule_flags_v4.csv                 (Detector A, base)
    03_detection/detectors/outputs/phash_clusters_v4.csv                  (Detector C)
    03_detection/detectors/outputs/template_families_v4.csv               (Detector D, raw)
    03_detection/detectors/outputs/template_families_v4_filtered.csv      (Detector D, rigid)
    03_detection/detectors/outputs/organizer_network_v4_clean.csv         (Detector E)
    03_detection/llm/llm_labels_llama8b_ft_v4.csv                         (Detector B)

Strategy: keep one row per normalized URL — preferring the row with the most
non-null fields (so we don't lose data that happens to be in only one variant).
"""
from __future__ import annotations
import re
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent  # ccs2026/

FILES_TO_DEDUP = [
    ROOT / "02_data_filtration" / "filtered_dataset_v4.csv",
    ROOT / "03_detection" / "detectors" / "outputs" / "hard_rule_flags_v4_plus.csv",
    ROOT / "03_detection" / "detectors" / "outputs" / "hard_rule_flags_v4.csv",
    ROOT / "03_detection" / "detectors" / "outputs" / "phash_clusters_v4.csv",
    ROOT / "03_detection" / "detectors" / "outputs" / "template_families_v4.csv",
    ROOT / "03_detection" / "detectors" / "outputs" / "template_families_v4_filtered.csv",
    ROOT / "03_detection" / "detectors" / "outputs" / "organizer_network_v4_clean.csv",
    ROOT / "03_detection" / "llm" / "llm_labels_llama8b_ft_v4.csv",
]


def normalize_url(u: str) -> str:
    s = (u or "").lower().strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


def dedupe_csv(path: Path) -> tuple[int, int, int]:
    """Returns (raw_rows, kept_rows, dropped). Skips silently if no `url` column."""
    if not path.exists():
        print(f"  skip: {path.name} not found")
        return (0, 0, 0)
    df = pd.read_csv(path, low_memory=False)
    if "url" not in df.columns:
        print(f"  skip: {path.name} has no `url` column")
        return (len(df), len(df), 0)
    raw = len(df)
    df["__norm"] = df["url"].apply(normalize_url)
    # Score each row by count of non-null fields (excluding url, __norm)
    cols_for_score = [c for c in df.columns if c not in ("url", "__norm")]
    df["__score"] = df[cols_for_score].notna().sum(axis=1)
    # Sort: best score first, then deterministic tiebreak by URL string
    df = df.sort_values(["__score", "url"], ascending=[False, True])
    df = df.drop_duplicates(subset="__norm", keep="first")
    df = df.drop(columns=["__norm", "__score"])
    kept = len(df)

    # Backup original (only on first run; if a backup already exists we leave it)
    backup = path.with_name(path.stem + "_PRE_DEDUPE" + path.suffix)
    if not backup.exists():
        shutil.copy(path, backup)
    df.to_csv(path, index=False)
    return (raw, kept, raw - kept)


def main() -> None:
    print("=" * 70)
    print("URL-variant dedupe across the v4 pipeline")
    print("=" * 70)
    total_raw = total_kept = total_dropped = 0
    for p in FILES_TO_DEDUP:
        raw, kept, dropped = dedupe_csv(p)
        total_raw += raw
        total_kept += kept
        total_dropped += dropped
        if raw:
            pct = 100.0 * dropped / raw if raw else 0
            print(f"  {p.name:<48}  raw={raw:>7,}  kept={kept:>7,}  dropped={dropped:>5,} ({pct:>4.1f}%)")
    print(f"\nTotal across all files: dropped {total_dropped:,} duplicate rows")
    print(f"Backups written as *_PRE_DEDUPE.csv alongside each rewritten file.")


if __name__ == "__main__":
    main()
