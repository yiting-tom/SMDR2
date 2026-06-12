// Shared top-nav: identity chip + logout + cross-page link, on both the
// dashboard (/) and the admin console (/admin). Loaded as a classic
// script by both pages so the header is identical everywhere. csrf.js
// (loaded first) wraps fetch and injects the CSRF token on the logout
// POST — no token handling here.
//
// The page declares its identity with <body data-page="dashboard|admin">;
// this script fills #topnav accordingly:
//   - dashboard + admin role  → a 管理 (admin console) link
//   - admin page              → a ← 產品列表 back link
//   - always                  → user chip (name · userid · role tag)
//   - oidc session            → 登出 button (hidden in bypass dev mode)
(function () {
  const nav = document.getElementById("topnav");
  if (!nav) return;
  const page = document.body.dataset.page || "";

  fetch("/api/me", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((me) => {
      if (!me) return;
      const frag = document.createDocumentFragment();

      if (page === "admin") {
        frag.appendChild(makeLink("/", "← 產品列表", "回產品列表"));
      } else if (me.is_admin) {
        frag.appendChild(makeLink("/admin", "管理", "Customers / 權限 / Audit(admin)"));
      }

      // The corporate JWT `description` claim is "<uid>, <中文名>, <英文名>"
      // — show it verbatim when present; fall back to name / userid.
      const chip = document.createElement("span");
      chip.className = "user-chip";
      const strong = document.createElement("strong");
      strong.textContent = me.description || me.name || me.userid;
      strong.title = me.description || me.userid;
      chip.appendChild(strong);
      const role = me.is_admin
        ? "admin"
        : (me.grants && me.grants.length ? "member" : "viewer");
      const tag = document.createElement("span");
      tag.className = "role-tag";
      tag.textContent = role;
      chip.appendChild(tag);
      frag.appendChild(chip);

      // Only an OIDC session can be logged out; bypass dev mode has none.
      if (me.source === "session") {
        const out = document.createElement("button");
        out.type = "button";
        out.className = "logout-btn";
        out.textContent = "登出";
        out.title = "結束此 session";
        out.addEventListener("click", async () => {
          out.disabled = true;
          try {
            await fetch("/auth/logout", { method: "POST" });
          } finally {
            location.href = "/";
          }
        });
        frag.appendChild(out);
      }

      nav.appendChild(frag);
    })
    .catch(() => {});

  function makeLink(href, text, title) {
    const a = document.createElement("a");
    a.className = "topnav-link";
    a.href = href;
    a.textContent = text;
    a.title = title;
    return a;
  }
})();
