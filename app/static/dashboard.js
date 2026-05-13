// Dashboard: drop zone for multi-upload + polling file list.

const $zone = document.getElementById("upload-zone");
const $input = document.getElementById("file-input");
const $pick = document.getElementById("pick-files-btn");
const $tbody = document.querySelector("#file-list tbody");
const $empty = document.getElementById("empty-msg");
const $status = document.getElementById("status");
const $librarySelect = document.getElementById("library-select");
const $newLibraryBtn = document.getElementById("new-library-btn");
const $libraryInfo = document.getElementById("library-info");
const $uploadTargetLib = document.getElementById("upload-target-lib");

let pollTimer = null;
let libraries = [];          // [{id, name, template_count, class_count}, ...]
const LIB_STORAGE_KEY = "smdr2.dashboard.selectedLibrary";

// ---- Drag & drop ---------------------------------------------------------
$zone.addEventListener("dragover", (e) => {
  e.preventDefault();
  $zone.classList.add("dragover");
});
$zone.addEventListener("dragleave", () => $zone.classList.remove("dragover"));
$zone.addEventListener("drop", (e) => {
  e.preventDefault();
  $zone.classList.remove("dragover");
  const files = [...(e.dataTransfer?.files ?? [])].filter(f => f.name.toLowerCase().endsWith(".dxf"));
  if (files.length) upload(files);
});

$pick.addEventListener("click", () => $input.click());
$input.addEventListener("change", () => {
  const files = [...$input.files];
  if (files.length) upload(files);
  $input.value = "";
});

// ---- Upload --------------------------------------------------------------
async function upload(files) {
  const libId = currentLibraryId();
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("library_id", libId);
  $status.textContent = `uploading ${files.length} file${files.length === 1 ? "" : "s"} → ${libraryName(libId)}…`;
  try {
    const res = await fetch("/api/files", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.text();
      $status.textContent = `upload failed: ${res.status}`;
      console.error(err);
      return;
    }
    const data = await res.json();
    $status.textContent = `uploaded ${data.files.length} file${data.files.length === 1 ? "" : "s"} → ${libraryName(libId)}`;
    await refresh();
    startPollingIfBusy();
  } catch (e) {
    console.error(e);
    $status.textContent = `upload error: ${e.message}`;
  }
}

// ---- File list -----------------------------------------------------------
async function refresh() {
  const res = await fetch("/api/files");
  if (!res.ok) return;
  const data = await res.json();
  renderTable(data.files);
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleString();
}

function renderTable(files) {
  $tbody.innerHTML = "";
  if (!files.length) {
    $empty.hidden = false;
    return;
  }
  $empty.hidden = true;
  for (const f of files) {
    const tr = document.createElement("tr");
    const canOpen = (f.status === "ready_to_match"
                     || f.status === "checking_rules"
                     || f.status === "report");
    const link = canOpen
      ? `<a class="open-link" href="/viewer/${f.id}">Open →</a>`
      : `<span style="color:#5d8aa8">—</span>`;
    const isBusy = f.status === "preprocessing" || f.status === "checking_rules";
    const libOptions = libraries.map(l =>
      `<option value="${l.id}" ${l.id === f.library_id ? "selected" : ""}>${escapeHtml(l.name)}</option>`
    ).join("");
    tr.innerHTML = `
      <td>${escapeHtml(f.name)}</td>
      <td class="numeric">${fmtSize(f.size)}</td>
      <td><select class="row-library-select" data-file-id="${f.id}" ${isBusy ? "disabled" : ""}>${libOptions}</select></td>
      <td><span class="status-pill status-${f.status}">${f.status}</span></td>
      <td class="numeric">${f.primitive_count != null ? f.primitive_count.toLocaleString() : "—"}</td>
      <td title="${new Date(f.uploaded_at * 1000).toLocaleString()}">${fmtTime(f.uploaded_at)}</td>
      <td>${link}</td>
    `;
    if (f.status === "error" && f.error) {
      const errRow = document.createElement("tr");
      errRow.innerHTML = `<td colspan="7" style="color:#ff5252; font-size:0.78rem; padding-top:0">${escapeHtml(f.error.split("\n")[0])}</td>`;
      $tbody.appendChild(tr);
      $tbody.appendChild(errRow);
    } else {
      $tbody.appendChild(tr);
    }
  }
  // Wire up per-row library selectors.
  $tbody.querySelectorAll(".row-library-select").forEach(sel => {
    sel.addEventListener("change", () => onRowLibraryChange(sel));
  });
}

