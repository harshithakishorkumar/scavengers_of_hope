"""Detector dispatch + verdict assembly.

Two code paths:
  1. Cached -- URL is one of the ~100k campaigns already in mongo. We read
     the precomputed detector_A/B/C and consensus fields and assemble a
     response.
  2. Live  -- URL is new. scrape.py fetches the page; we re-run the cheap
     detectors (A regex + redirection patterns + identity-signal extraction)
     and return a partial verdict.

Verdict tiers (matches the paper's three-detector consensus rule):
  - "fraud"      : 2 or 3 detectors fired
  - "suspicious" : exactly 1 detector fired
  - "unknown"    : no detector fired, or URL is outside our analysis set
"""
import ast
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from scrape import scrape_and_score


# Suffixes / phrases in an organizer name that strongly suggest a registered
# org rather than a private individual.
ORG_NAME_PATTERNS = [
    (re.compile(r"\be\.?V\.?\b", re.I),                    "e.V. (German registered association)"),
    (re.compile(r"\bgGmbH\b", re.I),                       "gGmbH (German nonprofit)"),
    (re.compile(r"\b(Inc|Corp|Corporation|LLC|Ltd|Limited|PLC|Co\.|GmbH)\b\.?", re.I),
                                                            "corporate entity suffix"),
    (re.compile(r"\b(Foundation|Stiftung|Fondation|Fundación|Fundacja|Fonden)\b", re.I),
                                                            "foundation"),
    (re.compile(r"\b(Trust|Charity|Charitable)\b", re.I),  "charity / trust"),
    (re.compile(r"\b(Verein|Stichting|Association|Society|Asociación|Associazione|Institute|Institut)\b", re.I),
                                                            "association / society / institute"),
    (re.compile(r"\b(NGO|Non[\s-]?profit|Nonprofit)\b", re.I),
                                                            "NGO / nonprofit"),
    (re.compile(r"\b(Hospital|Clinic|Klinik|Hopital)\b", re.I), "hospital / clinic"),
    (re.compile(r"\b(University|Universität|College|School)\b", re.I), "educational institution"),
    (re.compile(r"\b(Church|Mosque|Temple|Synagogue|Parish|Kirche|Gemeinde)\b", re.I),
                                                            "religious institution"),
    (re.compile(r"501\s*\(c\)\s*\(?3\)?",   re.I),         "US 501(c)(3) reference"),
]


def detect_org_hint(organizer_field) -> str | None:
    """If the organizer name string suggests a registered organization,
    return a short note. Else return None."""
    if not organizer_field:
        return None
    name = str(organizer_field)
    # Handle dict-form organizer strings: {'name': 'Foo e.V.', ...}
    if name.startswith("{") and "'name'" in name or '"name"' in name:
        try:
            d = ast.literal_eval(name)
            if isinstance(d, dict) and d.get("name"):
                name = str(d["name"])
        except (SyntaxError, ValueError):
            m = re.search(r"['\"]name['\"]\s*:\s*['\"](.+?)['\"]\s*[,}]", name)
            if m:
                name = m.group(1)
    for pat, label in ORG_NAME_PATTERNS:
        if pat.search(name):
            return f"Organizer name suggests a registered organization ({label})."
    return None


# Query params that are *almost* always tracking noise and not part of the
# campaign identity. Strip them before looking up the URL in mongo.
TRACKING_PARAMS = {
    "src", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_", "referrer", "referer", "mc_cid", "mc_eid",
    "share", "shared", "via", "from",
}


def host_of(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h.removeprefix("www.")


def _www_variant(url: str) -> str | None:
    """The same URL with the www. prefix toggled, or None if it has no host.

    Platforms serve both forms and our corpus stores whichever the crawler saw
    (FreeFunder records are mostly bare-host), so a lookup that only tries the
    literal address the browser reports misses the cached label.
    """
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = p.netloc
    if not host:
        return None
    swapped = host[4:] if host.lower().startswith("www.") else "www." + host
    return urlunparse(p._replace(netloc=swapped))


def normalize_url(url: str) -> list[str]:
    """Produce candidate URL forms to try in the cache, in order of priority:
       1. exact URL
       2. URL with tracking params stripped
       3. URL with all query + fragment stripped
       4. URL with trailing slash toggled (no query)
       5. every form above with the www. prefix toggled
    """
    try:
        p = urlparse(url)
    except Exception:
        return [url]
    candidates = [url]
    # Strip just the tracking params
    if p.query:
        kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if k.lower() not in TRACKING_PARAMS]
        u_kept = urlunparse(p._replace(query=urlencode(kept), fragment=""))
        if u_kept != url:
            candidates.append(u_kept)
    # Strip query + fragment entirely
    u_bare = urlunparse(p._replace(query="", fragment=""))
    if u_bare not in candidates:
        candidates.append(u_bare)
    # Toggle trailing slash
    u_slash = u_bare + "/" if not u_bare.endswith("/") else u_bare.rstrip("/")
    if u_slash not in candidates:
        candidates.append(u_slash)
    # Toggle the www. prefix on every form built so far, keeping priority order
    for u in list(candidates):
        alt = _www_variant(u)
        if alt and alt not in candidates:
            candidates.append(alt)
    return candidates


