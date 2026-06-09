// Dashboard: product cards with per-role DXF slots. Rule check is
// product-scoped and only available once every uploaded file has had its
// Match JSON saved.

import { openLayerModal } from "./layer_modal.js";
import { openLayoutModal } from "./layout_modal.js";
import { mountDevParamsModal } from "./dev_params.js";

// SBT/BD/POD always render as single-role slots. The 4th grid cell
// holds a split pair (RING on the left, LID on the right) — both
// halves are independent slots and may be filled concurrently. See
// the `viewer-ui` / `product-files` specs.
const SINGLE_ROLES = ["SBT", "BD", "POD"];

const $list = document.getElementById("product-list");
const $empty = document.getElementById("empty-msg");
const $status = document.getElementById("status");
const $librarySelect = document.getElementById("library-select");
const $newLibraryBtn = document.getElementById("new-library-btn");
const $newProductBtn = document.getElementById("new-product-btn");
const $modal = document.getElementById("product-modal");
const $newProductName = document.getElementById("new-product-name");
const $newProductLibrary = document.getElementById("new-product-library");
const $newProductCreate = document.getElementById("new-product-create");
const $fileInput = document.getElementById("file-input");
const $devModeToggle = document.getElementById("dev-mode-toggle");

let libraries = [];
let products = [];
let pollTimer = null;
let pendingSlot = null;   // when user clicks a slot or picks file: { productId, role }
// product_id -> { jobId, name } for in-flight rule-check jobs. Used to
// disable the button and to re-enable it on done/error from the
// existing dashboard tick. Hydrated on every refresh from each
// product's `latest_rule_check_job` so a user who navigates away
// during a run still sees the result on return.
const ruleCheckJobs = new Map();

// Persists which finished rule-check job_ids the user has already been
// notified about, so navigating back to the dashboard doesn't re-pop
// the same modal forever. Survives full page reloads via localStorage.
const SEEN_RC_JOBS_KEY = "smdr2.dashboard.seenRuleCheckJobs";
function _loadSeenRuleCheckJobs() {
  try {
    const raw = localStorage.getItem(SEEN_RC_JOBS_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}
function _saveSeenRuleCheckJobs(set) {
  // Cap the set so a long-running dashboard doesn't accumulate forever.
  // 200 is enough for many sessions; we drop the oldest by re-creating
  // from the last N inserted entries.
  const arr = [...set];
  const trimmed = arr.length > 200 ? arr.slice(arr.length - 200) : arr;
  try { localStorage.setItem(SEEN_RC_JOBS_KEY, JSON.stringify(trimmed)); } catch {}
}
let seenRuleCheckJobs = _loadSeenRuleCheckJobs();
function _markRuleCheckJobSeen(jobId) {
  seenRuleCheckJobs.add(jobId);
  _saveSeenRuleCheckJobs(seenRuleCheckJobs);
}

// ---- developer mode -----------------------------------------------------
// Persistent toggle (localStorage) that reveals dev-only download
// affordances on every product card / file row. OFF by default; when
// OFF the affordances aren't mounted at all (no greyed-out clutter).
const DEV_MODE_KEY = "smdr2.dashboard.devMode";
function getDevMode() { return localStorage.getItem(DEV_MODE_KEY) === "1"; }
function setDevMode(on) {
  if (on) localStorage.setItem(DEV_MODE_KEY, "1");
  else    localStorage.removeItem(DEV_MODE_KEY);
}

// Skip-layer-pick dev preference: sticky across reloads + across dev
// mode being toggled off and back on. Persistence is independent of
// dev-mode state so a dev who flips the box on stays in skip mode
// without re-ticking after every session. The flag is only honoured
// at upload time when BOTH dev mode is on AND this preference is on.
const SKIP_LAYER_PICK_KEY = "smdr2.dashboard.skipLayerPick";
function getSkipLayerPick() {
  return localStorage.getItem(SKIP_LAYER_PICK_KEY) === "1";
}
function setSkipLayerPick(on) {
  if (on) localStorage.setItem(SKIP_LAYER_PICK_KEY, "1");
  else    localStorage.removeItem(SKIP_LAYER_PICK_KEY);
}

// Generic browser-side download: wraps a Blob in a transient <a download>,
// clicks it, and revokes the object URL. Used by both the per-file Match
// JSON download and the per-product DRC bundle download so the filename
// is controlled uniformly (browsers would otherwise render JSON inline).
function downloadAsFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---- helpers -------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function fmtSize(b) {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}
function libraryName(id) {
  const l = libraries.find(x => x.id === id);
  return l ? l.name : id;
}

// ---- modal -----------------------------------------------------------------
function openModal() {
  $newProductLibrary.innerHTML = "";
  for (const lib of libraries) {
    const opt = document.createElement("option");
    opt.value = lib.id; opt.textContent = lib.name;
    if (lib.id === $librarySelect.value) opt.selected = true;
    $newProductLibrary.appendChild(opt);
  }
  $newProductName.value = "";
  $modal.hidden = false;
  setTimeout(() => $newProductName.focus(), 0);
}
function closeModal() { $modal.hidden = true; }
$newProductBtn.addEventListener("click", openModal);
$modal.addEventListener("click", (e) => { if (e.target.matches("[data-close]")) closeModal(); });
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$modal.hidden) closeModal(); });

$newProductCreate.addEventListener("click", async () => {
  const name = $newProductName.value.trim();
  if (!name) { $newProductName.focus(); return; }
  const libId = $newProductLibrary.value;
  const res = await fetch("/api/products", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, library_id: libId }),
  });
  if (!res.ok) {
    $status.textContent = `create failed: ${res.status}`;
    return;
  }
  closeModal();
  await refresh();
});

