// Dashboard: product cards with per-role DXF slots, scoped to the
// selected VERSION of each product. Rule check is version-scoped and
// only available once every uploaded file of that version has had its
// Match JSON saved. Libraries are version internals — never shown here.

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
const $newProductBtn = document.getElementById("new-product-btn");
const $modal = document.getElementById("product-modal");
const $newProductName = document.getElementById("new-product-name");
const $newProductVersionLabel = document.getElementById("new-product-version-label");
const $newProductCreate = document.getElementById("new-product-create");
const $fileInput = document.getElementById("file-input");
const $devModeToggle = document.getElementById("dev-mode-toggle");

let products = [];
let pollTimer = null;
let pendingSlot = null;   // when user clicks a slot or picks file: { versionId, role }
// version_id -> { jobId, name } for in-flight rule-check jobs. Used to
// disable the button and to re-enable it on done/error from the
// existing dashboard tick. Hydrated on every refresh from each
// version's `latest_rule_check_job` so a user who navigates away
// during a run still sees the result on return.
const ruleCheckJobs = new Map();

// product_id -> version_id the user picked in the card's version
// switcher. Absent (or stale) entries fall back to the latest version
// (last in the versions array). In-memory only — a reload snaps back
// to latest, which is the spec'd default.
const selectedVersions = new Map();

function selectedVersionOf(p) {
  const versions = p.versions || [];
  if (!versions.length) return null;
  const wanted = selectedVersions.get(p.id);
  return versions.find(v => v.id === wanted) ?? versions[versions.length - 1];
}

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
// JSON download and the per-version DRC bundle download so the filename
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
function fmtSignedAt(ts) {
  if (ts == null) return "—";
  try { return new Date(ts * 1000).toLocaleString(); }
  catch { return String(ts); }
}

// Every mutating call on a signed-off version returns HTTP 409 with
// detail {error: "version signed-off", signed_off_by, signed_off_at}.
// Surface that uniformly; returns true when the response WAS that 409
// (i.e., handled here), false otherwise.
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

// ---- modal -----------------------------------------------------------------
function openModal() {
  $newProductName.value = "";
  $newProductVersionLabel.value = "";
  $newProductVersionLabel.classList.remove("input-error");
  $modal.hidden = false;
  setTimeout(() => $newProductName.focus(), 0);
}
function closeModal() { $modal.hidden = true; }
$newProductBtn.addEventListener("click", openModal);
$modal.addEventListener("click", (e) => { if (e.target.matches("[data-close]")) closeModal(); });
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$modal.hidden) closeModal(); });
$newProductVersionLabel.addEventListener("input", () => {
  $newProductVersionLabel.classList.remove("input-error");
});

$newProductCreate.addEventListener("click", async () => {
  const name = $newProductName.value.trim();
  if (!name) { $newProductName.focus(); return; }
  // version_label is REQUIRED — block client-side and highlight the field.
  const versionLabel = $newProductVersionLabel.value.trim();
  if (!versionLabel) {
    $newProductVersionLabel.classList.add("input-error");
    $newProductVersionLabel.focus();
    return;
  }
  const res = await fetch("/api/products", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, version_label: versionLabel }),
  });
  if (!res.ok) {
    $status.textContent = `create failed: ${res.status}`;
    return;
  }
  closeModal();
  await refresh();
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

