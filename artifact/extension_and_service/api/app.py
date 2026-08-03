"""Scavengers of Hope — fraud-detection API.

Exposes a single POST /analyze endpoint that the browser extension calls
with the URL of the campaign the user is viewing. The endpoint returns a
verdict (clean / suspicious / fraud), the list of detectors that fired,
and a short evidence snippet for the user-facing popup.

Looks up the URL in the existing campaigns collection first (cached
verdict, instant). If the URL is not in the corpus, falls back to a live
scrape + detector pass (handled by scrape.py / analyze.py).
"""
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Reuse the existing mongo connection helper from the rest of the repo
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "db"))
from connection import get_db  # noqa: E402

from analyze import analyze_url  # noqa: E402


SUPPORTED_PLATFORMS = {
    "gofundme.com", "gogetfunding.com", "spotfund.com", "freefunder.com",
    "betterplace.org", "angelink.com", "experiment.com", "donorschoose.org",
    "ufandao.com", "my.ufandao.com", "seedandspark.com", "crowdfundr.com",
    "chuffed.org", "whydonate.com", "launchgood.com", "happypot.ch",
}


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Scavengers of Hope",
    description="Fraud-detection API for donation-based crowdfunding campaigns.",
    version="0.1.0",
)
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # browser extensions send Origin: chrome-extension://...
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class DetectorVerdict(BaseModel):
    name:     str
    fired:    bool
    evidence: str | None  = None
    details:  dict | None = None    # detector-specific extras (siblings, org_hint, ...)


class AnalyzeResponse(BaseModel):
    url: str
    verdict: str                 # "fraud" | "suspicious" | "clean" | "unknown"
    cached: bool                 # True if the URL was already in our corpus
    platform: str | None = None
    detectors: list[DetectorVerdict] = []
    summary: str = ""


db = get_db()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "scavengers-of-hope"}


@app.get("/supported-platforms")
async def supported_platforms():
    return {"platforms": sorted(SUPPORTED_PLATFORMS)}


@app.get("/quota")
async def quota():
    """Inspect today's live-reputation quota usage."""
    from live_reputation import live_quota_state
    return live_quota_state()


@app.post("/analyze", response_model=AnalyzeResponse)
@limiter.limit("30/hour")        # ~30 scans per hour per IP
def analyze(request: Request, body: AnalyzeRequest):
    # Sync def: FastAPI runs this in a threadpool so the blocking scrape +
    # reputation lookups inside analyze_url don't freeze the event loop
    # (otherwise one in-flight scan stalls /health and every other request).
    url = str(body.url)
    result = analyze_url(db, url, SUPPORTED_PLATFORMS)
    return result


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return HTTPException(status_code=429, detail="rate limit exceeded")
