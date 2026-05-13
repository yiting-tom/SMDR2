// Canvas DXF viewer — vanilla JS, AutoCAD-style interactions.
//
// World coords: DXF mm-scale, +Y up.
// Screen coords: canvas device-px, +Y down.
//
// Interactions (AutoCAD conventions):
//   middle-drag           → pan
//   wheel                 → zoom (cursor-anchored)
//   left-click on entity  → single pick (replaces selection)
//   shift+left-click      → toggle entity in selection
//   left-drag L→R         → Window select (blue, fully-inside)
//   left-drag R→L         → Crossing select (green, bbox-intersect)
//   Esc                   → clear selection / cancel current drag
//
// Chain mode (button toggle):
//   single-click expands selection to all primitives connected via shared
//   endpoints (within CONNECT_TOL). Affects single-click only — box-select
//   stays as is.

const $canvas = document.getElementById("dxf-canvas");
const $status = document.getElementById("status");
const $coords = document.getElementById("cursor-coords");
const $handle = document.getElementById("handle-info");
const $chainBtn = document.getElementById("chain-btn");
const $scanAllBtn = document.getElementById("scan-all-btn");
const $saveMatchBtn = document.getElementById("save-match-btn");
const $libraryBtn = document.getElementById("library-btn");
const $libraryModal = document.getElementById("library-modal");
const $libraryBody = document.getElementById("library-body");
const $librarySummary = document.getElementById("library-summary");
const $productContext = document.getElementById("product-context");
const $roleSwitcher = document.getElementById("role-switcher");

// Rule-check focus state — populated when the viewer is opened with
// ?rule=<name>&idx=<i> from the dashboard's rule-result modal.
let focusedSubRule = null;
// {ruleName, rulePass, ruleText, part, from, to, text, idx}
const $modeHint = document.getElementById("mode-hint");
const $classToolbar = document.getElementById("class-toolbar");
const ctx = $canvas.getContext("2d");

const FILE_ID = document.body.dataset.fileId;
const API = {
  primitives:    () => `/api/files/${FILE_ID}/primitives`,
  fileInfo:      () => `/api/files/${FILE_ID}`,
  match:         () => `/api/files/${FILE_ID}/match`,
  commit:        () => `/api/files/${FILE_ID}/commit`,
  scanAll:       () => `/api/files/${FILE_ID}/scan-all`,
  prematch:      () => `/api/files/${FILE_ID}/prematch`,
  matchJson:     () => `/api/files/${FILE_ID}/match-json`,
  // Classes/templates are file-scoped via the ?file_id= query — the server
  // resolves to the file's library.
  classes:       () => `/api/classes?file_id=${FILE_ID}`,
  templates:     () => `/api/templates?file_id=${FILE_ID}`,
  templateOne:   (id) => `/api/templates/${id}`,
};

const $librarySwitcher = document.getElementById("library-switcher");

// Returned from loadFileInfo so the bootstrap can run the focused-rule
// fetch only when the file lives inside a product.
let currentFileInfo = null;

async function loadFileInfo() {
  const [fileRes, libsRes] = await Promise.all([
    fetch(API.fileInfo()),
    fetch("/api/libraries"),
  ]);
  if (!fileRes.ok || !libsRes.ok) return;
  const file = await fileRes.json();
  currentFileInfo = file;
  const libs = (await libsRes.json()).libraries;
  $librarySwitcher.innerHTML = "";
  for (const lib of libs) {
    const opt = document.createElement("option");
    opt.value = lib.id;
    opt.textContent = lib.name;
    if (lib.id === file.library_id) opt.selected = true;
    $librarySwitcher.appendChild(opt);
  }

  // Product context + sibling-DXF switcher.
  if (file.product_id) {
    const pRes = await fetch(`/api/products/${file.product_id}`);
    if (pRes.ok) {
      const p = await pRes.json();
      $productContext.textContent = `${p.name} / ${file.dxf_role}`;
      $roleSwitcher.innerHTML = "";
      for (const role of ["SBT", "BD", "POD", "RING"]) {
        const sibling = p.files_by_role[role];
        const btn = document.createElement("a");
        btn.className = "role-btn";
        btn.dataset.role = role;
        btn.textContent = role;
        if (role === file.dxf_role) {
          btn.classList.add("current");
        } else if (sibling) {
          btn.href = `/viewer/${sibling.id}`;
        } else {
          btn.classList.add("empty");
          btn.title = `${role} not uploaded yet`;
        }
        $roleSwitcher.appendChild(btn);
      }
    }
  } else {
    $productContext.textContent = "";
    $roleSwitcher.innerHTML = "";
  }
}

$librarySwitcher.addEventListener("change", async () => {
  const newLibId = $librarySwitcher.value;
  if (!confirm("Switching libraries will re-process this file against the new library's templates. Continue?")) {
    // revert UI
    await loadFileInfo();
    return;
  }
  $librarySwitcher.disabled = true;
  setBaseStatus("switching library — re-processing…");
  try {
    const res = await fetch(API.fileInfo(), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ library_id: newLibId }),
    });
    if (!res.ok) {
      console.error(await res.text());
      setBaseStatus(`switch failed: ${res.status}`);
      $librarySwitcher.disabled = false;
      return;
    }
    // The file is back in `preprocessing`; reload viewer so prematch + classes
    // refresh against the new library once it finishes.
    window.location.reload();
  } catch (e) {
    console.error(e);
    setBaseStatus(`switch error: ${e.message}`);
    $librarySwitcher.disabled = false;
  }
});

const view = { cx: 0, cy: 0, zoom: 1 };
let primitives = [];
let primBBoxes = [];          // parallel: [xmin, ymin, xmax, ymax]
let background = "#1a1f26";
let dpr = window.devicePixelRatio || 1;

const selection = new Set();  // DXF entity handles (user-picked template)
const matchSet = new Set();   // handles found by /api/match (preview)
const nearMissSet = new Set();// handles that passed pre-filter but failed alignment (debug)
const hoverSet = new Set();   // ephemeral highlight (rule-check panel hover, etc.)
const pinnedSet = new Set();  // persistent highlight from a clicked rule
let pinnedRuleName = null;    // name of the currently-pinned rule (or null)

// Drag state. kind ∈ {"pan", "box", "click_pending"}.
let drag = null;

const PICKBOX_CSS_PX = 5;      // tolerance for single-pick (in CSS px)
const CLICK_DRAG_THRESHOLD_CSS = 3;  // drag exceeding this is no longer a click
const CONNECT_TOL = 0.01;            // endpoint match tolerance (world units / mm)

let chainMode = false;
let connectivityGraph = null;   // lazy: Array<Set<number>>  parallel to primitives

// Add-mode state machine. addModeClass: null = idle; else holds the class name
// currently being staged. matchesStaged: true once S has populated matchSet for
// the current selection (button shows ✓).
let addModeClass = null;
let matchesStaged = false;
let classes = [];               // [{name, count}, ...]
const HOTKEYS = "1234567890qwertyuiop".split("");  // up to 20 slots

const HIGHLIGHT_COLOR = "#00ffff";
const NEARMISS_COLOR = "#ff8800";
const HOVER_COLOR = "#ffeb3b";  // ephemeral (rule-check hover)
const HIGHLIGHT_WIDTH_MULT = 2.5;

