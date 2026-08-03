#!/usr/bin/env python3
"""
Corroboration stratification of the 3-detector consensus.

Motivation: GoGetFunding (largest platform) reported that Fraud-tier campaigns we
disclosed were known-legitimate. This script quantifies, from the shipped detector
outputs alone (NO ground truth required), how much of the Fraud tier rests on
independent/HARD evidence versus campaign-text-only agreement, and what the Fraud
tier looks like after a corroboration gate.

HARD signal (evidence outside the single campaign's own prose):
  - external reputation: VirusTotal >=2  OR  IPQS email >=85  OR  IPQS phone >=85
  - organizer identity : Detector C fired (cross-campaign identity/handle/domain reuse)
  - platform takedown  : HTTP 404/410 on May-2026 re-crawl
STRICT variant additionally requires Detector C to carry a *deterministic* edge
  (D.2 email / D.3 phone / D.4 handle / D.5 domain), not only the fuzzy-name D.1 edge.

TEXT-ONLY signal (inferred from the scraped description only):
  - Detector A heuristic arm (redirection-phrase scanner + hr_* regex)  [= consensus A minus reputation]
  - Detector B (LLM manipulation questions)

Outputs (written next to this script):
  - consensus_corroboration_stratified.csv  (per-campaign flags + gated tier)
  - corroboration_report.txt                (the printed report)
All paths are resolved relative to this file, so it runs from anywhere.
"""
import csv, collections, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # ccs2026/
DET  = ROOT / "03_detection"
CONSENSUS = DET / "consensus" / "consensus_3det_post_b_fixed.csv"
A_FLAGS   = DET / "detectors" / "outputs" / "detector_A_flags.csv"   # reputation arm only (VT/IPQS)
D_FLAGS   = DET / "detectors" / "outputs" / "detector_D_flags.csv"   # Detector C (organizer identity)
TAKEDOWN  = ROOT / "experiments" / "tracking" / "takedown_status.csv"
OUT_CSV   = Path(__file__).resolve().parent / "consensus_corroboration_stratified.csv"
OUT_TXT   = Path(__file__).resolve().parent / "corroboration_report.txt"

def is_true(x): return str(x).strip().lower() in ("1", "true", "yes")
def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

# ---- load external reputation arm (Detector A, VT/IPQS only) -------------------
reputation = {}   # url -> bool
with open(A_FLAGS, newline="") as f:
    for r in csv.DictReader(f):
        reputation[r["url"]] = (fnum(r.get("vt_max_score")) >= 2
                                or fnum(r.get("ipqs_email_max")) >= 85
                                or fnum(r.get("ipqs_phone_max")) >= 85)

# ---- load Detector C edges (organizer identity) -------------------------------
DETERMINISTIC = {"D.2_email", "D.3_phone", "D.4_handle", "D.5_domain"}
c_fired, c_deterministic = {}, {}
with open(D_FLAGS, newline="") as f:
    for r in csv.DictReader(f):
        fired = is_true(r.get("flag"))
        c_fired[r["url"]] = fired
        sigs = {t.strip() for t in (r.get("fired_signals") or "").replace(";", ",").split(",")}
        c_deterministic[r["url"]] = fired and bool(sigs & DETERMINISTIC)

# ---- load takedowns (404/410) -------------------------------------------------
taken_down = set()
with open(TAKEDOWN, newline="") as f:
    for r in csv.DictReader(f):
        if (r.get("category") or "").strip() == "taken_down":
            taken_down.add(r["url"])

# ---- walk the consensus -------------------------------------------------------
rows = []
with open(CONSENSUS, newline="") as f:
    for r in csv.DictReader(f):
        url, plat = r["url"], r["platform"]
        A, B, C = is_true(r["A"]), is_true(r["B"]), is_true(r["C"])
        score = int(r["score"])
        rep  = reputation.get(url, False)
        cdet = c_deterministic.get(url, False)
        tdn  = url in taken_down
        hard        = rep or C or tdn
        hard_strict = rep or cdet or tdn
        a_heuristic = A and not rep          # consensus-A minus the reputation arm
        rows.append(dict(platform=plat, url=url, A=A, B=B, C=C, score=score,
                         rep=rep, c_det=cdet, takedown=tdn,
                         a_heuristic=a_heuristic, hard=hard, hard_strict=hard_strict))

N = len(rows)
fraud = [x for x in rows if x["score"] >= 2]

# ---- write per-campaign CSV ---------------------------------------------------
with open(OUT_CSV, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["platform","url","A","B","C","score","reputation","c_deterministic",
                "takedown","a_heuristic","hard_corroboration","hard_strict",
                "gated_tier","gated_tier_strict"])
    for x in rows:
        def tier(hard):
            if x["score"] >= 2 and hard: return "fraud"
            if x["score"] >= 1:          return "suspicious"
            return "unknown"
        w.writerow([x["platform"],x["url"],int(x["A"]),int(x["B"]),int(x["C"]),x["score"],
                    int(x["rep"]),int(x["c_det"]),int(x["takedown"]),int(x["a_heuristic"]),
                    int(x["hard"]),int(x["hard_strict"]),tier(x["hard"]),tier(x["hard_strict"])])

