"""Version diff — compare two snapshots of the same product.

Both versions are complete snapshots (one library each + version_files
bindings), so the diff is computed on the fly; nothing is precomputed
or persisted. Templates are matched per class by the canonical
`template_signature` (translation / entity-order / vertex-order
invariant) rather than row id — clone copies geometry into new rows, so
id-based comparison would mark every carried-over template as changed.
"""

from __future__ import annotations

from typing import Any

from app.files import FILE_STORE, FileRecord
from app.library import LIBRARIES, Template, template_signature
from app.versions import Version

# Per-binding state fields the bindings diff inspects for file ids bound
# in both versions.
_BINDING_STATE_FIELDS = (
    "dxf_view",
    "selected_layers",
    "top_view_rect",
    "bottom_view_rect",
    "side_view_rect",
    "user_unit_override",
    "chosen_layout",
)


def _template_entry(class_name: str, t: Template) -> dict[str, Any]:
    return {
        "id": t.id,
        "class_name": class_name,
        "entity_count": len(t.entity_point_sets),
        "vertex_count": sum(len(e) for e in t.entity_point_sets),
        "bbox": list(t.bbox),
        "centroid": list(t.centroid),
        "entity_point_sets": t.entity_point_sets,
    }


def _signature_index(
    templates_by_class: dict[str, list[Template]],
) -> dict[tuple[str, tuple], tuple[str, Template]]:
    """(class_name, signature) → (class_name, template). Duplicate
    signatures within a class can't occur (commit-time dedup)."""
    idx: dict[tuple[str, tuple], tuple[str, Template]] = {}
    for cls, templates in templates_by_class.items():
        for t in templates:
            idx[(cls, template_signature(t.entity_point_sets))] = (cls, t)
    return idx


def _diff_templates(lib_from: str, lib_to: str) -> dict[str, list[dict]]:
    _, _, tpl_from = LIBRARIES.store.load_library(lib_from)
    _, _, tpl_to = LIBRARIES.store.load_library(lib_to)
    idx_from = _signature_index(tpl_from)
    idx_to = _signature_index(tpl_to)
    added = [
        _template_entry(cls, t)
        for key, (cls, t) in sorted(idx_to.items(), key=lambda kv: kv[0][0])
        if key not in idx_from
    ]
    removed = [
        _template_entry(cls, t)
        for key, (cls, t) in sorted(idx_from.items(), key=lambda kv: kv[0][0])
        if key not in idx_to
    ]
    return {"added": added, "removed": removed}


def _diff_configs(lib_from: str, lib_to: str) -> list[dict]:
    _, cfg_from, _ = LIBRARIES.store.load_library(lib_from)
    _, cfg_to, _ = LIBRARIES.store.load_library(lib_to)
    out: list[dict] = []
    for cls in sorted(set(cfg_from) | set(cfg_to)):
        a = cfg_from.get(cls)
        b = cfg_to.get(cls)
        if a == b:
            continue
        out.append({"class_name": cls, "from": a, "to": b})
    return out


def _file_mini(rec: FileRecord) -> dict[str, Any]:
    return {"file_id": rec.id, "name": rec.name, "size": rec.size}


def _diff_bindings(version_from: str, version_to: str) -> list[dict]:
    recs_from = {
        (r.dxf_role, r.id): r for r in FILE_STORE.list_by_version(version_from)
    }
    recs_to = {
        (r.dxf_role, r.id): r for r in FILE_STORE.list_by_version(version_to)
    }
    out: list[dict] = []
    for key in sorted(set(recs_from) | set(recs_to), key=lambda k: (k[0] or "", k[1])):
        role, _fid = key
        a = recs_from.get(key)
        b = recs_to.get(key)
        if a is None:
            out.append({
                "role": role, "kind": "added",
                "from": None, "to": _file_mini(b), "changed": [],
            })
        elif b is None:
            out.append({
                "role": role, "kind": "removed",
                "from": _file_mini(a), "to": None, "changed": [],
            })
        else:
            changed = [
                f for f in _BINDING_STATE_FIELDS
                if getattr(a, f) != getattr(b, f)
            ]
            if changed:
                out.append({
                    "role": role, "kind": "state_changed",
                    "from": _file_mini(a), "to": _file_mini(b),
                    "changed": changed,
                })
    return out


def diff_versions(v_from: Version, v_to: Version) -> dict[str, Any]:
    """Full diff payload between two versions of the same product.
    Pure read — works regardless of sign-off state."""
    templates = _diff_templates(v_from.library_id, v_to.library_id)
    configs = _diff_configs(v_from.library_id, v_to.library_id)
    bindings = _diff_bindings(v_from.id, v_to.id)
    return {
        "product_id": v_from.product_id,
        "from": v_from.to_dict(),
        "to": v_to.to_dict(),
        "templates": templates,
        "configs": configs,
        "bindings": bindings,
        "summary": {
            "templates_added": len(templates["added"]),
            "templates_removed": len(templates["removed"]),
            "configs_changed": len(configs),
            "bindings_changed": len(bindings),
            "identical": not (
                templates["added"] or templates["removed"]
                or configs or bindings
            ),
        },
    }
