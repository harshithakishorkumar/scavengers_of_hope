"""On-demand VT / IPQS lookups with strict rate limiting.

Used only when a fresh URL references an entity (domain or email) that is
NOT already in our cached results. Results from live calls are written back
into db.{vt_results, ipqs_email_results} so the next request for the same
entity is served from cache.

Caps (configurable via env vars):
  SOFH_VT_DAILY_CAP        (default 20)  total live VT calls per day
  SOFH_IPQS_DAILY_CAP      (default 20)  total live IPQS calls per day
  SOFH_LIVE_PER_REQUEST    (default  3)  max live calls per /analyze request

State file: /tmp/sofh_live_reputation_state.json (resets daily).
Keys: reused from ccs2026/03_detection/intel/{vt_lookup.py, ipqs_lookup.py}.
"""
import json
import os
import sys
import time
from collections import deque
from datetime import date
from pathlib import Path

import requests

# Import keys from the existing intel scripts
INTEL = Path(__file__).resolve().parent.parent.parent / "03_detection" / "intel"
sys.path.insert(0, str(INTEL))
try:
    from vt_lookup import VT_KEYS              # noqa: E402
except Exception as e:
    print(f"[live_reputation] failed to import VT_KEYS: {e}")
    VT_KEYS = []
try:
    from ipqs_lookup import API_KEYS as IPQS_KEYS   # noqa: E402
except Exception as e:
    print(f"[live_reputation] failed to import IPQS keys: {e}")
    IPQS_KEYS = []


STATE_FILE = Path("/tmp/sofh_live_reputation_state.json")

VT_DAILY_CAP     = int(os.environ.get("SOFH_VT_DAILY_CAP",     "20"))
IPQS_DAILY_CAP   = int(os.environ.get("SOFH_IPQS_DAILY_CAP",   "20"))
LIVE_PER_REQUEST = int(os.environ.get("SOFH_LIVE_PER_REQUEST", "3"))

VT_PER_KEY_RPM   = 4     # free-tier rate limit
IPQS_PER_KEY_RPM = 4     # conservative; IPQS doesn't publish strict RPM


# ------------------ state ------------------
def _load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text())
        except Exception:
            s = {}
    else:
        s = {}
    today = date.today().isoformat()
    if s.get("date") != today:
        s = {"date": today,
             "vt_count":      0, "ipqs_count":    0,
             "vt_key_idx":    0, "ipqs_key_idx":  0,
             "vt_last_calls": {}, "ipqs_last_calls": {}}
    return s


def _save_state(s):
    try:
        STATE_FILE.write_text(json.dumps(s))
    except Exception:
        pass


def _wait_for_key_rpm(last_calls_map: dict, idx: int, rpm: int):
    """Respect per-key minute-window throttle."""
    key = str(idx)
    history = last_calls_map.setdefault(key, [])
    now = time.time()
    history = [t for t in history if now - t < 60]
    if len(history) >= rpm:
        time.sleep(60 - (now - history[0]) + 0.5)
        now = time.time()
        history = [t for t in history if now - t < 60]
    history.append(now)
    last_calls_map[key] = history


# ------------------ VT ------------------
def lookup_domain_live(db, domain: str) -> dict | None:
    """Live VT lookup for one domain. Returns the cache-shaped doc on
    success, None on cap-hit / error / not-found."""
    if not VT_KEYS or not domain:
        return None
    s = _load_state()
    if s["vt_count"] >= VT_DAILY_CAP:
        return None
    n_keys = len(VT_KEYS)
    for off in range(n_keys):
        idx = (s["vt_key_idx"] + off) % n_keys
        _wait_for_key_rpm(s["vt_last_calls"], idx, VT_PER_KEY_RPM)
        key = VT_KEYS[idx][1]
        try:
            r = requests.get(
                f"https://www.virustotal.com/api/v3/domains/{domain}",
                headers={"x-apikey": key, "accept": "application/json"},
                timeout=12,
            )
        except requests.RequestException:
            continue
        s["vt_count"] += 1
        s["vt_key_idx"] = (idx + 1) % n_keys
        if r.status_code == 429 or r.status_code in (401, 403):
            continue
        if r.status_code == 404:
            db.vt_results.update_one(
                {"domain": domain},
                {"$set": {"domain": domain, "vt_status": "not_found",
                          "checked_at_live": True}},
                upsert=True)
            _save_state(s)
            return None
        if r.status_code == 200:
            try:
                attrs = (r.json().get("data") or {}).get("attributes") or {}
                stats = attrs.get("last_analysis_stats") or {}
            except ValueError:
                continue
            doc = {
                "domain":         domain,
                "vt_status":      "ok",
                "vt_malicious":   stats.get("malicious",   0),
                "vt_suspicious":  stats.get("suspicious",  0),
                "vt_harmless":    stats.get("harmless",    0),
                "vt_undetected":  stats.get("undetected",  0),
                "vt_reputation":  attrs.get("reputation",  0),
                "checked_at_live": True,
            }
            db.vt_results.update_one({"domain": domain}, {"$set": doc},
                                     upsert=True)
            _save_state(s)
            return doc
    _save_state(s)
    return None


# ------------------ IPQS email ------------------
def lookup_email_live(db, email: str) -> dict | None:
    if not IPQS_KEYS or not email:
        return None
    s = _load_state()
    if s["ipqs_count"] >= IPQS_DAILY_CAP:
        return None
    n_keys = len(IPQS_KEYS)
    for off in range(n_keys):
        idx = (s["ipqs_key_idx"] + off) % n_keys
        _wait_for_key_rpm(s["ipqs_last_calls"], idx, IPQS_PER_KEY_RPM)
        key = IPQS_KEYS[idx] if isinstance(IPQS_KEYS[idx], str) else IPQS_KEYS[idx][1]
        try:
            r = requests.get(
                f"https://www.ipqualityscore.com/api/json/email/{key}/{email}",
                timeout=12,
            )
        except requests.RequestException:
            continue
        s["ipqs_count"] += 1
        s["ipqs_key_idx"] = (idx + 1) % n_keys
        if r.status_code != 200:
            continue
        try:
            j = r.json()
        except ValueError:
            continue
        if not j.get("success", True):
            continue
        doc = {
            "email":  email,
            "result": j,
            "checked_at_live": True,
        }
        db.ipqs_email_results.update_one({"email": email}, {"$set": doc},
                                         upsert=True)
        _save_state(s)
        return doc
    _save_state(s)
    return None


# ------------------ caps inspection ------------------
def live_quota_state() -> dict:
    s = _load_state()
    return {
        "date":           s["date"],
        "vt_calls_used":  s["vt_count"],
        "vt_calls_cap":   VT_DAILY_CAP,
        "ipqs_calls_used": s["ipqs_count"],
        "ipqs_calls_cap":  IPQS_DAILY_CAP,
        "per_request_cap": LIVE_PER_REQUEST,
    }
