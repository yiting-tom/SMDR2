#!/usr/bin/env python3
"""Faithful performance baseline for SMDR2's matcher hot path.

Drives the REAL matcher (no mocks) with SYNTHETIC geometry only — no real
DXFs ever touch this script. The synthetic drawing mirrors a real IC-package
layout: a large grid of identical-radius circles (BGA balls — the dominant
entity) plus a handful of polyline footprints (SMD pads — the chamfer path),
with a few distinct circle radii (BGA / via / C4).

It measures, with repeats + median to damp noise, exactly the things the
2026-06 perf audit flagged as optimisation targets, so a before/after run is a
mechanical diff:

  build_shapes      build_entity_shapes(N)            (preprocess one-time cost)
  circle_template   one circle find_matches over N    (fast radius-bucket path)
  polyline_template one polyline find_matches over N  (slow PCA+chamfer path)
  scan_all_loop     K-template sequential loop         (#1 ProcessPool target)
  suppression       suppress_contained_matches(10k)   (regression-guarded)
  json_sizes        match-JSON indent vs compact + gzip, primitives + gzip
                                                       (#2 gzip / #7 compact)

Usage:
  python benchmarks/perf_matcher.py --label baseline
  python benchmarks/perf_matcher.py --label optimized --baseline benchmarks/results/baseline.json

Writes benchmarks/results/<label>.json and prints a table. With --baseline it
also prints the delta vs that file.
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Pure modules only — do NOT import app.main (it boots FastAPI).
from app.library import Template, build_handle_index  # noqa: E402
from app.matching import (  # noqa: E402
    build_entity_shapes,
    find_matches_from_pointsets,
)
from app.side_regions import suppress_contained_matches  # noqa: E402


# ---- synthetic geometry ----------------------------------------------------
# Three radii so circle templates land in distinct exact-radius buckets, like a
# real layout's BGA / via / C4 populations.
_RADII = (0.30, 0.15, 0.075)


def make_primitives(
    n_circles: int, n_polylines: int, n_circle_templates: int,
) -> tuple[list[dict], list[str]]:
    """Realistic radius mix: one large BGA population at a shared radius plus a
    tail of small distinct-radius populations (vias / fiducials / misc). Each
    circle template then matches a realistic, NON-overlapping set, so total
    circle matches ~= n_circles (not n_circles * n_templates, which would
    inflate output-construction cost and make the parallelization before/after
    misleading). Returns (prims, seed_handles) where seed_handles[i] seeds
    circle template i (one per distinct radius)."""
    prims: list[dict] = []
    T = max(n_circle_templates, 1)
    radii = [round(0.05 + i * 0.017, 4) for i in range(T)]  # distinct buckets
    small = 50
    pops = [max(n_circles - small * (T - 1), 1)] + [small] * (T - 1)
    side = int(sum(pops) ** 0.5) + 1
    pitch = 1.0
    seed_handles: list[str] = []
    k = 0
    for r, pop in zip(radii, pops):
        for j in range(pop):
            row, col = divmod(k, side)
            h = f"c{k:07d}"
            if j == 0:
                seed_handles.append(h)
            prims.append({
                "type": "circle", "handle": h,
                "center": [col * pitch, row * pitch], "r": r,
            })
            k += 1
    # SMD-pad footprints: identical 4-vertex rectangles, translated. A polyline
    # template then matches every one of them -> exercises the chamfer path,
    # whose O(N) signature gate-scan over all shapes is the real per-template
    # redundancy (paid regardless of match count).
    base = [(0.0, 0.0), (0.6, 0.0), (0.6, 0.3), (0.0, 0.3), (0.0, 0.0)]
    ox = (side + 2) * pitch
    for j in range(n_polylines):
        dx = ox + (j % 50) * 2.0
        dy = (j // 50) * 2.0
        prims.append({
            "type": "polyline",
            "handle": f"p{j:05d}",
            "points": [[x + dx, y + dy] for (x, y) in base],
        })
    return prims, seed_handles


def timed(fn, repeats: int) -> dict:
    samples = []
    out = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000.0)  # ms
    return {
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "repeats": repeats,
        "_result": out,
    }


def run(args) -> dict:
    n_c, n_p = args.circles, args.polylines
    prims, circ_handles = make_primitives(n_c, n_p, args.circle_templates)

    # 1. shape build (one-time preprocess cost)
    hi = build_handle_index(prims)
    bs = timed(lambda: build_entity_shapes(prims, hi), args.repeats)
    shapes = bs.pop("_result")

    # Templates drawn from the real synthetic shapes so they genuinely match.
    poly_handle = "p00000"
    circ_templates = [
        Template.from_entities("BGABall", [list(shapes[h].points)],
                               entity_kinds=["circle"])
        for h in circ_handles
    ]
    poly_templates = [
        Template.from_entities("SMD-2T", [list(shapes[poly_handle].points)],
                               entity_kinds=["polyline"])
        for _ in range(args.polyline_templates)
    ]

    def one_match(t):
        return find_matches_from_pointsets(
            t.entity_point_sets, shapes,
            entity_kinds=t.entity_kinds, strategy="chamfer",
        )

    # 2/3. single-template cost, fast vs slow path
    circ_one = timed(lambda: one_match(circ_templates[0]), max(args.repeats, 5))
    circ_n = len(circ_one.pop("_result").matches)
    poly_one = timed(lambda: one_match(poly_templates[0]), max(args.repeats, 5))
    poly_n = len(poly_one.pop("_result").matches)

    # 4. full sequential scan-all-style loop (the #1 ProcessPool target).
    all_t = circ_templates + poly_templates

    def scan_loop():
        out: dict[str, list[list[str]]] = {}
        for idx, t in enumerate(all_t):
            res = one_match(t)
            key = f"{t.class_name}.{idx}"
            for m in res.matches:
                out.setdefault(key, []).append(list(m.handles))
        return out

    scan = timed(scan_loop, args.repeats)
    scan_out = scan.pop("_result")

    # 5. contained-match suppression at scale (synthetic 10k+ instances).
    n_inst = max(args.suppression_instances, 1)
    supp_in = {"bga_ball.0": [[f"c{i:07d}"] for i in range(n_inst)]}
    supp = timed(lambda: suppress_contained_matches(
        {k: [list(x) for x in v] for k, v in supp_in.items()}), args.repeats)
    supp.pop("_result")

    # 6. serialization sizes (#2 gzip, #7 compact match JSON).
    match_indent = json.dumps(scan_out, indent=2).encode()
    match_compact = json.dumps(scan_out, separators=(",", ":")).encode()
    prim_json = json.dumps(prims, separators=(",", ":")).encode()
    sizes = {
        "match_json_indent2_bytes": len(match_indent),
        "match_json_compact_bytes": len(match_compact),
        "match_json_indent_over_compact": round(
            len(match_indent) / max(len(match_compact), 1), 3),
        "primitives_json_bytes": len(prim_json),
        "primitives_json_gzip_bytes": len(gzip.compress(prim_json, 6)),
        "primitives_gzip_ratio": round(
            len(prim_json) / max(len(gzip.compress(prim_json, 6)), 1), 2),
    }

    return {
        "config": {
            "circles": n_c, "polylines": n_p,
            "circle_templates": len(circ_templates),
            "polyline_templates": len(poly_templates),
            "templates_total": len(all_t),
            "repeats": args.repeats,
            "shapes_built": len(shapes),
        },
        "timings": {
            "build_shapes": bs,
            "circle_template_one": {**circ_one, "matches": circ_n},
            "polyline_template_one": {**poly_one, "matches": poly_n},
            "scan_all_loop": scan,
            "suppression_10k": {**supp, "instances": n_inst},
        },
        "sizes": sizes,
        "env": _env(),
    }


def _env() -> dict:
    import numpy
    import os
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            text=True).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
            text=True).strip()
    except Exception:
        commit = branch = "unknown"
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit, "git_branch": branch,
        "python": sys.version.split()[0], "numpy": numpy.__version__,
        "cpu_count": os.cpu_count(),
    }


def _print(report: dict, baseline: dict | None) -> None:
    c = report["config"]
    print(f"\n=== SMDR2 perf baseline · {report['env']['git_branch']}@"
          f"{report['env']['git_commit']} · {report['env']['timestamp']} ===")
    print(f"drawing: {c['circles']:,} circles + {c['polylines']} polylines "
          f"= {c['shapes_built']:,} shapes · {c['templates_total']} templates "
          f"({c['circle_templates']} circle + {c['polyline_templates']} poly) "
          f"· repeats={c['repeats']}")

    base_t = (baseline or {}).get("timings", {})
    print(f"\n{'metric':<24}{'median ms':>14}{'baseline':>14}{'delta':>12}")
    for k, v in report["timings"].items():
        cur = v["median_ms"]
        b = base_t.get(k, {}).get("median_ms")
        if b:
            d = f"{(cur - b) / b * 100:+.1f}%"
            bs = f"{b:,.2f}"
        else:
            d, bs = "—", "—"
        extra = ""
        if "matches" in v:
            extra = f"  ({v['matches']:,} matches)"
        if "instances" in v:
            extra = f"  ({v['instances']:,} inst)"
        print(f"{k:<24}{cur:>14,.2f}{bs:>14}{d:>12}{extra}")

    s = report["sizes"]
    print(f"\nsizes: match-JSON indent2={s['match_json_indent2_bytes']:,}B "
          f"vs compact={s['match_json_compact_bytes']:,}B "
          f"({s['match_json_indent_over_compact']}x) · "
          f"primitives={s['primitives_json_bytes']:,}B "
          f"-> gzip {s['primitives_json_gzip_bytes']:,}B "
          f"({s['primitives_gzip_ratio']}x)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--circles", type=int, default=200_000)
    ap.add_argument("--polylines", type=int, default=300)
    ap.add_argument("--circle-templates", type=int, default=30)
    ap.add_argument("--polyline-templates", type=int, default=20)
    ap.add_argument("--suppression-instances", type=int, default=10_000)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--baseline", default=None,
                    help="prior results JSON to diff against")
    args = ap.parse_args()

    report = run(args)
    base = json.loads(Path(args.baseline).read_text()) if args.baseline else None
    _print(report, base)

    out_dir = ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.label}.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
