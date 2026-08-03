"""Loads API keys from a gitignored secrets file instead of from source.

Key ORDER is load-bearing: vt_key_state.json and ipqs_key_state.json address
keys by list index (current_key_idx, dead_keys), so a reordered secrets file
silently retargets rotation state onto the wrong keys. Append new keys at the
end, never in the middle.

Override the secrets path with DONATIONSCAM_KEYS when running off-host.
"""
import json
import os
from pathlib import Path

DIR = Path(__file__).resolve().parent
SECRETS = Path(os.environ.get("DONATIONSCAM_KEYS", DIR / "api_keys.json"))


def load(section):
    if not SECRETS.exists():
        raise SystemExit(
            f"No secrets file at {SECRETS}.\n"
            f"Copy {DIR / 'api_keys.example.json'} to api_keys.json, fill in the\n"
            f"keys, and chmod 600 it. Or point DONATIONSCAM_KEYS at another file."
        )
    data = json.loads(SECRETS.read_text())
    if section not in data or not data[section]:
        raise SystemExit(f"{SECRETS} has no '{section}' keys.")
    return data[section]


def load_vt():
    """Returns [(label, key), ...] in the file's order."""
    return [tuple(entry) for entry in load("virustotal")]


def load_ipqs():
    """Returns [key, ...] in the file's order."""
    return load("ipqs")
