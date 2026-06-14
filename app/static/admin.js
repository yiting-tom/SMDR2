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

  // ---- sidebar section switching ------------------------------------------
  // The rail toggles which of the three .admin-section panels is visible —
  // no reload. Deep-linked through the URL hash (#customers/#grants/#audit)
  // so a link/bookmark lands on the right section (nav: deep-linking).
  const SECTIONS = ["customers", "grants", "audit"];
  function showSection(name) {
    if (!SECTIONS.includes(name)) name = "customers";
    document.querySelectorAll(".admin-nav-item").forEach((b) => {
      const on = b.dataset.section === name;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    document.querySelectorAll(".admin-section").forEach((s) => {
      s.classList.toggle("is-active", s.dataset.section === name);
    });
    if (location.hash.slice(1) !== name) {
      history.replaceState(null, "", `#${name}`);
    }
  }
  function setupNav() {
    document.querySelectorAll(".admin-nav-item").forEach((b) => {
      b.addEventListener("click", () => showSection(b.dataset.section));
    });
    window.addEventListener("hashchange", () =>
      showSection(location.hash.slice(1)));
    showSection(location.hash.slice(1) || "customers");
  }
  function setNavCount(name, n) {
    const el = $(`nav-count-${name}`);
    if (el) el.textContent = n;
  }

  // ---- reusable client-side paginator -------------------------------------
  // Pages an already-fetched row array through a table body, driving a
  // footer count note + prev/next pager. Each table owns one instance. The
  // pager hides when everything fits on a single page; current page is
  // preserved across re-renders (and clamped) unless setRows is told to
  // reset — so deleting a row doesn't kick you back to page 1, but a new
  // filter does. All three admin tables share this.
  function createPaginator({ table, pager, note, pageSize, colspan, renderRow, emptyText }) {
    const tbody = $(table).querySelector("tbody");
    let rows = [];
    let noteExtra = "";
    let page = 1;

    function render() {
      const total = rows.length;
      const pages = Math.max(1, Math.ceil(total / pageSize));
      page = Math.min(Math.max(1, page), pages);
      const start = (page - 1) * pageSize;
      const slice = rows.slice(start, start + pageSize);
      tbody.innerHTML = total
        ? slice.map(renderRow).join("")
        : `<tr><td colspan="${colspan}" class="admin-empty">${emptyText}</td></tr>`;
      if (note) {
        $(note).textContent = total
          ? `第 ${start + 1}–${start + slice.length} 筆 / 共 ${total} 筆${noteExtra}`
          : "";
      }
      $(pager).innerHTML = pages > 1
        ? `<button type="button" class="pager-btn" data-page="prev"`
          + `${page <= 1 ? " disabled" : ""}>‹ 上一頁</button>`
          + `<span class="pager-status">${page} / ${pages}</span>`
          + `<button type="button" class="pager-btn" data-page="next"`
          + `${page >= pages ? " disabled" : ""}>下一頁 ›</button>`
        : "";
    }

    $(pager).addEventListener("click", (e) => {
      const dir = e.target.dataset?.page;
      if (dir === "prev") page -= 1;
      else if (dir === "next") page += 1;
      else return;
      render();
    });

    return {
      setRows(newRows, { extra = "", resetPage = false } = {}) {
        rows = newRows;
        noteExtra = extra;
        if (resetPage) page = 1;
        render();
      },
    };
  }

  // ---- customers -----------------------------------------------------------
  // Product count per customer, from the already-fetched product list — a
  // customer with products is RESTRICT-protected server-side, so the count
  // doubles as the reason its delete is withheld.
  function productCountByCustomer() {
    const m = new Map();
    for (const p of products) m.set(p.customer_id, (m.get(p.customer_id) || 0) + 1);
    return m;
  }

  const customerPager = createPaginator({
    table: "customer-table", pager: "customer-pager", note: "customer-note",
    pageSize: 10, colspan: 4, emptyText: "尚無客戶",
    renderRow: (c) => {
      // 'uncategorized' is permanent; a customer holding products can't be
      // deleted (409) — show the reason instead of a button that only fails.
      const action = c.id === "uncategorized"
        ? `<span class="admin-empty">內建</span>`
        : c.n > 0
          ? `<span class="admin-empty" title="有產品的客戶不可刪除">不可刪除</span>`
          : `<button class="row-delete" data-del-customer="${esc(c.id)}">刪除</button>`;
      return `<tr>
        <td><code>${esc(c.id)}</code></td>
        <td>${esc(c.name)}</td>
        <td><span class="count-badge${c.n === 0 ? " zero" : ""}">${c.n}</span></td>
        <td>${action}</td>
      </tr>`;
    },
  });

  async function refreshCustomers() {
    customers = (await jget("/api/customers")).customers;
    const counts = productCountByCustomer();
    setNavCount("customers", customers.length);
    // Attach the product count to each row so renderRow stays pure.
    customerPager.setRows(
      customers.map((c) => ({ ...c, n: counts.get(c.id) || 0 })));
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
  let allGrants = [];               // last fetch (unfiltered) — drives filter
  const grantsById = new Map();     // id → grant, for the revoke confirm label
  let knownUsers = [];              // [{userid, name}] — grantee dropdown (個人)
  let knownDepts = [];              // [deptid]          — grantee dropdown (部門)
  const GRANTEE_OTHER = "__other__";

  // Toggle a single <option>'s disabled state by value.
  function setOptionEnabled(sel, value, enabled) {
    const opt = [...sel.options].find((o) => o.value === value);
    if (opt) opt.disabled = !enabled;
  }

  // Grantee picker = a dropdown of known users / depts + a trailing
  // "其他(手動輸入)" option. Selecting 其他 reveals the text field so an
  // admin can still pre-grant someone who has never logged in.
  function renderGranteeOptions() {
    const type = $("grant-grantee-type").value;
    const sel = $("grant-grantee-select");
    const cur = sel.value;
    const opts = type === "dept"
      ? knownDepts.map((d) => [d, d])
      : knownUsers.map((u) => [u.userid, u.name ? `${u.userid} — ${u.name}` : u.userid]);
    sel.innerHTML = opts.map(([v, label]) =>
      `<option value="${esc(v)}">${esc(label)}</option>`).join("")
      + `<option value="${GRANTEE_OTHER}">其他(手動輸入…)</option>`;
    // Keep the prior selection if it survived the type switch; otherwise the
    // first real option (or 其他 when the list is empty).
    sel.value = [...sel.options].some((o) => o.value === cur) ? cur : sel.options[0].value;
    toggleGranteeOther(sel.value === GRANTEE_OTHER);
  }
  function toggleGranteeOther(show) {
    const input = $("grant-grantee-id");
    input.hidden = !show;
    if (show) input.focus();
    else input.value = "";
  }
  // The grantee id actually being submitted: the dropdown value, or the
  // manual field when 其他 is picked.
  function currentGranteeId() {
    const sel = $("grant-grantee-select");
    return sel.value === GRANTEE_OTHER
      ? $("grant-grantee-id").value.trim()
      : sel.value;
  }
  // Prefill the grantee picker (used by row "再指派"): select the option if
  // it's a known grantee, else fall back to 其他 + manual value.
  function setGrantee(type, id) {
    $("grant-grantee-type").value = type;
    renderGranteeOptions();
    const sel = $("grant-grantee-select");
    if ([...sel.options].some((o) => o.value === id && o.value !== GRANTEE_OTHER)) {
      sel.value = id;
      toggleGranteeOther(false);
    } else {
      sel.value = GRANTEE_OTHER;
      toggleGranteeOther(true);
      $("grant-grantee-id").value = id;
    }
  }
  $("grant-grantee-select").onchange = () =>
    toggleGranteeOther($("grant-grantee-select").value === GRANTEE_OTHER);

  // Encode the server's grant rules in the form so an illegal combo can't be
  // built (no more "submit → 400"):
  //   • 部門  → 角色 only viewer
  //   • admin → 對象 only 個人, 範圍 only global
  // Conflicting options are disabled and the live value corrected. A hint
  // explains why something is locked.
  function reconcileGrantForm() {
    const typeSel = $("grant-grantee-type");
    const roleSel = $("grant-role");
    const scopeSel = $("grant-scope-type");

    const isDept = typeSel.value === "dept";
    setOptionEnabled(roleSel, "editor", !isDept);
    setOptionEnabled(roleSel, "admin", !isDept);
    if (isDept && roleSel.value !== "viewer") roleSel.value = "viewer";

    const isAdmin = roleSel.value === "admin";
    setOptionEnabled(typeSel, "dept", !isAdmin);
    setOptionEnabled(scopeSel, "customer", !isAdmin);
    setOptionEnabled(scopeSel, "product", !isAdmin);
    if (isAdmin && scopeSel.value !== "global") scopeSel.value = "global";

    $("grant-hint").textContent = isDept
      ? "部門只能授予 viewer"
      : isAdmin ? "admin 僅限個人 + 全域" : "";
    renderScopeOptions();
  }

  // The scope-target <select> is only shown for customer/product scopes;
  // its options come from the live customer / product lists.
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

  $("grant-grantee-type").onchange = () => {
    renderGranteeOptions();   // user list ↔ dept list
    reconcileGrantForm();     // dept → viewer lock
  };
  $("grant-role").onchange = reconcileGrantForm;
  $("grant-scope-type").onchange = renderScopeOptions;

  // Scope rendered as a colour-coded badge (type by text + colour, never
  // colour alone). scopeText() is the plain-text version used for filtering.
  function scopeBadge(g) {
    if (g.scope_type === "global")
      return `<span class="scope-badge scope-global">全域</span>`;
    if (g.scope_type === "customer") {
      const c = customers.find((x) => x.id === g.scope_id);
      return `<span class="scope-badge scope-customer">客戶 · ${esc(c ? c.name : g.scope_id)}</span>`;
    }
    const p = products.find((x) => x.id === g.scope_id);
    return `<span class="scope-badge scope-product">產品 · ${esc(p ? p.name : g.scope_id)}</span>`;
  }
  function scopeText(g) {
    if (g.scope_type === "global") return "global 全域";
    if (g.scope_type === "customer") {
      const c = customers.find((x) => x.id === g.scope_id);
      return `customer 客戶 ${c ? c.name : g.scope_id}`;
    }
    const p = products.find((x) => x.id === g.scope_id);
    return `product 產品 ${p ? p.name : g.scope_id}`;
  }

  const grantPager = createPaginator({
    table: "grant-table", pager: "grant-pager", note: "grant-note",
    pageSize: 10, colspan: 5, emptyText: "尚無權限指派",
    renderRow: (g) => `<tr>
      <td><span class="pill">${g.grantee_type === "dept" ? "部門" : "個人"}</span> <code>${esc(g.grantee_id)}</code></td>
      <td><span class="pill role-${esc(g.role)}">${esc(g.role)}</span></td>
      <td>${scopeBadge(g)}</td>
      <td><code>${esc(g.granted_by)}</code></td>
      <td class="row-actions">
        <button class="row-assign" data-assign-type="${esc(g.grantee_type)}"
                data-assign-id="${esc(g.grantee_id)}"
                title="再給此對象指派一個角色 / 產品">＋ 指派</button>
        <button class="row-delete" data-revoke="${esc(g.id)}">撤銷</button>
      </td>
    </tr>`,
  });

  // Filter the already-fetched grants by grantee / role / scope / granter.
  function applyGrantFilter(resetPage = false) {
    const q = $("grant-filter").value.trim().toLowerCase();
    const rows = q
      ? allGrants.filter((g) =>
          `${g.grantee_type} ${g.grantee_id} ${g.role} ${g.granted_by} ${scopeText(g)}`
            .toLowerCase().includes(q))
      : allGrants;
    grantPager.setRows(rows, { extra: q ? "(符合過濾)" : "", resetPage });
  }
  $("grant-filter").addEventListener("input", () => applyGrantFilter(true));

  async function refreshGrants() {
    const data = await jget("/api/grants");
    allGrants = data.grants;
    grantsById.clear();
    for (const g of allGrants) grantsById.set(g.id, g);
    setNavCount("grants", allGrants.length);
    knownUsers = data.known_users || [];
    knownDepts = data.known_deptids || [];
    renderGranteeOptions();
    applyGrantFilter();
  }

  // Briefly tint the form so a prefilled "再指派" lands visibly.
  function flashGrantForm() {
    const form = document.querySelector(".grant-form");
    if (!form) return;
    form.classList.remove("flash");
    void form.offsetWidth;   // restart the animation
    form.classList.add("flash");
  }

  $("grant-create").onclick = async () => {
    $("grant-error").textContent = "";
    const granteeId = currentGranteeId();
    if (!granteeId) {
      $("grant-error").textContent =
        $("grant-grantee-select").value === GRANTEE_OTHER
          ? "請輸入對象 userid / deptid"
          : "請選擇對象";
      if ($("grant-grantee-select").value === GRANTEE_OTHER) $("grant-grantee-id").focus();
      return;
    }
    const body = {
      grantee_type: $("grant-grantee-type").value,
      grantee_id: granteeId,
      role: $("grant-role").value,
      scope_type: $("grant-scope-type").value,
      scope_id: $("grant-scope-type").value === "global"
        ? "" : $("grant-scope-id").value,
    };
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
    await refreshGrants();   // re-renders the grantee dropdown (resets it)
  };

  // Row actions: confirm-then-revoke, and "再指派" which prefills the form
  // with this grantee (defaulting to product scope) for a fast follow-up.
  $("grant-table").addEventListener("click", async (e) => {
    const revokeBtn = e.target.closest("[data-revoke]");
    if (revokeBtn) {
      const gid = revokeBtn.dataset.revoke;
      const g = grantsById.get(gid);
      const label = g
        ? `${g.grantee_type === "dept" ? "部門" : "個人"} ${g.grantee_id}`
          + ` — ${g.role} @ ${scopeText(g)}`
        : gid;
      if (!confirm(`確定要撤銷此權限?\n\n${label}`)) return;
      await fetch(`/api/grants/${gid}`, { method: "DELETE" });
      await refreshGrants();
      return;
    }
    const assignBtn = e.target.closest("[data-assign-id]");
    if (assignBtn) {
      setGrantee(assignBtn.dataset.assignType, assignBtn.dataset.assignId);
      // Reset to the common "give this user a product (viewer)" starting
      // point — viewer keeps product scope legal (a stale admin would force
      // it back to global). The admin tweaks role/product from here.
      $("grant-role").value = "viewer";
      if (products.length) $("grant-scope-type").value = "product";
      reconcileGrantForm();
      flashGrantForm();
      $("grant-role").focus();
    }
  });

  // ---- audit -----------------------------------------------------------------
  // The API returns the latest `limit` entries (default 100, server caps at
  // 500). We pull the cap once, then page through the result client-side —
  // no offset round-trips. The note flags whether the log was truncated at
  // the cap (older rows exist but weren't returned) or merely filtered.
  const AUDIT_LIMIT = 500;
  const auditPager = createPaginator({
    table: "audit-table", pager: "audit-pager", note: "audit-note",
    pageSize: 20, colspan: 6, emptyText: "沒有符合的紀錄",
    renderRow: (a) => `<tr>
      <td class="mono">${esc(new Date(a.at * 1000).toLocaleString())}</td>
      <td><code>${esc(a.actor)}</code></td>
      <td><code>${esc(a.action)}</code></td>
      <td>${esc(a.target_type)}:${esc(a.target_id)}</td>
      <td>${esc(a.product_id || "")}</td>
      <td><code>${esc(JSON.stringify(a.detail || ""))}</code></td>
    </tr>`,
  });

  // Rebuild the actor / action filter dropdowns from a row set, preserving
  // the current selection. Only called on an UNFILTERED fetch, so the menus
  // always list the full set of values, not just those of the active filter.
  function fillFilterSelect(sel, values, allLabel) {
    const cur = sel.value;
    sel.innerHTML = `<option value="">${allLabel}</option>`
      + [...new Set(values)].filter(Boolean).sort()
          .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }

  async function refreshAudit() {
    const q = new URLSearchParams({ limit: String(AUDIT_LIMIT) });
    const actor = $("audit-actor").value;
    const action = $("audit-action").value;
    if (actor) q.set("actor", actor);
    if (action) q.set("action", action);
    const data = await jget(`/api/audit?${q}`);
    setNavCount("audit", data.audit.length);
    // Refresh the dropdown option lists only from the full (unfiltered) log
    // so picking one filter never prunes the other's choices.
    if (!actor && !action) {
      fillFilterSelect($("audit-actor"), data.audit.map((a) => a.actor), "全部 actor");
      fillFilterSelect($("audit-action"), data.audit.map((a) => a.action), "全部 action");
    }
    const extra = data.audit.length >= AUDIT_LIMIT
      ? "(達上限,較舊未顯示)"
      : (actor || action) ? "(符合過濾)" : "";
    // A new filter/refresh jumps back to page 1.
    auditPager.setRows(data.audit, { extra, resetPage: true });
  }
  // Dropdowns apply immediately; the button stays as an explicit refresh.
  $("audit-actor").onchange = refreshAudit;
  $("audit-action").onchange = refreshAudit;
  $("audit-refresh").onclick = refreshAudit;

  // ---- boot -----------------------------------------------------------------
  (async () => {
    setupNav();
    try {
      products = (await jget("/api/products")).products;
    } catch (_) { products = []; }
    await refreshCustomers();
    reconcileGrantForm();   // lock the form to a valid initial combo
    await refreshGrants();
    await refreshAudit();
  })();
})();
