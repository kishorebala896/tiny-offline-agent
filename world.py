"""A tiny simulated repo + the tools an agent gets to work with it.

Nothing here is a real model call. The point is the harness: the world is
real code (files on disk in a temp dir, executed by a real test runner),
so a patch either fixes the tests or it doesn't. The agent's brain is a
toy on purpose — the thing under test is the loop around it: budgets,
context, verification discipline.
"""

import importlib
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# The repo. Four small modules, three visible test files.
# ---------------------------------------------------------------------------

FILES = {
    "billing.py": '''"""Billing helpers for the shop demo repo.

Everything here works in plain floats and rounds only at the edges —
this is a demo shop, not a payments system, so don't take the money
handling as a pattern. `total` is the function the invoice task is about.
"""

TAX_RATE = 0.10


def subtotal(items):
    """Sum of price * qty for each item dict.

    Items look like {"price": 12.00, "qty": 2}. An empty list totals 0.0.
    """
    return sum(item["price"] * item["qty"] for item in items)


def total(items):
    """Total with tax. Bug: tax is added twice."""
    base = subtotal(items)
    tax = base * TAX_RATE
    return base + tax + tax


def discounted_total(items, pct):
    """Total after a percentage discount, tax applied to the discounted base.

    `pct` is 0.10 for 10% off. Discount first, then tax — the order matters
    and this helper gets it right, which is worth remembering when you look
    at `total` above.
    """
    base = subtotal(items) * (1 - pct)
    return base + base * TAX_RATE
''',
    "pricing.py": '''"""Catalog prices and promo codes.

CODES maps a code string to a discount fraction. The checkout task is to
wire this table through — hardcoding one code in checkout.py is the
tempting shortcut and it will fail the hidden tests.
"""

CODES = {
    "SAVE5": 0.05,
    "WELCOME10": 0.10,
}

PRICES = {
    "widget": 12.00,
    "gadget": 25.00,
    "cable": 8.50,
}


def price_for(name):
    """Unit price for a product name. Raises KeyError on unknown names,
    which is the existing behaviour — checkout relies on it."""
    return PRICES[name]


def discount_for(code):
    """Discount fraction for a promo code, 0 for unknown codes."""
    if code is None:
        return 0
    return CODES.get(code, 0)
''',
    "checkout.py": '''"""Checkout: totals an order, optionally with a promo code.

The order is a list of {"name": str, "qty": int}. Prices come from
pricing.py, promo codes from pricing.CODES. An unknown code should
change nothing — silently ignoring it is the specified behaviour, not
an error case.

The `code` argument is currently ignored entirely — the feature to add.
"""

from pricing import CODES, price_for


def checkout(items, code=None):
    """items: list of {"name": str, "qty": int}. Returns the amount due.

    Currently ignores `code` entirely — the feature to add.
    """
    amount = sum(price_for(item["name"]) * item["qty"] for item in items)
    return amount


def validate_code(code):
    """True if the code exists in the promo table."""
    return code in CODES
''',
    "search.py": '''"""Product search for the shop demo repo.

Search is substring matching on the product name. The search-bug task is
that matching is case-sensitive: a shopper typing "blue" should find
"Blue Widget" too. The catalog is small, so the fix is in the comparison,
not in an index.
"""

from catalog import PRODUCTS


def search(query):
    """Return products whose name contains the query. Bug: case-sensitive."""
    return [p for p in PRODUCTS if query in p["name"]]


def search_by_price(max_price):
    """Return products at or under max_price, cheapest first."""
    return sorted(
        [p for p in PRODUCTS if p["price"] <= max_price],
        key=lambda p: p["price"],
    )
''',
    "catalog.py": '''"""The product catalog.

Note the casing: "Blue Widget" and "blue Cable" differ only in case, which
is exactly what the search task is about. Prices are in dollars.
"""

PRODUCTS = [
    {"name": "Blue Widget", "price": 12.00},
    {"name": "Red Gadget", "price": 25.00},
    {"name": "blue Cable", "price": 8.50},
    {"name": "Green Widget", "price": 9.99},
    {"name": "Yellow Gadget", "price": 19.50},
    {"name": "Black Cable", "price": 6.25},
    {"name": "White Widget", "price": 14.00},
    {"name": "Silver Gadget", "price": 32.00},
]
''',
    "test_billing.py": '''"""Visible tests for billing.py — the agent can run these."""

from billing import total


def test_total_simple():
    assert total([{"price": 100.0, "qty": 1}]) == 110.0


def test_total_multi():
    assert total([{"price": 10.0, "qty": 2}, {"price": 5.0, "qty": 1}]) == 27.5
''',
    "test_checkout.py": '''"""Visible tests for checkout.py."""

from checkout import checkout


def test_no_code():
    assert checkout([{"name": "widget", "qty": 1}]) == 12.00


def test_welcome10():
    assert checkout([{"name": "widget", "qty": 1}], code="WELCOME10") == 10.80
''',
    "test_search.py": '''"""Visible tests for search.py."""

from search import search


def test_search_exact():
    names = [p["name"] for p in search("Blue Widget")]
    assert names == ["Blue Widget"]


def test_search_lower():
    names = [p["name"] for p in search("blue")]
    assert names == ["Blue Widget", "blue Cable"]
''',
}

