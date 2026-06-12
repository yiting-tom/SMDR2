// Product edit lock UI (specs/product-edit-lock).
//
// Explicit start-editing: the page loads in read-only awareness until the
// operator clicks 開始編輯; while held, a 30s heartbeat keeps the lock
// alive and closing the tab releases it (best-effort — the 5min TTL is
// the real cleanup). When someone else holds it, show who/since-when.
//
// In bypass mode (/api/me → source "bypass") the server skips lock
// enforcement; the widget still works and simply always succeeds.
(function () {
  const body = document.body;
  const productId = body.dataset.productId;
  const signedOff = !!body.dataset.signedOff;
  const slot = document.getElementById("edit-lock-slot");
  if (!productId || !slot || signedOff) return;

  let me = null;
  let heartbeatTimer = null;

  const el = document.createElement("span");
  el.id = "edit-lock";
  slot.appendChild(el);

  // CSRF: csrf.js (loaded before this script) wraps window.fetch and
  // injects X-CSRF-Token on every mutating request — no local handling.
  async function api(method, path) {
    const r = await fetch(path, { method });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  }

  function render(holder) {
    if (holder && me && holder === me.userid) {
      el.innerHTML =
        '<span class="lock-mine" title="你持有此產品的編輯鎖">編輯中</span> ' +
        '<button id="lock-release" type="button">結束編輯</button>';
      document.getElementById("lock-release").onclick = release;
    } else if (holder) {
      el.innerHTML =
        `<span class="lock-other" title="此產品正被編輯,目前唯讀">` +
        `🔒 ${holder} 編輯中(唯讀)</span>`;
    } else {
      el.innerHTML = '<button id="lock-acquire" type="button">開始編輯</button>';
      document.getElementById("lock-acquire").onclick = acquire;
    }
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(async () => {
      const r = await api("POST", `/api/products/${productId}/lock/heartbeat`);
      if (r.status !== 200) {  // lost the lock (TTL / force-release)
        stopHeartbeat();
        refresh();
      }
    }, 30000);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }

  async function acquire() {
    const r = await api("POST", `/api/products/${productId}/lock`);
    if (r.status === 200) {
      startHeartbeat();
      render(me ? me.userid : null);
    } else if (r.status === 423) {
      render(r.body.held_by || "(unknown)");
    } else if (r.status === 403) {
      el.innerHTML = '<span class="lock-other">無此產品的 editor 權限</span>';
    }
  }

  async function release() {
    stopHeartbeat();
    await api("DELETE", `/api/products/${productId}/lock`);
    refresh();
  }

  async function refresh() {
    const r = await api("GET", `/api/products/${productId}/lock`);
    render(r.status === 200 ? r.body.held_by : null);
  }

  window.addEventListener("beforeunload", () => {
    if (heartbeatTimer) {
      fetch(`/api/products/${productId}/lock`, {
        method: "DELETE", keepalive: true,
      });
    }
  });

  fetch("/api/me").then((r) => r.json()).then((m) => {
    me = m;
    refresh();
  }).catch(refresh);
})();
