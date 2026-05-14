// Pure-geometry helpers for the measure-distance tool.
//
// Everything in this file is DOM-free and side-effect-free so it can be
// unit-tested under `node --test`. Callers (the canvas wrappers in
// `canvas.js`) bind world-state — primitives, bboxes, pickbox tol, etc. —
// and pass it in as arguments.
//
// See tests/measure_core.test.mjs.

// Closest point on segment [a, b] to point p, clamped to the segment.
// All inputs are [x, y] arrays.
export function closestPointOnSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return [a[0], a[1]];
  let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
  if (t < 0) t = 0; else if (t > 1) t = 1;
  return [a[0] + t * dx, a[1] + t * dy];
}

// Bounding box of a single primitive ({line, polyline, filled_polygon, point}).
// Returns [xmin, ymin, xmax, ymax].
export function bboxOfPrimitive(p) {
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  const acc = (x, y) => {
    if (x < xmin) xmin = x; if (y < ymin) ymin = y;
    if (x > xmax) xmax = x; if (y > ymax) ymax = y;
  };
  switch (p.type) {
    case "line":           acc(p.start[0], p.start[1]); acc(p.end[0], p.end[1]); break;
    case "polyline":       for (const [x, y] of p.points) acc(x, y); break;
    case "filled_polygon": for (const r of p.rings) for (const [x, y] of r) acc(x, y); break;
    case "point":          acc(p.pos[0], p.pos[1]); break;
  }
  return [xmin, ymin, xmax, ymax];
}

// Circle detection. A closed polyline / single-ring filled_polygon with
// ≥ minVerts vertices whose distances from the centroid satisfy
// (rmax − rmin) / rmean ≤ radialTol is treated as a circle.
export const CIRCLE_MIN_VERTS = 8;
export const CIRCLE_RADIAL_TOL = 0.02;

export function detectCircle(pts, opts = {}) {
  const minVerts = opts.minVerts ?? CIRCLE_MIN_VERTS;
  const radialTol = opts.radialTol ?? CIRCLE_RADIAL_TOL;
  if (!pts || pts.length < minVerts) return null;
  const last = pts[pts.length - 1], first = pts[0];
  const n = (last[0] === first[0] && last[1] === first[1]) ? pts.length - 1 : pts.length;
  if (n < minVerts) return null;

  let sx = 0, sy = 0;
  for (let i = 0; i < n; i++) { sx += pts[i][0]; sy += pts[i][1]; }
  const cx = sx / n, cy = sy / n;

  let rmin = Infinity, rmax = 0, rsum = 0;
  for (let i = 0; i < n; i++) {
    const r = Math.hypot(pts[i][0] - cx, pts[i][1] - cy);
    if (r < rmin) rmin = r;
    if (r > rmax) rmax = r;
    rsum += r;
  }
  const rmean = rsum / n;
  if (rmean < 1e-9) return null;
  if ((rmax - rmin) / rmean > radialTol) return null;
  return { cx, cy, r: rmean };
}

