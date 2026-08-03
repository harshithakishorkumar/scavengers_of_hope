#!/usr/bin/env python3
"""
Combined hardened 3-detector consensus.

Applies the GO-rated detector-hardening rules (with the verify-stage carve-outs),
re-runs the >=2-of-3 fraud rule, joins the result to
consensus_corroboration_stratified.csv, and measures the effect on the Fraud tier
against both the shipped baseline and the post-hoc corroboration gate.

Rules applied (only GO / conditional-GO with carve-out; NO-GO rules skipped):

  Detector A, R1  reputation-arm sanitization
      The reputation arm (detector_A_flags.csv) fires on VT domain >=2,
      IPQS email >=85, or IPQS phone >=85. R1 keeps VT and IPQS-phone hits
      unchanged but trusts an IPQS *email* hit only when, for at least one
      extracted email scoring >=85, the IPQS record is:
        - valid == True (deliverable mailbox), and
        - dns_valid == True, and
        - risky_tld == False (real TLD), and
        - generic == False, and
        - the mailbox domain is not a generic-provider / social / payment /
          crowdfunding-platform domain.
      An email hit failing sanitization no longer contributes to the reputation
      arm. VT / IPQS-phone hits are untouched.

  Detector A, R3  off-platform-redirection multi-phrase gate
      Inside the heuristic arm (Detector E), sub-signal E.2 (redirection) fires
      only when redirection_flags.csv n_phrases >= 2. A single "email me /
      message me" mention no longer trips E.2. Detector E still fires if any of
      its other sub-signals (E.1 hr>=2, E.3 disposable, E.4 wallet, E.5 young
      domain, E.6 whois+hr) trips.

  Carve-out for BOTH A rules (verify mandate): A-stripping never demotes a
  campaign that carries a platform takedown (404/410) or a deterministic
  Detector-C edge. If either hard signal is present, the campaign's original A
  flag is preserved regardless of R1/R3.

  Detector B, R1  drop the side-channel disjunct
      Detector B fires on >= 4 of 10 yes ONLY. The historical q1+(q5|q7|q9)
      side-channel disjunct is removed. This also resolves the confirmed
      code-vs-paper mismatch (methodology.tex:141 already publishes ">=4/10,
      no side-channel disjunct"). No carve-out required (zero takedowns lost);
      demoted-but-corroborated labels are reported transparently.

  Detector C, rule (b) + takedown carve-out (audit + adversarial verify,
  2026-07-01): C counts toward the >=2-of-3 consensus for a campaign iff its
  identity cluster (i) contains at least one deterministic edge (D.2 email /
  D.3 phone / D.4 handle / D.5 domain), OR (ii) spans >= 2 platforms, OR
  (iii) has >= 3 members, OR (iv) the campaign itself was taken down by the
  platform. Rationale: 2-member same-platform name-only clusters are the
  weakest identity evidence (spot-check found them dominated by one person
  legitimately running a follow-up campaign, or name coincidence); the
  D.8 stylometric edge is a measured no-op (normalization defect: 100% of
  candidate edges pass the 0.95 cosine gate) and confers no evidence.

  FINAL corroboration definition (paper): a Fraud label is independently
  corroborated iff takedown OR sanitized reputation (rep_hard) OR the C
  cluster passes the evidence rule (i)-(iii).

Skipped (per verify verdicts):
  Detector A R2 (payment gate)  - NO-GO, non-reproducible root-cause figure.
  Detector B R2                 - alternative only; would leave paper mismatch.
  Detector B R3 (deception core)- NO-GO, destroys 11 takedown-confirmed fraud.

All paths resolve relative to this file, so it runs from anywhere.
"""
import csv, json, re, collections
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[2]            # ccs2026/
DET    = ROOT / "03_detection"
OUTDIR = ROOT / "03_detection" / "detectors" / "outputs"
INTEL  = DET / "intel"

A_FLAGS  = OUTDIR / "detector_A_flags.csv"              # reputation arm (VT/IPQS)
E_FLAGS  = OUTDIR / "detector_E_flags.csv"              # heuristic arm (merged into A)
D_FLAGS  = OUTDIR / "detector_D_flags.csv"              # Detector C (organizer identity)
B_FEATS  = DET / "llm" / "llm_features_qwen72b.csv"     # Detector B per-question labels
CONTACTS = INTEL / "campaign_contacts_llm.jsonl"        # url -> extracted emails
IPQS_EML = INTEL / "ipqs_email_results.jsonl"           # IPQS email cache
REDIR    = INTEL / "redirection_flags.csv"              # n_phrases per url

