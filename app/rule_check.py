"""Adapter for the external rule-checking team's in-tree module.

Rule logic is owned by the external rule-checking team — their module
lives at :mod:`app.external_rule_check` (currently a stub until they
commit). This module is a thin adapter: it forwards the bundle path,
validates the envelope on the way back, and returns the external
function's result verbatim.

See ``openspec/specs/design-rule-checking/spec.md`` —
"External rule function contract" and "RuleChecking JSON output shape"
— for the contract this adapter enforces.

RuleChecking JSON shape::

    {
        "<ruleName>": {
            "pass": bool,
            "text": str,                # overall rule description
            "rules": [                  # zero or more sub-rules
                {
                    "part":     "SBT" | "BD" | "POD" | "RING" | "LID",
                    "file_id":  str | None,
                    "from":     handleID | None,   # single source entity
                    "to":       handleID | None,   # single target entity
                    "text":     str,               # per-sub-rule message
                    "tol":      handleID | None,   # annotation-only entity
                    "tol_text": str | None,        # label next to `tol`
                },
                ...
            ]
        },
        ...
    }

Invariants (enforced by :func:`_validate_envelope`):

- ``rules`` MAY be empty; when non-empty, every sub-rule MUST carry
  non-empty ``text``.
- A sub-rule MUST set at least one of ``from``, ``tol``.
- ``to`` MAY only be set when ``from`` is also set.
- Any sub-rule with a non-null ``from`` / ``to`` / ``tol`` MUST also
  carry a non-null ``file_id``.
- ``tol_text`` MAY only be set when ``tol`` is also set.
"""

from __future__ import annotations

from pathlib import Path

from app.external_rule_check import check_rules as _external_check_rules


HandleID = str
SubRule = dict[str, object]
RuleResult = dict[str, dict[str, object]]


_VALID_PARTS = frozenset({"SBT", "BD", "POD", "RING", "LID"})


class RuleCheckOutputError(ValueError):
    """Raised when the external rule function returns a result that
    violates the RuleChecking JSON shape contract."""


def check_rules(product_id: str, bundle_dir: str | Path) -> RuleResult:
    """Invoke the external rule function and validate its envelope.

    The bundle directory MUST already be materialised at
    ``bundle_dir`` and conform to the layout
    :func:`app.drc_bundle.build_bundle_dir` writes — see the
    "External rule function contract" requirement.

    Returns the external function's result verbatim once validation
    passes. Raises :class:`RuleCheckOutputError` on any envelope
    violation; the worker maps that into a job-level error.
    """
    result = _external_check_rules(product_id, str(bundle_dir))
    _validate_envelope(result)
    return result


def _validate_envelope(result: object) -> None:
    """Raise :class:`RuleCheckOutputError` if ``result`` violates any
    invariant from the RuleChecking JSON output shape requirement.

    The adapter does NOT mutate ``result``; it only inspects it.
    """
    if not isinstance(result, dict):
        raise RuleCheckOutputError(
            f"expected dict result, got {type(result).__name__}"
        )
    for rule_name, payload in result.items():
        _validate_rule(rule_name, payload)


def _validate_rule(rule_name: object, payload: object) -> None:
    if not isinstance(rule_name, str):
        raise RuleCheckOutputError(
            f"rule name must be str, got {type(rule_name).__name__}"
        )
    if not isinstance(payload, dict):
        raise RuleCheckOutputError(
            f"rule {rule_name!r}: payload must be dict, got "
            f"{type(payload).__name__}"
        )
    for key in ("pass", "text", "rules"):
        if key not in payload:
            raise RuleCheckOutputError(
                f"rule {rule_name!r}: missing required key {key!r}"
            )
    if not isinstance(payload["pass"], bool):
        raise RuleCheckOutputError(
            f"rule {rule_name!r}: `pass` must be bool"
        )
    if not isinstance(payload["text"], str):
        raise RuleCheckOutputError(
            f"rule {rule_name!r}: `text` must be str"
        )
    rules = payload["rules"]
    if not isinstance(rules, list):
        raise RuleCheckOutputError(
            f"rule {rule_name!r}: `rules` must be list"
        )
    for idx, sub in enumerate(rules):
        _validate_sub_rule(rule_name, idx, sub)


def _validate_sub_rule(rule_name: str, idx: int, sub: object) -> None:
    label = f"rule {rule_name!r} sub-rule #{idx}"
    if not isinstance(sub, dict):
        raise RuleCheckOutputError(f"{label}: must be dict")

    part = sub.get("part")
    if part not in _VALID_PARTS:
        raise RuleCheckOutputError(
            f"{label}: `part` must be one of {sorted(_VALID_PARTS)}, "
            f"got {part!r}"
        )

    text = sub.get("text")
    if not isinstance(text, str) or not text:
        raise RuleCheckOutputError(
            f"{label}: `text` must be a non-empty string"
        )

    file_id = sub.get("file_id")
    if file_id is not None and not isinstance(file_id, str):
        raise RuleCheckOutputError(
            f"{label}: `file_id` must be str or None"
        )

    frm = _typed_handle(sub, "from", label)
    to = _typed_handle(sub, "to", label)
    tol = _typed_handle(sub, "tol", label)
    tol_text = sub.get("tol_text")
    if tol_text is not None and not isinstance(tol_text, str):
        raise RuleCheckOutputError(
            f"{label}: `tol_text` must be str or None"
        )

    if to is not None and frm is None:
        raise RuleCheckOutputError(
            f"{label}: `to` set but `from` is null"
        )
    if frm is None and tol is None:
        raise RuleCheckOutputError(
            f"{label}: must set at least one of `from`, `tol` "
            f"(nothing to highlight otherwise)"
        )
    if tol_text is not None and tol is None:
        raise RuleCheckOutputError(
            f"{label}: `tol_text` set but `tol` is null"
        )
    if (frm is not None or to is not None or tol is not None) and file_id is None:
        raise RuleCheckOutputError(
            f"{label}: sub-rule references a handle but `file_id` is null"
        )


def _typed_handle(sub: dict, key: str, label: str) -> str | None:
    """Read ``sub[key]`` and ensure it's a string handle or None."""
    val = sub.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise RuleCheckOutputError(
            f"{label}: `{key}` must be str or None, got {type(val).__name__}"
        )
    return val