// OSNAP resolver. Returns { x, y, kind } where kind is one of
// "endpoint" | "midpoint" | "center" | "quadrant" | "nearest" | "free".
// Priority: endpoint → midpoint → center → quadrant → nearest → free.
//
// Args (all required except primCircles which can be a sparse array):
//   wx, wy        — cursor in world coordinates
//   primitives    — array of primitive dicts (line/polyline/filled_polygon/point)
//   primBBoxes    — parallel array of [xmin, ymin, xmax, ymax] tight bboxes
//   primCircles   — parallel array of null | { cx, cy, r } (see detectCircle)
//   tol           — pickbox tolerance in world units
export function resolveSnap({ wx, wy, primitives, primBBoxes, primCircles, tol }) {
  const tol2 = tol * tol;
  let bestEnd = null,  bestEndD2  = tol2;
  let bestMid = null,  bestMidD2  = tol2;
  let bestCen = null,  bestCenD2  = tol2;
  let bestQua = null,  bestQuaD2  = tol2;
  let bestNear = null, bestNearD2 = tol2;

  for (let i = 0; i < primitives.length; i++) {
    const p = primitives[i];
    if (p.decorative) continue;
    const [bxmin, bymin, bxmax, bymax] = primBBoxes[i];
    if (wx < bxmin - tol || wx > bxmax + tol || wy < bymin - tol || wy > bymax + tol) continue;

    const circle = primCircles[i];
    if (circle) {
      const { cx, cy, r } = circle;
      {
        const dx = wx - cx, dy = wy - cy;
        const d2 = dx*dx + dy*dy;
        if (d2 <= bestCenD2) { bestCenD2 = d2; bestCen = [cx, cy]; }
      }
      const quads = [[cx + r, cy], [cx - r, cy], [cx, cy + r], [cx, cy - r]];
      for (const [qx, qy] of quads) {
        const dx = wx - qx, dy = wy - qy;
        const d2 = dx*dx + dy*dy;
        if (d2 <= bestQuaD2) { bestQuaD2 = d2; bestQua = [qx, qy]; }
      }
      const dxc = wx - cx, dyc = wy - cy;
      const dC = Math.hypot(dxc, dyc);
      if (dC > 1e-9) {
        const nx = cx + (dxc / dC) * r;
        const ny = cy + (dyc / dC) * r;
        const ex = wx - nx, ey = wy - ny;
        const d2 = ex*ex + ey*ey;
        if (d2 <= bestNearD2) { bestNearD2 = d2; bestNear = [nx, ny]; }
      }
      continue;
    }

    const verts = [];
    const segs = [];
    switch (p.type) {
      case "line":
        verts.push(p.start, p.end);
        segs.push([p.start[0], p.start[1], p.end[0], p.end[1]]);
        break;
      case "polyline": {
        const pts = p.points;
        for (const v of pts) verts.push(v);
        for (let j = 1; j < pts.length; j++) {
          segs.push([pts[j-1][0], pts[j-1][1], pts[j][0], pts[j][1]]);
        }
        if (p.closed && pts.length > 2) {
          const a = pts[pts.length - 1], b = pts[0];
          segs.push([a[0], a[1], b[0], b[1]]);
        }
        break;
      }
      case "filled_polygon":
        for (const ring of p.rings) {
          for (const v of ring) verts.push(v);
          for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
            segs.push([ring[k][0], ring[k][1], ring[j][0], ring[j][1]]);
          }
        }
        break;
      case "point":
        verts.push(p.pos);
        break;
    }

    for (const [vx, vy] of verts) {
      const dx = wx - vx, dy = wy - vy;
      const d2 = dx * dx + dy * dy;
      if (d2 <= bestEndD2) { bestEndD2 = d2; bestEnd = [vx, vy]; }
    }
    for (const [ax, ay, bx, by] of segs) {
      const mx = (ax + bx) / 2, my = (ay + by) / 2;
      const dx = wx - mx, dy = wy - my;
      const d2 = dx * dx + dy * dy;
      if (d2 <= bestMidD2) { bestMidD2 = d2; bestMid = [mx, my]; }
    }
    for (const [ax, ay, bx, by] of segs) {
      const foot = closestPointOnSegment([wx, wy], [ax, ay], [bx, by]);
      const dx = wx - foot[0], dy = wy - foot[1];
      const d2 = dx * dx + dy * dy;
      if (d2 <= bestNearD2) { bestNearD2 = d2; bestNear = foot; }
    }
  }

  if (bestEnd)  return { x: bestEnd[0],  y: bestEnd[1],  kind: "endpoint" };
  if (bestMid)  return { x: bestMid[0],  y: bestMid[1],  kind: "midpoint" };
  if (bestCen)  return { x: bestCen[0],  y: bestCen[1],  kind: "center" };
  if (bestQua)  return { x: bestQua[0],  y: bestQua[1],  kind: "quadrant" };
  if (bestNear) return { x: bestNear[0], y: bestNear[1], kind: "nearest" };
  return { x: wx, y: wy, kind: "free" };
}

// Ortho-constrain a candidate measure point against the chain anchor when
// Shift is held. The off-axis coordinate is locked to the anchor; the
// on-axis coordinate is taken from an injected resolveSnapFn(wx, wy) when
// available — so OSNAP still pulls to a vertex's X (or Y), while the rest
// of the rubber-band stays on the axis line.
//
// Args:
//   wx, wy         — cursor in world coords
//   anchor         — [x, y] world coord of the latest chain pick (or null)
//   shiftKey       — boolean
//   resolveSnapFn  — function (wx, wy) → { x, y, kind } (the snap resolver
//                    pre-bound to current world-state). Used to fetch the
//                    on-axis coordinate from a real geometry target.
// Returns:
//   null when ortho should not apply (no shift, no anchor),
//   otherwise { x, y, kind } where kind preserves the snap's original kind
//   (so the marker still renders) or "free" when no snap was found.
export function applyOrtho({ wx, wy, anchor, shiftKey, resolveSnapFn }) {
  if (!shiftKey || !anchor) return null;
  const [fx, fy] = anchor;
  const snap = resolveSnapFn(wx, wy);
  const useSnap = snap.kind !== "free";
  const dx = wx - fx, dy = wy - fy;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return { x: useSnap ? snap.x : wx, y: fy, kind: useSnap ? snap.kind : "free" };
  }
  return { x: fx, y: useSnap ? snap.y : wy, kind: useSnap ? snap.kind : "free" };
}
