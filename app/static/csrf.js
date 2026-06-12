// Global CSRF header injection (specs/auth-session).
//
// The BFF sets a readable `conform_csrf` cookie at login; the server
// requires its value in X-CSRF-Token on every mutating request. Rather
// than touching every fetch call site, wrap window.fetch once — loaded
// before all other app scripts. No-ops when the cookie is absent
// (bypass mode).
(function () {
  const orig = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    const method = (init.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
      const m = document.cookie.match(/(?:^|; )conform_csrf=([^;]+)/);
      if (m) {
        const headers = new Headers(init.headers || {});
        if (!headers.has("X-CSRF-Token")) {
          headers.set("X-CSRF-Token", decodeURIComponent(m[1]));
        }
        init.headers = headers;
      }
    }
    return orig.call(this, input, init);
  };
})();
