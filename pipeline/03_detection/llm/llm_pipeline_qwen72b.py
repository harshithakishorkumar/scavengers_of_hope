"""
llm_pipeline_qwen72b.py — vLLM-accelerated structured fraud extraction
========================================================================
Model:     Qwen/Qwen2.5-72B-Instruct-AWQ (AWQ 4-bit, ~40GB on A100 80GB)
Engine:    vLLM with continuous batching, greedy decoding
Dataset:   filtered_dataset.csv (65,662 campaigns, 13 platforms, >=100 word descriptions)

This pipeline replaces the earlier 12-question schema (which over-fired
"suspicious" on unknown personal fundraisers) with 12 concrete,
falsifiable, behaviorally-grounded questions. Each question can be answered
directly from the campaign text without judgment calls about subjective
qualities like "proportionate emotion".

The 12 questions feed into 7 categorical attribute dimensions used downstream
for per-attribute consensus labeling.

USAGE on vast.ai (A100 80GB instance):
    git clone <this script>
    pip install vllm pandas
    # Upload filtered_dataset.csv
    python llm_pipeline_qwen72b.py

OUTPUT:
    llm_features_qwen72b.csv  — 12 binary fraud-question features per campaign
    llm_labels_qwen72b.csv    — derived label + confidence + reasons
    llm_progress_qwen72b.json — checkpoint
    llm_summary_qwen72b.json  — run statistics
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict

import pandas as pd

# =============================================================================
# CONFIGURATION
# =============================================================================
INPUT_CSV  = Path(os.environ.get("INPUT_CSV", "filtered_dataset.csv"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "."))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FEATURES_CSV = OUTPUT_DIR / "llm_features_qwen72b.csv"
LABELS_CSV   = OUTPUT_DIR / "llm_labels_qwen72b.csv"
PROGRESS_JSON = OUTPUT_DIR / "llm_progress_qwen72b.json"
SUMMARY_JSON  = OUTPUT_DIR / "llm_summary_qwen72b.json"

MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen/Qwen2.5-72B-Instruct-AWQ")

# vLLM settings
VLLM_BATCH_SIZE     = int(os.environ.get("VLLM_BATCH_SIZE", 32))
GPU_MEM_UTIL        = float(os.environ.get("GPU_MEM_UTIL", 0.90))
MAX_MODEL_LEN       = int(os.environ.get("MAX_MODEL_LEN", 2048))
MAX_NEW_TOKENS      = 350
DESCRIPTION_CHARS   = 1500     # bigger window now that questions need more text
WRITE_EVERY         = 256
PRINT_EVERY         = 256

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUTPUT_DIR / "llm_pipeline_qwen72b.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


# =============================================================================
# REVISED 12-QUESTION SCHEMA — concrete, falsifiable, behavior-grounded
# =============================================================================
SYSTEM_MSG = """You are a fraud detection analyst examining crowdfunding campaigns.

Your task is to read each campaign and answer 12 specific yes/no questions. The questions ask about CONCRETE, OBSERVABLE features of the text — not subjective judgments.

CRITICAL INSTRUCTIONS:
1. Answer ONLY "yes" or "no". Use "unclear" ONLY when the description is too vague to tell either way — not when you are merely uncertain.
2. Base every answer on what is literally present in the campaign text, not on what you assume.
3. Output valid JSON exactly as requested. No commentary outside the JSON block.
4. Be decisive. Most questions have a clear yes or no answer in the text."""

PROMPT_TEMPLATE = """Read the campaign below and answer all 12 questions, then provide an overall label.

Campaign Title: {title}
Campaign Description: {description}

Q1 (External Payment): Does the description include external payment links or instructions to pay outside the platform? Look for: PayPal.me links, CashApp ($cashtag), Venmo (@username), Zelle, crypto wallet addresses, bank account/IBAN numbers, Western Union, MoneyGram, or instructions like "DM me to donate" or "contact me for payment details".