// Per-class colors for Scan All overlay. Chosen for contrast on the DXF's
// dark background and for mutual distinguishability.
const CLASS_COLORS = {
  smd:           "#ff5252",  // red
  substrate:     "#69f0ae",  // mint
  die_area:      "#ffeb3b",  // yellow
  lid_outer:     "#ba68c8",  // purple
  lid_inner:     "#f06292",  // pink
  bga_ball:      "#ffab40",  // orange
  pin_mark:      "#f48fb1",  // soft pink
  fiducial_mark: "#4dd0e1",  // teal
  "2d_barcode":  "#c6ff00",  // lime
};
const FALLBACK_CLASS_COLOR = "#888888";
function classColor(name) { return CLASS_COLORS[name] ?? FALLBACK_CLASS_COLOR; }

// scan-all overlay state: handle → class_name (for fast render lookup)
let scanAllByHandle = null;   // null = inactive; Map<handle, className> when active
let scanAllSummary = null;    // { byClass: {name: count}, total }

const STYLE_WINDOW = {
  fill:   "rgba(80, 130, 255, 0.10)",
  stroke: "rgba(120, 170, 255, 0.90)",
  dashed: false,
};
const STYLE_CROSSING = {
  fill:   "rgba(80, 255, 130, 0.10)",
  stroke: "rgba(140, 255, 170, 0.90)",
  dashed: true,
};

// ---- sizing ---------------------------------------------------------------
function resize() {
  dpr = window.devicePixelRatio || 1;
  const rect = $canvas.getBoundingClientRect();
  const newW = Math.round(rect.width * dpr);
  const newH = Math.round(rect.height * dpr);
  if (newW === $canvas.width && newH === $canvas.height) return;
  $canvas.width = newW;
  $canvas.height = newH;
  render();
}
window.addEventListener("resize", resize);

// Layout shifts (e.g., class toolbar populated after fetch) change the
// canvas's CSS size without firing window.resize. Without this observer,
// the canvas internal buffer stays at the pre-shift size and mouse coords
// no longer map correctly to world coords.
new ResizeObserver(resize).observe($canvas);

// ---- coordinate transforms -----------------------------------------------
function screenToWorld(sx, sy) {
  return [
    (sx - $canvas.width / 2) / view.zoom + view.cx,
    -(sy - $canvas.height / 2) / view.zoom + view.cy,
  ];
}
function eventToScreen(e) {
  const rect = $canvas.getBoundingClientRect();
  return [(e.clientX - rect.left) * dpr, (e.clientY - rect.top) * dpr];
}
function eventToWorld(e) {
  const [sx, sy] = eventToScreen(e);
  return screenToWorld(sx, sy);
}

// ---- fit-to-view ----------------------------------------------------------
function fitToBbox(bbox) {
  if (!bbox) return;
  const [xmin, ymin, xmax, ymax] = bbox;
  view.cx = (xmin + xmax) / 2;
  view.cy = (ymin + ymax) / 2;
  const w = xmax - xmin, h = ymax - ymin;
  if (w <= 0 || h <= 0) { view.zoom = 1; return; }
  view.zoom = Math.min(($canvas.width / w) * 0.92, ($canvas.height / h) * 0.92);
}

// ---- bbox precomputation -------------------------------------------------
function computeBBoxes() {
  primBBoxes = new Array(primitives.length);
  for (let i = 0; i < primitives.length; i++) primBBoxes[i] = bboxOf(primitives[i]);
}

// Per-handle aggregate stats — vertex count + bbox + path length — useful
// for diagnosing "why didn't this match?" by eyeballing two handles side by side.
let handleStats = null;
function ensureHandleStats() {
  if (handleStats) return handleStats;
  handleStats = new Map();
  const acc = (s, x, y) => {
    if (x < s.xmin) s.xmin = x;
    if (y < s.ymin) s.ymin = y;
    if (x > s.xmax) s.xmax = x;
    if (y > s.ymax) s.ymax = y;
  };
  const seglen = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  for (const p of primitives) {
    const h = p.handle;
    if (!h) continue;
    let s = handleStats.get(h);
    if (!s) {
      s = { vcount: 0, plen: 0, xmin: Infinity, ymin: Infinity, xmax: -Infinity, ymax: -Infinity };
      handleStats.set(h, s);
    }
    switch (p.type) {
      case "line":
        s.vcount += 2;
        acc(s, p.start[0], p.start[1]); acc(s, p.end[0], p.end[1]);
        s.plen += seglen(p.start, p.end);
        break;
      case "polyline": {
        const pts = p.points;
        s.vcount += pts.length;
        for (const [x, y] of pts) acc(s, x, y);
        for (let i = 1; i < pts.length; i++) s.plen += seglen(pts[i - 1], pts[i]);
        break;
      }
      case "filled_polygon":
        for (const r of p.rings) {
          s.vcount += r.length;
          for (const [x, y] of r) acc(s, x, y);
          for (let i = 1; i < r.length; i++) s.plen += seglen(r[i - 1], r[i]);
        }
        break;
      case "point":
        s.vcount += 1; acc(s, p.pos[0], p.pos[1]); break;
    }
  }
  return handleStats;
}

// Dump everything we know about a handle to console — copy-pasteable for debug.
function debugDumpHandle(handle) {
  const s = ensureHandleStats().get(handle);
  const prims = primitives.filter(p => p.handle === handle);
  console.log(`---- entity ${handle} ----`);
  console.log("stats:", s);
  console.log(`primitives (${prims.length}):`);
  for (const p of prims) console.log("  ", JSON.stringify(p));
}
function bboxOf(p) {
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

// ---- rendering ------------------------------------------------------------
function drawPrimitive(p, opts) {
  const { stroke, lineWidth, fill } = opts;
  switch (p.type) {
    case "line":
      ctx.beginPath();
      ctx.moveTo(p.start[0], p.start[1]); ctx.lineTo(p.end[0], p.end[1]);
      ctx.strokeStyle = stroke ?? p.color; ctx.lineWidth = lineWidth; ctx.stroke();
      break;
    case "polyline": {
      ctx.beginPath();
      const pts = p.points;
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      if (p.closed) ctx.closePath();
      ctx.strokeStyle = stroke ?? p.color; ctx.lineWidth = lineWidth; ctx.stroke();
      break;
    }
    case "filled_polygon":
      ctx.beginPath();
      for (const ring of p.rings) {
        if (ring.length < 3) continue;
        ctx.moveTo(ring[0][0], ring[0][1]);
        for (let i = 1; i < ring.length; i++) ctx.lineTo(ring[i][0], ring[i][1]);
        ctx.closePath();
      }
      ctx.fillStyle = fill ?? p.color; ctx.fill("evenodd");
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lineWidth; ctx.stroke(); }
      break;
    case "point":
      ctx.beginPath();
      ctx.arc(p.pos[0], p.pos[1], lineWidth * 1.5, 0, Math.PI * 2);
      ctx.fillStyle = fill ?? p.color; ctx.fill();
      break;
  }
}

function worldToScreen(x, y) {
  return [
    (x - view.cx) * view.zoom + $canvas.width / 2,
    -(y - view.cy) * view.zoom + $canvas.height / 2,
  ];
}

