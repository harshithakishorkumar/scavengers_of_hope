// Scavengers of Hope — popup script (premium UI)

const API_BASE = "http://localhost:8000";

const SUPPORTED_PLATFORMS = new Set([
  "gofundme.com", "gogetfunding.com", "spotfund.com", "freefunder.com",
  "betterplace.org", "angelink.com", "experiment.com", "donorschoose.org",
  "ufandao.com", "my.ufandao.com", "seedandspark.com", "crowdfundr.com",
  "chuffed.org", "whydonate.com", "launchgood.com", "happypot.ch",
]);

const VERDICT = {
  fraud:      { label: "Fraud-shaped", icon: "ico-fraud" },
  suspicious: { label: "Suspicious",   icon: "ico-suspicious" },
  unknown:    { label: "Unknown",      icon: "ico-unknown" },
};

// Match detector name → SVG icon id from popup.html <defs>
const DETECTOR_ICONS = {
  "External signals (A)":     "ico-reputation",
  "Behavioral LLM (B)":       "ico-llm",
  "Organizer identity (C)":   "ico-network",
};

// ─── helpers ─────────────────────────────────────────────
function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return ""; }
}

function isSupported(host) {
  if (SUPPORTED_PLATFORMS.has(host)) return true;
  for (const p of SUPPORTED_PLATFORMS) {
    if (host.endsWith("." + p)) return true;
  }
  return false;
}

function showState(name) {
  for (const s of ["idle", "loading", "result", "error"]) {
    document.getElementById(`state-${s}`).hidden = (s !== name);
  }
}

function svgUse(symbolId) {
  return `<svg viewBox="0 0 24 24" fill="none"><use href="#${symbolId}"/></svg>`;
}

function el(tag, opts = {}) {
  const e = document.createElement(tag);
  if (opts.cls)   e.className = opts.cls;
  if (opts.text)  e.textContent = opts.text;
  if (opts.html)  e.innerHTML = opts.html;
  if (opts.href)  e.href = opts.href;
  if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) e.setAttribute(k, v);
  return e;
}

// Step the loading animation through three phases
function startLoaderSteps() {
  const steps = document.querySelectorAll(".loader-steps .step");
  let i = 0;
  steps.forEach((s, k) => s.classList.toggle("step-active", k === 0));
  const interval = setInterval(() => {
    i = (i + 1) % steps.length;
    steps.forEach((s, k) => s.classList.toggle("step-active", k === i));
  }, 1100);
  return () => clearInterval(interval);
}

// ─── render ──────────────────────────────────────────────
function renderVerdict(data) {
  // Normalize to a known verdict so an unexpected value (e.g. "clean") can't crash render.
  const verdict = VERDICT[data.verdict] ? data.verdict : "unknown";
  const vinfo = VERDICT[verdict];

  const card = document.getElementById("verdict-card");
  card.className = "verdict-card " + verdict;

  const svgEl = document.getElementById("verdict-svg");
  svgEl.innerHTML = `<use href="#${vinfo.icon}"/>`;

  document.getElementById("verdict-label").textContent = vinfo.label;
  document.getElementById("verdict-summary").textContent = data.summary || "";

  // Fired count pill
  const fired = (data.detectors || []).filter(d => d.fired).length;
  const pill = document.getElementById("fired-count");
  if (fired === 0) {
    pill.textContent = "0 signals";
    pill.className = "fired-pill zero";
  } else {
    pill.textContent = `${fired} signal${fired > 1 ? "s" : ""} fired`;
    pill.className = "fired-pill" + (verdict === "fraud" ? " fraud" : "");
  }

  // Detector list
  const list = document.getElementById("detectors");
  list.innerHTML = "";

  for (const d of (data.detectors || [])) {
    const li = el("li", { cls: "detector-item" + (d.fired ? " fired" : "") });

    const row = el("div", { cls: "detector-row" });

    const iconWrap = el("div", { cls: "detector-icon" });
    const iconId = DETECTOR_ICONS[d.name] || "ico-llm";
    iconWrap.innerHTML = svgUse(iconId);
    row.appendChild(iconWrap);

    row.appendChild(el("span", { cls: "detector-name", text: d.name }));
    row.appendChild(el("span", {
      cls: "detector-badge",
      text: d.fired ? "fired" : "clean",
    }));
    li.appendChild(row);

    if (d.fired && d.evidence) {
      li.appendChild(el("div", { cls: "detector-evidence", text: d.evidence }));
    }

    if (d.fired && d.details) {
      if (d.details.org_hint) {
        li.appendChild(el("div", { cls: "org-hint", text: d.details.org_hint }));
      }
      if (d.details.siblings && d.details.siblings.length > 0) {
        const wrap = el("div", { cls: "siblings-wrapper" });
        const toggle = el("button", { cls: "siblings-toggle" });
        toggle.innerHTML = `See ${d.details.siblings.length} other campaign${d.details.siblings.length > 1 ? "s" : ""} by this organizer <span class="caret">▾</span>`;

        const ul = el("ul", { cls: "siblings-list" });
        ul.hidden = true;

        for (const s of d.details.siblings) {
          const item = el("li");
          const a = el("a", {
            href: s.url,
            attrs: { target: "_blank", rel: "noopener noreferrer" },
          });
          if (s.platform) {
            a.appendChild(el("span", { cls: "sibling-tag", text: s.platform }));
          }
          a.appendChild(el("span", {
            cls: "sibling-title",
            text: s.title || s.url,
          }));
          item.appendChild(a);
          ul.appendChild(item);
        }

        toggle.addEventListener("click", () => {
          ul.hidden = !ul.hidden;
          toggle.classList.toggle("open", !ul.hidden);
        });

        wrap.appendChild(toggle);
        wrap.appendChild(ul);
        li.appendChild(wrap);
      }
    }

    list.appendChild(li);
  }

  // Meta strip
  const meta = document.getElementById("meta");
  meta.className = "meta-strip" + (data.cached ? "" : " live");
  meta.innerHTML = "";
  meta.appendChild(el("span", { cls: "meta-dot" }));
  const text = (data.cached ? "Cached result" : "Live analysis") +
               (data.platform ? `  •  ${data.platform}` : "");
  meta.appendChild(el("span", { text }));

  showState("result");
}

