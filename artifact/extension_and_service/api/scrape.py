"""Live-scrape path for URLs not in our 100k corpus.

Fetches the campaign page, extracts the description text + organizer-shape
entities (emails, domains), and runs:
  - Detector A regex (canonical hard-rule patterns + redirection phrases)
  - Cached VT lookup against any extracted domains
  - Cached IPQS lookup against any extracted emails

External API calls are NEVER made on the live path; we only consult our
already-collected results in db.{vt_results, ipqs_email_results}. This keeps
the path fast (sub-second) and free of quota burn, at the cost of "we only
know about entities the corpus already touched."

Detector B (GPU) and C/D (corpus-context) are skipped on live URLs.
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Reuse the curated shared-infra skiplist used in the main pipeline so we
# don't VT-query forms.gle / facebook.com / etc.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "db"))
from db_util import is_shared_infra  # noqa: E402


USER_AGENT = (
    "ScavengersOfHope/0.1 (academic fraud-detection research; "
    "contact: tpadamjung@gmail.com)"
)

# ---------- regex sets ----------
CANONICAL_PHRASES = [
    r"\bwire\s+transfer\b",
    r"\bwestern\s+union\b",
    r"\bmoneygram\b",
    r"\bbitcoin\s+wallet\b",
    r"\b(?:btc|eth|usdt)\s+address\b",
    r"\bIBAN\b",
]
REDIRECTION_PHRASES = [
    r"\bdm\s+me\b",
    r"\bsend\s+(?:me\s+)?a\s+message\b",
    r"\bcontact\s+me\s+(?:directly|privately)\b",
    r"\bemail\s+me\s+(?:directly|privately)\b",
    r"\bwhatsapp\s+me\b",
    r"\btelegram\s+me\b",
    r"\boff[\s-]platform\b",
]
EMAIL_RE  = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# The crowdfunding platforms themselves. Their domains show up on every page
# (nav, footer, share links) and are NEVER organizer identity, so they must be
# excluded from extracted "organizer domains" — otherwise e.g. gofundme.com
# leaks in and matches any flagged cluster that merely mentioned a GoFundMe.
PLATFORM_DOMAINS = {
    "angelink.com", "betterplace.org", "chuffed.org", "crowdfundr.com",
    "donorschoose.org", "experiment.com", "freefunder.com", "gofundme.com",
    "gofundme.org", "gogetfunding.com", "happypot.ch", "launchgood.com",
    "ufandao.com", "my.ufandao.com", "seedandspark.com", "spotfund.com",
    "whydonate.com",
}


def _is_platform_domain(host: str) -> bool:
    return any(host == p or host.endswith("." + p) for p in PLATFORM_DOMAINS)

# Per-platform profile-URL patterns. When we find one of these in the live
# page HTML, we have a strong identity hook against the corpus.
PROFILE_URL_PATTERNS = [
    re.compile(r"(https?://(?:www\.)?launchgood\.com/user/newprofile#?!?/user-profile/profile/[\w.\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?betterplace\.org/[a-z]{2}/users/[\w\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?experiment\.com/users/[\w\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?seedandspark\.com/profiles/[\w\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?crowdfundr\.com/profile/[\w\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?ufandao\.com/[a-z]{2}/users/[\w\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?gogetfunding\.com/profile/[\w\-]+)", re.I),
    re.compile(r"(https?://(?:www\.)?chuffed\.org/profile/[\w\-]+)", re.I),
]


# ---------- fetch + parse ----------
def fetch_html(url: str, timeout: int = 12) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_description(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return og["content"]
    main = soup.find("main") or soup.find("article") or soup.body
    if not main:
        return ""
    return main.get_text(" ", strip=True)


def _canonical_email(e: str) -> str:
    """Lower + Gmail-dot-trick canonicalization to match identity_signals.email_norms."""
    e = (e or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.split("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def extract_entities(html: str, desc: str) -> tuple[set[str], set[str]]:
    """Return (emails, domains) — both post-skiplist, lowercased.

    Emails are canonicalized via Gmail-dot-trick so they match the
    identity_signals.email_norms field stored by the main pipeline.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Emails: scan body text (LLM-cached counterpart did the same)
    body_text = soup.get_text(" ", strip=True) if soup else desc
    emails_raw = set(m.lower() for m in EMAIL_RE.findall(body_text))
    emails = {
        _canonical_email(e) for e in emails_raw
        if "@" in e and not is_shared_infra(e.split("@", 1)[1])
    }

    # Domains: pull from <a href> attributes
    domains = set()
    for a in soup.find_all("a", href=True):
        h = a.get("href") or ""
        if not h.startswith("http"):
            continue
        try:
            host = (urlparse(h).hostname or "").lower().removeprefix("www.")
        except Exception:
            continue
        if not host or "." not in host:
            continue
        if is_shared_infra(host) or _is_platform_domain(host):
            continue
        domains.add(host)

    return emails, domains