// ---- library bar ---------------------------------------------------------
async function loadLibraries() {
  const res = await fetch("/api/libraries");
  if (!res.ok) return;
  const data = await res.json();
  libraries = data.libraries;
  const prev = sessionStorage.getItem("smdr2.dashboard.selectedLibrary") || data.default_id;
  $librarySelect.innerHTML = "";
  for (const lib of libraries) {
    const opt = document.createElement("option");
    opt.value = lib.id; opt.textContent = lib.name;
    $librarySelect.appendChild(opt);
  }
  $librarySelect.value = libraries.some(l => l.id === prev) ? prev : data.default_id;
}
$librarySelect.addEventListener("change", () => {
  sessionStorage.setItem("smdr2.dashboard.selectedLibrary", $librarySelect.value);
});
$newLibraryBtn.addEventListener("click", async () => {
  const name = prompt("New library name:");
  if (!name || !name.trim()) return;
  const res = await fetch("/api/libraries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  if (!res.ok) { $status.textContent = `create library failed: ${res.status}`; return; }
  const data = await res.json();
  await loadLibraries();
  $librarySelect.value = data.id;
});

// ---- product list --------------------------------------------------------
async function refresh() {
  const res = await fetch("/api/products");
  if (!res.ok) return;
  const data = await res.json();
  products = data.products;
  await _syncRuleCheckJobsFromProducts();
  renderProducts();
}

// For each product, reconcile the in-memory `ruleCheckJobs` Map (and
// "seen" set) against the server-reported `latest_rule_check_job`.
// This is what lets the dashboard pick up a job kicked off in a prior
// browser session: while we were on the viewer, the worker finished
// and persisted `rule_check.json`; on return, we surface the result
// (and auto-open the modal once) instead of leaving the user staring
// at a stale "Re-run Rule Check" button.
async function _syncRuleCheckJobsFromProducts() {
  if (!products.length) return;
  const completedThisSync = [];  // [{product, summary}]
  for (const p of products) {
    const lj = p.latest_rule_check_job;
    if (!lj || !lj.job_id) continue;

    if (lj.status === "queued" || lj.status === "running") {
      // Server still has a live job for this product — make sure we're
      // tracking it so the next tick polls and the button shows
      // "Running…". No-op if we already started it from this tab.
      if (!ruleCheckJobs.has(p.id)) {
        ruleCheckJobs.set(p.id, { jobId: lj.job_id, name: p.name });
        startPollingIfBusy();
      }
      continue;
    }

    if (lj.status === "done") {
      // Cleanup any stale tracking entry for this product (e.g. the
      // job we kicked off has just finished server-side).
      if (ruleCheckJobs.has(p.id)) ruleCheckJobs.delete(p.id);
      if (!seenRuleCheckJobs.has(lj.job_id)) {
        _markRuleCheckJobSeen(lj.job_id);
        completedThisSync.push({ product: p, summary: lj.result || {} });
      }
      continue;
    }

    if (lj.status === "error") {
      if (ruleCheckJobs.has(p.id)) ruleCheckJobs.delete(p.id);
      if (!seenRuleCheckJobs.has(lj.job_id)) {
        _markRuleCheckJobSeen(lj.job_id);
        $status.textContent =
          `Rule check on "${p.name}" failed: ${lj.error || "(no detail)"}`;
      }
      continue;
    }
  }

  // For any completed-while-away jobs, fetch the persisted result and
  // pop the modal once. Sequential to keep the modal stack sane.
  for (const { product, summary } of completedThisSync) {
    try {
      const r = await fetch(`/api/products/${product.id}/rule-check`);
      if (!r.ok) continue;
      const data = await r.json();
      data.roles_covered = summary.roles_covered || [];
      $status.textContent =
        `Rule check on "${product.name}": ` +
        `${data.pass_count}/${data.rule_count} pass ` +
        `(roles: ${data.roles_covered.join(", ")})`;
      showRuleResults(product, data);
    } catch (e) {
      console.error("failed to load persisted rule check", e);
    }
  }
}

// ---- customer fold state -------------------------------------------------
// Dashboard groups product cards by library_id (== customer). Fold state is
// stored as the set of *folded* library_ids so brand-new libraries default
// to folded without us having to write at registration time. Absence of the
// key in sessionStorage means "every section folded" (first-load default).
const FOLD_KEY = "smdr2.dashboard.foldedCustomers";

function loadFoldedSet() {
  try {
    const raw = sessionStorage.getItem(FOLD_KEY);
    if (raw == null) return null;  // null = "no record yet" → treat as all-folded
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}
function saveFoldedSet(set) {
  sessionStorage.setItem(FOLD_KEY, JSON.stringify([...set]));
}

function groupProductsByLibrary(prods) {
  const byLib = new Map();
  for (const p of prods) {
    if (!byLib.has(p.library_id)) byLib.set(p.library_id, []);
    byLib.get(p.library_id).push(p);
  }
  const groups = [];
  for (const [lid, items] of byLib) {
    const lib = libraries.find(l => l.id === lid) ?? { id: lid, name: lid };
    groups.push({ library: lib, products: items });
  }
  // Alphabetical (case-insensitive) by library name; library_id as deterministic tiebreak.
  groups.sort((a, b) => {
    const an = (a.library.name || "").toLowerCase();
    const bn = (b.library.name || "").toLowerCase();
    if (an < bn) return -1;
    if (an > bn) return 1;
    return a.library.id < b.library.id ? -1 : a.library.id > b.library.id ? 1 : 0;
  });
  return groups;
}

function renderProducts() {
  $list.innerHTML = "";
  if (!products.length) {
    $empty.hidden = false;
    return;
  }
  $empty.hidden = true;

  const stored = loadFoldedSet();
  const groups = groupProductsByLibrary(products);
  for (const g of groups) {
    // First-load default (no sessionStorage record) → fold every section.
    const folded = stored === null ? true : stored.has(g.library.id);
    $list.appendChild(customerSection(g.library, g.products, folded));
  }
}

function customerSection(lib, prods, folded) {
  const section = document.createElement("section");
  section.className = "customer-section";
  section.dataset.libraryId = lib.id;
  section.dataset.folded = folded ? "true" : "false";

  const header = document.createElement("header");
  header.className = "customer-section__header";
  header.setAttribute("role", "button");
  header.setAttribute("tabindex", "0");
  header.setAttribute("aria-expanded", folded ? "false" : "true");
  const countLabel = prods.length === 1 ? "1 product" : `${prods.length} products`;
  header.innerHTML =
    `<span class="customer-section__chevron">${folded ? "▸" : "▾"}</span>` +
    `<span class="customer-section__name">${escapeHtml(lib.name || lib.id)}</span>` +
    `<span class="customer-section__count">(${countLabel})</span>`;
  header.addEventListener("click", () => toggleCustomerFold(section));
  header.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();  // Space would otherwise scroll the page
      toggleCustomerFold(section);
    }
  });
  section.appendChild(header);

  const body = document.createElement("div");
  body.className = "customer-section__body";
  for (const p of prods) body.appendChild(productCard(p));
  section.appendChild(body);

  return section;
}