Q2 (Named Institution): Does the description name any specific institution that a reader could verify by searching online? Examples: a specific hospital name ("Mayo Clinic", "St. Jude"), a school, a government agency, a registered charity. Generic words like "the hospital" or "a local school" do NOT count.

Q3 (Named Person): Does the description name any specific identifiable person with a full name (first + last) and a clear role (patient, beneficiary, organizer, family member)? Generic references like "my brother" or "a friend" without a name do NOT count.

Q4 (Verifiable Event): Does the description reference a specific event with a date, location, or detail that could be looked up — such as a named natural disaster, a specific accident with a date, a court case, or a news event? Vague references like "the recent fire" without specifics do NOT count.

Q5 (Deadline Pressure): Does the description use deadline-pressure phrases such as "last chance", "only X days left", "before it's too late", "deadline tomorrow", "running out of time", or specific countdown language?

Q6 (Share Pressure): Does the description ask the reader to share, repost, or spread the campaign on social media? Look for phrases like "please share", "spread the word", "help us go viral", "share with everyone you know", "repost this".

Q7 (Guilt Language): Does the description use guilt or moral pressure aimed at potential donors? Look for phrases like "if you have a heart", "anyone with compassion", "true Christians/Muslims/believers would", "shame on anyone who scrolls past", "you would help if you cared".

Q8 (Defensive Language): Does the description contain defensive statements about the campaign's legitimacy, denials of being a scam, or rebuttals to skeptics? Examples: "this is not a scam", "I am a real person", "I would never lie about this", "people have accused me but...".

Q9 (Itemized Goal): Does the description provide an itemized breakdown of what the money will pay for, with specific amounts or percentages? Examples: "$5,000 for surgery, $2,000 for travel" or "60% to medical bills, 40% to rent".

Q10 (Specific Circumstance): Does the description name a specific medical condition (e.g., "stage 4 pancreatic cancer", "Lupus", "ALS"), legal case (with case number or court name), or other identifiable, named circumstance? Vague references like "an illness" do NOT count.

Q11 (Evidence Provided): Does the description reference photos, medical records, receipts, news articles, video updates, or other concrete evidence the donor could verify? Generic claims of having "documentation" do NOT count.

Q12 (Relationship Disclosed): Does the description clearly state the relationship between the organizer and the beneficiary AND explain how the organizer knows the beneficiary? Example: "I am Jane's sister and have lived with her for 15 years". Just stating "I am a friend" without context does NOT count.

