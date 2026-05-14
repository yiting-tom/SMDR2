// Unit tests for app/static/measure_core.js.
// Run via `node --test tests/measure_core.test.mjs`, or indirectly through
// pytest (see tests/test_measure_core_node.py).

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  closestPointOnSegment,
  bboxOfPrimitive,
  detectCircle,
  resolveSnap,
  applyOrtho,
} from "../app/static/measure_core.js";

// ---- helpers ---------------------------------------------------------------

function approxEqual(actual, expected, eps = 1e-9, msg = "") {
  assert.ok(Math.abs(actual - expected) <= eps,
    `${msg} expected ≈ ${expected}, got ${actual}`);
}

function approxPoint(actual, expected, eps = 1e-9, msg = "") {
  approxEqual(actual[0], expected[0], eps, `${msg} x`);
  approxEqual(actual[1], expected[1], eps, `${msg} y`);
}

// Build N sample points on a circle (centered at [cx, cy], radius r).
function sampleCircle(cx, cy, r, n, closed = true) {
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    pts.push([cx + r * Math.cos(t), cy + r * Math.sin(t)]);
  }
  if (closed) pts.push([pts[0][0], pts[0][1]]);  // repeat first vertex
  return pts;
}

// Make a world for resolveSnap with N primitives, bboxes, and circles.
function worldOf(primitives, primCircles = null) {
  const bboxes = primitives.map(p => bboxOfPrimitive(p));
  const circles = primCircles ?? primitives.map(() => null);
  return { primitives, primBBoxes: bboxes, primCircles: circles };
}

// ---- closestPointOnSegment -------------------------------------------------

test("closestPointOnSegment: point before segment clamps to A", () => {
  approxPoint(closestPointOnSegment([-1, 0], [0, 0], [10, 0]), [0, 0]);
});

test("closestPointOnSegment: point past segment clamps to B", () => {
  approxPoint(closestPointOnSegment([15, 0], [0, 0], [10, 0]), [10, 0]);
});

test("closestPointOnSegment: perpendicular drops to the foot", () => {
  approxPoint(closestPointOnSegment([5, 3], [0, 0], [10, 0]), [5, 0]);
});

test("closestPointOnSegment: degenerate segment returns A", () => {
  approxPoint(closestPointOnSegment([7, 9], [3, 4], [3, 4]), [3, 4]);
});

// ---- detectCircle ----------------------------------------------------------

test("detectCircle: null on undefined / too-few-vertices input", () => {
  assert.equal(detectCircle(undefined), null);
  assert.equal(detectCircle([]), null);
  assert.equal(detectCircle([[0, 0], [1, 0], [1, 1], [0, 1]]), null);  // 4 verts (rectangle)
});

test("detectCircle: 16-vertex unit circle at origin → detected", () => {
  const pts = sampleCircle(0, 0, 1, 16);
  const c = detectCircle(pts);
  assert.ok(c, "expected detection");
  approxEqual(c.cx, 0, 1e-9, "cx");
  approxEqual(c.cy, 0, 1e-9, "cy");
  approxEqual(c.r, 1, 1e-9, "r");
});

test("detectCircle: offset circle (cx=50, cy=50, r=0.4)", () => {
  const pts = sampleCircle(50, 50, 0.4, 14);
  const c = detectCircle(pts);
  assert.ok(c);
  approxEqual(c.cx, 50, 1e-9);
  approxEqual(c.cy, 50, 1e-9);
  approxEqual(c.r, 0.4, 1e-9);
});

test("detectCircle: rejects a rounded rectangle (straight sides)", () => {
  // Rounded-rect approximation: 4 quarter-arcs at the corners + 4 long sides.
  // Concretely: corners are 5-vertex quarter arcs, sides are pure straight
  // segments — the side midpoints sit much farther from the centroid than
  // the corner vertices, so radial variance is large.
  const w = 4, h = 2, r = 0.2;
  const pts = [];
  // Bottom edge (left → right), going across the long straight side.
  for (let i = 0; i <= 10; i++) pts.push([(-w / 2 + r) + (w - 2 * r) * (i / 10), -h / 2]);
  // Right edge.
  for (let i = 0; i <= 10; i++) pts.push([w / 2, (-h / 2 + r) + (h - 2 * r) * (i / 10)]);
  // Top edge.
  for (let i = 0; i <= 10; i++) pts.push([(w / 2 - r) - (w - 2 * r) * (i / 10), h / 2]);
  // Left edge.
  for (let i = 0; i <= 10; i++) pts.push([-w / 2, (h / 2 - r) - (h - 2 * r) * (i / 10)]);
  assert.equal(detectCircle(pts), null, "rounded rect must not be detected as a circle");
});