async function onRowLibraryChange(sel) {
  const fileId = sel.dataset.fileId;
  const newLibId = sel.value;
  const newLibName = libraryName(newLibId);
  sel.disabled = true;
  $status.textContent = `moving file to "${newLibName}" — re-processing…`;
  try {
    const res = await fetch(`/api/files/${fileId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ library_id: newLibId }),
    });
    if (!res.ok) {
      const err = await res.text();
      console.error("patch failed:", err);
      $status.textContent = `move failed: ${res.status}`;
      return;
    }
    await refresh();
    startPollingIfBusy();
  } catch (e) {
    console.error(e);
    $status.textContent = `move error: ${e.message}`;
  } finally {
    sel.disabled = false;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---- Polling -------------------------------------------------------------
async function startPollingIfBusy() {
  if (pollTimer) return;
  const tick = async () => {
    const res = await fetch("/api/files");
    if (!res.ok) return;
    const data = await res.json();
    renderTable(data.files);
    const busy = data.files.some(f =>
      f.status === "preprocessing" || f.status === "checking_rules"
      || f.status === "queued" || f.status === "parsing"  /* legacy */
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

// ---- Libraries -----------------------------------------------------------
function currentLibraryId() {
  return $librarySelect.value || "default";
}

function libraryName(id) {
  const lib = libraries.find(l => l.id === id);
  return lib ? lib.name : (id || "—");
}

function setSelectedLibrary(id) {
  $librarySelect.value = id;
  sessionStorage.setItem(LIB_STORAGE_KEY, id);
  updateLibraryInfo();
}

function updateLibraryInfo() {
  const id = currentLibraryId();
  const lib = libraries.find(l => l.id === id);
  if (!lib) {
    $libraryInfo.textContent = "";
    $uploadTargetLib.textContent = "";
    return;
  }
  $libraryInfo.textContent =
    `${lib.template_count} template${lib.template_count === 1 ? "" : "s"} · ` +
    `${lib.class_count} class${lib.class_count === 1 ? "" : "es"}`;
  $uploadTargetLib.textContent = `→ uploads go to "${lib.name}"`;
}

async function loadLibraries() {
  const res = await fetch("/api/libraries");
  if (!res.ok) return;
  const data = await res.json();
  libraries = data.libraries;
  const previous = sessionStorage.getItem(LIB_STORAGE_KEY)
                 || $librarySelect.value
                 || data.default_id;
  $librarySelect.innerHTML = "";
  for (const lib of libraries) {
    const opt = document.createElement("option");
    opt.value = lib.id;
    opt.textContent = lib.name;
    $librarySelect.appendChild(opt);
  }
  // Restore selection (or fall back to default).
  if (libraries.some(l => l.id === previous)) {
    $librarySelect.value = previous;
  } else {
    $librarySelect.value = data.default_id;
  }
  updateLibraryInfo();
}

$librarySelect.addEventListener("change", () => {
  sessionStorage.setItem(LIB_STORAGE_KEY, currentLibraryId());
  updateLibraryInfo();
});

$newLibraryBtn.addEventListener("click", async () => {
  const name = prompt("New library name:");
  if (!name || !name.trim()) return;
  const res = await fetch("/api/libraries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  if (!res.ok) {
    $status.textContent = `create library failed: ${res.status}`;
    return;
  }
  const data = await res.json();
  await loadLibraries();
  setSelectedLibrary(data.id);
  $status.textContent = `created library "${data.name}"`;
});

// ---- Bootstrap -----------------------------------------------------------
(async () => {
  await loadLibraries();
  await refresh();
  startPollingIfBusy();
})();