Output JSON in exactly this format:
{{
  "q1_external_payment": "yes/no/unclear",
  "q2_named_institution": "yes/no/unclear",
  "q3_named_person": "yes/no/unclear",
  "q4_verifiable_event": "yes/no/unclear",
  "q5_deadline_pressure": "yes/no/unclear",
  "q6_share_pressure": "yes/no/unclear",
  "q7_guilt_language": "yes/no/unclear",
  "q8_defensive_language": "yes/no/unclear",
  "q9_itemized_goal": "yes/no/unclear",
  "q10_specific_circumstance": "yes/no/unclear",
  "q11_evidence_provided": "yes/no/unclear",
  "q12_relationship_disclosed": "yes/no/unclear",
  "label": "unknown/suspicious/fraudulent",
  "confidence": 0.0-1.0,
  "reasons": ["one short reason", "another short reason"]
}}"""


# =============================================================================
# CONSTANTS — fraud-signal mapping
# =============================================================================
QUESTION_KEYS = [
    "q1_external_payment",
    "q2_named_institution",
    "q3_named_person",
    "q4_verifiable_event",
    "q5_deadline_pressure",
    "q6_share_pressure",
    "q7_guilt_language",
    "q8_defensive_language",
    "q9_itemized_goal",
    "q10_specific_circumstance",
    "q11_evidence_provided",
    "q12_relationship_disclosed",
]

# True = "yes" answer indicates fraud signal present
# False = "no" answer indicates fraud signal present (i.e. absence of legitimacy marker)
FRAUD_WHEN_YES = {
    "q1_external_payment":      True,    # external payment = fraud
    "q2_named_institution":     False,   # NO named institution = fraud
    "q3_named_person":          False,   # NO named person = fraud
    "q4_verifiable_event":      False,   # NO verifiable event = fraud
    "q5_deadline_pressure":     True,    # deadline pressure = fraud
    "q6_share_pressure":        True,    # share-pressure = fraud
    "q7_guilt_language":        True,    # guilt language = fraud
    "q8_defensive_language":    True,    # defensive language = fraud
    "q9_itemized_goal":         False,   # NO itemization = fraud
    "q10_specific_circumstance":False,   # NO specific circumstance = fraud
    "q11_evidence_provided":    False,   # NO evidence = fraud
    "q12_relationship_disclosed": False, # NO relationship disclosed = fraud
}

VALID_LABELS = {"unknown", "suspicious", "fraudulent"}


# =============================================================================
# UTILITIES
# =============================================================================
def answer_to_fraud_signal(key: str, answer: str) -> float:
    a = str(answer).strip().lower()
    if a == "unclear":
        return 0.5
    fraud_when_yes = FRAUD_WHEN_YES[key]
    if a == "yes":
        return 1.0 if fraud_when_yes else 0.0
    return 0.0 if fraud_when_yes else 1.0


def make_default_result() -> dict:
    result = {}
    for key in QUESTION_KEYS:
        result[key] = "unclear"
        result["fs_" + key] = 0.5
    result["llm_label"] = "unclear"
    result["llm_confidence"] = 0.0
    result["llm_reasons"] = []
    result["llm_fraud_signal_count"] = 6.0
    result["parse_error"] = True
    return result


def extract_json_from_text(text: str) -> Optional[dict]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            candidate_fixed = candidate.replace("'", '"').replace("```", "").replace("\n", " ")
            try:
                return json.loads(candidate_fixed)
            except json.JSONDecodeError:
                pass
    # Last resort: per-key regex extraction
    partial = {}
    for key in QUESTION_KEYS:
        km = re.search(r'"' + key + r'"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
        if km:
            partial[key] = km.group(1).lower()
    lm = re.search(r'"label"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
    if lm:
        partial["label"] = lm.group(1).lower()
    cm = re.search(r'"confidence"\s*:\s*([0-9.]+)', text, re.IGNORECASE)
    if cm:
        try:
            partial["confidence"] = float(cm.group(1))
        except ValueError:
            pass
    return partial if partial else None


def validate_and_normalise(parsed: dict) -> dict:
    result = {}
    for key in QUESTION_KEYS:
        raw = str(parsed.get(key, "unclear")).strip().lower()
        if raw not in ("yes", "no", "unclear"):
            raw = "unclear"
        result[key] = raw
        result["fs_" + key] = answer_to_fraud_signal(key, raw)

    raw_label = str(parsed.get("label", "unclear")).strip().lower()
    result["llm_label"] = raw_label if raw_label in VALID_LABELS else "unclear"

    try:
        conf = float(parsed.get("confidence", 0.0))
        result["llm_confidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        result["llm_confidence"] = 0.0

    reasons = parsed.get("reasons", [])
    if isinstance(reasons, list):
        result["llm_reasons"] = [str(r) for r in reasons[:5]]
    elif isinstance(reasons, str):
        result["llm_reasons"] = [reasons]
    else:
        result["llm_reasons"] = []

    result["llm_fraud_signal_count"] = round(
        sum(result["fs_" + k] for k in QUESTION_KEYS), 1
    )
    result["parse_error"] = False
    return result


# =============================================================================
# CSV WRITERS
# =============================================================================
def flush_features(records: list) -> None:
    if not records:
        return
    df = pd.DataFrame(records)
    header = not FEATURES_CSV.exists()
    df.to_csv(FEATURES_CSV, mode="a", header=header, index=False)


def flush_labels(records: list) -> None:
    if not records:
        return
    df = pd.DataFrame(records)
    header = not LABELS_CSV.exists()
    df.to_csv(LABELS_CSV, mode="a", header=header, index=False)


# =============================================================================
# MAIN
# =============================================================================
def main():
    pipeline_start = time.time()
    log.info("=" * 70)
    log.info("DonationScam LLM Pipeline (Qwen2.5-72B-AWQ via vLLM)")
    log.info("Started: %s", datetime.now().isoformat())
    log.info("Model: %s", MODEL_NAME)
    log.info("Batch size: %d | GPU mem util: %.2f", VLLM_BATCH_SIZE, GPU_MEM_UTIL)
    log.info("=" * 70)

    # ---- Load dataset ----
    log.info("Loading dataset: %s", INPUT_CSV)
    df = pd.read_csv(INPUT_CSV, low_memory=False)
    log.info("Dataset loaded: %d rows", len(df))
    total = len(df)

    # ---- Resume from existing CSV ----
    already_done = set()
    if FEATURES_CSV.exists():
        try:
            done_df = pd.read_csv(FEATURES_CSV, usecols=["url"])
            already_done = set(done_df["url"].astype(str).tolist())
            log.info("Resuming: %d already processed.", len(already_done))
        except Exception as e:
            log.warning("Could not read existing CSV: %s", e)

    mask = ~df["url"].astype(str).isin(already_done)
    df_todo = df[mask].reset_index(drop=True)
    log.info("Remaining: %d / %d", len(df_todo), total)

    if len(df_todo) == 0:
        log.info("All done!")
        return

    # ---- Load vLLM ----
    log.info("Loading vLLM model: %s", MODEL_NAME)
    from vllm import LLM, SamplingParams

    llm = LLM(
        model=MODEL_NAME,
        quantization="awq_marlin",
        dtype="half",
        max_model_len=MAX_MODEL_LEN,
        gpu_memory_utilization=GPU_MEM_UTIL,
        trust_remote_code=True,
        enforce_eager=False,
    )

    sampling_params = SamplingParams(
        temperature=0.0,           # greedy
        top_p=1.0,
        max_tokens=MAX_NEW_TOKENS,
        stop=["}\n\n", "\n\n\n"],
    )

    log.info("vLLM model loaded successfully.")

    # ---- Get tokenizer for chat template ----
    tokenizer = llm.get_tokenizer()

    # ---- Build all prompts ----
    log.info("Building prompts...")
    all_prompts = []
    all_urls = []
    all_platforms = []

    for _, row in df_todo.iterrows():
        url = str(row.get("url", ""))
        platform = str(row.get("platform", ""))
        title = str(row.get("title", "")).strip() or "(no title)"
        desc = str(row.get("description", ""))
        if not desc.strip() or desc.strip().lower() in ("nan", "none", ""):
            desc = "(no description provided)"
        desc = desc[:DESCRIPTION_CHARS]

        user_content = PROMPT_TEMPLATE.format(title=title, description=desc)

        messages = [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user_content},
        ]
        chat_text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        all_prompts.append(chat_text)
        all_urls.append(url)
        all_platforms.append(platform)

    log.info("Built %d prompts. Starting inference...", len(all_prompts))

    # ---- Process in batches ----
    label_counts = {"unknown": 0, "suspicious": 0, "fraudulent": 0, "unclear": 0}
    confidences = []
    feat_buffer = []
    label_buffer = []
    loop_start = time.time()

    for batch_start in range(0, len(all_prompts), VLLM_BATCH_SIZE):
        batch_end = min(batch_start + VLLM_BATCH_SIZE, len(all_prompts))
        batch_prompts = all_prompts[batch_start:batch_end]
        batch_urls = all_urls[batch_start:batch_end]
        batch_platforms = all_platforms[batch_start:batch_end]

        try:
            outputs = llm.generate(batch_prompts, sampling_params, use_tqdm=False)
        except Exception as exc:
            log.error("vLLM generate failed: %s", exc)
            outputs = None

        for i in range(len(batch_prompts)):
            url = batch_urls[i]
            platform = batch_platforms[i]

            if outputs is not None:
                raw_text = outputs[i].outputs[0].text
                parsed = extract_json_from_text(raw_text)
                if parsed is not None:
                    result = validate_and_normalise(parsed)
                else:
                    result = make_default_result()
            else:
                result = make_default_result()

            # Build feature record
            feat_rec = {"url": url, "platform": platform}
            for key in QUESTION_KEYS:
                feat_rec[key] = result[key]
                feat_rec["fs_" + key] = result["fs_" + key]
            feat_rec["llm_fraud_signal_count"] = result["llm_fraud_signal_count"]
            feat_rec["llm_label"] = result["llm_label"]
            feat_rec["llm_confidence"] = result["llm_confidence"]
            feat_rec["parse_error"] = result["parse_error"]
            feat_buffer.append(feat_rec)

            # Build label record
            label_buffer.append({
                "url": url,
                "platform": platform,
                "llm_label": result["llm_label"],
                "llm_confidence": result["llm_confidence"],
                "llm_reasons": json.dumps(result["llm_reasons"]),
                "llm_fraud_signal_count": result["llm_fraud_signal_count"],
            })

            label_counts[result["llm_label"]] = label_counts.get(result["llm_label"], 0) + 1
            confidences.append(result["llm_confidence"])

        # Flush buffers
        done_this_run = batch_end
        if len(feat_buffer) >= WRITE_EVERY or batch_end == len(all_prompts):
            flush_features(feat_buffer)
            flush_labels(label_buffer)
            feat_buffer.clear()
            label_buffer.clear()

            progress = {
                "last_updated": datetime.now().isoformat(),
                "processed_count": len(already_done) + done_this_run,
                "total_campaigns": total,
            }
            with open(PROGRESS_JSON, "w") as f:
                json.dump(progress, f)

        # Progress logging
        if done_this_run % PRINT_EVERY < VLLM_BATCH_SIZE or batch_end == len(all_prompts):
            elapsed = time.time() - loop_start
            rate = done_this_run / elapsed if elapsed > 0 else 0
            remaining = len(all_prompts) - done_this_run
            eta_s = remaining / rate if rate > 0 else 0
            log.info(
                "Progress: %d/%d (%.1f%%) | Rate: %.1f c/s | ETA: %s | "
                "legit=%d susp=%d fraud=%d unclear=%d",
                done_this_run, len(all_prompts),
                100.0 * done_this_run / len(all_prompts),
                rate, str(timedelta(seconds=int(eta_s))),
                label_counts.get("unknown", 0),
                label_counts.get("suspicious", 0),
                label_counts.get("fraudulent", 0),
                label_counts.get("unclear", 0),
            )

    # ---- Final flush ----
    flush_features(feat_buffer)
    flush_labels(label_buffer)

    # ---- Summary ----
    elapsed_total = time.time() - pipeline_start
    summary = {
        "run_completed": datetime.now().isoformat(),
        "model": MODEL_NAME,
        "total_processed": len(already_done) + len(all_prompts),
        "new_this_run": len(all_prompts),
        "resumed_from": len(already_done),
        "elapsed_seconds": round(elapsed_total, 1),
        "elapsed_human": str(timedelta(seconds=int(elapsed_total))),
        "vllm_batch_size": VLLM_BATCH_SIZE,
        "label_distribution": label_counts,
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
    }
    with open(SUMMARY_JSON, "w") as f:
        json.dump(summary, f, indent=2)

    log.info("=" * 70)
    log.info("Pipeline complete! %d campaigns in %s (%.1f c/s)",
             len(all_prompts),
             str(timedelta(seconds=int(elapsed_total))),
             len(all_prompts) / elapsed_total if elapsed_total > 0 else 0)
    log.info("=" * 70)


if __name__ == "__main__":
    main()
