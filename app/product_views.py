"""Per-(product, role) view resolution.

A product role (SBT / BD / POD / RING / LID) can be sourced from one
or more DXF files; the five roles are independent and may all be
populated on the same product. Each file carries a `dxf_view`:

  - 'multi'           — the file contains per-view region rects; each
                        non-null `<view>_view_rect` claims that view.
  - 'top'/'bottom'/'side' — the whole file is that single view.

Coverage of each logical view (top / bottom / side) within a
(product, role) is unique: at most one source per view. This module is
the single point that builds the per-view mapping and surfaces
conflicts as `ViewCoverageConflict`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from app.files import FILE_STORE, FileRecord


VIEWS: tuple[str, ...] = ("top", "bottom", "side")
VALID_VIEW_TAGS: frozenset[str] = frozenset({"multi", "top", "bottom", "side"})

ViewName = Literal["top", "bottom", "side"]
SourceKind = Literal["region", "whole_file"]


@dataclass(frozen=True)
class ViewSource:
    """Where a single logical view's geometry comes from."""

    file_id: str
    source: SourceKind
    rect: dict | None  # populated only for source='region'


class ViewCoverageConflict(Exception):
    """Two files claim coverage of the same view for one (product, role)."""

    def __init__(self, view: str, file_ids: list[str]):
        self.view = view
        self.file_ids = list(file_ids)
        super().__init__(
            f"view {view!r} is covered by multiple files: {self.file_ids}"
        )


def resolve_views(rows: Iterable[FileRecord]) -> dict[str, ViewSource]:
    """Build the {view -> source} mapping for the rows of one (product, role).

    Caller is responsible for filtering rows to the relevant role.
    Raises `ViewCoverageConflict` if any logical view is covered by more
    than one source.
    """
    mapping: dict[str, ViewSource] = {}
    # Track who claimed each view, so a conflict can list every contributor.
    claims: dict[str, list[str]] = {v: [] for v in VIEWS}

    for rec in rows:
        view_tag = rec.dxf_view or "multi"  # legacy NULL → multi
        if view_tag == "multi":
            for v in VIEWS:
                rect = getattr(rec, f"{v}_view_rect")
                if rect is not None:
                    claims[v].append(rec.id)
                    mapping[v] = ViewSource(
                        file_id=rec.id, source="region", rect=rect
                    )
        elif view_tag in VIEWS:
            claims[view_tag].append(rec.id)
            mapping[view_tag] = ViewSource(
                file_id=rec.id, source="whole_file", rect=None
            )
        # Any other tag is invalid — caller validates on write, so we
        # silently ignore here (defensive; should never happen).

    for v, contributors in claims.items():
        if len(contributors) > 1:
            raise ViewCoverageConflict(v, contributors)

    return mapping


def resolve_for_version(
    version_id: str, dxf_role: str
) -> dict[str, ViewSource]:
    """Convenience: load a version's bindings from `FILE_STORE` and
    resolve them."""
    rows = [
        f for f in FILE_STORE.list_by_version(version_id)
        if f.dxf_role == dxf_role
    ]
    return resolve_views(rows)