function render() {
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, $canvas.width, $canvas.height);
  ctx.save();
  ctx.translate($canvas.width / 2, $canvas.height / 2);
  ctx.scale(view.zoom, -view.zoom);
  ctx.translate(-view.cx, -view.cy);

  const hairline = (1 / view.zoom) * dpr;

  for (const p of primitives) drawPrimitive(p, { lineWidth: hairline });

  if (scanAllByHandle) {
    const hw = hairline * HIGHLIGHT_WIDTH_MULT;
    for (const p of primitives) {
      const cls = scanAllByHandle.get(p.handle);
      if (!cls) continue;
      if (selection.has(p.handle) || matchSet.has(p.handle) || nearMissSet.has(p.handle)) continue;
      const col = classColor(cls);
      drawPrimitive(p, { stroke: col, fill: col, lineWidth: hw });
    }
  }
  if (nearMissSet.size) {
    const hw = hairline * HIGHLIGHT_WIDTH_MULT;
    for (const p of primitives) {
      if (nearMissSet.has(p.handle) && !matchSet.has(p.handle) && !selection.has(p.handle)) {
        drawPrimitive(p, { stroke: NEARMISS_COLOR, fill: NEARMISS_COLOR, lineWidth: hw });
      }
    }
  }
  if (selection.size || matchSet.size) {
    const hw = hairline * HIGHLIGHT_WIDTH_MULT;
    for (const p of primitives) {
      if (selection.has(p.handle) || matchSet.has(p.handle)) {
        drawPrimitive(p, { stroke: HIGHLIGHT_COLOR, fill: HIGHLIGHT_COLOR, lineWidth: hw });
      }
    }
  }
  if (hoverSet.size || pinnedSet.size) {
    const hw = hairline * (HIGHLIGHT_WIDTH_MULT + 1);
    for (const p of primitives) {
      if (hoverSet.has(p.handle) || pinnedSet.has(p.handle)) {
        drawPrimitive(p, { stroke: HOVER_COLOR, fill: HOVER_COLOR, lineWidth: hw });
      }
    }
  }
  // Rule-check focused sub-rule: highlight from + to and draw an annotation
  // line between their centroids.
  if (focusedSubRule) {
    drawFocusedSubRule(hairline);
  }

  // Drag rectangle (window or crossing).
  if (drag && drag.kind === "box" && drag.currentWorld) {
    const [x1, y1] = drag.startWorld;
    const [x2, y2] = drag.currentWorld;
    const style = drag.mode === "window" ? STYLE_WINDOW : STYLE_CROSSING;
    const x = Math.min(x1, x2), y = Math.min(y1, y2);
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    ctx.fillStyle = style.fill;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = hairline * 1.5;
    if (style.dashed) ctx.setLineDash([6 * hairline, 4 * hairline]);
    ctx.strokeRect(x, y, w, h);
    if (style.dashed) ctx.setLineDash([]);
  }

  ctx.restore();

  // Screen-space annotations (don't scale with zoom).
  if (focusedSubRule) drawFocusedLabel();
}

// ---- focused sub-rule from rule check ------------------------------------
function computeHandlesCentroid(handles) {
  if (!handles || !handles.length) return null;
  ensureHandleStats();
  let sx = 0, sy = 0, n = 0;
  for (const h of handles) {
    const s = handleStats.get(h);
    if (!s) continue;
    sx += (s.xmin + s.xmax) / 2;
    sy += (s.ymin + s.ymax) / 2;
    n++;
  }
  if (n === 0) return null;
  return [sx / n, sy / n];
}

