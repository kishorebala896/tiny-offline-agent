"""Tests for tiny-offline-agent.

These cover the bits I actually got wrong while building it: the
candidate ranking (it first ran before exploration, so careful picked the
hardcode), the loop guard, and compaction dropping the reproduce output.
Run with: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import WINDOW_TOKENS, Agent, Context  # noqa: E402
from score import score_run  # noqa: E402
from tasks import candidates, grade  # noqa: E402
from world import World, count_tokens  # noqa: E402


class TestContext(unittest.TestCase):
    def test_token_count_is_chars_over_four(self):
        self.assertEqual(count_tokens("a" * 400), 100)

    def test_window_forces_compaction(self):
        ctx = Context(window=100)
        ctx.note("failing test: test_total_simple expects 110.0")
        ctx.add("tool_result", "x" * 500)  # 125 tokens > 100
        self.assertEqual(ctx.compactions, 1)
        self.assertLessEqual(ctx.used, 100)

    def test_compaction_keeps_notes(self):
        ctx = Context(window=100)
        ctx.note("failing test: test_total_simple expects 110.0")
        ctx.add("tool_result", "x" * 500)
        summary = ctx.entries[-1][1]
        self.assertIn("test_total_simple", summary)


class TestWorld(unittest.TestCase):
    def test_edit_changes_test_outcome(self):
        world = World()
        before = world.run_tests("test_billing.py")
        self.assertIn("0/2 passed", before)
        world.edit_file("billing.py", "    return base + tax + tax", "    return base + tax")
        after = world.run_tests("test_billing.py")
        self.assertIn("2/2 passed", after)

    def test_hidden_grading_catches_hardcode(self):
        world = World()
        # The greedy promo patch: hardcodes WELCOME10 only.
        path, old, new = candidates("promo-code", set())[0]
        world.edit_file(path, old, new)
        passed, total, _ = grade("promo-code", world)
        self.assertLess(passed, total)

    def test_hidden_grading_passes_real_fix(self):
        world = World()
        path, old, new = candidates("promo-code", {"pricing.py"})[0]
        world.edit_file(path, old, new)
        passed, total, _ = grade("promo-code", world)
        self.assertEqual(passed, total)


class TestPolicies(unittest.TestCase):
    def test_careful_passes_all_hidden(self):
        for task_id in ("invoice-total", "promo-code", "search-bug"):
            result = Agent("careful", "careful").run(task_id)
            self.assertEqual(result["outcome"], 1.0, task_id)

    def test_budget_passes_all_hidden(self):
        for task_id in ("invoice-total", "promo-code", "search-bug"):
            result = Agent("budget", "budget").run(task_id)
            self.assertEqual(result["outcome"], 1.0, task_id)

    def test_greedy_fails_at_least_two(self):
        failed = 0
        for task_id in ("invoice-total", "promo-code", "search-bug"):
            result = Agent("greedy", "greedy").run(task_id)
            if result["outcome"] < 1.0:
                failed += 1
        self.assertGreaterEqual(failed, 2)

    def test_greedy_trips_loop_guard(self):
        result = Agent("greedy", "greedy").run("invoice-total")
        self.assertGreaterEqual(result["loops"], 1)
        self.assertTrue(any("tool error" in f for f in result["findings"]))

    def test_careful_has_no_loops(self):
        # Re-running tests after an edit is verification, not a loop.
        for task_id in ("invoice-total", "promo-code", "search-bug"):
            result = Agent("careful", "careful").run(task_id)
            self.assertEqual(result["loops"], 0, task_id)

    def test_budget_uses_fewer_steps_than_careful(self):
        for task_id in ("invoice-total", "promo-code", "search-bug"):
            careful = Agent("careful", "careful").run(task_id)
            budget = Agent("budget", "budget").run(task_id)
            self.assertLess(budget["steps"], careful["steps"], task_id)

    def test_careful_compacts_on_long_tasks(self):
        # With a 600-token window the careful agent has to compact at least
        # once across the three tasks — that's the behavior being demoed.
        total_compactions = sum(
            Agent("careful", "careful").run(t)["compactions"]
            for t in ("invoice-total", "promo-code", "search-bug")
        )
        self.assertGreaterEqual(total_compactions, 1)


class TestScore(unittest.TestCase):
    def test_unverified_answer_scores_low(self):
        greedy = Agent("greedy", "greedy").run("invoice-total")
        seen = [s["text"] for s in greedy["trace"] if s["kind"] == "tool_result"]
        score = score_run(greedy, "invoice-total", seen)
        self.assertEqual(score["verification"], 0.0)
        self.assertEqual(score["verdict"], "FAIL")

    def test_careful_passes(self):
        careful = Agent("careful", "careful").run("invoice-total")
        seen = [s["text"] for s in careful["trace"] if s["kind"] == "tool_result"]
        score = score_run(careful, "invoice-total", seen)
        self.assertEqual(score["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