// For each product version, reconcile the in-memory `ruleCheckJobs` Map
// (and "seen" set) against the server-reported `latest_rule_check_job`.
// This is what lets the dashboard pick up a job kicked off in a prior
// browser session: while we were on the viewer, the worker finished
// and persisted the result; on return, we surface it (and auto-open
// the modal once) instead of leaving the user staring at a stale
// "Re-run Rule Check" button.
async function _syncRuleCheckJobsFromProducts() {
  if (!products.length) return;
  const completedThisSync = [];  // [{product, version, summary}]
  for (const p of products) {
    for (const v of (p.versions || [])) {
      const lj = v.latest_rule_check_job;
      if (!lj || !lj.job_id) continue;

      if (lj.status === "queued" || lj.status === "running") {
        // Server still has a live job for this version — make sure we're
        // tracking it so the next tick polls and the button shows
        // "Running…". No-op if we already started it from this tab.
        if (!ruleCheckJobs.has(v.id)) {
          ruleCheckJobs.set(v.id, { jobId: lj.job_id, name: `${p.name} / ${v.label}` });
          startPollingIfBusy();
        }
        continue;
      }

      if (lj.status === "done") {
        // Cleanup any stale tracking entry for this version (e.g. the
        // job we kicked off has just finished server-side).
        if (ruleCheckJobs.has(v.id)) ruleCheckJobs.delete(v.id);
        if (!seenRuleCheckJobs.has(lj.job_id)) {
          _markRuleCheckJobSeen(lj.job_id);
          completedThisSync.push({ product: p, version: v, summary: lj.result || {} });
        }
        continue;
      }

      if (lj.status === "error") {
        if (ruleCheckJobs.has(v.id)) ruleCheckJobs.delete(v.id);
        if (!seenRuleCheckJobs.has(lj.job_id)) {
          _markRuleCheckJobSeen(lj.job_id);
          $status.textContent =
            `Rule check on "${p.name} / ${v.label}" failed: ${lj.error || "(no detail)"}`;
        }
        continue;
      }
    }
  }

  // For any completed-while-away jobs, fetch the persisted result and
  // pop the modal once. Sequential to keep the modal stack sane.
  for (const { product, version, summary } of completedThisSync) {
    try {
      const r = await fetch(`/api/versions/${version.id}/rule-check`);
      if (!r.ok) continue;
      const data = await r.json();
      data.roles_covered = summary.roles_covered || [];
      $status.textContent =
        `Rule check on "${product.name} / ${version.label}": ` +
        `${data.pass_count}/${data.rule_count} pass ` +
        `(roles: ${data.roles_covered.join(", ")})`;
      showRuleResults(product, version, data);
    } catch (e) {
      console.error("failed to load persisted rule check", e);
    }
  }
}

function renderProducts() {
  $list.innerHTML = "";
  if (!products.length) {
    $empty.hidden = false;
    return;
  }
  $empty.hidden = true;
  // Flat list — no customer/library grouping (libraries are version
  // internals now).
  for (const p of products) $list.appendChild(productCard(p));
}

// ---- version bar ----------------------------------------------------------
// Per-card switcher: a <select> of version labels (latest is the default),
// the sign-off badge / buttons, and the "新增版本" (clone) action.
function versionBar(p, version) {
  const bar = document.createElement("div");
  bar.className = "version-bar";

  const label = document.createElement("span");
  label.className = "version-bar__label";
  label.textContent = "版本:";
  bar.appendChild(label);

  const select = document.createElement("select");
  select.className = "version-select";
  select.title = "切換此產品的版本(整張卡片會切到該版本的內容)";
  for (const v of p.versions) {
    const opt = document.createElement("option");
    opt.value = v.id;
    opt.textContent = v.signed_off_by ? `${v.label} 🔒` : v.label;
    if (version && v.id === version.id) opt.selected = true;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => {
    selectedVersions.set(p.id, select.value);
    renderProducts();
  });
  bar.appendChild(select);

  if (version?.signed_off_by) {
    const badge = document.createElement("span");
    badge.className = "signed-badge";
    badge.textContent =
      `已畫押 by ${version.signed_off_by} @ ${fmtSignedAt(version.signed_off_at)}`;
    badge.title = "此版本已凍結為唯讀;規則檢查結果仍可查看。";
    bar.appendChild(badge);
  }

  const spacer = document.createElement("span");
  spacer.className = "spacer";
  bar.appendChild(spacer);

  if (version) {
    if (version.signed_off_by) {
      // Admin affordance: unlock the version. Server-side admin check
      // lands with the auth change; the button is exposed per spec.
      const unsignBtn = document.createElement("button");
      unsignBtn.type = "button";
      unsignBtn.className = "version-action-btn";
      unsignBtn.textContent = "解除畫押";
      unsignBtn.title = "管理者操作:解除畫押,版本恢復可編輯";
      unsignBtn.addEventListener("click", () => unsignVersion(p, version));
      bar.appendChild(unsignBtn);
    } else {
      const signBtn = document.createElement("button");
      signBtn.type = "button";
      signBtn.className = "version-action-btn";
      signBtn.textContent = "畫押";
      signBtn.title = "凍結此版本(畫押後唯讀,僅管理者可解除)";
      signBtn.addEventListener("click", () => signOffVersion(p, version));
      bar.appendChild(signBtn);
    }
  }

  const newBtn = document.createElement("button");
  newBtn.type = "button";
  newBtn.className = "version-action-btn";
  newBtn.textContent = "新增版本";
  newBtn.title = "以目前選取的版本為基礎建立新版本(複製範本、比對設定與檔案綁定)";
  newBtn.addEventListener("click", () => createNewVersion(p, version));
  bar.appendChild(newBtn);

  return bar;
}

