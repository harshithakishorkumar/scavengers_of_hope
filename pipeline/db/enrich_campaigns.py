"""Enrich every campaign doc with derived fields:

  description_lang        ISO 639-1 code (en, de, fr, es, ...) detected from text
  llm_contacts_summary    counts + has_X bool flags + payment-kind list
  hr_flags                16 hard-rule regex patterns from hard_rule_v4.py
  urls_typed              parallel array: [{url, domain, category, ...}]
  raised_to_goal_ratio    raised_amount / max(goal_amount, 1)
  days_active             (today - created_date) in days
  raised_per_day          raised_amount / max(days_active, 1)

Idempotent. Run with --dry-run to preview without writes.
"""
import argparse
import html
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pymongo import UpdateOne

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03_detection" / "detectors"))

from connection import get_db
from db_util import is_shared_infra, is_self_platform, SHARED_INFRA_DOMAINS
from hard_rule_v4 import HR_PATTERNS

from langdetect import detect, DetectorFactory, LangDetectException
DetectorFactory.seed = 0  # deterministic output

HTML_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
URL_HOST_RE = re.compile(r"^https?://", re.IGNORECASE)


def strip_html(s):
    s = HTML_TAG_RE.sub(" ", s or "")
    s = html.unescape(s)
    return WS_RE.sub(" ", s).strip()


def host_of(url):
    s = (url or "").strip().lower()
    s = URL_HOST_RE.sub("", s)
    s = re.sub(r"^www\.", "", s)
    return s.split("/")[0].split("?")[0].split("#")[0].rstrip(".")


# Coarse URL categorization tables.
NEWS_DOMAINS = frozenset({
    "nytimes.com", "bbc.com", "bbc.co.uk", "theguardian.com", "guardian.co.uk",
    "washingtonpost.com", "reuters.com", "ap.org", "apnews.com", "npr.org",
    "dw.com", "lemonde.fr", "spiegel.de", "faz.net", "sueddeutsche.de",
    "cnn.com", "bloomberg.com", "ft.com", "wsj.com", "vox.com",
    "theatlantic.com", "propublica.org", "axios.com", "semafor.com",
    "nbcnews.com", "cbsnews.com", "abcnews.go.com", "usatoday.com",
    "huffpost.com", "salon.com", "slate.com", "newyorker.com", "economist.com",
    "time.com", "newsweek.com", "forbes.com", "thehill.com",
})
CHARITY_REG_DOMAINS = frozenset({
    "guidestar.org", "charity-commission.gov.uk", "bbb.org",
    "charitynavigator.org", "candid.org",
    "givewell.org", "everyaction.com",
})
CODE_DOMAINS    = frozenset({"github.com", "gitlab.com", "bitbucket.org",
                              "sourceforge.net", "raw.githubusercontent.com"})
WIKI_DOMAINS    = frozenset({"wikipedia.org", "en.wikipedia.org", "de.wikipedia.org",
                              "fr.wikipedia.org", "es.wikipedia.org",
                              "it.wikipedia.org", "wikimedia.org"})
DOC_EXT_RE      = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|txt|csv)(?:$|\?)",
                              re.IGNORECASE)
VIDEO_DOMAINS   = frozenset({"youtube.com", "youtu.be", "vimeo.com", "twitch.tv",
                              "dailymotion.com", "rumble.com"})
PAYMENT_DOMAINS = frozenset({"paypal.me", "paypal.com", "venmo.com", "cash.app",
                              "wise.com", "patreon.com", "buymeacoffee.com",
                              "ko-fi.com", "donorbox.org", "givebutter.com"})


def categorize_url(u, campaign_platform=None):
    if not u:
        return None
    h = host_of(u)
    if not h or "." not in h:
        return None
    if is_self_platform(h):
        return ("self_platform", h)
    if DOC_EXT_RE.search(u):
        return ("document", h)
    parts = h.split(".")
    candidates = [".".join(parts[i:]) for i in range(len(parts))] + [h]
    if any(c in PAYMENT_DOMAINS for c in candidates):
        return ("payment", h)
    if any(c in VIDEO_DOMAINS for c in candidates):
        return ("video", h)
    if any(c in NEWS_DOMAINS for c in candidates):
        return ("news", h)
    if any(c in WIKI_DOMAINS for c in candidates):
        return ("wiki", h)
    if any(c in CODE_DOMAINS for c in candidates):
        return ("code", h)
    if any(c in CHARITY_REG_DOMAINS for c in candidates):
        return ("charity_registry", h)
    if is_shared_infra(h):
        return ("shared_infra", h)
    return ("external", h)


def detect_lang(text):
    if not text or len(text) < 10:
        return None
    try:
        return detect(text[:2000])
    except LangDetectException:
        return None


def _ensure_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(d):
    if d is None:
        return None
    if isinstance(d, datetime):
        return _ensure_utc(d)
    s = str(d).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except Exception:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S+00:00", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return _ensure_utc(datetime.strptime(s, fmt))
            except Exception:
                continue
    return None


