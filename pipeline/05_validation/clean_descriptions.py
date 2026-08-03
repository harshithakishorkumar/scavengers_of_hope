"""Clean platform UI chrome out of items.description for the validation pool.

The original v4 scrape captured page-level UI text (carousel nav, "Donated So
Far" widgets, "Follow this campaign...", organizer/donor footer, "Show more
Show less" toggles, "Read more" suffixes, betterplace preambles, etc.) into
the description field. The annotators see this in the form, which adds noise
to their judgment.

This script applies the same boilerplate stripping that
03_detection/detectors/template_detector_v4.py uses pre-encoding, but on the
*data at rest* in Supabase. After running, items.description holds clean
campaign content only.

Idempotent: reapplying yields the same cleaned text.

Usage:
    SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...
    python clean_descriptions.py
"""
import os, re, sys
from supabase import create_client


# === GoGetFunding ===
# Strip everything from start of string through "Follow this campaign Help
# this ongoing fundraising campaign by making a donation and spreading the
# word. Campaign Story" plus optional "Links:" — the opener carousel/nav,
# donation widget, and CTA boilerplate all sit before that landmark phrase.
GGF_HEADER = re.compile(
    r"^.{0,800}?Follow this campaign\s+"
    r"Help this ongoing fundraising campaign by making a donation and spreading the word\.\s*"
    r"Campaign Story(?:\s+Links:)?\s*",
    re.IGNORECASE | re.DOTALL,
)
# Fallback: paused-campaign and other GGF variants that lack the "Follow this
# campaign..." sentence. They start with a currency widget ("CA$1,281.00
# raised of CA$2,436.00 goal 52% Funded 21 Donors") and then jump straight
# to "Campaign Story".
GGF_WIDGET_HEADER = re.compile(
    r"^\s*(?:Previous\s+Next\s+)*"                # leading carousel nav (already removed earlier in some cases)
    r"(?:US|CA|AU|NZ|HK|SG)?[\$€£¥₱]\s*[\d,]+(?:\.\d+)?"
    r".{0,600}?"
    r"Campaign Story(?:\s+Links:)?\s*",
    re.IGNORECASE | re.DOTALL,
)
# "Donated So Far" widget for paused campaigns that have no goal exposed.
GGF_DONATED_HEADER = re.compile(
    r"^\s*(?:Previous\s+Next\s+)*"
    r"(?:US|CA|AU|NZ|HK|SG)?[\$€£¥₱]\s*[\d,]+(?:\.\d+)?\s+Donated\s+So\s+Far"
    r".{0,400}?"
    r"Campaign Story(?:\s+Links:)?\s*",
    re.IGNORECASE | re.DOTALL,
)
# Trailing organizer / donor / "send a message" footer.
GGF_TAIL = re.compile(
    r"\s*(?:Organizer\s+.{0,300}?)?Send a message\b.{0,80}?Send a message.*$",
    re.IGNORECASE | re.DOTALL,
)
# Paused-campaign banner that some GGF pages show.
GGF_PAUSED = re.compile(
    r"\s*No more donations are being accepted at this time\.?"
    r"\s*Please contact the campaign owner if you would like to discuss further funding opportunities\.?\s*",
    re.IGNORECASE,
)
# UI toggles.
SHOW_MORE_LESS = re.compile(r"\s*Show more\s+Show less\s*", re.IGNORECASE)
PREV_NEXT      = re.compile(r"^\s*(?:Previous\s+Next\s+)+", re.IGNORECASE)
READ_MORE      = re.compile(r"\s*Read more\s*$", re.IGNORECASE)

# === Betterplace ===
BP_OPENERS = re.compile(
    r"^\s*(?:<div>\s*)?(?:"
    r"Thank you for visiting my page at betterplace\.org"
    r"|Great that you visit my donation campaign at betterplace\.org"
    r"|Let[’']s achieve something great together!"
    r"|Sch[oö]n, dass [Dd]u meine Spendenaktion bei betterplace\.org"
    r"|Do we want to do something really good together\?"
    r"|Wollen wir zusammen was richtig Gutes machen\?"
    r"|Money can[’']t buy happiness they say"
    r")",
    re.IGNORECASE,
)
BP_PARA_BREAK = re.compile(
    r"(?:<br>\s*<br>|</div>\s*<div>|\n\s*\n|That is why I would be very grateful if you donated to my project now!?)",
    re.IGNORECASE,
)