async function createNewVersion(p, sourceVersion) {
  const label = prompt(`新版本標籤(將複製 "${sourceVersion ? sourceVersion.label : "最新版"}" 的內容):`);
  if (label === null) return;
  const trimmed = label.trim();
  if (!trimmed) { $status.textContent = "版本標籤不可為空"; return; }
  const body = { label: trimmed };
  if (sourceVersion) body.clone_from = sourceVersion.id;
  const res = await fetch(`/api/products/${p.id}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 409) {
    alert(`版本標籤 "${trimmed}" 已存在於此產品,請換一個標籤。`);
    return;
  }
  if (!res.ok) {
    $status.textContent = `create version failed: ${res.status}`;
    return;
  }
  const created = await res.json();
  selectedVersions.set(p.id, created.id);
  $status.textContent = `已建立版本 "${created.label}"(複製自 ${sourceVersion ? sourceVersion.label : "最新版"})`;
  await refresh();
  startPollingIfBusy();  // cloned bindings re-preprocess in the background
}

async function signOffVersion(p, version) {
  if (!confirm(`確定要畫押 "${p.name} / ${version.label}" 嗎?\n畫押後此版本凍結為唯讀(上傳、比對、規則檢查皆停用)。`)) return;
  const res = await fetch(`/api/versions/${version.id}/sign-off`, { method: "POST" });
  if (!res.ok) {
    if (await handleSignedOff409(res)) { await refresh(); return; }
    $status.textContent = `sign-off failed: ${res.status}`;
    return;
  }
  $status.textContent = `已畫押 "${p.name} / ${version.label}"`;
  await refresh();
}

async function unsignVersion(p, version) {
  if (!confirm(`確定要解除 "${p.name} / ${version.label}" 的畫押嗎?(管理者操作)`)) return;
  const res = await fetch(`/api/versions/${version.id}/sign-off`, { method: "DELETE" });
  if (!res.ok) {
    $status.textContent = `unsign failed: ${res.status}`;
    return;
  }
  $status.textContent = `已解除畫押 "${p.name} / ${version.label}"`;
  await refresh();
}

function productCard(p) {
  const card = document.createElement("section");
  card.className = "product-card";
  card.dataset.productId = p.id;

  const version = selectedVersionOf(p);
  const signed = !!version?.signed_off_by;

  const header = document.createElement("header");
  header.innerHTML =
    `<span class="product-name">${escapeHtml(p.name)}</span>` +
    `<span class="spacer"></span>` +
    `<button class="product-delete" type="button" title="Delete this product">Delete</button>`;
  header.querySelector(".product-delete").addEventListener("click", () => deleteProduct(p));
  card.appendChild(header);

  card.appendChild(versionBar(p, version));

  if (!version) {
    const none = document.createElement("p");
    none.className = "empty";
    none.textContent = "此產品沒有任何版本(資料異常)";
    card.appendChild(none);
    return card;
  }

  const grid = document.createElement("div");
  grid.className = "slot-grid";
  for (const role of SINGLE_ROLES) grid.appendChild(slotCell(p, version, role));
  grid.appendChild(ringLidPairCell(p, version));
  card.appendChild(grid);

  const footer = document.createElement("div");
  footer.className = "product-footer";
  const prog = version.match_progress;
  footer.innerHTML =
    `<span class="match-progress">Match: <strong>${prog.saved}</strong>/${prog.total} saved</span>` +
    `<span class="spacer"></span>`;

  const rcBtn = document.createElement("button");
  rcBtn.type = "button";
  rcBtn.className = "rule-check-btn";
  const jobInFlight = ruleCheckJobs.has(version.id);
  rcBtn.disabled = !version.ready_for_rule_check || jobInFlight || signed;
  if (jobInFlight) {
    rcBtn.textContent = "Running…";
  } else {
    rcBtn.textContent = version.rule_check_available && version.ready_for_rule_check
      ? "Re-run Rule Check"
      : "Rule Check";
  }
  if (signed) {
    rcBtn.title = "版本已畫押(唯讀)— 結果仍可由 Check Result 查看";
  } else if (!version.ready_for_rule_check) {
    const remaining = prog.total === 0
      ? "upload at least one DXF first"
      : `${prog.total - prog.saved} file(s) still need Save Match`;
    rcBtn.title = remaining;
  } else if (jobInFlight) {
    rcBtn.title = "Rule check is running — see status bar.";
  }
  rcBtn.addEventListener("click", () => runRuleCheck(p, version));
  footer.appendChild(rcBtn);

  // "Check Result" re-opens the persisted rule-check result modal on
  // demand. The modal otherwise only auto-pops once (on job completion and
  // on the first dashboard re-entry after it), so this gives a standing
  // entry point whenever a result exists. Hidden while a job is in flight —
  // the in-flight run will auto-pop the fresh result on completion.
  // Stays available on signed versions: results remain viewable.
  if (version.rule_check_available && !jobInFlight) {
    const resBtn = document.createElement("button");
    resBtn.type = "button";
    resBtn.className = "rule-check-btn";
    resBtn.textContent = "Check Result";
    resBtn.title = "Show the latest rule-check result";
    resBtn.addEventListener("click", () => showRuleCheckResult(p, version));
    footer.appendChild(resBtn);
  }
  // Dev mode: Download All Match — the DRC handoff bundle (zip of
  // every role-attached DXF + per-file Match JSON + manifest.json).
  // Disabled (not hidden) when the version isn't ready_for_rule_check
  // so dev users see the affordance with an explanatory tooltip.
  // Bundle export is a read — allowed on signed versions too.
  if (getDevMode()) {
    const dlAll = document.createElement("button");
    dlAll.type = "button";
    dlAll.className = "rule-check-btn";
    dlAll.textContent = "Download All Match";
    dlAll.disabled = !version.ready_for_rule_check;
    if (!version.ready_for_rule_check) {
      const remaining = prog.total === 0
        ? "upload at least one DXF first"
        : `${prog.total - prog.saved} file(s) still need Save Match`;
      dlAll.title = remaining;
    } else {
      dlAll.title = "Download every DXF + Match JSON + manifest.json as a zip";
    }
    dlAll.addEventListener("click", () => downloadAllMatch(p, version));
    footer.appendChild(dlAll);

    // Dev mode: upload a hand-crafted RuleChecking JSON to simulate
    // an external rule-check result. Persists straight to
    // data/rule_check/{vid}.json after envelope validation; lets the
    // external team iterate on output shape without running their
    // pipeline. Mutates the version → blocked when signed.
    if (!signed) {
      const upBtn = document.createElement("button");
      upBtn.type = "button";
      upBtn.className = "rule-check-btn";
      upBtn.textContent = "📤 Upload Rule JSON";
      upBtn.title = "Upload a RuleChecking JSON to test the envelope and preview rendering";
      upBtn.addEventListener("click", () => uploadRuleJson(p, version));
      footer.appendChild(upBtn);
    }
  }
  card.appendChild(footer);

  return card;
}

// ---- developer-mode download handlers -----------------------------------
async function downloadMatchJson(file) {
  $status.textContent = `downloading match JSON for ${file.name}…`;
  try {
    const r = await fetch(`/api/files/${file.id}/match-json?version_id=${encodeURIComponent(file.version_id)}`);
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
async function uploadRuleJson(product, version) {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "application/json,.json";
  picker.addEventListener("change", async () => {
    const file = picker.files?.[0];
    if (!file) return;
    $status.textContent = `uploading "${file.name}" for "${product.name} / ${version.label}"…`;
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
      const r = await fetch(`/api/versions/${version.id}/rule-check/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!r.ok) {
        if (await handleSignedOff409(r)) return;
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
        `Uploaded for "${product.name} / ${version.label}": ` +
        `${summary.pass_count}/${summary.rule_count} pass ` +
        `(${summary.fail_count} fail)`;
      await refresh();  // rule_check_available flips, "Rule Check" → "Re-run"
    } catch (e) {
      $status.textContent = `upload failed: ${e.message}`;
    }
  });
  picker.click();
}