function toggleCustomerFold(section) {
  const isFolded = section.dataset.folded === "true";
  const next = !isFolded;
  section.dataset.folded = next ? "true" : "false";
  const header = section.querySelector(".customer-section__header");
  header.setAttribute("aria-expanded", next ? "false" : "true");
  header.querySelector(".customer-section__chevron").textContent = next ? "▸" : "▾";

  // Persist: load whatever is there (null → empty set, which will then
  // imply the *other* sections become expanded; we want to write an
  // explicit set so future loads stop defaulting all-folded).
  const stored = loadFoldedSet() ?? new Set(
    [...$list.querySelectorAll(".customer-section")]
      .filter(s => s !== section && s.dataset.folded === "true")
      .map(s => s.dataset.libraryId)
  );
  if (next) stored.add(section.dataset.libraryId);
  else stored.delete(section.dataset.libraryId);
  saveFoldedSet(stored);
}

function productCard(p) {
  const card = document.createElement("section");
  card.className = "product-card";
  card.dataset.productId = p.id;

  const header = document.createElement("header");
  header.innerHTML =
    `<span class="product-name">${escapeHtml(p.name)}</span>` +
    `<span class="product-library">${escapeHtml(libraryName(p.library_id))}</span>` +
    `<span class="spacer"></span>` +
    `<button class="product-delete" type="button" title="Delete this product">Delete</button>`;
  header.querySelector(".product-delete").addEventListener("click", () => deleteProduct(p));
  card.appendChild(header);

  const grid = document.createElement("div");
  grid.className = "slot-grid";
  for (const role of SINGLE_ROLES) grid.appendChild(slotCell(p, role));
  grid.appendChild(ringLidPairCell(p));
  card.appendChild(grid);

  const footer = document.createElement("div");
  footer.className = "product-footer";
  const prog = p.match_progress;
  footer.innerHTML =
    `<span class="match-progress">Match: <strong>${prog.saved}</strong>/${prog.total} saved</span>` +
    `<span class="spacer"></span>`;

  const rcBtn = document.createElement("button");
  rcBtn.type = "button";
  rcBtn.className = "rule-check-btn";
  const jobInFlight = ruleCheckJobs.has(p.id);
  rcBtn.disabled = !p.ready_for_rule_check || jobInFlight;
  if (jobInFlight) {
    rcBtn.textContent = "Running…";
  } else {
    rcBtn.textContent = p.rule_check_available && p.ready_for_rule_check
      ? "Re-run Rule Check"
      : "Rule Check";
  }
  if (!p.ready_for_rule_check) {
    const remaining = prog.total === 0
      ? "upload at least one DXF first"
      : `${prog.total - prog.saved} file(s) still need Save Match`;
    rcBtn.title = remaining;
  } else if (jobInFlight) {
    rcBtn.title = "Rule check is running — see status bar.";
  }
  rcBtn.addEventListener("click", () => runRuleCheck(p));
  footer.appendChild(rcBtn);

  // "Check Result" re-opens the persisted rule-check result modal on
  // demand. The modal otherwise only auto-pops once (on job completion and
  // on the first dashboard re-entry after it), so this gives a standing
  // entry point whenever a result exists. Hidden while a job is in flight —
  // the in-flight run will auto-pop the fresh result on completion.
  if (p.rule_check_available && !jobInFlight) {
    const resBtn = document.createElement("button");
    resBtn.type = "button";
    resBtn.className = "rule-check-btn";
    resBtn.textContent = "Check Result";
    resBtn.title = "Show the latest rule-check result";
    resBtn.addEventListener("click", () => showRuleCheckResult(p));
    footer.appendChild(resBtn);
  }
  // Dev mode: Download All Match — the DRC handoff bundle (zip of
  // every role-attached DXF + per-file Match JSON + manifest.json).
  // Disabled (not hidden) when the product isn't ready_for_rule_check
  // so dev users see the affordance with an explanatory tooltip.
  if (getDevMode()) {
    const dlAll = document.createElement("button");
    dlAll.type = "button";
    dlAll.className = "rule-check-btn";
    dlAll.textContent = "Download All Match";
    dlAll.disabled = !p.ready_for_rule_check;
    if (!p.ready_for_rule_check) {
      const remaining = prog.total === 0
        ? "upload at least one DXF first"
        : `${prog.total - prog.saved} file(s) still need Save Match`;
      dlAll.title = remaining;
    } else {
      dlAll.title = "Download every DXF + Match JSON + manifest.json as a zip";
    }
    dlAll.addEventListener("click", () => downloadAllMatch(p));
    footer.appendChild(dlAll);

    // Dev mode: upload a hand-crafted RuleChecking JSON to simulate
    // an external rule-check result. Persists straight to
    // data/rule_check/{pid}.json after envelope validation; lets the
    // external team iterate on output shape without running their
    // pipeline.
    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "rule-check-btn";
    upBtn.textContent = "📤 Upload Rule JSON";
    upBtn.title = "Upload a RuleChecking JSON to test the envelope and preview rendering";
    upBtn.addEventListener("click", () => uploadRuleJson(p));
    footer.appendChild(upBtn);
  }
  card.appendChild(footer);

  return card;
}

// ---- developer-mode download handlers -----------------------------------
async function downloadMatchJson(file) {
  $status.textContent = `downloading match JSON for ${file.name}…`;
  try {
    const r = await fetch(`/api/files/${file.id}/match-json`);
    if (!r.ok) {
      $status.textContent = `download failed: ${r.status}`;
      return;
    }
    downloadAsFile(await r.blob(), `match-${file.id}.json`);
    $status.textContent = `downloaded match-${file.id}.json`;
  } catch (e) {
    $status.textContent = `download failed: ${e.message}`;
  }
}

