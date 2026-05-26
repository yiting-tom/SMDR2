"""Dev-mode mock for ``check_rules``.

Activated when ``SMDR2_DEV_MOCK_DRC=1`` is set in the environment (the
real entry point lives in :mod:`app.external_rule_check._stub` —
this module is dispatched there). Reads the materialised handoff
bundle and emits three RuleChecking JSON rules, each carrying **one
sub-rule per role** present in the bundle so the viewer's rule-focus
rendering can be smoke-tested separately for SBT / BD / POD (and
RING / LID when those are uploaded too):

- **MockDistance**  — from + to (dashed line + midpoint label)
- **MockHighlight** — from only (single highlight + adjacent label)
- **MockTolerance** — tol + tol_text (annotation-only highlight + label)

For each role, the mock picks two handles (or one for the simpler
rules) from that role's first candidate file's match JSON. Roles with
no usable candidates are skipped silently. Every text carries a
``[mock]`` marker so the engineer can never confuse it with a real
DRC result.

Delete this file together with ``_stub.py`` when the real module
lands.
"""

from __future__ import annotations

import json
from pathlib import Path


# Iterate roles in spec order so the rule sidebar lists sub-rules in a
# predictable sequence regardless of upload order. RING / LID at the
# end since they're optional.
_ROLE_ORDER = ("SBT", "BD", "POD", "RING", "LID")


def check_rules(product_id: str, bundle_dir: str) -> dict:
    bundle = Path(bundle_dir)
    manifest = json.loads((bundle / "manifest.json").read_text())
    by_role = _candidates_by_role(manifest, bundle)
    return {
        "MockDistance":  _per_role_distance(by_role),
        "MockHighlight": _per_role_highlight(by_role),
        "MockTolerance": _per_role_tolerance(by_role),
    }


# ---- bundle introspection -----------------------------------------------

def _candidates_by_role(manifest: dict, bundle: Path) -> dict[str, list[tuple]]:
    """Group every (file_id, class_name, handles) candidate by role.

    Each tuple is ``(file_id, class_name, handles)`` — handles are the
    first match group of that class on that file. Roles with no
    candidates are absent from the returned dict.
    """
    out: dict[str, list[tuple]] = {}
    for entry in manifest.get("files", []):
        file_id = entry["file_id"]
        role = entry["role"]
        match_path = bundle / entry["match_json"]
        if not match_path.exists():
            continue
        mj = json.loads(match_path.read_text())
        for key, groups in mj.items():
            cls = _class_from_key(key)
            if cls is None:
                continue
            for handles in groups:
                if handles:
                    out.setdefault(role, []).append((file_id, cls, list(handles)))
    return out


def _class_from_key(key: str) -> str | None:
    """Match JSON key shape: ``<class>.<idx>`` or
    ``<view>.<class>.<idx>``. Returns the class component or None."""
    parts = key.rsplit(".", 2)
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 3:
        return parts[1]
    return None


def _ordered_roles(by_role: dict[str, list[tuple]]) -> list[str]:
    """Roles present in the bundle, in spec order. Any unrecognised
    role (shouldn't happen given the schema enum) trails the known
    ones alphabetically."""
    known = [r for r in _ROLE_ORDER if r in by_role]
    extra = sorted(r for r in by_role if r not in _ROLE_ORDER)
    return known + extra


# ---- rule builders ------------------------------------------------------

def _per_role_distance(by_role: dict[str, list[tuple]]) -> dict:
    """One from+to sub-rule per role: pick the first file with ≥ 2
    candidates of different classes; from = first class's first handle,
    to = the other class's first handle."""
    subs: list[dict] = []
    for role in _ordered_roles(by_role):
        cands = by_role[role]
        by_file: dict[str, list[tuple]] = {}
        for c in cands:
            by_file.setdefault(c[0], []).append(c)
        for fid, items in by_file.items():
            if len(items) < 2:
                continue
            a = items[0]
            b = next((c for c in items[1:] if c[1] != a[1]), items[1])
            subs.append({
                "part":     role,
                "file_id":  fid,
                "from":     a[2][0],
                "to":       b[2][0],
                "text":     f"[mock] {a[1]} ↔ {b[1]} = 12.34 mm (> 5.0)",
                "tol":      None,
                "tol_text": None,
            })
            break   # one sub-rule per role
    return {
        "pass":  True,
        "text":  "[mock] from→to distance, one sub-rule per role",
        "rules": subs,
    }


def _per_role_highlight(by_role: dict[str, list[tuple]]) -> dict:
    """One from-only sub-rule per role: first candidate of that role."""
    subs: list[dict] = []
    for role in _ordered_roles(by_role):
        cands = by_role[role]
        if not cands:
            continue
        fid, cls, handles = cands[0]
        subs.append({
            "part":     role,
            "file_id":  fid,
            "from":     handles[0],
            "to":       None,
            "text":     f"[mock] first {cls} on {role}",
            "tol":      None,
            "tol_text": None,
        })
    return {
        "pass":  True,
        "text":  "[mock] from-only highlight, one sub-rule per role",
        "rules": subs,
    }


def _per_role_tolerance(by_role: dict[str, list[tuple]]) -> dict:
    """One tol+tol_text sub-rule per role: pick a later candidate (3rd
    if available, else last) so MockHighlight and MockTolerance focus
    different entities when the user clicks them in sequence. Set
    ``pass: False`` so the viewer renders this rule in red — that
    exercises the failure-colour path alongside the green from+to and
    from-only above."""
    subs: list[dict] = []
    for role in _ordered_roles(by_role):
        cands = by_role[role]
        if not cands:
            continue
        pick = cands[2] if len(cands) >= 3 else cands[-1]
        fid, cls, handles = pick
        subs.append({
            "part":     role,
            "file_id":  fid,
            "from":     None,
            "to":       None,
            "text":     f"[mock] {cls} tolerance annotation",
            "tol":      handles[0],
            "tol_text": f"⚠ [mock] {cls} ±0.5 mm",
        })
    return {
        "pass":  False,
        "text":  "[mock] tolerance annotation, one sub-rule per role",
        "rules": subs,
    }
