/**
 * Auth — login / register / session gate for the desktop app.
 * Token + username are kept in localStorage. All API calls made
 * through UI.api automatically carry the Bearer token.
 */
window.Auth = (function () {
  const TOKEN_KEY = "mf_token";
  const USER_KEY = "mf_user";

  /* Signalled once we know whether a stored session is valid. app.js holds the
   * first page load until this settles, because every page fetches its data at
   * the top level of its inline script and a fetch made before the token is
   * known returns 401 that UI.api() silently swallows - the user just sees an
   * empty page until they press Refresh. app.js owns a safety timeout so a
   * failure here can never leave the app stuck on a blank screen. */
  let readyResolve = null;
  let ready = false;
  const readyPromise = new Promise((res) => { readyResolve = res; });

  function signalReady() {
    if (ready) return;
    ready = true;
    if (readyResolve) readyResolve();
    if (window.App && window.App.signalAuthReady) window.App.signalAuthReady();
  }

  function whenReady() {
    return readyPromise;
  }

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function getUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch (e) {
      return null;
    }
  }

  function saveSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function authHeaders() {
    const t = getToken();
    return t ? { Authorization: "Bearer " + t } : {};
  }

  async function fetchMe() {
    const res = await fetch("/auth/me", { headers: authHeaders() });
    if (!res.ok) throw new Error("session");
    return res.json();
  }

  async function doLogin(username, password) {
    const url = "/auth/login?username=" + encodeURIComponent(username) +
      "&password=" + encodeURIComponent(password);
    const res = await fetch(url, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(window.UI.formatError(data, "Login failed"));
    const meRes = await fetch("/auth/me", {
      headers: { Authorization: "Bearer " + data.access_token },
    });
    const me = meRes.ok ? await meRes.json() : { username: username };
    saveSession(data.access_token, me);
    return me;
  }

  async function doRegister(payload) {
    const res = await fetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(window.UI.formatError(data, "Registration failed"));
    return doLogin(payload.username, payload.password);
  }

  function applyRoleClass(user) {
    document.body.classList.remove("role-admin", "role-manager", "role-data_entry", "role-viewer", "role-challan_entry");
    const role = user && user.role ? String(user.role) : "";
    if (role) document.body.classList.add("role-" + role);
  }

  function showApp(user) {
    // Did we arrive here from a signed-out state? If so the page that is
    // currently on screen was loaded without a token, so its data fetch failed
    // silently and it is showing empty. Reload it now that we are signed in.
    // On the session-restore path the first load has not happened yet (app.js
    // waits on whenReady), so there is nothing to reload and we must not.
    const wasSignedOut = !getToken() || !getUser();
    document.getElementById("auth-screen").style.display = "none";
    document.body.classList.remove("logged-out");
    applyRoleClass(user);
    const info = document.getElementById("user-info");
    if (info && user) {
      const name = user.full_name || user.username || "User";
      const role = (user.role || "").replace("_", " ");
      info.textContent = name + (role ? " (" + role + ")" : "");
      info.title = "Logged in as " + (user.username || name);
    }
    // Show test environment badge if window title has [TEST] prefix
    const badge = document.getElementById("env-badge");
    if (badge) {
      const isTest = document.title.startsWith("[TEST]");
      if (isTest) {
        badge.textContent = "Test Environment";
        badge.style.display = "inline-block";
      } else {
        badge.style.display = "none";
      }
    }
       const out = document.getElementById("logout-btn");
       if (out) out.style.display = "";
       const sw = document.getElementById("switch-user-btn");
       if (sw) sw.style.display = "";
       if (window.App && window.App.refreshReportNav) window.App.refreshReportNav();
       // The backup dot can only answer /backup/records with a token: a
       // sign-in must re-run it, or the footer keeps the pre-login "—".
       if (window.App && window.App.refreshBackupStatus) window.App.refreshBackupStatus();
       // A sign-in always starts with Editor Mode off; reflect the new token.
       if (window.App && window.App.refreshEditorMode) {
         window.App.refreshEditorMode(!!(window.App.isEditorModeOn && window.App.isEditorModeOn()));
       }
       if (wasSignedOut && window.App && window.App.refreshCurrentPage) {
         // Deferred a tick so the role classes above are applied first.
         setTimeout(() => {
           try { window.App.refreshCurrentPage(); }
           catch (e) { console.warn("post-login reload failed", e); }
         }, 0);
       }
       signalReady();
     }

  function showLogin() {
    document.getElementById("auth-screen").style.display = "flex";
    document.body.classList.add("logged-out");
    document.body.classList.remove("role-admin", "role-manager", "role-data_entry", "role-viewer");
    const out = document.getElementById("logout-btn");
    if (out) out.style.display = "none";
    const sw = document.getElementById("switch-user-btn");
    if (sw) sw.style.display = "none";
    // Land the caret in the username box so the first thing typed goes
    // straight in - on launch and after a sign-out, without a click. Deferred
    // one tick because the screen was display:none a moment ago and WebView2
    // drops a focus() made before it is laid out.
    const user = document.getElementById("login-username");
    if (user) setTimeout(() => { try { user.focus(); } catch (e) {} }, 0);
  }

  async function init() {
    bindTabs();
    bindForms();
    const logoutBtn = document.getElementById("logout-btn");
    if (logoutBtn) logoutBtn.addEventListener("click", logout);
    // Restore session if the stored token is still valid
    if (getToken()) {
      try {
        const me = await fetchMe();
        saveSession(getToken(), me);
        // A restored session never hits /auth/login, so record its start:
        // session-scoped visibility (challan_entry) keys off the newest
        // login audit row. Never blocks startup.
        try {
          await fetch("/auth/session", { method: "POST", headers: authHeaders() });
        } catch (e) { /* visibility falls back, app still starts */ }
        showApp(me);
        return;
      } catch (e) {
        clearSession();
      }
    }
    showLogin();
    signalReady();
  }

  function bindTabs() {
    const loginTab = document.getElementById("tab-login");
    const regTab = document.getElementById("tab-register");
    if (!loginTab || !regTab) return;
    loginTab.addEventListener("click", () => switchTab("login"));
    regTab.addEventListener("click", () => switchTab("register"));
  }

  function switchTab(which) {
    const loginTab = document.getElementById("tab-login");
    const regTab = document.getElementById("tab-register");
    const loginForm = document.getElementById("login-form");
    const regForm = document.getElementById("register-form");
    const isLogin = which === "login";
    loginTab.classList.toggle("active", isLogin);
    regTab.classList.toggle("active", !isLogin);
    loginForm.style.display = isLogin ? "block" : "none";
    regForm.style.display = !isLogin ? "block" : "none";
    setAuthMsg("");
    // Coming back from Register: the same "type straight away" expectation.
    if (isLogin) {
      const user = document.getElementById("login-username");
      if (user) setTimeout(() => { try { user.focus(); } catch (e) {} }, 0);
    }
  }

  function setAuthMsg(text, isError) {
    const el = document.getElementById("auth-msg");
    if (!el) return;
    el.textContent = text || "";
    el.style.color = isError ? "var(--color-danger)" : "var(--color-text-muted)";
  }

  function bindForms() {
    const loginForm = document.getElementById("login-form");
    if (loginForm) {
      loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = document.getElementById("login-btn");
        setAuthMsg("Signing in…");
        btn.disabled = true;
        try {
          const me = await doLogin(
            document.getElementById("login-username").value.trim(),
            document.getElementById("login-password").value
          );
          setAuthMsg("");
          loginForm.reset();
          showApp(me);
          if (window.App) window.App.navigate();
          if (window.UI) window.UI.notify("Welcome, " + (me.full_name || me.username || ""), "success");
        } catch (err) {
          setAuthMsg(err.message || "Login failed", true);
        } finally {
          btn.disabled = false;
        }
      });
    }
    const regForm = document.getElementById("register-form");
    if (regForm) {
      regForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = document.getElementById("reg-btn");
        const pw = document.getElementById("reg-password").value;
        if (pw.length < 6) {
          setAuthMsg("Password must be at least 6 characters", true);
          return;
        }
        setAuthMsg("Creating account…");
        btn.disabled = true;
        try {
          const me = await doRegister({
            username: document.getElementById("reg-username").value.trim(),
            password: pw,
            full_name: document.getElementById("reg-fullname").value.trim() || null,
            email: document.getElementById("reg-email").value.trim() || null,
            role: document.getElementById("reg-role").value,
          });
          setAuthMsg("");
          regForm.reset();
          showApp(me);
          if (window.App) window.App.navigate();
          if (window.UI) window.UI.notify("Account created. Welcome!", "success");
        } catch (err) {
          setAuthMsg(err.message || "Registration failed", true);
        } finally {
          btn.disabled = false;
        }
      });
    }
  }

    function logout() {
      clearSession();
      // Stop any background refresh so a signed-out timer cannot keep firing
      // API calls with no token.
      if (window.App && window.App.stopAutoRefresh) window.App.stopAutoRefresh();
      if (window.UI) window.UI.notify("Signed out", "info");
      showLogin();
    }

  // Global safety net: every same-origin fetch() call automatically carries
  // the login token (all pages use raw fetch for saves/uploads/deletes).
  function patchFetch() {
    if (window.fetch.__mfPatched) return;
    const origFetch = window.fetch.bind(window);
    window.fetch = function (url, opts) {
      try {
        const u = typeof url === "string" ? url : (url && url.url) || "";
        const sameOrigin = typeof u === "string" &&
          (u.startsWith("/") || u.startsWith(window.location.origin));
        if (sameOrigin) {
          opts = opts || {};
          const headers = new Headers(opts.headers || {});
          if (!headers.has("Authorization")) {
            const t = getToken();
            if (t) headers.set("Authorization", "Bearer " + t);
          }
          opts = Object.assign({}, opts, { headers });
        }
      } catch (e) { /* never break the call */ }
      return origFetch(url, opts);
    };
    window.fetch.__mfPatched = true;
  }
  patchFetch();

    return { init, logout, getToken, getUser, authHeaders, showApp, showLogin, saveSession,
             whenReady, signalReady };
  })();

window.addEventListener("DOMContentLoaded", () => window.Auth.init());