// Dev mode: prompt for a JSON file, POST it to the upload endpoint.
// Surfaces envelope-validation errors inline (400 detail) so the user
// can fix the JSON and try again.
async function uploadRuleJson(product) {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "application/json,.json";
  picker.addEventListener("change", async () => {
    const file = picker.files?.[0];
    if (!file) return;
    $status.textContent = `uploading "${file.name}" for "${product.name}"…`;
    let body;
    try {
      body = await file.text();
      JSON.parse(body);  // fail fast before the round-trip
    } catch (e) {
      $status.textContent = `upload aborted: invalid JSON — ${e.message}`;
      alert(`Invalid JSON in "${file.name}":\n\n${e.message}`);
      return;
    }
    try {
      const r = await fetch(`/api/products/${product.id}/rule-check/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!r.ok) {
        let detail = `${r.status}`;
        try {
          const j = await r.json();
          if (typeof j?.detail === "string") detail = j.detail;
        } catch { /* not JSON */ }
        $status.textContent = `upload rejected: ${detail}`;
        alert(`Upload rejected (envelope invalid):\n\n${detail}`);
        return;
      }
      const summary = await r.json();
      $status.textContent =
        `Uploaded for "${product.name}": ` +
        `${summary.pass_count}/${summary.rule_count} pass ` +
        `(${summary.fail_count} fail)`;
      await refresh();  // rule_check_available flips, "Rule Check" → "Re-run"
    } catch (e) {
      $status.textContent = `upload failed: ${e.message}`;
    }
  });
  picker.click();
}

async function downloadAllMatch(product) {
  $status.textContent = `building DRC bundle for "${product.name}"…`;
  try {
    const r = await fetch(`/api/products/${product.id}/drc-bundle`);
    if (!r.ok) {
      let msg = `bundle download failed: ${r.status}`;
      try {
        const body = await r.json();
        if (typeof body?.detail === "string") msg = `bundle download failed: ${body.detail}`;
      } catch { /* not JSON */ }
      $status.textContent = msg;
      return;
    }
    downloadAsFile(await r.blob(), `drc-bundle-${product.id}.zip`);
    $status.textContent = `downloaded drc-bundle-${product.id}.zip`;
  } catch (e) {
    $status.textContent = `bundle download failed: ${e.message}`;
  }
}

function slotCell(product, role, opts = {}) {
  const { disabledReason = null } = opts;
  const cell = document.createElement("div");
  cell.className = "slot";
  cell.dataset.role = role;
  cell.dataset.productId = product.id;

  const allFiles = (product.files_by_role_all && product.files_by_role_all[role]) || [];
  cell.innerHTML = `<span class="role-label">${role}</span>`;

  if (!allFiles.length) {
    cell.classList.add("empty");
    if (disabledReason) {
      // RING/LID pair: this half is locked because its opposite role
      // already holds a file. Render a non-interactive placeholder
      // matching the viewer-ui spec's `slot.empty.disabled`.
      cell.classList.add("disabled");
      cell.title = disabledReason;
      cell.innerHTML += `<span class="file-name">unavailable</span>`;
      return cell;
    }
    cell.innerHTML += `<span class="file-name">+ Drop or click</span>`;
    cell.addEventListener("click", () => pickFile(product.id, role));
    wireDragAndDrop(cell, product.id, role);
    return cell;
  }

  if (allFiles.length === 1) {
    // Common case — single-file presentation matches the pre-multi-DXF UI
    // (file name + status + Open/Layers/Replace) and adds a small
    // "+ Add file" affordance so the user can grow into multi-file mode
    // without having to delete-and-re-upload.
    renderSingleFileSlot(cell, product, role, allFiles[0]);
    cell.appendChild(buildAddButton(product, role));
    return cell;
  }

  // 2+ files — stack them, each with its own compact action row.
  const filesContainer = document.createElement("div");
  filesContainer.className = "slot-files";
  for (const f of allFiles) {
    filesContainer.appendChild(slotFileRow(product, role, f, /*compact=*/true));
  }
  cell.appendChild(filesContainer);
  cell.appendChild(buildAddButton(product, role));
  return cell;
}

// The 4th grid cell is one container holding two adjacent `slotCell`
// halves — RING on the left, LID on the right — each rendered as an
// independent single-role slot. Both halves may be filled.
function ringLidPairCell(product) {
  const cell = document.createElement("div");
  cell.className = "slot-pair";
  cell.appendChild(slotCell(product, "RING"));
  cell.appendChild(slotCell(product, "LID"));
  return cell;
}

function buildAddButton(product, role) {
  const btn = document.createElement("button");
  btn.className = "replace-btn slot-add";
  btn.type = "button";
  btn.textContent = "+ Add file";
  btn.title = "Upload another DXF into this role";
  btn.addEventListener("click", () => pickFile(product.id, role));
  return btn;
}

function renderSingleFileSlot(cell, product, role, f) {
  // Inlined "old" rendering: file-name + status + actions directly on
  // the cell, no per-row wrapping.
  const { statusColor, statusLabel, matchBadge } = fileStatusBits(f);
  cell.innerHTML +=
    `<span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>` +
    `<span class="slot-status">${matchBadge} · <span style="color:${statusColor}">${escapeHtml(statusLabel)}</span></span>`;
  appendUnitScaleAnnotation(cell.querySelector(".slot-status"), f);
  cell.appendChild(buildFileActions(product, role, f, /*compact=*/false));
}

function slotFileRow(product, role, f, compact) {
  const row = document.createElement("div");
  row.className = "slot-file";
  row.dataset.fileId = f.id;
  const { statusColor, statusLabel, matchBadge } = fileStatusBits(f);
  row.innerHTML =
    `<span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>` +
    `<span class="slot-status">${matchBadge} · <span style="color:${statusColor}">${escapeHtml(statusLabel)}</span></span>`;
  appendUnitScaleAnnotation(row.querySelector(".slot-status"), f);
  row.appendChild(buildFileActions(product, role, f, compact));
  return row;
}

// bbox diagonal length, or null when there is no bbox yet.
function bboxDiagonal(bbox) {
  if (!bbox || bbox.length < 4) return null;
  const dx = bbox[2] - bbox[0];
  const dy = bbox[3] - bbox[1];
  return Math.hypot(Math.max(dx, 0), Math.max(dy, 0));
}

// Format a length for the unit badges: up to one decimal, thousands
// separators, no trailing ".0".
function fmtLen(n) {
  if (n == null || !isFinite(n)) return "?";
  const r = Math.round(n * 10) / 10;
  return r.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

// Per-file unit annotations on the dashboard (`/`):
//   1. Always show the file's unit (`單位 <label>`).
//   2. If the unit is not mm AND the file was not rescaled, raise an amber
//      warning — it is being treated as mm as-authored, so a non-mm source
//      may be at the wrong scale; the operator should confirm or pick a unit.
//   3. If the file WAS adjusted (a unit override rescaled it to mm), show the
//      before → after extent so the change is visible.
function appendUnitScaleAnnotation(parent, f) {
  if (!parent) return;
  const adjusted = !!(f.applied_scale && f.applied_scale !== 1.0);

  // 1) + 2) unit badge — only once the file has been parsed (has a bbox).
  if (f.bbox && f.unit_label) {
    const warnNonMm = !f.unit_is_mm && !adjusted;
    const badge = document.createElement("span");
    badge.className = warnNonMm ? "warn-badge" : "unit-badge";
    badge.textContent = warnNonMm
      ? `⚠ 單位 ${f.unit_label}（非 mm）`
      : `單位 ${f.unit_label}`;
    badge.title = warnNonMm
      ? `來源 $INSUNITS = ${f.unit_label}，非 mm。目前直接以 mm 處理；` +
        `若實際單位不同，請在檢視器手動指定單位以換算。`
      : `單位：${f.unit_label}`;
    parent.appendChild(badge);
  }

  // 3) adjusted → before/after pill.
  if (adjusted && f.applied_scale_label) {
    const pill = document.createElement("span");
    pill.className = "rescaled-pill";
    const override = f.user_unit_override ? "（手動指定）" : "";
    const post = bboxDiagonal(f.bbox);
    const pre = post != null && f.applied_scale ? post / f.applied_scale : null;
    const beforeAfter =
      pre != null
        ? `：對角線 ${fmtLen(pre)} ${f.unit_label} → ${fmtLen(post)} mm`
        : "";
    pill.textContent = `ℹ 已調整 ${f.applied_scale_label}${override}${beforeAfter}`;
    pill.title = f.unit_scale_warning_detail || "";
    parent.appendChild(pill);
  }

  // Recover pill — independent of unit/rescale; rendered after so the
  // ordering stays unit → rescale → recover → layout.
  appendRecoverAnnotation(parent, f);
  appendLayoutAnnotation(parent, f);
}

// "tab: <name>" pill when the file renders from a paper-space AutoCAD
// layout rather than model space. Modelspace files (chosen_layout NULL)
// get nothing — the common case stays unannotated.
function appendLayoutAnnotation(parent, f) {
  if (!parent || !f.chosen_layout) return;
  const pill = document.createElement("span");
  pill.className = "rescaled-pill";
  pill.textContent = `▦ tab: ${f.chosen_layout}`;
  pill.title = `Geometry loaded from AutoCAD layout tab "${f.chosen_layout}" (model space was empty)`;
  parent.appendChild(pill);
}

function appendRecoverAnnotation(parent, f) {
  const notes = f.dxf_recover_notes;
  if (!notes) return;
  const pill = document.createElement("span");
  pill.className = "rescaled-pill";
  const nFixed = notes.n_fixed ?? 0;
  const nUnrec = notes.n_unrecoverable ?? 0;
  pill.textContent = `ℹ recovered (${nFixed}/${nUnrec})`;
  pill.title = notes.strict_error || "";
  parent.appendChild(pill);
}

function fileStatusBits(f) {
  const statusColor =
    f.status === "ready_to_match"     ? "#69f0ae" :
    f.status === "preprocessing"      ? "#ffb84d" :
    f.status === "discovering_layers" ? "#ffb84d" :
    f.status === "awaiting_layout"    ? "#ffd54f" :
    f.status === "awaiting_layers"    ? "#ffd54f" :
    f.status === "error"              ? "#ff5252" : "#9aa5b1";
  const statusLabel =
    f.status === "discovering_layers" ? "scanning layers…" :
    f.status === "awaiting_layout"    ? "pick view" :
    f.status === "awaiting_layers"    ? "pick layers" :
    f.status;
  const matchBadge = f.match_saved
    ? `<span style="color:#69f0ae;font-size:0.78rem;">✓ matched</span>`
    : `<span style="color:#9aa5b1;font-size:0.78rem;">not matched</span>`;
  return { statusColor, statusLabel, matchBadge };
}

function buildFileActions(product, role, f, compact) {
  const actions = document.createElement("div");
  actions.className = "slot-actions";
  if (f.status === "awaiting_layout") {
    const pickBtn = document.createElement("button");
    pickBtn.className = "primary action-btn";
    pickBtn.type = "button";
    pickBtn.textContent = "Pick view";
    pickBtn.title = "This DXF's geometry is in AutoCAD layout tabs — pick which one to load";
    pickBtn.addEventListener("click", () => promptLayoutSelection(f));
    actions.appendChild(pickBtn);
  } else if (f.status === "awaiting_layers") {
    const pickBtn = document.createElement("button");
    pickBtn.className = "primary action-btn";
    pickBtn.type = "button";
    pickBtn.textContent = "Pick layers";
    pickBtn.addEventListener("click", () => promptLayerSelection(f));
    actions.appendChild(pickBtn);
  } else if (f.status === "ready_to_match") {
    actions.innerHTML = `<a class="open-link" href="/viewer/${f.id}">Open →</a>`;
  }
  // Re-pick the AutoCAD tab when this file went through the layout picker
  // (a manifest exists) and isn't mid-discovery / errored / already at the
  // pick-view gate.
  if (
    f.has_layout_options
    && f.status !== "awaiting_layout"
    && f.status !== "discovering_layers"
    && f.status !== "error"
  ) {
    const viewBtn = document.createElement("button");
    viewBtn.className = "replace-btn";
    viewBtn.type = "button";
    viewBtn.textContent = "View";
    viewBtn.title = "Change which AutoCAD layout tab feeds the matcher";
    viewBtn.addEventListener("click", () => promptLayoutSelection(f));
    actions.appendChild(viewBtn);
  }
  if (
    f.status !== "awaiting_layout"
    && f.status !== "discovering_layers"
    && f.status !== "error"
  ) {
    const layersBtn = document.createElement("button");
    layersBtn.className = "replace-btn";
    layersBtn.type = "button";
    layersBtn.textContent = "Layers";
    layersBtn.title = "Edit which layers feed the matcher";
    layersBtn.addEventListener("click", () => editLayers(f));
    actions.appendChild(layersBtn);
  }
  const replace = document.createElement("button");
  replace.className = "replace-btn";
  replace.type = "button";
  replace.textContent = "Replace";
  replace.title = "Replace this DXF";
  replace.addEventListener("click", () => pickFile(product.id, role, f.id));
  actions.appendChild(replace);
  // Dev mode: Download Match JSON. Hidden entirely unless dev mode is on
  // AND the file has a saved Match JSON to download (endpoint would 404
  // otherwise). Filename `match-<file_id>.json` matches the on-disk
  // storage convention so file ids in dev tools are easy to cross-ref.
  if (getDevMode() && f.match_saved) {
    const dl = document.createElement("button");
    dl.className = "replace-btn";
    dl.type = "button";
    dl.textContent = "Download Match";
    dl.title = "Download this file's Match JSON";
    dl.addEventListener("click", () => downloadMatchJson(f));
    actions.appendChild(dl);
  }
  // Delete (detach) is exposed on every file row — including single-file
  // slots — so the engineer can empty a slot without uploading a
  // replacement. Needed for flows like switching a product between RING
  // and LID, which requires detaching the lone existing file first.
  const del = document.createElement("button");
  del.className = "replace-btn";
  del.type = "button";
  del.textContent = "✕";
  del.title = "Remove this DXF from the role";
  del.addEventListener("click", () => deleteProductFile(product, role, f));
  actions.appendChild(del);
  return actions;
}

function wireDragAndDrop(cell, productId, role) {
  cell.addEventListener("dragover", (e) => { e.preventDefault(); cell.classList.add("dragover"); });
  cell.addEventListener("dragleave", () => cell.classList.remove("dragover"));
  cell.addEventListener("drop", (e) => {
    e.preventDefault();
    cell.classList.remove("dragover");
    const file = [...(e.dataTransfer?.files ?? [])]
      .find(f => f.name.toLowerCase().endsWith(".dxf"));
    if (file) uploadFile(productId, role, file);
  });
}

// `replaceFileId` is the id of the file this upload should evict before
// landing the new one (the "Replace" button path). Omit for additive uploads.
function pickFile(productId, role, replaceFileId = null) {
  pendingSlot = { productId, role, replaceFileId };
  $fileInput.click();
}
$fileInput.addEventListener("change", () => {
  const f = $fileInput.files?.[0];
  $fileInput.value = "";
  if (f && pendingSlot) {
    uploadFile(
      pendingSlot.productId,
      pendingSlot.role,
      f,
      pendingSlot.replaceFileId || null,
    );
  }
});

async function uploadFile(productId, role, file, replaceFileId = null) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("dxf_role", role);
  if (replaceFileId) fd.append("replace_file_id", replaceFileId);
  // Dev shortcut: bypass Phase 1 entirely. The flag is honoured by
  // the server unconditionally, but we only send it when BOTH dev
  // mode is on AND the dev preference is on, so production users
  // (who never see the checkbox) never trigger it.
  if (getDevMode() && getSkipLayerPick()) {
    fd.append("skip_layer_pick", "true");
  }
  $status.textContent = `uploading ${file.name} → ${role}…`;
  const res = await fetch(`/api/products/${productId}/files`, { method: "POST", body: fd });
  if (!res.ok) {
    let msg = `upload failed: ${res.status}`;
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (typeof detail === "string") msg = `upload failed: ${detail}`;
    } catch { /* response wasn't JSON */ }
    $status.textContent = msg;
    return;
  }
  $status.textContent = `uploaded ${file.name} → ${role}`;
  await refresh();
  startPollingIfBusy();
}

async function deleteProductFile(product, role, file) {
  if (!confirm(`Remove "${file.name}" from ${role}?`)) return;
  const res = await fetch(`/api/products/${product.id}/files/${file.id}`, { method: "DELETE" });
  if (!res.ok) {
    $status.textContent = `remove failed: ${res.status}`;
    return;
  }
  $status.textContent = `removed ${file.name} from ${role}`;
  await refresh();
}

async function deleteProduct(p) {
  if (!confirm(`Delete product "${p.name}" and all its files from this view?`)) return;
  const res = await fetch(`/api/products/${p.id}`, { method: "DELETE" });
  if (!res.ok) {
    $status.textContent = `delete failed: ${res.status}`;
    return;
  }
  await refresh();
}

async function runRuleCheck(p) {
  if (ruleCheckJobs.has(p.id)) return;  // already in flight; button should be disabled
  $status.textContent = `submitting rule check on "${p.name}"…`;
  const res = await fetch(`/api/products/${p.id}/rule-check`, { method: "POST" });
  if (!res.ok) {
    const err = await res.text();
    $status.textContent = `rule-check submit failed: ${res.status}`;
    console.error(err);
    return;
  }
  const { job_id: jobId } = await res.json();
  ruleCheckJobs.set(p.id, { jobId, name: p.name });
  $status.textContent =
    `Rule check on "${p.name}" running (job ${jobId.slice(0, 8)}…)`;
  renderProducts();      // reflect "Running…" on the button immediately
  startPollingIfBusy();  // tick handler watches `ruleCheckJobs` too
}

// Poll a single rule-check job; called from the dashboard tick. Returns
// `true` while the job is still in flight so the tick keeps running.
async function _stepRuleCheckJob(productId) {
  const entry = ruleCheckJobs.get(productId);
  if (!entry) return false;
  let job;
  try {
    const r = await fetch(`/api/jobs/${entry.jobId}`);
    if (!r.ok) {
      $status.textContent = `rule-check job lost: ${r.status}`;
      ruleCheckJobs.delete(productId);
      return false;
    }
    job = await r.json();
  } catch (e) {
    $status.textContent = `rule-check poll error: ${e}`;
    return true;  // transient — try again next tick
  }
  if (job.status === "done") {
    ruleCheckJobs.delete(productId);
    _markRuleCheckJobSeen(entry.jobId);
    const summary = job.result || {};
    $status.textContent =
      `Rule check on "${entry.name}": ` +
      `${summary.pass_count}/${summary.rule_count} pass ` +
      `(roles: ${(summary.roles_covered || []).join(", ")})`;
    // Refresh products (so rule_check_available flips) and fetch the
    // persisted result for the modal.
    await refresh();
    const product = products.find(p => p.id === productId);
    if (product) {
      try {
        const r = await fetch(`/api/products/${productId}/rule-check`);
        if (r.ok) {
          const data = await r.json();
          // The persisted GET returns `results / rule_count / pass_count /
          // fail_count` but not roles_covered; merge the job summary in
          // so `showRuleResults` can render the roles line.
          data.roles_covered = summary.roles_covered || [];
          showRuleResults(product, data);
        }
      } catch (e) {
        console.error("failed to load persisted rule check", e);
      }
    }
    return false;
  }
  if (job.status === "error") {
    ruleCheckJobs.delete(productId);
    _markRuleCheckJobSeen(entry.jobId);
    $status.textContent = `Rule check on "${entry.name}" failed: ${job.error || "(no detail)"}`;
    renderProducts();
    return false;
  }
  // queued or running
  return true;
}

