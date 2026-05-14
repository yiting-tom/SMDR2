"""Run the JS measure_core unit tests under pytest.

The measure-distance tool's pure geometry helpers live in
`app/static/measure_core.js`. They're tested with `node:test` in
`tests/measure_core.test.mjs`; this wrapper just subprocess-runs that suite
so a single `pytest` invocation exercises both the Python and JS sides.

Skipped automatically when `node` is not installed on the runner.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = REPO_ROOT / "tests" / "measure_core.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not installed")
def test_measure_core_js_suite():
    assert TEST_FILE.exists(), f"missing JS test file: {TEST_FILE}"
    result = subprocess.run(
        ["node", "--test", str(TEST_FILE)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        pytest.fail(
            "node --test failed:\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
