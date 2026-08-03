"""VirusTotal domain reputation lookup with key rotation and per-key pacing.

Free tier: 4 req/min, 500/day per key. With 11 keys -> ~44/min, 5,500/day.

Reads:  vt_pending_v6.txt
Writes: vt_domain_results.jsonl  (one record per domain, resumable)
"""
import json
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import requests

import api_keyring

DIR         = Path(__file__).resolve().parent
DOMAINS_IN  = DIR / "vt_pending_v6.txt"
RESULTS_OUT = DIR / "vt_domain_results.jsonl"
STATE_FILE  = DIR / "vt_key_state.json"

VT_KEYS = api_keyring.load_vt()

PER_KEY_RPM    = 4
QUOTA_MARKERS  = ("QuotaExceededError", "quota exceeded", "limit of requests", "DailyUserRateLimit")


def load_state():
    s = {"dead_keys": [], "current_key_idx": 0, "last_calls": {}}
    if STATE_FILE.exists():
        loaded = json.loads(STATE_FILE.read_text())
        s.update({k: loaded.get(k, s[k]) for k in s})
        s["last_calls"] = {int(k): deque(v, maxlen=PER_KEY_RPM)
                           for k, v in loaded.get("last_calls", loaded.get("last_call_times", {})).items()}
    return s


def save_state(s):
    STATE_FILE.write_text(json.dumps({
        "dead_keys":       s["dead_keys"],
        "current_key_idx": s["current_key_idx"],
        "last_calls":      {str(k): list(v) for k, v in s["last_calls"].items()},
    }, indent=2))


def next_key(s):
    n = len(VT_KEYS)
    for off in range(n):
        idx = (s["current_key_idx"] + off) % n
        if idx not in s["dead_keys"]:
            s["current_key_idx"] = (idx + 1) % n  # round-robin
            return idx
    return None


def wait_for_key(s, idx):
    dq = s["last_calls"].setdefault(idx, deque(maxlen=PER_KEY_RPM))
    if len(dq) >= PER_KEY_RPM:
        slack = 60 - (time.time() - dq[0])
        if slack > 0:
            time.sleep(slack + 0.5)


def vt_get(key, domain):
    r = requests.get(f"https://www.virustotal.com/api/v3/domains/{domain}",
                     headers={"x-apikey": key, "accept": "application/json"}, timeout=30)
    try:
        return {"status_code": r.status_code, **r.json()}
    except ValueError:
        return {"status_code": r.status_code, "error": "non-json", "text": r.text[:200]}


def summarize(resp):
    sc = resp.get("status_code")
    if sc == 404:
        return {"vt_status": "not_found", "vt_available": False}
    if sc != 200:
        return {"vt_status": "error", "vt_available": False, "vt_raw_status": sc}
    a = resp.get("data", {}).get("attributes", {})
    stats = a.get("last_analysis_stats", {})
    return {
        "vt_status":         "ok",
        "vt_available":      True,
        "vt_reputation":     a.get("reputation", 0),
        "vt_harmless":       stats.get("harmless", 0),
        "vt_malicious":      stats.get("malicious", 0),
        "vt_suspicious":     stats.get("suspicious", 0),
        "vt_undetected":     stats.get("undetected", 0),
        "vt_total_engines":  sum(stats.values()) if stats else 0,
        "vt_categories":     list((a.get("categories") or {}).values()),
        "vt_creation_date":  a.get("creation_date"),
        "vt_registrar":      a.get("registrar"),
    }


def already_done():
    if not RESULTS_OUT.exists():
        return set()
    return {json.loads(l).get("domain") for l in RESULTS_OUT.open() if l.strip()}


def main():
    if not DOMAINS_IN.exists():
        sys.exit(f"missing {DOMAINS_IN}")

    domains = [d.strip().lower() for d in DOMAINS_IN.read_text().splitlines() if d.strip()]
    done = already_done()
    todo = [d for d in domains if d not in done]
    print(f"total={len(domains)} done={len(done)} todo={len(todo)}")
    if not todo:
        return

    s = load_state()
    ok = nf = err = 0
    later = []

    for i, domain in enumerate(todo, 1):
        idx = next_key(s)
        if idx is None:
            print("all keys dead, stopping")
            break
        wait_for_key(s, idx)
        try:
            resp = vt_get(VT_KEYS[idx][1], domain)
        except requests.RequestException as e:
            print(f"  [{i}/{len(todo)}] {domain} net err: {e}")
            err += 1
            time.sleep(5)
            continue
        s["last_calls"].setdefault(idx, deque(maxlen=PER_KEY_RPM)).append(time.time())

        sc = resp.get("status_code")
        if sc == 429:
            later.append(domain)
            continue
        if sc in (401, 403):
            print(f"  key {idx} auth fail ({sc}), marking dead")
            s["dead_keys"].append(idx)
            continue
        if any(m.lower() in json.dumps(resp).lower() for m in QUOTA_MARKERS):
            print(f"  key {idx} quota exhausted, marking dead")
            s["dead_keys"].append(idx)
            continue

        summary = summarize(resp)
        rec = {"domain": domain, "checked_at": datetime.now().isoformat(),
               "api_key_index": idx, **summary,
               "raw_attributes": resp.get("data", {}).get("attributes") if sc == 200 else None}
        with RESULTS_OUT.open("a") as f:
            f.write(json.dumps(rec) + "\n")

        if summary["vt_status"] == "ok":
            ok += 1
            print(f"  [{i}/{len(todo)}] {domain:35s} rep={summary['vt_reputation']:>4d}"
                  f" mal={summary['vt_malicious']} susp={summary['vt_suspicious']}")
        elif summary["vt_status"] == "not_found":
            nf += 1
            print(f"  [{i}/{len(todo)}] {domain:35s} not_found")
        else:
            err += 1
            print(f"  [{i}/{len(todo)}] {domain:35s} err {sc}")

        if i % 20 == 0:
            save_state(s)

    if later:
        print(f"\nretrying {len(later)} domains that hit 429 after 65s")
        time.sleep(65)
        for j, domain in enumerate(later, 1):
            idx = next_key(s)
            if idx is None:
                break
            wait_for_key(s, idx)
            try:
                resp = vt_get(VT_KEYS[idx][1], domain)
            except requests.RequestException:
                err += 1
                continue
            s["last_calls"].setdefault(idx, deque(maxlen=PER_KEY_RPM)).append(time.time())
            sc = resp.get("status_code")
            if sc == 429 or sc in (401, 403):
                continue
            summary = summarize(resp)
            rec = {"domain": domain, "checked_at": datetime.now().isoformat(),
                   "api_key_index": idx, **summary}
            with RESULTS_OUT.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            ok += 1 if summary["vt_status"] == "ok" else 0
            nf += 1 if summary["vt_status"] == "not_found" else 0
            if j % 20 == 0:
                save_state(s)

    save_state(s)
    print(f"ok={ok} not_found={nf} err={err}")


if __name__ == "__main__":
    main()
