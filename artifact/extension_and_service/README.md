# Scavengers of Hope — Detection Framework

Productionization of the 4-detector fraud-detection pipeline as an
end-user-facing browser extension backed by a FastAPI service.

For the research methodology, see the rest of `ccs2026/`. This directory
holds only the deployable artifact.

## Layout

```
framework/
├── api/                 FastAPI backend (runs on crawlbox0)
│   ├── app.py           HTTP service entry point
│   ├── analyze.py       Detector dispatch + verdict assembly
│   ├── scrape.py        Live-scrape for URLs not in our corpus
│   └── requirements.txt
└── extension/           Chrome MV3 extension
    ├── manifest.json
    ├── popup.html       Verdict UI (opens when the icon is clicked)
    ├── popup.js
    ├── content.js       (Optional; only runs on supported platforms)
    └── icons/
```

## What it does

1. User visits a crowdfunding campaign on one of 15 supported platforms.
2. User clicks the Scavengers of Hope icon in their browser toolbar.
3. Extension sends the current tab's URL to the backend.
4. Backend either:
   * returns the cached verdict if the URL is one of the 100,294 campaigns
     we already analyzed, or
   * scrapes the page live, runs Detectors A / C / D, and returns a fresh
     verdict (Detector B requires GPU and is served from cache only).
5. Extension renders the verdict + evidence in the popup.

**Manual scan only.** The extension never sends URLs to the backend
without an explicit user click.

## Running the API locally

```bash
cd ccs2026/framework/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Then:

```bash
curl -X POST http://localhost:8000/analyze \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.gofundme.com/f/example-campaign"}'
```

## Loading the extension for development

1. Open `chrome://extensions/`
2. Toggle on Developer Mode
3. Click "Load unpacked"
4. Select `ccs2026/framework/extension/`
5. Visit any supported crowdfunding page and click the extension icon
