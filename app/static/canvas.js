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

import {
  closestPointOnSegment,
  detectCircle,
  resolveSnap as _resolveSnapCore,
  applyOrtho as _applyOrthoCore,
} from "./measure_core.js";
import { mountDevParamsModal } from "./dev_params.js";
import { initUnitPicker } from "./unit_picker.js";

const $canvas = document.getElementById("dxf-canvas");
const $status = document.getElementById("status");
const $coords = document.getElementById("cursor-coords");
const $handle = document.getElementById("handle-info");
const $chainBtn = document.getElementById("chain-btn");
const $scanAllBtn = document.getElementById("scan-all-btn");
const $saveMatchBtn = document.getElementById("save-match-btn");
// Holds the active save_match job_id while we're polling for completion;
// null when no save is in flight. Acts as the re-entrancy guard for
// rapid Save Match clicks — see `saveMatchJson` / `pollSaveMatchJob`.
let saveMatchInFlight = null;
const $libraryBtn = document.getElementById("library-btn");
const $libraryModal = document.getElementById("library-modal");
const $libraryBody = document.getElementById("library-body");
const $librarySummary = document.getElementById("library-summary");
const $visibilityBtn = document.getElementById("visibility-btn");
const $visibilityPanel = document.getElementById("visibility-panel");
const $visibilityList = document.getElementById("visibility-list");
const $visibilityClose = document.getElementById("visibility-close");
const $visibilityAll = document.getElementById("visibility-all");
const $visibilityInvert = document.getElementById("visibility-invert");

// Live layer-visibility state. Independent of the DB-backed `selected_layers`
// (which gates Phase 2 / matching). This Set just hides layers from the
// canvas + pick/snap/select; nothing is sent to the server.
const hiddenLayers = new Set();
const VIS_STORAGE_KEY = `smdr2.hiddenLayers.${document.body.dataset.fileId}`;

function layerOf(p) { return p.layer || "0"; }
// Single per-primitive render gate, called at the top of every render pass
// (main, scan-all, near-miss, selection). Hides primitives in two cases:
//   1. Their layer is toggled off in the Layers panel.
//   2. They were tagged `decorative` by the back-end DXF flattener
//      (TEXT / MTEXT / DIMENSION / HATCH). These carry no information the
//      matching workflow uses, and ezdxf's font fallback otherwise renders
//      MTEXT as per-character placeholder rectangles when the DXF's
//      referenced font isn't installed locally — the "一格格的長方形文字"
//      artefact. Matching / selection / library scan already skip decoratives
//      (see `library.py` and `dxf.py:collect_entity_points`); rendering now
//      agrees with them.
function isLayerVisible(p) {
  if (p.decorative) return false;
  return !hiddenLayers.has(layerOf(p));
}

function loadHiddenLayersFromSession() {
  try {
    const raw = sessionStorage.getItem(VIS_STORAGE_KEY);
    if (!raw) return;
    for (const n of JSON.parse(raw)) hiddenLayers.add(String(n));
  } catch { /* ignore */ }
}

function persistHiddenLayers() {
  try {
    sessionStorage.setItem(VIS_STORAGE_KEY, JSON.stringify([...hiddenLayers]));
  } catch { /* ignore */ }
}
// #product-context ("{product} / {version}") is rendered server-side in
// viewer.html — read-only; version switching happens on the product page.
const $roleSwitcher = document.getElementById("role-switcher");

// Rule-check focus state — populated when the viewer is opened with
// ?rule=<name>&idx=<i> from the dashboard's rule-result modal.
let focusedSubRule = null;
// {ruleName, rulePass, ruleText, part, from, to, text, idx}
const $modeHint = document.getElementById("mode-hint");
const $classToolbar = document.getElementById("class-toolbar");
// Single floating class-selector panel overlaid on the canvas (replaces the
// long toolbar row): one "Objects" panel holding every category
// (Structure / Balls / SMD / Marks / Other).
const $classPanelLeft = document.getElementById("class-panel-left");
const $classPanelLeftBody = document.getElementById("class-panel-left-body");
// Collapse / expand a floating panel to just its header (clicking ▾).
document.querySelectorAll(".floating-collapse[data-panel]").forEach((b) => {
  b.addEventListener("click", () => {
    const panel = document.getElementById(b.dataset.panel);
    if (panel) panel.classList.toggle("collapsed");
  });
});
const ctx = $canvas.getContext("2d");

const FILE_ID = document.body.dataset.fileId;
// Version context — injected by the server into the page (the viewer URL
// itself is /viewer/{file_id}?version_id=...). Every file-centric API
// call MUST carry ?version_id= or the server 422s.
const VERSION_ID = document.body.dataset.versionId
  || new URLSearchParams(location.search).get("version_id")
  || "";
const VERSION_LABEL = document.body.dataset.versionLabel || "";
const PRODUCT_NAME = document.body.dataset.productName || "";
// True when the version is signed off (frozen / read-only). Server-side
// 409 guards are authoritative; these client-side gates are UX.
const SIGNED_OFF = document.body.dataset.signedOff === "1";

// Caller's effective role on this product (viewer|editor|admin), read from
// the product object in GET /api/products at boot. Drives client-side
// affordance gating; the server still enforces every write
// (add-role-based-ui-gating).
let EFFECTIVE_ROLE = null;
function viewerReadOnly() { return EFFECTIVE_ROLE === "viewer"; }