def _detector_block(doc: dict, key: str, fired_key: str = "flagged",
                    extra_keys: list[str] | None = None) -> dict | None:
    block = doc.get(key)
    if not isinstance(block, dict):
        return None
    fired = bool(block.get(fired_key))
    out = {"fired": fired}
    if extra_keys:
        for k in extra_keys:
            if k in block:
                out[k] = block[k]
    return out


# Human-readable names for the organizer-identity (Detector C) match signals.
# The stored fired_signals use legacy "D.x_code" tags from an earlier build;
# translate them so the user-facing evidence stays consistent with "Detector C"
# and is readable (a donor should see "organizer name", not "D.1_name").
_SIGNAL_NAMES = {
    "name":        "organizer name",
    "email":       "email address",
    "phone":       "phone number",
    "handle":      "payment handle",
    "domain":      "website domain",
    "stylometric": "writing style",
    "profile":     "platform profile",
    "avatar":      "profile image",
}


def humanize_signals(raw) -> str:
    """Turn 'D.1_name;D.5_domain;D.8_stylometric' into
    'organizer name, website domain, writing style'."""
    if not raw:
        return ""
    seen, out = set(), []
    for tok in str(raw).split(";"):
        tok = tok.strip()
        if not tok:
            continue
        key = tok.split("_")[-1].lower() if "_" in tok else tok.lower()
        name = _SIGNAL_NAMES.get(key, key)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return ", ".join(out) if out else str(raw)


def _evidence_snippet(doc: dict, detector_key: str) -> str:
    block = doc.get(detector_key, {}) or {}
    if detector_key == "detector_A":
        reasons = []
        if int(block.get("vt_max_score", 0) or 0) >= 2:
            reasons.append(
                f"VirusTotal flagged an extracted domain "
                f"(score {block['vt_max_score']})")
        if int(block.get("ipqs_email_max", 0) or 0) >= 85:
            reasons.append(
                f"IPQualityScore flagged an organizer email "
                f"(fraud_score {block['ipqs_email_max']})")
        if int(block.get("ipqs_phone_max", 0) or 0) >= 85:
            reasons.append(
                f"IPQualityScore flagged an organizer phone "
                f"(fraud_score {block['ipqs_phone_max']})")
        if int(block.get("hr_pattern_count", 0) or 0) >= 2:
            reasons.append(
                f"{block['hr_pattern_count']} off-platform payment/redirect "
                f"patterns detected in the description")
        if block.get("redirection_flag"):
            reasons.append("Off-platform redirection language detected")
        if block.get("disposable_email"):
            reasons.append("Disposable-email provider used")
        if int(block.get("wallet_reuse_max", 0) or 0) >= 2:
            reasons.append(
                f"Payment handle reused across "
                f"{block['wallet_reuse_max']} campaigns")
        if block.get("has_young_domain"):
            reasons.append("Extracted domain registered in the last 30 days")
        if block.get("whois_privacy"):
            reasons.append("Extracted domain uses WHOIS privacy guard")
        if reasons:
            return "; ".join(reasons) + "."
        return "External reputation or heuristic signals fired."
    if detector_key == "detector_B":
        n = block.get("n_yes")
        if n is not None:
            return (f"LLM behavioral classifier: {n} of 10 manipulation "
                    f"questions answered yes.")
        return "LLM behavioral classifier flagged manipulation tactics."
    if detector_key == "detector_C":
        sz = block.get("cluster_size")
        pl = block.get("cluster_platforms")
        if sz and pl:
            others = max(int(sz) - 1, 1)
            plat_word = "platform" if pl == 1 else "platforms"
            msg = (f"Same organizer identity also appears on {others} other "
                   f"campaign(s) across {pl} {plat_word}.")
            signals = block.get("fired_signals")
            if signals:
                msg += f" Linked via {humanize_signals(signals)}."
            msg += (" Could be a charity running multiple campaigns or a "
                    "coordinated fraud ring; verify the campaigns look authentic.")
            return msg
        return "Same organizer identity appears on other campaigns."
    return ""


def _verdict_from_detectors(d_blocks: dict) -> str:
    fired = sum(
        1 for k in ("detector_A", "detector_B", "detector_C")
        if d_blocks.get(k, {}).get("fired")
    )
    if fired >= 2:
        return "fraud"
    if fired == 1:
        return "suspicious"
    return "unknown"


