"""Dump every campaign with social_handles into a JSONL the H100 bundle can read.

Each record carries the full HTML-stripped campaign description (capped at
DESC_CAP chars) so the LLM can read the whole campaign to disambiguate the
handle. Covers p90 description length cleanly.

Output: ccs2026_h100_social_disambig_bundle/social_handles_input.jsonl

One line per handle (NOT per campaign), schema:
  {
    "campaign_url": str,
    "handle_idx":   int,    # position in the campaign's social_handles array
    "raw":          str,    # original raw string from social_handles_raw
    "current": {
      "platform": "instagram" | ... | "unknown",
      "user_id":  str,
      "url":      str | null
    },
    "description": str      # full HTML-stripped description, capped at DESC_CAP
  }
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

OUT = Path(__file__).resolve().parent.parent.parent / "ccs2026_h100_social_disambig_bundle" / "social_handles_input.jsonl"

HTML_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
DESC_CAP = 6000  # covers p90 of description length (5.3k); truncates the long tail


def strip_html(s):
    return WS_RE.sub(" ", html.unescape(HTML_TAG_RE.sub(" ", s or ""))).strip()


def main():
    db = get_db()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n_campaigns = n_handles = 0
    with open(OUT, "w") as fout:
        cur = db.campaigns.find(
            {"llm_contacts.social_handles.0": {"$exists": True}},
            {"_id": 0, "url": 1, "description": 1,
             "llm_contacts.social_handles": 1,
             "llm_contacts.social_handles_raw": 1},
        )
        for doc in cur:
            n_campaigns += 1
            url = doc["url"]
            description = strip_html(doc.get("description") or "")[:DESC_CAP]
            sh = (doc.get("llm_contacts") or {}).get("social_handles") or []
            raw_list = (doc.get("llm_contacts") or {}).get("social_handles_raw") or []

            for i, entry in enumerate(sh):
                if not isinstance(entry, dict):
                    continue
                rec = {
                    "campaign_url": url,
                    "handle_idx":   i,
                    "raw":          (raw_list[i] if i < len(raw_list) else None),
                    "current": {
                        "platform": entry.get("platform"),
                        "user_id":  entry.get("user_id") or "",
                        "url":      entry.get("url"),
                    },
                    "description": description,
                }
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_handles += 1

    print(f"[done] {n_campaigns:,} campaigns -> {n_handles:,} handle entries")
    print(f"  -> {OUT}")
    print(f"  size: {OUT.stat().st_size / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
