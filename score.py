"""Score a finished agent run, trace-judge style — but lighter.

Same idea as last week's trace-judge, shrunk to fit this repo: outcome,
grounding, verification, economy, discipline. Weights: outcome .40,
grounding .20, verification .20, economy .10, discipline .10.

Honest limits, same as before: grounding only checks numbers, verification
only checks that tests ran after the last edit, economy needs a par. The
point is a cheap, deterministic signal — not a benchmark.
"""

import re

from tasks import TASKS

NUMBER = re.compile(r"\$?\d[\d,]*\.?\d*")


def _numbers(text):
    """Numbers worth checking: skip bare small ints (too generic)."""
    found = []
    for m in NUMBER.finditer(text):
        raw = m.group(0).replace("$", "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value < 10 and "." not in raw:
            continue
        found.append(raw)
    return found


def score_run(result, task_id, world_texts):
    """Score one agent run. `world_texts` is the text the agent actually
    saw (tool results + task text) — grounding checks numbers against it."""
    findings = list(result["findings"])
    task = TASKS[task_id]

    # outcome — fraction of hidden tests passing
    outcome = result["outcome"]
    if outcome < 1.0:
        findings.append(f"outcome: hidden tests {result['hidden']} — not all pass")

    # grounding — every number in the final answer must appear in something
    # the agent saw (a tool result or the task text).
    answer = next(
        (s["text"] for s in result["trace"] if s["kind"] == "answer"), ""
    )
    seen = "\n".join(world_texts) + "\n" + task["text"]
    nums = _numbers(answer)
    bad = [n for n in nums if n not in seen and n.rstrip("0") not in seen]
    if nums:
        grounding = 1.0 - len(bad) / len(nums)
        for n in bad:
            findings.append(f"grounding: {n} in the answer appears nowhere the agent saw")
    else:
        grounding = 1.0

    # verification — did it run the tests after its last edit?
    verification = 1.0 if result["verified"] else 0.0
    if not result["verified"]:
        findings.append("verification: answered without running the tests after the last edit")

    # economy — steps and tokens against the task par / window.
    par = task["par_steps"]
    steps = result["steps"]
    economy = max(0.0, 1.0 - max(0, steps - par) / par)
    if steps > par:
        findings.append(f"economy: {steps} steps vs par {par}")

    # discipline — loops and ignored tool errors.
    discipline = 1.0
    if result["loops"]:
        discipline -= 0.5
    if any("tool error" in f for f in result["findings"]):
        discipline -= 0.5
    discipline = max(0.0, discipline)

    overall = (
        0.40 * outcome
        + 0.20 * grounding
        + 0.20 * verification
        + 0.10 * economy
        + 0.10 * discipline
    )
    if outcome < 1.0 or grounding < 0.5:
        verdict = "FAIL"
    elif overall >= 0.85:
        verdict = "PASS"
    else:
        verdict = "PASS WITH ISSUES"
    return {
        "overall": round(overall, 2),
        "outcome": round(outcome, 2),
        "grounding": round(grounding, 2),
        "verification": verification,
        "economy": round(economy, 2),
        "discipline": round(discipline, 2),
        "verdict": verdict,
        "findings": findings,
    }