# ---- report -------------------------------------------------------------------
def pct(n, d): return f"{100*n/d:5.1f}%" if d else "  n/a"
out = []
def p(s=""): out.append(s)

p("="*74)
p("CORROBORATION STRATIFICATION OF THE 3-DETECTOR CONSENSUS")
p("="*74)
p(f"Corpus campaigns:            {N:,}")
tiers = collections.Counter("fraud" if x["score"]>=2 else "suspicious" if x["score"]>=1 else "unknown" for x in rows)
p(f"  Fraud (score>=2):          {tiers['fraud']:,}")
p(f"  Suspicious (score==1):     {tiers['suspicious']:,}")
p(f"  Unknown (score==0):        {tiers['unknown']:,}")
p(f"Per-detector fire (corpus):  A={sum(x['A'] for x in rows):,}  "
  f"B={sum(x['B'] for x in rows):,}  C={sum(x['C'] for x in rows):,}")
p(f"  A reputation arm (VT/IPQS):{sum(x['rep'] for x in rows):,}")
p(f"  A heuristic arm (text):    {sum(x['a_heuristic'] for x in rows):,}")

p("")
p("-- Detector independence (corpus-wide) ---------------------------------")
Bset = sum(x["B"] for x in rows)
ah_b = sum(x["a_heuristic"] and x["B"] for x in rows)
ar_b = sum(x["rep"] and x["B"] for x in rows)
c_b  = sum(x["C"] and x["B"] for x in rows)
p(f"B flags:                     {Bset:,}")
p(f"A_heuristic AND B:           {ah_b:,}  ({pct(ah_b,Bset)} of B)   <- one text axis")
p(f"A_reputation AND B:          {ar_b:,}  ({pct(ar_b,Bset)} of B)")
p(f"C AND B:                     {c_b:,}  ({pct(c_b,Bset)} of B)")

p("")
p("-- Fraud tier stratified by corroboration ------------------------------")
nf = len(fraud)
n_rep = sum(x["rep"] for x in fraud)
n_c   = sum(x["C"] for x in fraud)
n_cd  = sum(x["c_det"] for x in fraud)
n_td  = sum(x["takedown"] for x in fraud)
n_hard= sum(x["hard"] for x in fraud)
n_hs  = sum(x["hard_strict"] for x in fraud)
n_text= nf - n_hard
n_ab  = sum(x["a_heuristic"] and x["B"] and not x["C"] for x in fraud)
p(f"Fraud campaigns:             {nf:,}")
p(f"  with external reputation:  {n_rep:,}  ({pct(n_rep,nf)})")
p(f"  with Detector C:           {n_c:,}  ({pct(n_c,nf)})")
p(f"    of which deterministic C:{n_cd:,}  ({pct(n_cd,nf)})")
p(f"  with platform takedown:    {n_td:,}  ({pct(n_td,nf)})")
p(f"  ANY hard corroboration:    {n_hard:,}  ({pct(n_hard,nf)})   <- GATED fraud")
p(f"  hard (STRICT C) :          {n_hs:,}  ({pct(n_hs,nf)})   <- gated-strict")
p(f"  TEXT-ONLY (no hard):       {n_text:,}  ({pct(n_text,nf)})   -> demote to Suspicious")
p(f"  A_heur+B only, no C:       {n_ab:,}  ({pct(n_ab,nf)})")

p("")
p("-- Per-platform fraud vs gated fraud -----------------------------------")
p(f"{'platform':<22}{'campaigns':>10}{'fraud':>8}{'gated':>8}{'text-only':>11}{'%txt':>7}")
byp = collections.defaultdict(lambda: [0,0,0,0])   # n, fraud, gated, textonly
for x in rows:
    b = byp[x["platform"]]; b[0]+=1
    if x["score"]>=2:
        b[1]+=1
        if x["hard"]: b[2]+=1
        else:         b[3]+=1
for plat,(n,fr,ga,txt) in sorted(byp.items(), key=lambda kv:-kv[1][1]):
    if fr==0: continue
    p(f"{plat[:21]:<22}{n:>10,}{fr:>8,}{ga:>8,}{txt:>11,}{pct(txt,fr):>7}")

report = "\n".join(out)
print(report)
OUT_TXT.write_text(report + "\n")
print(f"\n[written] {OUT_CSV.relative_to(ROOT)}")
print(f"[written] {OUT_TXT.relative_to(ROOT)}")
