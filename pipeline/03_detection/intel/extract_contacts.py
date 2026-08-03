"""
Detector A — LLM Contact Extraction
====================================
Llama-3.1-8B-Instruct in JSON mode + literal-source verification.

For each campaign in input/campaigns.csv, extracts emails, phones,
payment handles, social handles, URLs, names, and locations that
LITERALLY appear in the description. Output is one JSON record per line
in output/campaign_contacts_llm.jsonl.

RESUMABLE: re-reads existing output file at startup and skips URLs
already processed. Ship the starter file at output/campaign_contacts_llm.jsonl
(which contains 100,294 already-processed v4 rows) and only the ~44k delta
will be processed.

BACKENDS:
    BACKEND=vllm   (recommended for H100/A100, ~150 campaigns/min)
    BACKEND=ollama (fallback, ~20-40 campaigns/min)

ENV VARS:
    BACKEND=vllm|ollama          (default: vllm if available)
    INPUT_CSV=path               (default: ../input/campaigns.csv)
    OUTPUT_JSONL=path            (default: output/campaign_contacts_llm.jsonl)
    WORKERS=N                    (default: 32 for vllm, 8 for ollama)
    VLLM_URL=http://...          (default: http://localhost:8000/v1/chat/completions)
    OLLAMA_URL=http://...        (default: http://localhost:11434/api/generate)
    LLM_MODEL=hf/name|ollama-tag (default: NousResearch/Meta-Llama-3.1-8B-Instruct or llama3.1:8b-instruct-q4_K_M)

Usage:
    bash run.sh
"""
from __future__ import annotations
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
INPUT_CSV    = Path(os.environ.get("INPUT_CSV", HERE.parent / "input" / "campaigns.csv"))
OUTPUT_JSONL = Path(os.environ.get("OUTPUT_JSONL", HERE / "output" / "campaign_contacts_llm.jsonl"))
OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

BACKEND     = os.environ.get("BACKEND", "vllm").lower()
WORKERS     = int(os.environ.get("WORKERS", "32" if BACKEND == "vllm" else "8"))
VLLM_URL    = os.environ.get("VLLM_URL", "http://localhost:8000/v1/chat/completions")
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL       = os.environ.get("LLM_MODEL",
                             "llama3.1:8b-instruct-q4_K_M" if BACKEND == "ollama"
                             else "NousResearch/Meta-Llama-3.1-8B-Instruct")
MAX_DESC_CHARS = 6000   # p95 of v4 corpus = ~3,954 chars

PROMPT = """\
Extract contact details and identifying signals from the crowdfunding campaign
text below. Capture only what LITERALLY APPEARS in the text. Never invent,
paraphrase, or echo anything that is not present.

Output JSON with these exact keys, each a list of strings:

emails           - every email address that appears in the text
phones           - every phone number that appears in the text (keep original formatting)
payment_handles  - payment-account identifiers actually written in the text:
                   Cash App tags of the form $Username (must include the dollar
                   sign and a real username), Venmo handles starting with @,
                   paypal.me/<user>, wise.com payment links, IBAN numbers, BTC
                   or ETH wallet addresses, account numbers explicitly labeled
                   as donation/payment accounts. Do NOT include currency
                   amounts like "$25" or "$0.25", platform names with no
                   handle, or generic words.
social_handles   - social-media usernames written in the text: Telegram @user,
                   Discord user#1234, Instagram @user, WhatsApp/Signal/Skype
                   handles, etc.
urls             - every URL or bare domain that appears in the text
names            - every person's name explicitly mentioned
locations        - every place name explicitly mentioned (city, country, region,
                   hospital, address)

Hard rules:
- Only include items that literally appear in the text. Never invent. Never
  copy from these instructions.
- For each key, return [] if nothing of that kind is in the text. Never use
  "n/a", "none", "null", "", "-", or placeholder strings.
- Do not put a URL or domain into emails. Do not put an email into urls.
- Output JSON only, no commentary.

Campaign title: {title}

Campaign description:
{description}
"""

EMPTY = {"emails": [], "phones": [], "payment_handles": [], "social_handles": [],
         "urls": [], "names": [], "locations": []}

PLACEHOLDERS = {"", "n/a", "na", "none", "null", "nil", "-", "--", "tbd", "tba",
                "not provided", "not applicable", "not specified", "not available",
                "unknown", "your_email_here", "example@example.com"}


def already_done(path: Path) -> set[str]:
    if not path.exists(): return set()
    done = set()
    with path.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
                if d.get("url"): done.add(d["url"])
            except json.JSONDecodeError:
                pass
    return done