STRAT    = Path(__file__).resolve().parent / "consensus_corroboration_stratified.csv"
OUT_CSV  = Path(__file__).resolve().parent / "combined_hardened_consensus.csv"

IPQS_THRESHOLD = 85
VT_THRESHOLD   = 2

B_QCOLS = [
    "q1_external_payment", "q2_deadline_pressure", "q3_guilt_language",
    "q4_defensive_language", "q5_impersonation_no_consent",
    "q6_tragedy_exploitation", "q7_allocation_overpromise",
    "q8_fake_credential", "q9_external_verification", "q10_multi_channel",
]

# Generic-provider / social / payment / crowdfunding-platform mailbox domains.
# An IPQS email hit on one of these domains is not a per-campaign reputation
# signal (the domain is shared by millions of legitimate users).
GENERIC_MAIL_DOMAINS = {
    # generic webmail
    "gmail.com","googlemail.com","yahoo.com","yahoo.co.uk","yahoo.co.in","ymail.com",
    "rocketmail.com","hotmail.com","hotmail.co.uk","outlook.com","live.com","msn.com",
    "aol.com","icloud.com","me.com","mac.com","gmx.com","gmx.de","gmx.net","mail.com",
    "proton.me","protonmail.com","pm.me","zoho.com","yandex.com","yandex.ru","mail.ru",
    "web.de","t-online.de","free.fr","orange.fr","laposte.net","comcast.net","att.net",
    "verizon.net","sbcglobal.net","bellsouth.net","cox.net","charter.net","btinternet.com",
    "sky.com","virginmedia.com","googlemail.co.uk","fastmail.com","hey.com","tutanota.com",
    "tuta.com","hushmail.com","inbox.com","email.com",
    "163.com","126.com","qq.com","sina.com","foxmail.com","naver.com","hanmail.net",
    "rediffmail.com","hotmail.fr","hotmail.es","hotmail.it","hotmail.de","outlook.fr",
    "outlook.de","outlook.es","live.co.uk","live.fr","live.de","aol.co.uk","aim.com",
    # social / payment / crowdfunding platforms
    "facebook.com","fb.com","instagram.com","twitter.com","x.com","tiktok.com",
    "paypal.com","venmo.com","cash.app","cashapp.com","zellepay.com","wise.com",
    "gofundme.com","gogetfunding.com","justgiving.com","fundrazr.com","fundly.com",
    "spotfund.com","betterplace.org","kickstarter.com","indiegogo.com","patreon.com",
    "ko-fi.com","buymeacoffee.com","donorbox.org","classy.org","givebutter.com",
}

REAL_TLD_RE = re.compile(r"\.[a-z]{2,}$", re.I)


def is_true(x): return str(x).strip().lower() in ("1", "true", "yes")
def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def b_true(x):
    # IPQS booleans arrive as python bools when parsed from JSON
    return x is True or str(x).strip().lower() in ("true", "1", "yes")


def load_csv_index(path, key="url"):
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get(key): out[r[key]] = r
    return out


# ---------------------------------------------------------------- load sources
A_idx = load_csv_index(A_FLAGS)
E_idx = load_csv_index(E_FLAGS)
D_idx = load_csv_index(D_FLAGS)

# Detector C cluster-level evidence rule: cluster passes iff it has a
# deterministic edge anywhere, spans >=2 platforms, or has >=3 members.
DET_EDGES = {"D.2_email", "D.3_phone", "D.4_handle", "D.5_domain"}
cluster_has_det = collections.defaultdict(bool)
for r in D_idx.values():
    if not is_true(r.get("flag")): continue
    cid = r.get("cluster_id")
    sigs = {t.strip() for t in (r.get("fired_signals") or "").replace(";", ",").split(",")}
    if sigs & DET_EDGES:
        cluster_has_det[cid] = True