// Append the version scope to a URL, regardless of whether it already
// has a query string.
function withVersion(url) {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}version_id=${encodeURIComponent(VERSION_ID)}`;
}

const API = {
  primitives:    () => withVersion(`/api/files/${FILE_ID}/primitives`),
  warmShapes:    () => withVersion(`/api/files/${FILE_ID}/warm-shapes`),
  fileInfo:      () => withVersion(`/api/files/${FILE_ID}`),
  match:         () => withVersion(`/api/files/${FILE_ID}/match`),
  commit:        () => withVersion(`/api/files/${FILE_ID}/commit`),
  scanAll:       () => withVersion(`/api/files/${FILE_ID}/scan-all`),
  prematch:      () => withVersion(`/api/files/${FILE_ID}/prematch`),
  matchJson:     () => withVersion(`/api/files/${FILE_ID}/match-json`),
  sideRegions:   () => withVersion(`/api/files/${FILE_ID}/side-regions`),
  // Classes/templates are version-scoped — the version owns its library 1:1.
  classes:       () => `/api/classes?version_id=${encodeURIComponent(VERSION_ID)}`,
  templates:     () => `/api/templates?version_id=${encodeURIComponent(VERSION_ID)}`,
  templateOne:   (id) => `/api/templates/${id}`,
};

function fmtSignedAt(ts) {
  if (ts == null) return "—";
  try { return new Date(ts * 1000).toLocaleString(); }
  catch { return String(ts); }
}

// Mutating calls on a signed-off version come back HTTP 409 with
// detail {error: "version signed-off", signed_off_by, signed_off_at}.
// Returns true when the response was that 409 (and was surfaced).
async function handleSignedOff409(res) {
  if (res.status !== 409) return false;
  let detail = null;
  try { detail = (await res.clone().json())?.detail; } catch { /* not JSON */ }
  if (detail && detail.error === "version signed-off") {
    alert(`此版本已由 ${detail.signed_off_by} 於 ${fmtSignedAt(detail.signed_off_at)} 畫押,無法修改。`);
    return true;
  }
  return false;
}

const SIGNED_MSG = "版本已畫押(唯讀)— 無法修改";

// Returned from loadFileInfo so the bootstrap can run the focused-rule
// fetch only when the binding carries a role.
let currentFileInfo = null;

// The version payload for VERSION_ID (files_by_role / files_by_role_all /
// match flags). There is no GET /api/versions/{vid} endpoint, so we locate
// it inside GET /api/products (the viewer needs the role-sibling lists
// anyway). Cached here; refreshed by refreshRoleSwitcher().
let currentVersionInfo = null;

async function fetchVersionContext() {
  try {
    const res = await fetch("/api/products");
    if (!res.ok) return null;
    const data = await res.json();
    for (const p of data.products ?? []) {
      const v = (p.versions ?? []).find(x => x.id === VERSION_ID);
      if (v) {
        EFFECTIVE_ROLE = p.effective_role ?? EFFECTIVE_ROLE;
        return v;
      }
    }
  } catch (e) {
    console.warn("fetchVersionContext failed:", e);
  }
  return null;
}

// Viewer-role gating: hide the toolbar write entry points and signpost the
// read-only state. Reads (Scan All / Measure / Layers / Rules / class
// inspection / pan-zoom) stay; the server still 403s any write that slips
// through (e.g. a frame-select commit), so this is UX alignment only.
function applyRoleGating() {
  if (!viewerReadOnly()) return;
  if ($saveMatchBtn) $saveMatchBtn.hidden = true;
  if ($libraryBtn) $libraryBtn.hidden = true;
  if (!document.getElementById("viewer-readonly-chip")) {
    const chip = document.createElement("span");
    chip.id = "viewer-readonly-chip";
    chip.className = "signed-badge";
    chip.textContent = "唯讀";
    chip.title = "你對此產品為唯讀(viewer)— 編輯功能已隱藏";
    const ctx = document.getElementById("product-context");
    if (ctx && ctx.parentNode) ctx.parentNode.insertBefore(chip, ctx.nextSibling);
    else document.querySelector("header")?.appendChild(chip);
  }
}

async function loadFileInfo() {
  const fileRes = await fetch(API.fileInfo());
  if (!fileRes.ok) return;
  const file = await fileRes.json();
  currentFileInfo = file;
  // Restore persisted view rectangles so the overlay is visible on load.
  sideRects.top_view = file.top_view_rect ?? null;
  sideRects.bottom_view = file.bottom_view_rect ?? null;
  sideRects.side_view = file.side_view_rect ?? null;

  // Version context (role-sibling switcher). The product / version label
  // itself is rendered server-side in #product-context — read-only;
  // switching versions happens on the product page.
  currentVersionInfo = await fetchVersionContext();
  if (currentVersionInfo) {
    renderRoleSwitcher(currentVersionInfo, file);
  } else {
    closeRoleMenu();
    $roleSwitcher.innerHTML = "";
  }
  applyRoleGating();  // hide write tools for viewer-role callers
  // Stash the product name on the file object so the unit-picker's
  // confirm modal can show "Clear saved Match JSON for <name>"
  // without re-fetching products.
  file.product_name = PRODUCT_NAME || null;

  // Bootstrap the unit-override picker now that the file payload (and
  // version context) is loaded. Recompute completions reload the page
  // so the canvas re-reads the new geometry from /primitives. On a
  // signed-off version the picker is display-only.
  initUnitPicker(file, {
    versionId: VERSION_ID,
    readOnly: SIGNED_OFF,
    onComplete: () => window.location.reload(),
  });
}

// ---- Role switcher with per-role sibling-DXF dropdown -------------------
// Surfaces every DXF a product has under each role (`files_by_role_all`)
// so the engineer can flip between siblings (e.g., a BD's top + bottom
// DXFs) without dropping back to the dashboard. Roles with 0 or 1 file
// render as before; ≥ 2 → dropdown.
let openRoleMenu = null;  // currently-open <ul.role-menu>, or null

function closeRoleMenu() {
  if (!openRoleMenu) return;
  openRoleMenu.hidden = true;
  const trigger = openRoleMenu.previousElementSibling;
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  openRoleMenu = null;
}

function renderRoleSwitcher(version, file) {
  closeRoleMenu();
  $roleSwitcher.innerHTML = "";
  for (const role of ["SBT", "BD", "POD"]) {
    $roleSwitcher.appendChild(renderRoleSlot(version, file, role));
  }
  // 4th position: split RING | LID | NovelLID group — all three render
  // independently and may all be populated.
  $roleSwitcher.appendChild(renderRingLidPair(version, file));
}

// A role is "matched" once every sibling DXF under it has had its match
// saved — mirrors the rule-check-readiness predicate used on the dashboard
// (`ready_for_rc = all(f.match_saved for f in uploaded)`). Empty roles
// don't qualify so the dashed-empty styling stays untouched.
function isRoleMatched(siblings) {
  return siblings.length > 0 && siblings.every(s => s.match_saved);
}

// A sibling DXF is only reachable from the switcher once it has finished the
// discovering_layers → awaiting_layers → preprocessing pipeline and reached
// `ready_to_match`. Opening a not-yet-ready (or errored) file would 425 on the
// /primitives fetch and leave a blank canvas, so such siblings render disabled
// rather than as navigable links.
function isSibViewable(sib) {
  return sib?.status === "ready_to_match";
}
function notReadyTitle(sib, role) {
  if (sib.status === "error") return `${role} 處理失敗`;
  if (sib.status === "awaiting_layers") return `${role} 尚未挑選圖層`;
  return `${role} 尚未完成 preprocess`;  // discovering_layers / preprocessing
}

// Re-pull the version payload so the role switcher reflects fresh
// `match_saved` flags. Callers: after Save Match JSON succeeds, and after
// a region edit clears match_saved server-side.
async function refreshRoleSwitcher() {
  if (!currentFileInfo) return;
  const v = await fetchVersionContext();
  if (!v) return;
  currentVersionInfo = v;
  renderRoleSwitcher(v, currentFileInfo);
}

function renderRoleSlot(version, file, role, opts = {}) {
  const { disabledReason = null } = opts;
  const siblings = version.files_by_role_all?.[role] ?? [];
  const isCurrentRole = role === file.dxf_role;

  if (siblings.length === 0) {
    const btn = document.createElement("a");
    btn.className = "role-btn empty";
    btn.dataset.role = role;
    btn.textContent = role;
    if (disabledReason) {
      btn.classList.add("disabled");
      btn.title = disabledReason;
    } else {
      btn.title = `${role} not uploaded yet`;
    }
    return btn;
  }
  if (siblings.length === 1) {
    const sibling = siblings[0];
    const btn = document.createElement("a");
    btn.className = "role-btn";
    btn.dataset.role = role;
    btn.textContent = role;
    if (sibling.id === file.id) {
      btn.classList.add("current");
    } else if (isSibViewable(sibling)) {
      btn.href = withVersion(`/viewer/${sibling.id}`);
    } else {
      btn.classList.add("disabled");
      btn.title = notReadyTitle(sibling, role);
    }
    if (isRoleMatched(siblings)) btn.classList.add("matched");
    return btn;
  }
  return buildRoleDropdown(role, siblings, isCurrentRole, file.id);
}

// 4th position: render RING and LID side-by-side. Each half is an
// independent role slot — both may be populated concurrently.
function renderRingLidPair(version, file) {
  const wrap = document.createElement("span");
  wrap.className = "role-btn-pair";
  wrap.appendChild(renderRoleSlot(version, file, "RING"));
  wrap.appendChild(renderRoleSlot(version, file, "LID"));
  wrap.appendChild(renderRoleSlot(version, file, "NovelLID"));
  return wrap;
}

function buildRoleDropdown(role, siblings, isCurrentRole, currentFileId) {
  const wrap = document.createElement("span");
  wrap.className = "role-btn-wrap";

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "role-btn role-btn--multi";
  if (isCurrentRole) trigger.classList.add("current");
  if (isRoleMatched(siblings)) trigger.classList.add("matched");
  trigger.dataset.role = role;
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.textContent = `${role} ×${siblings.length} ▾`;

  const menu = document.createElement("ul");
  menu.className = "role-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;

  for (const sib of siblings) {
    const li = document.createElement("li");
    li.setAttribute("role", "none");
    const label = sib.name ?? sib.dxf_view ?? sib.id;
    let item;
    if (sib.id === currentFileId) {
      item = document.createElement("span");
      item.className = "role-menu__item role-menu__item--current";
    } else if (isSibViewable(sib)) {
      item = document.createElement("a");
      item.className = "role-menu__item";
      item.href = withVersion(`/viewer/${sib.id}`);
    } else {
      item = document.createElement("span");
      item.className = "role-menu__item role-menu__item--disabled";
      item.title = notReadyTitle(sib, role);
    }
    if (sib.match_saved) item.classList.add("role-menu__item--matched");
    item.setAttribute("role", "menuitem");
    item.textContent = label;
    li.appendChild(item);
    menu.appendChild(li);
  }

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const wasOpen = openRoleMenu === menu;
    closeRoleMenu();
    if (!wasOpen) {
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      openRoleMenu = menu;
    }
  });

  wrap.appendChild(trigger);
  wrap.appendChild(menu);
  return wrap;
}

// Outside-click closes the open role-menu (skipped when the click is on
// the trigger itself — the trigger's own handler will toggle correctly).
document.addEventListener("mousedown", (e) => {
  if (!openRoleMenu) return;
  const wrap = openRoleMenu.parentElement;
  if (wrap && wrap.contains(e.target)) return;
  closeRoleMenu();
});

// Esc closes the open role-menu. Early-return when no menu is open so we
// never intercept the viewer's other Esc handlers (mark-mode cancel,
// measure-tool cancel, library modal, etc.).
window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !openRoleMenu) return;
  const trigger = openRoleMenu.previousElementSibling;
  closeRoleMenu();
  if (trigger) trigger.focus();
  e.stopPropagation();
}, true);

// (Library switching is gone — one version owns one library; the
// equivalent operation is switching versions on the product page.)

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

// Measure-distance tool (AutoCAD `DIST`-style, with continuous chaining).
// Mutually exclusive with addMode.
//
// measureState.picks           = the active chain's world [x,y] anchors;
//                                 each left-click appends one; picks.length
//                                 >= 1 means a rubber-band is live from
//                                 picks[last] to the cursor. Frozen segments
//                                 within the active chain are the pairs
//                                 picks[i], picks[i+1].
// measureState.chains          = committed chains (each one a picks-shaped
//                                 array with >= 2 picks). Enter pushes the
//                                 active chain here and resets picks=[].
// measureState.snapHint        = last resolveSnap() result for marker rendering
// measureState.lastCursor      = world [x,y] from the most recent mousemove —
//                                 kept so a Shift key press or right-click
//                                 pop can re-resolve without requiring
//                                 mouse motion
// measureState.cancelHitboxes  = screen-space CSS-pixel rects of the per-chain
//                                 ✕ buttons; rebuilt every render
let measureMode = false;
let measureState = {
  chains: [],
  picks: [],
  snapHint: null,
  lastCursor: null,
  cancelHitboxes: [],
};

function measureAnchor() {
  return measureState.picks.length ? measureState.picks[measureState.picks.length - 1] : null;
}

// Mark-side-regions mode. The user paints up to three axis-aligned,
// world-space rectangles — top_view, bottom_view, side_view — that tag
// each match instance with a "top_view." / "bottom_view." / "side_view."
// key prefix at save-match time. Persisted per-file on the server.
//
// The mode cycles through the three slots in order. For each slot:
//   - left-drag → provisional rect (sideRects[slot] updated, NOT yet PATCHed)
//   - Enter     → commit current sideRects[slot] (provisional or unchanged) and advance
//   - bare-click (release-at-press-point) → skip slot (sideRects[slot] unchanged) and advance
//   - Esc       → cancel entire session: sideRects revert to the pre-session snapshot
//
// The PATCH fires once on the final commit/skip; cancelling never PATCHes.
//
//   sideRects.top_view / .bottom_view / .side_view : {x0,y0,x1,y1} | null
//   sideRectsSnapshot                              : null | {...}  (pre-session copy for Esc)
//   markMode                                       : null | "top_view" | "bottom_view" | "side_view"
//   markQueue                                      : remaining slots to capture this session
//   markDrag                                       : { startWorld, currentWorld } | null
const sideRects = { top_view: null, bottom_view: null, side_view: null };
let sideRectsSnapshot = null;
let markMode = null;
let markQueue = [];
let markDrag = null;
// CSS-pixel hitboxes for the per-view × delete glyph drawn on each label.
// Populated each frame by drawSideRegionLabels; consumed by mousedown.
const sideLabelHitboxes = [];

const SIDE_STYLES = {
  top_view: {
    fill:   "rgba(124, 231, 194, 0.035)",
    stroke: "rgba(124, 231, 194, 0.85)",
    label:  "top view",
    labelColor: "rgba(124, 231, 194, 0.95)",
  },
  bottom_view: {
    fill:   "rgba(231, 160, 124, 0.035)",
    stroke: "rgba(231, 160, 124, 0.85)",
    label:  "bottom view",
    labelColor: "rgba(231, 160, 124, 0.95)",
  },
  side_view: {
    fill:   "rgba(180, 168, 255, 0.035)",
    stroke: "rgba(180, 168, 255, 0.85)",
    label:  "side view",
    labelColor: "rgba(180, 168, 255, 0.95)",
  },
};
const MARK_MIN_AREA = 1e-6; // world-units²; smaller drags count as a bare click (skip)

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
// Below this on-screen radius a circle gets collapsed into a 1×1 dot in a
// per-color batched Path2D fill — cheaper by orders of magnitude than
// stroking N tiny arcs. The number is a UX call: at 3 px a BGA ball reads
// as a "filled dot" rather than its outline, but in dense packaging arrays
// that's already what the eye sees, and the pan FPS gain at mid-zoom is
// massive. Bump back down to ~1 px if visual fidelity becomes a concern.
const DOT_THRESHOLD_CSS_PX = 12.0;

// Per-class colors for Scan All overlay. Chosen for contrast on the DXF's
// dark background and for mutual distinguishability. SMD-2T/3T/8T/14T share
// a red family so the eye groups SMD variants together; the Ring/Lid classes
// share a purple/violet family for the same reason; C4Ball and BGABall share the
// exact same orange (both are ball-type interconnect — user chose visual
// unification over per-class distinction).
const CLASS_COLORS = {
  "SMD-2T":       "#ff5252",  // red
  "SMD-3T":       "#ff8a80",  // light red
  "SMD-8T":       "#d50000",  // deep red
  "SMD-14T":      "#b71c1c",  // maroon
  "Substrate":    "#69f0ae",  // mint
  "DieArea":      "#ffeb3b",  // yellow
  "RingOuter":    "#ba68c8",  // purple
  "RingInner":    "#f06292",  // pink
  "LidOuter":     "#7e57c2",  // deep lavender — sibling of RingOuter
  "LidInner":     "#9575cd",  // muted purple — sibling of LidOuter
  "Lid(SideView)":"#673ab7",  // strong violet — lid side-view profile
  "NovelLidOuter":"#5e35b1",  // indigo-violet — novel lid family
  "NovelLidFP":   "#3949ab",  // indigo — novel lid footprint
  "C4Ball":       "#ffab40",  // orange — shares BGABall color (both are ball-type interconnect)
  "BGABall":      "#ffab40",  // orange
  "Protrusion":   "#80d8ff",  // light blue — distinct from SMD reds / BGA orange
  "Pin-1":        "#f48fb1",  // soft pink
  "FiducialCircle": "#4dd0e1",  // teal
  "FiducialCross":  "#26c6da",  // darker teal — sibling of FiducialCircle
  "FiducialSquare": "#00acc1",  // even darker teal — sibling of FiducialCircle / FiducialCross
  "2DBarcode":      "#c6ff00",  // lime
  "DAM1":           "#8d6e63",  // brown — encapsulation dam (inner); browns have no neighbour in the palette
  "DAM2":           "#5d4037",  // dark brown — encapsulation dam (outer); sibling of DAM1
};
const FALLBACK_CLASS_COLOR = "#888888";
function classColor(name) { return CLASS_COLORS[name] ?? FALLBACK_CLASS_COLOR; }

const DARK_LABEL = "#0b0e13";
const LIGHT_LABEL = "#ffffff";
// WCAG relative luminance of an sRGB colour (channels 0..255).
function _relLuminance(r, g, b) {
  const lin = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
// Luminance of the two candidate labels, precomputed (DARK_LABEL ≈ 0.0045).
const _DARK_LABEL_LUM = _relLuminance(0x0b, 0x0e, 0x13);
const _LIGHT_LABEL_LUM = 1.0;
function _contrastRatio(l1, l2) {
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
// Pick a black or white label that reads on a `hex` background by comparing the
// actual WCAG contrast ratio of each candidate against the fill and taking the
// higher — correct for every fill regardless of saturation (a plain BT.601
// luminance threshold mispicks white on mid-tone cyans/reds/purples). Used to
// label a "found" class button whose background is the full class colour. Falls
// back to near-black on an unparseable colour.
function labelColorOn(hex) {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex || "");
  if (!m) return DARK_LABEL;
  const n = parseInt(m[1], 16);
  const fill = _relLuminance((n >> 16) & 255, (n >> 8) & 255, n & 255);
  return _contrastRatio(fill, _DARK_LABEL_LUM) >= _contrastRatio(fill, _LIGHT_LABEL_LUM)
    ? DARK_LABEL : LIGHT_LABEL;
}

// Per-class view constraints — JS mirror of library.CLASS_VIEW_CONSTRAINTS.
// Some IC-packaging classes only physically appear in specific views:
// C4 bumps in top_view, BGA balls in bottom_view / side_view. The Scan
// All overlay and the match-JSON serialiser both filter constrained
// classes whose containment falls outside the allowed set, including
// the "unassigned" position (no view rect covers the instance).
//
// MUST stay in sync with app/library.py — tests/test_canvas_constants.py
// parses the literal between the BEGIN/END sentinel comments to verify.
// CLASS_VIEW_CONSTRAINTS_BEGIN
const CLASS_VIEW_CONSTRAINTS = {
  "C4Ball":         ["top_view"],
  "BGABall":        ["bottom_view"],
  "FiducialCircle": ["top_view"],
  "FiducialCross":  ["top_view", "bottom_view"],
  "FiducialSquare": ["top_view", "bottom_view"],
  "SMD-2T":         ["top_view", "bottom_view"],
  "SMD-3T":         ["top_view", "bottom_view"],
  "SMD-8T":         ["top_view", "bottom_view"],
  "SMD-14T":        ["top_view", "bottom_view"],
};
// CLASS_VIEW_CONSTRAINTS_END

// Functional grouping for the class toolbar — mirror of library.py
// CLASS_CATEGORY / CLASS_CATEGORY_ORDER (kept in sync by the drift test in
// tests/test_canvas_constants.py). A class absent here is uncategorised and
// the toolbar groups it under a trailing "Other".
// CLASS_CATEGORY_BEGIN
const CLASS_CATEGORY = {
  "Substrate":      "structure",
  "DieArea":        "structure",
  "DAM1":           "structure",
  "DAM2":           "structure",
  "RingOuter":      "structure",
  "RingInner":      "structure",
  "LidOuter":       "structure",
  "LidInner":       "structure",
  "Lid(SideView)":  "structure",
  "NovelLidOuter":  "structure",
  "NovelLidFP":     "structure",
  "Protrusion":     "structure",
  "C4Ball":         "balls",
  "BGABall":        "balls",
  "SMD-2T":         "smd",
  "SMD-3T":         "smd",
  "SMD-8T":         "smd",
  "SMD-14T":        "smd",
  "FiducialCircle": "marks",
  "FiducialCross":  "marks",
  "FiducialSquare": "marks",
  "Pin-1":          "marks",
  "2DBarcode":      "marks",
};
// CLASS_CATEGORY_END
// CLASS_CATEGORY_ORDER_BEGIN
const CLASS_CATEGORY_ORDER = [
  ["structure", "Structure"],
  ["balls", "Balls & Bumps"],
  ["smd", "SMD Pads"],
  ["marks", "Fiducials & Marks"],
];
// CLASS_CATEGORY_ORDER_END

function isAllowedView(className, view) {
  const allowed = CLASS_VIEW_CONSTRAINTS[className];
  if (!allowed) return true;
  return view !== null && allowed.includes(view);
}

// Classify a world-space point against the file's view rectangles
// using the same top_view > bottom_view > side_view priority as
// app.side_regions.side_prefix_for. Returns the matched view name
// or null (unassigned).
function viewForPoint(x, y) {
  const r = (rect) =>
    rect !== null && pointInRect(x, y, rect.x0, rect.y0, rect.x1, rect.y1);
  if (r(sideRects.top_view))    return "top_view";
  if (r(sideRects.bottom_view)) return "bottom_view";
  if (r(sideRects.side_view))   return "side_view";
  return null;
}

// Apply CLASS_VIEW_CONSTRAINTS to a freshly-built scan-all map pair,
// mutating both in place. Drops handles whose class is constrained and
// whose bbox-center isn't in the allowed view set; decrements byClass
// counts accordingly so the status line shows post-filter totals.
//
// Multi-primitive handles get their combined bbox computed by union'ing
// the per-primitive primBBoxes; single-primitive handles (the common
// case for C4Ball / BGABall circles) trivially get their own bbox.
function applyViewConstraintsToScanAll(byHandle, byClass) {
  if (!Object.keys(CLASS_VIEW_CONSTRAINTS).length) return;
  // Group bbox by handle (only handles that need filtering).
  const constrainedHandles = new Set();
  for (const [h, cls] of byHandle) {
    if (CLASS_VIEW_CONSTRAINTS[cls]) constrainedHandles.add(h);
  }
  if (!constrainedHandles.size) return;
  // Union primBBoxes per handle for constrained handles only.
  const handleBBox = new Map(); // handle → [xmin, ymin, xmax, ymax]
  for (let i = 0; i < primitives.length; i++) {
    const h = primitives[i].handle;
    if (!constrainedHandles.has(h)) continue;
    const b = primBBoxes[i];
    const ex = handleBBox.get(h);
    if (!ex) {
      handleBBox.set(h, [b[0], b[1], b[2], b[3]]);
    } else {
      if (b[0] < ex[0]) ex[0] = b[0];
      if (b[1] < ex[1]) ex[1] = b[1];
      if (b[2] > ex[2]) ex[2] = b[2];
      if (b[3] > ex[3]) ex[3] = b[3];
    }
  }
  for (const h of constrainedHandles) {
    const cls = byHandle.get(h);
    const bbox = handleBBox.get(h);
    if (!bbox) continue;  // handle has no primitive (shouldn't happen)
    const cx = (bbox[0] + bbox[2]) * 0.5;
    const cy = (bbox[1] + bbox[3]) * 0.5;
    const view = viewForPoint(cx, cy);
    if (!isAllowedView(cls, view)) {
      byHandle.delete(h);
      byClass[cls] = (byClass[cls] ?? 1) - 1;
      if (byClass[cls] <= 0) delete byClass[cls];
    }
  }
}

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

// ---- recentre on a focused sub-rule (center-entity-on-rule-navigation) ---
// Never frame a target tighter than this many world mm — keeps a tiny /
// single-point sub-rule target from over-zooming on go-to-role navigation.
const MIN_FRAME_SPAN = 40;

// Union world bbox of the focused sub-rule's geometry — handle primitives
// (`from` / `to` / `tol`) plus coordinate points (`from_coordinates` /
// `to_coordinates` / `to_entity`). Returns null when nothing resolves.
function focusedSubRuleBounds() {
  if (!focusedSubRule) return null;
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  const addPt = (x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (x < xmin) xmin = x;
    if (y < ymin) ymin = y;
    if (x > xmax) xmax = x;
    if (y > ymax) ymax = y;
  };
  const addHandle = (h) => {
    if (!h) return;
    for (const p of primitives) {
      if (p.handle !== h) continue;
      const [a, b, c, d] = bboxOf(p);
      if (Number.isFinite(a)) { addPt(a, b); addPt(c, d); }
    }
  };
  addHandle(focusedSubRule.from);
  const toList = Array.isArray(focusedSubRule.to)
    ? focusedSubRule.to
    : (focusedSubRule.to ? [focusedSubRule.to] : []);
  for (const t of toList) addHandle(t);
  addHandle(focusedSubRule.tol);
  const fco = focusedSubRule.from_coordinates;
  const tco = focusedSubRule.to_coordinates;
  if (fco) addPt(fco[0], fco[1]);
  if (tco) addPt(tco[0], tco[1]);
  if (Array.isArray(focusedSubRule.to_entity)) {
    for (const pt of focusedSubRule.to_entity) addPt(pt[0], pt[1]);
  }
  return xmin === Infinity ? null : [xmin, ymin, xmax, ymax];
}

// Pan + zoom so the focused sub-rule is centred and framed. The frame never
// shrinks below MIN_FRAME_SPAN, so a tiny / single-point target lands at a
// standing zoom instead of filling the canvas. No-op when no geometry.
function recenterOnFocusedSubRule() {
  const b = focusedSubRuleBounds();
  if (!b) return;
  const [xmin, ymin, xmax, ymax] = b;
  view.cx = (xmin + xmax) / 2;
  view.cy = (ymin + ymax) / 2;
  // 1.6× the bbox for margin, floored at MIN_FRAME_SPAN.
  const span = Math.max(
    (xmax - xmin) * 1.6, (ymax - ymin) * 1.6, MIN_FRAME_SPAN,
  );
  view.zoom = (Math.min($canvas.width, $canvas.height) / span) * 0.92;
  render();
}

// ---- bbox precomputation -------------------------------------------------
function computeBBoxes() {
  primBBoxes = new Array(primitives.length);
  for (let i = 0; i < primitives.length; i++) primBBoxes[i] = bboxOf(primitives[i]);
}

// ---- circle detection (for CEN / QUA OSNAP) -----------------------------
// DXF CIRCLE entities are flattened to closed polylines by the renderer
// (see app/dxf.py). We re-detect them client-side via `detectCircle` from
// `measure_core.js` so the measure tool can offer center + 4 cardinal
// quadrant snaps for round geometry (BGA balls, fiducial marks, etc.).
let primCircles = [];  // parallel to primitives: null or { cx, cy, r }

function computePrimCircles() {
  primCircles = new Array(primitives.length).fill(null);
  for (let i = 0; i < primitives.length; i++) {
    const p = primitives[i];
    if (p.decorative) continue;
    if (p.type === "circle") {
      // Backend already emitted us a circle — no detection needed.
      primCircles[i] = { cx: p.center[0], cy: p.center[1], r: p.r };
    } else if (p.type === "polyline" && p.closed) {
      primCircles[i] = detectCircle(p.points);
    } else if (p.type === "filled_polygon" && p.rings.length === 1) {
      primCircles[i] = detectCircle(p.rings[0]);
    }
  }
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
    case "circle":         acc(p.center[0] - p.r, p.center[1] - p.r); acc(p.center[0] + p.r, p.center[1] + p.r); break;
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
    case "circle":
      ctx.beginPath();
      ctx.arc(p.center[0], p.center[1], p.r, 0, Math.PI * 2);
      if (p.filled) {
        ctx.fillStyle = fill ?? p.color; ctx.fill();
        if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lineWidth; ctx.stroke(); }
      } else {
        ctx.strokeStyle = stroke ?? p.color; ctx.lineWidth = lineWidth; ctx.stroke();
      }
      break;
  }
}

function worldToScreen(x, y) {
  return [
    (x - view.cx) * view.zoom + $canvas.width / 2,
    -(y - view.cy) * view.zoom + $canvas.height / 2,
  ];
}

// Emit one device-pixel `Path2D` rect per (x, y) world position, keyed by
// color. Steps out of the world transform so dots are crisp 1×1 fills
// regardless of zoom / DPR. Reused by the main pass + every highlight
// pass that needs sub-pixel-circle LOD batching.
function flushDotBuckets(dotBuckets) {
  if (!dotBuckets.size) return;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const halfWpx = $canvas.width / 2, halfHpx = $canvas.height / 2;
  for (const [color, xs] of dotBuckets) {
    const path = new Path2D();
    for (let k = 0; k < xs.length; k += 2) {
      const sx = (xs[k]     - view.cx) * view.zoom + halfWpx;
      const sy = -(xs[k + 1] - view.cy) * view.zoom + halfHpx;
      path.rect(sx | 0, sy | 0, 1, 1);
    }
    ctx.fillStyle = color;
    ctx.fill(path);
  }
  ctx.restore();
}

function render() {
  const t0 = performance.now();
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, $canvas.width, $canvas.height);
  ctx.save();
  ctx.translate($canvas.width / 2, $canvas.height / 2);
  ctx.scale(view.zoom, -view.zoom);
  ctx.translate(-view.cx, -view.cy);

  const hairline = (1 / view.zoom) * dpr;

  // Visible-world rect (with a small margin so a stroke that peeks in
  // doesn't get clipped). Used to cull both the main pass and every
  // highlight pass against the precomputed `primBBoxes`.
  const halfW = $canvas.width  / (2 * view.zoom);
  const halfH = $canvas.height / (2 * view.zoom);
  const cullMargin = hairline * HIGHLIGHT_WIDTH_MULT;
  const vx0 = view.cx - halfW - cullMargin, vx1 = view.cx + halfW + cullMargin;
  const vy0 = view.cy - halfH - cullMargin, vy1 = view.cy + halfH + cullMargin;
  const bboxInView = (b) => !(b[2] < vx0 || b[0] > vx1 || b[3] < vy0 || b[1] > vy1);

  // Dot threshold in world units: at this radius (or below) a circle is
  // sub-pixel and gets collapsed into a batched dot at flush time.
  const dotR = (DOT_THRESHOLD_CSS_PX * dpr) / view.zoom;
  // color → flat Float32 array of (x, y) world positions for batched dots.
  const dotBuckets = new Map();

  let drawn = 0, culled = 0, dot = 0;
  for (let i = 0; i < primitives.length; i++) {
    const p = primitives[i];
    if (!isLayerVisible(p)) continue;
    if (!bboxInView(primBBoxes[i])) { culled++; continue; }
    if (p.type === "circle" && p.r < dotR) {
      let bucket = dotBuckets.get(p.color);
      if (!bucket) { bucket = []; dotBuckets.set(p.color, bucket); }
      bucket.push(p.center[0], p.center[1]);
      dot++;
      continue;
    }
    drawPrimitive(p, { lineWidth: hairline });
    drawn++;
  }
  // Flush each color bucket as a single Path2D of device-pixel rects. We
  // step out of the world transform into device-pixel space so each dot is
  // a crisp 1×1 fill regardless of zoom / DPR — and one fill per color is
  // far cheaper than N separate fillRects for the same N.
  flushDotBuckets(dotBuckets);

  // Persistent side-region overlay — drawn beneath highlights so it never
  // hides selection / match / scan-all feedback.
  drawSideRegionsOverlay(hairline);

  if (scanAllByHandle) {
    const hw = hairline * HIGHLIGHT_WIDTH_MULT;
    const buckets = new Map();
    for (let i = 0; i < primitives.length; i++) {
      const p = primitives[i];
      if (!isLayerVisible(p)) continue;
      if (!bboxInView(primBBoxes[i])) continue;
      const cls = scanAllByHandle.get(p.handle);
      if (!cls) continue;
      if (selection.has(p.handle) || matchSet.has(p.handle) || nearMissSet.has(p.handle)) continue;
      const col = classColor(cls);
      if (p.type === "circle" && p.r < dotR) {
        let bucket = buckets.get(col);
        if (!bucket) { bucket = []; buckets.set(col, bucket); }
        bucket.push(p.center[0], p.center[1]);
        continue;
      }
      drawPrimitive(p, { stroke: col, fill: col, lineWidth: hw });
    }
    flushDotBuckets(buckets);
  }
  if (nearMissSet.size) {
    const hw = hairline * HIGHLIGHT_WIDTH_MULT;
    const buckets = new Map();
    for (let i = 0; i < primitives.length; i++) {
      const p = primitives[i];
      if (!isLayerVisible(p)) continue;
      if (!bboxInView(primBBoxes[i])) continue;
      if (nearMissSet.has(p.handle) && !matchSet.has(p.handle) && !selection.has(p.handle)) {
        if (p.type === "circle" && p.r < dotR) {
          let bucket = buckets.get(NEARMISS_COLOR);
          if (!bucket) { bucket = []; buckets.set(NEARMISS_COLOR, bucket); }
          bucket.push(p.center[0], p.center[1]);
          continue;
        }
        drawPrimitive(p, { stroke: NEARMISS_COLOR, fill: NEARMISS_COLOR, lineWidth: hw });
      }
    }
    flushDotBuckets(buckets);
  }
  if (selection.size || matchSet.size) {
    const hw = hairline * HIGHLIGHT_WIDTH_MULT;
    const buckets = new Map();
    for (let i = 0; i < primitives.length; i++) {
      const p = primitives[i];
      if (!isLayerVisible(p)) continue;
      if (!bboxInView(primBBoxes[i])) continue;
      if (selection.has(p.handle) || matchSet.has(p.handle)) {
        if (p.type === "circle" && p.r < dotR) {
          let bucket = buckets.get(HIGHLIGHT_COLOR);
          if (!bucket) { bucket = []; buckets.set(HIGHLIGHT_COLOR, bucket); }
          bucket.push(p.center[0], p.center[1]);
          continue;
        }
        drawPrimitive(p, { stroke: HIGHLIGHT_COLOR, fill: HIGHLIGHT_COLOR, lineWidth: hw });
      }
    }
    flushDotBuckets(buckets);
  }
  if (hoverSet.size || pinnedSet.size) {
    const hw = hairline * (HIGHLIGHT_WIDTH_MULT + 1);
    const buckets = new Map();
    for (let i = 0; i < primitives.length; i++) {
      const p = primitives[i];
      if (!isLayerVisible(p)) continue;
      if (!bboxInView(primBBoxes[i])) continue;
      if (hoverSet.has(p.handle) || pinnedSet.has(p.handle)) {
        if (p.type === "circle" && p.r < dotR) {
          let bucket = buckets.get(HOVER_COLOR);
          if (!bucket) { bucket = []; buckets.set(HOVER_COLOR, bucket); }
          bucket.push(p.center[0], p.center[1]);
          continue;
        }
        drawPrimitive(p, { stroke: HOVER_COLOR, fill: HOVER_COLOR, lineWidth: hw });
      }
    }
    flushDotBuckets(buckets);
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
  drawSideRegionLabels();
  if (focusedSubRule) drawFocusedLabel();
  if (measureMode) drawMeasureOverlay();

  // Expose per-frame counters for the status line and ad-hoc DevTools
  // probing — drawn / culled / dot let us verify the cull + LOD wins on
  // BGA-heavy files (see data/test_3layers.dxf in tasks.md).
  const ms = performance.now() - t0;
  window.__renderStats = { drawn, culled, dot, ms };
}

// ---- focused sub-rule from rule check ------------------------------------
// The external rule function pre-resolves each sub-rule's `from`/`to`/`tol`
// into single primitive handles. The viewer's job is presentational:
// highlight whichever of those entities are set, and when `from` + `to`
// are both set, draw a dashed segment between the closest pair of points
// across the two primitives' geometries (vertex-vs-edge search, see
// `shortestSegmentBetween` below) — bbox-centre would put the line
// through the entity interior on long thin shapes; perpendicular-foot
// search keeps it pinned to the actual nearest edges.

// Segments (edge pairs) for a single primitive handle. A point is emitted
// as a degenerate segment whose endpoints coincide. Mirrors the old
// list-based collector but takes a single handle since `from`/`to`/`tol`
// are now single-handle fields.
function collectHandleSegments(handle) {
  const segs = [];
  if (!handle) return segs;
  for (const p of primitives) {
    if (p.handle !== handle) continue;
    switch (p.type) {
      case "line":
        segs.push([p.start, p.end]);
        break;
      case "polyline": {
        const pts = p.points;
        for (let i = 1; i < pts.length; i++) segs.push([pts[i - 1], pts[i]]);
        if (p.closed && pts.length > 2) {
          const a = pts[pts.length - 1], b = pts[0];
          if (a[0] !== b[0] || a[1] !== b[1]) segs.push([a, b]);
        }
        break;
      }
      case "filled_polygon":
        for (const ring of p.rings) {
          if (ring.length < 2) continue;
          for (let i = 1; i < ring.length; i++) segs.push([ring[i - 1], ring[i]]);
          const a = ring[ring.length - 1], b = ring[0];
          if (a[0] !== b[0] || a[1] !== b[1]) segs.push([a, b]);
        }
        break;
      case "point":
        segs.push([p.pos, p.pos]);
        break;
      case "circle": {
        // Sample the ring into 32 tangent segments so the shortest-segment
        // search treats the circle as its discretised polygon.
        const cx = p.center[0], cy = p.center[1], r = p.r;
        const N = 32;
        let px = cx + r, py = cy;
        for (let k = 1; k <= N; k++) {
          const a = (2 * Math.PI * k) / N;
          const nx = cx + r * Math.cos(a), ny = cy + r * Math.sin(a);
          segs.push([[px, py], [nx, ny]]);
          px = nx; py = ny;
        }
        break;
      }
    }
    // A handle maps to at most one primitive; stop after finding it.
    break;
  }
  return segs;
}

// True shortest segment between two single primitives: for every vertex
// of one shape, find its closest point on every edge of the other (and
// vice versa); the global minimum is the answer. Captures the
// perpendicular-foot case that pure vertex-to-vertex misses (e.g., two
// parallel SMD edges where the nearest pair sits in the middle of both
// edges, not at either's corner).
function shortestSegmentBetween(handleA, handleB) {
  const segsA = collectHandleSegments(handleA);
  const segsB = collectHandleSegments(handleB);
  if (!segsA.length || !segsB.length) return null;

  let best = Infinity, bestA = null, bestB = null;

  const seenA = new Set();
  for (const [u, v] of segsA) {
    for (const p of [u, v]) {
      const key = `${p[0]},${p[1]}`;
      if (seenA.has(key)) continue;
      seenA.add(key);
      for (const [q1, q2] of segsB) {
        const foot = closestPointOnSegment(p, q1, q2);
        const dx = p[0] - foot[0], dy = p[1] - foot[1];
        const d2 = dx * dx + dy * dy;
        if (d2 < best) { best = d2; bestA = p; bestB = foot; }
      }
    }
  }
  const seenB = new Set();
  for (const [u, v] of segsB) {
    for (const p of [u, v]) {
      const key = `${p[0]},${p[1]}`;
      if (seenB.has(key)) continue;
      seenB.add(key);
      for (const [q1, q2] of segsA) {
        const foot = closestPointOnSegment(p, q1, q2);
        const dx = p[0] - foot[0], dy = p[1] - foot[1];
        const d2 = dx * dx + dy * dy;
        if (d2 < best) { best = d2; bestA = foot; bestB = p; }
      }
    }
  }
  return bestA && bestB ? [bestA, bestB] : null;
}

// Fallback for the from-only / tol-only label position when there's no
// from↔to segment to anchor against — bbox centre is still a reasonable
// single deterministic point inside the entity.
function primitiveCenter(handle) {
  if (!handle) return null;
  for (const p of primitives) {
    if (p.handle !== handle) continue;
    const [xmin, ymin, xmax, ymax] = bboxOf(p);
    if (!Number.isFinite(xmin)) return null;
    return [(xmin + xmax) / 2, (ymin + ymax) / 2];
  }
  return null;
}

function drawFocusedSubRule(hairline) {
  const FOCUS_COLOR = focusedSubRule.rulePass ? "#69f0ae" : "#ff5252";
  const hw = hairline * (HIGHLIGHT_WIDTH_MULT + 1.5);
  // `to` is `str | list[str] | null` on the wire — normalise once at
  // the top so the rest of this function (and `drawFocusedLabel`)
  // works against a single canonical iteration target.
  const toList = Array.isArray(focusedSubRule.to)
    ? focusedSubRule.to
    : (focusedSubRule.to ? [focusedSubRule.to] : []);
  const handles = new Set();
  if (focusedSubRule.from) handles.add(focusedSubRule.from);
  for (const t of toList) handles.add(t);
  if (focusedSubRule.tol)  handles.add(focusedSubRule.tol);
  for (const p of primitives) {
    if (handles.has(p.handle)) {
      drawPrimitive(p, { stroke: FOCUS_COLOR, fill: FOCUS_COLOR, lineWidth: hw });
    }
  }
  if (focusedSubRule.from && toList.length) {
    // Fan: one dashed segment from `from` to each `to_i` (shortest
    // vertex-vs-edge path per pair, same as the scalar form).
    ctx.strokeStyle = FOCUS_COLOR;
    ctx.lineWidth = hairline * 2.2;
    for (const t of toList) {
      const segment = shortestSegmentBetween(focusedSubRule.from, t);
      if (!segment) continue;
      const [fc, tc] = segment;
      ctx.setLineDash([8 * hairline, 5 * hairline]);
      ctx.beginPath();
      ctx.moveTo(fc[0], fc[1]);
      ctx.lineTo(tc[0], tc[1]);
      ctx.stroke();
      ctx.setLineDash([]);
      drawEndpointMarker(fc, hairline);
      drawEndpointMarker(tc, hairline);
    }
  }

  // ---- coordinate mode (points already in this file's world frame) ----
  // (add-rule-check-coordinate-display). Drawn in world space like the
  // handle geometry above.
  const fco = focusedSubRule.from_coordinates;
  const tco = focusedSubRule.to_coordinates;
  if (fco && tco) {
    // Point-to-point distance: a SOLID line between the two raw points.
    ctx.strokeStyle = FOCUS_COLOR;
    ctx.lineWidth = hairline * 2.2;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(fco[0], fco[1]);
    ctx.lineTo(tco[0], tco[1]);
    ctx.stroke();
    drawEndpointMarker(fco, hairline);
    drawEndpointMarker(tco, hairline);
  }
  const poly = focusedSubRule.to_entity;
  if (Array.isArray(poly) && poly.length) {
    // Cross-product target outline: a CLOSED dashed polygon (last→first).
    ctx.strokeStyle = FOCUS_COLOR;
    ctx.lineWidth = hairline * 2.2;
    ctx.setLineDash([8 * hairline, 5 * hairline]);
    ctx.beginPath();
    ctx.moveTo(poly[0][0], poly[0][1]);
    for (let i = 1; i < poly.length; i++) ctx.lineTo(poly[i][0], poly[i][1]);
    ctx.closePath();
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawEndpointMarker(pt, hairline) {
  // Cross-hair (×) — pins the measurement point precisely to its xy.
  // Two diagonal strokes; sized to read clearly at typical viewer
  // zooms (the marker should be obvious without crowding the dashed
  // connector or the highlighted entity).
  const half = hairline * 7;
  ctx.save();
  ctx.lineWidth = hairline * 2.4;
  ctx.beginPath();
  ctx.moveTo(pt[0] - half, pt[1] - half);
  ctx.lineTo(pt[0] + half, pt[1] + half);
  ctx.moveTo(pt[0] + half, pt[1] - half);
  ctx.lineTo(pt[0] - half, pt[1] + half);
  ctx.stroke();
  ctx.restore();
}

function drawLabelBox(text, screenX, screenY, color) {
  if (!text) return;
  ctx.save();
  ctx.font = `${13 * dpr}px ui-monospace, monospace`;
  const m = ctx.measureText(text);
  const padX = 9 * dpr, padY = 5 * dpr;
  const tw = m.width + padX * 2;
  const th = 16 * dpr + padY * 2;
  const x = screenX - tw / 2, y = screenY - th - 10 * dpr;
  ctx.fillStyle = "rgba(0,0,0,0.85)";
  ctx.fillRect(x, y, tw, th);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1 * dpr;
  ctx.strokeRect(x, y, tw, th);
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, screenX, y + th / 2);
  ctx.restore();
}

function drawFocusedLabel() {
  const FOCUS_COLOR = focusedSubRule.rulePass ? "#69f0ae" : "#ff5252";
  // Same normaliser as `drawFocusedSubRule` — `to` can be string,
  // non-empty list, or null. The label anchors to the FIRST segment
  // when `to` is a list, so fan rules don't stack labels on top of
  // each other.
  const toList = Array.isArray(focusedSubRule.to)
    ? focusedSubRule.to
    : (focusedSubRule.to ? [focusedSubRule.to] : []);

  // from + to: text at the midpoint of the (first) segment so the
  // label lands on the same line the user sees.
  // from only: text adjacent to `from`'s bbox centre.
  // tol + tol_text: rendered independently next to `tol` (may coexist
  // with the from/to label when both groups are populated).
  if (focusedSubRule.from && toList.length) {
    const segment = shortestSegmentBetween(focusedSubRule.from, toList[0]);
    if (segment) {
      const [fc, tc] = segment;
      const [mx, my] = worldToScreen((fc[0] + tc[0]) / 2, (fc[1] + tc[1]) / 2);
      drawLabelBox(focusedSubRule.text || focusedSubRule.ruleText || "",
                   mx, my, FOCUS_COLOR);
    }
  } else if (focusedSubRule.from) {
    const fc = primitiveCenter(focusedSubRule.from);
    if (fc) {
      const [mx, my] = worldToScreen(fc[0], fc[1]);
      drawLabelBox(focusedSubRule.text || focusedSubRule.ruleText || "",
                   mx, my, FOCUS_COLOR);
    }
  }

  if (focusedSubRule.tol && focusedSubRule.tol_text) {
    const tc = primitiveCenter(focusedSubRule.tol);
    if (tc) {
      const [mx, my] = worldToScreen(tc[0], tc[1]);
      drawLabelBox(focusedSubRule.tol_text, mx, my, FOCUS_COLOR);
    }
  }

  // Coordinate mode: distance (mm) at the midpoint of the from→to line —
  // the measured value for a point-to-point sub-rule. `text` itself stays
  // in the sidebar (add-rule-check-coordinate-display).
  const fco = focusedSubRule.from_coordinates;
  const tco = focusedSubRule.to_coordinates;
  if (fco && tco) {
    const d = Math.hypot(tco[0] - fco[0], tco[1] - fco[1]);
    const [mx, my] = worldToScreen((fco[0] + tco[0]) / 2, (fco[1] + tco[1]) / 2);
    drawLabelBox(`${fmtCoord(d)} mm`, mx, my, FOCUS_COLOR);
  }
}

// ---- Measure tool overlay -----------------------------------------------
const MEASURE_COLOR = "#ffeb3b";
const $measureReadout = document.getElementById("measure-readout");

function drawSnapMarker(snap) {
  if (!snap || snap.kind === "free") return;
  const [sx, sy] = worldToScreen(snap.x, snap.y);
  const size = 6 * dpr;
  ctx.save();
  ctx.strokeStyle = MEASURE_COLOR;
  ctx.lineWidth = 1.5 * dpr;
  ctx.beginPath();
  if (snap.kind === "endpoint") {
    ctx.rect(sx - size, sy - size, size * 2, size * 2);
  } else if (snap.kind === "midpoint") {
    ctx.moveTo(sx,        sy - size);
    ctx.lineTo(sx + size, sy + size);
    ctx.lineTo(sx - size, sy + size);
    ctx.closePath();
  } else if (snap.kind === "center") {
    ctx.arc(sx, sy, size, 0, Math.PI * 2);
    // Tiny cross-hair inside the circle so it reads as "center" not "circle vertex".
    ctx.moveTo(sx - size * 0.5, sy); ctx.lineTo(sx + size * 0.5, sy);
    ctx.moveTo(sx, sy - size * 0.5); ctx.lineTo(sx, sy + size * 0.5);
  } else if (snap.kind === "quadrant") {
    ctx.moveTo(sx,        sy - size);
    ctx.lineTo(sx + size, sy);
    ctx.lineTo(sx,        sy + size);
    ctx.lineTo(sx - size, sy);
    ctx.closePath();
  } else if (snap.kind === "nearest") {
    ctx.moveTo(sx - size, sy - size); ctx.lineTo(sx + size, sy + size);
    ctx.moveTo(sx + size, sy - size); ctx.lineTo(sx - size, sy + size);
  }
  ctx.stroke();
  ctx.restore();
}

function drawMeasureSegment(a, b, dashed) {
  const [sx1, sy1] = worldToScreen(a[0], a[1]);
  const [sx2, sy2] = worldToScreen(b[0], b[1]);
  ctx.save();
  ctx.strokeStyle = MEASURE_COLOR;
  ctx.lineWidth = 1.5 * dpr;
  if (dashed) ctx.setLineDash([6 * dpr, 4 * dpr]);
  ctx.beginPath();
  ctx.moveTo(sx1, sy1);
  ctx.lineTo(sx2, sy2);
  ctx.stroke();
  if (dashed) ctx.setLineDash([]);
  ctx.restore();
}

// Midpoint-of-segment label. Sits perpendicular-offset from the line so it
// doesn't overlap the rubber-band; mimics the look of drawFocusedLabel.
function drawSegmentLabel(a, b, text) {
  const [sax, say] = worldToScreen(a[0], a[1]);
  const [sbx, sby] = worldToScreen(b[0], b[1]);
  const sdx = sbx - sax, sdy = sby - say;
  const slen = Math.hypot(sdx, sdy);
  if (slen < 2) return;
  const mx = (sax + sbx) / 2;
  const my = (say + sby) / 2;
  // Perpendicular unit (rotate the segment 90° CCW in screen space).
  const px = -sdy / slen, py = sdx / slen;
  const offset = 10 * dpr;
  const cx = mx + px * offset;
  const cy = my + py * offset;

  ctx.save();
  ctx.font = `${12 * dpr}px ui-monospace, monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const m = ctx.measureText(text);
  const padX = 6 * dpr, padY = 3 * dpr;
  const tw = m.width + padX * 2;
  const th = 14 * dpr + padY * 2;
  ctx.fillStyle = "rgba(0,0,0,0.82)";
  ctx.fillRect(cx - tw / 2, cy - th / 2, tw, th);
  ctx.strokeStyle = MEASURE_COLOR;
  ctx.lineWidth = 1 * dpr;
  ctx.strokeRect(cx - tw / 2, cy - th / 2, tw, th);
  ctx.fillStyle = MEASURE_COLOR;
  ctx.fillText(text, cx, cy);
  ctx.restore();
}