def _call_vllm_once(prompt: str, timeout: int = 120) -> tuple[str, int]:
    t0 = time.time()
    r = requests.post(VLLM_URL, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }, timeout=timeout)
    r.raise_for_status()
    ms = int((time.time() - t0) * 1000)
    return r.json()["choices"][0]["message"]["content"], ms


def _call_ollama_once(prompt: str, timeout: int = 180) -> tuple[str, int]:
    t0 = time.time()
    r = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 1024},
    }, timeout=timeout)
    r.raise_for_status()
    ms = int((time.time() - t0) * 1000)
    return r.json().get("response", ""), ms


# Retry layer + circuit breaker. Prevents the v4 failure mode where Ollama
# died mid-run and we wrote 63k empty error rows.
_CONSECUTIVE_BACKEND_FAILS = 0
_CIRCUIT_THRESHOLD = int(os.environ.get("CIRCUIT_THRESHOLD", "50"))
_BACKOFF_SECONDS = [1, 5, 30]   # 3 attempts total: immediate, then 1s, 5s, 30s


def llm(prompt: str) -> tuple[str, int]:
    """Retry on transient backend failures (connection refused, timeouts).
    Trips circuit breaker if backend has been dead for too long."""
    global _CONSECUTIVE_BACKEND_FAILS
    fn = _call_vllm_once if BACKEND == "vllm" else _call_ollama_once
    last_err = None
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            out = fn(prompt)
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
            # 5xx is retryable; 4xx is a real bug
            if e.response is not None and 500 <= e.response.status_code < 600:
                last_err = e
                if attempt < len(_BACKOFF_SECONDS):
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
            raise
    # All retries exhausted -> backend is sick
    _CONSECUTIVE_BACKEND_FAILS += 1
    if _CONSECUTIVE_BACKEND_FAILS >= _CIRCUIT_THRESHOLD:
        print(f"\n!!! CIRCUIT BREAKER: {_CONSECUTIVE_BACKEND_FAILS} consecutive backend failures.", flush=True)
        print(f"!!! Backend ({BACKEND}) appears dead. Exiting so we don't write garbage rows.", flush=True)
        print(f"!!! Restart the model server and re-run this script; it will resume from where it stopped.", flush=True)
        sys.exit(2)
    raise last_err


def clean_list(lst, source_text: str, kind: str) -> list[str]:
    """Anti-hallucination: keep only items that literally appear in source."""
    if not isinstance(lst, list): return []
    out = []
    source_lc = source_text.lower()
    for x in lst:
        if not isinstance(x, str): continue
        s = x.strip()
        if not s or s.lower() in PLACEHOLDERS: continue
        if s.lower() not in source_lc: continue   # literal-source check
        out.append(s)
    # Dedupe preserving order
    seen = set(); deduped = []
    for x in out:
        kx = x.lower()
        if kx not in seen:
            seen.add(kx); deduped.append(x)
    return deduped


def process_one(row: dict) -> dict:
    url = row["url"]
    title = (row.get("title") or "")[:500]
    desc = (row.get("description") or "")[:MAX_DESC_CHARS]
    if not desc.strip():
        return {"url": url, "ok": True, "ms": 0, **EMPTY}
    prompt = PROMPT.format(title=title, description=desc)
    try:
        raw, ms = llm(prompt)
        data = json.loads(raw)
    except Exception as e:
        return {"url": url, "ok": False, "err": str(e)[:300], "ms": 0, **EMPTY}
    source = title + "\n" + desc
    out = {"url": url, "ok": True, "ms": ms}
    for k in EMPTY:
        out[k] = clean_list(data.get(k, []), source, k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="cap rows (for testing)")
    args = ap.parse_args()

    df = pd.read_csv(INPUT_CSV)
    print(f"loaded {len(df):,} campaigns from {INPUT_CSV}", flush=True)
    done = already_done(OUTPUT_JSONL)
    print(f"already-done in output:  {len(done):,}", flush=True)
    todo = [r for _, r in df.iterrows() if r.get("url") and r["url"] not in done]
    if args.max > 0: todo = todo[:args.max]
    print(f"to process:              {len(todo):,}", flush=True)
    print(f"backend={BACKEND}  workers={WORKERS}  model={MODEL}", flush=True)

    if not todo:
        print("nothing to do.", flush=True); return

    t0 = time.time(); n = 0; n_err = 0
    with OUTPUT_JSONL.open("a") as fh, \
         ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(process_one, dict(r)): r for r in todo}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as e:
                res = {"url": "?", "ok": False, "err": str(e)[:300], "ms": 0, **EMPTY}
            fh.write(json.dumps(res) + "\n"); fh.flush()
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
    print(f"output: {OUTPUT_JSONL}", flush=True)


if __name__ == "__main__":
    main()