def c_rule_passes(d_row):
    """Evidence rule (i)-(iii) for the campaign's cluster."""
    if not d_row or not is_true(d_row.get("flag")):
        return False
    if cluster_has_det[d_row.get("cluster_id")]:
        return True
    if fnum(d_row.get("cluster_platforms")) >= 2:
        return True
    if fnum(d_row.get("cluster_size")) >= 3:
        return True
    return False

B_idx = {}
with open(B_FEATS, newline="") as f:
    for r in csv.DictReader(f):
        if r.get("url"): B_idx[r["url"]] = r

# url -> list of extracted emails
contacts = {}
with open(CONTACTS) as f:
    for line in f:
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        if d.get("url"): contacts[d["url"]] = d.get("emails") or []

# email (lowercased) -> IPQS result dict
ipqs_email = {}
with open(IPQS_EML) as f:
    for line in f:
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        e = (d.get("email") or "").lower()
        if e: ipqs_email[e] = d.get("result") or {}

# url -> n_phrases (redirection)
redir_n = {}
with open(REDIR, newline="") as f:
    for r in csv.DictReader(f):
        if r.get("url"):
            redir_n[r["url"]] = fnum(r.get("n_phrases"))

# stratified corroboration flags (this defines the corpus + hard signals)
strat = load_csv_index(STRAT)


# ------------------------------------------------- R1: email hit sanitization
def email_hit_survives(url):
    """True if this campaign retains a *sanitized* IPQS-email reputation hit."""
    for e in contacts.get(url, []):
        res = ipqs_email.get((e or "").lower())
        if not res: continue
        if fnum(res.get("fraud_score")) < IPQS_THRESHOLD: continue
        # sanitization gates
        if not b_true(res.get("valid")):        continue   # deliverable mailbox
        if not b_true(res.get("dns_valid")):    continue
        if b_true(res.get("risky_tld")):        continue   # real TLD
        if b_true(res.get("generic")):          continue   # not a role/generic box
        dom = (e or "").split("@")[-1].strip().lower()
        if not dom or not REAL_TLD_RE.search(dom): continue
        if dom in GENERIC_MAIL_DOMAINS:          continue
        return True
    return False


def reputation_flag_R1(url, a_row):
    """Reputation arm under R1. VT and IPQS-phone kept; IPQS-email sanitized."""
    vt   = fnum(a_row.get("vt_max_score"))    >= VT_THRESHOLD
    phone= fnum(a_row.get("ipqs_phone_max"))  >= IPQS_THRESHOLD
    email_raw = fnum(a_row.get("ipqs_email_max")) >= IPQS_THRESHOLD
    email = email_raw and email_hit_survives(url)
    return vt or phone or email


# ---------------------------------------------- R3: heuristic arm w/ multi-phrase
def heuristic_flag_R3(url, e_row):
    """Detector E under R3: E.2 requires n_phrases>=2; other sub-signals unchanged."""
    if not e_row:
        return False
    hr_n       = fnum(e_row.get("hr_pattern_count"))
    disposable = is_true(e_row.get("disposable_email"))
    wallet_max = fnum(e_row.get("wallet_reuse_max"))
    young      = is_true(e_row.get("has_young_domain"))
    privacy    = is_true(e_row.get("whois_privacy"))
    # E.2 redirection now gated at n_phrases>=2 (was: any redirection flag)
    redir_e2   = redir_n.get(url, 0.0) >= 2
    fired = (
        hr_n >= 2 or          # E.1
        redir_e2 or           # E.2 (hardened)
        disposable or         # E.3
        wallet_max >= 2 or    # E.4
        young or              # E.5
        (privacy and hr_n >= 1)  # E.6
    )
    return bool(fired)


# --------------------------------------------------------- B: >=4/10, no disjunct
def b_flag_R1(b_row):
    if str(b_row.get("ok")).lower() not in ("true", "1"):
        return False
    yeses = sum(1 for c in B_QCOLS if str(b_row.get(c, "")).lower() == "yes")
    return yeses >= 4


# --------------------------------------------------------- baseline recomputation
def b_flag_baseline(b_row):
    if str(b_row.get("ok")).lower() not in ("true", "1"):
        return False
    yeses = [c for c in B_QCOLS if str(b_row.get(c, "")).lower() == "yes"]
    if len(yeses) >= 4:
        return True
    return "q1_external_payment" in yeses and any(
        q in yeses for q in ("q5_impersonation_no_consent",
                             "q7_allocation_overpromise",
                             "q9_external_verification"))

