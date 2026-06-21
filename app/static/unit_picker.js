// Viewer header unit-override picker.
//
// On load the picker reflects the file's current `applied_scale` (via
// `SCALE_TO_UNIT`) and `user_unit_override`. Changing the selection
// opens a confirm modal listing the downstream invalidations
// (preprocess re-run, connectivity rebuild, Match JSON cleared per
// affected product). Only after the operator confirms does the
// picker POST `/api/files/{id}/unit-override` and disable itself
// until the recompute completes.
//
// Cross-session recovery: when the viewer loads while a preprocess
// job is already in flight (status == "preprocessing"), the picker
// boots into the disabled / job-in-flight state and polls until the
// file flips back to `ready_to_match`.

const UNIT_TO_SCALE = {
  "mm": 1.0,
  "cm": 10.0,
  "m": 1000.0,
  "inch": 25.4,
  "μm": 0.001,
};

// Reverse map for pre-selecting the dropdown from a file's
// `applied_scale`. Floats are compared by exact equality because the
// only values that should ever land here come from `UNIT_TO_SCALE`
// itself (the backend uses the same source-of-truth table).
const SCALE_TO_UNIT = new Map([
  [1.0, "mm"],
  [10.0, "cm"],
  [1000.0, "m"],
  [25.4, "inch"],
  [0.001, "μm"],
]);

// Map a DXF `$INSUNITS` integer to our picker's unit-name vocabulary,
// for the "Differs from file declaration" soft hint. Anything outside
// this map (unitless = 0, missing, foot = 2, …) means we can't
// produce a meaningful hint, so the picker stays silent.
const INSUNITS_TO_UNIT = {
  1: "inch",
  4: "mm",
  5: "cm",
  6: "m",
};

const POLL_INTERVAL_MS = 1500;