def fetch_cluster_siblings(db, doc: dict, max_siblings: int = 4) -> list[dict]:
    """For a C-flagged campaign, return up to N other campaigns in the same
    organizer-identity cluster (so the user can click through and judge)."""
    c = doc.get("detector_C") or {}
    cluster_id = c.get("cluster_id")
    own_url = doc.get("url")
    if cluster_id is None:
        return []
    cur = db.campaigns.find(
        {"detector_C.cluster_id": cluster_id,
         "url": {"$ne": own_url}},
        {"_id": 0, "url": 1, "platform": 1, "title": 1, "organizer": 1},
    ).limit(max_siblings + 4)
    out = []
    for s in cur:
        out.append({
            "url":      s.get("url"),
            "platform": s.get("platform"),
            "title":    (s.get("title") or "")[:100],
        })
        if len(out) >= max_siblings:
            break
    return out


def assemble_response(doc: dict, url: str, platform: str | None,
                      cached: bool, db=None) -> dict:
    """Turn a campaign mongo doc into an AnalyzeResponse-shaped dict."""
    d_blocks = {
        "detector_A": _detector_block(doc, "detector_A") or {"fired": False},
        "detector_B": _detector_block(doc, "detector_B") or {"fired": False},
        "detector_C": _detector_block(doc, "detector_C") or {"fired": False},
    }

    # Prefer the pre-computed consensus tier in mongo; recompute otherwise.
    consensus = doc.get("consensus") or {}
    verdict = consensus.get("tier") or _verdict_from_detectors(d_blocks)

    # Detector C carries the cluster details and sibling list.
    c_fired = d_blocks["detector_C"]["fired"]
    c_entry = {
        "name": "Organizer identity (C)",
        "fired": c_fired,
        "evidence": _evidence_snippet(doc, "detector_C") if c_fired else None,
    }
    if c_fired:
        c_block = doc.get("detector_C") or {}
        details = {
            "cluster_size":      c_block.get("cluster_size"),
            "cluster_platforms": c_block.get("cluster_platforms"),
            "matched_via":       humanize_signals(c_block.get("fired_signals")) or None,
        }
        org_hint = detect_org_hint(doc.get("organizer"))
        if org_hint:
            details["org_hint"] = org_hint
        if db is not None:
            siblings = fetch_cluster_siblings(db, doc)
            if siblings:
                details["siblings"] = siblings
        c_entry["details"] = details

    detectors = [
        {"name": "External signals (A)",
         "fired": d_blocks["detector_A"]["fired"],
         "evidence": _evidence_snippet(doc, "detector_A")
                     if d_blocks["detector_A"]["fired"] else None},
        {"name": "Behavioral LLM (B)",
         "fired": d_blocks["detector_B"]["fired"],
         "evidence": _evidence_snippet(doc, "detector_B")
                     if d_blocks["detector_B"]["fired"] else None},
        c_entry,
    ]

    summaries = {
        "fraud":      "Two or more independent fraud-shaped signals fired. "
                      "Treat with caution and verify offline before donating.",
        "suspicious": "One signal fired. Worth verifying offline before donating.",
        "unknown":    "No fraud-shaped signals detected, or this URL is not in "
                      "our analysis set. Absence of signal is not a guarantee.",
    }

    return {
        "url":       url,
        "verdict":   verdict,
        "cached":    cached,
        "platform":  platform,
        "detectors": detectors,
        "summary":   summaries.get(verdict, summaries["unknown"]),
    }


def analyze_url(db, url: str, supported_platforms: set[str]) -> dict:
    """Main dispatch: cached lookup, fall back to live scrape."""
    host = host_of(url)
    platform = None
    for p in supported_platforms:
        if host == p or host.endswith("." + p):
            platform = p
            break

    # 1. Cached lookup — try multiple URL normalizations
    for candidate in normalize_url(url):
        doc = db.campaigns.find_one({"url": candidate})
        if doc:
            return assemble_response(doc, url,
                                     platform=doc.get("platform"),
                                     cached=True, db=db)

    # 2. Prefix-regex lookup — handles the inverse case where the user has
    # the bare URL but mongo stored it WITH a tracking query. Strip query+
    # fragment, build a regex that allows an optional "?suffix" tail.
    try:
        p = urlparse(url)
        bare = urlunparse(p._replace(query="", fragment="")).rstrip("/")
        if bare:
            pattern = "^" + re.escape(bare) + r"/?(\?.*)?$"
            doc = db.campaigns.find_one({"url": {"$regex": pattern}})
            if doc:
                return assemble_response(doc, url,
                                         platform=doc.get("platform"),
                                         cached=True, db=db)
    except Exception:
        pass

    # 2. Unsupported platform -> bail
    if platform is None:
        return {
            "url":       url,
            "verdict":   "unknown",
            "cached":    False,
            "platform":  None,
            "detectors": [],
            "summary":   "This URL is not on a platform we currently support.",
        }

    # 3. Live scrape + detectors (uses cached VT/IPQS for any entities
    # extracted from the live page; no external API calls)
    try:
        synthetic_doc = scrape_and_score(url, platform, db=db)
    except Exception as e:
        return {
            "url":       url,
            "verdict":   "unknown",
            "cached":    False,
            "platform":  platform,
            "detectors": [],
            "summary":   f"Live analysis failed: {e}",
        }
    return assemble_response(synthetic_doc, url, platform=platform,
                             cached=False, db=db)