const $ruleResultsModal = document.getElementById("rule-results-modal");
const $ruleResultsTitle = document.getElementById("rule-results-title");
const $ruleResultsSummary = document.getElementById("rule-results-summary");
const $ruleResultsBody = document.getElementById("rule-results-body");

$ruleResultsModal.addEventListener("click", (e) => {
  if (e.target.matches("[data-close]")) $ruleResultsModal.hidden = true;
});

// `to` may arrive as string, non-empty list of strings, or null. A
// non-empty list is locatable; an empty list (illegal upstream per
// the DRC integration contract, but we stay defensive) is treated
// as no-`to`.
function hasToValue(to) {
  if (!to) return false;
  if (Array.isArray(to)) return to.length > 0;
  return true;
}

// A sub-rule is "locatable" iff it carries at least one handle field
// the viewer can highlight (from / to / tol). Per the DRC integration
// contract every well-formed sub-rule SHOULD be locatable, but the
// dashboard stays defensive against malformed emits so a click on a
// no-handles row doesn't open the viewer to an empty highlight.
function isLocatable(sub) {
  return Boolean(sub && (sub.from || hasToValue(sub.to) || sub.tol));
}

// Re-open the persisted rule-check result modal for a product on demand
// (the "Check Result" button). Unlike the auto-pop path there's no live job
// summary here, so roles_covered comes from the product's latest rule-check
// job result when available (null after a server restart → empty list, which
// showRuleResults renders gracefully).
async function showRuleCheckResult(p) {
  $status.textContent = `loading rule-check result for "${p.name}"…`;
  try {
    const r = await fetch(`/api/products/${p.id}/rule-check`);
    if (!r.ok) {
      $status.textContent = `no rule-check result for "${p.name}" (${r.status})`;
      return;
    }
    const data = await r.json();
    data.roles_covered = p.latest_rule_check_job?.result?.roles_covered || [];
    showRuleResults(p, data);
    $status.textContent = "";
  } catch (e) {
    $status.textContent = `failed to load rule-check result: ${e}`;
    console.error("showRuleCheckResult", e);
  }
}

