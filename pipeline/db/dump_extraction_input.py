"""Dump every campaign with a non-empty description into a JSONL the H100
extraction bundle can read.

Schema (one line per campaign):
  {
    "url":         str,    # campaign URL (key for merge later)
    "platform":    str,    # crowdfunding platform (GoFundMe, Betterplace, ...)
    "description": str,    # HTML-stripped, capped at DESC_CAP chars
  }

Output: ccs2026_h100_extraction_bundle/extraction_input.jsonl
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db

OUT = Path(__file__).resolve().parent.parent.parent / "ccs2026_h100_extraction_bundle" / "extraction_input.jsonl"

HTML_TAG_RE = re.compile(r"<[^>]+>")
HREF_RE     = re.compile(r'<a\s+[^>]*href="(https?://[^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
MAILTO_RE   = re.compile(r'<a\s+[^>]*href="mailto:([^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
TEL_RE      = re.compile(r'<a\s+[^>]*href="tel:([^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
WS_RE       = re.compile(r"\s+")
DESC_CAP    = 6000


def strip_html(s):
    """Strip HTML but preserve href / mailto / tel URLs inline so the LLM
    can still extract them. Transforms:
        <a href="https://x.com/y">Anna</a> -> Anna [https://x.com/y]
        <a href="mailto:x@y">contact</a>   -> contact [x@y]
        <a href="tel:+1...">call</a>       -> call [+1...]
    """
    if not s:
        return ""
    s = HREF_RE.sub(  lambda m: f"{m.group(2)} [{m.group(1)}]", s)
    s = MAILTO_RE.sub(lambda m: f"{m.group(2)} [{m.group(1)}]", s)
    s = TEL_RE.sub(   lambda m: f"{m.group(2)} [{m.group(1)}]", s)
    return WS_RE.sub(" ", html.unescape(HTML_TAG_RE.sub(" ", s))).strip()


def main():
    db = get_db()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    with open(OUT, "w") as fout:
        cur = db.campaigns.find(
            {"description": {"$ne": None, "$ne": ""}},
            {"_id": 0, "url": 1, "platform": 1, "description": 1},
        )
        for doc in cur:
            url = doc.get("url")
            if not url:
                continue
            desc = strip_html(doc.get("description") or "")[:DESC_CAP]
            if not desc:
                continue
            fout.write(json.dumps({
                "url":         url,
                "platform":    doc.get("platform") or "",
                "description": desc,
            }, ensure_ascii=False) + "\n")
            n += 1

    sz = OUT.stat().st_size
    print(f"[done] {n:,} campaigns -> {OUT}")
    print(f"  size: {sz / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