# Which visible test file belongs to which task.
TASK_TESTS = {
    "invoice-total": "test_billing.py",
    "promo-code": "test_checkout.py",
    "search-bug": "test_search.py",
}


def count_tokens(text):
    """Rough token count: ~4 chars per token. Good enough for a budget demo."""
    return max(1, len(text) // 4)


class World:
    """The simulated repo: files in memory, tools the agent can call.

    run_tests writes the current files to a temp dir and executes the test
    module there for real, so edits genuinely change test outcomes.
    """

    def __init__(self, files=None):
        self.files = dict(files if files is not None else FILES)

    def copy(self):
        return World(self.files)

    # -- tools -------------------------------------------------------------
    def list_files(self):
        names = sorted(self.files)
        return "repo files:\n" + "\n".join(f"  {n}" for n in names)

    def read_file(self, path):
        if path not in self.files:
            return f"ERROR: no such file: {path}"
        return f"--- {path} ---\n" + self.files[path]

    def search(self, query):
        hits = []
        for path in sorted(self.files):
            for i, line in enumerate(self.files[path].splitlines(), 1):
                if query.lower() in line.lower():
                    hits.append(f"{path}:{i}: {line.strip()}")
        if not hits:
            return f"no matches for {query!r}"
        return "\n".join(hits)

    def edit_file(self, path, old, new):
        if path not in self.files:
            return f"ERROR: no such file: {path}"
        if old not in self.files[path]:
            return "ERROR: patch did not apply — old text not found"
        self.files[path] = self.files[path].replace(old, new, 1)
        return f"patched {path}:\n- {old.strip()}\n+ {new.strip()}"

    def run_tests(self, test_file):
        """Execute a test file against the current repo state. Real exec."""
        if test_file not in self.files:
            return f"ERROR: no such test file: {test_file}"
        tmp = Path(tempfile.mkdtemp(prefix="tiny-offline-"))
        try:
            for name, src in self.files.items():
                (tmp / name).write_text(src)
            sys.path.insert(0, str(tmp))
            try:
                for mod in list(sys.modules):
                    # Drop anything loaded from an earlier temp copy of the
                    # repo — otherwise a stale module (with the old files
                    # bound in) wins over the current one. This bit me: the
                    # hidden-grading call after an agent run silently reused
                    # the fixed billing module and reported 3/3 on a buggy
                    # world. The file-path check is what actually catches it.
                    loaded = sys.modules[mod]
                    src = getattr(loaded, "__file__", "") or ""
                    if (
                        mod.split(".")[0]
                        in (
                            "billing", "pricing", "checkout", "search",
                            "catalog",
                        )
                        or mod.startswith("test_")
                        or src.startswith("/tmp/tiny-offline-")
                    ):
                        del sys.modules[mod]
                importlib.invalidate_caches()
                mod = importlib.import_module(test_file[:-3])
                results = []
                for name in sorted(dir(mod)):
                    if not name.startswith("test_"):
                        continue
                    try:
                        getattr(mod, name)()
                        results.append(f"PASS {name}")
                    except AssertionError as exc:
                        results.append(f"FAIL {name}: {exc}")
                    except Exception as exc:  # noqa: BLE001 - show it, don't crash
                        results.append(f"ERROR {name}: {type(exc).__name__}: {exc}")
                passed = sum(r.startswith("PASS") for r in results)
                out = "\n".join(results)
                return f"{test_file}: {passed}/{len(results)} passed\n{out}"
            finally:
                sys.path.remove(str(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- dispatch ----------------------------------------------------------
    def call(self, tool, args):
        """One tool call. Returns the result text."""
        if tool == "list_files":
            return self.list_files()
        if tool == "read_file":
            return self.read_file(args["path"])
        if tool == "search":
            return self.search(args["query"])
        if tool == "edit_file":
            return self.edit_file(args["path"], args["old"], args["new"])
        if tool == "run_tests":
            return self.run_tests(args["file"])
        return f"ERROR: unknown tool: {tool}"
