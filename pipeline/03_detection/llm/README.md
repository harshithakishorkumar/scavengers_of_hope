# LLM — Qwen-2.5-72B prompt-only outputs (legacy)

Earlier-phase outputs from the **prompt-only** Qwen-2.5-72B run on v3 (65,662 campaigns). This was Detector B before we decided to fine-tune. The prompt-only approach is superseded by the QLoRA fine-tune in `../training/`.

## Files

| File | What it is |
|---|---|
| `llm_pipeline_qwen72b.py` | Driver script (vLLM, greedy decoding, 12-question schema) |
| `llm_labels_qwen72b.csv` | Per-campaign label (fraud/suspicious/legit) + confidence + reasons |
| `llm_features_qwen72b.csv` | Per-campaign 12 binary question answers |
| `llm_summary_qwen72b.json` | Summary stats |
| `qwen72b_run.out` | Inference log |
| `QWEN_RESULTS_REPORT.md` | Write-up of the original run's findings |

## Why kept

Two reasons:
1. Paper currently cites firing rate (46.3%) and approach from this run. Until the QLoRA version replaces it in the paper, these numbers are still live.
2. The prompt template and question schema in `llm_pipeline_qwen72b.py` are reused by the fine-tune SFT data builder (`../training/build_sft_data.py`).

## What to run going forward

Use `../training/` — the fine-tuned model is what the paper will ultimately describe as Detector B.
