// AutoCAD-tab (layout) selection modal, used by the dashboard when a DXF's
// geometry lives in more than one paper-space layout and the operator must
// pick which tab to load. It is the single-select sibling of
// layer_modal.js — same fetch/thumbnail/confirm contract, radios instead of
// checkboxes, no select-all/none.
//
// Usage:
//   import { openLayoutModal } from "./layout_modal.js";
//   await openLayoutModal({
//     fileId: "abc123",
//     onConfirm: async (layoutName) => { ... },  // called after POST 200
//   });
//
// The modal markup (`#layout-modal`) lives in dashboard.html.

function $(id) { return document.getElementById(id); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

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

function renderCards(bodyEl, fileId, layouts, chosenName) {
  bodyEl.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "layer-grid";
  for (const layout of layouts) {
    const card = document.createElement("label");
    card.className = "layer-card";
    const space = layout.is_paperspace ? "paper space" : "model space";
    card.innerHTML =
      `<div class="layer-thumb">` +
        `<img src="/api/files/${fileId}/layout-preview/${encodeURIComponent(layout.safe_name)}.svg" ` +
        `alt="${escapeHtml(layout.name)}" loading="lazy" />` +
      `</div>` +
      `<div class="layer-row">` +
        `<input type="radio" name="layout-pick" data-layout="${escapeHtml(layout.name)}" ` +
          (layout.name === chosenName ? "checked" : "") + ` />` +
        `<span class="layer-name" title="${escapeHtml(layout.name)} (${space})">${escapeHtml(layout.name)}</span>` +
        `<span class="layer-count">${layout.entity_count}</span>` +
      `</div>`;
    grid.appendChild(card);
  }
  bodyEl.appendChild(grid);
}

function selectedLayout(modal) {
  const checked = modal.querySelector('input[type="radio"][data-layout]:checked');
  return checked ? checked.dataset.layout : null;
}

function recomputeFooter(modal) {
  const chosen = selectedLayout(modal);
  const confirmBtn = modal.querySelector("[data-layout-confirm]");
  confirmBtn.disabled = !chosen;
  confirmBtn.textContent = chosen ? `Load "${chosen}"` : "Pick a view";
  return chosen;
}

/**
 * Open the AutoCAD-tab selection modal for a given file.
 *
 * Resolves with `{ confirmed: true, layout }` after a successful confirm,
 * or `{ confirmed: false }` if the user closes without confirming.
 *
 * @param {object} opts
 * @param {string} opts.fileId
 * @param {string} [opts.fileName]  optional, for the title row
 * @param {(layout: string) => Promise<void>} [opts.onConfirm]  called after
 *        POST /layouts succeeds; the modal closes on resolve.
 */
export async function openLayoutModal(opts) {
  const { fileId, fileName, onConfirm } = opts;
  const modal = $("layout-modal");
  if (!modal) throw new Error("layout-modal markup missing on this page");
  const titleEl = modal.querySelector("[data-layout-title]");
  const bodyEl = modal.querySelector("[data-layout-body]");
  const confirmBtn = modal.querySelector("[data-layout-confirm]");

  teardown();
  activeListeners = [];

  titleEl.textContent = fileName
    ? `${fileName} — pick which view (AutoCAD tab) to load`
    : "Pick which view (AutoCAD tab) to load";
  renderEmpty(bodyEl, "Loading views…");
  confirmBtn.disabled = true;
  confirmBtn.textContent = "Pick a view";
  modal.hidden = false;

  return new Promise((resolve) => {
    // Set while the confirm POST is in flight so Esc / overlay-click can't
    // resolve {confirmed:false} after the server has already committed the
    // pick (which would skip the dashboard's immediate refresh).
    let inFlight = false;
    const close = (result) => {
      if (inFlight) return;
      teardown();
      modal.hidden = true;
      resolve(result);
    };
    const showError = (msg) => {
      // Surface an error WITHOUT wiping the radio grid, so the user can retry.
      let err = bodyEl.querySelector(".layout-error");
      if (!err) {
        err = document.createElement("p");
        err.className = "layer-empty layout-error";
        bodyEl.prepend(err);
      }
      err.textContent = msg;
    };

    activeKeyHandler = (e) => { if (e.key === "Escape") close({ confirmed: false }); };
    window.addEventListener("keydown", activeKeyHandler);
    for (const el of modal.querySelectorAll("[data-close]")) {
      listen(el, "click", () => close({ confirmed: false }));
    }

    (async () => {
      try {
        const res = await fetch(`/api/files/${fileId}/layouts`);
        if (!res.ok) {
          renderEmpty(bodyEl, `Views not available yet (HTTP ${res.status}).`);
          return;
        }
        const data = await res.json();
        const layouts = data.manifest?.layouts ?? [];
        if (!layouts.length) {
          renderEmpty(bodyEl, "This DXF has no selectable views.");
          return;
        }
        // Default: existing choice if any, else the first (richest) tab.
        const chosenName = data.chosen_layout ?? layouts[0].name;
        renderCards(bodyEl, fileId, layouts, chosenName);
        const recompute = () => recomputeFooter(modal);
        recompute();
        for (const rb of bodyEl.querySelectorAll('input[type="radio"][data-layout]')) {
          listen(rb, "change", recompute);
        }
        listen(confirmBtn, "click", async () => {
          const chosen = recompute();
          if (!chosen) return;
          confirmBtn.disabled = true;
          confirmBtn.textContent = "Loading…";
          inFlight = true;
          try {
            const r = await fetch(`/api/files/${fileId}/layouts`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ layout: chosen }),
            });
            if (!r.ok) {
              const text = await r.text();
              recompute();  // restore footer (button text + enabled state)
              showError(`Load failed: ${r.status} ${text}`);
              return;
            }
            if (onConfirm) await onConfirm(chosen);
            inFlight = false;  // allow close() to settle the promise
            close({ confirmed: true, layout: chosen });
          } catch (e) {
            recompute();
            showError(`Load failed: ${e}`);
          } finally {
            inFlight = false;
          }
        });
      } catch (e) {
        renderEmpty(bodyEl, `Error loading views: ${e}`);
      }
    })();
  });
}
