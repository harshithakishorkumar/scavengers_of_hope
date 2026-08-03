#!/usr/bin/env python3
"""
D.4 payment-handle enrichment sweep (2026-07-02).

Question: Detector C's D.4 (payment-handle reuse) edge fires on only 42
campaigns because handles come solely from the LLM contact extraction.
How much hard identity evidence does a direct regex sweep of the raw
descriptions in filtered_dataset_v4.csv recover, and what would it do to
the hardened consensus (combined_hardened_consensus.csv) if swept-handle
reuse counted as a Detector C fire under the existing evidence rule?

Sweep targets (description field, all 100,294 campaigns):
  paypal  paypal.me/<handle>
  cashapp cash.app/$tag URLs + standalone $cashtags CONTEXT-GATED (a
          cash-app context phrase within +-80 chars; tag must start with
          a letter, len>=3, small blocklist) so dollar amounts (e.g. $500)
          and stray tokens don't fire
  venmo   venmo.com/<handle> URLs + @handles CONTEXT-GATED (the word
          "venmo" within +-60 chars; lookbehind kills email locals)
  iban    [A-Z]{2}\\d{2}... candidates, spaces stripped, ISO-country
          length check + mod-97 == 1 validation
  btc     legacy base58 (full Base58Check double-SHA256 checksum) and
          bech32/bech32m (BIP-173/350 checksum) addresses
  eth     0x + 40 hex

Buckets: normalized (lowercased, type-namespaced) handle -> campaign set,
bucket size capped at 10 (same MAX_DOMAIN_SHARE cap as Detector C).

Guardrail measured both ways: an edge counts under GUARDRAIL only if the
bucket contains >=2 DISTINCT non-empty organizer name token-sets (same
normalization as detector_D_identity.organizer_token_set). Same-org
buckets (one person/org reusing its own account) are reported separately.

READ-ONLY with respect to canonical files. Outputs:
  experiments/reliability/d4_swept_handles.csv        per (campaign, handle) row
  experiments/reliability/d4_handle_enrichment_report.txt
"""
from __future__ import annotations
import csv, hashlib, re, sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0].parent          # ccs2026/
DATA = ROOT / "02_data_filtration" / "filtered_dataset_v4.csv"
DFLAGS = ROOT / "03_detection" / "detectors" / "outputs" / "detector_D_flags.csv"
CONS = HERE / "combined_hardened_consensus.csv"
OUT_HANDLES = HERE / "d4_swept_handles.csv"
OUT_REPORT = HERE / "d4_handle_enrichment_report.txt"

BUCKET_CAP = 10           # same as Detector C MAX_DOMAIN_SHARE
CASH_CTX_WIN = 80
VENMO_CTX_WIN = 120   # 60 misses real handles ("...Venmo ... I won't lose 3% -
                      # use whichever is easiest. @Kevin-Bergeron-12")

# ------------------------------------------------------------------ validators
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_IDX = {c: i for i, c in enumerate(B58)}