function drawPickDot(pt) {
  const [sx, sy] = worldToScreen(pt[0], pt[1]);
  ctx.save();
  ctx.fillStyle = MEASURE_COLOR;
  ctx.beginPath();
  ctx.arc(sx, sy, 4 * dpr, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawMeasureOverlay() {
  const { chains, picks, snapHint } = measureState;
  // Hitboxes are derived state — rebuild every frame so pan / zoom / resize
  // keep the click target glued to the visible ✕ glyph.
  measureState.cancelHitboxes = [];

  // Committed chains: solid segments + endpoint dots + per-segment labels +
  // one ✕ cancel affordance per chain (anchored to the first segment).
  for (let ci = 0; ci < chains.length; ci++) {
    const c = chains[ci];
    for (let i = 1; i < c.length; i++) {
      drawMeasureSegment(c[i - 1], c[i], false);
    }
    for (const p of c) drawPickDot(p);
    for (let i = 1; i < c.length; i++) {
      const a = c[i - 1], b = c[i];
      drawSegmentLabel(a, b, fmtCoord(Math.hypot(b[0] - a[0], b[1] - a[1])));
    }
    if (c.length >= 2) drawCancelButton(c[0], c[1], ci);
  }

  // Active chain: frozen segments between consecutive picks.
  for (let i = 1; i < picks.length; i++) {
    drawMeasureSegment(picks[i - 1], picks[i], false);
  }
  // Endpoint dots on every active pick.
  for (const p of picks) drawPickDot(p);
  // Live rubber-band from the last pick to the snap-resolved cursor.
  const anchor = measureAnchor();
  if (anchor && snapHint) {
    drawMeasureSegment(anchor, [snapHint.x, snapHint.y], true);
  }
  if (snapHint) drawSnapMarker(snapHint);

  // Midpoint label per active-chain segment. Frozen segments show their
  // distance; the live segment additionally appends Σ once the chain has
  // ≥ 2 picks.
  let frozenTotal = 0;
  for (let i = 1; i < picks.length; i++) {
    const a = picks[i - 1], b = picks[i];
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    frozenTotal += d;
    drawSegmentLabel(a, b, fmtCoord(d));
  }
  if (anchor && snapHint) {
    const b = [snapHint.x, snapHint.y];
    const d = Math.hypot(b[0] - anchor[0], b[1] - anchor[1]);
    let label = fmtCoord(d);
    if (picks.length >= 2) {
      label = `${fmtCoord(d)} · Σ=${fmtCoord(frozenTotal + d)}`;
    }
    drawSegmentLabel(anchor, b, label);
  }

  updateMeasureReadout();
}

// Draw a 14×14 CSS-px ✕ button next to the midpoint-offset label of
// segment a..b, then push its CSS-pixel hitbox onto cancelHitboxes so
// mousedown can route the click to chain removal. Position math mirrors
// drawSegmentLabel so the ✕ tracks the label across pan/zoom.
function drawCancelButton(a, b, chainIndex) {
  const [sax, say] = worldToScreen(a[0], a[1]);
  const [sbx, sby] = worldToScreen(b[0], b[1]);
  const sdx = sbx - sax, sdy = sby - say;
  const slen = Math.hypot(sdx, sdy);
  if (slen < 2) return;
  const mx = (sax + sbx) / 2;
  const my = (say + sby) / 2;
  const px = -sdy / slen, py = sdx / slen;
  const offset = 10 * dpr;
  const lcx = mx + px * offset;
  const lcy = my + py * offset;

  // Recompute the label box so we can park the ✕ flush to its right edge.
  // drawSegmentLabel uses the same font/padding — keep them in sync.
  const labelText = fmtCoord(Math.hypot(b[0] - a[0], b[1] - a[1]));
  ctx.save();
  ctx.font = `${12 * dpr}px ui-monospace, monospace`;
  const labelW = ctx.measureText(labelText).width + 12 * dpr;     // text + 2 * padX
  ctx.restore();

  const btn = 14 * dpr;
  const gap = 4 * dpr;
  const bx = lcx + labelW / 2 + gap;          // left edge of the ✕ box
  const by = lcy - btn / 2;                   // top edge

  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.82)";
  ctx.fillRect(bx, by, btn, btn);
  ctx.strokeStyle = MEASURE_COLOR;
  ctx.lineWidth = 1 * dpr;
  ctx.strokeRect(bx, by, btn, btn);
  // ✕ glyph: two diagonal strokes inset by 25% of the box.
  const inset = btn * 0.25;
  ctx.beginPath();
  ctx.moveTo(bx + inset, by + inset);
  ctx.lineTo(bx + btn - inset, by + btn - inset);
  ctx.moveTo(bx + btn - inset, by + inset);
  ctx.lineTo(bx + inset, by + btn - inset);
  ctx.stroke();
  ctx.restore();

  // Hitbox in CSS pixels (screen coords above are in device pixels).
  measureState.cancelHitboxes.push({
    cssLeft:   bx / dpr,
    cssTop:    by / dpr,
    cssRight:  (bx + btn) / dpr,
    cssBottom: (by + btn) / dpr,
    chainIndex,
  });
}

function fmtCoord(n) {
  // 3 decimal places, strip trailing zeros and lone trailing dot.
  return n.toFixed(3).replace(/\.?0+$/, "");
}

function updateMeasureReadout() {
  if (!$measureReadout) return;
  const { snapHint } = measureState;
  const anchor = measureAnchor();
  if (!anchor || !snapHint) { $measureReadout.hidden = true; return; }

  // d and Σ live on the per-segment canvas labels. The HTML readout near
  // the cursor only carries Δx / Δy, which are awkward to express on a
  // single midpoint label and useful for verifying axis-aligned picks.
  const dx = snapHint.x - anchor[0];
  const dy = snapHint.y - anchor[1];
  $measureReadout.hidden = false;
  $measureReadout.innerHTML =
    `<span class="m-dx">Δx = ${fmtCoord(dx)}</span>` +
    `<span class="m-dy">Δy = ${fmtCoord(dy)}</span>`;

  const [sx, sy] = worldToScreen(snapHint.x, snapHint.y);
  const rect = $canvas.getBoundingClientRect();
  const mainRect = $canvas.parentElement.getBoundingClientRect();
  const offX = rect.left - mainRect.left;
  const offY = rect.top - mainRect.top;
  $measureReadout.style.left = `${offX + sx / dpr + 12}px`;
  $measureReadout.style.top  = `${offY + sy / dpr + 12}px`;
}

// ---- Rule check sidebar -------------------------------------------------
const $rulesBtn = document.getElementById("rules-btn");
const $ruleSidebar = document.getElementById("rule-sidebar");
const $ruleSidebarSummary = document.getElementById("rule-sidebar-summary");
const $ruleSidebarBody = document.getElementById("rule-sidebar-body");
const $ruleSidebarClose = document.getElementById("rule-sidebar-close");
const $ruleSearch = document.getElementById("rule-search");
const $ruleCategoryFilter = document.getElementById("rule-category-filter");
const $ruleStatusFilter = document.getElementById("rule-status-filter");

// Rule sidebar filter state — re-renders are driven through these.
let ruleSearchQuery = "";
let ruleCategoryFilter = "";   // "" = all categories
let ruleStatusFilter = "all";  // "all" | "pass" | "fail"
let currentRuleRole = null;    // last role passed to renderRuleSidebar, for filter re-renders

// Rule names follow "<prefix>-<ruleId>"; the category is the <prefix>, i.e.
// everything before the first "-". The ruleId need not be numeric, so we split
// on the separator rather than matching a trailing number. A name without a
// "-" (or one leading with "-") is its own category.
function ruleCategoryOf(name) {
  const i = name.indexOf("-");
  return i > 0 ? name.slice(0, i) : name;
}

// Lightweight case-insensitive subsequence fuzzy match (no extra deps):
// every char of `query` must appear in `text` in order. Empty query matches.
function fuzzyMatch(query, text) {
  if (!query) return true;
  const q = query.toLowerCase();
  const t = (text || "").toLowerCase();
  let i = 0;
  for (let j = 0; j < t.length && i < q.length; j++) {
    if (t[j] === q[i]) i++;
  }
  return i === q.length;
}

// Populate the category <select> from the current rule set, preserving the
// active selection. Called when rule results load, not on every keystroke.
function populateRuleCategoryFilter() {
  const prev = ruleCategoryFilter;
  const cats = new Set();
  if (currentRuleResults?.results) {
    for (const name of Object.keys(currentRuleResults.results)) {
      cats.add(ruleCategoryOf(name));
    }
  }
  const sorted = [...cats].sort();
  $ruleCategoryFilter.innerHTML =
    `<option value="">All categories</option>` +
    sorted.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  ruleCategoryFilter = sorted.includes(prev) ? prev : "";
  $ruleCategoryFilter.value = ruleCategoryFilter;
}

// Rule sidebar defaults every rule to folded; the user-opened rules
// are remembered per session so re-render (e.g. focusing a sub-rule)
// doesn't snap them shut again. Inverted from the prior "remember the
// folded ones" scheme — production rule sets are long, the user reads
// fails first, and most rules stay closed by default.
const RULE_OPEN_KEY = "smdr2.viewer.ruleOpened";
function getOpenedRules() {
  try { return new Set(JSON.parse(sessionStorage.getItem(RULE_OPEN_KEY) ?? "[]")); }
  catch { return new Set(); }
}
function setOpenedRules(s) {
  sessionStorage.setItem(RULE_OPEN_KEY, JSON.stringify([...s]));
}

let currentProductInfo = null;     // version payload cached for sibling links (files_by_role*)
let currentRuleResults = null;     // /api/versions/{vid}/rule-check response cached

async function loadRuleSidebar(role) {
  $rulesBtn.hidden = false;
  // The version payload (sibling files_by_role) was loaded by loadFileInfo;
  // fetch the version's rule check alongside.
  currentProductInfo = currentVersionInfo ?? await fetchVersionContext();
  const rRes = await fetch(`/api/versions/${encodeURIComponent(VERSION_ID)}/rule-check`);
  if (!rRes.ok) {
    // No rule check yet — keep the button visible but the sidebar empty.
    currentRuleResults = null;
    renderRuleSidebar(role);
    return;
  }
  currentRuleResults = await rRes.json();
  populateRuleCategoryFilter();
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
  currentRuleRole = role;
  $ruleSidebarBody.innerHTML = "";
  if (!currentRuleResults) {
    $ruleSidebarSummary.textContent = "";
    $ruleSidebarBody.innerHTML =
      `<div class="empty-msg">No rule check yet for this version. ` +
      `Run it from the dashboard.</div>`;
    return;
  }
  const d = currentRuleResults;

  const opened = getOpenedRules();
  // Fail rules render first so the engineer sees what needs attention
  // without scrolling past the passes. Stable order within each group
  // preserves the external rule function's emission sequence.
  const allEntries = Object.entries(d.results).sort(([, a], [, b]) =>
    a.pass === b.pass ? 0 : a.pass ? 1 : -1
  );
  // Apply the sidebar filters: status (all/pass/fail), category
  // (the <prefix> of the <prefix>-<ruleId> rule name), and a fuzzy search
  // over name + description.
  const entries = allEntries.filter(([name, rule]) => {
    if (ruleStatusFilter === "pass" && !rule.pass) return false;
    if (ruleStatusFilter === "fail" && rule.pass) return false;
    if (ruleCategoryFilter && ruleCategoryOf(name) !== ruleCategoryFilter) return false;
    if (ruleSearchQuery
        && !fuzzyMatch(ruleSearchQuery, name)
        && !fuzzyMatch(ruleSearchQuery, rule.text || "")) return false;
    return true;
  });
  $ruleSidebarSummary.textContent = entries.length === allEntries.length
    ? `${d.pass_count}/${d.rule_count} pass`
    : `${d.pass_count}/${d.rule_count} pass · showing ${entries.length}`;
  if (!entries.length) {
    $ruleSidebarBody.innerHTML =
      `<div class="empty-msg">No rules match the current filter.</div>`;
    return;
  }
  for (const [name, rule] of entries) {
    const details = document.createElement("details");
    details.dataset.ruleName = name;
    details.open = opened.has(name);
    details.addEventListener("toggle", () => {
      const o = getOpenedRules();
      if (details.open) o.add(name); else o.delete(name);
      setOpenedRules(o);
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

// Pick the DXF the sub-rule's geometry actually lives in. When the
// backend tags the sub-rule with `file_id` (multi-DXF roles do, every
// new rule SHOULD) we route on that exactly — otherwise the primary
// file for the role is good enough (single-DXF case).
function resolveSubRuleFile(sub) {
  const siblings = currentProductInfo?.files_by_role_all?.[sub.part] ?? [];
  if (sub.file_id) {
    const match = siblings.find(f => f.id === sub.file_id);
    if (match) return match;
  }
  return currentProductInfo?.files_by_role?.[sub.part] ?? null;
}

function subRuleHasHandles(sub) {
  if (sub.from || sub.from_entity) return true;  // from_entity is an alias
  if (sub.tol) return true;
  if (Array.isArray(sub.to)) return sub.to.length > 0;
  return !!sub.to;
}

// Coordinate-mode geometry (add-rule-check-coordinate-display): a point-to-
// point distance and/or a to_entity outline. Self-located in the open file's
// world frame, so it's always drawable/focusable in the current view.
function subRuleHasCoords(sub) {
  if (sub.from_coordinates && sub.to_coordinates) return true;
  return Array.isArray(sub.to_entity) && sub.to_entity.length > 0;
}

function renderSubRuleItem(ruleName, idx, sub, currentRole, rulePass) {
  const li = document.createElement("li");
  li.dataset.ruleName = ruleName;
  li.dataset.idx = String(idx);

  // No handle AND no coordinate geometry → nothing to draw, so render as
  // plain text without nav hint or click.
  if (!subRuleHasHandles(sub) && !subRuleHasCoords(sub)) {
    li.classList.add("text-only");
    li.innerHTML =
      `<span class="part">${escapeHtml(sub.part)}</span>` +
      `<span class="sub-text">${escapeHtml(sub.text || "")}</span>` +
      `<span></span>`;
    return li;
  }

  const targetFile = resolveSubRuleFile(sub);
  // "Local focus" when the sub-rule's geometry is on THIS DXF (handle mode),
  // OR it carries coordinate geometry (already in this file's frame —
  // add-rule-check-coordinate-display). For multi-DXF roles
  // `sub.part === currentRole` isn't enough — BD siblings share a role but
  // live in separate coordinate spaces.
  const isLocal = subRuleHasCoords(sub)
    || (!!targetFile && targetFile.id === FILE_ID);

  let hintHtml = "";
  if (isLocal) {
    li.classList.add("same-role");
    hintHtml = `<span class="nav-hint">show</span>`;
  } else if (targetFile) {
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
    if (isLocal) {
      focusSubRule(ruleName, idx, rulePass, sub);
      highlightFocusedInSidebar();
    } else if (targetFile) {
      location.href = `/viewer/${targetFile.id}?version_id=${encodeURIComponent(VERSION_ID)}&rule=${encodeURIComponent(ruleName)}&idx=${idx}`;
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
    // `from_entity` is an alias of `from` (add-rule-check-coordinate-display).
    from:     sub.from     ?? sub.from_entity ?? null,
    to:       sub.to       ?? null,
    tol:      sub.tol      ?? null,
    tol_text: sub.tol_text ?? null,
    // Coordinate mode — points already in this file's world frame (DXF mm).
    from_coordinates: sub.from_coordinates ?? null,
    to_coordinates:   sub.to_coordinates ?? null,
    to_entity:        sub.to_entity ?? null,
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
  // Go-to-role landing: centre + frame the target so the operator arrives on
  // the entity, not the default whole-file view. Local sidebar clicks call
  // focusSubRule directly and deliberately keep the current pan/zoom
  // (center-entity-on-rule-navigation).
  recenterOnFocusedSubRule();
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

// ---- rule sidebar filters (fuzzy search + category + status) -------------
$ruleSearch.addEventListener("input", () => {
  ruleSearchQuery = $ruleSearch.value.trim();
  renderRuleSidebar(currentRuleRole);
});
$ruleCategoryFilter.addEventListener("change", () => {
  ruleCategoryFilter = $ruleCategoryFilter.value;
  renderRuleSidebar(currentRuleRole);
});
$ruleStatusFilter.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-status]");
  if (!btn) return;
  ruleStatusFilter = btn.dataset.status;
  for (const b of $ruleStatusFilter.querySelectorAll("button")) {
    b.classList.toggle("active", b === btn);
  }
  renderRuleSidebar(currentRuleRole);
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
    case "circle": {
      const dx = wx - p.center[0], dy = wy - p.center[1];
      const d = Math.hypot(dx, dy);
      return Math.abs(d - p.r) <= tol;
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
    if (!isLayerVisible(primitives[i])) continue;
    const [bxmin, bymin, bxmax, bymax] = primBBoxes[i];
    if (wx < bxmin - tol || wx > bxmax + tol || wy < bymin - tol || wy > bymax + tol) continue;
    if (primHitTest(primitives[i], wx, wy, tol)) return i;
  }
  return -1;
}

// Thin wrappers over the pure core in measure_core.js. They bind the
// canvas's mutable world-state (primitives, bboxes, circles, zoom/dpr) so
// the call site doesn't have to thread state through every invocation.
// The pure implementations are unit-tested in tests/measure_core.test.mjs.
function resolveSnap(wx, wy) {
  const tol = (PICKBOX_CSS_PX * dpr) / view.zoom;
  return _resolveSnapCore({
    wx, wy, primitives, primBBoxes, primCircles, tol,
    isHidden: (p) => !isLayerVisible(p),
  });
}

function applyOrtho(wx, wy, shiftKey) {
  return _applyOrthoCore({
    wx,
    wy,
    anchor: measureAnchor(),
    shiftKey,
    resolveSnapFn: resolveSnap,
  });
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
    if (!isLayerVisible(p)) continue;
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
    case "circle": {
      // Circle-vs-rect: closest point on rect to (cx,cy); intersect if within r.
      // Covers both ring-crosses-rect and rect-inside-disk.
      const cx = p.center[0], cy = p.center[1], r = p.r;
      const qx = cx < xmin ? xmin : (cx > xmax ? xmax : cx);
      const qy = cy < ymin ? ymin : (cy > ymax ? ymax : cy);
      const dx = cx - qx, dy = cy - qy;
      return dx * dx + dy * dy <= r * r;
    }
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
    if (!isLayerVisible(primitives[i])) continue;
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

  if (markMode) {
    $modeHint.textContent = `MARK ${markMode} · drag a rectangle, Enter to keep, click to skip`;
  } else if (measureMode) {
    const n = measureState.picks.length;
    const m = measureState.chains.length;
    const clearLabel = m ? "Esc to clear all" : "Esc to clear";
    if (n === 0 && m === 0) {
      $modeHint.textContent = "MEASURE · pick first point";
    } else if (n === 0) {
      $modeHint.textContent =
        `MEASURE · pick first point · ${m} chain${m === 1 ? "" : "s"} saved (${clearLabel})`;
    } else {
      $modeHint.textContent =
        `MEASURE · pick next point · ${n} pt${n === 1 ? "" : "s"} (Shift = ortho, ${clearLabel})`;
    }
  } else if (addModeClass) {
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
    const [wx, wy] = eventToWorld(e);
    // Per-view × delete hitbox: takes precedence over every other left-click
    // gesture (mark drag, measure pick, selection) so the user can always
    // remove a specific view's rectangle from the canvas chrome.
    if (sideLabelHitboxes.length) {
      const rect = $canvas.getBoundingClientRect();
      const cssX = e.clientX - rect.left;
      const cssY = e.clientY - rect.top;
      for (const h of sideLabelHitboxes) {
        if (cssX >= h.cssLeft && cssX <= h.cssRight &&
            cssY >= h.cssTop  && cssY <= h.cssBottom) {
          clearSpecificView(h.view);
          return;
        }
      }
    }
    if (markMode) {
      // Mark-mode owns the canvas: left-press starts a one-shot rectangle
      // capture for the current side. No selection, no pickbox.
      markDrag = { startWorld: [wx, wy], currentWorld: [wx, wy] };
      render();
      return;
    }
    if (measureMode) {
      // Per-chain ✕ cancel hitbox wins over the pick-append flow. Hitboxes
      // are in CSS pixels — convert clientX/Y to canvas-local CSS pixels.
      const rect = $canvas.getBoundingClientRect();
      const cssX = e.clientX - rect.left;
      const cssY = e.clientY - rect.top;
      for (const h of measureState.cancelHitboxes) {
        if (cssX >= h.cssLeft && cssX <= h.cssRight &&
            cssY >= h.cssTop  && cssY <= h.cssBottom) {
          measureState.chains.splice(h.chainIndex, 1);
          updateStatus();
          render();
          return;
        }
      }
      // Measure click: snap, then append to picks[]. Each click extends the
      // chain — picks[i],picks[i+1] are frozen segments, picks[last] anchors
      // the live rubber-band to the cursor.
      const snap = applyOrtho(wx, wy, e.shiftKey) ?? resolveSnap(wx, wy);
      measureState.picks.push([snap.x, snap.y]);
      measureState.snapHint = snap;
      updateStatus();
      render();
      return;
    }
    // Left: click_pending — becomes box-drag if mouse moves past threshold.
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

  if (markMode && markDrag) {
    markDrag.currentWorld = [wx, wy];
    render();
    return;
  }

  if (measureMode && !drag) {
    measureState.lastCursor = [wx, wy];
    measureState.snapHint = applyOrtho(wx, wy, e.shiftKey) ?? resolveSnap(wx, wy);
    render();
    return;
  }

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
  if (markMode && markDrag) {
    const [x1, y1] = markDrag.startWorld;
    const [x2, y2] = markDrag.currentWorld;
    const x0 = Math.min(x1, x2), xMax = Math.max(x1, x2);
    const y0 = Math.min(y1, y2), yMax = Math.max(y1, y2);
    const area = (xMax - x0) * (yMax - y0);
    markDrag = null;
    if (area < MARK_MIN_AREA) {
      // Bare-click (mousedown→mouseup at same point) — skip the current
      // view, leaving sideRects[markMode] as-is, and advance.
      advanceMarkSlot();
      return;
    }
    // Real drag — capture provisionally and wait for Enter to commit.
    // The rect is visible in the overlay immediately; pressing Enter
    // advances to the next slot, pressing Esc reverts the whole session.
    sideRects[markMode] = { x0, y0, x1: xMax, y1: yMax };
    render();
    return;
  }
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
  if (data.library_id) window.__libraryId = data.library_id;
  renderClassToolbar();
}

async function editClassStrategy(cls) {
  if (SIGNED_OFF) {
    setBaseStatus(SIGNED_MSG);
    return;
  }
  const currentStrategy = cls.match_strategy || "chamfer";
  const strategyRaw = prompt(
    `Match strategy for class "${cls.name}":\n` +
    `  "chamfer" — per-vertex chamfer distance (default, BGA / SMD)\n` +
    `  "signature" — bbox + aspect + vertex-count (substrate / lid)\n` +
    `Enter "chamfer" or "signature":`,
    currentStrategy,
  );
  if (strategyRaw === null) return;  // cancelled
  const strategy = strategyRaw.trim().toLowerCase();
  if (strategy !== "chamfer" && strategy !== "signature") {
    setBaseStatus(`unknown strategy "${strategyRaw}" (use chamfer or signature)`);
    return;
  }
  const body = { strategy };
  if (strategy === "signature") {
    const defaultRatio = cls.bbox_ratio != null ? cls.bbox_ratio : 0.05;
    const ratioRaw = prompt(
      `bbox_ratio for "${cls.name}" (±fraction agreement on bbox extent).\n` +
      `Empty = 0.05 (5%, recommended for substrate). Range: (0, 1].`,
      String(defaultRatio),
    );
    if (ratioRaw === null) return;  // cancelled
    const trimmed = ratioRaw.trim();
    if (trimmed !== "") {
      const v = Number(trimmed);
      if (!Number.isFinite(v) || v <= 0 || v > 1) {
        setBaseStatus(`bbox_ratio must be in (0, 1] (got "${ratioRaw}")`);
        return;
      }
      body.bbox_ratio = v;
    }
  }
  const url = `/api/versions/${encodeURIComponent(VERSION_ID)}/classes/${encodeURIComponent(cls.name)}/strategy`;
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (await handleSignedOff409(res)) return;
    setBaseStatus(`set strategy failed: ${res.status}`);
    return;
  }
  await fetchClasses();
  const saved = await res.json();
  setBaseStatus(
    saved.match_strategy === "signature"
      ? `${cls.name} → signature (bbox_ratio=${saved.bbox_ratio})`
      : `${cls.name} → chamfer (default)`,
  );
}

// Less-common classes hidden from the toolbar by default to keep it tight.
// Hotkeys still resolve to them — only the button is suppressed. Clicking
// "More ▾" reveals them for the rest of the tab session; entering add-mode
// for a hidden class (e.g. via hotkey) auto-expands so the active state is
// visible.
const COLLAPSED_TOOLBAR_CLASSES = new Set(["SMD-3T", "SMD-8T", "SMD-14T"]);
const TOOLBAR_EXPAND_KEY = "smdr2.toolbar.expanded";
function isToolbarExpanded() {
  return sessionStorage.getItem(TOOLBAR_EXPAND_KEY) === "1";
}
function setToolbarExpanded(v) {
  if (v) sessionStorage.setItem(TOOLBAR_EXPAND_KEY, "1");
  else   sessionStorage.removeItem(TOOLBAR_EXPAND_KEY);
}

// Build one class-toolbar button. Extracted from renderClassToolbar so the
// toolbar can render buttons grouped under category headers (CLASS_CATEGORY)
// without duplicating the button-construction logic.
function makeClassBtn(cls) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "class-btn";
  btn.dataset.className = cls.name;
  const clsColor = classColor(cls.name);
  btn.style.setProperty("--class-color", clsColor);
  btn.style.setProperty("--class-text", labelColorOn(clsColor));
  if (addModeClass === cls.name) {
    btn.classList.add(matchesStaged ? "staged" : "active");
  }
  btn.innerHTML = `<span class="name">${cls.name}</span>`;
  const isSig = cls.match_strategy === "signature";
  if (isSig) {
    const tag = document.createElement("span");
    tag.className = "class-strategy-tag";
    tag.textContent = cls.bbox_ratio != null
      ? `sig·${Math.round(cls.bbox_ratio * 100)}%`
      : "sig";
    btn.appendChild(tag);
  }
  // Live match count from the active scan-all / prematch overlay. Only
  // rendered when scan-all has actually been computed for this file
  // (scanAllSummary is null until loadPrematch or runScanAll populates
  // it). A class with zero hits in the current scan still gets the chip
  // so "did the matcher run vs. has it not been touched" reads clearly:
  // missing chip = scan not run; "×0" = scan ran but this class has no
  // matches in this image.
  const matchN = scanAllSummary?.byClass?.[cls.name];
  if (scanAllSummary && matchN !== undefined) {
    const cnt = document.createElement("span");
    cnt.className = "class-match-count";
    cnt.textContent = `×${matchN}`;
    if (matchN === 0) cnt.classList.add("zero");
    btn.appendChild(cnt);
  }
  // Found vs absent telegraphs "already extracted in this image" at a glance.
  // A class matched ≥1 instance (prematch overlay on load, or an explicit
  // scan-all) renders "found": the button is FILLED with its class colour
  // and a contrasting black/white label. Every other class — including
  // before any scan has run — is "absent": a dim class-colour outline. The
  // filled-vs-outline contrast is the strong signal the operator scans for.
  // The class being added right now is exempt (keeps its active styling).
  if (addModeClass !== cls.name) {
    btn.classList.add(matchN > 0 ? "found" : "absent");
  }
  const matchedSuffix = scanAllSummary && matchN !== undefined
    ? `\nmatched ${matchN} instance${matchN === 1 ? "" : "s"} in this image`
    : "";
  btn.title = (isSig
    ? `${cls.name} · ${cls.count} template${cls.count === 1 ? "" : "s"}\n` +
      `signature-only match (bbox/aspect/vertex-count) at ±${(cls.bbox_ratio ?? 0.05) * 100}% — right-click to edit`
    : `${cls.name} · ${cls.count} template${cls.count === 1 ? "" : "s"}\n` +
      `chamfer match (default) — right-click to switch to signature mode`
  ) + matchedSuffix;
  btn.addEventListener("click", () => enterAddMode(cls.name));
  btn.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    editClassStrategy(cls);
  });
  return btn;
}

function renderClassToolbar() {
  // Class buttons live in a single floating "Objects" panel overlaid on the
  // canvas, grouped by CLASS_CATEGORY (Structure / Balls / SMD / Marks /
  // Other). Hotkeys are unaffected: they map HOTKEYS[idx] → classes[idx] by
  // index, independent of grouping.
  if (!$classPanelLeftBody) return;  // not the viewer
  $classPanelLeftBody.innerHTML = "";
  const expanded = isToolbarExpanded()
    || (addModeClass && COLLAPSED_TOOLBAR_CLASSES.has(addModeClass));

  // The More/Less toggle reveals the collapsed SMD variants; it lives inside
  // whichever group owns COLLAPSED_TOOLBAR_CLASSES (the SMD group).
  const makeToggleBtn = () => {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "class-btn class-btn-more";
    let hiddenHits = 0;
    if (!expanded && scanAllSummary) {
      for (const name of COLLAPSED_TOOLBAR_CLASSES) {
        hiddenHits += scanAllSummary.byClass?.[name] ?? 0;
      }
    }
    toggle.textContent = expanded
      ? "Less ▴"
      : (hiddenHits > 0 ? `More ▾ ×${hiddenHits}` : "More ▾");
    toggle.title = expanded
      ? "Hide less-common SMD variants"
      : `Show SMD-3T / SMD-8T / SMD-14T${
          hiddenHits > 0 ? ` (${hiddenHits} matched in this image)` : ""
        }`;
    toggle.addEventListener("click", () => {
      setToolbarExpanded(!expanded);
      renderClassToolbar();
    });
    return toggle;
  };

  // Bucket the library's classes by functional category, preserving rank
  // order within each bucket. A class with no CLASS_CATEGORY entry (e.g. a
  // user-defined class) falls into a trailing "Other" group so it's never
  // dropped.
  const OTHER = "__other__";
  const byCat = new Map();
  for (const cls of classes) {
    const cat = CLASS_CATEGORY[cls.name] || OTHER;
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat).push(cls);
  }
  const renderGroup = (target, label, members) => {
    const hasCollapsible = members.some(
      (c) => COLLAPSED_TOOLBAR_CLASSES.has(c.name));
    const visible = expanded
      ? members
      : members.filter((c) => !COLLAPSED_TOOLBAR_CLASSES.has(c.name));
    if (visible.length === 0 && !hasCollapsible) return false;
    const groupEl = document.createElement("div");
    groupEl.className = "class-group";
    const hdr = document.createElement("span");
    hdr.className = "class-toolbar-group";
    hdr.textContent = label;
    groupEl.appendChild(hdr);
    for (const cls of visible) groupEl.appendChild(makeClassBtn(cls));
    if (hasCollapsible) groupEl.appendChild(makeToggleBtn());
    target.appendChild(groupEl);
    return true;
  };

  let leftGroups = 0;
  for (const [key, label] of CLASS_CATEGORY_ORDER) {
    if (renderGroup($classPanelLeftBody, label, byCat.get(key) || [])) {
      leftGroups++;
    }
  }
  if (byCat.has(OTHER)
      && renderGroup($classPanelLeftBody, "Other", byCat.get(OTHER))) {
    leftGroups++;
  }
  // Hide the panel entirely when it has no groups (e.g. an empty library).
  if ($classPanelLeft) $classPanelLeft.hidden = leftGroups === 0;
}

function enterAddMode(className) {
  if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return; }  // commit path is frozen
  if (measureMode) return;  // mutually exclusive with measure mode
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
    const reqBody = { handles: [...selection] };
    if (addModeClass) reqBody.class_name = addModeClass;
    const res = await fetch(API.match(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reqBody),
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
  if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return; }
  // Saving a template is a write — viewers can't (server would 403 anyway).
  if (viewerReadOnly()) { setBaseStatus("唯讀(viewer)— 無法新增範本"); return; }
  if (!addModeClass || !selection.size) return;
  try {
    const res = await fetch(API.commit(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ class_name: addModeClass, handles: [...selection] }),
    });
    if (!res.ok) {
      if (await handleSignedOff409(res)) return;
      const err = await res.text();
      console.error("commit failed:", err);
      setBaseStatus(`commit error: ${res.status}`);
      return;
    }
    const data = await res.json();
    // Refresh class counts.
    const cls = classes.find(c => c.name === data.class_name);
    if (cls) cls.count = data.count;

    // If scan-all is active, fold this template's just-matched handles
    // into the overlay so the new class lights up immediately. We use
    // the live-preview matchSet that the S-key scan populated — it
    // already holds every handle that matched the same pattern we
    // just committed, so there's no need to round-trip scan-all again
    // (which would re-run all 17 classes' templates server-side).
    //
    // Re-applied after the merge: view constraints (via
    // applyViewConstraintsToScanAll below), which also disambiguate
    // same-geometry classes (BGABall bottom-only vs FiducialCircle
    // top-only). Those are reproducible client-side, so no full scan-all
    // round-trip is needed on commit.
    const newClass = data.class_name;
    let mergedStatus = "";
    // On dedup hit the server discarded the new template; the existing
    // row's handle set is already in the overlay, so any merge here
    // would be a no-op-with-render. Skip the whole block.
    if (!data.already_existed && scanAllByHandle) {
      // Source pattern (user's selection) is an identity match of
      // the just-committed template. The server-side find_matches
      // skips the seed via template_handle_set, so matchSet covers
      // only the OTHER instances. Merge both to cover the full set
      // of handles the new template is responsible for.
      for (const h of selection) scanAllByHandle.set(h, newClass);
      for (const h of matchSet) scanAllByHandle.set(h, newClass);
      const byClass = {};
      for (const [, c] of scanAllByHandle) {
        byClass[c] = (byClass[c] || 0) + 1;
      }
      applyViewConstraintsToScanAll(scanAllByHandle, byClass);
      scanAllSummary = { byClass, total: scanAllByHandle.size };
      mergedStatus = ` · overlay +${byClass[newClass] ?? 0} ${newClass}`;
    }

    setBaseStatus(
      data.already_existed
        ? `template already in library (#${data.count})`
        : `saved ${data.class_name} template (#${data.count})${mergedStatus}`,
    );
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
    applyViewConstraintsToScanAll(byHandle, byClass);
    const filteredTotal = byHandle.size;
    scanAllByHandle = byHandle;
    scanAllSummary = { byClass, total: filteredTotal };
    const dt = (performance.now() - t0).toFixed(0);
    const breakdown = Object.entries(byClass)
      .map(([c, n]) => `${c}:${n}`).join(" ");
    setBaseStatus(`scan-all: ${filteredTotal} hits in ${dt}ms · ${breakdown || "(empty library)"}`);
    renderClassToolbar();
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
  renderClassToolbar();
  render();
}