def safe_float(v):
    try:
        f = float(v)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def enrich_doc(doc, today_utc):
    title = doc.get("title") or ""
    desc  = doc.get("description") or ""
    text_clean = strip_html(title + " " + desc)

    # description_lang
    lang = detect_lang(text_clean)

    # llm_contacts_summary
    c = doc.get("llm_contacts") or {}
    payments = c.get("payment_handles") or []
    socials  = c.get("social_handles") or []
    payment_kinds   = sorted({p.get("kind") for p in payments if isinstance(p, dict) and p.get("kind")})
    social_platforms = sorted({s.get("platform") for s in socials if isinstance(s, dict) and s.get("platform")})
    contacts_summary = {
        "n_emails":          len(c.get("emails")    or []),
        "n_phones":          len(c.get("phones")    or []),
        "n_urls":            len(c.get("urls")      or []),
        "n_social_handles":  len(socials),
        "n_payment_handles": len(payments),
        "n_names":           len(c.get("names")     or []),
        "n_locations":       len(c.get("locations") or []),
        "payment_kinds":     payment_kinds,
        "social_platforms":  social_platforms,
        "n_payment_kinds":   len(payment_kinds),
        "n_social_platforms":len(social_platforms),
        "has_email":         bool(c.get("emails")),
        "has_phone":         bool(c.get("phones")),
        "has_crypto":        bool({"btc", "eth"} & set(payment_kinds)),
        "has_paypal":        "paypal" in payment_kinds,
        "has_cashapp":       "cashapp" in payment_kinds,
        "has_venmo":         "venmo" in payment_kinds,
        "has_iban":          "iban" in payment_kinds,
        "n_payment_diversity": len(payment_kinds),
    }

    # hr_flags (apply 16 regex patterns)
    hr = {}
    n_hr = 0
    for name, pat in HR_PATTERNS:
        hit = bool(pat.search(text_clean))
        hr[name] = hit
        if hit:
            n_hr += 1
    hr["n_hr_hits"] = n_hr

    # urls_typed
    urls = c.get("urls") or []
    urls_typed = []
    cat_counts = Counter()
    for u in urls:
        cat = categorize_url(u)
        if cat is None:
            continue
        category, h = cat
        urls_typed.append({
            "url":              u,
            "domain":           h,
            "category":         category,
            "is_shared_infra":  is_shared_infra(h),
            "is_self_platform": is_self_platform(h),
        })
        cat_counts[category] += 1

    # derived metrics
    raised = safe_float(doc.get("raised_amount"))
    goal   = safe_float(doc.get("goal_amount"))
    ratio  = (raised / goal) if (raised is not None and goal and goal > 0) else None

    created = parse_date(doc.get("created_date"))
    days = None
    if created is not None:
        days = max(0, (today_utc - created).days)
    rpd = (raised / max(days, 1)) if (raised is not None and days is not None) else None

    return {
        "description_lang":     lang,
        "llm_contacts_summary": contacts_summary,
        "hr_flags":             hr,
        "llm_contacts.urls_typed": urls_typed,
        "urls_category_counts": dict(cat_counts),
        "raised_to_goal_ratio": ratio,
        "days_active":          days,
        "raised_per_day":       rpd,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    db = get_db()
    today = datetime.now(timezone.utc)

    cur = db.campaigns.find({}, {
        "_id": 1, "url": 1, "title": 1, "description": 1,
        "raised_amount": 1, "goal_amount": 1, "created_date": 1,
        "llm_contacts": 1, "platform": 1,
    })
    if args.limit:
        cur = cur.limit(args.limit)

    n = 0
    n_with_lang = 0
    lang_counts = Counter()
    cat_totals = Counter()
    hr_total = Counter()
    has_x_total = Counter()
    ops = []

    for doc in cur:
        enriched = enrich_doc(doc, today)
        n += 1

        if enriched["description_lang"]:
            n_with_lang += 1
            lang_counts[enriched["description_lang"]] += 1
        for c, k in enriched["urls_category_counts"].items():
            cat_totals[c] += k
        for hr_name, hit in enriched["hr_flags"].items():
            if hr_name == "n_hr_hits":
                continue
            if hit:
                hr_total[hr_name] += 1
        s = enriched["llm_contacts_summary"]
        for k, v in s.items():
            if k.startswith("has_") and v:
                has_x_total[k] += 1

        if not args.dry_run:
            # Note: llm_contacts_summary and urls_category_counts intentionally
            # NOT written here. Both were dropped 2026-05-08 as redundant
            # (summary stale relative to v6 organizer/third_party split;
            # urls_category_counts empty on 92% of docs). Stats still
            # computed in-memory above for the run-time totals printout.
            sets = {
                "description_lang":         enriched["description_lang"],
                "hr_flags":                 enriched["hr_flags"],
                "llm_contacts.urls_typed":  enriched["llm_contacts.urls_typed"],
                "raised_to_goal_ratio":     enriched["raised_to_goal_ratio"],
                "days_active":              enriched["days_active"],
                "raised_per_day":           enriched["raised_per_day"],
            }
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": sets}))
            if len(ops) >= 500:
                db.campaigns.bulk_write(ops, ordered=False); ops = []
        if n % 5000 == 0:
            print(f"  {n:,} processed")

    if ops and not args.dry_run:
        db.campaigns.bulk_write(ops, ordered=False)

    # Mirror the same enrichment onto llm_contacts collection where it makes sense
    if not args.dry_run:
        # urls_typed mirror so direct queries on llm_contacts work too
        pass

    print()
    print(f"[done] enriched {n:,} campaigns")
    print()
    print(f"description_lang detected on {n_with_lang:,} campaigns; top languages:")
    for lang, k in lang_counts.most_common(10):
        print(f"  {lang:6s} {k:>7,} ({100*k/max(n_with_lang,1):.1f}%)")
    print()
    print("urls_typed by category:")
    for cat, k in cat_totals.most_common():
        print(f"  {cat:20s} {k:>7,}")
    print()
    print("hr_flags hit rates (top 8):")
    for name, k in hr_total.most_common(8):
        print(f"  {name:24s} {k:>6,} ({100*k/n:.1f}%)")
    print()
    print("contact has-X flags:")
    for name, k in has_x_total.most_common():
        print(f"  {name:18s} {k:>7,} ({100*k/n:.1f}%)")
    if args.dry_run:
        print("\n[dry-run] no DB writes")


if __name__ == "__main__":
    main()