def base58check_ok(addr: str) -> bool:
    n = 0
    for ch in addr:
        if ch not in B58_IDX:
            return False
        n = n * 58 + B58_IDX[ch]
    # leading-'1' chars encode leading zero bytes
    pad = len(addr) - len(addr.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
    raw = b"\x00" * pad + body
    if len(raw) != 25:
        return False
    payload, chk = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == chk

BECH32_CHARS = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
def _b32_polymod(values):
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= GEN[i] if ((top >> i) & 1) else 0
    return chk
def bech32_ok(addr: str) -> bool:
    if addr != addr.lower():
        return False
    if not addr.startswith("bc1"):
        return False
    hrp, data = "bc", addr[3:]
    if any(c not in BECH32_CHARS for c in data) or len(data) < 6:
        return False
    vals = [BECH32_CHARS.index(c) for c in data]
    exp = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    pm = _b32_polymod(exp + vals)
    return pm in (1, 0x2bc830a3)        # bech32 or bech32m

IBAN_LEN = {  # ISO 13616 registry (country -> total length)
 "AD":24,"AE":23,"AL":28,"AT":20,"AZ":28,"BA":20,"BE":16,"BG":22,"BH":22,
 "BR":29,"BY":28,"CH":21,"CR":22,"CY":28,"CZ":24,"DE":22,"DK":18,"DO":28,
 "EE":20,"EG":29,"ES":24,"FI":18,"FO":18,"FR":27,"GB":22,"GE":22,"GI":23,
 "GL":18,"GR":27,"GT":28,"HR":21,"HU":28,"IE":22,"IL":23,"IQ":23,"IS":26,
 "IT":27,"JO":30,"KW":30,"KZ":20,"LB":28,"LC":32,"LI":21,"LT":20,"LU":20,
 "LV":21,"LY":25,"MC":27,"MD":24,"ME":22,"MK":19,"MR":27,"MT":31,"MU":30,
 "NL":18,"NO":15,"PK":24,"PL":28,"PS":29,"PT":25,"QA":29,"RO":24,"RS":22,
 "SA":24,"SC":31,"SD":18,"SE":24,"SI":19,"SK":24,"SM":27,"ST":25,"SV":28,
 "TL":23,"TN":24,"TR":26,"UA":29,"VA":22,"VG":24,"XK":20,
}
def iban_ok(iban: str) -> bool:
    cc = iban[:2]
    if cc not in IBAN_LEN or len(iban) != IBAN_LEN[cc]:
        return False
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban):
        return False
    s = iban[4:] + iban[:4]
    num = "".join(str(int(c, 36)) for c in s)
    return int(num) % 97 == 1

