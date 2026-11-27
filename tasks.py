"""The three tasks, the patch candidates, and the hidden grading.

The brain is deliberately a toy: for each task it can propose a few
candidate patches, in a fixed order. The policies differ in what they do
with those candidates — greedy takes the first one blind, careful and
budget try each in a sandbox world and keep the first that passes the
visible tests. That difference is the whole experiment: hypothesis
testing vs first guess, under the same budget.
"""

from world import TASK_TESTS, World

# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

TASKS = {
    "invoice-total": {
        "title": "Invoice total is 10% too high",
        "text": (
            "A $100 order totals $120.00, should be $110.00. "
            "Tax is 10%. Something in billing.py is wrong. Fix it."
        ),
        "files": ["billing.py"],
        "par_steps": 16,
    },
    "promo-code": {
        "title": "Add promo-code support to checkout",
        "text": (
            "checkout() ignores its `code` argument. Promo codes live in "
            "pricing.py (WELCOME10 = 10% off, SAVE5 = 5% off). Wire the code "
            "through so a valid code discounts the order and an unknown code "
            "changes nothing."
        ),
        "files": ["checkout.py", "pricing.py"],
        "par_steps": 17,
    },
    "search-bug": {
        "title": "Search misses matches on case",
        "text": (
            "search('blue') should find 'Blue Widget' and 'blue Cable', but "
            "it only returns 'blue Cable'. The bug is in search.py. "
            "Reproduce it first, then fix it."
        ),
        "files": ["search.py", "catalog.py"],
        "par_steps": 18,
    },
}

# ---------------------------------------------------------------------------
# Patch candidates, per task. Ordered: first is the plausible-but-wrong one.
# ---------------------------------------------------------------------------

PATCHES = {
    "invoice-total": [
        # Greedy's pick: "total is too high, kill the tax." Plausible from the
        # task text alone, wrong on the numbers.
        ("billing.py",
         "TAX_RATE = 0.10",
         "TAX_RATE = 0.0"),
        # The real fix: tax applied twice.
        ("billing.py",
         "    return base + tax + tax",
         "    return base + tax"),
    ],
    "promo-code": [
        # Hardcodes one code. Passes the visible WELCOME10 test, fails the
        # hidden SAVE5 test. Only candidate available if you never read
        # pricing.py.
        ("checkout.py",
         "    amount = sum(price_for(item[\"name\"]) * item[\"qty\"] for item in items)\n    return amount",
         "    amount = sum(price_for(item[\"name\"]) * item[\"qty\"] for item in items)\n"
         "    if code == \"WELCOME10\":\n"
         "        amount = round(amount * 0.90, 2)\n"
         "    return amount"),
        # The real fix: look the code up in pricing.CODES.
        ("checkout.py",
         "    amount = sum(price_for(item[\"name\"]) * item[\"qty\"] for item in items)\n    return amount",
         "    amount = sum(price_for(item[\"name\"]) * item[\"qty\"] for item in items)\n"
         "    if code is not None:\n"
         "        amount = round(amount * (1 - CODES.get(code, 0)), 2)\n"
         "    return amount"),
    ],
    "search-bug": [
        # Lowercases only the query — still case-sensitive on the product
        # side, so 'blue' still misses 'Blue Widget'.
        ("search.py",
         'return [p for p in PRODUCTS if query in p["name"]]',
         'return [p for p in PRODUCTS if query.lower() in p["name"]]'),
        # The real fix: compare lowercased on both sides.
        ("search.py",
         'return [p for p in PRODUCTS if query in p["name"]]',
         'return [p for p in PRODUCTS if query.lower() in p["name"].lower()]'),
    ],
}


def candidates(task_id, read_files):
    """Patch candidates for a task, ordered by what the agent has seen.

    If the agent read pricing.py, the lookup patch for promo-code ranks
    first — it knows a codes table exists. Otherwise the hardcode ranks
    first. This is the one place exploration quality changes the answer:
    careful reads more, so it has a better first hypothesis.
    """
    cands = list(PATCHES[task_id])
    if task_id == "promo-code" and "pricing.py" in read_files:
        cands = [cands[1], cands[0]]
    return cands


# ---------------------------------------------------------------------------
# Hidden grading — extra cases the agent never sees.
# ---------------------------------------------------------------------------

HIDDEN = {
    "invoice-total": (
        "from billing import total\n"
        "def test_hidden_a():\n"
        "    assert total([{'price': 100.0, 'qty': 1}]) == 110.0\n"
        "def test_hidden_b():\n"
        "    assert total([{'price': 10.0, 'qty': 3}, {'price': 7.5, 'qty': 2}]) == 49.5\n"
        "def test_hidden_empty():\n"
        "    assert total([]) == 0.0\n"
    ),
    "promo-code": (
        "from checkout import checkout\n"
        "def test_hidden_save5():\n"
        "    assert checkout([{'name': 'widget', 'qty': 1}], code='SAVE5') == 11.40\n"
        "def test_hidden_unknown():\n"
        "    assert checkout([{'name': 'widget', 'qty': 1}], code='NOPE') == 12.00\n"
        "def test_hidden_welcome10_two():\n"
        "    assert checkout([{'name': 'gadget', 'qty': 2}], code='WELCOME10') == 45.00\n"
    ),
    "search-bug": (
        "from search import search\n"
        "def test_hidden_upper():\n"
        "    assert [p['name'] for p in search('BLUE')] == ['Blue Widget', 'blue Cable']\n"
        "def test_hidden_partial():\n"
        "    assert [p['name'] for p in search('widget')] == ['Blue Widget', 'Green Widget', 'White Widget']\n"
        "def test_hidden_none():\n"
        "    assert search('zzz') == []\n"
    ),
}


def grade(task_id, world):
    """Run the hidden tests against the world's current files.

    Returns (passed, total, detail_text). Uses the world's own test runner
    by temporarily adding the hidden test file.
    """
    hidden_file = f"test_hidden_{task_id.replace('-', '_')}.py"
    graded = world.copy()
    graded.files[hidden_file] = HIDDEN[task_id]
    out = graded.run_tests(hidden_file)
    first = out.splitlines()[0]
    passed, total = first.split(": ")[1].split(" ")[0].split("/")
    return int(passed), int(total), out
