# Detector B

Qwen-2.5-72B-Instruct-AWQ answers ten yes/no questions about manipulation
tactics present in a campaign description.

| File | What it is |
|---|---|
| `detector_B_label_questions.py` | the inference driver (vLLM, `Qwen/Qwen2.5-72B-Instruct-AWQ`) |
| `detector_B_prompt.txt` | system message, all ten question texts, user template, JSON output schema |

**Fire rule: at least 4 of 10 answers are `yes`.** `unclear` does not count, and
a response that fails strict JSON validation abstains on that campaign.

Question-threshold sensitivity on the 100,294-campaign analysis set: at least 3
fires on 3,964 campaigns, at least 4 on 1,100, at least 5 on 204. Per-question
yes rates are in `../../../artifact/evidence/detector_B_answers.csv.gz`.

Every question asks whether a tactic is present, never whether verifiable
detail is absent, so a sparse but honest appeal cannot accumulate `yes` answers
by omission.

An earlier twelve-question prompt-only run exists in the project history. It is
not what the paper reports and is not shipped here.