test("detectCircle: noisy circle within tol is detected, outside tol is not", () => {
  const pts = sampleCircle(0, 0, 1, 20, false);
  // Perturb every vertex by ±0.005 radially (< 0.02 mean tol).
  for (let i = 0; i < pts.length; i++) {
    const [x, y] = pts[i];
    const r = Math.hypot(x, y);
    const factor = 1 + ((i % 2 === 0) ? +0.005 : -0.005);
    pts[i] = [x / r * factor, y / r * factor];
  }
  assert.ok(detectCircle(pts), "within tol should detect");

  // Now exaggerate the perturbation to break the heuristic.
  const noisy = sampleCircle(0, 0, 1, 20, false).map(([x, y], i) =>
    (i % 2 === 0) ? [x * 1.1, y * 1.1] : [x * 0.9, y * 0.9]
  );
  assert.equal(detectCircle(noisy), null, "outside tol should reject");
});

// ---- bboxOfPrimitive -------------------------------------------------------

test("bboxOfPrimitive: line", () => {
  const bb = bboxOfPrimitive({ type: "line", start: [1, 2], end: [4, 5] });
  assert.deepEqual(bb, [1, 2, 4, 5]);
});

test("bboxOfPrimitive: polyline", () => {
  const bb = bboxOfPrimitive({ type: "polyline", points: [[0, 0], [3, 1], [-2, 5]] });
  assert.deepEqual(bb, [-2, 0, 3, 5]);
});

// ---- resolveSnap -----------------------------------------------------------

test("resolveSnap: free point when nothing is in pickbox", () => {
  const world = worldOf([{ type: "line", start: [100, 100], end: [200, 100] }]);
  const r = resolveSnap({ wx: 0, wy: 0, ...world, tol: 1 });
  assert.equal(r.kind, "free");
  approxPoint([r.x, r.y], [0, 0]);
});

test("resolveSnap: endpoint snap on a line's start", () => {
  const world = worldOf([{ type: "line", start: [10, 10], end: [20, 10] }]);
  const r = resolveSnap({ wx: 10.1, wy: 10.1, ...world, tol: 1 });
  assert.equal(r.kind, "endpoint");
  approxPoint([r.x, r.y], [10, 10]);
});

test("resolveSnap: midpoint of a line beats nearest-on-edge", () => {
  const world = worldOf([{ type: "line", start: [0, 0], end: [10, 0] }]);
  const r = resolveSnap({ wx: 5, wy: 0.2, ...world, tol: 1 });
  assert.equal(r.kind, "midpoint");
  approxPoint([r.x, r.y], [5, 0]);
});

test("resolveSnap: nearest-on-edge fires off-midpoint", () => {
  const world = worldOf([{ type: "line", start: [0, 0], end: [10, 0] }]);
  // Hover at (3, 0.4): inside pickbox of the segment, far from endpoints
  // and from midpoint (5, 0). Expect nearest = (3, 0).
  const r = resolveSnap({ wx: 3, wy: 0.4, ...world, tol: 1 });
  assert.equal(r.kind, "nearest");
  approxPoint([r.x, r.y], [3, 0]);
});

test("resolveSnap: endpoint wins over midpoint when both in pickbox", () => {
  // Short line — endpoint and midpoint pickboxes overlap.
  const world = worldOf([{ type: "line", start: [0, 0], end: [1, 0] }]);
  const r = resolveSnap({ wx: 0.1, wy: 0.1, ...world, tol: 1 });
  assert.equal(r.kind, "endpoint");
  approxPoint([r.x, r.y], [0, 0]);
});

test("resolveSnap: decorative primitives are ignored", () => {
  const prims = [{ type: "line", start: [0, 0], end: [10, 0], decorative: true }];
  const world = worldOf(prims);
  const r = resolveSnap({ wx: 0.1, wy: 0.1, ...world, tol: 1 });
  assert.equal(r.kind, "free");
});

test("resolveSnap: center snap on a circle primitive", () => {
  const pts = sampleCircle(50, 50, 0.4, 14, false);
  const prims = [{ type: "polyline", points: pts, closed: true }];
  const circles = [{ cx: 50, cy: 50, r: 0.4 }];
  const world = worldOf(prims, circles);
  const r = resolveSnap({ wx: 50.05, wy: 50.05, ...world, tol: 1 });
  assert.equal(r.kind, "center");
  approxPoint([r.x, r.y], [50, 50]);
});