function toggleScanAll() {
  if (scanAllByHandle) clearScanAll();
  else runScanAll();
}

$scanAllBtn.addEventListener("click", toggleScanAll);

// ---- Save Match JSON -----------------------------------------------------
// The endpoint is async: POST returns 202 + {job_id}; the worker does
// the per-template matching loop on the process pool, and we poll
// `/api/jobs/{job_id}` for completion. The button stays disabled and
// `saveMatchInFlight` holds the active job_id until the poll resolves,
// so a second click while saving is a no-op.
async function saveMatchJson() {
  if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return; }
  if (saveMatchInFlight) return;
  const t0 = performance.now();
  $saveMatchBtn.disabled = true;
  setBaseStatus("save-match: submitting…");
  let jobId;
  try {
    const res = await fetch(API.matchJson(), { method: "POST" });
    if (!res.ok) {
      if (await handleSignedOff409(res)) { $saveMatchBtn.disabled = false; return; }
      const err = await res.text();
      console.error("save-match submit failed:", err);
      setBaseStatus(`save-match error: ${res.status}`);
      $saveMatchBtn.disabled = false;
      return;
    }
    const data = await res.json();
    jobId = data.job_id;
  } catch (e) {
    console.error(e);
    setBaseStatus(`save-match error: ${e.message}`);
    $saveMatchBtn.disabled = false;
    return;
  }
  saveMatchInFlight = jobId;
  setBaseStatus(`save-match: running (job ${jobId.slice(0, 8)}…)`);
  pollSaveMatchJob(jobId, t0);
}