def extract_profile_url(html: str) -> str | None:
    """Try each platform-specific profile-URL regex against the raw HTML."""
    for pat in PROFILE_URL_PATTERNS:
        m = pat.search(html)
        if m:
            return m.group(1).lower().rstrip("/")
    return None


# ---------- detector aggregations ----------
def detector_A_regex(text: str) -> dict:
    canon_hits = sum(1 for p in CANONICAL_PHRASES
                     if re.search(p, text, re.IGNORECASE))
    return {"hr_strong": canon_hits >= 1, "regex_strength": canon_hits}


def detector_redirection(text: str) -> dict:
    hits = [p for p in REDIRECTION_PHRASES
            if re.search(p, text, re.IGNORECASE)]
    return {"flagged": bool(hits), "phrases": [{"phrase": h} for h in hits]}


def lookup_c_cluster(db, emails: set[str], domains: set[str],
                     profile_url: str | None) -> dict | None:
    """Find an existing flagged organizer-identity cluster (Detector C) that
    shares a reliable identity signal with this fresh URL.

    Only the platform-specific organizer profile URL and the organizer email
    are used. Page-link domains are intentionally NOT matched: they are
    harvested from arbitrary <a href> tags (navigation, social, external
    references), not organizer identity, and the corpus-side
    llm_contacts.organizer.domains field is itself polluted with platform,
    social, and email-provider domains. Matching on them fired C on nearly
    every campaign (e.g. every GoFundMe page matched via gofundme.com).

    Returns the cluster's detector_C block from the first matching campaign,
    along with which signal hit. None if no signal matches a flagged cluster.
    """
    if profile_url:
        hit = db.campaigns.find_one(
            {"organizer": {"$regex": re.escape(profile_url), "$options": "i"},
             "detector_C.flagged": True},
            {"_id": 0, "detector_C": 1, "url": 1, "platform": 1},
        )
        if hit:
            return {"matched_signal": "profile_url",
                    "matched_value":  profile_url,
                    "detector_C":     hit["detector_C"],
                    "match_url":      hit.get("url"),
                    "match_platform": hit.get("platform")}

    for e in emails:
        if not e:
            continue
        hit = db.campaigns.find_one(
            {"identity_signals.email_norms": e,
             "detector_C.flagged": True},
            {"_id": 0, "detector_C": 1, "url": 1, "platform": 1},
        )
        if hit:
            return {"matched_signal": "email",
                    "matched_value":  e,
                    "detector_C":     hit["detector_C"],
                    "match_url":      hit.get("url"),
                    "match_platform": hit.get("platform")}

    return None


