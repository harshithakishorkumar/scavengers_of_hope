"""Precompute Claude Sonnet 4.6 verdicts for all 200 validation samples.

Runs once. Stores claude_label, claude_confidence, claude_reasoning,
claude_model in the items table. Idempotent — skips items that already have a
verdict. Uses prompt caching on the system prompt to keep cost minimal.

Cost estimate at Sonnet 4.6 pricing ($3/M input, $15/M output):
    ~700 input tokens + ~140 output tokens per item
    200 items -> ~$0.70 total (with prompt caching applied)

Usage:
    pip install anthropic supabase
    export ANTHROPIC_API_KEY=sk-ant-...
    export SUPABASE_URL=https://<ref>.supabase.co
    export SUPABASE_SERVICE_ROLE_KEY=eyJ...
    python precompute_claude.py
"""
import json, os, sys, time
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("error: pip install anthropic"); sys.exit(1)
try:
    from supabase import create_client
except ImportError:
    print("error: pip install supabase"); sys.exit(1)


MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a senior fraud analyst specializing in donation-based crowdfunding campaigns. Your job is to give a quick second-opinion verdict on a campaign so a human reviewer can decide whether you agree with them.

Ground every judgment in the literal content of the campaign text. Do not speculate beyond what the text says. The output schema has three classes:
  - "fraud":      observable fraud signals are present (off-platform payment, defensive language, vague beneficiary, manufactured urgency without verifiable detail)
  - "suspicious": some concerning signals but not enough to call fraud outright
  - "unknown":    no strong signals either way; could be a legitimate appeal but you cannot verify, OR the text is too thin / ambiguous to judge

Output VALID JSON ONLY, no prose outside the JSON, conforming exactly to:
  {"label": "fraud" | "suspicious" | "unknown",
   "confidence": 1 | 2 | 3 | 4 | 5,
   "reasoning": "<one to two short sentences citing the most decisive signal(s)>"}

Decisive signals to cite when present:
  - external-payment requests (CashApp, Venmo, Telegram DM, crypto wallet, IBAN)
  - defensive language ("this is not a scam", "I am genuine")
  - vague or untraceable beneficiary
  - guilt or urgency pressure with no verifiable detail
  - missing organizer-beneficiary relationship disclosure
Counter-signals when present:
  - specific verifiable institution (named hospital, school, registered charity)
  - itemized fund usage with concrete amounts
  - clearly disclosed organizer-beneficiary relationship
  - references to records, news articles, photos
"""

USER_TEMPLATE = """Title: {title}
Platform: {platform}
Goal: {goal}
Raised: {raised}
Organizer: {organizer}

Description:
{description}
"""


def fmt_money(amt, cur):
    if amt is None or amt == "":
        return "(unknown)"
    try:
        return f"{cur or ''}{float(amt):,.0f}"
    except (TypeError, ValueError):
        return str(amt)


def label_one(client, system_blocks, item):
    user = USER_TEMPLATE.format(
        title=(item.get("title") or "(no title)")[:300],
        platform=item.get("platform") or "(unknown)",
        goal=fmt_money(item.get("goal_amount"), item.get("currency")),
        raised=fmt_money(item.get("amount_raised"), item.get("currency")),
        organizer=item.get("organizer_name") or "(unknown)",
        description=(item.get("description") or "(no description)")[:6000],
    )

    msg = client.messages.create(
        model=MODEL,
        max_tokens=300,
        temperature=0.0,
        system=system_blocks,
        messages=[{"role": "user", "content": user}],
    )

    text = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
    # Best-effort JSON extraction (Claude sometimes wraps in ```json ... ```)
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    parsed = json.loads(text)
    return {
        "claude_label":      parsed["label"].lower(),
        "claude_confidence": int(parsed["confidence"]),
        "claude_reasoning":  parsed["reasoning"][:1000],
        "claude_model":      MODEL,
    }


def main():
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    ak     = os.environ.get("ANTHROPIC_API_KEY")
    if not (sb_url and sb_key and ak):
        print("error: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY required.")
        sys.exit(1)

    sb = create_client(sb_url, sb_key)
    cl = anthropic.Anthropic(api_key=ak)

    items = sb.table("items").select("*").execute().data
    print(f"loaded {len(items)} items from supabase")

    todo = [i for i in items if not i.get("claude_label")]
    print(f"{len(todo)} items need a Claude verdict")

    if not todo:
        return

    # Anthropic prompt caching: mark the system prompt as cacheable across calls
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT,
         "cache_control": {"type": "ephemeral"}}
    ]

    failed = []
    t0 = time.time()
    for n, item in enumerate(todo, 1):
        try:
            verdict = label_one(cl, system_blocks, item)
            sb.table("items").update(verdict).eq("item_id", item["item_id"]).execute()
            print(f"  [{n:>3}/{len(todo)}] {item['item_id']}  {verdict['claude_label']:>10}  conf={verdict['claude_confidence']}")
        except Exception as e:
            print(f"  [{n:>3}/{len(todo)}] {item['item_id']}  FAIL: {e}")
            failed.append((item["item_id"], str(e)))

    print(f"\ndone in {time.time() - t0:.0f}s. failures: {len(failed)}")
    for iid, err in failed[:10]:
        print(f"  {iid}: {err[:200]}")


if __name__ == "__main__":
    main()