function showRuleResults(product, data) {
  $ruleResultsTitle.textContent = `Rule Check — ${product.name}`;
  $ruleResultsSummary.textContent =
    `${data.pass_count}/${data.rule_count} pass · roles: ${(data.roles_covered || []).join(", ")}`;
  $ruleResultsBody.innerHTML = "";

  for (const [name, rule] of Object.entries(data.results)) {
    const card = document.createElement("section");
    card.className = "rule-result-card";
    const status = rule.pass ? "✓" : "✗";
    const statusClass = rule.pass ? "pass" : "fail";

    // Affordance chip: tells the operator at a glance how many of this
    // rule's sub-rules can be located in the viewer vs. are text-only.
    const subs = rule.rules || [];
    let chipText, chipTitle;
    if (subs.length === 0) {
      chipText = "ℹ no locator";
      chipTitle = "no sub-rules emitted";
    } else {
      const nLocatable = subs.filter(isLocatable).length;
      const nTextOnly = subs.length - nLocatable;
      chipText = `🎯 ${nLocatable} · ℹ ${nTextOnly}`;
      chipTitle = `${nLocatable} locatable · ${nTextOnly} text-only sub-rule(s)`;
    }

    card.innerHTML =
      `<header>` +
        `<span class="status ${statusClass}">${status}</span>` +
        `<span class="name">${escapeHtml(name)}</span>` +
        `<span class="text">${escapeHtml(rule.text || "")}</span>` +
        `<span class="rescaled-pill" title="${escapeHtml(chipTitle)}">${escapeHtml(chipText)}</span>` +
      `</header>` +
      `<ol class="subrules"></ol>`;
    const subList = card.querySelector(".subrules");
    if (!subs.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No sub-rules emitted (rule could not be evaluated)";
      subList.appendChild(li);
    } else {
      subs.forEach((sub, idx) => {
        // Prefer `sub.file_id` (set by every origin-scoped rule) so
        // multi-DXF roles route to the DXF whose geometry the sub-rule
        // actually references. Falls back to the primary file for the
        // role when the rule emits no `file_id` (e.g. legacy data).
        // `sub.part` may be RING or LID; the backend keys
        // `files_by_role_all` by raw `dxf_role`, so a sub-rule with
        // `part: "LID"` lights up the LID half of the split 4th cell
        // without any extra branching here.
        const siblings = product.files_by_role_all?.[sub.part] ?? [];
        const file = (sub.file_id && siblings.find(f => f.id === sub.file_id))
                  || product.files_by_role[sub.part];
        const locatable = isLocatable(sub);
        const li = document.createElement("li");
        // Three branches: (a) file not uploaded → existing no-file
        // message, no icon; (b) locatable → 🎯 + clickable link;
        // (c) text-only → ℹ + dimmed row, no link.
        let trailing;
        let textPrefix = "";
        if (!file) {
          trailing = `<span class="no-file">${escapeHtml(sub.part)} not uploaded</span>`;
        } else if (locatable) {
          textPrefix = "🎯 ";
          trailing = `<a class="view-link" href="/viewer/${file.id}?rule=${encodeURIComponent(name)}&idx=${idx}">View in ${escapeHtml(sub.part)} →</a>`;
        } else {
          textPrefix = "ℹ ";
          trailing = "";
          li.classList.add("subrule-text-only");
        }
        li.innerHTML =
          `<span class="part">${escapeHtml(sub.part)}</span>` +
          `<span class="text">${escapeHtml(textPrefix + (sub.text || ""))}</span>` +
          trailing;
        subList.appendChild(li);
      });
    }
    $ruleResultsBody.appendChild(card);
  }
  $ruleResultsModal.hidden = false;
}