# ------------------------------------------------------------------ extractors
RE_PAYPAL  = re.compile(r"paypal\.me/([A-Za-z0-9][A-Za-z0-9._\-]{1,49})", re.I)
RE_CASHURL = re.compile(r"cash\.app/\$?([A-Za-z][A-Za-z0-9_]{1,20})", re.I)
RE_CASHTAG = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9_]{2,20})\b")
RE_CASHCTX = re.compile(r"cash\s?app|cashapp|cash\s?tag|cashtag|square\s?cash", re.I)
RE_VENMOURL= re.compile(r"venmo\.com/(?:u/)?([A-Za-z0-9][A-Za-z0-9_\-]{2,30})", re.I)
RE_VENMOAT = re.compile(r"(?<![\w.])@([A-Za-z0-9][A-Za-z0-9_\-]{2,30})\b")
RE_VENMOCTX= re.compile(r"venmo", re.I)
RE_IBAN    = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,32})\b")
RE_BTC58   = re.compile(r"\b([13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
RE_BECH32  = re.compile(r"\b(bc1[a-z0-9]{8,87})\b")
RE_ETH     = re.compile(r"\b(0x[a-fA-F0-9]{40})\b(?![a-fA-F0-9])")

CASHTAG_BLOCK = {"cashapp", "cashtag", "cash", "usd", "cad", "aud", "nzd"}
VENMO_AT_BLOCK = {"gmail", "yahoo", "hotmail", "outlook", "aol", "icloud",
                  "venmo", "gmail.com", "paypal"}
PAYPAL_BLOCK = {"pools"}

def strip_punct(s: str) -> str:
    return s.strip().strip(".,;:!?)('\"").lower()

def ctx_hit(text, m, ctx_re, win):
    lo = max(0, m.start() - win); hi = min(len(text), m.end() + win)
    return bool(ctx_re.search(text[lo:hi]))

def extract_handles(text: str):
    """Yield (type, normalized_handle) tuples from one description."""
    out = set()
    if not isinstance(text, str) or not text:
        return out
    for m in RE_PAYPAL.finditer(text):
        h = strip_punct(m.group(1))
        if len(h) >= 2 and h not in PAYPAL_BLOCK:
            out.add(("paypal", h))
    for m in RE_CASHURL.finditer(text):
        h = strip_punct(m.group(1))
        if len(h) >= 2 and h not in CASHTAG_BLOCK:
            out.add(("cashapp", h))
    for m in RE_CASHTAG.finditer(text):
        h = strip_punct(m.group(1))
        if h in CASHTAG_BLOCK or len(h) < 3:
            continue
        if ctx_hit(text, m, RE_CASHCTX, CASH_CTX_WIN):
            out.add(("cashapp", h))
    for m in RE_VENMOURL.finditer(text):
        h = strip_punct(m.group(1))
        if h and h not in {"code", "signup", "u"}:
            out.add(("venmo", h))
    for m in RE_VENMOAT.finditer(text):
        h = strip_punct(m.group(1))
        if h in VENMO_AT_BLOCK:
            continue
        if ctx_hit(text, m, RE_VENMOCTX, VENMO_CTX_WIN):
            out.add(("venmo", h))
    for m in RE_IBAN.finditer(text):
        iban = re.sub(r"\s", "", m.group(1)).upper()
        if iban_ok(iban):
            out.add(("iban", iban.lower()))
    for m in RE_BTC58.finditer(text):
        if base58check_ok(m.group(1)):
            out.add(("btc", m.group(1)))     # base58 is case-sensitive
    for m in RE_BECH32.finditer(text):
        if bech32_ok(m.group(1)):
            out.add(("btc", m.group(1)))
    for m in RE_ETH.finditer(text):
        out.add(("eth", m.group(1).lower()))
    return out

# same normalization as detector_D_identity.organizer_token_set
def organizer_token_set(name):
    if not isinstance(name, str):
        return None
    toks = [t for t in re.findall(r"\w+", name.lower()) if len(t) >= 2]
    if len(toks) < 1:
        return None
    return tuple(sorted(toks))

def same_operator(ts1, ts2):
    """Fuzzy same-operator test for GUARDRAIL-2. GGF organizer strings carry
    city suffixes ('Rafat Qudaih Gaza' vs 'Rafat Qudaih Gaza City') and one
    scrape artifact stores a python-dict string; exact token-set equality
    misclassifies those as distinct people. Rule: same operator iff exact
    token-set match, OR token_sort_ratio >= 85, OR >= 2 shared name tokens."""
    if ts1 is None or ts2 is None:
        return False
    if ts1 == ts2:
        return True
    if fuzz.token_sort_ratio(" ".join(ts1), " ".join(ts2)) >= 85:
        return True
    return len(set(ts1) & set(ts2)) >= 2

# ------------------------------------------------------------------------ main
def main():
    tee = open(OUT_REPORT, "w")
    def P(*a):
        line = " ".join(str(x) for x in a)
        print(line); tee.write(line + "\n")

    df = pd.read_csv(DATA, low_memory=False)
    P(f"corpus rows: {len(df):,} (unique urls: {df['url'].nunique():,})")

    url_plat = dict(zip(df["url"], df["platform"]))
    url_org  = dict(zip(df["url"], df["organizer"]))

    # ---- 1. sweep ----
    camp_handles = {}          # url -> set((type, handle))
    buckets = defaultdict(set) # (type, handle) -> {urls}
    for url, text in zip(df["url"], df["description"].fillna("")):
        hs = extract_handles(text)
        if hs:
            camp_handles[url] = hs
            for h in hs:
                buckets[h].add(url)

    n_swept = len(camp_handles)
    P(f"\n[1] SWEEP")
    P(f"campaigns with >=1 swept handle: {n_swept:,} "
      f"({100*n_swept/len(df):.2f}% of corpus)")
    type_camp = Counter(); type_handle = Counter()
    for (t, h), urls in buckets.items():
        type_handle[t] += 1; type_camp[t] += len(urls)
    for t in sorted(type_handle):
        P(f"  {t:8s} handles={type_handle[t]:5,}  campaign-mentions={type_camp[t]:5,}")
    P(f"distinct handles total: {len(buckets):,}")

    oversized = {h: len(u) for h, u in buckets.items() if len(u) > BUCKET_CAP}
    if oversized:
        P(f"buckets dropped by cap>{BUCKET_CAP}: {len(oversized)} -> "
          + "; ".join(f"{t}:{h}({n})" for (t, h), n in sorted(oversized.items(), key=lambda x: -x[1])))
    buckets = {h: u for h, u in buckets.items() if len(u) <= BUCKET_CAP}
    multi = {h: sorted(u) for h, u in buckets.items() if len(u) >= 2}
    multi_camps = sorted({u for us in multi.values() for u in us})
    P(f"handles in >=2 campaigns (cap {BUCKET_CAP}): {len(multi):,} "
      f"covering {len(multi_camps):,} campaigns")
    P("bucket size distribution (multi): " +
      str(sorted(Counter(len(u) for u in multi.values()).items())))

    # ---- roster of all multi buckets (for spot reading) ----
    P("\n[1b] MULTI-BUCKET ROSTER")
    for (t, h), us in sorted(multi.items()):
        P(f"  {t}:{h}  (n={len(us)})")
        for u in us:
            P(f"     {u}  org={url_org.get(u)!r}")

    # ---- classify each multi bucket by organizer name-sets ----
    def bucket_orgclass(urls):
        names = {organizer_token_set(url_org.get(u)) for u in urls}
        names.discard(None)
        if len(names) >= 2:
            return "distinct-org"
        if len(names) == 1 and all(organizer_token_set(url_org.get(u)) for u in urls):
            return "same-org"
        return "unknown-org"   # missing organizer(s), can't tell
    bclass = {h: bucket_orgclass(u) for h, u in multi.items()}
    cc = Counter(bclass.values())
    P(f"\n[4] GUARDRAIL: multi-campaign bucket organizer classes:")
    for k in ("same-org", "distinct-org", "unknown-org"):
        P(f"  {k:12s} {cc.get(k,0):4d}  ({100*cc.get(k,0)/max(len(multi),1):.1f}%)")

    # per-campaign edge sets, three ways
    def campaign_has_edge(url, mode):
        my = organizer_token_set(url_org.get(url))
        for h in camp_handles.get(url, ()):
            us = multi.get(h)
            if not us:
                continue
            if mode == "all":
                return h
            for o in us:
                if o == url:
                    continue
                on = organizer_token_set(url_org.get(o))
                if on is None or my is None:
                    continue          # can't demonstrate distinctness
                if mode == "gr1" and on != my:
                    return h          # exact token-set differs
                if mode == "gr2" and not same_operator(my, on):
                    return h          # fuzzy-distinct operator
        return None
    edge_all = {u for u in camp_handles if campaign_has_edge(u, "all")}
    edge_gr1 = {u for u in camp_handles if campaign_has_edge(u, "gr1")}
    edge_gr2 = {u for u in camp_handles if campaign_has_edge(u, "gr2")}
    P(f"campaigns with a swept-handle edge: ALL-EDGES {len(edge_all):,}  "
      f"GUARDRAIL-1(exact-distinct) {len(edge_gr1):,}  "
      f"GUARDRAIL-2(fuzzy-distinct) {len(edge_gr2):,}")

    # pair-level operator classification
    def pair_class(a, b):
        ta, tb = organizer_token_set(url_org.get(a)), organizer_token_set(url_org.get(b))
        if ta is None or tb is None:
            return "unknown-op"
        return "same-op" if same_operator(ta, tb) else "distinct-op"

    # ---- 2. overlap with existing D.4 + new pairs ----
    d4_urls, dflag = set(), {}
    with open(DFLAGS, newline="") as f:
        for r in csv.DictReader(f):
            dflag[r["url"]] = r
            if "D.4_handle" in (r.get("fired_signals") or ""):
                d4_urls.add(r["url"])
    P(f"\n[2] ENRICHMENT vs existing D.4")
    P(f"existing D.4 campaigns: {len(d4_urls)}")
    P(f"overlap swept-multi-campaigns vs D.4: {len(set(multi_camps) & d4_urls)}")
    P(f"swept-multi campaigns NOT already D.4: {len(set(multi_camps) - d4_urls):,}")
    inC = sum(1 for u in multi_camps if dflag.get(u, {}).get("flag") == "1")
    P(f"swept-multi campaigns already C-flagged (any cluster): {inC}")

    pairs = set()
    for h, us in multi.items():
        pairs.update(combinations(us, 2))
    same_cluster = 0
    new_pairs = []
    for a, b in sorted(pairs):
        ra, rb = dflag.get(a), dflag.get(b)
        if ra and rb and ra.get("flag") == "1" and rb.get("flag") == "1" \
           and ra.get("cluster_id") and ra.get("cluster_id") == rb.get("cluster_id"):
            same_cluster += 1
        else:
            new_pairs.append((a, b))
    xplat_pairs = sum(1 for a, b in pairs if url_plat.get(a) != url_plat.get(b))
    plats = {url_plat.get(u) for u in multi_camps}
    xplat_buckets = sum(1 for us in multi.values()
                        if len({url_plat.get(u) for u in us}) >= 2)
    P(f"swept campaign pairs (hard identity edges): {len(pairs):,}")
    P(f"  already co-clustered by shipped Detector C: {same_cluster}")
    P(f"  NEW pairs (not co-clustered):               {len(new_pairs):,}")
    P(f"  cross-platform pairs: {xplat_pairs}   cross-platform buckets: {xplat_buckets}")
    P(f"  platforms covered by multi-bucket campaigns: {len(plats)} -> {sorted(str(p) for p in plats)}")
    P(f"  pair operator classes (ALL pairs):  {dict(Counter(pair_class(a,b) for a,b in pairs))}")
    P(f"  pair operator classes (NEW pairs):  {dict(Counter(pair_class(a,b) for a,b in new_pairs))}")

    # ---- 3. consensus impact ----
    cons = {}
    with open(CONS, newline="") as f:
        for r in csv.DictReader(f):
            cons[r["url"]] = r
    P(f"\n[3] CONSENSUS IMPACT (combined_hardened_consensus.csv)")
    MODES = (("ALL-EDGES", edge_all), ("GUARDRAIL-1", edge_gr1),
             ("GUARDRAIL-2", edge_gr2))
    textonly = [u for u, r in cons.items()
                if r["fraud_hard"] == "1" and r["corroborated"] == "0"]
    P(f"text-only fraud baseline: {len(textonly)} (expect 459)")
    for label, edges in MODES:
        gain = [u for u in textonly if u in edges]
        P(f"  text-only fraud gaining HARD corroboration [{label}]: {len(gain)}")
        for u in gain:
            hs = [f"{t}:{h}" for (t, h) in camp_handles[u] if (t, h) in multi]
            P(f"    + {u}  ({url_plat.get(u)})  handles={hs}")

    susp = [u for u, r in cons.items()
            if r["score_hard"] == "1" and r["C_hard"] == "0"]
    susp_c1 = sum(1 for u, r in cons.items() if r["score_hard"] == "1" and r["C_hard"] == "1")
    P(f"suspicious (score_hard=1): {susp_c1 + len(susp)} of which C_hard already fired: {susp_c1}")
    promo = {}
    for label, edges in MODES:
        pr = [u for u in susp if u in edges]
        promo[label] = pr
        P(f"  suspicious -> fraud promotions [{label}]: {len(pr)}")
        for u in pr:
            P(f"      {u}")
    # also: swept edge as C fire for score_hard=0 -> becomes suspicious (context)
    zero_up = [u for u, r in cons.items()
               if r["score_hard"] == "0" and u in edge_all]
    P(f"  (context) unknown(score 0) campaigns that would become suspicious [ALL-EDGES]: {len(zero_up)}")

    # takedown preservation: rule is purely additive
    takedown_fraud = sum(1 for r in cons.values()
                         if r["fraud_hard"] == "1" and r["takedown"] == "1")
    P(f"takedown preservation: additive rule, no demotions possible; "
      f"{takedown_fraud}/109 takedown-fraud retained")
    rep_overlap = sum(1 for u in multi_camps
                      if cons.get(u, {}).get("rep_hard") == "1")
    take_overlap = sum(1 for u in multi_camps
                       if cons.get(u, {}).get("takedown") == "1")
    P(f"sanitized-reputation overlap among swept-multi campaigns: {rep_overlap}")
    P(f"takedown overlap among swept-multi campaigns: {take_overlap}")

    # fraud-tier delta
    # full before/after bookkeeping per mode
    fraud0 = sum(1 for r in cons.values() if r["fraud_hard"] == "1")
    corr0 = sum(1 for r in cons.values()
                if r["fraud_hard"] == "1" and r["corroborated"] == "1")
    ggf0 = sum(1 for r in cons.values()
               if r["fraud_hard"] == "1" and r["platform"] == "GoGetFunding")
    ch0 = sum(1 for r in cons.values() if r["C_hard"] == "1")
    P(f"\nBEFORE: fraud {fraud0:,} ({corr0} corr / {fraud0-corr0} text-only)  "
      f"GGF {ggf0}  C_hard fires {ch0:,}")
    for label, edges in MODES:
        pr = promo[label]
        gain = [u for u in textonly if u in edges]
        newC = sum(1 for u in edges if cons.get(u, {}).get("C_hard") == "0")
        fraudN = fraud0 + len(pr)
        corrN = corr0 + len(gain) + len(pr)   # promotions corroborated via C rule
        ggfN = ggf0 + sum(1 for u in pr
                          if cons.get(u, {}).get("platform") == "GoGetFunding")
        P(f"AFTER [{label}]: fraud {fraudN:,} ({corrN} corr / {fraudN-corrN} "
          f"text-only)  GGF {ggfN}  C_hard fires {ch0+newC:,} (+{newC})")

    # ---- promoted campaign dossiers (for manual reading) ----
    P(f"\n[3b] PROMOTED-CAMPAIGN DOSSIERS (ALL-EDGES)")
    desc_map = dict(zip(df["url"], df["description"].fillna("")))
    title_map = dict(zip(df["url"], df["title"].fillna("")))
    for u in promo["ALL-EDGES"]:
        r = cons[u]
        hs = [(t, h) for (t, h) in camp_handles[u] if (t, h) in multi]
        P(f"\n  URL: {u}")
        P(f"  platform={url_plat.get(u)} organizer={url_org.get(u)!r} "
          f"A={r['A_hard']} B={r['B_hard']} takedown={r['takedown']} "
          f"gr1={'Y' if u in edge_gr1 else 'N'} gr2={'Y' if u in edge_gr2 else 'N'}")
        P(f"  title: {title_map.get(u, '')[:100]}")
        for (t, h) in hs:
            others = [o for o in multi[(t, h)] if o != u]
            P(f"  handle {t}:{h}  bucket_class={bclass[(t,h)]}  co-members:")
            for o in others:
                oc = cons.get(o, {})
                P(f"    - {o} ({url_plat.get(o)}) org={url_org.get(o)!r} "
                  f"tier={oc.get('gated_tier','?')} takedown={oc.get('takedown','?')}")
        P(f"  desc[:400]: {desc_map.get(u, '')[:400]!r}")

    # ---- write per-campaign handle CSV ----
    with open(OUT_HANDLES, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "platform", "organizer", "handle_type", "handle",
                    "bucket_size", "bucket_class", "edge_all",
                    "edge_guardrail1", "edge_guardrail2"])
        for u in sorted(camp_handles):
            for (t, h) in sorted(camp_handles[u]):
                key = (t, h)
                size = len(buckets.get(key, ())) if key in buckets else len(oversized) and oversized.get(key, 0)
                size = len(buckets[key]) if key in buckets else oversized.get(key, 0)
                w.writerow([u, url_plat.get(u), url_org.get(u), t, h, size,
                            bclass.get(key, ""), int(u in edge_all),
                            int(u in edge_gr1), int(u in edge_gr2)])
    P(f"\n[written] {OUT_HANDLES}")
    P(f"[written] {OUT_REPORT}")
    tee.close()

if __name__ == "__main__":
    main()
