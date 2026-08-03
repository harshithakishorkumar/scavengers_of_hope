# Manual Validation Deploy Notes

End-to-end setup for the 6-annotator, 200-sample, 3-overlap validation pool.

## Architecture

```
+------------------+        +-----------------------+        +---------------------+
| validators (web) |  --->  | Vercel serverless     |  --->  | Supabase Postgres   |
| /validate.html   |  <---  | api/sample, /label,   |  <---  | items, assignments, |
|                  |        | /export, /progress    |        | labels              |
+------------------+        +-----------------------+        +---------------------+
```

- Static HTML at `tools/website/validate.html`
- Python serverless functions at `tools/website/api/*.py`
- Postgres tables in Supabase: `items`, `assignments`, `labels`
- Sample pool + 3-overlap assignment is built locally and pushed once via
  `05_validation/seed_supabase.py`

## One-time setup

1. **Build the pool** (already done):
   ```bash
   cd ccs2026
   python3 05_validation/build_sample_pool.py
   # writes 05_validation/{samples.json, assignment.json, assignment.csv, summary.txt}
   ```

2. **Create Supabase project** (free tier):
   - Sign up at https://supabase.com (use Padam or Bhupendra's account).
   - New project, name it `crowdfraud-validation`. Pick the closest region.
   - Go to **SQL Editor**, paste `05_validation/supabase_schema.sql`, run.

3. **Seed the database**:
   ```bash
   pip install supabase
   export SUPABASE_URL=https://<project-ref>.supabase.co
   export SUPABASE_SERVICE_ROLE_KEY=<service_role JWT>     # NOT anon key
   python3 05_validation/seed_supabase.py
   ```
   This is idempotent; safe to re-run.

4. **Configure Vercel project env vars**:
   - In the Vercel dashboard for the project, **Settings -> Environment Variables**:
     - `SUPABASE_URL` = the project URL
     - `SUPABASE_SERVICE_ROLE_KEY` = the service_role JWT
   - Apply to **Production** and **Preview**.

5. **Deploy**:
   ```bash
   cd ccs2026/tools/website
   vercel --prod
   ```
   Vercel detects `api/*.py` automatically and installs `api/requirements.txt`.

## How an annotator uses it

1. Visits `https://<your-vercel-domain>/validate`
2. Picks their name (Padam / Harshita / Danish / Bhupendra / Afsah / Chrysm)
3. Sees one campaign at a time with title, description, organizer, finance metadata, and platform
4. **Phase 1 — first-pass:** picks **Fraud / Suspicious / Unknown / Skip** (keyboard shortcuts 1-4) before any model output is shown
5. **Phase 2 — Claude reveal:** Claude Sonnet 4.6's verdict + confidence + 1-2 sentence reasoning fades in
6. Confirm (Enter) or revise (1-4); both first and final decisions are recorded, plus a `revised_after_llm` boolean
7. Confidence 1-5 (default 3) and optional notes per item
8. Auto-advance to next item; after 100 items, they see "Done"

The form remembers their name in localStorage so they can come back later.

## Downloading the labels

Anyone with the URL can hit:
```
https://<your-vercel-domain>/api/export
```
which returns `manual_validation_v4.csv` with columns:
`item_id, url, platform, title, aux_label, aux_score, annotator, decision, confidence, notes, created_at, updated_at`.

## Computing inter-rater agreement (after collection)

Once everyone is done, on the local box:
```bash
curl https://<your-vercel-domain>/api/export -o manual_validation_v4.csv
python3 05_validation/compute_kappa.py manual_validation_v4.csv
# emits: pairwise Cohen's kappa table, Fleiss' kappa for items with full coverage,
# % agreement with consensus, fraud-class precision and recall.
```

(`compute_kappa.py` will be added once we have labels to test against.)
