"""The agent loop: context window, compaction, step budget, loop guard.

Three policies, one shared loop. They differ only in discipline:

- greedy:  read one file, patch the first candidate, answer. No reproduce,
  no verify, and it re-reads + re-applies the same patch afterwards.
- careful: explore, reproduce (run tests), test each patch candidate in a
  sandbox and keep the first that passes, verify, then confirm by
  re-reading the patched file before answering.
- budget:  careful, but it compacts early, never re-reads a file it already
  has in its notes, tries at most two candidates, and answers the moment
  the tests pass.

The sandbox tries are recorded as `think` steps — in a real agent that
reasoning still costs context, so it costs tokens here too.
"""

from tasks import TASKS, candidates, grade
from world import TASK_TESTS, World, count_tokens

WINDOW_TOKENS = 600        # the whole point: this is small on purpose
COMPACT_AT = 0.70          # budget policy compacts here; careful at 1.00
MAX_STEPS = 20

GREEDY_ANSWERS = {
    "invoice-total": "Fixed billing.py — removed the tax. Total is now $100.00.",
    "promo-code": (
        "Wired up WELCOME10 in checkout.py — a $25.00 gadget is now $22.50 "
        "with the code. Verified — all tests pass."
    ),
    "search-bug": (
        "Fixed search.py — search('blue') now returns 3 matches. "
        "Verified — all tests pass."
    ),
}


class Context:
    """The agent's working memory: tool results plus a notes buffer.

    Tool results pile up until the window fills; then the old ones get
    replaced by a compact summary of the notes. The notes are the only
    thing that survives compaction — so what the agent bothered to write
    down is what it still knows.
    """

    def __init__(self, window=WINDOW_TOKENS, compact_at=1.0):
        self.window = window
        self.compact_at = compact_at
        self.entries = []          # (kind, text, tokens)
        self.notes = []            # short facts, survive compaction
        self.compactions = 0
        self.peak = 0              # high-water mark — what the table reports

    @property
    def used(self):
        return sum(t for _, _, t in self.entries)

    def note(self, fact):
        if fact not in self.notes:
            self.notes.append(fact)

    def add(self, kind, text):
        self.entries.append((kind, text, count_tokens(text)))
        self.peak = max(self.peak, self.used)
        if self.used > self.compact_at * self.window:
            self.compact()

    def compact(self):
        """Drop old tool results, keep the notes as one summary entry."""
        summary = "notes so far:\n" + "\n".join(f"- {n}" for n in self.notes)
        if not self.notes:
            summary = "notes so far: (none — nothing was written down)"
        kept = [e for e in self.entries if e[0] == "answer"]
        self.entries = kept + [("summary", summary, count_tokens(summary))]
        self.compactions += 1


