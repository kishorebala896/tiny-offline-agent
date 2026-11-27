# tiny-offline-agent

Two releases landed this week that are really the same story. On Nov 24 PrismML shipped Ternary Bonsai 2.5 27B — a 27-billion-parameter model with every weight stored as -1, 0, or +1, so the whole thing runs in about 5.9 GB. On Nov 20 Thinking Machines shipped Inkling 2 Mini — 26.4B parameters total, only ~3.4B active per token, ~16–20 GB, aimed at writing code with no internet connection. (Both vendor-reported, both Apache 2.0 — treat the benchmark numbers as the vendors' own until independent runs land: Bonsai 2.5 claims 75.6 on SWE-bench Verified and 90.5 on AppWorld; Inkling 2 Mini claims 48.2% on DeepSWE v1.1. The Bonsai announcement also shows the model doing up to 180 consecutive offline tool calls to reproduce a bug, fix it, and run the tests.)

The story isn't really the compression. It's that the agent now lives on your laptop, on your files, with nobody else's API in the loop. And once the model is local, the interesting engineering moves somewhere else: the loop around the model. How many steps does it get? How much context can it hold before it has to forget something? What does it do when a tool call fails? Does it check its work before answering?

That's what this repo is. A simulated offline coding agent — no model calls at all, stdlib only — working three small tasks in a fake shop repo, inside a deliberately tight budget: a 600-token context window and 20 steps max. Three policies run the same tasks:

- **greedy** — reads one file, patches the first thing that looks plausible, answers. No reproducing the bug, no running the tests.
- **careful** — reads everything relevant, reproduces the failure, tries each patch candidate in a sandbox and keeps the first one that passes the visible tests, verifies, then re-reads the patched file before answering.
- **budget** — careful, but it compacts early (at 70% of the window instead of when full), never re-reads a file it already has in its notes, and answers the moment the tests pass.

The brain is a toy on purpose — the patch candidates are fixed per task and the "thinking" is rule-based. What's real is the world: the repo files are actual Python, executed by a real test runner, so a patch either fixes the tests or it doesn't. The thing under test is the loop, not the brain.

## Run it

```bash
python demo.py                 # the table: 3 policies x 3 tasks
python demo.py --trace         # plus one full trace, printed
python demo.py --save-trace    # writes examples/careful-invoice-trace.json
python -m unittest discover -s tests   # 15 tests
```

## What I got

```
window: 600 tokens, max 20 steps
agent     task           hidden  steps  tokens  compact  loops  score  verdict
greedy    invoice-total  1/3     13     604     1        1      0.23   FAIL
greedy    promo-code     2/3     13     595     0        1      0.43   FAIL
greedy    search-bug     1/3     13     486     0        1      0.43   FAIL
careful   invoice-total  3/3     16     656     1        0      1.0    PASS
careful   promo-code     3/3     17     806     1        0      1.0    PASS
careful   search-bug     3/3     18     667     1        0      1.0    PASS
budget    invoice-total  3/3     14     400     0        0      1.0    PASS
budget    promo-code     3/3     15     471     1        0      1.0    PASS
budget    search-bug     3/3     16     424     1        0      1.0    PASS
```

The greedy agent is the cautionary tale, and every one of its failures is a different classic:

- On the invoice task it zeroes the tax rate ("total is too high, kill the tax") — plausible from the task text alone, wrong on the numbers. Hidden tests: 1/3.
- On the promo-code task it hardcodes WELCOME10 without ever reading pricing.py, so it passes the visible test and fails the hidden SAVE5 test. It also claims a $25.00 gadget is now $22.50 — a number that appears nowhere it looked. The grounding judge catches that.
- On every task it re-applies the same patch after already applying it, gets "old text not found" back, and sails straight past the error to its answer. The loop guard catches the repeat; the discipline judge catches the ignored error.

Careful passes everything, but look at the tokens column: peak context of 806 on the promo task against a 600-token window. It only survives because compaction kicks in — and compaction is lossy by design, it keeps only the notes. Budget passes everything too, with 2 fewer steps and roughly 40% less peak context (400–471 vs 656–806), because it compacts early, doesn't re-read files it already has, and skips the confirmation re-read once the tests pass. The boring lesson, again: discipline is cheaper than capacity.

What surprised me: the budget agent's early compaction didn't cost it anything here. I expected at least one task where compacting at 70% would drop a fact it needed later — a failing test name, an expected value — but the notes buffer held. That's probably because these tasks are small; on a longer task the tradeoff would bite, and that's the honest limit of this demo.

## What failed along the way

- **My first loop guard was backwards.** It flagged *any* repeated tool call, so careful got flagged for running the tests twice — reproduce, then verify after the edit. That's legitimate, not a loop. Same mistake I made in trace-judge with retries. The fix: a repeat only counts if the repo hasn't changed since the last identical call. Re-running tests after an edit is verification; re-applying the same patch with nothing in between is a loop.
- **The test runner lied to me.** The hidden-grading call after an agent run reported 3/3 on a buggy world. The hidden test module had been imported once (bound to the fixed billing module) and `run_tests` never evicted it — my cleanup list named the visible modules but not the hidden ones. The fix is to evict anything loaded from a temp copy of the repo, checked by file path. Stale-module bugs are the worst kind: the output looks fine.
- **Float dust broke a hidden test.** `12 * 0.95` is `11.399999999999999`, not `11.40`, so the SAVE5 hidden test failed on the correct patch until the patch rounded to cents. Money code rounds; the demo now does too.
- **My own visible test contradicted the fix.** `test_search_exact` expected `search("Blue")` to return only `["Blue Widget"]` — but case-insensitive matching must also return `"blue Cable"`. The test was written against the buggy behavior. I changed it to search for the full name `"Blue Widget"`. A test suite that encodes the bug is worse than no test; this one nearly shipped the bug as the spec.
- **The first draft of the sandbox ranking ran before exploration**, so careful tried the hardcode patch first on the promo task even though it had read pricing.py. Candidate ranking now happens after exploration, so reading the codes table genuinely changes which patch gets tried first.

## How the pieces fit

- `world.py` — the simulated repo (billing, pricing, checkout, search, catalog + visible tests) and the tools: `list_files`, `read_file`, `search`, `edit_file`, `run_tests`. `run_tests` writes the current files to a temp dir and executes them for real.
- `agent.py` — the loop: context window, notes + compaction, step budget, the loop guard, and the three policies.
- `tasks.py` — the three tasks, the patch candidates per task, and the hidden grading (extra cases the agent never sees).
- `score.py` — a small trace scorer in the spirit of last week's trace-judge: outcome, grounding, verification, economy, discipline.
- `demo.py` — runs all nine combinations and prints the table.
- `examples/` — one real trace, from an actual careful run.

## Rough edges

- The brain is a toy. The policies differ in discipline, not intelligence — the patch candidates are fixed lists, and "trying a candidate in a sandbox" is a stand-in for a model reasoning about a fix. Swapping in a real local model is the obvious next step, and the loop is written so the brain is one function.
- Token counting is `len(text) // 4`. Rough, but consistent — and the window is small on purpose, so the exact constant matters less than the pressure it creates.
- Grounding only checks numbers, same limit as trace-judge. If an agent invents a fact in words, only the outcome judge catches it.
- Compaction keeps notes and drops everything else. On a longer task that would lose something important; here the notes buffer happened to be enough. A real system would summarize instead of dropping.
- Everything is deterministic — same table every run. That's good for a demo and unlike a real model, which would wobble.

## TODO

- Point last week's trace-judge at these traces — the two repos are siblings and the judges should agree.
- Swap the toy brain for a real local model (llama.cpp + Bonsai 2.5 is the obvious pairing, given the week) and see which findings survive contact with a real model.
- A bigger repo for the world — three files is enough to demo the loop, not enough to make context pressure realistic.
- Summarizing compaction instead of drop-everything compaction, and a test where the summary loses a fact.

## Why this week

The Bonsai 2.5 and Inkling 2 Mini releases are the week's story, and they both point at the same shift: the model gets small enough and good enough to live on the machine where the work is. The interesting questions stop being "which model" and start being "how do you run an agent for 180 tool calls without it losing the thread, looping, or answering without checking." This repo is my small, runnable version of those questions — no API key, no GPU, just the loop, a 600-token window, and three tasks that punish sloppiness.