function pollSaveMatchJob(jobId, t0) {
  const intervalId = setInterval(async () => {
    let job;
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (!r.ok) {
        // 404 right after submit can happen in theory if the executor
        // process is still warming; treat as transient and retry.
        console.warn("save-match job poll non-ok:", r.status);
        return;
      }
      job = await r.json();
    } catch (e) {
      console.warn("save-match poll transient failure:", e);
      return;
    }
    if (job.status === "done") {
      clearInterval(intervalId);
      saveMatchInFlight = null;
      const dt = (performance.now() - t0).toFixed(0);
      const result = job.result || {};
      const nKeys = (result.template_keys || []).length;
      setBaseStatus(
        `match saved: ${nKeys} template variant(s), ` +
        `${result.total_matches} total matches → ${result.saved_to} (${dt}ms)`
      );
      if (currentFileInfo) currentFileInfo.match_saved = true;
      refreshRoleSwitcher();
      $saveMatchBtn.disabled = false;
      return;
    }
    if (job.status === "error") {
      clearInterval(intervalId);
      saveMatchInFlight = null;
      setBaseStatus(`save-match error: ${job.error || "(no detail)"}`);
      $saveMatchBtn.disabled = false;
      return;
    }
    // queued / running — keep polling.
  }, 500);
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
  $librarySummary.textContent =
    `${templates.length} template${templates.length === 1 ? "" : "s"}`
    + (SIGNED_OFF ? " · 已畫押(唯讀)" : "");

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