// ─── local cache ─────────────────────────────────────────
const CACHE_TTL_MS = 15 * 60 * 1000;   // 15 minutes
const CACHE_KEY = (url) => `verdict:${url}`;

async function getCached(url) {
  if (!chrome?.storage?.local) return null;
  const k = CACHE_KEY(url);
  const obj = await chrome.storage.local.get(k);
  const entry = obj[k];
  if (!entry) return null;
  if (Date.now() - entry.ts > CACHE_TTL_MS) return null;
  return entry.data;
}

async function setCached(url, data) {
  if (!chrome?.storage?.local) return;
  await chrome.storage.local.set({
    [CACHE_KEY(url)]: { ts: Date.now(), data },
  });
}

async function scan(url, { force = false } = {}) {
  if (!force) {
    const cached = await getCached(url);
    if (cached) {
      renderVerdict({ ...cached, cached: true });
      return;
    }
  }

  showState("loading");
  const stopSteps = startLoaderSteps();

  // Abort the request if the API/tunnel stalls, so the popup can never hang
  // forever on the loading animation — it falls through to the retry screen.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);

  try {
    const resp = await fetch(`${API_BASE}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`API ${resp.status}: ${txt.slice(0, 200)}`);
    }
    const data = await resp.json();
    stopSteps();
    await setCached(url, data);
    renderVerdict(data);
  } catch (e) {
    stopSteps();
    document.getElementById("error-msg").textContent =
      e.name === "AbortError"
        ? "The analysis service timed out. Make sure the API and SSH tunnel are running, then retry."
        : "The analysis service is unreachable. " + e.message;
    showState("error");
  } finally {
    clearTimeout(timeout);
  }
}

// ─── init ────────────────────────────────────────────────
async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url   = tab && tab.url ? tab.url : "";
  const host  = hostOf(url);

  document.getElementById("current-url").textContent =
    url ? (host || "unknown") : "No active tab.";

  const btn = document.getElementById("scan-btn");
  if (url && isSupported(host)) {
    btn.disabled = false;
    btn.querySelector(".btn-label").textContent = "Re-scan this campaign";
    btn.onclick = () => scan(url, { force: true });
    scan(url);   // auto-run on open; uses cache if fresh
  } else {
    btn.querySelector(".btn-label").textContent =
      "Not a supported crowdfunding page";
  }

  document.getElementById("retry-btn").onclick =
    () => scan(url, { force: true });
}

document.addEventListener("DOMContentLoaded", init);

// The side panel stays open across navigations and tab switches, so re-run the
// lookup whenever the background worker reports the active page changed. The
// toolbar popup never receives this message: it is torn down on every close.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "tab-changed") init();
});