def a_flag_baseline(url):
    a = A_idx.get(url) or {}
    e = E_idx.get(url) or {}
    return bool(int(a.get("flag", 0) or 0)) or bool(int(e.get("flag", 0) or 0))


# ----------------------------------------------------------------- main compute
urls = list(strat.keys())
rows = []
tallies = collections.Counter()

for url in urls:
    s = strat[url]
    takedown = is_true(s.get("takedown"))
    c_det    = is_true(s.get("c_deterministic"))
    carveout = takedown or c_det          # A-stripping protected campaigns

    a_row = A_idx.get(url) or {}
    e_row = E_idx.get(url) or {}
    d_row = D_idx.get(url) or {}
    b_row = B_idx.get(url) or {}

    # ---- baseline flags (shipped) ----
    A0 = a_flag_baseline(url)
    B0 = b_flag_baseline(b_row)
    C0 = bool(int(d_row.get("flag", 0) or 0))
    score0 = int(A0) + int(B0) + int(C0)
    fraud0 = score0 >= 2

    # ---- hardened A: R1 reputation + R3 heuristic, then carve-out ----
    repH  = reputation_flag_R1(url, a_row)
    heurH = heuristic_flag_R3(url, e_row)
    A_hard = repH or heurH
    if carveout and A0 and not A_hard:
        A_hard = True                    # protected: never demote hard-signal campaign
        tallies["A_carveout_saves"] += 1

    # ---- hardened B: >=4/10, no disjunct ----
    B_hard = b_flag_R1(b_row)

    # ---- hardened C: cluster evidence rule (b) + takedown carve-out ----
    c_rule = c_rule_passes(d_row)
    C_hard = C0 and (c_rule or takedown)
    if C0 and not c_rule and takedown:
        tallies["C_carveout_saves"] += 1

    scoreH = int(A_hard) + int(B_hard) + int(C_hard)
    fraudH = scoreH >= 2

    # final corroboration: takedown OR sanitized reputation OR rule-passing C
    corroborated = takedown or repH or (C0 and c_rule)

    rows.append(dict(
        url=url, platform=s.get("platform", ""),
        A0=int(A0), B0=int(B0), C0=int(C0), score0=score0, fraud0=int(fraud0),
        A_hard=int(A_hard), B_hard=int(B_hard), C_hard=int(C_hard),
        rep_hard=int(repH), heur_hard=int(heurH),
        c_rule=int(c_rule), corroborated=int(corroborated),
        score_hard=scoreH, fraud_hard=int(fraudH),
        takedown=int(takedown), c_deterministic=int(c_det),
        reputation=int(is_true(s.get("reputation"))),
        hard_corroboration=int(is_true(s.get("hard_corroboration"))),
        hard_strict=int(is_true(s.get("hard_strict"))),
        gated_tier=s.get("gated_tier", ""),
    ))

# ------------------------------------------------------------------ write CSV
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows: w.writerow(r)

# ------------------------------------------------------------------ measure
def pct(n, d): return f"{100*n/d:.1f}%" if d else "n/a"

N        = len(rows)
A0_n     = sum(r["A0"] for r in rows)
B0_n     = sum(r["B0"] for r in rows)
C0_n     = sum(r["C0"] for r in rows)
fraud0_n = sum(r["fraud0"] for r in rows)

Ah_n     = sum(r["A_hard"] for r in rows)
Bh_n     = sum(r["B_hard"] for r in rows)
Ch_n     = sum(r["C_hard"] for r in rows)
fraudH_n = sum(r["fraud_hard"] for r in rows)

# GoGetFunding slice
def ggf(r): return r["platform"] == "GoGetFunding"
ggf_rows   = [r for r in rows if ggf(r)]
ggf_n      = len(ggf_rows)
ggf_fraud0 = sum(r["fraud0"] for r in ggf_rows)
ggf_fraudH = sum(r["fraud_hard"] for r in ggf_rows)
# text-only among hardened GGF fraud = fraud & not corroborated (final defn)
ggf_fraudH_textonly = sum(1 for r in ggf_rows if r["fraud_hard"] and not r["corroborated"])
ggf_fraud0_textonly = sum(1 for r in ggf_rows if r["fraud0"] and not r["hard_corroboration"])