# === GoFundMe ===
GFM_TRUST = re.compile(r"^\s*Donation protected\s*\n?", re.IGNORECASE)

# === HTML / CSS leak (any platform) ===
CSS_BLOCK  = re.compile(r"[a-z]+\.[a-z]\d+\s*\{[^}]*\}\s*", re.IGNORECASE)
HTML_BR    = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
HTML_BLOCK = re.compile(r"<\s*/?\s*(?:div|p|span|b|i|u|strong|em|font|table|tr|td)[^>]*>", re.IGNORECASE)
HTML_ANY   = re.compile(r"<[^>]+>")
ENT_NBSP   = re.compile(r"&nbsp;")
ENT_AMP    = re.compile(r"&amp;")
ENT_QUOT   = re.compile(r"&quot;")
ENT_NUM    = re.compile(r"&#\d+;")
WS_RUN     = re.compile(r"[ \t]+")
NL_RUN     = re.compile(r"\n{3,}")


def clean_desc(text: str, platform: str = "") -> str:
    if not isinstance(text, str) or not text:
        return ""
    out = text
    plat = (platform or "").lower()

    if plat == "gogetfunding" or "Follow this campaign" in out or "Campaign Story" in out:
        # Order matters: strip the leading carousel nav first so the widget
        # patterns can anchor at the start of the string.
        out = PREV_NEXT.sub("", out)
        # Try the strict landmark first, then progressively looser fallbacks.
        if "Follow this campaign" in out:
            out = GGF_HEADER.sub("", out)
        if out.startswith(("US$","CA$","AU$","NZ$","HK$","SG$","$","€","£","¥","₱")) and "Campaign Story" in out[:1000]:
            out = GGF_WIDGET_HEADER.sub("", out, count=1)
        if "Donated So Far" in out[:200]:
            out = GGF_DONATED_HEADER.sub("", out, count=1)
        out = GGF_TAIL.sub("", out)
        out = GGF_PAUSED.sub(" ", out)
        out = SHOW_MORE_LESS.sub(" ", out)

    if plat == "gofundme":
        out = GFM_TRUST.sub("", out)
        out = READ_MORE.sub("", out)

    if plat == "betterplace" or BP_OPENERS.match(out):
        m = BP_OPENERS.match(out)
        if m:
            rest = out[m.end():]
            bm = BP_PARA_BREAK.search(rest, 0, min(len(rest), 1500))
            if bm:
                out = rest[bm.end():]

    # Common cleanup
    out = CSS_BLOCK.sub(" ", out)
    out = HTML_BR.sub("\n", out)
    out = HTML_BLOCK.sub(" ", out)
    out = HTML_ANY.sub(" ", out)
    out = ENT_NBSP.sub(" ", out)
    out = ENT_AMP.sub("&", out)
    out = ENT_QUOT.sub('"', out)
    out = ENT_NUM.sub(" ", out)
    out = WS_RUN.sub(" ", out)
    out = NL_RUN.sub("\n\n", out)
    return out.strip()


def main():
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not sb_url or not sb_key:
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required.")
        sys.exit(1)

    sb = create_client(sb_url, sb_key)
    items = sb.table("items").select("item_id,platform,description").execute().data
    print(f"loaded {len(items)} items")

    fixed, untouched = 0, 0
    sample_lines = []
    for it in items:
        old = it.get("description") or ""
        new = clean_desc(old, it["platform"])
        if new == old.strip():
            untouched += 1
            continue
        sb.table("items").update({"description": new}).eq("item_id", it["item_id"]).execute()
        # Also drop any cached translation, since the source changed.
        sb.table("items").update({
            "description_translation": None,
            "description_language":    None,
        }).eq("item_id", it["item_id"]).execute()
        fixed += 1
        if len(sample_lines) < 6:
            sample_lines.append((it["item_id"], it["platform"], len(old), len(new), old[:80], new[:80]))

    print(f"\ncleaned: {fixed}    untouched: {untouched}")
    print()
    for iid, plat, old_len, new_len, old_head, new_head in sample_lines:
        print(f"  {iid} ({plat:>14}): {old_len:>5}ch -> {new_len:>5}ch")
        print(f"     OLD: {old_head!r}")
        print(f"     NEW: {new_head!r}")


if __name__ == "__main__":
    main()
