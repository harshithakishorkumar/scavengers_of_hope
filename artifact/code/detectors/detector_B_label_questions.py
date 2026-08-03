"""
Detector B — Qwen-72B 10-Question Teacher Labeling
====================================================
Qwen-2.5-72B-Instruct-AWQ scores every campaign in input/campaigns.csv
against 10 fraud-archetype yes/no/unclear questions.

Runs on the ENTIRE corpus from scratch (no caching from old v4 runs) for
methodological consistency across all 10 questions.

RESUMABLE: re-reads existing output CSV at startup and skips URLs already
processed. Safe to kill and restart anytime.

BACKEND: vLLM with continuous batching (REQUIRED for 72B on H100). Uses
AWQ 4-bit quantization (~40 GB VRAM on H100/A100 80GB; fits comfortably
in 95 GB).

ENV VARS:
    INPUT_CSV=path        (default: ../input/campaigns.csv)
    OUTPUT_CSV=path       (default: output/llm_features_qwen72b.csv)
    VLLM_URL=http://...   (default: http://localhost:8000/v1/chat/completions)
    LLM_MODEL=hf/name     (default: Qwen/Qwen2.5-72B-Instruct-AWQ)
    WORKERS=N             (default: 16; safe for AWQ continuous batching)

Usage:
    bash run.sh
"""
from __future__ import annotations
import argparse, csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
INPUT_CSV  = Path(os.environ.get("INPUT_CSV", HERE.parent / "input" / "campaigns.csv"))
OUTPUT_CSV = Path(os.environ.get("OUTPUT_CSV", HERE / "output" / "llm_features_qwen72b.csv"))
OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8000/v1/chat/completions")
MODEL    = os.environ.get("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")
WORKERS  = int(os.environ.get("WORKERS", "16"))
MAX_DESC_CHARS = 12000

QUESTIONS = """\
Q1 — External Payment
Does the description include external payment links or instructions to pay
outside the platform? Look for: PayPal.me links, CashApp ($cashtag), Venmo
(@username), Zelle, crypto wallet addresses, bank account/IBAN numbers,
Western Union, MoneyGram, or instructions like "DM me to donate" or
"contact me for payment details".

Q2 — Deadline Pressure
Does the description use deadline-pressure phrases such as "last chance",
"only X days left", "before it's too late", "deadline tomorrow", "running
out of time", or specific countdown language?

Q3 — Guilt Language
Does the description use guilt or moral pressure aimed at potential donors?
Look for phrases like "if you have a heart", "anyone with compassion",
"true Christians/Muslims/believers would", "shame on anyone who scrolls
past", "you would help if you cared".

Q4 — Defensive Language
Does the description contain defensive statements about the campaign's
legitimacy, denials of being a scam, or rebuttals to skeptics? Examples:
"this is not a scam", "I am a real person", "I would never lie about
this", "people have accused me but...".

Q5 — Impersonation Without Consent
Does the description claim to raise funds on behalf of a named third party
(not the organizer themselves) while showing NO indication that this person
or their family authorized the campaign? Look for: organizer's name differs
from the named beneficiary AND there is no quoted statement from the
beneficiary or family, no "authorized by [family]", no joint photo with
caption acknowledging the campaign, no link to a verified family account,
and no statement that the beneficiary is aware of the campaign.

Q6 — Recent Tragedy Exploitation
Does the description reference a specific recent public tragedy by name
(named disaster like "Camp Mystic flood" or "Hurricane Helene", mass-
casualty event, or named viral crime victim) while claiming personal stake
(relative, friend, witness) WITHOUT naming the specific victim and the
organizer's verifiable relation to them? Look for: vague phrases like
"someone I knew", "a friend of mine", "my cousin" without giving the
victim's name, or invoking the disaster name as the entire justification
for the campaign.

Q7 — Allocation Overpromise
Does the description promise that 100%, every dollar, every penny, or all
proceeds will go to a specific cause, WITHOUT naming the registered
charity, hospital, legal entity, or specific individual who would receive
the funds? Look for: sweeping promises like "100% of donations will go to
building the wall", "every penny will fund cancer research", "all proceeds
to charity", without naming the receiving organization, EIN, or destination
account.

Q8 — Fake Credential Claim
Does the description claim a specific credential or institutional status
(military veteran, doctor/nurse/EMT, registered charity / 501(c)(3) /
nonprofit, licensed practitioner) WITHOUT providing the verifiable
identifier? Look for: claims like "decorated veteran", "registered
nonprofit", "licensed nurse", "longtime fundraiser for charities" without
giving unit + years served, EIN / charity registration number, state of
incorporation, license number, or other concrete details a reader could
verify.

Q9 — External Verification Request
Does the description ask donors to message, DM, PM, email, or text the
organizer to RECEIVE PROOF or VERIFICATION (photos, medical records,
receipts, hospital paperwork, account info), instead of providing that
evidence in the description itself? This is distinct from Q1, which is
about external PAYMENT. Look for: "DM me for receipts", "PM me to see
medical records", "email me for proof", "message me for hospital paperwork",
"contact me privately to verify".

Q10 — Multi-Channel Solicitation
Does the description tell readers they can ALSO donate via channels outside
the platform — IN ADDITION TO this fundraiser's normal donate button —
such as Facebook group, in-person handoff, phone call, mail, church
collection, text message, or multiple linked campaigns? Look for: "you can
also send to my Venmo @x", "churches collecting cash donations at our
address", "call this number to donate by phone", "see our other GoFundMe
at...", "contact us at [address] for in-person donations".
"""