async function downloadAllMatch(product, version) {
  $status.textContent = `building DRC bundle for "${product.name} / ${version.label}"…`;
  try {
    const r = await fetch(`/api/versions/${version.id}/drc-bundle`);
    if (!r.ok) {
      let msg = `bundle download failed: ${r.status}`;
      try {
        const body = await r.json();
        if (typeof body?.detail === "string") msg = `bundle download failed: ${body.detail}`;
      } catch { /* not JSON */ }
      $status.textContent = msg;
      return;
    }
    downloadAsFile(await r.blob(), `drc-bundle-${version.id}.zip`);
    $status.textContent = `downloaded drc-bundle-${version.id}.zip`;
  } catch (e) {
    $status.textContent = `bundle download failed: ${e.message}`;
  }
}

function slotCell(product, version, role, opts = {}) {
  const { disabledReason = null } = opts;
  const signed = !!version.signed_off_by;
  const cell = document.createElement("div");
  cell.className = "slot";
  cell.dataset.role = role;
  cell.dataset.versionId = version.id;

  const allFiles = (version.files_by_role_all && version.files_by_role_all[role]) || [];
  cell.innerHTML = `<span class="role-label">${role}</span>`;

  if (!allFiles.length) {
    cell.classList.add("empty");
    if (disabledReason || signed) {
      // Disabled either because the opposite RING/LID half holds a file,
      // or because the version is signed off (uploads frozen).
      cell.classList.add("disabled");
      cell.title = disabledReason || "版本已畫押(唯讀)— 無法上傳";
      cell.innerHTML += `<span class="file-name">${signed && !disabledReason ? "已畫押(唯讀)" : "unavailable"}</span>`;
      return cell;
    }
    cell.innerHTML += `<span class="file-name">+ Drop or click</span>`;
    cell.addEventListener("click", () => pickFile(version.id, role));
    wireDragAndDrop(cell, version.id, role);
    return cell;
  }

  if (allFiles.length === 1) {
    // Common case — single-file presentation matches the pre-multi-DXF UI
    // (file name + status + Open/Layers/Replace) and adds a small
    // "+ Add file" affordance so the user can grow into multi-file mode
    // without having to delete-and-re-upload.
    renderSingleFileSlot(cell, version, role, allFiles[0]);
    if (!signed) cell.appendChild(buildAddButton(version, role));
    return cell;
  }

  // 2+ files — stack them, each with its own compact action row.
  const filesContainer = document.createElement("div");
  filesContainer.className = "slot-files";
  for (const f of allFiles) {
    filesContainer.appendChild(slotFileRow(version, role, f, /*compact=*/true));
  }
  cell.appendChild(filesContainer);
  if (!signed) cell.appendChild(buildAddButton(version, role));
  return cell;
}

