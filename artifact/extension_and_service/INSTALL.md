# Installing the Scavengers of Hope extension (dev mode)

The extension talks to a FastAPI backend. The default build points at
`http://127.0.0.1:8000`. Pick the option below that matches where you're
running Chrome.

## Option A — Chrome on the same machine as the API

If Chrome runs on crawlbox0 itself, everything just works. Start the
API and load the extension:

```bash
# Start the API (one-time setup already done)
cd "/home/C00621463/DonationScam - CCS Remote/ccs2026/framework/api"
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open Chrome → `chrome://extensions/` → toggle on **Developer mode**
→ click **Load unpacked** → select
`/home/C00621463/DonationScam - CCS Remote/ccs2026/framework/extension/`.

## Option B — Chrome on your laptop, API on crawlbox0 (recommended)

Forward the API port over SSH so your laptop's Chrome can hit it as if
it were local:

```bash
# Run this on your LAPTOP (not on crawlbox0)
ssh -N -L 8000:127.0.0.1:8000 C00621463@crawlbox0.cmix.louisiana.edu
```

Leave that terminal open. Then on your laptop:

1. `git clone` or `scp -r` the extension folder to your local machine:
   ```bash
   scp -r C00621463@crawlbox0.cmix.louisiana.edu:"'/home/C00621463/DonationScam - CCS Remote/ccs2026/framework/extension'" ~/scavengers-of-hope
   ```
2. Open Chrome → `chrome://extensions/`
3. Toggle on **Developer mode** (top-right)
4. Click **Load unpacked**
5. Select the `~/scavengers-of-hope/` folder
6. Pin the extension icon to the toolbar (puzzle-piece menu → pin)

## Trying it

1. Visit any campaign URL on one of the 15 supported platforms.
   Quick test URLs you can copy-paste into Chrome:
   - `https://www.betterplace.org/de/projects/148925` (A fired)
   - `https://www.betterplace.org/de/projects/142342` (D fired)
   - `https://www.betterplace.org/de/projects/10082`  (clean)
2. Click the toolbar icon.
3. Popup opens with the campaign verdict.

## Troubleshooting

**"Could not contact the analysis service" in the popup.**
The extension can't reach the API. If you're on Option B, confirm the
SSH tunnel is still up (`curl http://localhost:8000/health` on your
laptop should return JSON). If you're on Option A, confirm the
uvicorn process is still running.

**Popup says "Not a supported crowdfunding page".**
The current tab isn't on one of the 15 platforms. Try one of the test
URLs above.

**Permission prompt mentioning broad host access.**
Shouldn't happen — the extension only uses `activeTab`. If Chrome
shows a host-permission prompt, double-check `manifest.json` hasn't
been edited.

## Updating the extension after code changes

`chrome://extensions/` → find the entry → click the circular reload
arrow. No reinstall needed.