SYSTEM = ("You are an expert at detecting deceptive crowdfunding campaigns. "
          "Read the description carefully and answer each yes/no question. "
          "Reply only with valid JSON in the format below — no extra text. "
          "Use \"unclear\" only when the description does not give enough "
          "information to decide.")

PROMPT = f"""{{system}}

{QUESTIONS}

Campaign title: {{title}}

Campaign description:
{{description}}

Output JSON in exactly this format:
{{{{
  "q1_external_payment":          "yes/no/unclear",
  "q2_deadline_pressure":         "yes/no/unclear",
  "q3_guilt_language":            "yes/no/unclear",
  "q4_defensive_language":        "yes/no/unclear",
  "q5_impersonation_no_consent":  "yes/no/unclear",
  "q6_tragedy_exploitation":      "yes/no/unclear",
  "q7_allocation_overpromise":    "yes/no/unclear",
  "q8_fake_credential":           "yes/no/unclear",
  "q9_external_verification":     "yes/no/unclear",
  "q10_multi_channel":            "yes/no/unclear"
}}}}
"""

QKEYS = [
    "q1_external_payment", "q2_deadline_pressure", "q3_guilt_language",
    "q4_defensive_language", "q5_impersonation_no_consent",
    "q6_tragedy_exploitation", "q7_allocation_overpromise",
    "q8_fake_credential", "q9_external_verification", "q10_multi_channel",
]


def normalize(v):
    if not isinstance(v, str): return "unclear"
    s = v.strip().lower()
    if s in ("yes", "y", "true", "1"): return "yes"
    if s in ("no",  "n", "false", "0"): return "no"
    return "unclear"


def _call_vllm_once(prompt: str, timeout: int = 120) -> tuple[str, int]:
    t0 = time.time()
    r = requests.post(VLLM_URL, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }, timeout=timeout)
    r.raise_for_status()
    ms = int((time.time() - t0) * 1000)
    return r.json()["choices"][0]["message"]["content"], ms


# Retry layer + circuit breaker. Prevents writing thousands of "ok=false"
# rows if vLLM dies mid-run (the v4 Ollama failure mode).
_CONSECUTIVE_BACKEND_FAILS = 0
_CIRCUIT_THRESHOLD = int(os.environ.get("CIRCUIT_THRESHOLD", "50"))
_BACKOFF_SECONDS = [1, 5, 30]