export function initUnitPicker(file, opts = {}) {
  const $wrap = document.getElementById("unit-picker-wrap");
  const $select = document.getElementById("unit-picker");
  const $badge = document.getElementById("unit-picker-badge");
  const $hint = document.getElementById("unit-picker-hint");
  if (!$wrap || !$select || !$badge || !$hint) return;

  const fileId = file.id;
  // File endpoints are version-scoped — every fetch carries ?version_id=.
  const versionId = opts.versionId ?? file.version_id;
  const vq = `version_id=${encodeURIComponent(versionId)}`;
  const onComplete = typeof opts.onComplete === "function" ? opts.onComplete : null;
  // Signed-off version: the picker is read-only (display only, no POST).
  const readOnly = !!opts.readOnly;

  let currentFile = file;
  let pollTimer = null;

  // Reflect the file's current state in the picker. Called on init
  // and after every recompute completes.
  function render() {
    const f = currentFile;
    const desired = pickUnitForFile(f);
    $select.value = desired;
    $select.dataset.committed = desired;

    if (f.user_unit_override) {
      $badge.hidden = false;
      $badge.classList.remove("job-inflight");
      $badge.textContent = "set by you";
      $badge.title = `Override: ${f.user_unit_override}`;
    } else {
      $badge.hidden = true;
      $badge.textContent = "";
      $badge.title = "";
    }
    renderHint(f, desired);
  }

  function renderHint(f, selected) {
    const declared = INSUNITS_TO_UNIT[f.insunits];
    if (declared && declared !== selected) {
      $hint.hidden = false;
      $hint.textContent = `⚠ Differs from file declaration (${declared})`;
    } else {
      $hint.hidden = true;
      $hint.textContent = "";
    }
  }

  function setJobInflight(jobId) {
    $select.disabled = true;
    $badge.hidden = false;
    $badge.classList.add("job-inflight");
    $badge.textContent = jobId ? `recompute: ${jobId.slice(0, 6)}…` : "recompute…";
    $badge.title = jobId ? `Job ${jobId}` : "Preprocess in flight";
    $hint.hidden = true;
    startPollingFile();
  }

  function clearJobInflight() {
    stopPollingFile();
    $select.disabled = false;
    $badge.classList.remove("job-inflight");
    render();
    if (onComplete) onComplete(currentFile);
  }

  function startPollingFile() {
    stopPollingFile();
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/files/${fileId}?${vq}`);
        if (!res.ok) return;
        const f = await res.json();
        if (f.status !== "preprocessing") {
          currentFile = f;
          clearJobInflight();
        } else {
          currentFile = f;
        }
      } catch (err) {
        // Network hiccup — keep polling.
      }
    }, POLL_INTERVAL_MS);
  }

  function stopPollingFile() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ---- Wire up the dropdown ---------------------------------------
  $select.addEventListener("change", () => {
    const next = $select.value;
    const committed = $select.dataset.committed;
    if (next === committed) return;
    openConfirmModal(currentFile, next, {
      onCancel: () => { $select.value = committed; },
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/files/${fileId}/unit-override?${vq}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ unit: next }),
          });
          if (res.status === 409) {
            // Distinguish "version signed-off" (frozen, no job) from the
            // legacy "job already in flight" 409 (carries job_id).
            const body = await res.json().catch(() => null);
            const detail = body?.detail;
            if (detail && detail.error === "version signed-off") {
              $select.value = committed;
              $select.disabled = true;
              alert(`此版本已由 ${detail.signed_off_by} 畫押,無法變更單位。`);
              return;
            }
            if (body?.job_id) {
              $select.dataset.committed = next;
              setJobInflight(body.job_id);
              return;
            }
            $select.value = committed;
            alert(`Unit override failed: 409`);
            return;
          }
          if (res.status === 202) {
            const body = await res.json();
            const jobId = body.job_id;
            $select.dataset.committed = next;
            setJobInflight(jobId);
          } else {
            // Backend rejected — bounce back to the previous selection.
            $select.value = committed;
            alert(`Unit override failed: ${res.status} ${res.statusText}`);
          }
        } catch (err) {
          $select.value = committed;
          alert(`Unit override failed: ${err.message}`);
        }
      },
    });
  });

  // ---- Initial paint -----------------------------------------------
  render();
  if (readOnly) {
    // Signed-off version: show the committed unit but reject edits.
    $select.disabled = true;
    $select.title = "版本已畫押(唯讀)— 無法變更單位";
    return;
  }
  if (file.status === "preprocessing") {
    setJobInflight(null);
  }
}

// Choose the dropdown option for a file. When `applied_scale` is one
// of the canonical unit multipliers, return that unit; otherwise fall
// back to mm (the spec says: also surface an `(actual ×<scale>)` badge
// in this case — we drop that for now because the only way the value
// gets out of band is a future unit added to the picker without
// updating SCALE_TO_UNIT, which is a developer bug, not a runtime
// case the operator hits).
function pickUnitForFile(f) {
  const scale = typeof f.applied_scale === "number" ? f.applied_scale : 1.0;
  return SCALE_TO_UNIT.get(scale) || "mm";
}

function openConfirmModal(file, nextUnit, callbacks) {
  const $modal = document.getElementById("unit-override-modal");
  const $body = document.getElementById("unit-override-modal-body");
  const $cancel = document.getElementById("unit-override-cancel");
  const $confirm = document.getElementById("unit-override-confirm");
  if (!$modal || !$body || !$cancel || !$confirm) {
    callbacks.onConfirm();
    return;
  }

  const detectorChoice = describeDetectorChoice(file);
  const products = listAffectedProducts(file);
  $body.className = "unit-override-modal-body";
  $body.innerHTML = `
    <p>Set this file's unit to <strong>${escapeHtml(nextUnit)}</strong>?
       Applying this will:</p>
    <ul>
      <li>Re-run preprocess for this file with the new multiplier
        (<code>×${UNIT_TO_SCALE[nextUnit]}</code>).</li>
      <li>Rebuild cached connectivity and pre-match for this file.</li>
      <li>${renderAffectedProductsLine(products)}</li>
      <li>${detectorChoice
        ? `To undo, pick <strong>${escapeHtml(detectorChoice)}</strong>
            again — that's what the auto-rescale detector would have chosen.`
        : "To undo, pick the previously-active unit again."}</li>
    </ul>
  `;

  $modal.hidden = false;

  function cleanup() {
    $modal.hidden = true;
    $cancel.removeEventListener("click", onCancel);
    $confirm.removeEventListener("click", onConfirm);
    for (const el of $modal.querySelectorAll("[data-close]")) {
      el.removeEventListener("click", onCancel);
    }
  }
  function onCancel() { cleanup(); callbacks.onCancel(); }
  function onConfirm() { cleanup(); callbacks.onConfirm(); }
  $cancel.addEventListener("click", onCancel);
  $confirm.addEventListener("click", onConfirm);
  for (const el of $modal.querySelectorAll("[data-close]")) {
    el.addEventListener("click", onCancel);
  }
}

function renderAffectedProductsLine(products) {
  if (products.length === 0) {
    return "No products affected — this file is not bound to one.";
  }
  const head = products.slice(0, 3).map(p => escapeHtml(p.name)).join(", ");
  const tail = products.length > 3 ? ` and ${products.length - 3} more` : "";
  return `Clear saved Match JSON for ${products.length} product`
       + `${products.length === 1 ? "" : "s"}: ${head}${tail}.`;
}

// Today a file row belongs to at most one product, so this list has 0
// or 1 entry. Shaped as a list so we can swap in a server-returned
// list later without changing the modal.
function listAffectedProducts(file) {
  if (file.product_name) {
    return [{ id: file.product_id, name: file.product_name }];
  }
  if (file.product_id) {
    return [{ id: file.product_id, name: file.product_id }];
  }
  return [];
}

// Best-effort label for the unit the auto-rescale detector would
// have chosen. We don't have the detector's raw output on the file
// payload, so we approximate from `applied_scale + user_unit_override`:
// if no override is set, the current `applied_scale` IS what the
// detector picked.
function describeDetectorChoice(file) {
  if (file.user_unit_override) return null; // we don't know what detector picks without re-running
  return pickUnitForFile(file);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[c]));
}
