# Intel — VirusTotal, IPQualityScore, Chainabuse

External-API reputation lookups that feed Detector A.

## Scripts

| Script | Does | Reads | Writes |
|---|---|---|---|
| `extract_domains_v4_delta.py` | extract unique domains from filtered campaigns, subtract allowlist + already-done | `02_data_filtration/filtered_dataset_v4.csv`, `vt_domain_results.jsonl` | `unknown_domains_v4_pending.txt` |
| `vt_lookup.py` | VirusTotal domain reputation, 11-key round-robin rotation | `unknown_domains_v4_pending.txt` | `vt_domain_results.jsonl`, `vt_key_state.json` |
| `ipqs_lookup.py` | IPQS email + phone fraud scoring, 28-key rotation with daily caps (199 emails/19 phones per key) | `unique_emails_v4_delta.txt`, `unique_phones_v4_delta.txt` | `ipqs_email_results.jsonl`, `ipqs_phone_results.jsonl`, `ipqs_key_state.json` |
| `chainabuse_lookup.py` | Community crypto-abuse cross-check (legacy; needs refactor for v4) | crypto addresses | `chainabuse_results.jsonl` |

## Rate limits

- **VirusTotal (free tier):** 4 requests/min/key, 500/day/key. With 11 keys: 44/min, 5,500/day. Expected wall-clock for 1,699 domains ≈ 40 min.
- **IPQualityScore:** 5,000 lookups/month/key. We have 28 keys. Enough for our 325 emails + 2,149 phones.
- **Chainabuse:** Rate-limited internally; used for spot checks, not bulk.

## State files

- `vt_key_state.json` — per-key last call times, dead keys
- `ipqs_key_state.json` — per-key month-rolling usage
- `.chainabuse_key` — single API key, 600 perms

## Output format

Each `*_results.jsonl` is append-only, one JSON record per lookup. Idempotent: scripts skip entries already in the output file.

## Thresholds for "bad"

- **VT:** `vt_malicious ≥ 1` OR `vt_suspicious ≥ 3` (see `rebuild_detector_A.py`)
- **IPQS:** `fraud_score ≥ 75` (high-risk threshold)

## Running

```bash
# Run from this folder — relative paths resolve from __file__
python3 vt_lookup.py       # writes vt_domain_results.jsonl, resumable
python3 ipqs_lookup.py emails  # or phones / all — writes ipqs_*_results.jsonl, resumable
```