def cached_reputation(db, emails: set[str], domains: set[str],
                      enable_live: bool = True) -> dict:
    """Look up emails/domains in our existing VT/IPQS results.

    If enable_live is True (default), on a cache miss for the first
    LIVE_PER_REQUEST entities we'll make a live VT/IPQS call (subject to the
    daily caps in live_reputation.py). Results from live calls are written
    back to the cache, so the *next* request for the same entity is served
    from mongo with no external call.

    Returns counts shaped the same way reputation.summary is in the main
    corpus, so the response assembler can consume it identically.
    """
    # First pass: cache hits
    domain_results = {}
    email_results  = {}
    if domains:
        for r in db.vt_results.find(
            {"domain": {"$in": list(domains)}},
            {"_id": 0, "domain": 1, "vt_malicious": 1, "vt_suspicious": 1,
             "vt_status": 1},
        ):
            domain_results[r["domain"]] = r
    if emails:
        for r in db.ipqs_email_results.find(
            {"email": {"$in": list(emails)}},
            {"_id": 0, "email": 1, "result.fraud_score": 1},
        ):
            email_results[r["email"]] = r

    # Second pass: live lookups for cache misses (rate-limited)
    if enable_live:
        from live_reputation import (
            lookup_domain_live, lookup_email_live, LIVE_PER_REQUEST,
        )
        budget = LIVE_PER_REQUEST
        for d in domains:
            if budget <= 0:
                break
            if d in domain_results:
                continue
            res = lookup_domain_live(db, d)
            budget -= 1
            if res:
                domain_results[d] = res
        for e in emails:
            if budget <= 0:
                break
            if e in email_results:
                continue
            res = lookup_email_live(db, e)
            budget -= 1
            if res:
                email_results[e] = res

    n_dom_checked = len(domain_results)
    n_dom_flagged = sum(
        1 for r in domain_results.values()
        if (r.get("vt_malicious") or 0) + (r.get("vt_suspicious") or 0) >= 1
    )
    n_em_checked = len(email_results)
    n_em_flagged = sum(
        1 for r in email_results.values()
        if ((r.get("result") or {}).get("fraud_score") or 0) >= 85
    )

    return {
        "any_vt_flagged":            n_dom_flagged > 0,
        "any_ipqs_email_flagged":    n_em_flagged > 0,
        "any_ipqs_phone_flagged":    False,    # phones skipped on live path
        "n_domains_checked":         n_dom_checked,
        "n_domains_flagged":         n_dom_flagged,
        "n_emails_checked":          n_em_checked,
        "n_emails_flagged":          n_em_flagged,
        "n_phones_checked":          0,
        "n_phones_flagged":          0,
    }


# ---------- main entrypoint ----------
def scrape_and_score(url: str, platform: str, db=None) -> dict:
    """Fetch + analyze a single URL. Returns a synthetic campaign-doc shape
    that the response assembler can consume identically to a cached doc."""
    html = fetch_html(url)
    desc = extract_description(html)

    a_regex     = detector_A_regex(desc)
    redirection = detector_redirection(desc)

    rep_summary = {
        "any_vt_flagged":            False,
        "any_ipqs_email_flagged":    False,
        "n_domains_flagged":         0,
        "n_emails_flagged":          0,
    }
    c_block = {"flagged": False}
    emails, domains = set(), set()
    if db is not None:
        emails, domains = extract_entities(html, desc)
        profile_url     = extract_profile_url(html)
        rep_summary     = cached_reputation(db, emails, domains)
        c_hit = lookup_c_cluster(db, emails, domains, profile_url)
        if c_hit:
            c_block = {
                **c_hit["detector_C"],
                "matched_signal": c_hit["matched_signal"],
                "match_url":      c_hit.get("match_url"),
                "match_platform": c_hit.get("match_platform"),
            }

    vt_score      = 2 if rep_summary.get("any_vt_flagged") else 0
    ipqs_email    = 90 if rep_summary.get("any_ipqs_email_flagged") else 0
    hr_count      = int(a_regex.get("regex_strength") or 0)
    redirect_flag = bool(redirection.get("flagged"))

    a_flagged = (vt_score >= 2 or ipqs_email >= 85 or hr_count >= 2 or redirect_flag)

    return {
        "url":         url,
        "platform":    platform,
        "description": desc[:2000],
        "detector_A":  {
            "flagged":           a_flagged,
            "vt_max_score":      vt_score,
            "ipqs_email_max":    ipqs_email,
            "ipqs_phone_max":    0,
            "hr_pattern_count":  hr_count,
            "redirection_flag":  redirect_flag,
            "disposable_email":  False,
            "wallet_reuse_max":  0,
            "has_young_domain":  False,
            "whois_privacy":     False,
        },
        "detector_B":   {"flagged": False},
        "detector_C":   c_block,
    }