test("resolveSnap: quadrant snap on circle's right cardinal point", () => {
  const pts = sampleCircle(0, 0, 1, 20, false);
  const prims = [{ type: "polyline", points: pts, closed: true }];
  const circles = [{ cx: 0, cy: 0, r: 1 }];
  const world = worldOf(prims, circles);
  // Hover near (1.02, 0.02): out of center-pickbox, into quadrant-right
  // pickbox.
  const r = resolveSnap({ wx: 1.02, wy: 0.02, ...world, tol: 0.1 });
  assert.equal(r.kind, "quadrant");
  approxPoint([r.x, r.y], [1, 0]);
});

test("resolveSnap: circle primitive's polyline vertices do NOT fire endpoint", () => {
  // A 16-vertex circle's vertices land at angles 0°, 22.5°, 45° ...
  // When primCircles[i] is set, those vertices should not be available as
  // endpoint snaps. Hover near the 22.5° vertex (which is not a cardinal
  // quadrant point), expect center / quadrant / nearest — never endpoint.
  const r = 1;
  const ang = (Math.PI * 2) / 16;
  const pts = sampleCircle(0, 0, r, 16, false);
  const prims = [{ type: "polyline", points: pts, closed: true }];
  const circles = [{ cx: 0, cy: 0, r }];
  const world = worldOf(prims, circles);
  const vx = r * Math.cos(ang), vy = r * Math.sin(ang);
  const snap = resolveSnap({ wx: vx + 0.01, wy: vy + 0.01, ...world, tol: 0.1 });
  assert.notEqual(snap.kind, "endpoint");
});

// ---- applyOrtho ------------------------------------------------------------

function makeSnapFn(fixedResult) {
  return () => fixedResult;
}

test("applyOrtho: returns null when shiftKey is false", () => {
  const r = applyOrtho({
    wx: 5, wy: 3,
    anchor: [0, 0],
    shiftKey: false,
    resolveSnapFn: makeSnapFn({ x: 5, y: 3, kind: "free" }),
  });
  assert.equal(r, null);
});

test("applyOrtho: returns null when anchor is missing", () => {
  const r = applyOrtho({
    wx: 5, wy: 3,
    anchor: null,
    shiftKey: true,
    resolveSnapFn: makeSnapFn({ x: 5, y: 3, kind: "free" }),
  });
  assert.equal(r, null);
});

test("applyOrtho: horizontal lock when |dx| > |dy|, free point", () => {
  const r = applyOrtho({
    wx: 20, wy: 12,
    anchor: [10, 10],
    shiftKey: true,
    resolveSnapFn: makeSnapFn({ x: 20, y: 12, kind: "free" }),
  });
  assert.deepEqual(r, { x: 20, y: 10, kind: "free" });
});

test("applyOrtho: vertical lock when |dy| > |dx|, free point", () => {
  const r = applyOrtho({
    wx: 11, wy: 30,
    anchor: [10, 10],
    shiftKey: true,
    resolveSnapFn: makeSnapFn({ x: 11, y: 30, kind: "free" }),
  });
  assert.deepEqual(r, { x: 10, y: 30, kind: "free" });
});

test("applyOrtho: horizontal lock with snap target takes snap.x, preserves kind", () => {
  // Cursor moved horizontally — vertex at (50, 12) is in the snap pickbox.
  // Expected: ortho horizontal → (50, anchor.y=10), kind preserved.
  const r = applyOrtho({
    wx: 49.9, wy: 12.1,
    anchor: [10, 10],
    shiftKey: true,
    resolveSnapFn: makeSnapFn({ x: 50, y: 12, kind: "endpoint" }),
  });
  assert.deepEqual(r, { x: 50, y: 10, kind: "endpoint" });
});

test("applyOrtho: vertical lock with snap target takes snap.y, preserves kind", () => {
  const r = applyOrtho({
    wx: 11, wy: 29.9,
    anchor: [10, 10],
    shiftKey: true,
    resolveSnapFn: makeSnapFn({ x: 12, y: 30, kind: "center" }),
  });
  assert.deepEqual(r, { x: 10, y: 30, kind: "center" });
});

test("applyOrtho: axis chosen from raw cursor delta, not snap delta", () => {
  // Raw cursor at (50.05, 11), anchor at (10, 10):
  //   |dx| = 40.05, |dy| = 1 → horizontal lock.
  // The snap target happens to lie at (50, 50) — a much bigger Δy.
  // If we'd used the snap delta we'd lock vertical and lose the user's
  // intent. We must lock horizontal.
  const r = applyOrtho({
    wx: 50.05, wy: 11,
    anchor: [10, 10],
    shiftKey: true,
    resolveSnapFn: makeSnapFn({ x: 50, y: 50, kind: "endpoint" }),
  });
  assert.equal(r.kind, "endpoint");
  approxEqual(r.x, 50, 1e-9, "horizontal lock → snap.x");
  approxEqual(r.y, 10, 1e-9, "horizontal lock → anchor.y");
});