function drawFocusedSubRule(hairline) {
  const FOCUS_COLOR = focusedSubRule.rulePass ? "#69f0ae" : "#ff5252";
  const hw = hairline * (HIGHLIGHT_WIDTH_MULT + 1.5);
  const handles = new Set([...(focusedSubRule.from ?? []), ...(focusedSubRule.to ?? [])]);
  for (const p of primitives) {
    if (handles.has(p.handle)) {
      drawPrimitive(p, { stroke: FOCUS_COLOR, fill: FOCUS_COLOR, lineWidth: hw });
    }
  }
  const fc = computeHandlesCentroid(focusedSubRule.from);
  const tc = computeHandlesCentroid(focusedSubRule.to);
  if (fc && tc) {
    ctx.strokeStyle = FOCUS_COLOR;
    ctx.lineWidth = hairline * 2.2;
    ctx.setLineDash([8 * hairline, 5 * hairline]);
    ctx.beginPath();
    ctx.moveTo(fc[0], fc[1]);
    ctx.lineTo(tc[0], tc[1]);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawFocusedLabel() {
  const fc = computeHandlesCentroid(focusedSubRule.from);
  const tc = computeHandlesCentroid(focusedSubRule.to);
  let midX, midY;
  if (fc && tc) {
    [midX, midY] = worldToScreen((fc[0] + tc[0]) / 2, (fc[1] + tc[1]) / 2);
  } else if (fc) {
    [midX, midY] = worldToScreen(fc[0], fc[1]);
  } else if (tc) {
    [midX, midY] = worldToScreen(tc[0], tc[1]);
  } else {
    return;
  }
  const text = focusedSubRule.text || focusedSubRule.ruleText || "";
  if (!text) return;
  const FOCUS_COLOR = focusedSubRule.rulePass ? "#69f0ae" : "#ff5252";
  ctx.save();
  ctx.font = `${13 * dpr}px ui-monospace, monospace`;
  const m = ctx.measureText(text);
  const padX = 9 * dpr, padY = 5 * dpr;
  const tw = m.width + padX * 2;
  const th = 16 * dpr + padY * 2;
  const x = midX - tw / 2, y = midY - th - 10 * dpr;
  ctx.fillStyle = "rgba(0,0,0,0.85)";
  ctx.fillRect(x, y, tw, th);
  ctx.strokeStyle = FOCUS_COLOR;
  ctx.lineWidth = 1 * dpr;
  ctx.strokeRect(x, y, tw, th);
  ctx.fillStyle = FOCUS_COLOR;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, midX, y + th / 2);
  ctx.restore();
}

// ---- Rule check sidebar -------------------------------------------------
const $rulesBtn = document.getElementById("rules-btn");
const $ruleSidebar = document.getElementById("rule-sidebar");
const $ruleSidebarSummary = document.getElementById("rule-sidebar-summary");
const $ruleSidebarBody = document.getElementById("rule-sidebar-body");
const $ruleSidebarClose = document.getElementById("rule-sidebar-close");

const RULE_FOLD_KEY = "smdr2.viewer.ruleFolded";
function getRuleFolded() {
  try { return new Set(JSON.parse(sessionStorage.getItem(RULE_FOLD_KEY) ?? "[]")); }
  catch { return new Set(); }
}
function setRuleFolded(s) {
  sessionStorage.setItem(RULE_FOLD_KEY, JSON.stringify([...s]));
}

let currentProductInfo = null;     // /api/products/{id} response cached for sibling links
let currentRuleResults = null;     // /api/products/{id}/rule-check response cached

async function loadRuleSidebar(productId, role) {
  $rulesBtn.hidden = false;
  // Fetch the product (for sibling files_by_role) and the rule check together.
  const [pRes, rRes] = await Promise.all([
    fetch(`/api/products/${productId}`),
    fetch(`/api/products/${productId}/rule-check`),
  ]);
  if (pRes.ok) currentProductInfo = await pRes.json();
  if (!rRes.ok) {
    // No rule check yet — keep the button visible but the sidebar empty.
    currentRuleResults = null;
    renderRuleSidebar(role);
    return;
  }
  currentRuleResults = await rRes.json();
  renderRuleSidebar(role);

  // Apply ?rule=&idx= focus if requested.
  const params = new URLSearchParams(location.search);
  const ruleName = params.get("rule");
  const idxStr = params.get("idx");
  if (ruleName && idxStr !== null) {
    focusSubRuleByKey(ruleName, parseInt(idxStr, 10), role);
    $ruleSidebar.hidden = false;
    $rulesBtn.classList.add("active");
  }
}

function renderRuleSidebar(role) {
  $ruleSidebarBody.innerHTML = "";
  if (!currentRuleResults) {
    $ruleSidebarSummary.textContent = "";
    $ruleSidebarBody.innerHTML =
      `<div class="empty-msg">No rule check yet for this product. ` +
      `Run it from the dashboard.</div>`;
    return;
  }
  const d = currentRuleResults;
  $ruleSidebarSummary.textContent =
    `${d.pass_count}/${d.rule_count} pass`;

  const folded = getRuleFolded();
  for (const [name, rule] of Object.entries(d.results)) {
    const details = document.createElement("details");
    details.dataset.ruleName = name;
    details.open = !folded.has(name);
    details.addEventListener("toggle", () => {
      const f = getRuleFolded();
      if (details.open) f.delete(name); else f.add(name);
      setRuleFolded(f);
    });

    const summary = document.createElement("summary");
    summary.innerHTML =
      `<div class="rule-head-row">` +
        `<span class="rule-status ${rule.pass ? "pass" : "fail"}">${rule.pass ? "✓" : "✗"}</span>` +
        `<span class="rule-name">${escapeHtml(name)}</span>` +
      `</div>` +
      `<div class="rule-text">${escapeHtml(rule.text || "")}</div>`;
    details.appendChild(summary);

    const subList = document.createElement("ol");
    subList.className = "subrules";
    const subs = rule.rules || [];
    if (!subs.length) {
      const li = document.createElement("li");
      li.className = "missing-file";
      li.innerHTML = `<span class="part">—</span><span class="sub-text">(no sub-rules)</span><span></span>`;
      subList.appendChild(li);
    } else {
      subs.forEach((sub, idx) => {
        const li = renderSubRuleItem(name, idx, sub, role, rule.pass);
        subList.appendChild(li);
      });
    }
    details.appendChild(subList);
    $ruleSidebarBody.appendChild(details);
  }
  highlightFocusedInSidebar();
}

function renderSubRuleItem(ruleName, idx, sub, currentRole, rulePass) {
  const li = document.createElement("li");
  li.dataset.ruleName = ruleName;
  li.dataset.idx = String(idx);
  const sibling = currentProductInfo?.files_by_role?.[sub.part];

  let hintHtml = "";
  if (sub.part === currentRole) {
    li.classList.add("same-role");
    hintHtml = `<span class="nav-hint">show</span>`;
  } else if (sibling) {
    li.classList.add("other-role");
    hintHtml = `<span class="nav-hint">→ ${escapeHtml(sub.part)} viewer</span>`;
  } else {
    li.classList.add("missing-file");
    hintHtml = `<span class="nav-hint">(no file)</span>`;
  }

  li.innerHTML =
    `<span class="part">${escapeHtml(sub.part)}</span>` +
    `<span class="sub-text">${escapeHtml(sub.text || "")}</span>` +
    hintHtml;

  li.addEventListener("click", () => {
    if (sub.part === currentRole) {
      focusSubRule(ruleName, idx, rulePass, sub);
      highlightFocusedInSidebar();
    } else if (sibling) {
      location.href = `/viewer/${sibling.id}?rule=${encodeURIComponent(ruleName)}&idx=${idx}`;
    }
  });
  return li;
}

function focusSubRule(ruleName, idx, rulePass, sub) {
  focusedSubRule = {
    ruleName,
    rulePass: !!rulePass,
    ruleText: currentRuleResults?.results?.[ruleName]?.text ?? "",
    idx,
    part: sub.part,
    from: sub.from || [],
    to:   sub.to   || [],
    text: sub.text || "",
  };
  render();
  setBaseStatus(`Rule check focus: ${ruleName} · ${sub.text}`);
}

function focusSubRuleByKey(ruleName, idx, role) {
  const rule = currentRuleResults?.results?.[ruleName];
  if (!rule) return;
  const sub = rule.rules?.[idx];
  if (!sub) return;
  if (sub.part !== role) {
    // Navigate elsewhere — but loadRuleSidebar is invoked on the viewer for
    // *this* file, so if the part disagrees we just don't focus anything.
    return;
  }
  focusSubRule(ruleName, idx, rule.pass, sub);
}

function highlightFocusedInSidebar() {
  for (const li of $ruleSidebarBody.querySelectorAll(".subrules li")) {
    li.classList.toggle("focused",
      focusedSubRule
      && li.dataset.ruleName === focusedSubRule.ruleName
      && parseInt(li.dataset.idx, 10) === focusedSubRule.idx
    );
  }
}

$rulesBtn.addEventListener("click", () => {
  $ruleSidebar.hidden = !$ruleSidebar.hidden;
  $rulesBtn.classList.toggle("active", !$ruleSidebar.hidden);
});
$ruleSidebarClose.addEventListener("click", () => {
  $ruleSidebar.hidden = true;
  $rulesBtn.classList.remove("active");
});

// ---- hit-tests -----------------------------------------------------------
function distPointToSegmentSq(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) {
    const ex = px - x1, ey = py - y1;
    return ex * ex + ey * ey;
  }
  let t = ((px - x1) * dx + (py - y1) * dy) / len2;
  if (t < 0) t = 0; else if (t > 1) t = 1;
  const fx = x1 + t * dx, fy = y1 + t * dy;
  const ex = px - fx, ey = py - fy;
  return ex * ex + ey * ey;
}

function pointInRing(px, py, ring) {
  // Ray-casting; counts crossings of an east-going ray.
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > py) !== (yj > py)) &&
      (px < ((xj - xi) * (py - yi)) / (yj - yi || 1e-30) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function primHitTest(p, wx, wy, tol) {
  const tol2 = tol * tol;
  switch (p.type) {
    case "line":
      return distPointToSegmentSq(wx, wy, p.start[0], p.start[1], p.end[0], p.end[1]) <= tol2;
    case "polyline": {
      const pts = p.points;
      for (let i = 1; i < pts.length; i++) {
        if (distPointToSegmentSq(wx, wy, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]) <= tol2) return true;
      }
      if (p.closed && pts.length > 2) {
        const a = pts[pts.length - 1], b = pts[0];
        if (distPointToSegmentSq(wx, wy, a[0], a[1], b[0], b[1]) <= tol2) return true;
      }
      return false;
    }
    case "filled_polygon": {
      // Even-odd point-in-rings, OR within tol of any boundary edge.
      let inside = false;
      for (const ring of p.rings) if (pointInRing(wx, wy, ring)) inside = !inside;
      if (inside) return true;
      for (const ring of p.rings) {
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          if (distPointToSegmentSq(wx, wy, ring[j][0], ring[j][1], ring[i][0], ring[i][1]) <= tol2) return true;
        }
      }
      return false;
    }
    case "point": {
      const dx = wx - p.pos[0], dy = wy - p.pos[1];
      return dx * dx + dy * dy <= tol2;
    }
  }
  return false;
}

