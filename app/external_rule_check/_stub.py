"""Placeholder ``check_rules`` until the external rule-checking team
commits their implementation. Delete this file when their real module
lands (and re-point ``app/external_rule_check/__init__.py`` at it).

Dev-mode escape hatch: setting ``SMDR2_DEV_MOCK_DRC=1`` in the
environment makes the stub dispatch to ``_dev_mock.check_rules``,
which reads the materialised bundle and emits a 3-rule fixture
exercising all three viewer display modes (from+to, from-only,
tol+tol_text). Production behaviour is unaffected — without the env
var the stub still raises ``NotImplementedError``.
"""

from __future__ import annotations

import os


def check_rules(product_id: str, bundle_dir: str) -> dict:
    if os.environ.get("SMDR2_DEV_MOCK_DRC") == "1":
        from app.external_rule_check._dev_mock import check_rules as _mock
        return _mock(product_id, bundle_dir)
    raise NotImplementedError(
        "external rule module not yet committed — replace this stub with "
        "the external team's implementation of "
        "`check_rules(product_id, bundle_dir) -> dict`. Set "
        "SMDR2_DEV_MOCK_DRC=1 to dispatch to the dev-mode mock instead."
    )
