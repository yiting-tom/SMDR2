// Dev-mode parameter modal — shared by dashboard (DXF group) and
// viewer (Matching group). Both pages drive the same
// `/api/dev/settings` + `/api/dev/reprocess-all` endpoints; this module
// owns the form rendering, validation echo, localStorage mirror and
// the reset/apply/reprocess wiring.
//
// Reset is scoped per-modal: it POSTs every visible field's default,
// which is enough to revert the slice without touching the other
// module's overrides. The `{reset: true}` server affordance is left
// alone for callers that want a wipe-all (CLI, ad-hoc curl).

const DEV_MODE_KEY = "smdr2.dashboard.devMode";
const DEV_OVERRIDES_KEY = "smdr2.dashboard.devOverrides";

function isDevMode() { return localStorage.getItem(DEV_MODE_KEY) === "1"; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function moduleTitle(m) {
  return m === "matching" ? "Matching" : m === "dxf" ? "DXF" : m;
}

/**
 * Wire up a gear button + modal pair to the dev-overrides endpoints.
 *
 * opts:
 *   toggleId, modalId, bodyId, applyId, resetId
 *   reprocessId   — when set, exposes a "Re-preprocess all files" action.
 *                   Re-preprocess is a global server operation regardless
 *                   of which modal triggers it.
 *   moduleFilter  — "matching" | "dxf"; only rows whose `module` matches
 *                   are rendered + sent to the server on Apply/Reset.
 *   statusEl      — optional DOM node whose textContent receives short
 *                   status updates.
 *   onJobStart    — optional fn(jobId) called after reprocess kick-off.
 */
export function mountDevParamsModal(opts) {
  const $toggle = document.getElementById(opts.toggleId);
  const $modal = document.getElementById(opts.modalId);
  const $body = document.getElementById(opts.bodyId);
  const $apply = document.getElementById(opts.applyId);
  const $reset = document.getElementById(opts.resetId);
  const $reprocess = opts.reprocessId ? document.getElementById(opts.reprocessId) : null;
  if (!$toggle || !$modal || !$body || !$apply || !$reset) {
    console.warn("dev_params: missing DOM nodes, skipping mount", opts);
    return;
  }

  function setStatus(msg) {
    if (opts.statusEl) opts.statusEl.textContent = msg;
  }

  function renderForm(settings) {
    const subset = settings.filter(e => e.module === opts.moduleFilter);
    $body.innerHTML = "";
    if (!subset.length) {
      $body.innerHTML = `<p class="dev-params-loading">No parameters for module "${opts.moduleFilter}".</p>`;
      return;
    }
    const group = document.createElement("section");
    group.className = "dev-params-group";
    const h3 = document.createElement("h3");
    h3.textContent = moduleTitle(opts.moduleFilter);
    group.appendChild(h3);
    for (const e of subset) {
      const row = document.createElement("label");
      row.className = "dev-params-row";
      row.dataset.name = e.name;
      const overridden = e.current !== e.default;
      const step = e.type === "int" ? "1" : "any";
      row.innerHTML =
        `<span class="dev-params-name">${escapeHtml(e.name)}` +
          (overridden ? ` <span class="dev-params-mod">●</span>` : "") +
          `</span>` +
        `<input type="number" step="${step}" min="${e.min}" max="${e.max}" ` +
          `value="${e.current}" data-default="${e.default}" data-type="${e.type}" />` +
        `<span class="dev-params-help">default ${e.default} · range [${e.min}, ${e.max}]</span>` +
        `<span class="dev-params-desc">${escapeHtml(e.description || "")}</span>` +
        `<span class="dev-params-error" hidden></span>`;
      group.appendChild(row);
    }
    $body.appendChild(group);
  }

  function collectDelta() {
    const out = {};
    for (const input of $body.querySelectorAll("input[type=number]")) {
      const row = input.closest(".dev-params-row");
      const name = row.dataset.name;
      const def = parseFloat(input.dataset.default);
      const type = input.dataset.type;
      const raw = input.value.trim();
      if (raw === "") continue;
      const num = parseFloat(raw);
      if (!Number.isFinite(num)) continue;
      if (num === def) continue;
      out[name] = type === "int" ? Math.round(num) : num;
    }
    return out;
  }

  function collectFilterDefaults() {
    // Used by Reset to send every visible field's default value.
    // Avoids touching keys outside this modal's scope.
    const out = {};
    for (const input of $body.querySelectorAll("input[type=number]")) {
      const row = input.closest(".dev-params-row");
      const def = parseFloat(input.dataset.default);
      const type = input.dataset.type;
      out[row.dataset.name] = type === "int" ? Math.round(def) : def;
    }
    return out;
  }

  function clearErrors() {
    for (const el of $body.querySelectorAll(".dev-params-error")) {
      el.hidden = true;
      el.textContent = "";
    }
  }

  function showErrors(perKey) {
    for (const [name, msg] of Object.entries(perKey)) {
      const row = $body.querySelector(`.dev-params-row[data-name="${name}"]`);
      if (!row) continue;
      const err = row.querySelector(".dev-params-error");
      err.textContent = msg;
      err.hidden = false;
    }
  }

  async function open() {
    $modal.hidden = false;
    $body.innerHTML = `<p class="dev-params-loading">Loading current settings…</p>`;
    try {
      const r = await fetch("/api/dev/settings");
      if (!r.ok) throw new Error(`GET failed: ${r.status}`);
      const data = await r.json();
      renderForm(data.settings);
      localStorage.setItem(DEV_OVERRIDES_KEY, JSON.stringify(data.settings));
    } catch (err) {
      $body.innerHTML = `<p class="dev-params-error" style="display:block">${escapeHtml(String(err))}</p>`;
    }
  }

  function close() { $modal.hidden = true; }

  $toggle.addEventListener("click", open);
  $modal.addEventListener("click", (e) => {
    if (e.target.matches("[data-close]")) close();
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$modal.hidden) close();
  });

  $apply.addEventListener("click", async () => {
    clearErrors();
    const body = collectDelta();
    if (!Object.keys(body).length) {
      setStatus("dev params: nothing to apply");
      return;
    }
    const r = await fetch("/api/dev/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.status === 400) {
      const data = await r.json();
      showErrors(data.detail?.errors || {});
      return;
    }
    if (!r.ok) { setStatus(`dev params apply failed: ${r.status}`); return; }
    const data = await r.json();
    renderForm(data.settings);
    localStorage.setItem(DEV_OVERRIDES_KEY, JSON.stringify(data.settings));
    setStatus("dev params applied");
  });

  $reset.addEventListener("click", async () => {
    const body = collectFilterDefaults();
    const r = await fetch("/api/dev/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) { setStatus(`dev params reset failed: ${r.status}`); return; }
    const data = await r.json();
    renderForm(data.settings);
    // Mirror only contains the slice we manage; on full reset of our
    // slice we just refresh the mirror with the new state.
    localStorage.setItem(DEV_OVERRIDES_KEY, JSON.stringify(data.settings));
    setStatus(`${moduleTitle(opts.moduleFilter)} params reset to defaults`);
  });

  if ($reprocess) {
    $reprocess.addEventListener("click", async () => {
      const ok = confirm(
        "Re-preprocess every uploaded file with the currently-applied dev " +
        "parameters?\n\nThis rewrites parsed primitives and pre-match caches " +
        "for every file in storage. Saved Match JSONs are kept on disk, but " +
        "their referenced handles may go stale if vertex counts change."
      );
      if (!ok) return;
      const r = await fetch("/api/dev/reprocess-all", { method: "POST" });
      if (!r.ok) { setStatus(`reprocess-all failed: ${r.status}`); return; }
      const { job_id } = await r.json();
      close();
      if (opts.onJobStart) opts.onJobStart(job_id);
    });
  }

  function syncToggleVisibility() {
    $toggle.hidden = !isDevMode();
  }

  syncToggleVisibility();
  return { syncToggleVisibility, open, close };
}