class Agent:
    def __init__(self, name, policy, window=WINDOW_TOKENS, max_steps=MAX_STEPS):
        self.name = name
        self.policy = policy
        # Budget compacts early (70% of window); the others only when full.
        compact_at = COMPACT_AT if policy == "budget" else 1.0
        self.ctx = Context(window, compact_at=compact_at)
        self.max_steps = max_steps
        self.trace = []
        self.findings = []
        self.read_files = set()
        self.tests_run = 0
        self.verified = False      # ran tests after the last edit
        self._seen_calls = {}      # (tool, args) -> repo state before the call

    # -- trace helpers ------------------------------------------------------
    def _step(self, kind, text):
        self.trace.append({"step": len(self.trace) + 1, "kind": kind, "text": text})
        if kind in ("tool_result", "think"):
            self.ctx.add(kind, text)

    def _think(self, text):
        self._step("think", text)

    def _call(self, world, tool, args):
        """One tool call, with the loop guard watching.

        A repeat is only a loop if the repo hasn't changed since the last
        identical call. Re-running the tests after an edit is legitimate
        verification, not a loop — my first version flagged any repeat,
        which was exactly backwards (same mistake as trace-judge's retry
        logic). So the guard compares the state *before* this call with
        the state *after* the last identical call: if nothing changed in
        between, the agent is going in circles.
        """
        key = (tool, tuple(sorted(args.items())))
        pre_state = tuple(sorted(world.files.items()))
        if key in self._seen_calls and self._seen_calls[key] == pre_state:
            self.findings.append(
                f"loop: {tool} {args} repeated with no state change "
                "since the last identical call"
            )
        self._step("tool_call", f"{tool} {args}")
        result = world.call(tool, args)
        self._seen_calls[key] = tuple(sorted(world.files.items()))
        if tool == "read_file" and not result.startswith("ERROR"):
            self.read_files.add(args["path"])
            for line in result.splitlines():
                if "def " in line or line.strip().startswith(("TAX_RATE", "CODES")):
                    self.ctx.note(f"{args['path']}: {line.strip()}")
        if tool == "run_tests":
            self.tests_run += 1
        if result.startswith("ERROR"):
            self.findings.append(f"tool error ignored or hit: {result}")
        self._step("tool_result", result)
        return result

    # -- the run ------------------------------------------------------------
    def run(self, task_id):
        task = TASKS[task_id]
        world = World()
        self._think(f"task: {task['title']} — {task['text']}")
        self.ctx.note(f"task: {task['title']}")

        test_file = TASK_TESTS[task_id]

        if self.policy == "greedy":
            self._greedy(task_id, task, world, test_file)
        elif self.policy == "careful":
            self._careful(task_id, task, world, test_file)
        else:  # budget
            self._budget(task_id, task, world, test_file)

        passed, total, _ = grade(task_id, world)
        return {
            "agent": self.name,
            "task": task_id,
            "outcome": passed / total if total else 0.0,
            "hidden": f"{passed}/{total}",
            "steps": len(self.trace),
            "tokens": self.ctx.peak,
            "compactions": self.ctx.compactions,
            "loops": sum(1 for f in self.findings if f.startswith("loop:")),
            "findings": self.findings,
            "verified": self.verified,
            "trace": self.trace,
        }

    # -- policies -----------------------------------------------------------
    def _explore(self, task, world, files):
        self._call(world, "list_files", {})
        for path in files:
            if self.policy == "budget" and path in self.read_files:
                self._think(f"skip re-read of {path} — already in my notes")
                continue
            self._call(world, "read_file", {"path": path})

    def _try_candidates(self, task_id, world, test_file, limit=None):
        """Try each candidate in a sandbox world; return the first that
        passes the visible tests, or None. Each try is a think step.

        Candidates are ranked by what the agent has actually read — see
        tasks.candidates. That ranking happens here, after exploration,
        so reading pricing.py genuinely changes which patch gets tried first.
        """
        cands = candidates(task_id, self.read_files)
        tried = 0
        for path, old, new in cands:
            if limit is not None and tried >= limit:
                break
            tried += 1
            sandbox = world.copy()
            res = sandbox.edit_file(path, old, new)
            if res.startswith("ERROR"):
                self._think(f"candidate {tried} doesn't apply: {res}")
                continue
            out = sandbox.run_tests(test_file)
            ok = "0/" not in out.splitlines()[0] and "FAIL" not in out and "ERROR" not in out
            self._think(f"candidate {tried} in sandbox: {out.splitlines()[0]}")
            if ok:
                return (path, old, new)
        return None

    def _greedy(self, task_id, task, world, test_file):
        # One file, first candidate, no tests, then re-read + re-apply.
        self._explore(task, world, [task["files"][0]])
        path, old, new = candidates(task_id, self.read_files)[0]
        self._call(world, "edit_file", {"path": path, "old": old, "new": new})
        self.ctx.note(f"patched {path}")
        # "Check my work" — re-read the same file, re-apply the same patch.
        self._call(world, "read_file", {"path": path})
        res = self._call(world, "edit_file", {"path": path, "old": old, "new": new})
        if res.startswith("ERROR"):
            # Sailed straight past a failed tool call. Classic.
            self._think("patch didn't apply the second time — moving on anyway")
        self._answer(GREEDY_ANSWERS[task_id])

    def _careful(self, task_id, task, world, test_file):
        self._explore(task, world, task["files"])
        out = self._call(world, "run_tests", {"file": test_file})
        self.ctx.note(f"reproduce: {out.splitlines()[0]}")
        patch = self._try_candidates(task_id, world, test_file)
        if patch is None:
            self._answer("Could not find a patch that passes the visible tests.")
            return
        self._call(world, "edit_file",
                   {"path": patch[0], "old": patch[1], "new": patch[2]})
        self.ctx.note(f"patched {patch[0]}")
        out = self._call(world, "run_tests", {"file": test_file})
        self.verified = "FAIL" not in out and "ERROR" not in out
        self.ctx.note(f"verify: {out.splitlines()[0]}")
        # One extra confirmation read before answering — careful is thorough.
        self._call(world, "read_file", {"path": patch[0]})
        passed = out.splitlines()[0]
        self._answer(
            f"Fixed {patch[0]}. Visible tests now read: {passed}. "
            "Patch chosen because it passed the visible tests in a sandbox first."
        )

    def _budget(self, task_id, task, world, test_file):
        # Careful, but compact early and stop the moment the tests pass.
        self._explore(task, world, task["files"])
        out = self._call(world, "run_tests", {"file": test_file})
        self.ctx.note(f"reproduce: {out.splitlines()[0]}")
        patch = self._try_candidates(task_id, world, test_file, limit=2)
        if patch is None:
            self._answer("Could not find a patch that passes the visible tests.")
            return
        self._call(world, "edit_file",
                   {"path": patch[0], "old": patch[1], "new": patch[2]})
        self.ctx.note(f"patched {patch[0]}")
        out = self._call(world, "run_tests", {"file": test_file})
        self.verified = "FAIL" not in out and "ERROR" not in out
        self.ctx.note(f"verify: {out.splitlines()[0]}")
        # Early exit: tests pass, answer now — no confirmation re-read.
        passed = out.splitlines()[0]
        self._answer(
            f"Fixed {patch[0]}. Visible tests now read: {passed}."
        )

    def _answer(self, text):
        self._step("answer", text)