function pickIndexAt(wx, wy) {
  // Pickbox: PICKBOX_CSS_PX in CSS px → device px → world units.
  const tol = (PICKBOX_CSS_PX * dpr) / view.zoom;
  // First-hit linear scan, prefer last-drawn (top) on tie by iterating in reverse.
  for (let i = primitives.length - 1; i >= 0; i--) {
    if (primitives[i].decorative) continue;
    const [bxmin, bymin, bxmax, bymax] = primBBoxes[i];
    if (wx < bxmin - tol || wx > bxmax + tol || wy < bymin - tol || wy > bymax + tol) continue;
    if (primHitTest(primitives[i], wx, wy, tol)) return i;
  }
  return -1;
}

// ---- connectivity graph (for Chain mode) --------------------------------
function buildConnectivity() {
  // Two primitives are connected if one's endpoint lies within CONNECT_TOL
  // of the other's endpoint. Uses a spatial hash for O(N) construction.
  // Only LINE and OPEN POLYLINE participate — closed shapes are complete
  // on their own; points / fills don't chain.
  const cellSize = CONNECT_TOL;
  const cells = new Map();
  const endpoints = [];

  const addEndpoint = (idx, x, y) => {
    const key = `${Math.round(x / cellSize)},${Math.round(y / cellSize)}`;
    let cell = cells.get(key);
    if (!cell) { cell = []; cells.set(key, cell); }
    cell.push({ idx, x, y });
    endpoints.push({ idx, x, y });
  };

  for (let i = 0; i < primitives.length; i++) {
    const p = primitives[i];
    if (p.decorative) continue;
    if (p.type === "line") {
      addEndpoint(i, p.start[0], p.start[1]);
      addEndpoint(i, p.end[0], p.end[1]);
    } else if (p.type === "polyline" && !p.closed && p.points.length >= 2) {
      const a = p.points[0], b = p.points[p.points.length - 1];
      addEndpoint(i, a[0], a[1]);
      addEndpoint(i, b[0], b[1]);
    }
  }

  const adj = new Array(primitives.length);
  for (let i = 0; i < adj.length; i++) adj[i] = new Set();
  const tol2 = CONNECT_TOL * CONNECT_TOL;

  for (const ep of endpoints) {
    const qx = Math.round(ep.x / cellSize);
    const qy = Math.round(ep.y / cellSize);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        const cell = cells.get(`${qx + dx},${qy + dy}`);
        if (!cell) continue;
        for (const other of cell) {
          if (other.idx === ep.idx) continue;
          const ddx = other.x - ep.x, ddy = other.y - ep.y;
          if (ddx * ddx + ddy * ddy <= tol2) {
            adj[ep.idx].add(other.idx);
            adj[other.idx].add(ep.idx);
          }
        }
      }
    }
  }
  return adj;
}

function ensureConnectivity() {
  if (!connectivityGraph) {
    const t0 = performance.now();
    connectivityGraph = buildConnectivity();
    console.log(`[chain] connectivity built in ${(performance.now() - t0).toFixed(0)}ms`);
  }
  return connectivityGraph;
}

function expandChain(startIdx) {
  const adj = ensureConnectivity();
  const visited = new Set([startIdx]);
  const queue = [startIdx];
  while (queue.length) {
    const cur = queue.shift();
    for (const nb of adj[cur]) {
      if (!visited.has(nb)) {
        visited.add(nb);
        queue.push(nb);
      }
    }
  }
  return visited;
}

// ---- geometric tests for selection ---------------------------------------
function pointInRect(x, y, xmin, ymin, xmax, ymax) {
  return x >= xmin && x <= xmax && y >= ymin && y <= ymax;
}

// Liang–Barsky: any part of segment [x1,y1]-[x2,y2] inside the rect?
function segmentIntersectsRect(x1, y1, x2, y2, xmin, ymin, xmax, ymax) {
  let t0 = 0, t1 = 1;
  const dx = x2 - x1, dy = y2 - y1;
  const p = [-dx, dx, -dy, dy];
  const q = [x1 - xmin, xmax - x1, y1 - ymin, ymax - y1];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return false;
    } else {
      const t = q[i] / p[i];
      if (p[i] < 0) {
        if (t > t1) return false;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return false;
        if (t < t1) t1 = t;
      }
    }
  }
  return true;
}

function primCrossesRect(p, xmin, ymin, xmax, ymax) {
  // True iff actual geometry intersects the rect (boundary in/out OR a hatch
  // interior contains the rect). Bbox overlap alone is NOT enough — a small
  // rect inside a large hollow polyline must not count.
  switch (p.type) {
    case "line":
      return segmentIntersectsRect(p.start[0], p.start[1], p.end[0], p.end[1], xmin, ymin, xmax, ymax);
    case "polyline": {
      const pts = p.points;
      for (let i = 1; i < pts.length; i++) {
        if (segmentIntersectsRect(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1], xmin, ymin, xmax, ymax)) return true;
      }
      if (p.closed && pts.length > 2) {
        const a = pts[pts.length-1], b = pts[0];
        if (segmentIntersectsRect(a[0], a[1], b[0], b[1], xmin, ymin, xmax, ymax)) return true;
      }
      return false;
    }
    case "filled_polygon": {
      // (a) any boundary segment crosses, or (b) the rect lies wholly inside
      // the filled region (interior counts as "intersects" for a hatch).
      for (const ring of p.rings) {
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          if (segmentIntersectsRect(ring[j][0], ring[j][1], ring[i][0], ring[i][1], xmin, ymin, xmax, ymax)) return true;
        }
      }
      let inside = false;
      for (const ring of p.rings) if (pointInRing(xmin, ymin, ring)) inside = !inside;
      return inside;
    }
    case "point":
      return pointInRect(p.pos[0], p.pos[1], xmin, ymin, xmax, ymax);
  }
  return false;
}

// ---- selection ops -------------------------------------------------------
function selectByBox(x1, y1, x2, y2, mode, additive) {
  invalidateMatches();
  const xmin = Math.min(x1, x2), ymin = Math.min(y1, y2);
  const xmax = Math.max(x1, x2), ymax = Math.max(y1, y2);
  if (!additive) selection.clear();
  for (let i = 0; i < primitives.length; i++) {
    if (primitives[i].decorative) continue;
    const [bxmin, bymin, bxmax, bymax] = primBBoxes[i];
    let hit = false;
    if (mode === "window") {
      // Window: entity bbox fully inside rect (for our point-based primitives,
      // bbox-inside ⇔ all-vertices-inside).
      hit = bxmin >= xmin && bymin >= ymin && bxmax <= xmax && bymax <= ymax;
    } else {
      // Crossing: bbox-overlap is only a quick reject; actual geometry must
      // intersect the rect, otherwise a hollow polyline surrounding the
      // selection rect would be wrongly selected.
      if (bxmax < xmin || bxmin > xmax || bymax < ymin || bymin > ymax) continue;
      hit = primCrossesRect(primitives[i], xmin, ymin, xmax, ymax);
    }
    if (hit) selection.add(primitives[i].handle);
  }
  updateStatus();
}
function clearSelection() {
  if (!selection.size && !matchSet.size && !nearMissSet.size) return;
  selection.clear();
  clearMatches();
  updateStatus(); render();
}

function clearMatches() {
  if (!matchSet.size && !matchesStaged && !nearMissSet.size) return;
  matchSet.clear();
  nearMissSet.clear();
  matchesStaged = false;
  renderClassToolbar();
}

// Any explicit change to `selection` should invalidate staged matches —
// the matcher needs to re-run on the new template.
function invalidateMatches() {
  if (matchSet.size || matchesStaged) {
    clearMatches();
  }
}

