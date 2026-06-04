"""Drift-guard tests: keep canvas.js JS literals in sync with their
Python source-of-truth counterparts in app/library.py.

JavaScript can't import from Python, so a few constants are duplicated
across the boundary. We delimit each duplicate with sentinel comments
(`// <NAME>_BEGIN` / `// <NAME>_END`) and parse the JS literal here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.library import (
    CLASS_CATEGORY,
    CLASS_CATEGORY_ORDER,
    CLASS_VIEW_CONSTRAINTS,
)


CANVAS_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "canvas.js"


def _extract_js_literal(src: str, name: str) -> str:
    """Return the JS object literal between // <name>_BEGIN and _END
    sentinel comments. Raises if not found."""
    pattern = re.compile(
        rf"//\s*{re.escape(name)}_BEGIN\s*\n(.*?)//\s*{re.escape(name)}_END",
        re.DOTALL,
    )
    m = pattern.search(src)
    if not m:
        raise AssertionError(
            f"sentinel block {name}_BEGIN/_END not found in {CANVAS_JS}"
        )
    return m.group(1)


def _js_object_to_dict(block: str) -> dict:
    """Crude but sufficient: strip the `const NAME = ` prefix, drop the
    trailing `;`, JSON-parse what remains. The JS literal MUST be a
    pure object expression (no template strings, no comments, no
    trailing commas) — that's a contract this drift-guard enforces."""
    # Drop assignment LHS (`const NAME = `) and trailing semicolon.
    body = block.strip()
    body = re.sub(r"^const\s+\w+\s*=\s*", "", body)
    body = body.rstrip().rstrip(";")
    # Trailing commas are valid JS but not JSON — strip them inside
    # objects (`,}`) and arrays (`,]`) before handing to json.loads.
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


def test_class_view_constraints_js_mirror_matches_python():
    src = CANVAS_JS.read_text()
    js_block = _extract_js_literal(src, "CLASS_VIEW_CONSTRAINTS")
    js_obj = _js_object_to_dict(js_block)

    # Convert the Python dict's frozenset values to sorted lists so we
    # can compare apples-to-apples with the JS arrays. Sort both sides
    # so order in the JS literal doesn't matter.
    py_normalised = {k: sorted(v) for k, v in CLASS_VIEW_CONSTRAINTS.items()}
    js_normalised = {k: sorted(v) for k, v in js_obj.items()}

    assert py_normalised == js_normalised, (
        f"canvas.js CLASS_VIEW_CONSTRAINTS drift detected:\n"
        f"  python: {py_normalised}\n"
        f"  js:     {js_normalised}\n"
        f"Update either file so the two match."
    )


def test_class_category_js_mirror_matches_python():
    src = CANVAS_JS.read_text()

    js_cat = _js_object_to_dict(_extract_js_literal(src, "CLASS_CATEGORY"))
    assert js_cat == CLASS_CATEGORY, (
        f"canvas.js CLASS_CATEGORY drift detected:\n"
        f"  python: {CLASS_CATEGORY}\n"
        f"  js:     {js_cat}\n"
        f"Update either file so the two match."
    )

    js_order = _js_object_to_dict(_extract_js_literal(src, "CLASS_CATEGORY_ORDER"))
    # Python is list[tuple]; the JS mirror is list[list]. Normalise to compare.
    py_order = [list(pair) for pair in CLASS_CATEGORY_ORDER]
    assert js_order == py_order, (
        f"canvas.js CLASS_CATEGORY_ORDER drift detected:\n"
        f"  python: {py_order}\n"
        f"  js:     {js_order}\n"
        f"Update either file so the two match."
    )
