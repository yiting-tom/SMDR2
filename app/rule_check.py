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
                    # --- handle mode (entities in the open file) -----------
                    "from":     handleID | None,           # single source entity
                    "from_entity": handleID | None,        # alias of `from`
                    "to":       handleID | list[handleID] | None,  # single or fan
                    "tol":      handleID | None,            # annotation-only entity
                    "tol_text": str | None,                 # label next to `tol`
                    # --- coordinate mode (open-file world frame, DXF mm) ---
                    "from_coordinates": [number, number] | None,  # paired
                    "to_coordinates":   [number, number] | None,  # paired
                    "to_entity": list[[number, number]] | None,   # closed outline
                    # --- always ------------------------------------------
                    "text":     str,                        # per-sub-rule message
                },
                ...
            ]
        },
        ...
    }

A sub-rule carries a HANDLE group (entities by DXF handle, resolved in the
open file) and/or a COORDINATE group (raw points already in the open file's
world frame — the emitter pre-transforms cross-product geometry). Either or
both MAY be present; one with neither is a text-only informational entry.

Invariants (enforced by :func:`_validate_envelope`):

- ``rules`` MAY be empty; every sub-rule MUST carry non-empty ``text``.
- A sub-rule MAY carry no handle and no coordinate group — such "text-only"
  sub-rules are accepted as informational entries (sidebar shows the
  message; canvas draws nothing).
- ``from_entity`` is an alias of ``from``; it is normalised to ``from`` and,
  if both are set, they MUST be equal.
- ``to`` MAY only be set when ``from`` (or ``from_entity``) is also set, for
  both the scalar and list forms. When ``to`` is a list it MUST be non-empty
  and every element a non-empty string handle. Empty list ``[]`` is rejected
  — send ``None`` to mean "no ``to``".
- Any sub-rule with a non-null handle (``from`` / ``from_entity`` / ``to`` /
  ``tol``) MUST also carry a non-null ``file_id``. The coordinate group does
  NOT require ``file_id`` (its points are self-located in the open frame).
- ``from_coordinates`` and ``to_coordinates`` are each ``[number, number]``
  of finite numbers and are PAIRED — one present requires the other.
- ``to_entity`` when set is a NON-EMPTY list of ``[number, number]`` finite
  points. Empty list ``[]`` is rejected — send ``None`` to mean "no outline".
- ``tol_text`` MAY only be set when ``tol`` is also set.
"""

from __future__ import annotations

import math
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

    # ---- handle group --------------------------------------------------
    frm = _typed_handle(sub, "from", label)
    from_entity = _typed_handle(sub, "from_entity", label)
    if frm is not None and from_entity is not None and frm != from_entity:
        raise RuleCheckOutputError(
            f"{label}: `from` and `from_entity` disagree "
            f"({frm!r} vs {from_entity!r}); `from_entity` is an alias of `from`"
        )
    effective_from = frm if frm is not None else from_entity
    to = _typed_to(sub, label)
    tol = _typed_handle(sub, "tol", label)
    tol_text = sub.get("tol_text")
    if tol_text is not None and not isinstance(tol_text, str):
        raise RuleCheckOutputError(
            f"{label}: `tol_text` must be str or None"
        )

    # ---- coordinate group (open-file world frame; no file_id needed) ----
    from_coords = _typed_point(sub, "from_coordinates", label)
    to_coords = _typed_point(sub, "to_coordinates", label)
    if (from_coords is None) != (to_coords is None):
        raise RuleCheckOutputError(
            f"{label}: `from_coordinates` and `to_coordinates` must be "
            f"set together (point-to-point distance is a pair)"
        )
    _typed_point_list(sub, "to_entity", label)

    # ---- relational invariants -----------------------------------------
    if _has_to_value(to) and effective_from is None:
        raise RuleCheckOutputError(
            f"{label}: `to` set but `from` / `from_entity` is null"
        )
    if tol_text is not None and tol is None:
        raise RuleCheckOutputError(
            f"{label}: `tol_text` set but `tol` is null"
        )
    # Only the HANDLE group needs a file_id; coordinate geometry is
    # self-located in the open file's world frame.
    has_handle = (
        effective_from is not None or _has_to_value(to) or tol is not None
    )
    if has_handle and file_id is None:
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


def _is_number(v: object) -> bool:
    """A finite int/float — bool is rejected (a coordinate is not a flag)."""
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
    )


def _typed_point(sub: dict, key: str, label: str) -> list[float] | None:
    """Read ``sub[key]`` as a ``[number, number]`` of finite numbers, or
    None. Coordinates are in the open file's world frame (DXF mm)."""
    val = sub.get(key)
    if val is None:
        return None
    if not isinstance(val, list) or len(val) != 2 or not all(_is_number(c) for c in val):
        raise RuleCheckOutputError(
            f"{label}: `{key}` must be [number, number] (finite) or None"
        )
    return [float(val[0]), float(val[1])]


def _typed_point_list(sub: dict, key: str, label: str) -> list[list[float]] | None:
    """Read ``sub[key]`` as a non-empty list of ``[number, number]`` points,
    or None. Empty list `[]` is rejected — send ``None`` for "no outline"."""
    val = sub.get(key)
    if val is None:
        return None
    if not isinstance(val, list):
        raise RuleCheckOutputError(
            f"{label}: `{key}` must be a list of [number, number] or None"
        )
    if not val:
        raise RuleCheckOutputError(
            f"{label}: `{key}` is an empty list; emit null instead"
        )
    out = []
    for i, pt in enumerate(val):
        if not isinstance(pt, list) or len(pt) != 2 or not all(_is_number(c) for c in pt):
            raise RuleCheckOutputError(
                f"{label}: `{key}`[{i}] must be [number, number] (finite)"
            )
        out.append([float(pt[0]), float(pt[1])])
    return out


def _typed_to(sub: dict, label: str) -> str | list[str] | None:
    """Read ``sub["to"]`` and ensure it's a string handle, a non-empty
    list of string handles, or None. Empty list `[]` is explicitly
    rejected — emitters that mean "no ``to``" SHALL send ``None``."""
    val = sub.get("to")
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        if not val:
            raise RuleCheckOutputError(
                f"{label}: `to` is an empty list; emit null instead"
            )
        for i, elem in enumerate(val):
            if not isinstance(elem, str):
                raise RuleCheckOutputError(
                    f"{label}: `to` list element #{i} must be a "
                    f"non-empty string, got {type(elem).__name__}"
                )
            if not elem:
                raise RuleCheckOutputError(
                    f"{label}: `to` list element #{i} is an empty string"
                )
        return val
    raise RuleCheckOutputError(
        f"{label}: `to` must be str, list of str, or None, got "
        f"{type(val).__name__}"
    )


def _has_to_value(to: object) -> bool:
    """Return True iff ``to`` carries a target handle in any form
    (non-empty string or non-empty list). Validation is done by
    ``_typed_to`` upstream; this is a pure existence check."""
    if to is None:
        return False
    if isinstance(to, list):
        return len(to) > 0
    return True
