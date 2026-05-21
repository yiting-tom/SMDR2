"""External rule-checking team's in-tree module.

This subpackage is owned by the external rule-checking team. SMDR2's
adapter (`app/rule_check.py`) imports `check_rules` from here:

    from app.external_rule_check import check_rules as _external_check_rules

Until the external team's real implementation lands, ``_stub.py``
exports a placeholder that raises ``NotImplementedError`` so the
import resolves and any rule-check job fails loudly.

Integration steps (when their code arrives):

1. Add their module files alongside ``_stub.py`` (e.g. ``rules.py``,
   ``geometry.py``, etc.).
2. Re-point the re-export below at their entry point.
3. Delete ``_stub.py``.
4. Run ``pytest tests/test_rule_check.py tests/test_rule_check_job.py``
   to confirm the envelope contract still holds.

See ``openspec/specs/design-rule-checking/spec.md`` —
"External rule function contract" — for the full boundary contract.
"""

from app.external_rule_check._stub import check_rules

__all__ = ["check_rules"]
