"""Demo: three policies, same three tasks, same tight budget.

    python demo.py                # the table
    python demo.py --trace        # also print one full trace
    python demo.py --save-trace   # write examples/careful-invoice-trace.json

Window is 900 tokens on purpose — small enough that the careful agent
has to compact on the longer tasks, which is the behavior being demoed.
"""

import argparse
import json
from pathlib import Path

from agent import WINDOW_TOKENS, Agent
from score import score_run
from tasks import TASKS
from world import World

POLICIES = ["greedy", "careful", "budget"]


def run_all():
    rows = []
    for policy in POLICIES:
        for task_id in TASKS:
            agent = Agent(policy, policy)
            result = agent.run(task_id)
            # Grounding needs everything the agent saw: tool results + task.
            seen = [s["text"] for s in result["trace"] if s["kind"] == "tool_result"]
            score = score_run(result, task_id, seen)
            rows.append((policy, task_id, result, score))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="store_true", help="print one full trace")
    ap.add_argument("--save-trace", action="store_true", help="write an example trace JSON")
    args = ap.parse_args()

    rows = run_all()
    print(f"window: {WINDOW_TOKENS} tokens, max 20 steps")
    print(f"{'agent':<9} {'task':<14} {'hidden':<7} {'steps':<6} {'tokens':<7} {'compact':<8} {'loops':<6} {'score':<6} verdict")
    for policy, task_id, result, score in rows:
        print(
            f"{policy:<9} {task_id:<14} {result['hidden']:<7} {result['steps']:<6} "
            f"{result['tokens']:<7} {result['compactions']:<8} {result['loops']:<6} "
            f"{score['overall']:<6} {score['verdict']}"
        )
    print()
    for policy, task_id, result, score in rows:
        if score["findings"]:
            print(f"{policy} / {task_id}:")
            for f in score["findings"]:
                print(f"  - {f}")

    if args.trace or args.save_trace:
        agent = Agent("careful", "careful")
        result = agent.run("invoice-total")
        if args.save_trace:
            out = Path(__file__).parent / "examples" / "careful-invoice-trace.json"
            out.parent.mkdir(exist_ok=True)
            out.write_text(json.dumps(result["trace"], indent=2))
            print(f"\nsaved {out}")
        if args.trace:
            print("\n--- careful / invoice-total trace ---")
            for s in result["trace"]:
                print(f"[{s['step']:>2} {s['kind']:<11}] {s['text'][:200]}")


if __name__ == "__main__":
    main()