// The 4th grid cell is one container holding two adjacent `slotCell`
// halves — RING on the left, LID on the right — each rendered as an
// independent single-role slot. Both halves may be filled.
function ringLidPairCell(product, version) {
  const cell = document.createElement("div");
  cell.className = "slot-pair";
  cell.appendChild(slotCell(product, version, "RING"));
  cell.appendChild(slotCell(product, version, "LID"));
  return cell;
}

function buildAddButton(version, role) {
  const btn = document.createElement("button");
  btn.className = "replace-btn slot-add";
  btn.type = "button";
  btn.textContent = "+ Add file";
  btn.title = "Upload another DXF into this role";
  btn.addEventListener("click", () => pickFile(version.id, role));
  return btn;
}

function renderSingleFileSlot(cell, version, role, f) {
  // Inlined "old" rendering: file-name + status + actions directly on
  // the cell, no per-row wrapping.
  const { statusColor, statusLabel, matchBadge } = fileStatusBits(f);
  cell.innerHTML +=
    `<span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>` +
    `<span class="slot-status">${matchBadge} · <span style="color:${statusColor}">${escapeHtml(statusLabel)}</span></span>`;
  appendUnitScaleAnnotation(cell.querySelector(".slot-status"), f);
  cell.appendChild(buildFileActions(version, role, f, /*compact=*/false));
}