// ---- layer-selection prompts --------------------------------------------
// The modal is user-driven only — never auto-popped. A file sitting in
// `awaiting_layers` shows a "Pick layers" call-to-action on its slot;
// clicking it (or the "Layers" button on a post-Phase-1 file) is the
// only way to open the modal.
async function promptLayerSelection(file) {
  const result = await openLayerModal({
    fileId: file.id,
    fileName: file.name,
    onConfirm: async () => {
      $status.textContent = `Phase 2 running on ${file.name}…`;
    },
  });
  if (result.confirmed) await refresh();
  startPollingIfBusy();
}

// Layout (AutoCAD-tab) picker — shown when a DXF's geometry lives in more
// than one paper-space layout. Confirming pins the tab and re-runs layer
// discovery against it, so the flow chains straight into the layer picker.
async function promptLayoutSelection(file) {
  const result = await openLayoutModal({
    fileId: file.id,
    fileName: file.name,
    onConfirm: async (layout) => {
      $status.textContent = `Loading "${layout}" from ${file.name}…`;
    },
  });
  if (result.confirmed) await refresh();
  startPollingIfBusy();
}

async function editLayers(file) {
  // Manual re-open. If the file has no manifest (legacy), kick off
  // discovery first.
  const hasManifest = file.status !== "error";  // ready/preprocessing imply manifest exists
  const result = await openLayerModal({
    fileId: file.id,
    fileName: file.name,
    triggerDiscovery: !hasManifest || file.status === "preprocessing",
    onConfirm: async () => {
      $status.textContent = `Re-preprocessing ${file.name} with new layer set…`;
    },
  });
  if (result.confirmed) {
    await refresh();
    startPollingIfBusy();
  }
}