// ---- status bar ----------------------------------------------------------
let baseStatus = "";
function setBaseStatus(s) { baseStatus = s; updateStatus(); }
function updateStatus() {
  const parts = [baseStatus];
  if (selection.size) {
    parts.push(`${selection.size} entit${selection.size === 1 ? "y" : "ies"} selected`);
  }
  if (matchSet.size) {
    parts.push(`${matchSet.size} match${matchSet.size === 1 ? "" : "es"}`);
  }
  if (nearMissSet.size) {
    parts.push(`${nearMissSet.size} near-miss`);
  }
  $status.textContent = parts.join(" · ");

  if (selection.size) {
    const handles = [...selection];
    if (handles.length === 1) {
      const s = ensureHandleStats().get(handles[0]);
      if (s) {
        const w = s.xmax - s.xmin, h = s.ymax - s.ymin;
        const small = Math.min(w, h), big = Math.max(w, h);
        $handle.textContent =
          `${handles[0]} · ${s.vcount}v · ${small.toFixed(4)}×${big.toFixed(4)} · path ${s.plen.toFixed(4)}`;
        debugDumpHandle(handles[0]);
      } else {
        $handle.textContent = `Handle ${handles[0]}`;
      }
    } else {
      $handle.textContent = `Handle ${handles[0]} +${handles.length - 1}`;
    }
    $handle.classList.remove("empty");
  } else {
    $handle.textContent = "Handle —";
    $handle.classList.add("empty");
  }

  if (addModeClass) {
    if (matchesStaged) {
      $modeHint.textContent = `ADD ${addModeClass} · press Enter to commit, Esc to cancel`;
    } else if (selection.size) {
      $modeHint.textContent = `ADD ${addModeClass} · press S to scan`;
    } else {
      $modeHint.textContent = `ADD ${addModeClass} · frame-select a pattern`;
    }
  } else {
    $modeHint.textContent = "";
  }
}

// ---- interaction: mouse --------------------------------------------------
$canvas.addEventListener("contextmenu", (e) => e.preventDefault());

$canvas.addEventListener("mousedown", (e) => {
  if (e.button === 1) {
    // Middle: pan
    e.preventDefault();
    drag = {
      kind: "pan",
      startClient: { x: e.clientX, y: e.clientY },
      startView: { cx: view.cx, cy: view.cy },
    };
    $canvas.classList.add("panning");
  } else if (e.button === 0) {
    // Left: click_pending — becomes box-drag if mouse moves past threshold.
    const [wx, wy] = eventToWorld(e);
    drag = {
      kind: "click_pending",
      startClient: { x: e.clientX, y: e.clientY },
      startWorld: [wx, wy],
      shift: e.shiftKey,
    };
  }
});

window.addEventListener("mousemove", (e) => {
  const [wx, wy] = eventToWorld(e);
  $coords.textContent = `${wx.toFixed(3)}, ${wy.toFixed(3)}`;

  if (!drag) return;
  if (drag.kind === "pan") {
    const dxScreen = (e.clientX - drag.startClient.x) * dpr;
    const dyScreen = (e.clientY - drag.startClient.y) * dpr;
    view.cx = drag.startView.cx - dxScreen / view.zoom;
    view.cy = drag.startView.cy + dyScreen / view.zoom;
    render();
    return;
  }
  if (drag.kind === "click_pending") {
    const dx = e.clientX - drag.startClient.x;
    const dy = e.clientY - drag.startClient.y;
    if (Math.abs(dx) > CLICK_DRAG_THRESHOLD_CSS || Math.abs(dy) > CLICK_DRAG_THRESHOLD_CSS) {
      // Promote to box drag.
      drag = {
        kind: "box",
        startClient: drag.startClient,
        startWorld: drag.startWorld,
        currentWorld: [wx, wy],
        currentClient: { x: e.clientX, y: e.clientY },
        mode: e.clientX >= drag.startClient.x ? "window" : "crossing",
        shift: drag.shift,
      };
      render();
    }
    return;
  }
  if (drag.kind === "box") {
    drag.currentWorld = [wx, wy];
    drag.currentClient = { x: e.clientX, y: e.clientY };
    // Update mode live based on current direction relative to start.
    drag.mode = e.clientX >= drag.startClient.x ? "window" : "crossing";
    render();
  }
});

window.addEventListener("mouseup", (e) => {
  if (!drag) return;
  if (drag.kind === "pan") {
    $canvas.classList.remove("panning");
  } else if (drag.kind === "click_pending") {
    // Pure click: single-pick (optionally expanded to its connected chain).
    invalidateMatches();
    const [wx, wy] = drag.startWorld;
    const hitIdx = pickIndexAt(wx, wy);
    if (hitIdx !== -1) {
      const indices = chainMode ? expandChain(hitIdx) : new Set([hitIdx]);
      const handles = new Set();
      for (const idx of indices) handles.add(primitives[idx].handle);

      if (drag.shift) {
        // Toggle the whole chain: if every handle is already selected, remove;
        // otherwise add.
        let allSelected = true;
        for (const h of handles) { if (!selection.has(h)) { allSelected = false; break; } }
        if (allSelected) for (const h of handles) selection.delete(h);
        else            for (const h of handles) selection.add(h);
      } else {
        selection.clear();
        for (const h of handles) selection.add(h);
      }
    } else {
      if (!drag.shift) selection.clear();
    }
    updateStatus();
  } else if (drag.kind === "box") {
    const [x1, y1] = drag.startWorld;
    const [x2, y2] = drag.currentWorld;
    selectByBox(x1, y1, x2, y2, drag.mode, drag.shift);
  }
  drag = null;
  render();
});

// ---- interaction: zoom ---------------------------------------------------
$canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const [sx, sy] = eventToScreen(e);
  const [wxBefore, wyBefore] = screenToWorld(sx, sy);
  view.zoom *= Math.exp(-e.deltaY * 0.0015);
  const [wxAfter, wyAfter] = screenToWorld(sx, sy);
  view.cx += wxBefore - wxAfter;
  view.cy += wyBefore - wyAfter;
  render();
}, { passive: false });

// ---- class toolbar & add-mode state machine ------------------------------
async function fetchClasses() {
  const res = await fetch(API.classes());
  const data = await res.json();
  classes = data.classes;
  renderClassToolbar();
}

function renderClassToolbar() {
  $classToolbar.innerHTML = "";
  classes.forEach((cls, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "class-btn";
    btn.dataset.className = cls.name;
    btn.style.setProperty("--class-color", classColor(cls.name));
    if (addModeClass === cls.name) {
      btn.classList.add(matchesStaged ? "staged" : "active");
    }
    const hotkey = HOTKEYS[i] ?? "";
    btn.innerHTML =
      `<span class="icon">${addModeClass === cls.name && matchesStaged ? "✓" : "+"}</span>` +
      `<span class="name">${cls.name}</span>` +
      `<span class="count">(${cls.count})</span>` +
      (hotkey ? `<span class="hotkey">[${hotkey}]</span>` : "");
    btn.addEventListener("click", () => enterAddMode(cls.name));
    $classToolbar.appendChild(btn);
  });
}

function enterAddMode(className) {
  if (addModeClass === className) {
    // toggle off
    exitAddMode();
    return;
  }
  // Switching class — clear any staged matches but keep selection so user
  // can reuse the same template for a different class if they want.
  clearMatches();
  addModeClass = className;
  renderClassToolbar();
  updateStatus();
}

