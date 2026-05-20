// Dashboard: product cards with per-role DXF slots. Rule check is
// product-scoped and only available once every uploaded file has had its
// Match JSON saved.

import { openLayerModal } from "./layer_modal.js";
import { mountDevParamsModal } from "./dev_params.js";

// SBT/BD/POD always render as single-role slots. The 4th grid cell is
// a split pair: RING on the left, LID on the right (mutually exclusive
// — uploading to one disables the other; see the `viewer-ui` /
// `product-files` specs for the full rule).
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
  renderProducts();
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
  rcBtn.disabled = !p.ready_for_rule_check;
  rcBtn.textContent = p.rule_check_available && p.ready_for_rule_check
    ? "Re-run Rule Check"
    : "Rule Check";
  if (!p.ready_for_rule_check) {
    const remaining = prog.total === 0
      ? "upload at least one DXF first"
      : `${prog.total - prog.saved} file(s) still need Save Match`;
    rcBtn.title = remaining;
  }
  rcBtn.addEventListener("click", () => runRuleCheck(p));
  footer.appendChild(rcBtn);
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
// halves — RING on the left, LID on the right. When one half holds
// ≥1 file, the other half renders as `slot.empty.disabled` (no
// click, no drag/drop, `title` names a conflicting file id) so the
// RING-XOR-LID server-side rule is mirrored in the UI.
function ringLidPairCell(product) {
  const ring = (product.files_by_role_all && product.files_by_role_all["RING"]) || [];
  const lid  = (product.files_by_role_all && product.files_by_role_all["LID"])  || [];
  const cell = document.createElement("div");
  cell.className = "slot-pair";

  const ringDisabled = ring.length === 0 && lid.length > 0
    ? `LID file ${lid[0].id} already locked this product into a LID configuration. Remove it to upload a RING.`
    : null;
  const lidDisabled  = lid.length === 0 && ring.length > 0
    ? `RING file ${ring[0].id} already locked this product into a RING configuration. Remove it to upload a LID.`
    : null;

  cell.appendChild(slotCell(product, "RING", { disabledReason: ringDisabled }));
  cell.appendChild(slotCell(product, "LID",  { disabledReason: lidDisabled }));
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
  if (f.unit_scale_warning) {
    const badge = document.createElement("span");
    badge.className = "warn-badge";
    badge.textContent = "⚠ unit";
    badge.title = f.unit_scale_warning_detail || "";
    cell.querySelector(".slot-status").appendChild(badge);
  }
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
  if (f.unit_scale_warning) {
    const badge = document.createElement("span");
    badge.className = "warn-badge";
    badge.textContent = "⚠ unit";
    badge.title = f.unit_scale_warning_detail || "";
    row.querySelector(".slot-status").appendChild(badge);
  }
  row.appendChild(buildFileActions(product, role, f, compact));
  return row;
}

function fileStatusBits(f) {
  const statusColor =
    f.status === "ready_to_match"     ? "#69f0ae" :
    f.status === "preprocessing"      ? "#ffb84d" :
    f.status === "discovering_layers" ? "#ffb84d" :
    f.status === "awaiting_layers"    ? "#ffd54f" :
    f.status === "error"              ? "#ff5252" : "#9aa5b1";
  const statusLabel =
    f.status === "discovering_layers" ? "scanning layers…" :
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
  if (f.status === "awaiting_layers") {
    const pickBtn = document.createElement("button");
    pickBtn.className = "primary action-btn";
    pickBtn.type = "button";
    pickBtn.textContent = "Pick layers";
    pickBtn.addEventListener("click", () => promptLayerSelection(f));
    actions.appendChild(pickBtn);
  } else if (f.status === "ready_to_match") {
    actions.innerHTML = `<a class="open-link" href="/viewer/${f.id}">Open →</a>`;
  }
  if (f.status !== "discovering_layers" && f.status !== "error") {
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
  $status.textContent = `running rule check on "${p.name}"…`;
  const res = await fetch(`/api/products/${p.id}/rule-check`, { method: "POST" });
  if (!res.ok) {
    const err = await res.text();
    $status.textContent = `rule-check failed: ${res.status}`;
    console.error(err);
    return;
  }
  const data = await res.json();
  $status.textContent =
    `Rule check on "${p.name}": ${data.pass_count}/${data.rule_count} pass ` +
    `(roles: ${data.roles_covered.join(", ")})`;
  await refresh();
  showRuleResults(p, data);
}

const $ruleResultsModal = document.getElementById("rule-results-modal");
const $ruleResultsTitle = document.getElementById("rule-results-title");
const $ruleResultsSummary = document.getElementById("rule-results-summary");
const $ruleResultsBody = document.getElementById("rule-results-body");

$ruleResultsModal.addEventListener("click", (e) => {
  if (e.target.matches("[data-close]")) $ruleResultsModal.hidden = true;
});

function showRuleResults(product, data) {
  $ruleResultsTitle.textContent = `Rule Check — ${product.name}`;
  $ruleResultsSummary.textContent =
    `${data.pass_count}/${data.rule_count} pass · roles: ${data.roles_covered.join(", ")}`;
  $ruleResultsBody.innerHTML = "";

  for (const [name, rule] of Object.entries(data.results)) {
    const card = document.createElement("section");
    card.className = "rule-result-card";
    const status = rule.pass ? "✓" : "✗";
    const statusClass = rule.pass ? "pass" : "fail";
    card.innerHTML =
      `<header>` +
        `<span class="status ${statusClass}">${status}</span>` +
        `<span class="name">${escapeHtml(name)}</span>` +
        `<span class="text">${escapeHtml(rule.text || "")}</span>` +
      `</header>` +
      `<ol class="subrules"></ol>`;
    const subList = card.querySelector(".subrules");
    const subs = rule.rules || [];
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
        const li = document.createElement("li");
        const viewBtn = file
          ? `<a class="view-link" href="/viewer/${file.id}?rule=${encodeURIComponent(name)}&idx=${idx}">View in ${sub.part} →</a>`
          : `<span class="no-file">${sub.part} not uploaded</span>`;
        li.innerHTML =
          `<span class="part">${escapeHtml(sub.part)}</span>` +
          `<span class="text">${escapeHtml(sub.text || "")}</span>` +
          viewBtn;
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
    const busy = products.some(p =>
      Object.values(p.files_by_role).some(f => f && (
        f.status === "preprocessing"
        || f.status === "discovering_layers"
        || f.status === "checking_rules"
      ))
    );
    if (busy) {
      pollTimer = setTimeout(tick, 1500);
    } else {
      pollTimer = null;
      $status.textContent = "idle";
    }
  };
  pollTimer = setTimeout(tick, 1500);
}

// ---- developer mode wiring ----------------------------------------------
function syncDevModeButton() {
  const on = getDevMode();
  $devModeToggle.setAttribute("aria-pressed", on ? "true" : "false");
  $devModeToggle.textContent = on ? "Developer Mode: ON" : "Developer Mode";
  devParams.syncToggleVisibility();
}
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
