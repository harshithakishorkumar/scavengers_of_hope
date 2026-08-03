"""Apply all H100 LLM extraction outputs to MongoDB campaigns.

Reads:
  ccs2026_h100_extraction_bundle/extraction_results.jsonl
       per-campaign organizer/third_party {emails, phones, domains, payment_handles}
       (payment_handles came back empty in this pass)
  ccs2026_h100_post_extraction_bundle/payment_handles_results.jsonl
       targeted re-extraction for payment_handles only
  ccs2026_h100_post_extraction_bundle/redirection_results.jsonl
       per-campaign off-platform redirection phrases + channels

Writes (per campaign):
  llm_contacts.organizer.{emails, phones, domains, payment_handles}
  llm_contacts.third_party.{emails, phones, domains, payment_handles}
  llm_contacts.dropped.{organizer, third_party}                    # paper-time audit
  llm_contacts.extraction_metadata                                  # provenance
  redirection.flagged          (bool, AFTER filtering boilerplate)
  redirection.phrases          (filtered list)
  redirection.dropped_phrases  (boilerplate that we filtered)
  identity_signals             (canonicalized name/email/phone/payment/domain)
  cross_platform_links         (email/phone/payment edges)

Idempotent. --dry-run for preview.
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import phonenumbers
from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


ROOT     = Path(__file__).resolve().parent.parent.parent
H100_EXT = ROOT / "ccs2026_h100_extraction_bundle"      / "extraction_results.jsonl"
H100_PH  = ROOT / "ccs2026_h100_post_extraction_bundle" / "payment_handles_results.jsonl"
H100_RD  = ROOT / "ccs2026_h100_post_extraction_bundle" / "redirection_results.jsonl"


# ---------- Constants ----------

PAYMENT_URL_TO_KIND = [
    (r"^(?:[a-z0-9.-]*\.)?paypal\.me$",     "paypal"),
    (r"^(?:[a-z0-9.-]*\.)?paypal\.com$",    "paypal"),
    (r"^(?:[a-z0-9.-]*\.)?venmo\.com$",     "venmo"),
    (r"^(?:[a-z0-9.-]*\.)?cash\.app$",      "cashapp"),
    (r"^(?:[a-z0-9.-]*\.)?cashapp\.com$",   "cashapp"),
    (r"^(?:[a-z0-9.-]*\.)?patreon\.com$",   "patreon"),
    (r"^(?:[a-z0-9.-]*\.)?ko-fi\.com$",     "kofi"),
    (r"^(?:[a-z0-9.-]*\.)?buymeacoffee\.com$","buymeacoffee"),
    (r"^(?:[a-z0-9.-]*\.)?wise\.com$",      "wise"),
    (r"^(?:[a-z0-9.-]*\.)?revolut\.me$",    "revolut"),
    (r"^(?:[a-z0-9.-]*\.)?revolut\.com$",   "revolut"),
    (r"^(?:[a-z0-9.-]*\.)?gcash\.com$",     "gcash"),
    (r"^(?:[a-z0-9.-]*\.)?payoneer\.com$",  "payoneer"),
]

# Redirection-FP boilerplate: GoGetFunding's standard "contact campaign owner"
# UI text appears on every page. Drop these so the redirection signal isn't
# inflated by platform UI.
REDIRECTION_BOILERPLATE = [
    "send a message",
    "send me a message",
    "message me",
    "pm me",
    "contact us",
    "contact the campaign owner",
    "contact me",
    "contact me directly",
    "please contact the campaign owner",
    "please contact them",
    "please contact me",
]

PLATFORM_REGION = {
    "Betterplace":   "DE", "GoFundMe":      "US", "Spotfund":      "US",
    "GoGetFunding":  "US", "FreeFunder":    "US", "DonorsChoose":  "US",
    "Angelink":      "US", "Experiment":    "US", "Ufandao":       "DE",
    "Happypot":      "CH", "Chuffed":       "AU", "WhyDonate":     "NL",
    "LaunchGood":    "US", "SeedAndSpark":  "US", "Crowdfundr":    "CA",
}


# ---------- Helpers ----------

def host_of(u):
    u = (u or "").lower().strip()
    u = re.sub(r"^https?://", "", u)
    return u.split("/")[0].split("?")[0].split("#")[0].strip(".")


def detect_payment_kind_from_domain(domain):
    """Return canonical payment kind if domain matches a known payment service."""
    d = (domain or "").lower()
    for pat, kind in PAYMENT_URL_TO_KIND:
        if re.match(pat, d):
            return kind
    return None


def is_redirection_boilerplate(phrase):
    p = (phrase or "").strip().lower().rstrip(".!,:;")
    if len(p) < 4:
        return True
    for bp in REDIRECTION_BOILERPLATE:
        # match if the phrase IS the boilerplate, or starts with it,
        # or differs only by a trivial trailing word
        if p == bp or p.startswith(bp + " ") or p.startswith(bp):
            return True
    return False


def normalize_email(e):
    e = (e or "").strip().lower()
    if not e or "@" not in e:
        return None
    local, _, host = e.partition("@")
    # Gmail tricks: drop +tag, drop dots in local part
    if host in {"gmail.com", "googlemail.com"}:
        local = local.split("+")[0].replace(".", "")
        host = "gmail.com"
    else:
        local = local.split("+")[0]
    return f"{local}@{host}" if local else None


def normalize_phone_e164(raw_or_e164, region_hint=None):
    if not raw_or_e164:
        return None
    s = raw_or_e164.strip()
    digits = re.sub(r"\D+", "", s)
    if not digits or len(digits) < 7:
        return None
    # Already E.164?
    if s.startswith("+"):
        try:
            p = phonenumbers.parse(s, None)
            if phonenumbers.is_valid_number(p):
                return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException: pass
    # 00-prefix international
    if digits.startswith("00") and len(digits) >= 10:
        try:
            p = phonenumbers.parse("+" + digits[2:], None)
            if phonenumbers.is_valid_number(p):
                return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException: pass
    # Implicit + prefix
    try:
        p = phonenumbers.parse("+" + digits, None)
        if phonenumbers.is_valid_number(p):
            return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException: pass
    # Region hint
    if region_hint:
        try:
            p = phonenumbers.parse(s, region_hint)
            if phonenumbers.is_valid_number(p):
                return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException: pass
    return None


def normalize_name(n):
    if not n: return None
    s = unicodedata.normalize("NFKD", str(n)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 \-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def canonical_payment_key(kind, value):
    v = (value or "").strip().lower().lstrip("@$")
    return f"{kind}:{v}" if kind and v else None


def canonical_domain(d):
    d = (d or "").strip().lower()
    d = re.sub(r"^www\.", "", d)
    return d.strip(".") or None


# ---------- Per-campaign builders ----------

def build_organizer_third_party_blocks(ext_record, ph_record_map):
    """Combine main extraction (organizer/third_party) with payment_handles
    recovery for the same campaign. Auto-move misbucketed payment URLs from
    the domains list into payment_handles. Return (organizer, third_party,
    dropped, auto_moved_count)."""
    auto_moved = 0
    org = ext_record.get("organizer") or {}
    tp  = ext_record.get("third_party") or {}

    out_org = {
        "emails":   list(dict.fromkeys(org.get("emails") or [])),
        "phones":   list(dict.fromkeys(org.get("phones") or [])),
        "domains":  [],
        "payment_handles": list(org.get("payment_handles") or []),
    }
    out_tp = {
        "emails":   list(dict.fromkeys(tp.get("emails") or [])),
        "phones":   list(dict.fromkeys(tp.get("phones") or [])),
        "domains":  [],
        "payment_handles": list(tp.get("payment_handles") or []),
    }

    # Auto-move payment URLs from domains to payment_handles
    seen_org_ph = {(h.get("kind"), (h.get("value") or "").lower()) for h in out_org["payment_handles"] if isinstance(h, dict)}
    seen_tp_ph  = {(h.get("kind"), (h.get("value") or "").lower()) for h in out_tp["payment_handles"]  if isinstance(h, dict)}
    seen_org_dom = set()
    seen_tp_dom  = set()
    for d in (org.get("domains") or []):
        d2 = host_of(d)
        if not d2 or d2 in seen_org_dom: continue
        seen_org_dom.add(d2)
        kind = detect_payment_kind_from_domain(d2)
        if kind:
            key = (kind, d2.lower())
            if key not in seen_org_ph:
                out_org["payment_handles"].append({"kind": kind, "value": d2, "found_by": "domains_auto_move"})
                seen_org_ph.add(key)
                auto_moved += 1
        else:
            out_org["domains"].append(d2)
    for d in (tp.get("domains") or []):
        d2 = host_of(d)
        if not d2 or d2 in seen_tp_dom: continue
        seen_tp_dom.add(d2)
        kind = detect_payment_kind_from_domain(d2)
        if kind:
            key = (kind, d2.lower())
            if key not in seen_tp_ph:
                out_tp["payment_handles"].append({"kind": kind, "value": d2, "found_by": "domains_auto_move"})
                seen_tp_ph.add(key)
                auto_moved += 1
        else:
            out_tp["domains"].append(d2)

    # Merge in payment_handles-recovery results for the same URL
    rec = ph_record_map.get(ext_record.get("url"))
    if rec:
        for entry in (rec.get("organizer") or {}).get("payment_handles") or []:
            if not isinstance(entry, dict): continue
            kind = entry.get("kind"); val = entry.get("value")
            key = (kind, (val or "").lower())
            if key not in seen_org_ph and kind and val:
                out_org["payment_handles"].append({"kind": kind, "value": val, "found_by": "payment_recovery"})
                seen_org_ph.add(key)
        for entry in (rec.get("third_party") or {}).get("payment_handles") or []:
            if not isinstance(entry, dict): continue
            kind = entry.get("kind"); val = entry.get("value")
            key = (kind, (val or "").lower())
            if key not in seen_tp_ph and kind and val:
                out_tp["payment_handles"].append({"kind": kind, "value": val, "found_by": "payment_recovery"})
                seen_tp_ph.add(key)

    dropped = ext_record.get("dropped") or {}
    return out_org, out_tp, dropped, auto_moved


def filter_redirection(rd_record):
    """Drop platform-boilerplate phrases and any phrase under 4 chars."""
    kept = []
    dropped = []
    for p in rd_record.get("phrases") or []:
        if not isinstance(p, dict): continue
        phrase = p.get("phrase") or ""
        channel = p.get("channel") or "other"
        if is_redirection_boilerplate(phrase):
            dropped.append({"phrase": phrase, "channel": channel, "reason": "boilerplate_or_short"})
        else:
            kept.append({"phrase": phrase, "channel": channel})
    return kept, dropped


def compute_identity_signals(organizer, platform):
    region = PLATFORM_REGION.get(platform, "US")
    name_norm = None   # We don't currently extract organizer names from the LLM
                       # output; placeholder for future iteration.
    email_norms = sorted({e for e in (normalize_email(x) for x in organizer.get("emails") or []) if e})
    phone_norms = sorted({p for p in (normalize_phone_e164(x, region) for x in organizer.get("phones") or []) if p})
    payment_keys = sorted({canonical_payment_key(h.get("kind"), h.get("value"))
                           for h in (organizer.get("payment_handles") or []) if isinstance(h, dict)} - {None})
    domain_keys  = sorted({canonical_domain(d) for d in organizer.get("domains") or []} - {None, ""})
    return {
        "name_norm":    name_norm,
        "email_norms":  email_norms,
        "phone_norms":  phone_norms,
        "payment_keys": payment_keys,
        "domain_keys":  domain_keys,
    }


# ---------- Cross-platform link computation ----------

def compute_cross_platform_links(per_campaign_signals):
    """Given {url -> identity_signals + platform}, compute per-campaign cross-platform links.
    Returns {url -> cross_platform_links}."""
    # Build inverted indexes
    by_email = defaultdict(list)
    by_phone = defaultdict(list)
    by_payment = defaultdict(list)
    for url, sig in per_campaign_signals.items():
        plat = sig["platform"]
        for e in sig["signals"]["email_norms"]:
            by_email[e].append((url, plat))
        for p in sig["signals"]["phone_norms"]:
            by_phone[p].append((url, plat))
        for pk in sig["signals"]["payment_keys"]:
            by_payment[pk].append((url, plat))

    # For each campaign, look up its own signals and gather links to OTHER campaigns
    out = {}
    for url, sig in per_campaign_signals.items():
        plat = sig["platform"]
        shares_email   = []
        shares_phone   = []
        shares_payment = []
        for e in sig["signals"]["email_norms"]:
            for other_url, other_plat in by_email[e]:
                if other_url != url:
                    shares_email.append({"url": other_url, "platform": other_plat, "value": e})
        for p in sig["signals"]["phone_norms"]:
            for other_url, other_plat in by_phone[p]:
                if other_url != url:
                    shares_phone.append({"url": other_url, "platform": other_plat, "value": p})
        for pk in sig["signals"]["payment_keys"]:
            for other_url, other_plat in by_payment[pk]:
                if other_url != url:
                    shares_payment.append({"url": other_url, "platform": other_plat, "value": pk})
        linked_urls = {x["url"] for x in shares_email + shares_phone + shares_payment}
        linked_platforms = {x["platform"] for x in shares_email + shares_phone + shares_payment}
        out[url] = {
            "shares_email":           shares_email,
            "shares_phone":           shares_phone,
            "shares_payment":         shares_payment,
            "total_linked_campaigns": len(linked_urls),
            "total_linked_platforms": len(linked_platforms),
            "is_cross_platform":      len(linked_platforms - {plat}) > 0,
        }
    return out


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="(debug) only process N campaigns")
    args = ap.parse_args()

    # Index payment_handles_recovery and redirection by URL
    print("=== Loading inputs ===")
    ph_map = {}
    if H100_PH.exists():
        for l in open(H100_PH):
            r = json.loads(l)
            ph_map[r["url"]] = r
    print(f"  payment_handles records: {len(ph_map):,}")

    rd_map = {}
    if H100_RD.exists():
        for l in open(H100_RD):
            r = json.loads(l)
            rd_map[r["url"]] = r
    print(f"  redirection records:     {len(rd_map):,}")

    # Stream extraction_results, build per-campaign updates
    print(f"  reading extraction_results.jsonl...")
    db = get_db()
    ts = datetime.now(timezone.utc).isoformat()

    n = 0
    auto_moved_total = 0
    n_redirection_kept = n_redirection_dropped = 0
    n_org_payment_recovery_added = 0
    per_campaign_signals = {}
    update_buf = {}    # url -> $set dict (to be combined with cross_platform_links later)

    for line in open(H100_EXT):
        r = json.loads(line)
        url = r["url"]
        platform = r.get("platform") or ""
        if args.limit and n >= args.limit: break

        organizer, third_party, dropped, auto_moved = build_organizer_third_party_blocks(r, ph_map)
        auto_moved_total += auto_moved
        n_org_payment_recovery_added += sum(1 for h in organizer["payment_handles"] if h.get("found_by") == "payment_recovery")

        # Redirection (filtered)
        rd_kept = []; rd_dropped = []; rd_flagged = False
        if url in rd_map:
            rd_kept, rd_dropped = filter_redirection(rd_map[url])
            rd_flagged = bool(rd_kept)
            n_redirection_kept    += len(rd_kept)
            n_redirection_dropped += len(rd_dropped)

        # Identity signals (organizer-only)
        signals = compute_identity_signals(organizer, platform)
        per_campaign_signals[url] = {"platform": platform, "signals": signals}

        update_buf[url] = {
            "llm_contacts.organizer":   organizer,
            "llm_contacts.third_party": third_party,
            "llm_contacts.dropped":     dropped,
            "redirection.flagged":         rd_flagged,
            "redirection.phrases":         rd_kept,
            "redirection.dropped_phrases": rd_dropped,
            "identity_signals": signals,
        }
        n += 1

    print(f"\n=== Build summary ===")
    print(f"  campaigns processed:                  {n:,}")
    print(f"  payment URLs auto-moved domains->PH:  {auto_moved_total:,}")
    print(f"  organizer payment_handles from recov: {n_org_payment_recovery_added:,}")
    print(f"  redirection phrases kept:             {n_redirection_kept:,}")
    print(f"  redirection phrases dropped (boilerplate): {n_redirection_dropped:,}")

    # Cross-platform links
    print(f"\n=== Computing cross_platform_links ===")
    cpl = compute_cross_platform_links(per_campaign_signals)
    n_cross = sum(1 for x in cpl.values() if x["is_cross_platform"])
    n_any   = sum(1 for x in cpl.values() if x["total_linked_campaigns"] > 0)
    print(f"  campaigns with at least one shared signal:    {n_any:,}")
    print(f"  campaigns linked across multiple platforms:   {n_cross:,}")

    # Fold into updates
    for url, links in cpl.items():
        if url in update_buf:
            update_buf[url]["cross_platform_links"] = links

    if args.dry_run:
        print(f"\n[dry-run] {len(update_buf):,} campaigns would be updated. No DB writes performed.")
        return

    # Single global metadata doc (paper-time provenance) — NOT per-campaign
    db.extraction_runs.replace_one(
        {"scope": "global"},
        {
            "scope":               "global",
            "model":               "Llama-3.1-8B-Instruct",
            "passes":              ["main_extract", "payment_handle_recovery", "redirection"],
            "applied_at":          ts,
            "covers_n_campaigns":  len(update_buf),
        },
        upsert=True,
    )

    # Bulk-write
    print(f"\n=== Writing to MongoDB ===")
    BATCH = 500
    ops = [UpdateOne({"url": url}, {"$set": d}) for url, d in update_buf.items()]
    n_matched = 0
    for i in range(0, len(ops), BATCH):
        res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
        n_matched += res.matched_count
        if (i // BATCH) % 20 == 0:
            print(f"  ... {i+BATCH:,}/{len(ops):,}  matched_so_far={n_matched:,}")
    print(f"  TOTAL matched: {n_matched:,} / {len(ops):,}")

    # Verify
    print(f"\n=== Post-write verification (mongosh-equivalent counts) ===")
    print(f"  campaigns w/ llm_contacts.organizer.payment_handles.0:  {db.campaigns.count_documents({'llm_contacts.organizer.payment_handles.0': {'$exists': True}}):,}")
    print(f"  campaigns w/ llm_contacts.organizer.emails.0:           {db.campaigns.count_documents({'llm_contacts.organizer.emails.0':          {'$exists': True}}):,}")
    print(f"  campaigns w/ llm_contacts.organizer.domains.0:          {db.campaigns.count_documents({'llm_contacts.organizer.domains.0':         {'$exists': True}}):,}")
    print(f"  campaigns w/ redirection.flagged: true:                 {db.campaigns.count_documents({'redirection.flagged': True}):,}")
    print(f"  campaigns w/ identity_signals.email_norms.0:            {db.campaigns.count_documents({'identity_signals.email_norms.0':            {'$exists': True}}):,}")
    print(f"  campaigns w/ cross_platform_links.is_cross_platform:    {db.campaigns.count_documents({'cross_platform_links.is_cross_platform':   True}):,}")


if __name__ == "__main__":
    main()