def call_vllm(prompt: str, timeout: int = 120) -> tuple[str, int]:
    global _CONSECUTIVE_BACKEND_FAILS
    last_err = None
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            out = _call_vllm_once(prompt, timeout=timeout)
            _CONSECUTIVE_BACKEND_FAILS = 0
            return out
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            last_err = e
            if attempt < len(_BACKOFF_SECONDS):
                time.sleep(_BACKOFF_SECONDS[attempt])
                continue
            break
        except requests.HTTPError as e:
            if e.response is not None and 500 <= e.response.status_code < 600:
                last_err = e
                if attempt < len(_BACKOFF_SECONDS):
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
            raise
    _CONSECUTIVE_BACKEND_FAILS += 1
    if _CONSECUTIVE_BACKEND_FAILS >= _CIRCUIT_THRESHOLD:
        print(f"\n!!! CIRCUIT BREAKER: {_CONSECUTIVE_BACKEND_FAILS} consecutive backend failures.", flush=True)
        print(f"!!! vLLM appears dead. Exiting so we don't write garbage rows.", flush=True)
        print(f"!!! Restart vLLM and re-run; the script resumes from where it stopped.", flush=True)
        sys.exit(2)
    raise last_err


def already_done(path: Path) -> set[str]:
    if not path.exists(): return set()
    done = set()
    with path.open() as fh:
        rdr = csv.DictReader(fh)
        for row in rdr:
            if row.get("url"): done.add(row["url"])
    return done


def process_one(row: dict) -> dict:
    url = row.get("url") or ""
    title = (row.get("title") or "")[:500]
    desc  = (row.get("description") or "")[:MAX_DESC_CHARS]
    base = {"url": url, "ok": True, "ms": 0,
            **{k: "unclear" for k in QKEYS}, "err": ""}
    if not desc.strip():
        return base
    prompt = PROMPT.format(system=SYSTEM, title=title, description=desc)
    try:
        raw, ms = call_vllm(prompt)
        data = json.loads(raw)
        base["ms"] = ms
        for k in QKEYS:
            base[k] = normalize(data.get(k, "unclear"))
    except Exception as e:
        base["ok"] = False
        base["err"] = str(e)[:300]
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    args = ap.parse_args()

    df = pd.read_csv(INPUT_CSV)
    print(f"loaded {len(df):,} campaigns from {INPUT_CSV}", flush=True)
    done = already_done(OUTPUT_CSV)
    print(f"already-done in output:  {len(done):,}", flush=True)
    todo = [r for _, r in df.iterrows() if r.get("url") and r["url"] not in done]
    if args.max > 0: todo = todo[:args.max]
    print(f"to process:              {len(todo):,}", flush=True)
    print(f"model={MODEL}  workers={WORKERS}", flush=True)

    if not todo:
        print("nothing to do.", flush=True); return

    fieldnames = ["url", "ok", "ms", *QKEYS, "err"]
    write_header = not OUTPUT_CSV.exists() or OUTPUT_CSV.stat().st_size == 0

    t0 = time.time(); n = 0; n_err = 0
    with OUTPUT_CSV.open("a", newline="") as fh, \
         ThreadPoolExecutor(max_workers=WORKERS) as ex:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        if write_header: w.writeheader(); fh.flush()
        futures = {ex.submit(process_one, dict(r)): r for r in todo}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as e:
                res = {"url": "?", "ok": False, "err": str(e)[:300], "ms": 0,
                       **{k: "unclear" for k in QKEYS}}
            w.writerow(res); fh.flush()
            n += 1
            if not res.get("ok"): n_err += 1
            if n % 50 == 0:
                elapsed = time.time() - t0
                rate = n / elapsed if elapsed else 0
                eta = (len(todo) - n) / rate if rate else 0
                print(f"  [{n:>6,}/{len(todo):,}]  rate={rate:.1f}/sec  "
                      f"err={n_err}  eta={eta/60:.0f}min", flush=True)

    print(f"\ndone. processed={n:,}  errors={n_err}  "
          f"total_time={(time.time()-t0)/60:.1f}min", flush=True)
    print(f"output: {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
