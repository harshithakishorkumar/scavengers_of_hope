"""Build the final v6 VT and IPQS pending lists.

Sources (union across all):
  1. H100 extraction outputs (organizer-attributed only)
       extraction_results.jsonl    -> emails, phones, domains
       payment_handles_results.jsonl -> payment-handle-derived domains
  2. Harshita's unchecked candidates
       harshita/emails_free.jsonl, emails_org.jsonl (unchecked subset)
       harshita/phones.jsonl (unchecked subset)
       harshita/urls_unknown.jsonl, urls_priority.jsonl, urls_social.jsonl (URL hosts -> domain candidates)
  3. Existing pending queue
       domains_pending_for_vt.txt

Filters:
  - VT skiplist: email providers, social majors, crowdfunding platforms, generic high-volume hosts
  - Strict regex on domain shape (drops 1% noise)
  - Dedupe against existing results (vt_domain_results.jsonl, ipqs_email_results.jsonl, ipqs_phone_results.jsonl)

Outputs (in this dir):
  vt_pending_v6.txt
  ipqs_email_pending_v6.txt
  ipqs_phone_pending_v6.txt
  v6_build_summary.json     # counts at each stage
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import phonenumbers


# Map our 15 platforms (and Harshita's URL-host names) to ISO-3166 region codes
# so the phonenumbers library can interpret local-format numbers correctly.
PLATFORM_REGION = {
    # Our DB platform field (capitalized brand names)
    "Betterplace":   "DE",
    "GoFundMe":      "US",
    "Spotfund":      "US",
    "GoGetFunding":  "US",
    "FreeFunder":    "US",
    "DonorsChoose":  "US",
    "Angelink":      "US",
    "Experiment":    "US",
    "Ufandao":       "DE",
    "Happypot":      "CH",
    "Chuffed":       "AU",
    "WhyDonate":     "NL",
    "LaunchGood":    "US",
    "SeedAndSpark":  "US",
    "Crowdfundr":    "CA",
    # Harshita's URL hosts (lowercase domain form)
    "betterplace.org":   "DE",
    "gofundme.com":      "US",
    "spotfund.com":      "US",
    "gogetfunding.com":  "US",
    "freefunder.com":    "US",
    "donorschoose.org":  "US",
    "angelink.com":      "US",
    "experiment.com":    "US",
    "ufandao.com":       "DE",
    "my.ufandao.com":    "DE",
    "happypot.ch":       "CH",
    "chuffed.org":       "AU",
    "whydonate.com":     "NL",
    "launchgood.com":    "US",
    "seedandspark.com":  "US",
    "crowdfundr.com":    "CA",
    "crowdfunder.co.uk": "GB",
    "ulule.com":         "FR",
    "globalgiving.org":  "US",
}


def normalize_phone_e164(raw, candidate_regions):
    """Try to coerce raw phone to E.164. Returns the E.164 string or None.

    Strategy (in order):
      1. If raw starts with '00' and is long enough, treat as European
         international format (00xxx) and convert to +xxx
      2. Try parsing with implicit '+' prefix (assumes raw is already E.164)
      3. Try parsing with each candidate region as default (in order, most-
         common-platform-region first)
    """
    if not raw or len(raw) < 7:
        return None

    # 1. 00-prefix international
    if raw.startswith("00") and len(raw) >= 10:
        try:
            parsed = phonenumbers.parse("+" + raw[2:], None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass

    # 2. Already-E.164 (treat raw as the digits-after-the-plus)
    try:
        parsed = phonenumbers.parse("+" + raw, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass

    # 3. Each candidate region as default
    for region in candidate_regions:
        try:
            parsed = phonenumbers.parse(raw, region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            continue

    return None


def regions_for_platforms(platforms):
    """Order candidate regions by platform-frequency (most-common first)."""
    if not platforms:
        return ["US"]
    region_counts = Counter()
    for p in platforms:
        r = PLATFORM_REGION.get(p)
        if r:
            region_counts[r] += 1
    if not region_counts:
        return ["US"]
    return [r for r, _ in region_counts.most_common()]

ROOT      = Path(__file__).resolve().parent.parent.parent.parent  # repo root
INTEL_DIR = ROOT / "ccs2026" / "03_detection" / "intel"
H100_EXT  = ROOT / "ccs2026_h100_extraction_bundle"      / "extraction_results.jsonl"
H100_PH   = ROOT / "ccs2026_h100_post_extraction_bundle" / "payment_handles_results.jsonl"
HARSHITA  = ROOT / "harshita"

# ---------- VT skiplist ----------
VT_SKIPLIST = {
    # Email providers
    "gmail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "yahoo.co.jp",
    "yahoo.fr", "yahoo.de", "yahoo.com.ph", "yahoo.com.au", "yahoo.com.br",
    "yahoo.es", "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.de",
    "outlook.com", "outlook.fr", "outlook.de", "icloud.com", "me.com", "mac.com",
    "aol.com", "live.com", "msn.com", "mail.com", "protonmail.com", "proton.me",
    "gmx.de", "gmx.com", "gmx.net", "web.de", "yandex.ru", "yandex.com", "yandex.ua",
    "mail.ru", "rambler.ru", "qq.com", "163.com", "126.com", "sina.com", "sina.cn",
    "googlemail.com",
    # Major social platforms
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "youtu.be", "tiktok.com", "linkedin.com", "pinterest.com", "snapchat.com",
    "threads.net", "threads.com", "t.me", "telegram.me", "telegram.org",
    "wa.me", "whatsapp.com", "discord.com", "discord.gg", "reddit.com",
    "tumblr.com", "vk.com", "weibo.com",
    # Crowdfunding platforms (self-references)
    "gofundme.com", "gofundme.org", "betterplace.org", "betterplace.com",
    "globalgiving.org", "chuffed.org", "spotfund.com", "launchgood.com",
    "crowdfundr.com", "donorschoose.org", "whydonate.com", "crowdfunder.co.uk",
    "happypot.com", "seedandspark.com", "experiment.com", "ufandao.com",
    "gogetfunding.com", "angelink.org", "freefunder.com", "kickstarter.com",
    "indiegogo.com", "patreon.com",
    # Generic giants — pointless to query
    "google.com", "google.co.uk", "google.de", "google.fr",
    "wikipedia.org", "en.wikipedia.org", "amazon.com", "ebay.com",
    "apple.com", "microsoft.com", "github.com", "github.io",
}
# Subdomain-suffix patterns to also skip
def is_skiplisted(domain):
    d = (domain or "").lower().strip()
    if d in VT_SKIPLIST:
        return True
    # Match domains ending in any skiplisted parent (e.g. webmail.gmail.com)
    for sl in VT_SKIPLIST:
        if d.endswith("." + sl):
            return True
    return False

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$", re.I)
TLD_RE    = re.compile(r"\.([a-z]{2,24})$", re.I)   # final label must be alphabetic ⇒ rejects "1.2.3" / "0918.912.3095"
LETTER_RE = re.compile(r"[a-z]", re.I)
EMAIL_RE  = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", re.I)


def is_valid_domain(d):
    """Stricter than DOMAIN_RE: also require a real-looking alphabetic TLD
    and at least one letter somewhere (rejects phone numbers like 0918.912.3095)."""
    if not d or not DOMAIN_RE.match(d):
        return False
    if not TLD_RE.search(d):
        return False
    if not LETTER_RE.search(d):
        return False
    return True


def is_valid_email(e):
    if not e or len(e) > 254:
        return False
    return bool(EMAIL_RE.match(e))


def host_of(u):
    u = (u or "").lower().strip()
    u = re.sub(r"^https?://", "", u)
    return u.split("/")[0].split("?")[0].split("#")[0].strip(".")


def normalize_email(e):
    return (e or "").strip().lower()


def normalize_phone(p):
    digits = re.sub(r"\D+", "", p or "")
    if not digits:
        return None
    # length sanity: real international numbers are 7-15 digits
    if len(digits) < 7 or len(digits) > 15:
        return None
    # reject if more than half the digits are leading zeros (anonymized/junk)
    leading_zeros = len(digits) - len(digits.lstrip("0"))
    if leading_zeros >= 3 and leading_zeros >= len(digits) // 2:
        return None
    return digits


def main():
    summary = {"sources": {}, "pre_filter": {}, "post_filter": {}, "final": {}}

    # ---------- Load existing state for dedupe ----------
    existing_vt = set()
    for l in open(INTEL_DIR / "vt_domain_results.jsonl"):
        try: existing_vt.add((json.loads(l).get("domain") or "").lower())
        except: pass

    existing_ipqs_email = set()
    p = INTEL_DIR / "ipqs_email_results.jsonl"
    if p.exists():
        for l in open(p):
            try:
                r = json.loads(l)
                v = normalize_email(r.get("email") or r.get("value") or "")
                if v: existing_ipqs_email.add(v)
            except: pass

    # For phones, only count as "already done" entries that successfully
    # returned a verdict. Failed entries (~838 of 2,384) get re-queried after
    # we re-normalize.
    existing_ipqs_phone_e164 = set()
    n_existing_phone_total = n_existing_phone_success = 0
    p = INTEL_DIR / "ipqs_phone_results.jsonl"
    if p.exists():
        for l in open(p):
            try:
                r = json.loads(l)
                n_existing_phone_total += 1
                # Old schema: result.success may indicate whether IPQS gave a verdict
                res = r.get("result") or {}
                if not res.get("success"):
                    continue
                n_existing_phone_success += 1
                raw = (r.get("phone") or r.get("value") or "")
                # Try to normalize the historical phone string to E.164 so
                # we can dedupe against new pending entries that are E.164.
                e164 = normalize_phone_e164(re.sub(r"\D+", "", raw), ["US"])
                if e164:
                    existing_ipqs_phone_e164.add(e164)
            except: pass

    print(f"existing_vt:                       {len(existing_vt):,}")
    print(f"existing_ipqs_email:               {len(existing_ipqs_email):,}")
    print(f"existing_ipqs_phone (total/success): {n_existing_phone_total:,} / {n_existing_phone_success:,}")
    print(f"  -> normalized to E.164 for dedupe: {len(existing_ipqs_phone_e164):,}")
    print(f"  (failed entries get a fresh chance on re-query)")
    print()

    # ---------- 1. H100 organizer extraction ----------
    # Phones get tracked with their source platform so we can pick a default
    # region for normalization. Domains and emails are simpler.
    h100_domains = set()
    h100_emails  = set()
    h100_phone_platforms = defaultdict(set)   # raw_digits -> {platform, ...}
    if H100_EXT.exists():
        for l in open(H100_EXT):
            r = json.loads(l)
            platform = r.get("platform") or ""
            org = r.get("organizer") or {}
            for d in (org.get("domains") or []):
                d = host_of(d)
                if d: h100_domains.add(d)
            for e in (org.get("emails") or []):
                e = normalize_email(e)
                if e: h100_emails.add(e)
            for ph in (org.get("phones") or []):
                digits = re.sub(r"\D+", "", ph or "")
                if digits and len(digits) >= 7:
                    h100_phone_platforms[digits].add(platform)
    summary["sources"]["h100_organizer_domains"] = len(h100_domains)
    summary["sources"]["h100_organizer_emails"]  = len(h100_emails)
    summary["sources"]["h100_organizer_phones"]  = len(h100_phone_platforms)

    # ---------- 2. H100 payment-handle recovery (extract domains from URL-shaped values) ----------
    ph_domains = set()
    if H100_PH.exists():
        for l in open(H100_PH):
            r = json.loads(l)
            for entry in ((r.get("organizer") or {}).get("payment_handles") or []):
                v = entry.get("value", "")
                if "/" in v or "." in v:
                    d = host_of(v)
                    if d: ph_domains.add(d)
    summary["sources"]["h100_payment_handle_domains"] = len(ph_domains)

    # ---------- 3. Harshita's unchecked candidates ----------
    h_domains = set()
    for fname in ("urls_unknown.jsonl", "urls_priority.jsonl", "urls_social.jsonl"):
        p = HARSHITA / fname
        if not p.exists(): continue
        for l in open(p):
            try:
                r = json.loads(l)
                d = host_of(r.get("value"))
                if d: h_domains.add(d)
            except: pass
    summary["sources"]["harshita_url_domains"] = len(h_domains)

    h_emails_unchecked = set()
    for fname in ("emails_org.jsonl", "emails_free.jsonl"):
        p = HARSHITA / fname
        if not p.exists(): continue
        for l in open(p):
            try:
                r = json.loads(l)
                if not r.get("checked"):
                    e = normalize_email(r.get("value"))
                    if e: h_emails_unchecked.add(e)
            except: pass
    summary["sources"]["harshita_emails_unchecked"] = len(h_emails_unchecked)

    h_phone_platforms = defaultdict(set)   # raw_digits -> {platform, ...}
    p = HARSHITA / "phones.jsonl"
    if p.exists():
        for l in open(p):
            try:
                r = json.loads(l)
                if not r.get("checked"):
                    digits = re.sub(r"\D+", "", r.get("value") or "")
                    if digits and len(digits) >= 7:
                        for plat in (r.get("platforms") or []):
                            h_phone_platforms[digits].add(plat)
            except: pass
    summary["sources"]["harshita_phones_unchecked"] = len(h_phone_platforms)

    # ---------- 4. Existing VT pending queue ----------
    legacy_pending = set()
    p = INTEL_DIR / "domains_pending_for_vt.txt"
    if p.exists():
        for line in open(p):
            d = host_of(line.strip())
            if d: legacy_pending.add(d)
    summary["sources"]["legacy_vt_pending"] = len(legacy_pending)

    # ---------- Build VT pending v6 ----------
    vt_union = h100_domains | ph_domains | h_domains | legacy_pending
    summary["pre_filter"]["vt_union"] = len(vt_union)

    vt_after_skiplist = {d for d in vt_union if not is_skiplisted(d)}
    summary["post_filter"]["vt_after_skiplist"] = len(vt_after_skiplist)

    vt_after_validation = {d for d in vt_after_skiplist if is_valid_domain(d)}
    summary["post_filter"]["vt_after_validation"] = len(vt_after_validation)

    vt_pending = sorted(vt_after_validation - existing_vt)
    summary["final"]["vt_pending"] = len(vt_pending)

    # ---------- Build IPQS email pending v6 ----------
    email_union_raw = h100_emails | h_emails_unchecked
    summary["pre_filter"]["ipqs_email_union_raw"] = len(email_union_raw)
    email_union = {e for e in email_union_raw if is_valid_email(e)}
    summary["post_filter"]["ipqs_email_after_validation"] = len(email_union)
    ipqs_email_pending = sorted(email_union - existing_ipqs_email)
    summary["final"]["ipqs_email_pending"] = len(ipqs_email_pending)

    # ---------- Build IPQS phone pending v6 (with E.164 normalization) ----------
    # Merge H100 + Harshita platform sets, then for each unique raw digit
    # string, normalize to E.164 using the platform-derived candidate regions.
    merged_phone_platforms = defaultdict(set)
    for digits, plats in h100_phone_platforms.items():
        merged_phone_platforms[digits] |= plats
    for digits, plats in h_phone_platforms.items():
        merged_phone_platforms[digits] |= plats

    summary["pre_filter"]["ipqs_phone_union_raw"] = len(merged_phone_platforms)

    e164_to_meta = {}   # E.164 -> {platforms, raw_inputs}
    n_normalized = 0
    n_failed = 0
    fail_examples = []
    for digits, plats in merged_phone_platforms.items():
        regions = regions_for_platforms(plats)
        e164 = normalize_phone_e164(digits, regions)
        if e164:
            n_normalized += 1
            if e164 not in e164_to_meta:
                e164_to_meta[e164] = {"platforms": set(), "raw_inputs": set()}
            e164_to_meta[e164]["platforms"] |= plats
            e164_to_meta[e164]["raw_inputs"].add(digits)
        else:
            n_failed += 1
            if len(fail_examples) < 5:
                fail_examples.append((digits, sorted(plats)))

    summary["post_filter"]["ipqs_phone_normalized_e164"] = len(e164_to_meta)
    summary["post_filter"]["ipqs_phone_normalize_failed"] = n_failed
    summary["post_filter"]["ipqs_phone_normalize_fail_examples"] = fail_examples

    ipqs_phone_pending = sorted(set(e164_to_meta.keys()) - existing_ipqs_phone_e164)
    summary["final"]["ipqs_phone_pending"] = len(ipqs_phone_pending)

    # ---------- Write outputs ----------
    out_vt    = INTEL_DIR / "vt_pending_v6.txt"
    out_email = INTEL_DIR / "ipqs_email_pending_v6.txt"
    out_phone = INTEL_DIR / "ipqs_phone_pending_v6.txt"

    out_vt.write_text("\n".join(vt_pending) + ("\n" if vt_pending else ""))
    out_email.write_text("\n".join(ipqs_email_pending) + ("\n" if ipqs_email_pending else ""))
    out_phone.write_text("\n".join(ipqs_phone_pending) + ("\n" if ipqs_phone_pending else ""))

    # Summary
    summary_path = INTEL_DIR / "v6_build_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print()
    print(f"WROTE:")
    print(f"  {out_vt}    ({len(vt_pending):,} domains)")
    print(f"  {out_email}    ({len(ipqs_email_pending):,} emails)")
    print(f"  {out_phone}    ({len(ipqs_phone_pending):,} phones)")
    print(f"  {summary_path}")


if __name__ == "__main__":
    main()