// ---- polling -------------------------------------------------------------
function startPollingIfBusy() {
  if (pollTimer) return;
  const tick = async () => {
    await refresh();
    // Step every active rule-check job. _stepRuleCheckJob() removes
    // entries from `ruleCheckJobs` once they reach done/error.
    const ruleJobProducts = Array.from(ruleCheckJobs.keys());
    await Promise.all(ruleJobProducts.map(pid => _stepRuleCheckJob(pid)));

    const fileBusy = products.some(p =>
      Object.values(p.files_by_role).some(f => f && (
        f.status === "preprocessing"
        || f.status === "discovering_layers"
        || f.status === "checking_rules"
      ))
    );
    const ruleBusy = ruleCheckJobs.size > 0;
    if (fileBusy || ruleBusy) {
      pollTimer = setTimeout(tick, 1500);
    } else {
      pollTimer = null;
      if ($status.textContent.startsWith("submitting") || $status.textContent.startsWith("Rule check on")) {
        // Leave the last rule-check status line in place.
      } else {
        $status.textContent = "idle";
      }
    }
  };
  pollTimer = setTimeout(tick, 1500);
}

// ---- developer mode wiring ----------------------------------------------
const $skipLayerPickWrap = document.getElementById("skip-layer-pick-wrap");
const $skipLayerPick = document.getElementById("skip-layer-pick");

function syncDevModeButton() {
  const on = getDevMode();
  $devModeToggle.setAttribute("aria-pressed", on ? "true" : "false");
  $devModeToggle.textContent = on ? "Developer Mode: ON" : "Developer Mode";
  devParams.syncToggleVisibility();
  // Skip-layer-pick checkbox is only present in the DOM when dev mode
  // is on. Persisted state is restored every time it becomes visible
  // so a dev who turned the box on, toggled dev mode off, then back
  // on, sees the box re-checked.
  $skipLayerPickWrap.hidden = !on;
  if (on) $skipLayerPick.checked = getSkipLayerPick();
}
$skipLayerPick.addEventListener("change", (e) => {
  setSkipLayerPick(e.target.checked);
});
$devModeToggle.addEventListener("click", () => {
  setDevMode(!getDevMode());
  syncDevModeButton();
  renderProducts();   // remount cards so dev-only buttons appear / disappear
});

// ---- dev parameter modal -------------------------------------------------
// DXF group only on the dashboard — matching params live on the viewer
// page next to where users actually iterate on matches. Re-preprocess
// is exposed here because it operates on the full file store, which is
// the dashboard's concern, not a single file's.
const devParams = mountDevParamsModal({
  toggleId: "dev-params-toggle",
  modalId: "dev-params-modal",
  bodyId: "dev-params-body",
  applyId: "dev-params-apply",
  resetId: "dev-params-reset",
  reprocessId: "dev-params-reprocess",
  moduleFilter: "dxf",
  statusEl: $status,
  onJobStart: pollReprocessJob,
});

async function pollReprocessJob(jobId) {
  $status.textContent = `Re-preprocessing all files (job ${jobId.slice(0, 8)}…)`;
  const tick = async () => {
    const r = await fetch(`/api/jobs/${jobId}`);
    if (!r.ok) { $status.textContent = `reprocess job lost: ${r.status}`; return; }
    const job = await r.json();
    const done = job.done ?? 0;
    const total = job.total ?? 0;
    $status.textContent = job.status === "done"
      ? `Re-preprocess done (${done}/${total}, ${job.skipped || 0} skipped, ${job.errors?.length || 0} errors)`
      : `Re-preprocessing ${done}/${total}…`;
    if (job.status !== "done") setTimeout(tick, 1500);
    else await refresh();
  };
  tick();
}

// ---- bootstrap -----------------------------------------------------------
(async () => {
  syncDevModeButton();
  await loadLibraries();
  await refresh();
  startPollingIfBusy();
})();
