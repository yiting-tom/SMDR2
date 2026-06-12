// Admin console: customers CRUD, grants (person/dept × role × scope),
// audit viewer. Dept dropdown = deptids seen at login (known_deptids) but
// free input stays legal — a dept nobody logged in from yet simply
// matches later (specs/authorization).
(function () {
  const $ = (id) => document.getElementById(id);
  let customers = [];
  let products = [];

  async function jget(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    return r.json();
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---- customers -----------------------------------------------------------
  async function refreshCustomers() {
    customers = (await jget("/api/customers")).customers;
    $("customer-table").querySelector("tbody").innerHTML = customers
      .map((c) => `<tr><td><code>${esc(c.id)}</code></td><td>${esc(c.name)}</td>
        <td>${c.id === "uncategorized" ? "" :
          `<button class="row-delete" data-del-customer="${esc(c.id)}">刪除</button>`}</td></tr>`)
      .join("");
  }

  $("customer-create").onclick = async () => {
    $("customer-error").textContent = "";
    const name = $("customer-name").value.trim();
    if (!name) return;
    const r = await fetch("/api/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!r.ok) {
      $("customer-error").textContent =
        r.status === 409 ? "名稱已存在" : `失敗(${r.status})`;
      return;
    }
    $("customer-name").value = "";
    await refreshCustomers();
    renderScopeOptions();
  };

  $("customer-table").addEventListener("click", async (e) => {
    const cid = e.target.dataset?.delCustomer;
    if (!cid) return;
    const r = await fetch(`/api/customers/${cid}`, { method: "DELETE" });
    $("customer-error").textContent =
      r.ok ? "" : (r.status === 409 ? "該客戶底下仍有產品" : `失敗(${r.status})`);
    await refreshCustomers();
    renderScopeOptions();
  });

  // ---- grants ----------------------------------------------------------------
  function renderScopeOptions() {
    const t = $("grant-scope-type").value;
    const sel = $("grant-scope-id");
    if (t === "global") { sel.hidden = true; sel.innerHTML = ""; return; }
    sel.hidden = false;
    const src = t === "customer"
      ? customers.map((c) => [c.id, c.name])
      : products.map((p) => [p.id, p.name]);
    sel.innerHTML = src.map(([v, label]) =>
      `<option value="${esc(v)}">${esc(label)}</option>`).join("");
  }
  $("grant-scope-type").onchange = renderScopeOptions;

  function scopeLabel(g) {
    if (g.scope_type === "global") return "global";
    if (g.scope_type === "customer") {
      const c = customers.find((x) => x.id === g.scope_id);
      return `customer:${c ? c.name : g.scope_id}`;
    }
    const p = products.find((x) => x.id === g.scope_id);
    return `product:${p ? p.name : g.scope_id}`;
  }

  async function refreshGrants() {
    const data = await jget("/api/grants");
    $("deptid-options").innerHTML = data.known_deptids
      .map((d) => `<option value="${esc(d)}">`).join("");
    $("grant-table").querySelector("tbody").innerHTML = data.grants.length
      ? data.grants.map((g) => `<tr>
        <td><span class="pill">${g.grantee_type === "dept" ? "部門" : "個人"}</span> ${esc(g.grantee_id)}</td>
        <td>${esc(g.role)}</td>
        <td>${esc(scopeLabel(g))}</td>
        <td>${esc(g.granted_by)}</td>
        <td><button class="row-delete" data-revoke="${esc(g.id)}">撤銷</button></td>
      </tr>`).join("")
      : `<tr><td colspan="5" class="admin-empty">尚無權限指派</td></tr>`;
  }

  $("grant-create").onclick = async () => {
    $("grant-error").textContent = "";
    const body = {
      grantee_type: $("grant-grantee-type").value,
      grantee_id: $("grant-grantee-id").value.trim(),
      role: $("grant-role").value,
      scope_type: $("grant-scope-type").value,
      scope_id: $("grant-scope-type").value === "global"
        ? "" : $("grant-scope-id").value,
    };
    if (!body.grantee_id) return;
    const r = await fetch("/api/grants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail;
      $("grant-error").textContent = detail || `失敗(${r.status})`;
      return;
    }
    $("grant-grantee-id").value = "";
    await refreshGrants();
  };

  $("grant-table").addEventListener("click", async (e) => {
    const gid = e.target.dataset?.revoke;
    if (!gid) return;
    await fetch(`/api/grants/${gid}`, { method: "DELETE" });
    await refreshGrants();
  });

  // ---- audit -----------------------------------------------------------------
  async function refreshAudit() {
    const q = new URLSearchParams();
    if ($("audit-actor").value.trim()) q.set("actor", $("audit-actor").value.trim());
    if ($("audit-action").value.trim()) q.set("action", $("audit-action").value.trim());
    const data = await jget(`/api/audit?${q}`);
    $("audit-table").querySelector("tbody").innerHTML = data.audit.length
      ? data.audit.map((a) => `<tr>
        <td>${new Date(a.at * 1000).toLocaleString()}</td>
        <td>${esc(a.actor)}</td>
        <td>${esc(a.action)}</td>
        <td>${esc(a.target_type)}:${esc(a.target_id)}</td>
        <td>${esc(a.product_id || "")}</td>
        <td><code>${esc(JSON.stringify(a.detail || ""))}</code></td>
      </tr>`).join("")
      : `<tr><td colspan="6" class="admin-empty">沒有符合的紀錄</td></tr>`;
  }
  $("audit-refresh").onclick = refreshAudit;

  // ---- boot -----------------------------------------------------------------
  (async () => {
    try {
      products = (await jget("/api/products")).products;
    } catch (_) { products = []; }
    await refreshCustomers();
    renderScopeOptions();
    await refreshGrants();
    await refreshAudit();
  })();
})();
