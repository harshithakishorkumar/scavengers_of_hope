"""Refresh Social_Handles.xlsx with the current MongoDB state.

For every (campaign_url, raw_value) pair from social_handles_raw, decides
whether it was dropped or kept, and if kept, looks up the current typed
entry in social_handles to get final platform / user_id / url plus the
chain of actions that led to that state.

Output: ccs2026/03_detection/intel/Social_Handles.xlsx
"""
import sys
from pathlib import Path

import xlsxwriter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db
from migrate_social_handles import parse_social_handle

OUT = Path(__file__).resolve().parent.parent / "03_detection" / "intel" / "Social_Handles.xlsx"


def status_for(parse_drop, kept_entry, raw):
    """Return (status, drop_reason)."""
    if parse_drop:
        return "dropped", parse_drop
    if not kept_entry:
        return "dropped_or_replaced", "no_match_in_current"
    # was originally bare-@ -> if now has url, was disambiguated
    is_bare_at = isinstance(raw, str) and raw.strip().startswith("@")
    if is_bare_at:
        if kept_entry.get("platform") != "unknown":
            return "kept_disambiguated", ""
        return "kept_unknown", ""
    # had url, was reclassified if platform changed from initial parse
    return "kept", ""


def find_in_current(current, raw_user_id):
    if not raw_user_id:
        return None
    target = raw_user_id.lower()
    for e in current:
        if (e.get("user_id") or "").lower() == target:
            return e
    return None


def main():
    db = get_db()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    cur = db.campaigns.find(
        {"llm_contacts.social_handles_raw.0": {"$exists": True}},
        {"_id": 0, "url": 1, "platform": 1,
         "llm_contacts.social_handles": 1,
         "llm_contacts.social_handles_raw": 1},
    )

    for doc in cur:
        camp_url = doc["url"]
        camp_plat = doc.get("platform", "")
        cur_typed = (doc.get("llm_contacts") or {}).get("social_handles") or []
        raw_list  = (doc.get("llm_contacts") or {}).get("social_handles_raw") or []

        for raw in raw_list:
            init_plat, init_uid, init_url, drop = parse_social_handle(raw)
            kept = find_in_current(cur_typed, init_uid)
            status, drop_reason = status_for(drop, kept, raw)

            if status.startswith("kept") and kept:
                final_plat = kept.get("platform", "")
                final_uid  = kept.get("user_id", "")
                final_url  = kept.get("url") or ""
            else:
                final_plat = ""
                final_uid  = ""
                final_url  = ""

            rows.append([
                camp_url, camp_plat, raw, status, drop_reason,
                init_plat or "", final_plat, final_uid, final_url,
            ])

    print(f"rows to write: {len(rows):,}")

    wb = xlsxwriter.Workbook(str(OUT))
    ws = wb.add_worksheet("Social Handles")

    header_fmt = wb.add_format({"bold": True, "bg_color": "#1F4E78",
                                  "font_color": "white", "border": 1})
    drop_fmt   = wb.add_format({"bg_color": "#FCE4D6"})
    kept_fmt   = wb.add_format({"bg_color": "#E2EFDA"})
    unk_fmt    = wb.add_format({"bg_color": "#FFF2CC"})
    disamb_fmt = wb.add_format({"bg_color": "#D6DCE5"})

    headers = ["campaign_url", "campaign_platform", "raw", "status",
                "drop_reason", "initial_platform", "final_platform",
                "final_user_id", "final_url"]
    ws.write_row(0, 0, headers, header_fmt)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(rows), len(headers) - 1)

    for r, row in enumerate(rows, 1):
        status = row[3]
        if status == "dropped":
            fmt = drop_fmt
        elif status == "kept_disambiguated":
            fmt = disamb_fmt
        elif status == "kept_unknown":
            fmt = unk_fmt
        elif status.startswith("kept"):
            fmt = kept_fmt
        else:
            fmt = drop_fmt
        ws.write_row(r, 0, row, fmt)

    ws.set_column(0,  0, 60)  # campaign_url
    ws.set_column(1,  1, 14)  # campaign_platform
    ws.set_column(2,  2, 38)  # raw
    ws.set_column(3,  3, 22)  # status
    ws.set_column(4,  4, 28)  # drop_reason
    ws.set_column(5,  5, 16)  # initial_platform
    ws.set_column(6,  6, 14)  # final_platform
    ws.set_column(7,  7, 32)  # final_user_id
    ws.set_column(8,  8, 60)  # final_url

    summary = wb.add_worksheet("Summary")
    summary.write_row(0, 0, ["status", "n"], header_fmt)
    counts = {}
    for r in rows:
        counts[r[3]] = counts.get(r[3], 0) + 1
    for i, (s, n) in enumerate(sorted(counts.items(), key=lambda x: -x[1]), 1):
        summary.write_row(i, 0, [s, n])
    summary.set_column(0, 0, 28); summary.set_column(1, 1, 10)

    plat_counts = {}
    for r in rows:
        if r[3].startswith("kept") and r[6]:
            plat_counts[r[6]] = plat_counts.get(r[6], 0) + 1
    summary.write_row(len(counts) + 3, 0, ["final_platform", "n"], header_fmt)
    for i, (p, n) in enumerate(sorted(plat_counts.items(), key=lambda x: -x[1])):
        summary.write_row(len(counts) + 4 + i, 0, [p, n])

    wb.close()
    print(f"[wrote] {OUT}")
    print()
    print("status counts:")
    for s, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {s:24s} {n:>6,}")


if __name__ == "__main__":
    main()