// Fold-state persistence so the user's "I folded BGABall away" sticks
// across opening/closing the modal (and across page reloads in the same tab).
// Classes in DEFAULT_FOLDED_CLASSES start folded on first visit (less-common
// SMD variants); once the user toggles anything, sessionStorage is written
// and their explicit choices win from then on.
const FOLD_KEY = "smdr2.library.folded";
const DEFAULT_FOLDED_CLASSES = ["SMD-3T", "SMD-8T", "SMD-14T"];
function getFoldedClasses() {
  const raw = sessionStorage.getItem(FOLD_KEY);
  if (raw === null) return new Set(DEFAULT_FOLDED_CLASSES);
  try { return new Set(JSON.parse(raw)); }
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

  // Actions: move dropdown + delete. Both mutate the version's library —
  // rendered disabled when the version is signed off (server enforces 409).
  const actions = document.createElement("div");
  actions.className = "actions";
  const moveSelect = document.createElement("select");
  moveSelect.title = SIGNED_OFF ? "版本已畫押(唯讀)— 無法移動範本" : "Move to another class";
  for (const c of classes) {
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = c.name;
    if (c.name === t.class_name) opt.selected = true;
    moveSelect.appendChild(opt);
  }
  moveSelect.disabled = SIGNED_OFF;
  moveSelect.addEventListener("change", async () => {
    moveSelect.disabled = true;
    try {
      const res = await fetch(API.templateOne(t.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ class_name: moveSelect.value }),
      });
      if (!res.ok) {
        if (!(await handleSignedOff409(res))) console.error(await res.text());
        await renderLibrary();  // revert the dropdown to the stored class
      } else {
        await refreshClassCounts();
        await renderLibrary();
      }
    } finally {
      moveSelect.disabled = SIGNED_OFF;
    }
  });
  actions.appendChild(moveSelect);

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "delete-btn";
  delBtn.textContent = "Delete";
  delBtn.disabled = SIGNED_OFF;
  if (SIGNED_OFF) delBtn.title = "版本已畫押(唯讀)— 無法刪除範本";
  delBtn.addEventListener("click", async () => {
    if (!confirm(`Delete template ${t.key}?`)) return;
    delBtn.disabled = true;
    try {
      const res = await fetch(API.templateOne(t.id), { method: "DELETE" });
      if (!res.ok) {
        if (!(await handleSignedOff409(res))) console.error(await res.text());
      } else {
        await refreshClassCounts();
        await renderLibrary();
      }
    } finally {
      delBtn.disabled = SIGNED_OFF;
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
  applyViewConstraintsToScanAll(byHandle, byClass);
  scanAllByHandle = byHandle;
  scanAllSummary = { byClass, total: byHandle.size };
  $scanAllBtn.classList.add("active");
  renderClassToolbar();
  render();
}

// ---- interaction: keyboard -----------------------------------------------
window.addEventListener("keydown", (e) => {
  // Ignore when typing in an input (none right now, but defensive).
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

  // Esc cascade: cancel active box drag → clear every measurement (active
  // chain + every committed chain; stay in measure mode) → cancel mark-mode
  // drag / exit mark mode → clear scan-all overlay → exit add-mode → clear
  // selection.
  if (e.key === "Escape") {
    if (drag && drag.kind === "box") { drag = null; render(); return; }
    if (measureMode && (measureState.picks.length || measureState.chains.length)) {
      measureState.chains = [];
      measureState.picks = [];
      measureState.snapHint = null;
      if ($measureReadout) $measureReadout.hidden = true;
      updateStatus();
      render();
      return;
    }
    if (markMode) {
      if (markDrag) { markDrag = null; render(); return; }
      // Cancel the entire session: revert any provisional rectangles
      // drawn this session, exit without PATCHing the server.
      exitMarkMode({ commit: false });
      return;
    }
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

  // D = toggle measure-distance tool (AutoCAD `DIST`). No-op during add-mode.
  if ((e.key === "d" || e.key === "D") && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (!addModeClass) toggleMeasureMode();
    e.preventDefault();
    return;
  }

  // V = toggle mark-side-regions ("Views") mode — mnemonic for the "Views"
  // toolbar button. No-op while add-mode or measure-mode owns the canvas.
  // ("r" is intentionally NOT bound here, so it falls through to its class
  // hotkey below.)
  if ((e.key === "v" || e.key === "V") && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (!addModeClass && !measureMode) toggleMarkMode();
    e.preventDefault();
    return;
  }

  // C = toggle chain-select mode. No-op while add-mode, measure-mode, or
  // mark-mode owns the canvas (chain mode is a selection-modifier; firing
  // it inside a tool-mode would be confusing).
  if ((e.key === "c" || e.key === "C") && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (!addModeClass && !measureMode && !markMode) toggleChainMode();
    e.preventDefault();
    return;
  }

  // Hotkeys → enter add mode for that class. Don't fire while typing or
  // while measure mode is active (measure mode owns the keyboard).
  if (!e.metaKey && !e.ctrlKey && !e.altKey) {
    const key = e.key.toLowerCase();
    const idx = HOTKEYS.indexOf(key);
    if (idx !== -1 && idx < classes.length) {
      if (measureMode || markMode) { e.preventDefault(); return; }
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

  // Enter inside mark mode = commit current slot's rectangle (provisional
  // or unchanged) and advance to the next slot. After the last slot the
  // session ends and PATCHes the three rectangles in one request.
  if (e.key === "Enter" && markMode) {
    if (markDrag) { markDrag = null; }
    advanceMarkSlot();
    e.preventDefault();
    return;
  }

  // Enter inside measure mode = commit the active chain into chains[] and
  // start a fresh active chain. Chains with < 2 picks have no segment to
  // display, so we reset without committing. addMode and measureMode are
  // mutually exclusive, so this branch sits before the addMode Enter below.
  if (e.key === "Enter" && measureMode) {
    if (measureState.picks.length >= 2) {
      measureState.chains.push(measureState.picks);
    }
    measureState.picks = [];
    measureState.snapHint = null;
    if ($measureReadout) $measureReadout.hidden = true;
    updateStatus();
    render();
    e.preventDefault();
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
function toggleChainMode() {
  chainMode = !chainMode;
  $chainBtn.classList.toggle("active", chainMode);
  if (chainMode) ensureConnectivity();  // build eagerly so first click feels instant
}

$chainBtn.addEventListener("click", toggleChainMode);

// ---- measure tool toggle -------------------------------------------------
const $measureBtn = document.getElementById("measure-btn");

function enterMeasureMode() {
  if (addModeClass) return false;        // blocked while staging a template
  if (measureMode) return true;
  measureMode = true;
  if ($measureBtn) $measureBtn.classList.add("active");
  updateStatus();
  render();
  return true;
}

function exitMeasureMode() {
  measureMode = false;
  measureState = {
    chains: [],
    picks: [],
    snapHint: null,
    lastCursor: null,
    cancelHitboxes: [],
  };
  if ($measureBtn) $measureBtn.classList.remove("active");
  if ($measureReadout) $measureReadout.hidden = true;
  updateStatus();
  render();
}

function toggleMeasureMode() {
  if (measureMode) exitMeasureMode();
  else enterMeasureMode();
}

if ($measureBtn) $measureBtn.addEventListener("click", toggleMeasureMode);

// Re-resolve the rubber-band when Shift is pressed/released without mouse
// movement, so ortho lock engages/disengages on key state alone.
function reresolveMeasureSnap(shiftKey) {
  if (!measureMode || !measureState.lastCursor) return;
  const [wx, wy] = measureState.lastCursor;
  measureState.snapHint = applyOrtho(wx, wy, shiftKey) ?? resolveSnap(wx, wy);
  render();
}
window.addEventListener("keydown", (e) => {
  if (e.key === "Shift" && measureMode && measureAnchor()) {
    reresolveMeasureSnap(true);
  }
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Shift" && measureMode && measureAnchor()) {
    reresolveMeasureSnap(false);
  }
});

// Right-click in measure mode pops the active chain's last pick (AutoCAD's
// `U` inside DIST). The context menu is always suppressed while measure mode
// is on so the user can right-click anywhere without browser surprise.
$canvas.addEventListener("contextmenu", (e) => {
  if (!measureMode) return;
  e.preventDefault();
  if (measureState.picks.length === 0) return;
  measureState.picks.pop();
  // Re-resolve so the rubber-band and snap marker visually catch up to the
  // new anchor without requiring a mousemove.
  if (measureState.lastCursor) {
    const [wx, wy] = measureState.lastCursor;
    measureState.snapHint = applyOrtho(wx, wy, e.shiftKey) ?? resolveSnap(wx, wy);
  } else {
    measureState.snapHint = null;
  }
  updateStatus();
  render();
});

// ---- mark side regions tool ---------------------------------------------
const $sidesBtn = document.getElementById("sides-btn");
const $sidesMenu = document.getElementById("sides-menu");

function drawSideRegionsOverlay(hairline) {
  for (const side of ["top_view", "bottom_view", "side_view"]) {
    const r = sideRects[side];
    if (!r) continue;
    const style = SIDE_STYLES[side];
    const x = r.x0, y = r.y0;
    const w = r.x1 - r.x0, h = r.y1 - r.y0;
    ctx.fillStyle = style.fill;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = hairline * 1.5;
    ctx.strokeRect(x, y, w, h);
  }
  // In-progress mark-mode drag — same style as the view we're currently
  // capturing so the user sees the live preview in the right color.
  if (markMode && markDrag && markDrag.currentWorld) {
    const [x1, y1] = markDrag.startWorld;
    const [x2, y2] = markDrag.currentWorld;
    const style = SIDE_STYLES[markMode];
    const x = Math.min(x1, x2), y = Math.min(y1, y2);
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    ctx.fillStyle = style.fill;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = hairline * 1.5;
    ctx.setLineDash([6 * hairline, 4 * hairline]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
  }
}

// Screen-space labels for the persistent side rectangles. Drawn after the
// world-space ctx.restore() so text stays upright and a constant size.
// Each committed view also gets an "×" delete glyph inside the label
// background; its CSS-pixel hitbox is pushed into sideLabelHitboxes so
// the mousedown handler can clear that specific view.
function drawSideRegionLabels() {
  sideLabelHitboxes.length = 0;
  ctx.save();
  ctx.font = `${12 * dpr}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "center";
  const xGlyph = "×";
  const xGlyphW = ctx.measureText(xGlyph).width;
  const xGap = 6 * dpr;
  for (const side of ["top_view", "bottom_view", "side_view"]) {
    const r = sideRects[side];
    if (!r) continue;
    const style = SIDE_STYLES[side];
    // Top-center of the rectangle, in world coords r has y up so the "top"
    // edge is y1; lift the label a few screen px above it.
    const [sx, syTop] = worldToScreen((r.x0 + r.x1) / 2, r.y1);
    const padX = 8 * dpr, padY = 4 * dpr, gap = 6 * dpr;
    const m = ctx.measureText(style.label);
    const textW = m.width;
    const textH = 12 * dpr;
    const innerW = textW + xGap + xGlyphW;
    const bgX = sx - innerW / 2 - padX;
    const bgY = syTop - gap - textH - padY * 2;
    const bgW = innerW + padX * 2;
    const bgH = textH + padY * 2;
    ctx.fillStyle = "rgba(10, 14, 22, 0.75)";
    ctx.fillRect(bgX, bgY, bgW, bgH);
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = 1 * dpr;
    ctx.strokeRect(bgX, bgY, bgW, bgH);
    ctx.fillStyle = style.labelColor;
    const labelCx = bgX + padX + textW / 2;
    const xCx = bgX + padX + textW + xGap + xGlyphW / 2;
    ctx.fillText(style.label, labelCx, bgY + bgH - padY);
    ctx.fillText(xGlyph, xCx, bgY + bgH - padY);
    // Hitbox in CSS pixels (canvas coords are device pixels, scaled by dpr).
    // Pad a few px so the click target is forgiving.
    const tol = 4 * dpr;
    sideLabelHitboxes.push({
      view: side,
      cssLeft:   (xCx - xGlyphW / 2 - tol) / dpr,
      cssRight:  (xCx + xGlyphW / 2 + tol) / dpr,
      cssTop:    (bgY + padY - tol) / dpr,
      cssBottom: (bgY + bgH - padY + tol) / dpr,
    });
  }
  // In-progress drag: label follows the live rectangle so the user sees
  // which side they're painting before they release.
  if (markMode && markDrag && markDrag.currentWorld) {
    const [x1, y1] = markDrag.startWorld;
    const [x2, y2] = markDrag.currentWorld;
    const style = SIDE_STYLES[markMode];
    const cx = (x1 + x2) / 2, topY = Math.max(y1, y2);
    const [sx, syTop] = worldToScreen(cx, topY);
    const padX = 8 * dpr, padY = 4 * dpr, gap = 6 * dpr;
    const m = ctx.measureText(style.label);
    const textW = m.width;
    const textH = 12 * dpr;
    const bgX = sx - textW / 2 - padX;
    const bgY = syTop - gap - textH - padY * 2;
    const bgW = textW + padX * 2;
    const bgH = textH + padY * 2;
    ctx.fillStyle = "rgba(10, 14, 22, 0.75)";
    ctx.fillRect(bgX, bgY, bgW, bgH);
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = 1 * dpr;
    ctx.setLineDash([4 * dpr, 3 * dpr]);
    ctx.strokeRect(bgX, bgY, bgW, bgH);
    ctx.setLineDash([]);
    ctx.fillStyle = style.labelColor;
    ctx.fillText(style.label, sx, bgY + bgH - padY);
  }
  ctx.restore();
}

function enterMarkMode(queue) {
  if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return false; }
  if (addModeClass || measureMode) return false;
  // Snapshot pre-session sideRects so Esc can revert any provisional drags.
  sideRectsSnapshot = { ...sideRects };
  markQueue = queue.slice();
  markMode = markQueue.shift() ?? null;
  if (!markMode) { exitMarkMode({ commit: false }); return false; }
  if ($sidesBtn) $sidesBtn.classList.add("active");
  markDrag = null;
  updateStatus();
  render();
  return true;
}

function exitMarkMode({ commit }) {
  if (!commit && sideRectsSnapshot) {
    // Revert: restore the three rectangles to their pre-session state.
    sideRects.top_view = sideRectsSnapshot.top_view ?? null;
    sideRects.bottom_view = sideRectsSnapshot.bottom_view ?? null;
    sideRects.side_view = sideRectsSnapshot.side_view ?? null;
  }
  const shouldPatch = commit && !!sideRectsSnapshot;
  sideRectsSnapshot = null;
  markMode = null;
  markQueue = [];
  markDrag = null;
  if ($sidesBtn) $sidesBtn.classList.remove("active");
  updateStatus();
  render();
  if (shouldPatch) {
    patchSideRegions();
  }
}

function toggleMarkMode() {
  if (markMode) { exitMarkMode({ commit: false }); return; }
  enterMarkMode(["top_view", "bottom_view", "side_view"]);
}

async function patchSideRegions() {
  if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return false; }
  const body = {
    top_view_rect: sideRects.top_view,
    bottom_view_rect: sideRects.bottom_view,
    side_view_rect: sideRects.side_view,
  };
  try {
    const res = await fetch(API.sideRegions(), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      if (await handleSignedOff409(res)) return false;
      setBaseStatus(`save sides failed: ${res.status}`);
      return false;
    }
    // The server clears match_saved on any region edit — refresh local copy
    // so the dashboard / save-match button reflects the new state, and re-
    // render the role switcher so a previously-green tab drops back.
    if (currentFileInfo) currentFileInfo.match_saved = false;
    refreshRoleSwitcher();
    return true;
  } catch (e) {
    console.error(e);
    setBaseStatus(`save sides error: ${e.message}`);
    return false;
  }
}

function clearSpecificView(view) {
  // Clear one view's rectangle and persist immediately. Works inside or
  // outside mark mode; when called during mark mode the snapshot is also
  // cleared so Esc cannot restore what the user explicitly deleted.
  if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return; }
  if (!sideRects[view]) return;
  sideRects[view] = null;
  if (sideRectsSnapshot) sideRectsSnapshot[view] = null;
  patchSideRegions();
  render();
}

function advanceMarkSlot() {
  // Move to the next slot in the queue, or commit + exit when done.
  markMode = markQueue.shift() ?? null;
  if (!markMode) {
    exitMarkMode({ commit: true });
  } else {
    updateStatus();
    render();
  }
}

if ($sidesBtn) {
  $sidesBtn.addEventListener("click", toggleMarkMode);
  // Right-click → small dropdown of redraw/clear options.
  $sidesBtn.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!$sidesMenu) return;
    const rect = $sidesBtn.getBoundingClientRect();
    $sidesMenu.style.left = `${rect.left}px`;
    $sidesMenu.style.top = `${rect.bottom + 4}px`;
    $sidesMenu.hidden = false;
  });
}

if ($sidesMenu) {
  $sidesMenu.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-sides-action]");
    if (!btn) return;
    $sidesMenu.hidden = true;
    const action = btn.dataset.sidesAction;
    if (action === "all") {
      enterMarkMode(["top_view", "bottom_view", "side_view"]);
    } else if (action === "top_view" || action === "bottom_view" || action === "side_view") {
      enterMarkMode([action]);
    } else if (action === "clear") {
      if (SIGNED_OFF) { setBaseStatus(SIGNED_MSG); return; }
      sideRects.top_view = null;
      sideRects.bottom_view = null;
      sideRects.side_view = null;
      await patchSideRegions();
      render();
    }
  });
  // Click anywhere else dismisses the menu.
  window.addEventListener("mousedown", (e) => {
    if ($sidesMenu.hidden) return;
    if (!$sidesMenu.contains(e.target) && e.target !== $sidesBtn) {
      $sidesMenu.hidden = true;
    }
  });
}

// ---- layer-visibility panel ---------------------------------------------
// Live, session-only filter that hides primitives from render / pick /
// select / snap / chain. Independent of the persisted `selected_layers`
// that gates Phase 2 matching.

// Distinct layer names sorted alphabetically — derived from the loaded
// primitives, including the implicit "0" bucket for primitives without an
// explicit layer.
let availableLayers = [];

function collectAvailableLayers() {
  const counts = new Map();
  for (const p of primitives) {
    const name = layerOf(p);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  availableLayers = [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name));
  // Drop any stale hidden-layer names that are no longer in the file
  // (post Phase-2 re-run can change the layer set).
  const known = new Set(availableLayers.map(l => l.name));
  for (const n of [...hiddenLayers]) {
    if (!known.has(n)) hiddenLayers.delete(n);
  }
}

function renderVisibilityPanel() {
  $visibilityList.innerHTML = "";
  for (const layer of availableLayers) {
    const hidden = hiddenLayers.has(layer.name);
    const row = document.createElement("label");
    row.className = "vis-row" + (hidden ? " hidden" : "");
    row.innerHTML =
      `<button class="vis-eye" type="button" aria-pressed="${!hidden}" ` +
        `title="${hidden ? "Show" : "Hide"} layer">` +
        `${hidden ? "○" : "●"}</button>` +
      `<span class="vis-name" title="${escAttr(layer.name)}">${escText(layer.name)}</span>` +
      `<span class="vis-count">${layer.count}</span>`;
    row.querySelector(".vis-eye").addEventListener("click", (e) => {
      e.preventDefault();
      toggleLayerVisibility(layer.name);
    });
    $visibilityList.appendChild(row);
  }
}

function escText(s) {
  return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function escAttr(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toggleLayerVisibility(name) {
  if (hiddenLayers.has(name)) hiddenLayers.delete(name);
  else hiddenLayers.add(name);
  persistHiddenLayers();
  // Hidden layers change the connectivity graph — invalidate the lazy cache.
  connectivityGraph = null;
  renderVisibilityPanel();
  render();
}

function showAllLayers() {
  if (!hiddenLayers.size) return;
  hiddenLayers.clear();
  persistHiddenLayers();
  connectivityGraph = null;
  renderVisibilityPanel();
  render();
}

function invertLayerVisibility() {
  const next = new Set();
  for (const layer of availableLayers) {
    if (!hiddenLayers.has(layer.name)) next.add(layer.name);
  }
  hiddenLayers.clear();
  for (const n of next) hiddenLayers.add(n);
  persistHiddenLayers();
  connectivityGraph = null;
  renderVisibilityPanel();
  render();
}

$visibilityBtn.addEventListener("click", () => {
  $visibilityPanel.hidden = !$visibilityPanel.hidden;
  if (!$visibilityPanel.hidden) renderVisibilityPanel();
});
$visibilityClose.addEventListener("click", () => { $visibilityPanel.hidden = true; });
$visibilityAll.addEventListener("click", showAllLayers);
$visibilityInvert.addEventListener("click", invertLayerVisibility);

// ---- developer matching-parameter modal --------------------------------
// Matching-only on the viewer; DXF params and Re-preprocess live on the
// dashboard. The gear shows itself iff dev mode is on in localStorage
// (set by the dashboard's Developer Mode toggle — there's no toggle on
// the viewer page).
mountDevParamsModal({
  toggleId: "dev-params-toggle",
  modalId: "dev-params-modal",
  bodyId: "dev-params-body",
  applyId: "dev-params-apply",
  resetId: "dev-params-reset",
  moduleFilter: "matching",
  statusEl: $status,
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
  if (!primRes.ok) {
    // Most likely HTTP 425: the file is still discovering layers / preprocessing
    // (or errored), so /primitives refuses. Surface it instead of calling
    // .json() on the error body and leaving a permanently blank canvas with the
    // status stuck on "fetching…".
    setBaseStatus(
      primRes.status === 425
        ? "此圖面尚未完成 preprocess,無法開啟 — 請回 Products 頁等待就緒"
        : `無法載入圖元 (HTTP ${primRes.status})`
    );
    return;
  }
  const data = await primRes.json();
  const tFetch = (performance.now() - t0).toFixed(0);

  // Fire-and-forget warm-up of the server-side EntityShape cache
  // (`_cached_shapes` LRU). Without this the user's first /api/match or
  // /api/scan-all pays the ~1–2 s `build_entity_shapes` cost on large
  // drawings; with it, the build runs in the server's worker thread
  // while the canvas is still drawing primitives. Errors are ignored —
  // the cache will be populated lazily by the first scan if this fails.
  fetch(API.warmShapes(), { method: "POST" }).catch(() => {});

  primitives = data.primitives;
  background = data.background || "#1a1f26";
  fitToBbox(data.bbox);

  const tBox0 = performance.now();
  computeBBoxes();
  computePrimCircles();
  const tBox = (performance.now() - tBox0).toFixed(0);

  loadHiddenLayersFromSession();
  collectAvailableLayers();
  renderVisibilityPanel();

  const t1 = performance.now();
  render();
  const tRender = (performance.now() - t1).toFixed(0);

  const rs = window.__renderStats || { drawn: 0, culled: 0, dot: 0 };
  setBaseStatus(
    `${data.count.toLocaleString()} primitives · fetch ${tFetch}ms · bbox ${tBox}ms · render ${tRender}ms · drawn ${rs.drawn.toLocaleString()} culled ${rs.culled.toLocaleString()} dot ${rs.dot.toLocaleString()}`
  );

  // Pre-match overlay was computed at preprocessing time; show it
  // automatically so user sees library coverage on arrival.
  await loadPrematch();
  // Populate the rule-check sidebar (and apply ?rule=&idx= focus if any).
  if (VERSION_ID && currentFileInfo?.dxf_role) {
    await loadRuleSidebar(currentFileInfo.dxf_role);
  } else {
    $rulesBtn.hidden = true;
  }
}

// Signed-off version: pre-disable every mutating affordance in the
// toolbar (server-side 409 guards stay authoritative). Viewing, pan,
// zoom, measure, scan-all, and rule results all keep working.
if (SIGNED_OFF) {
  $saveMatchBtn.disabled = true;
  $saveMatchBtn.title = "版本已畫押(唯讀)— 無法重存 Match JSON";
  const sidesBtnEl = document.getElementById("sides-btn");
  if (sidesBtnEl) {
    sidesBtnEl.disabled = true;
    sidesBtnEl.title = "版本已畫押(唯讀)— 無法標記視圖區域";
  }
}

load();
