"""Compute description-text features and donor-pattern features.

Description features (corpus-wide, all 100,294 campaigns):
  description_features.char_count
  description_features.emoji_count
  description_features.uppercase_ratio
  description_features.exclamation_count
  description_features.emergency_keyword_count    // urgent, dying, dire, asap, ...
  description_features.deadline_keyword_count     // deadline, last day, expires, ...

Donor-pattern features (Spotfund only ~12k campaigns with donations[]):
  donor_features.total_donations
  donor_features.anon_donations
  donor_features.anon_ratio
  donor_features.unique_donor_avatars
  donor_features.avatar_diversity_ratio       // unique / total (lower = sock-puppet-shape)
  donor_features.unique_donor_names
  donor_features.self_loop_count               // donations whose donor name matches the organizer

Idempotent. --dry-run for preview.
"""
import argparse
import html
import re
import sys
import unicodedata
from pathlib import Path

from pymongo import UpdateOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection import get_db


# --- text-feature regexes ---
HTML_TAG_RE = re.compile(r"<[^>]+>")
EMOJI_RE    = re.compile(
    r"["
    r"\U0001F300-\U0001F5FF"   # symbols & pictographs
    r"\U0001F600-\U0001F64F"   # emoticons
    r"\U0001F680-\U0001F6FF"   # transport
    r"\U0001F700-\U0001F77F"
    r"\U0001F780-\U0001F7FF"
    r"\U0001F800-\U0001F8FF"
    r"\U0001F900-\U0001F9FF"   # supplemental
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF"
    r"\U00002600-\U000026FF"   # misc symbols
    r"\U00002700-\U000027BF"   # dingbats
    r"]"
)
EMERGENCY_RE = re.compile(
    r"\b(?:urgent(?:ly)?|emergency|dying|critical(?:ly)?|asap|immediate(?:ly)?|"
    r"dire|crisis|life[\s-]?threatening|terminal(?:ly)?|please\s+help|save\s+(?:me|us|him|her))\b",
    re.IGNORECASE,
)
DEADLINE_RE = re.compile(
    r"\b(?:deadline|last\s+(?:day|chance|hours?)|final\s+hours?|by\s+(?:friday|monday|"
    r"tuesday|wednesday|thursday|saturday|sunday)|expires?\s+(?:today|tomorrow|soon)|"
    r"running\s+out\s+of\s+time|time\s+is\s+running\s+out|only\s+\d+\s+days?\s+left)\b",
    re.IGNORECASE,
)


def text_features(html_str):
    if not html_str:
        return None
    text = HTML_TAG_RE.sub(" ", html_str)
    text = html.unescape(text)
    n_chars = len(text)
    if n_chars == 0:
        return {"char_count": 0, "emoji_count": 0, "uppercase_ratio": 0.0,
                "exclamation_count": 0, "emergency_keyword_count": 0,
                "deadline_keyword_count": 0}
    n_emoji = len(EMOJI_RE.findall(text))
    n_upper = sum(1 for c in text if c.isupper())
    n_alpha = sum(1 for c in text if c.isalpha())
    upper_ratio = (n_upper / n_alpha) if n_alpha else 0.0
    n_excl  = text.count("!")
    n_emerg = len(EMERGENCY_RE.findall(text))
    n_dead  = len(DEADLINE_RE.findall(text))
    return {
        "char_count":              n_chars,
        "emoji_count":             n_emoji,
        "uppercase_ratio":         round(upper_ratio, 4),
        "exclamation_count":       n_excl,
        "emergency_keyword_count": n_emerg,
        "deadline_keyword_count":  n_dead,
    }


def normalize_name(s):
    if not s: return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s.strip()


DEFAULT_AVATAR_RE = re.compile(
    r"default_user_photo|default_anonymous_user_photo|profile_default|"
    r"avatar_placeholder|default_avatar|user_default",
    re.IGNORECASE,
)


def donor_features(donations, organizer_name):
    if not donations:
        return None
    org_norm = normalize_name(organizer_name)
    total = len(donations)
    n_anon = 0
    avatars_all = set()
    avatars_nondefault = set()
    n_donations_with_real_avatar = 0
    names = set()
    self_loop = 0
    for d in donations:
        if not isinstance(d, dict): continue
        if d.get("is_anonymous"):
            n_anon += 1
        a = (d.get("avatar_url") or "").strip().lower().split("?")[0]
        if a:
            avatars_all.add(a)
            if not DEFAULT_AVATAR_RE.search(a):
                avatars_nondefault.add(a)
                n_donations_with_real_avatar += 1
        nm = normalize_name(d.get("name"))
        if nm:
            names.add(nm)
            if org_norm and (nm == org_norm or
                             (len(nm) >= 4 and nm in org_norm) or
                             (len(org_norm) >= 4 and org_norm in nm)):
                self_loop += 1

    # Sock-puppet ratio: among donations with non-default avatars, how many
    # unique avatars? Lower = more sock-puppet-shape.
    if n_donations_with_real_avatar >= 3:
        nondefault_ratio = round(len(avatars_nondefault) / n_donations_with_real_avatar, 4)
    else:
        nondefault_ratio = None

    return {
        "total_donations":              total,
        "anon_donations":               n_anon,
        "anon_ratio":                   round(n_anon / total, 4),
        "unique_donor_avatars_total":   len(avatars_all),
        "unique_donor_avatars_nondefault": len(avatars_nondefault),
        "n_donations_with_real_avatar": n_donations_with_real_avatar,
        "nondefault_avatar_diversity_ratio": nondefault_ratio,
        "unique_donor_names":           len(names),
        "self_loop_count":              self_loop,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = get_db()

    cur = db.campaigns.find({}, {
        "_id": 1, "url": 1, "description": 1, "donations": 1, "organizer": 1
    })

    ops = []
    n_text = 0
    n_donor = 0
    avg_donor_anon = 0.0
    n_low_diversity = 0   # avatar_diversity_ratio < 0.6 (sock-puppet candidate)
    for doc in cur:
        update = {}

        tf = text_features(doc.get("description") or "")
        if tf:
            update["description_features"] = tf
            n_text += 1

        df = donor_features(doc.get("donations") or [], doc.get("organizer") or "")
        if df:
            update["donor_features"] = df
            n_donor += 1
            avg_donor_anon += df["anon_ratio"]
            r = df.get("nondefault_avatar_diversity_ratio")
            if r is not None and df["n_donations_with_real_avatar"] >= 3 and r < 0.6:
                n_low_diversity += 1

        if update:
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}))

    print(f"=== summary ===")
    print(f"  text features computed:         {n_text:,}")
    print(f"  donor features computed:        {n_donor:,}")
    if n_donor:
        print(f"  avg anon-donation ratio:        {avg_donor_anon/n_donor:.3f}")
    print(f"  low avatar-diversity (sock-puppet candidates, total>=3 & ratio<0.6): {n_low_diversity:,}")

    if not args.dry_run and ops:
        BATCH = 500
        n_matched = 0
        for i in range(0, len(ops), BATCH):
            res = db.campaigns.bulk_write(ops[i:i+BATCH], ordered=False)
            n_matched += res.matched_count
        print(f"\n  applied to mongo:               {n_matched:,}")


if __name__ == "__main__":
    main()
