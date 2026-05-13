// Dashboard: product cards with per-role DXF slots. Rule check is
// product-scoped and only available once every uploaded file has had its
// Match JSON saved.

const ROLES = ["SBT", "BD", "POD", "RING"];

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

let libraries = [];
let products = [];
let pollTimer = null;
let pendingSlot = null;   // when user clicks a slot or picks file: { productId, role }

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

function renderProducts() {
  $list.innerHTML = "";
  if (!products.length) {
    $empty.hidden = false;
    return;
  }
  $empty.hidden = true;
  for (const p of products) $list.appendChild(productCard(p));
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
  for (const role of ROLES) grid.appendChild(slotCell(p, role));
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
  card.appendChild(footer);

  return card;
}

function slotCell(product, role) {
  const cell = document.createElement("div");
  cell.className = "slot";
  cell.dataset.role = role;
  cell.dataset.productId = product.id;

  const f = product.files_by_role[role];
  cell.innerHTML = `<span class="role-label">${role}</span>`;

  if (!f) {
    cell.classList.add("empty");
    cell.innerHTML += `<span class="file-name">+ Drop or click</span>`;
    cell.addEventListener("click", () => pickFile(product.id, role));
    wireDragAndDrop(cell, product.id, role);
    return cell;
  }

  const statusColor =
    f.status === "ready_to_match" ? "#69f0ae" :
    f.status === "preprocessing" ? "#ffb84d" :
    f.status === "error"         ? "#ff5252" : "#9aa5b1";
  const matchBadge = f.match_saved
    ? `<span style="color:#69f0ae;font-size:0.78rem;">✓ matched</span>`
    : `<span style="color:#9aa5b1;font-size:0.78rem;">not matched</span>`;

  cell.innerHTML +=
    `<span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>` +
    `<span class="slot-status">${matchBadge} · <span style="color:${statusColor}">${f.status}</span></span>`;

  const actions = document.createElement("div");
  actions.className = "slot-actions";
  if (f.status === "ready_to_match") {
    actions.innerHTML = `<a class="open-link" href="/viewer/${f.id}">Open →</a>`;
  }
  const replace = document.createElement("button");
  replace.className = "replace-btn";
  replace.type = "button";
  replace.textContent = "Replace";
  replace.addEventListener("click", () => pickFile(product.id, role));
  actions.appendChild(replace);
  cell.appendChild(actions);

  return cell;
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

function pickFile(productId, role) {
  pendingSlot = { productId, role };
  $fileInput.click();
}
$fileInput.addEventListener("change", () => {
  const f = $fileInput.files?.[0];
  $fileInput.value = "";
  if (f && pendingSlot) {
    uploadFile(pendingSlot.productId, pendingSlot.role, f);
  }
});

async function uploadFile(productId, role, file) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("dxf_role", role);
  $status.textContent = `uploading ${file.name} → ${role}…`;
  const res = await fetch(`/api/products/${productId}/files`, { method: "POST", body: fd });
  if (!res.ok) {
    $status.textContent = `upload failed: ${res.status}`;
    return;
  }
  $status.textContent = `uploaded ${file.name} → ${role}`;
  await refresh();
  startPollingIfBusy();
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
        const file = product.files_by_role[sub.part];
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

// ---- polling -------------------------------------------------------------
function startPollingIfBusy() {
  if (pollTimer) return;
  const tick = async () => {
    await refresh();
    const busy = products.some(p =>
      Object.values(p.files_by_role).some(f => f && (f.status === "preprocessing" || f.status === "checking_rules"))
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

// ---- bootstrap -----------------------------------------------------------
(async () => {
  await loadLibraries();
  await refresh();
  startPollingIfBusy();
})();