function exitAddMode() {
  addModeClass = null;
  clearMatches();
  renderClassToolbar();
  updateStatus();
  render();
}

async function scanCurrentSelection() {
  if (!selection.size) return;
  setBaseStatus(`scanning…`);
  const t0 = performance.now();
  try {
    const res = await fetch(API.match(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handles: [...selection] }),
    });
    if (!res.ok) {
      const err = await res.text();
      console.error("match failed:", err);
      setBaseStatus(`match error: ${res.status}`);
      return;
    }
    const data = await res.json();
    matchSet.clear();
    nearMissSet.clear();
    for (const m of data.matches) for (const h of m.handles) matchSet.add(h);
    for (const n of (data.near_misses ?? [])) for (const h of n.handles) nearMissSet.add(h);
    matchesStaged = true;
    const dt = (performance.now() - t0).toFixed(0);
    setBaseStatus(`scan: ${data.count} matches, ${data.near_count ?? 0} near-miss in ${dt}ms`);
    renderClassToolbar();
    updateStatus();
    render();
  } catch (e) {
    console.error(e);
    setBaseStatus(`match error: ${e.message}`);
  }
}

async function commitCurrentTemplate() {
  if (!addModeClass || !selection.size) return;
  try {
    const res = await fetch(API.commit(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ class_name: addModeClass, handles: [...selection] }),
    });
    if (!res.ok) {
      const err = await res.text();
      console.error("commit failed:", err);
      setBaseStatus(`commit error: ${res.status}`);
      return;
    }
    const data = await res.json();
    // Refresh class counts.
    const cls = classes.find(c => c.name === data.class_name);
    if (cls) cls.count = data.count;
    setBaseStatus(`saved ${data.class_name} template (#${data.count})`);
    selection.clear();
    matchSet.clear();
    nearMissSet.clear();
    matchesStaged = false;
    addModeClass = null;
    renderClassToolbar();
    updateStatus();
    render();
  } catch (e) {
    console.error(e);
    setBaseStatus(`commit error: ${e.message}`);
  }
}

// ---- scan-all ------------------------------------------------------------
async function runScanAll() {
  $scanAllBtn.classList.add("active");
  setBaseStatus("scan-all: running…");
  const t0 = performance.now();
  try {
    const res = await fetch(API.scanAll());
    if (!res.ok) {
      console.error("scan-all failed:", await res.text());
      setBaseStatus(`scan-all error: ${res.status}`);
      $scanAllBtn.classList.remove("active");
      return;
    }
    const data = await res.json();
    const byHandle = new Map();
    const byClass = {};
    for (const [cls, handles] of Object.entries(data.by_class)) {
      byClass[cls] = handles.length;
      for (const h of handles) byHandle.set(h, cls);
    }
    scanAllByHandle = byHandle;
    scanAllSummary = { byClass, total: data.total };
    const dt = (performance.now() - t0).toFixed(0);
    const breakdown = Object.entries(byClass)
      .map(([c, n]) => `${c}:${n}`).join(" ");
    setBaseStatus(`scan-all: ${data.total} hits in ${dt}ms · ${breakdown || "(empty library)"}`);
    updateStatus();
    render();
  } catch (e) {
    console.error(e);
    setBaseStatus(`scan-all error: ${e.message}`);
    $scanAllBtn.classList.remove("active");
  }
}

function clearScanAll() {
  if (!scanAllByHandle) return;
  scanAllByHandle = null;
  scanAllSummary = null;
  $scanAllBtn.classList.remove("active");
  render();
}

function toggleScanAll() {
  if (scanAllByHandle) clearScanAll();
  else runScanAll();
}

$scanAllBtn.addEventListener("click", toggleScanAll);

// ---- Save Match JSON -----------------------------------------------------
async function saveMatchJson() {
  $saveMatchBtn.disabled = true;
  const t0 = performance.now();
  try {
    const res = await fetch(API.matchJson(), { method: "POST" });
    if (!res.ok) {
      const err = await res.text();
      console.error("save-match failed:", err);
      setBaseStatus(`save-match error: ${res.status}`);
      return;
    }
    const data = await res.json();
    const dt = (performance.now() - t0).toFixed(0);
    setBaseStatus(
      `match saved: ${data.template_keys.length} template variant(s), ` +
      `${data.total_matches} total matches → ${data.saved_to} (${dt}ms)`
    );
  } catch (e) {
    console.error(e);
    setBaseStatus(`save-match error: ${e.message}`);
  } finally {
    $saveMatchBtn.disabled = false;
  }
}
$saveMatchBtn.addEventListener("click", saveMatchJson);

// ---- Library modal ------------------------------------------------------
$libraryBtn.addEventListener("click", openLibrary);
$libraryModal.addEventListener("click", (e) => {
  if (e.target.matches("[data-close]")) closeLibrary();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$libraryModal.hidden) {
    closeLibrary();
    e.stopPropagation();
  }
}, true);

async function openLibrary() {
  $libraryModal.hidden = false;
  await renderLibrary();
}

function closeLibrary() {
  $libraryModal.hidden = true;
}

async function renderLibrary() {
  $libraryBody.innerHTML = `<div class="library-empty">loading…</div>`;
  const res = await fetch(API.templates());
  if (!res.ok) {
    $libraryBody.innerHTML = `<div class="library-empty">failed to load</div>`;
    return;
  }
  const data = await res.json();
  const templates = data.templates;
  $librarySummary.textContent = `${templates.length} template${templates.length === 1 ? "" : "s"}`;

  if (templates.length === 0) {
    $libraryBody.innerHTML =
      `<div class="library-empty">No templates yet. Pick a class and frame-select a pattern, then Enter to commit.</div>`;
    return;
  }

  // Group by class while preserving the order classes are defined in
  // (matches the dashboard / toolbar ordering).
  const byClass = new Map();
  for (const t of templates) {
    if (!byClass.has(t.class_name)) byClass.set(t.class_name, []);
    byClass.get(t.class_name).push(t);
  }

  $libraryBody.innerHTML = "";
  const folded = getFoldedClasses();
  for (const [cls, items] of byClass) {
    const group = document.createElement("details");
    group.className = "library-class-group";
    group.style.setProperty("--class-color", classColor(cls));
    group.open = !folded.has(cls);
    group.addEventListener("toggle", () => {
      const f = getFoldedClasses();
      if (group.open) f.delete(cls); else f.add(cls);
      setFoldedClasses(f);
    });

    const summary = document.createElement("summary");
    const h = document.createElement("h3");
    h.textContent = `${cls} · ${items.length}`;
    summary.appendChild(h);
    group.appendChild(summary);

    for (const t of items) {
      group.appendChild(buildTemplateCard(t));
    }
    $libraryBody.appendChild(group);
  }
}

// Fold-state persistence so the user's "I folded bga_ball away" sticks
// across opening/closing the modal (and across page reloads in the same tab).
const FOLD_KEY = "smdr2.library.folded";
function getFoldedClasses() {
  try { return new Set(JSON.parse(sessionStorage.getItem(FOLD_KEY) ?? "[]")); }
  catch { return new Set(); }
}
function setFoldedClasses(s) {
  sessionStorage.setItem(FOLD_KEY, JSON.stringify([...s]));
}