# recall cost = baseline-fraud that hardening demotes AND that carried hard corroboration
demoted = [r for r in rows if r["fraud0"] and not r["fraud_hard"]]
recall_cost_hard   = sum(1 for r in demoted if r["hard_corroboration"])
recall_cost_strict = sum(1 for r in demoted if r["hard_strict"])
recall_cost_takedown = sum(1 for r in demoted if r["takedown"])
recall_cost_cdet   = sum(1 for r in demoted if r["c_deterministic"])
recall_cost_rep    = sum(1 for r in demoted if r["reputation"])
demoted_textonly   = sum(1 for r in demoted if not r["hard_corroboration"])

# post-hoc corroboration gate (from stratified CSV): fraud & hard_corroboration
gate_corpus = sum(1 for r in rows if r["fraud0"] and r["hard_corroboration"])
gate_ggf    = sum(1 for r in ggf_rows if r["fraud0"] and r["hard_corroboration"])
# overlap: hardened fraud that also survives the gate
both        = sum(1 for r in rows if r["fraud_hard"] and r["hard_corroboration"])
hard_only   = sum(1 for r in rows if r["fraud_hard"] and not r["hard_corroboration"])

print("="*74)
print("COMBINED HARDENED CONSENSUS  (A:R1+R3 w/ carve-out,  B:R1 >=4/10)")
print("="*74)
print(f"Corpus campaigns: {N:,}")
print()
print("Per-detector fire (corpus):")
print(f"  A  baseline {A0_n:,}  ->  hardened {Ah_n:,}   (cut {A0_n-Ah_n:,})")
print(f"  B  baseline {B0_n:,}  ->  hardened {Bh_n:,}   (cut {B0_n-Bh_n:,})")
print(f"  C  baseline {C0_n:,}  ->  hardened {Ch_n:,}   (cluster evidence rule)")
print(f"  A carve-out saves (takedown/det-C protected): {tallies['A_carveout_saves']:,}")
print(f"  C carve-out saves (takedown):                 {tallies['C_carveout_saves']:,}")
corr_f  = sum(1 for r in rows if r["fraud_hard"] and r["corroborated"])
txt_f   = sum(1 for r in rows if r["fraud_hard"] and not r["corroborated"])
print(f"  FINAL fraud corroboration: {corr_f:,} corroborated + {txt_f:,} text-only "
      f"({pct(txt_f, corr_f+txt_f)} text-only)" if (corr_f+txt_f) else "")
print()
print("Fraud tier (score>=2 of 3):")
print(f"  baseline fraud:  {fraud0_n:,}   (expected 2,297)")
print(f"  hardened fraud:  {fraudH_n:,}   (cut {fraud0_n-fraudH_n:,})")
print()
print("GoGetFunding:")
print(f"  campaigns:       {ggf_n:,}")
print(f"  baseline fraud:  {ggf_fraud0:,}  (expected 1,664)   text-only {ggf_fraud0_textonly:,} = {pct(ggf_fraud0_textonly,ggf_fraud0)}")
print(f"  hardened fraud:  {ggf_fraudH:,}   text-only {ggf_fraudH_textonly:,} = {pct(ggf_fraudH_textonly,ggf_fraudH)}")
print()
print("Recall cost (baseline fraud demoted by hardening):")
print(f"  total demoted:               {len(demoted):,}")
print(f"    text-only (no hard):       {demoted_textonly:,}  ({pct(demoted_textonly,len(demoted))})")
print(f"    CORROBORATED lost (hard):  {recall_cost_hard:,}   <- recall cost")
print(f"      of which takedown:       {recall_cost_takedown:,}")
print(f"      of which deterministic-C:{recall_cost_cdet:,}")
print(f"      of which reputation:     {recall_cost_rep:,}")
print(f"    corroborated (STRICT-C):   {recall_cost_strict:,}")
print()
print("vs post-hoc corroboration gate:")
print(f"  gate keeps (corpus):  {gate_corpus:,}   (expected 1,062)")
print(f"  gate keeps (GGF):     {gate_ggf:,}   (expected 718)")
print(f"  hardened fraud:       {fraudH_n:,}  =  {both:,} corroborated + {hard_only:,} text-only-but-hardened")
print()
print(f"[written] {OUT_CSV}")
