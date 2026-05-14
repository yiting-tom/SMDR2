// Shared layer-selection modal used by both the dashboard and the viewer.
//
// Usage:
//   import { openLayerModal } from "./layer_modal.js";
//   await openLayerModal({
//     fileId: "abc123",
//     onConfirm: async (selectedLayers) => { ... },  // called after POST 200
//   });
//
// The modal markup lives in dashboard.html / viewer.html (same `#layer-modal`
// structure on both pages). We bind handlers fresh on each open so the
// caller can re-target it for any file.

function $(id) { return document.getElementById(id); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Track the currently-bound listeners so we can remove them on close.
let activeListeners = null;
let activeKeyHandler = null;

function teardown() {
  if (activeListeners) {
    for (const { el, type, fn } of activeListeners) el.removeEventListener(type, fn);
    activeListeners = null;
  }
  if (activeKeyHandler) {
    window.removeEventListener("keydown", activeKeyHandler);
    activeKeyHandler = null;
  }
}

function listen(el, type, fn) {
  el.addEventListener(type, fn);
  activeListeners.push({ el, type, fn });
}

function renderEmpty(bodyEl, message) {
  bodyEl.innerHTML = `<p class="layer-empty">${escapeHtml(message)}</p>`;
}

function renderCards(bodyEl, fileId, layers, selectedSet) {
  bodyEl.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "layer-grid";
  for (const layer of layers) {
    const card = document.createElement("label");
    card.className = "layer-card";
    card.innerHTML =
      `<div class="layer-thumb">` +
        `<img src="/api/files/${fileId}/layer-preview/${encodeURIComponent(layer.safe_name)}.svg" ` +
        `alt="${escapeHtml(layer.name)}" loading="lazy" />` +
      `</div>` +
      `<div class="layer-row">` +
        `<input type="checkbox" data-layer="${escapeHtml(layer.name)}" ` +
          (selectedSet.has(layer.name) ? "checked" : "") + ` />` +
        `<span class="layer-name" title="${escapeHtml(layer.name)}">${escapeHtml(layer.name)}</span>` +
        `<span class="layer-count">${layer.entity_count}</span>` +
      `</div>`;
    grid.appendChild(card);
  }
  bodyEl.appendChild(grid);
}

function recomputeFooter(modal, total) {
  const boxes = modal.querySelectorAll('input[type="checkbox"][data-layer]');
  const checked = [...boxes].filter(b => b.checked);
  const confirmBtn = modal.querySelector("[data-layer-confirm]");
  confirmBtn.disabled = checked.length === 0;
  confirmBtn.textContent = `Use selected (${checked.length} of ${total})`;
  return checked.map(b => b.dataset.layer);
}

/**
 * Open the layer-selection modal for a given file.
 *
 * Resolves with `{ confirmed: true, layers }` after a successful confirm,
 * or `{ confirmed: false }` if the user closes the modal without
 * confirming. Throws if the manifest can't be fetched (e.g. Phase 1
 * hasn't finished and `triggerDiscovery` was false).
 *
 * @param {object} opts
 * @param {string} opts.fileId
 * @param {string} [opts.fileName]  optional, for the title row
 * @param {boolean} [opts.triggerDiscovery]  POST /discover-layers first
 *        and wait for the manifest before rendering. Default: false.
 * @param {(layers: string[]) => Promise<void>} [opts.onConfirm]  called
 *        after the POST /layers succeeds; the modal closes on resolve.
 */
export async function openLayerModal(opts) {
  const { fileId, fileName, triggerDiscovery = false, onConfirm } = opts;
  const modal = $("layer-modal");
  if (!modal) throw new Error("layer-modal markup missing on this page");
  const titleEl = modal.querySelector("[data-layer-title]");
  const bodyEl = modal.querySelector("[data-layer-body]");
  const confirmBtn = modal.querySelector("[data-layer-confirm]");
  const selectAllBtn = modal.querySelector("[data-layer-all]");
  const selectNoneBtn = modal.querySelector("[data-layer-none]");

  teardown();
  activeListeners = [];

  titleEl.textContent = fileName
    ? `${fileName} — pick layers to include`
    : "Pick layers to include";
  renderEmpty(bodyEl, "Loading layers…");
  confirmBtn.disabled = true;
  confirmBtn.textContent = "Use selected";
  modal.hidden = false;

  return new Promise((resolve) => {
    const close = (result) => {
      teardown();
      modal.hidden = true;
      resolve(result);
    };

    // Esc / overlay close
    activeKeyHandler = (e) => { if (e.key === "Escape") close({ confirmed: false }); };
    window.addEventListener("keydown", activeKeyHandler);
    for (const el of modal.querySelectorAll("[data-close]")) {
      listen(el, "click", () => close({ confirmed: false }));
    }

    // Background fetch (with optional discovery kick-off)
    (async () => {
      try {
        if (triggerDiscovery) {
          const r = await fetch(`/api/files/${fileId}/discover-layers`, { method: "POST" });
          if (!r.ok) throw new Error(`discover-layers failed: ${r.status}`);
          // Poll until the manifest is on disk (Phase 1 finishes).
          for (let i = 0; i < 200; i++) {
            const probe = await fetch(`/api/files/${fileId}/layers`);
            if (probe.ok) break;
            if (probe.status !== 404) throw new Error(`layers fetch failed: ${probe.status}`);
            await new Promise(r => setTimeout(r, 300));
          }
        }
        const res = await fetch(`/api/files/${fileId}/layers`);
        if (!res.ok) {
          renderEmpty(bodyEl, `Layers not available yet (HTTP ${res.status}).`);
          return;
        }
        const data = await res.json();
        const layers = data.manifest?.layers ?? [];
        if (!layers.length) {
          renderEmpty(bodyEl, "This DXF has no layers we could discover.");
          return;
        }
        // Default selection: existing selection if any, else all layers.
        const initial = data.selected_layers ?? layers.map(l => l.name);
        const selectedSet = new Set(initial);
        renderCards(bodyEl, fileId, layers, selectedSet);
        const recompute = () => recomputeFooter(modal, layers.length);
        recompute();
        for (const cb of bodyEl.querySelectorAll('input[type="checkbox"][data-layer]')) {
          listen(cb, "change", recompute);
        }
        listen(selectAllBtn, "click", () => {
          for (const cb of bodyEl.querySelectorAll('input[type="checkbox"][data-layer]')) cb.checked = true;
          recompute();
        });
        listen(selectNoneBtn, "click", () => {
          for (const cb of bodyEl.querySelectorAll('input[type="checkbox"][data-layer]')) cb.checked = false;
          recompute();
        });
        listen(confirmBtn, "click", async () => {
          const chosen = recompute();
          if (!chosen.length) return;
          confirmBtn.disabled = true;
          confirmBtn.textContent = "Saving…";
          const r = await fetch(`/api/files/${fileId}/layers`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ layers: chosen }),
          });
          if (!r.ok) {
            const text = await r.text();
            renderEmpty(bodyEl, `Save failed: ${r.status} ${text}`);
            confirmBtn.disabled = false;
            return;
          }
          if (onConfirm) await onConfirm(chosen);
          close({ confirmed: true, layers: chosen });
        });
      } catch (e) {
        renderEmpty(bodyEl, `Error loading layers: ${e}`);
      }
    })();
  });
}