function buildTemplateCard(t) {
  const card = document.createElement("div");
  card.className = "template-card";
  card.style.setProperty("--class-color", classColor(t.class_name));

  // Thumbnail
  const thumb = document.createElement("canvas");
  thumb.className = "thumb";
  thumb.width = 56 * (window.devicePixelRatio || 1);
  thumb.height = 56 * (window.devicePixelRatio || 1);
  thumb.style.width = "56px";
  thumb.style.height = "56px";
  drawTemplateThumbnail(thumb, t);
  card.appendChild(thumb);

  // Meta
  const meta = document.createElement("div");
  meta.className = "meta";
  const [xmin, ymin, xmax, ymax] = t.bbox;
  const w = xmax - xmin, h = ymax - ymin;
  const small = Math.min(w, h), big = Math.max(w, h);
  meta.innerHTML =
    `<span class="key">${t.key}</span>` +
    `<span class="stats">${t.entity_count} entit${t.entity_count === 1 ? "y" : "ies"} · ` +
    `${t.vertex_count}v · ${small.toFixed(3)}×${big.toFixed(3)} mm</span>`;
  card.appendChild(meta);

  // Actions: move dropdown + delete
  const actions = document.createElement("div");
  actions.className = "actions";
  const moveSelect = document.createElement("select");
  moveSelect.title = "Move to another class";
  for (const c of classes) {
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = c.name;
    if (c.name === t.class_name) opt.selected = true;
    moveSelect.appendChild(opt);
  }
  moveSelect.addEventListener("change", async () => {
    moveSelect.disabled = true;
    try {
      const res = await fetch(API.templateOne(t.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ class_name: moveSelect.value }),
      });
      if (!res.ok) {
        console.error(await res.text());
      } else {
        await refreshClassCounts();
        await renderLibrary();
      }
    } finally {
      moveSelect.disabled = false;
    }
  });
  actions.appendChild(moveSelect);

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "delete-btn";
  delBtn.textContent = "Delete";
  delBtn.addEventListener("click", async () => {
    if (!confirm(`Delete template ${t.key}?`)) return;
    delBtn.disabled = true;
    try {
      const res = await fetch(API.templateOne(t.id), { method: "DELETE" });
      if (!res.ok) {
        console.error(await res.text());
      } else {
        await refreshClassCounts();
        await renderLibrary();
      }
    } finally {
      delBtn.disabled = false;
    }
  });
  actions.appendChild(delBtn);
  card.appendChild(actions);

  return card;
}

function drawTemplateThumbnail(canvas, t) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.fillStyle = "#0f1318";
  ctx.fillRect(0, 0, W, H);

  const [xmin, ymin, xmax, ymax] = t.bbox;
  const dw = Math.max(xmax - xmin, 1e-6);
  const dh = Math.max(ymax - ymin, 1e-6);
  const pad = 6 * (window.devicePixelRatio || 1);
  const s = Math.min((W - pad * 2) / dw, (H - pad * 2) / dh);

  ctx.save();
  ctx.translate(W / 2, H / 2);
  ctx.scale(s, -s);
  ctx.translate(-(xmin + xmax) / 2, -(ymin + ymax) / 2);
  ctx.strokeStyle = classColor(t.class_name);
  ctx.lineWidth = 1.4 / s * (window.devicePixelRatio || 1);
  for (const pts of t.entity_point_sets) {
    if (pts.length < 2) continue;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.stroke();
  }
  ctx.restore();
}

async function refreshClassCounts() {
  // Re-fetch /api/classes and rebuild the toolbar so counts stay in sync.
  await fetchClasses();
}

// Rule checking moved to the dashboard (product-scoped). The hover/pin
// highlight infrastructure is kept available via `hoverSet` / `pinnedSet`
// in case other features want it; nothing populates them in this build.

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---- Auto-load pre-match overlay ----------------------------------------
async function loadPrematch() {
  const res = await fetch(API.prematch());
  if (!res.ok) return;
  const data = await res.json();
  if (!data.total) return;
  const byHandle = new Map();
  const byClass = {};
  for (const [cls, handles] of Object.entries(data.by_class)) {
    byClass[cls] = handles.length;
    for (const h of handles) byHandle.set(h, cls);
  }
  scanAllByHandle = byHandle;
  scanAllSummary = { byClass, total: data.total };
  $scanAllBtn.classList.add("active");
  render();
}

// ---- interaction: keyboard -----------------------------------------------
window.addEventListener("keydown", (e) => {
  // Ignore when typing in an input (none right now, but defensive).
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

  // Esc cascade: cancel active box drag → clear scan-all overlay →
  // exit add-mode → clear selection.
  if (e.key === "Escape") {
    if (drag && drag.kind === "box") { drag = null; render(); return; }
    if (scanAllByHandle) { clearScanAll(); return; }
    if (addModeClass) { exitAddMode(); return; }
    clearSelection();
    return;
  }

  // A = toggle scan-all (runs every library template).
  if ((e.key === "a" || e.key === "A") && !e.metaKey && !e.ctrlKey && !e.altKey) {
    toggleScanAll();
    e.preventDefault();
    return;
  }

  // Hotkeys → enter add mode for that class. Don't fire while typing.
  if (!e.metaKey && !e.ctrlKey && !e.altKey) {
    const key = e.key.toLowerCase();
    const idx = HOTKEYS.indexOf(key);
    if (idx !== -1 && idx < classes.length) {
      enterAddMode(classes[idx].name);
      e.preventDefault();
      return;
    }
  }

  // S = scan whenever there is a selection. In ADD mode this stages the
  // match for commit (✓ → Enter); outside ADD mode it's just a preview.
  if ((e.key === "s" || e.key === "S") && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (selection.size) {
      scanCurrentSelection();
      e.preventDefault();
    }
    return;
  }

  // Enter = commit staged template.
  if (e.key === "Enter") {
    if (addModeClass && matchesStaged) {
      commitCurrentTemplate();
      e.preventDefault();
    }
  }
});

// ---- chain mode toggle ---------------------------------------------------
$chainBtn.addEventListener("click", () => {
  chainMode = !chainMode;
  $chainBtn.classList.toggle("active", chainMode);
  if (chainMode) ensureConnectivity();  // build eagerly so first click feels instant
});

// ---- bootstrap -----------------------------------------------------------
async function load() {
  resize();
  setBaseStatus("fetching…");
  const t0 = performance.now();
  const [primRes, _c, _f] = await Promise.all([
    fetch(API.primitives()),
    fetchClasses(),
    loadFileInfo(),
  ]);
  const data = await primRes.json();
  const tFetch = (performance.now() - t0).toFixed(0);

  primitives = data.primitives;
  background = data.background || "#1a1f26";
  fitToBbox(data.bbox);

  const tBox0 = performance.now();
  computeBBoxes();
  const tBox = (performance.now() - tBox0).toFixed(0);

  const t1 = performance.now();
  render();
  const tRender = (performance.now() - t1).toFixed(0);

  setBaseStatus(
    `${data.count.toLocaleString()} primitives · fetch ${tFetch}ms · bbox ${tBox}ms · render ${tRender}ms`
  );

  // Pre-match overlay was computed at preprocessing time; show it
  // automatically so user sees library coverage on arrival.
  await loadPrematch();
  // Populate the rule-check sidebar (and apply ?rule=&idx= focus if any).
  if (currentFileInfo?.product_id && currentFileInfo?.dxf_role) {
    await loadRuleSidebar(currentFileInfo.product_id, currentFileInfo.dxf_role);
  } else {
    $rulesBtn.hidden = true;
  }
}

load();
