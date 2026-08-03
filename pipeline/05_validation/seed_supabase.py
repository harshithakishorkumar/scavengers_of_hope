"""Seed the Supabase database with the 200 sample items + 600 assignment rows.

Run this once after creating the Supabase project and running supabase_schema.sql.
Set the env vars:
    SUPABASE_URL=https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=<service_role JWT>

Usage:
    pip install supabase
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python seed_supabase.py
"""
import json, os, sys
from pathlib import Path

try:
    from supabase import create_client
except ImportError:
    print("error: pip install supabase  (Python client)")
    sys.exit(1)

URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not URL or not KEY:
    print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required.")
    sys.exit(1)

HERE = Path(__file__).resolve().parent
samples = json.loads((HERE / "samples.json").read_text())
assignment = json.loads((HERE / "assignment.json").read_text())

print(f"connecting to {URL}")
client = create_client(URL, KEY)

print(f"inserting {len(samples)} items...")
# upsert keyed on item_id so re-running is idempotent
client.table("items").upsert(samples).execute()

assignment_rows = [
    {"item_id": iid, "annotator": a}
    for iid, trio in assignment.items() for a in trio
]
print(f"inserting {len(assignment_rows)} assignment rows...")
client.table("assignments").upsert(assignment_rows).execute()

print("done.")