function slotFileRow(version, role, f, compact) {
  const row = document.createElement("div");
  row.className = "slot-file";
  row.dataset.fileId = f.id;
  const { statusColor, statusLabel, matchBadge } = fileStatusBits(f);
  row.innerHTML =
    `<span class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>` +
    `<span class="slot-status">${matchBadge} · <span style="color:${statusColor}">${escapeHtml(statusLabel)}</span></span>`;
  appendUnitScaleAnnotation(row.querySelector(".slot-status"), f);
  row.appendChild(buildFileActions(version, role, f, compact));
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
      ? `⚠ 單位 ${f.unit_label}(非 mm)`
      : `單位 ${f.unit_label}`;
    badge.title = warnNonMm
      ? `來源 $INSUNITS = ${f.unit_label},非 mm。目前直接以 mm 處理;` +
        `若實際單位不同,請在檢視器手動指定單位以換算。`
      : `單位:${f.unit_label}`;
    parent.appendChild(badge);
  }

  // 3) adjusted → before/after pill.
  if (adjusted && f.applied_scale_label) {
    const pill = document.createElement("span");
    pill.className = "rescaled-pill";
    const override = f.user_unit_override ? "(手動指定)" : "";
    const post = bboxDiagonal(f.bbox);
    const pre = post != null && f.applied_scale ? post / f.applied_scale : null;
    const beforeAfter =
      pre != null
        ? `:對角線 ${fmtLen(pre)} ${f.unit_label} → ${fmtLen(post)} mm`
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

function buildFileActions(version, role, f, compact) {
  const signed = !!version.signed_off_by;
  const actions = document.createElement("div");
  actions.className = "slot-actions";
  if (f.status === "awaiting_layout" && !signed) {
    const pickBtn = document.createElement("button");
    pickBtn.className = "primary action-btn";
    pickBtn.type = "button";
    pickBtn.textContent = "Pick view";
    pickBtn.title = "This DXF's geometry is in AutoCAD layout tabs — pick which one to load";
    pickBtn.addEventListener("click", () => promptLayoutSelection(f));
    actions.appendChild(pickBtn);
  } else if (f.status === "awaiting_layers" && !signed) {
    const pickBtn = document.createElement("button");
    pickBtn.className = "primary action-btn";
    pickBtn.type = "button";
    pickBtn.textContent = "Pick layers";
    pickBtn.addEventListener("click", () => promptLayerSelection(f));
    actions.appendChild(pickBtn);
  } else if (f.status === "ready_to_match") {
    actions.innerHTML =
      `<a class="open-link" href="/viewer/${f.id}?version_id=${encodeURIComponent(version.id)}">Open →</a>`;
  }
  // Editing affordances (re-pick view/layers, replace, delete) mutate the
  // version — render none of them when signed off. Viewing stays.
  if (signed) return actions;

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
  replace.addEventListener("click", () => pickFile(version.id, role, f.id));
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
  del.addEventListener("click", () => deleteVersionFile(version, role, f));
  actions.appendChild(del);
  return actions;
}

function wireDragAndDrop(cell, versionId, role) {
  cell.addEventListener("dragover", (e) => { e.preventDefault(); cell.classList.add("dragover"); });
  cell.addEventListener("dragleave", () => cell.classList.remove("dragover"));
  cell.addEventListener("drop", (e) => {
    e.preventDefault();
    cell.classList.remove("dragover");
    const file = [...(e.dataTransfer?.files ?? [])]
      .find(f => f.name.toLowerCase().endsWith(".dxf"));
    if (file) uploadFile(versionId, role, file);
  });
}

// `replaceFileId` is the id of the file this upload should evict before
// landing the new one (the "Replace" button path). Omit for additive uploads.
function pickFile(versionId, role, replaceFileId = null) {
  pendingSlot = { versionId, role, replaceFileId };
  $fileInput.click();
}
$fileInput.addEventListener("change", () => {
  const f = $fileInput.files?.[0];
  $fileInput.value = "";
  if (f && pendingSlot) {
    uploadFile(
      pendingSlot.versionId,
      pendingSlot.role,
      f,
      pendingSlot.replaceFileId || null,
    );
  }
});

async function uploadFile(versionId, role, file, replaceFileId = null) {
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
  const res = await fetch(`/api/versions/${versionId}/files`, { method: "POST", body: fd });
  if (!res.ok) {
    if (await handleSignedOff409(res)) { await refresh(); return; }
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

async function deleteVersionFile(version, role, file) {
  if (!confirm(`Remove "${file.name}" from ${role}?`)) return;
  const res = await fetch(`/api/versions/${version.id}/files/${file.id}`, { method: "DELETE" });
  if (!res.ok) {
    if (await handleSignedOff409(res)) { await refresh(); return; }
    $status.textContent = `remove failed: ${res.status}`;
    return;
  }
  $status.textContent = `removed ${file.name} from ${role}`;
  await refresh();
}

async function deleteProduct(p) {
  if (!confirm(`Delete product "${p.name}" — every version, file binding, and result with it?`)) return;
  const res = await fetch(`/api/products/${p.id}`, { method: "DELETE" });
  if (!res.ok) {
    $status.textContent = `delete failed: ${res.status}`;
    return;
  }
  selectedVersions.delete(p.id);
  await refresh();
}

async function runRuleCheck(p, version) {
  if (ruleCheckJobs.has(version.id)) return;  // already in flight; button should be disabled
  $status.textContent = `submitting rule check on "${p.name} / ${version.label}"…`;
  const res = await fetch(`/api/versions/${version.id}/rule-check`, { method: "POST" });
  if (!res.ok) {
    if (await handleSignedOff409(res)) { await refresh(); return; }
    const err = await res.text();
    $status.textContent = `rule-check submit failed: ${res.status}`;
    console.error(err);
    return;
  }
  const { job_id: jobId } = await res.json();
  ruleCheckJobs.set(version.id, { jobId, name: `${p.name} / ${version.label}` });
  $status.textContent =
    `Rule check on "${p.name} / ${version.label}" running (job ${jobId.slice(0, 8)}…)`;
  renderProducts();      // reflect "Running…" on the button immediately
  startPollingIfBusy();  // tick handler watches `ruleCheckJobs` too
}

// Locate (product, version) for a version id in the freshly-refreshed
// `products` snapshot. Returns {product, version} or nulls.
function findVersion(versionId) {
  for (const p of products) {
    const v = (p.versions || []).find(x => x.id === versionId);
    if (v) return { product: p, version: v };
  }
  return { product: null, version: null };
}

// Poll a single rule-check job; called from the dashboard tick. Returns
// `true` while the job is still in flight so the tick keeps running.
async function _stepRuleCheckJob(versionId) {
  const entry = ruleCheckJobs.get(versionId);
  if (!entry) return false;
  let job;
  try {
    const r = await fetch(`/api/jobs/${entry.jobId}`);
    if (!r.ok) {
      $status.textContent = `rule-check job lost: ${r.status}`;
      ruleCheckJobs.delete(versionId);
      return false;
    }
    job = await r.json();
  } catch (e) {
    $status.textContent = `rule-check poll error: ${e}`;
    return true;  // transient — try again next tick
  }
  if (job.status === "done") {
    ruleCheckJobs.delete(versionId);
    _markRuleCheckJobSeen(entry.jobId);
    const summary = job.result || {};
    $status.textContent =
      `Rule check on "${entry.name}": ` +
      `${summary.pass_count}/${summary.rule_count} pass ` +
      `(roles: ${(summary.roles_covered || []).join(", ")})`;
    // Refresh products (so rule_check_available flips) and fetch the
    // persisted result for the modal.
    await refresh();
    const { product, version } = findVersion(versionId);
    if (product && version) {
      try {
        const r = await fetch(`/api/versions/${versionId}/rule-check`);
        if (r.ok) {
          const data = await r.json();
          // The persisted GET returns `results / rule_count / pass_count /
          // fail_count` but not roles_covered; merge the job summary in
          // so `showRuleResults` can render the roles line.
          data.roles_covered = summary.roles_covered || [];
          showRuleResults(product, version, data);
        }
      } catch (e) {
        console.error("failed to load persisted rule check", e);
      }
    }
    return false;
  }
  if (job.status === "error") {
    ruleCheckJobs.delete(versionId);
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

// Re-open the persisted rule-check result modal for a version on demand
// (the "Check Result" button). Unlike the auto-pop path there's no live job
// summary here, so roles_covered comes from the version's latest rule-check
// job result when available (null after a server restart → empty list, which
// showRuleResults renders gracefully).
async function showRuleCheckResult(p, version) {
  $status.textContent = `loading rule-check result for "${p.name} / ${version.label}"…`;
  try {
    const r = await fetch(`/api/versions/${version.id}/rule-check`);
    if (!r.ok) {
      $status.textContent = `no rule-check result for "${p.name} / ${version.label}" (${r.status})`;
      return;
    }
    const data = await r.json();
    data.roles_covered = version.latest_rule_check_job?.result?.roles_covered || [];
    showRuleResults(p, version, data);
    $status.textContent = "";
  } catch (e) {
    $status.textContent = `failed to load rule-check result: ${e}`;
    console.error("showRuleCheckResult", e);
  }
}

function showRuleResults(product, version, data) {
  $ruleResultsTitle.textContent = `Rule Check — ${product.name} / ${version.label}`;
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
        const siblings = version.files_by_role_all?.[sub.part] ?? [];
        const file = (sub.file_id && siblings.find(f => f.id === sub.file_id))
                  || version.files_by_role[sub.part];
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
          trailing = `<a class="view-link" href="/viewer/${file.id}?version_id=${encodeURIComponent(version.id)}&rule=${encodeURIComponent(name)}&idx=${idx}">View in ${escapeHtml(sub.part)} →</a>`;
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
// only way to open the modal. All file-centric endpoints are version
// scoped now — `f.version_id` rides along into the modal's fetches.
async function promptLayerSelection(file) {
  const result = await openLayerModal({
    fileId: file.id,
    versionId: file.version_id,
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
    versionId: file.version_id,
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
    versionId: file.version_id,
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
    const ruleJobVersions = Array.from(ruleCheckJobs.keys());
    await Promise.all(ruleJobVersions.map(vid => _stepRuleCheckJob(vid)));

    // A file in ANY version of any product keeps the poll alive — a
    // freshly-cloned version re-preprocesses its bindings even though
    // it may not be the selected one.
    const fileBusy = products.some(p =>
      (p.versions || []).some(v =>
        Object.values(v.files_by_role).some(f => f && (
          f.status === "preprocessing"
          || f.status === "discovering_layers"
          || f.status === "checking_rules"
        ))
      )
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
  await refresh();
  startPollingIfBusy();
})();
