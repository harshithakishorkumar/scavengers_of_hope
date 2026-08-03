"""Sync the 5-detector outputs back into mongo db.campaigns.

Replaces the OLD detector_A / detector_B / detector_C / detector_D embedded
fields (which were from the previous pipeline architecture) with the NEW
architecture's outputs, adds the missing detector_E field, and adds a
top-level `consensus` field with score + tier.

Reads:
  detectors/outputs/detector_A_flags.csv
  llm/llm_features_qwen72b.csv              (Detector B)
  detectors/outputs/detector_C_flags.csv
  detectors/outputs/detector_D_flags.csv
  detectors/outputs/detector_E_flags.csv
  intel/wallet_reuse_flags.csv              (for E.4 subfield)
  intel/whois_features.csv                  (for E.5/E.6 subfields)
  intel/email_quality_flags.csv             (for E.3 subfield)

Writes to mongo db.campaigns: bulk update_one() ops, one per URL.
Sets pipeline_version field to "2026-05-26" so we can track when the schema
changed.
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path

from pymongo import UpdateOne

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "db"))
from connection import get_db

DET_DIR = ROOT / "03_detection" / "detectors" / "outputs"
INTEL = ROOT / "03_detection" / "intel"
LLM_DIR = ROOT / "03_detection" / "llm"

PIPELINE_VERSION = "2026-05-26"
B_QCOLS = [
    "q1_external_payment", "q2_deadline_pressure", "q3_guilt_language",
    "q4_defensive_language", "q5_impersonation_no_consent",
    "q6_tragedy_exploitation", "q7_allocation_overpromise",
    "q8_fake_credential", "q9_external_verification", "q10_multi_channel",
]


def b_fire_rule(row) -> tuple[bool, int]:
    """Returns (flagged, n_yes). Same rule used in the consensus."""
    if str(row.get("ok")).lower() not in ("true", "1"):
        return False, 0
    yeses = [c for c in B_QCOLS if str(row.get(c, "")).lower() == "yes"]
    n = len(yeses)
    if n >= 4:
        return True, n
    side_channel = "q1_external_payment" in yeses and any(
        q in yeses for q in
        ("q5_impersonation_no_consent", "q7_allocation_overpromise",
         "q9_external_verification")
    )
    return side_channel, n


def load_csv_index(path: Path, key: str = "url") -> dict:
    out = {}
    if not path.exists():
        print(f"  [warn] {path.name} missing")
        return out
    with path.open() as fh:
        rdr = csv.DictReader(fh)
        for row in rdr:
            k = row.get(key)
            if k:
                out[k] = row
    return out


def main():
    db = get_db()
    n_total = db.campaigns.count_documents({})
    print(f"campaigns in db.campaigns: {n_total:,}")

    # Load all 5 detector outputs
    a_idx = load_csv_index(DET_DIR / "detector_A_flags.csv")
    c_idx = load_csv_index(DET_DIR / "detector_C_flags.csv")
    d_idx = load_csv_index(DET_DIR / "detector_D_flags.csv")
    e_idx = load_csv_index(DET_DIR / "detector_E_flags.csv")
    print(f"loaded A:{len(a_idx):,}  C:{len(c_idx):,}  "
          f"D:{len(d_idx):,}  E:{len(e_idx):,}")

    # Detector B (from llm_features_qwen72b.csv)
    b_idx = {}
    b_path = LLM_DIR / "llm_features_qwen72b.csv"
    if b_path.exists():
        with b_path.open() as fh:
            for row in csv.DictReader(fh):
                if row.get("url"):
                    b_idx[row["url"]] = row
    print(f"loaded B: {len(b_idx):,}")

    # E sub-signal helper indices
    eq_idx = load_csv_index(INTEL / "email_quality_flags.csv")
    wr_idx = load_csv_index(INTEL / "wallet_reuse_flags.csv")
    wf_idx = load_csv_index(INTEL / "whois_features.csv")

    # Build bulk updates
    ops = []
    n_skip = 0
    for url in db.campaigns.distinct("url"):
        a = a_idx.get(url) or {}
        b = b_idx.get(url) or {}
        c = c_idx.get(url) or {}
        d = d_idx.get(url) or {}
        e = e_idx.get(url) or {}

        if not (a or b or c or d or e):
            n_skip += 1
            continue

        b_flag, b_yes = b_fire_rule(b)

        # Build new detector subfields
        new_a = {
            "flagged": bool(int(a.get("flag", 0) or 0)),
            "vt_max_score": int(a.get("vt_max_score", 0) or 0),
            "ipqs_email_max": int(a.get("ipqs_email_max", 0) or 0),
            "ipqs_phone_max": int(a.get("ipqs_phone_max", 0) or 0),
            "reasons": a.get("reasons", "") or "",
        }
        new_b = {
            "flagged": bool(b_flag),
            "n_yes": int(b_yes),
            "ok": str(b.get("ok", "")).lower() in ("true", "1"),
        }
        for q in B_QCOLS:
            new_b[q] = (b.get(q) or "").lower() or "unclear"

        new_c = {
            "flagged": bool(int(c.get("flag", 0) or 0)),
            "narrative_cluster_id": (
                int(float(c["narrative_cluster_id"]))
                if c.get("narrative_cluster_id") and c["narrative_cluster_id"] != ""
                else None
            ),
            "narrative_cluster_size": int(c.get("narrative_cluster_size", 0) or 0),
            "shared_entities_count": int(c.get("shared_entities_count", 0) or 0),
        }
        new_d = {
            "flagged": bool(int(d.get("flag", 0) or 0)),
            "cluster_id": (
                int(float(d["cluster_id"]))
                if d.get("cluster_id") and d["cluster_id"] != ""
                else None
            ),
            "cluster_size": int(d.get("cluster_size", 0) or 0),
            "cluster_platforms": int(d.get("cluster_platforms", 0) or 0),
            "fired_signals": d.get("fired_signals", "") or "",
        }
        new_e = {
            "flagged": bool(int(e.get("flag", 0) or 0)),
            "hr_pattern_count": int(e.get("hr_pattern_count", 0) or 0),
            "redirection_flag": bool(int(e.get("redirection", 0) or 0)),
            "disposable_email": bool(int(e.get("disposable_email", 0) or 0)),
            "wallet_reuse_max": int(e.get("wallet_reuse_max", 0) or 0),
            "has_young_domain": bool(int(e.get("has_young_domain", 0) or 0)),
            "whois_privacy": bool(int(e.get("whois_privacy", 0) or 0)),
            "fired_signals": e.get("fired_signals", "") or "",
        }
        score = (
            int(new_a["flagged"]) + int(new_b["flagged"]) +
            int(new_c["flagged"]) + int(new_d["flagged"]) +
            int(new_e["flagged"])
        )
        tier = "fraud" if score >= 3 else ("suspicious" if score >= 1 else "unknown")
        consensus = {
            "score": score,
            "tier": tier,
            "detectors_fired": [
                name for name, det in
                [("A", new_a), ("B", new_b), ("C", new_c),
                 ("D", new_d), ("E", new_e)]
                if det["flagged"]
            ],
        }

        ops.append(UpdateOne(
            {"url": url},
            {"$set": {
                "detector_A": new_a,
                "detector_B": new_b,
                "detector_C": new_c,
                "detector_D": new_d,
                "detector_E": new_e,
                "consensus": consensus,
                "pipeline_version": PIPELINE_VERSION,
            }},
        ))

    print(f"prepared {len(ops):,} updates  (skipped {n_skip:,} URLs with no detector data)")
    if not ops:
        print("nothing to update"); return

    # Execute in batches
    BATCH = 5000
    n_modified = 0
    for i in range(0, len(ops), BATCH):
        batch = ops[i:i+BATCH]
        result = db.campaigns.bulk_write(batch, ordered=False)
        n_modified += result.modified_count
        print(f"  batch {i//BATCH + 1}/{(len(ops)-1)//BATCH + 1}: "
              f"modified {result.modified_count}, total so far {n_modified:,}",
              flush=True)

    print(f"\nfinal: modified {n_modified:,} of {len(ops):,} prepared updates")

    # Sanity check
    print("\n=== mongo state after update ===")
    for path, label in [
        ("detector_A.flagged", "A flagged"),
        ("detector_B.flagged", "B flagged"),
        ("detector_C.flagged", "C flagged"),
        ("detector_D.flagged", "D flagged"),
        ("detector_E.flagged", "E flagged"),
        ("consensus.tier", "any tier set"),
    ]:
        n = db.campaigns.count_documents({path: {"$exists": True}})
        flag_count = db.campaigns.count_documents({path: True}) if "flagged" in path else None
        if flag_count is not None:
            print(f"  {label:<18}  {flag_count:>6,}  (subfield present on {n:,})")
        else:
            print(f"  {label:<18}  exists on {n:,}")
    for tier in ("fraud", "suspicious", "unknown"):
        n = db.campaigns.count_documents({"consensus.tier": tier})
        print(f"  tier={tier:<12}  {n:,}")


if __name__ == "__main__":
    main()
