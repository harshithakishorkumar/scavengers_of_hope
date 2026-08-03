"""IPQS email/phone reputation lookup with rotating free-tier keys.

Usage:
    python ipqs_lookup.py {emails|phones|all}
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import api_keyring

DIR = Path(__file__).resolve().parent
STATE_FILE = DIR / "ipqs_key_state.json"

API_KEYS = api_keyring.load_ipqs()

DEAD_MARKERS = ("insufficient credits", "exceeded your request quota", "Invalid API key")

# Stop one call below the free-tier daily limit so IPQS never returns the
# quota-exceeded response, which sends a notification email to the key holder.
MODES = {
    "emails": dict(field="email", cap=199,
                   in_path=DIR / "ipqs_email_pending_v6.txt",
                   out_path=DIR / "ipqs_email_results.jsonl",
                   url="https://www.ipqualityscore.com/api/json/email/{key}/{val}",
                   params={"timeout": 7, "fast": "false", "abuse_strictness": 0}),
    "phones": dict(field="phone", cap=19,
                   in_path=DIR / "ipqs_phone_pending_v6.txt",
                   out_path=DIR / "ipqs_phone_results.jsonl",
                   url="https://www.ipqualityscore.com/api/json/phone/{key}/{val}",
                   # No 'country' param: phones in the pending list are now
                   # E.164-normalized (+CC...), so IPQS reads the country from
                   # the prefix itself. Hard-coding country=US,CA biased every
                   # non-NA number.
                   params={}),
}


def today():
    return datetime.utcnow().strftime("%Y-%m-%d")


def load_state():
    s = {"dead_keys": [], "current_key_idx": 0, "usage_today": {}, "usage_date": today()}
    if STATE_FILE.exists():
        s.update(json.loads(STATE_FILE.read_text()))
        s.setdefault("usage_today", {})
        s.setdefault("usage_date", today())
    if s["usage_date"] != today():
        s.update(usage_today={}, dead_keys=[], usage_date=today())
    return s


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))


def usage(s, idx):
    return s["usage_today"].get(str(idx), 0)


def next_key(s, cap):
    n = len(API_KEYS)
    for off in range(n):
        idx = (s["current_key_idx"] + off) % n
        if idx not in s["dead_keys"] and usage(s, idx) < cap:
            s["current_key_idx"] = idx
            return idx
    return None


def already_done(out, field):
    if not out.exists():
        return set()
    return {json.loads(l).get(field) for l in out.open() if l.strip()}


def run(mode):
    cfg = MODES[mode]
    items = [l.strip() for l in cfg["in_path"].read_text().splitlines() if l.strip()]
    done = already_done(cfg["out_path"], cfg["field"])
    todo = [x for x in items if x not in done]
    print(f"[{mode}] total={len(items)} done={len(done)} todo={len(todo)}")
    if not todo:
        return

    s = load_state()
    cap = cfg["cap"]
    idx = next_key(s, cap)
    if idx is None:
        print("all keys capped or dead, try tomorrow")
        return

    ok = err = 0
    for i, val in enumerate(todo, 1):
        if usage(s, idx) >= cap:
            save_state(s)
            idx = next_key(s, cap)
            if idx is None:
                print("all keys capped for today, stopping")
                break

        try:
            r = requests.get(cfg["url"].format(key=API_KEYS[idx], val=val),
                             params=cfg["params"], timeout=15).json()
        except requests.RequestException as e:
            print(f"  [{i}/{len(todo)}] {val}: net err {e}")
            err += 1
            time.sleep(2)
            continue

        if any(m in r.get("message", "") for m in DEAD_MARKERS):
            print(f"  [{i}/{len(todo)}] key {idx} dead: {r['message'][:60]}")
            s["dead_keys"].append(idx)
            idx = next_key(s, cap)
            if idx is None:
                break
            continue

        s["usage_today"][str(idx)] = usage(s, idx) + 1
        with cfg["out_path"].open("a") as f:
            f.write(json.dumps({cfg["field"]: val,
                                "checked_at": datetime.now().isoformat(),
                                "api_key_index": idx, "result": r}) + "\n")
        ok += 1
        print(f"  [{i}/{len(todo)}] {val:38s} fraud={r.get('fraud_score', '?')} "
              f"(key {idx} {usage(s, idx)}/{cap})")
        if ok % 25 == 0:
            save_state(s)
        time.sleep(1)

    save_state(s)
    print(f"[{mode}] ok={ok} err={err}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("emails", "phones", "all"):
        sys.exit("Usage: python ipqs_lookup.py {emails|phones|all}")
    for m in (("emails", "phones") if sys.argv[1] == "all" else (sys.argv[1],)):
        run(m)
