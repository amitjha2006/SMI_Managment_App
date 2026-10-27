/**
 * App Router — Vanilla JS single-page router
 */

/* How long the first page load waits for auth.js to report whether a stored
   session is valid. File scope, not IIFE scope, because the DOMContentLoaded
   handler below lives outside the IIFE. It is a safety valve: the gate should
   always be released by auth.js in a few hundred ms, and if auth.js is missing
   or broken we boot anyway rather than sit on a blank screen. */
const AUTH_GATE_MS = 3000;

window.App = (function () {
  const routes = {
    "/dashboard": "/frontend/pages/dashboard.html",
    "/alerts": "/frontend/pages/alerts.html",
    "/customers": "/frontend/pages/customers.html",
    "/suppliers": "/frontend/pages/suppliers.html",
    "/products": "/frontend/pages/inventory.html",
    "/raw-materials": "/frontend/pages/raw-materials.html",
    "/invoices": "/frontend/pages/invoices.html",
    "/challans": "/frontend/pages/challans.html",
    "/followup": "/frontend/pages/followup.html",
    "/production": "/frontend/pages/production.html",
    "/production-history": "/frontend/pages/production-history.html",
    "/expenses": "/frontend/pages/expenses.html",
    "/employees": "/frontend/pages/employees.html",
    "/payroll": "/frontend/pages/payroll.html",
    "/import": "/frontend/pages/import.html",
    "/reports": "/frontend/pages/reports.html",
    "/analysis": "/frontend/pages/analysis.html",
    "/raw-material-summary": "/frontend/pages/raw-material-summary.html",
    "/purchase-summary": "/frontend/pages/purchase-summary.html",
    "/payment-summary": "/frontend/pages/payment-summary.html",
    "/monthly-sales": "/frontend/pages/monthly-sales.html",
    "/backup": "/frontend/pages/backup.html",
    "/users": "/frontend/pages/users.html",
    "/audit": "/frontend/pages/audit.html",
    "/help": "/frontend/pages/help.html",
    "/settings": "/frontend/pages/settings.html",
  };

  const defaultRoute = "/dashboard";

  const views = {};

  // ---- Editor Mode -------------------------------------------------------
  // A second gate in front of role permissions. The server enforces it, so
  // this is only the switch and the visual state; the authority lives in the
  // signed token's `editor` claim.
  function editorModeOn() {
    try {
      const token = (window.Auth && window.Auth.getToken()) || "";
      const part = token.split(".")[1];
      if (!part) return false;
      const pad = part.replace(/-/g, "+").replace(/_/g, "/");
      const claims = JSON.parse(decodeURIComponent(
        atob(pad + "=".repeat((4 - (pad.length % 4)) % 4))
          .split("").map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2)).join("")));
      return !!claims.editor;
    } catch (e) {
      return false;
    }
  }

  // ---- Challan Entry profile ---------------------------------------------
  // A challan-only login: the sidebar shows Challans and nothing else, every
  // other route redirects back, and the editor switch is hidden (creating
  // drafts needs no editor mode). The server enforces the same boundary, so
  // this is only the visible half.
  function currentRole() {
    try {
      const me = window.Auth ? window.Auth.getUser() : null;
      return String((me && me.role) || "");
    } catch (e) { return ""; }
  }

  function isChallanEntry() { return currentRole() === "challan_entry"; }

  function applyChallanEntryNav() {
    const on = isChallanEntry();
    document.querySelectorAll(".nav-link").forEach((a) => {
      if (a.getAttribute("data-page") !== "challans") {
        a.style.display = on ? "none" : "";
      }
    });
    document.querySelectorAll("#nav-list li.nav-section").forEach((li) => {
      li.style.display = on ? "none" : "";
    });
    const editorBtn = document.getElementById("editor-toggle");
    if (editorBtn) editorBtn.style.display = on ? "none" : "";
  }

  function applyEditorMode(on) {
    document.body.classList.toggle("editor-on", !!on);
    document.body.classList.toggle("editor-off", !on);
    const btn = document.getElementById("editor-toggle");
    if (btn) {
      btn.textContent = on ? "🔓 Editor Mode: ON" : "🔒 Editor Mode: OFF";
      btn.classList.toggle("editor-on", !!on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.title = on
        ? "Editor Mode is on - records can be edited or deleted. Click to switch off."
        : "Editor Mode is off. Nothing can be edited or deleted until you switch it on.";
    }
  }

  async function toggleEditorMode() {
    const btn = document.getElementById("editor-toggle");
    const next = !editorModeOn();
    if (btn) btn.disabled = true;
    try {
      const res = await window.UI.api("/auth/editor-mode", {
        method: "POST",
        body: { enabled: next },
      });
      const user = window.Auth ? window.Auth.getUser() : null;
      if (res && res.access_token) window.Auth.saveSession(res.access_token, user);
      applyEditorMode(next);
      if (window.UI) {
        window.UI.notify(
          next ? "Editor Mode ON - editing and deleting are enabled."
               : "Editor Mode OFF - records are protected from changes.",
          next ? "warning" : "success");
      }
    } catch (e) {
      if (window.UI) window.UI.notify(e.message || "Could not switch Editor Mode", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function init() {
    applyTheme(currentTheme());
    // The server copy wins once the session is known, so the choice
    // follows the user across devices.
    whenAuthReady().then(syncThemeFromServer);
    // Paper stays light: a dark printout wastes toner and washes out.
    window.addEventListener("beforeprint", () => {
      if (document.documentElement.dataset.theme === "dark") {
        document.documentElement.dataset.printTheme = "dark";
        delete document.documentElement.dataset.theme;
      }
    });
    window.addEventListener("afterprint", () => {
      if (document.documentElement.dataset.printTheme === "dark") {
        document.documentElement.dataset.theme = "dark";
        delete document.documentElement.dataset.printTheme;
      }
    });
    bindNav();
    bindMenuToggle();
    bindSectionToggles();
    const refreshBtn = document.getElementById("refresh-page");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshBtn.disabled = true;
        try { refreshCurrentPage(); }
        finally { setTimeout(() => { refreshBtn.disabled = false; }, 400); }
      });
    }
    const editorBtn = document.getElementById("editor-toggle");
    if (editorBtn) editorBtn.addEventListener("click", toggleEditorMode);
    // Reflect the token on every load. A fresh sign-in carries no `editor`
    // claim, so this always starts OFF.
    applyEditorMode(editorModeOn());
    applyChallanEntryNav();
    bindGlobalSearch();
    window.addEventListener("hashchange", navigate);
    document.addEventListener("keydown", shortcuts);
    initIdleLock();
    navigate();
    refreshBackupStatus();
    refreshAlertBadge();
  }

  // Shared-PC auto-lock: sign out after N idle minutes (Settings -> Session
  // Lock, 0 = off). Any click/keypress/mousemove counts as activity. The
  // timeout itself is readable by every role; only admins may change it.
  let lockMinutes = 15;
  let lastActivity = Date.now();

  async function refreshLockSetting() {
    try {
      if (!window.Auth || !window.Auth.getToken()) return;
      const d = window.UI ? await window.UI.api("/settings/session") : null;
      if (d && d.lock_minutes != null) lockMinutes = parseInt(d.lock_minutes) || 0;
    } catch (e) { /* keep the last known value; never break startup */ }
  }

  function initIdleLock() {
    ["click", "keydown", "mousemove"].forEach(ev =>
      document.addEventListener(ev, () => { lastActivity = Date.now(); },
        { passive: true, capture: true }));
    refreshLockSetting();
    setInterval(() => {
      try {
        if (!lockMinutes || !window.Auth || !window.Auth.getToken()) return;
        if (Date.now() - lastActivity > lockMinutes * 60000) {
          lastActivity = Date.now();
          window.Auth.logout();
          if (window.UI) window.UI.notify(
            "Locked after " + lockMinutes + " idle minutes. Sign in again to continue.", "warning", 8000);
        }
      } catch (e) { /* a lock check must never break the app */ }
    }, 30000);
  }

  // Counter-staff shortcuts: N new challan, P record production,
  // / focuses page search, ? lists them. Never fires while typing,
  // with modifiers, or signed out.
  function shortcuts(e) {
    if (!e || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if (document.body.classList.contains("logged-out")) return;
    if (e.key === "/") {
      e.preventDefault();
      const s = document.querySelector("#main-content input[type=search]");
      if (s) s.focus();
      else if (window.UI) window.UI.notify("No search box on this page", "info");
    } else if (e.key === "?") {
      if (window.UI) window.UI.infoDialog("Keyboard shortcuts", "N — new challan\nP — record production\n/ — focus page search\n? — this list");
    } else if (e.key === "n" || e.key === "N") {
      window.location.hash = "#/challans?new=1";
    } else if (e.key === "p" || e.key === "P") {
      window.location.hash = "#/production?new=1";
    }
  }

  async function refreshAlertBadge() {
    const badge = document.getElementById("alert-badge");
    if (!badge) return;
    try {
      if (!window.Auth || !window.Auth.getToken()) {
        badge.style.display = "none";
        return;
      }
      let seen = 0;
      try { seen = parseInt(localStorage.getItem("mf_alerts_seen") || "0") || 0; } catch (e) {}
      const d = await window.UI.api("/notifications/unread?since_id=" + seen);
      if (d && d.unread > 0) {
        badge.textContent = d.unread > 99 ? "99+" : d.unread;
        badge.style.display = "";
      } else {
        badge.style.display = "none";
      }
    } catch (e) {
      badge.style.display = "none";
    }
  }

  async function refreshBackupStatus() {
    const el = document.getElementById("last-backup");
    if (!el) return;
    const dot = document.getElementById("backup-dot");
    const paint = (color) => {
      if (!dot) return;
      dot.classList.remove("dot-green", "dot-amber", "dot-red");
      dot.classList.add(color);
    };
    try {
      const data = window.UI ? await window.UI.api("/backup/records") : [];
      if (data && data.length) {
        const dt = new Date(data[0].backup_date);
        const today = new Date();
        const days = Math.floor((today - dt) / 86400000);
        const sameDay = dt.toDateString() === today.toDateString();
        const yest = new Date(today);
        yest.setDate(today.getDate() - 1);
        el.textContent = sameDay ? "Today" :
          (dt.toDateString() === yest.toDateString() ? "Yesterday" :
            dt.toLocaleDateString("en-IN", { day: "numeric", month: "short" }));
        // Backup health: green ≤1 day, amber 2–3 days, red beyond that.
        paint(days <= 1 ? "dot-green" : (days <= 3 ? "dot-amber" : "dot-red"));
        if (days > 3) el.textContent += ` (${days}d ago — back up now)`;
      } else {
        el.textContent = "Never";
        paint("dot-red");
      }
    } catch (e) {
      el.textContent = "—";
    }
  }

  function navigate() {
    const raw = window.location.hash ? window.location.hash.replace("#", "") : defaultRoute;
    const clean = raw.split("?")[0] || "/";
    const seg = clean.split("/").pop() || "dashboard";
    const pagePath = routes[raw] || routes[clean] || routes["/" + seg] || routes[defaultRoute];
    const pageName = clean === "/" ? "dashboard" : seg;

    // Challan Entry profiles live on the Challans page alone; anything else
    // bounces straight back (the server refuses those routes regardless).
    applyChallanEntryNav();
    if (isChallanEntry() && pageName !== "challans") {
      if (window.UI) window.UI.notify("Challan Entry profiles can only use Challans", "error");
      if (window.location.hash !== "#/challans") window.location.hash = "#/challans";
      return;
    }

    // The old combined Inventory page is split: raw materials live on
    // #/raw-materials, products on #/products. Saved links keep working:
    // kind=raw (or a bare link) lands on raw materials, kind=product and
    // ?new=1 (Add Product) land on products, query preserved.
    if (clean === "/inventory") {
      const query = raw.includes("?") ? raw.slice(raw.indexOf("?")) : "";
      const kind = (raw.match(/[?&]kind=([^&]*)/) || [])[1] || "";
      const target = (kind === "product" || /[?&]new=1/.test(raw)) ? "#/products" : "#/raw-materials";
      if (window.location.hash !== target + query) window.location.hash = target + query;
      return;
    }

    // Display titles that differ from the route segment (routes stay short).
    const PAGE_TITLES = { invoices: "Invoices", "payment-summary": "Payment Received", "raw-materials": "Raw Materials", "raw-material-summary": "Raw Material Summary", "purchase-summary": "Purchase Summary", "production-history": "Production History" };
    document.getElementById("page-title").textContent =
      PAGE_TITLES[pageName] || (pageName.charAt(0).toUpperCase() + pageName.slice(1));
    // Admin-only pages
    if ((pageName === "users" || pageName === "backup") && window.Auth) {
      const me = window.Auth.getUser();
      const role = me && me.role ? String(me.role) : "";
      if (role !== "admin") {
        if (window.UI) window.UI.notify("Admin access required", "error");
        window.location.hash = "#/dashboard";
        return;
      }
    }
    document.querySelectorAll(".nav-link").forEach((a) => {
      a.classList.remove("active");
      if (a.getAttribute("data-page") === (pageName || "dashboard")) a.classList.add("active");
    });

    loadPage(pagePath || routes[defaultRoute]);
    refreshAlertBadge();
    syncAutoRefresh(pageName);
  }

  async function loadPage(url) {
    const container = document.getElementById("main-content");
    try {
      // Bypass browser cache so edited pages always load fresh
      const cacheBuster = (url.includes("?") ? "&" : "?") + "t=" + Date.now();
      const res = await fetch(url + cacheBuster, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const html = await res.text();
      container.innerHTML = html;

      // Execute any inline scripts from the loaded HTML (innerHTML ignores them).
      // Each script is isolated: one failing script must not abort its
      // siblings or bubble up and trip the error card below.
      container.querySelectorAll("script").forEach((oldScript) => {
        try {
          const newScript = document.createElement("script");
          if (oldScript.src) {
            newScript.src = oldScript.src;
          } else {
            newScript.textContent = oldScript.textContent;
          }
          container.appendChild(newScript);
        } catch (e) {
          console.warn("page script failed for " + url, e);
        }
      });

      // Call initPage if defined
      const initScript = document.createElement("script");
      initScript.textContent = `
        (function(){
          if(typeof window.initPage === 'function') window.initPage();
          if(typeof previewImport === 'function' && document.getElementById('import-form')) {
            // Import functions are already available globally from inline script
          }
        })();
      `;
      container.appendChild(initScript);

      // Start each page at the top. Swapping innerHTML does not move the
      // scroll position, so arriving from a long page (Challans, Production,
      // Audit) used to leave the new view rendered far down the screen with
      // its heading off-screen. Instant, not smooth, so there is no visible
      // animation on every navigation.
      if (window.scrollY !== 0 || document.documentElement.scrollTop !== 0) {
        window.scrollTo(0, 0);
      }
    } catch (e) {
      container.innerHTML = `<div class="card"><h3>Error</h3><p>Failed to load page: ${e.message}</p></div>`;
    }
  }

  function bindNav() {
    document.querySelectorAll(".nav-link").forEach((a) => {
      a.addEventListener("click", (e) => {
        // Let hash change handle it
      });
    });
  }

  // Global search: one box across customers, challans, invoices, products
  // and suppliers. Debounced; Enter jumps to the first hit, Esc closes.
  // Result links are list deep-links (?search=), honored by each page's own
  // search box, so no detail-open plumbing is needed per record type.
  let gsTimer = null;
  function bindGlobalSearch() {
    const input = document.getElementById("global-search");
    const panel = document.getElementById("global-search-results");
    if (!input || !panel || input.dataset.gsBound === "1") return;
    input.dataset.gsBound = "1";
    const escH = s => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const goLink = (page, term, extra) =>
      "#/" + page + "?search=" + encodeURIComponent(term) + (extra || "");
    async function run() {
      const q = (input.value || "").trim();
      if (q.length < 2) { panel.style.display = "none"; panel.innerHTML = ""; return; }
      let data;
      try {
        data = await window.UI.api("/search?q=" + encodeURIComponent(q));
      } catch (e) { panel.style.display = "none"; return; }
      const groups = [
        ["Customers", (data.customers || []).map(r => ({
          label: r.name, sub: r.company || r.contact || "",
          go: goLink("customers", r.name),
        }))],
        ["Challans", (data.challans || []).map(r => ({
          label: r.number, sub: (r.customer || "") + (r.status ? " · " + r.status : ""),
          go: goLink("challans", r.number),
        }))],
        ["Invoices", (data.invoices || []).map(r => ({
          label: r.number, sub: r.supplier || "",
          go: goLink("invoices", r.number),
        }))],
        ["Products", (data.products || []).map(r => ({
          label: r.name, sub: "",
          go: goLink("inventory", r.name, "&kind=product"),
        }))],
        ["Suppliers", (data.suppliers || []).map(r => ({
          label: r.name, sub: r.company || r.contact || "",
          go: goLink("suppliers", r.name),
        }))],
        // Payments open directly: the hit jumps to the parent record and
        // highlights that exact receipt/payment row.
        ["Payments", (data.payments || []).map(r => ({
          label: r.code,
          sub: [(r.customer || r.supplier || ""), (r.amount ? "₹" + r.amount : ""),
                (r.date && window.UI ? window.UI.fmtDay(r.date) : "")].filter(Boolean).join(" · "),
          go: r.kind === "receipt"
            ? "#/challans?challan=" + r.challan_id + "&payment=" + r.id
            : "#/invoices?invoice=" + r.invoice_id + "&payment=" + r.id,
        }))],
        // Same description can repeat (monthly rent): the sub-line carries
        // date, amount and category so the right row is picked before opening.
        ["Expenses", (data.expenses || []).map(r => ({
          label: r.description,
          sub: [(r.date && window.UI ? window.UI.fmtDay(r.date) : ""),
                (r.amount ? "₹" + r.amount : ""), (r.category || "")].filter(Boolean).join(" · "),
          go: goLink("expenses", r.description),
        }))],
      ].filter(([, items]) => items.length);
      if (!groups.length) {
        panel.innerHTML = `<div style="padding:10px 12px; font-size:13px; color:var(--color-text-muted);">No matches.</div>`;
        panel.style.display = "block";
        return;
      }
      panel.innerHTML = groups.map(([title, items]) =>
        `<div style="padding:6px 12px 2px; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--color-text-muted);">${title}</div>` +
        items.map(it =>
          `<a href="${it.go}" style="display:block; padding:6px 12px; font-size:13px; text-decoration:none; color:inherit;" data-goto="${escH(it.go)}">${escH(it.label)}${it.sub ? ` <span style="color:var(--color-text-muted);">— ${escH(it.sub)}</span>` : ""}</a>`
        ).join("")).join("");
      panel.style.display = "block";
      panel.querySelectorAll("a").forEach(a => a.addEventListener("click", () => {
        panel.style.display = "none";
        input.value = "";
      }));
    }
    input.addEventListener("input", () => {
      clearTimeout(gsTimer);
      gsTimer = setTimeout(run, 250);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { panel.style.display = "none"; input.blur(); }
      else if (e.key === "Enter") {
        const first = panel.querySelector("a");
        if (first && panel.style.display === "block") {
          e.preventDefault();
          window.location.hash = first.dataset.goto;
          panel.style.display = "none";
          input.value = "";
        }
      }
    });
    document.addEventListener("click", (e) => {
      const wrap = document.getElementById("global-search-wrap");
      if (wrap && !wrap.contains(e.target)) panel.style.display = "none";
    });
  }

  function onReportsHash() {
    return (window.location.hash || "").split("?")[0] === "#/reports";
  }

  function stash(key, value) {
    try { sessionStorage.setItem(key, value); } catch (e) {}
  }

  async function refreshReportNav() {
    const box = document.getElementById("nav-custom-reports");
    if (!box) return;
    let list = [];
    try {
      if (window.UI) list = await window.UI.api("/reports/saved");
    } catch (e) { /* e.g. not logged in yet — leave empty */ }
    const esc = s => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    box.innerHTML = (list || []).map(r =>
      `<li style="display:flex; align-items:center; gap:2px;">` +
      `<a href="#/reports" class="nav-link" data-page="reports" data-run-report="${r.id}" style="flex:1;" title="Run ${esc(r.name)}">` +
      `${r.kind === "link" ? "🔗 " : "📄 "}${esc(r.name)}</a>` +
      (r.kind === "link" ? `` :
        `<button type="button" class="nav-mini-btn" data-edit-report="${r.id}" title="Edit">✎</button>`) +
      `<button type="button" class="nav-mini-btn" data-del-report="${r.id}" title="Delete">✕</button>` +
      `</li>`
    ).join("") +
    `<li><a href="#/reports" class="nav-link nav-sub-new" data-page="reports" data-run-report="new" title="Create a new report">＋ New Report</a></li>`;
    box.querySelectorAll("a[data-run-report]").forEach(a => {
      a.addEventListener("click", (e) => {
        const id = a.dataset.runReport;
        stash("run-report", id);
        if (onReportsHash()) {
          e.preventDefault();
          if (id === "new") {
            const el = document.getElementById("cr-source");
            if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
          } else if (typeof window.runSavedReport === "function") {
            window.runSavedReport(parseInt(id, 10));
          }
        }
      });
    });
    box.querySelectorAll("button[data-edit-report]").forEach(b => {
      b.addEventListener("click", () => {
        stash("edit-report", b.dataset.editReport);
        if (onReportsHash() && typeof window.editSavedReport === "function") {
          window.editSavedReport(parseInt(b.dataset.editReport, 10));
        } else {
          window.location.hash = "#/reports";
        }
      });
    });
    box.querySelectorAll("button[data-del-report]").forEach(b => {
      b.addEventListener("click", () => {
        stash("del-report", b.dataset.delReport);
        if (onReportsHash() && typeof window.deleteSavedReport === "function") {
          window.deleteSavedReport(parseInt(b.dataset.delReport, 10));
        } else {
          window.location.hash = "#/reports";
        }
      });
    });
  }

  function bindSectionToggles() {
    const KEY = "mf_nav_collapsed";
    let collapsed = [];
    try {
      collapsed = JSON.parse(localStorage.getItem(KEY) || "[]");
    } catch (e) { collapsed = []; }
    const apply = (section, hide) => {
      if (section === "custom-reports") {
        const ul = document.getElementById("nav-custom-reports");
        if (ul) ul.style.display = hide ? "none" : "";
      } else {
        document.querySelectorAll(`#nav-list li[data-group="${section}"]`).forEach(li => {
          li.classList.toggle("nav-collapsed-child", hide);
        });
      }
      const btn = document.querySelector(`.nav-section-toggle[data-section="${section}"]`);
      if (btn) {
        btn.setAttribute("aria-expanded", String(!hide));
        const chev = btn.querySelector(".chev");
        if (chev) chev.textContent = hide ? "+" : "−";
      }
    };
    collapsed.forEach(s => apply(s, true));
    function saveCollapsed(list) {
      try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) {}
    }
    document.querySelectorAll(".nav-section-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        const section = btn.dataset.section;
        const hide = btn.getAttribute("aria-expanded") === "true";
        apply(section, hide);
        let cur = [];
        try { cur = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) {}
        if (hide && !cur.includes(section)) cur.push(section);
        if (!hide) cur = cur.filter(s => s !== section);
        saveCollapsed(cur);
      });
    });
  }

  function bindMenuToggle() {
    const btn = document.getElementById("menu-toggle");
    const sidebar = document.getElementById("sidebar");
    if (!btn || !sidebar) return;
    // Labels are bare text nodes, so CSS alone would clip them mid-word:
    // the toggle swaps each link to its leading emoji (badge kept) and
    // restores the stored markup on unfold. Tooltips carry full names.
    const COLLAPSE_KEY = "mf_sidebar";
    function compactNav(on) {
      document.querySelectorAll(".nav-link").forEach((a) => {
        if (on) {
          if (a.dataset.full === undefined) {
            a.dataset.full = a.innerHTML;
            a.title = a.textContent.trim();
          }
          const badge = a.querySelector(".badge");
          const first = (a.textContent.trim().match(/^\S+/) || [""])[0];
          a.textContent = first;
          if (badge) a.appendChild(badge);
        } else if (a.dataset.full !== undefined) {
          a.innerHTML = a.dataset.full;
          delete a.dataset.full;
          a.removeAttribute("title");
        }
      });
    }
    function setCollapsed(on) {
      document.body.classList.toggle("sidebar-collapsed", on);
      try { localStorage.setItem(COLLAPSE_KEY, on ? "1" : "0"); } catch (e) {}
      if (window.innerWidth > 960) compactNav(on);
      btn.setAttribute("aria-expanded", on ? "true" : "false");
    }
    let collapsed = false;
    try { collapsed = localStorage.getItem(COLLAPSE_KEY) === "1"; } catch (e) {}
    if (collapsed && window.innerWidth > 960) setCollapsed(true);
    else btn.setAttribute("aria-expanded", sidebar.classList.contains("open"));
    btn.addEventListener("click", () => {
      if (window.innerWidth <= 960) {
        // A desktop-folded rail must not follow into mobile overlay mode.
        if (document.body.classList.contains("sidebar-collapsed")) setCollapsed(false);
        sidebar.classList.toggle("open");
        btn.setAttribute("aria-expanded", sidebar.classList.contains("open"));
        return;
      }
      setCollapsed(!document.body.classList.contains("sidebar-collapsed"));
    });
    document.addEventListener("click", (e) => {
      if (window.innerWidth <= 960 && sidebar.classList.contains("open")) {
        if (!sidebar.contains(e.target) && !btn.contains(e.target)) sidebar.classList.remove("open");
      }
    });
  }

  /**
   * Refresh the current view. Prefers the page's own loader, because those
   * re-fetch with the page's current filter state (search box, date range, the
   * "Show archived" toggle). Pages that do not publish a loader fall back to
   * re-injecting the page, which re-runs its initial load.
   */
  function refreshCurrentPage() {
    const raw = window.location.hash ? window.location.hash.replace("#", "") : defaultRoute;
    const clean = raw.split("?")[0] || "/";
    const seg = clean.split("/").pop() || "dashboard";
    const pageName = clean === "/" ? "dashboard" : seg;

    /* A page can be refreshed through whichever of these it happens to expose,
       and the order matters.
         1. AppPageRefreshers[page]  - an explicit opt-in, always wins
         2. refresh<Page>             - the dashboard's own naming
         3. load<Page>                - the naming most pages use
         4. loadPage (below)          - re-inject the whole page

       Only `load<Page>` used to be tried, and the dashboard exposes
       `refreshDashboard`, so auto-refresh silently found nothing and fell
       through to a full page re-inject on every tick - heavy, flickery, and it
       re-runs every inline script. Same trap waiting for monthly-sales
       (loadPivot), payroll (loadPayrollPreview) and users (loadPermissions).

       Re-injecting is a last resort, not a failure: it does refresh, which is
       why the symptom was "seems broken" rather than "does nothing". */
    const camel = pageName.replace(/-([a-z])/g, (m, c) => c.toUpperCase());
    const cap = camel.charAt(0).toUpperCase() + camel.slice(1);

    const explicit = window.AppPageRefreshers && window.AppPageRefreshers[pageName];
    const candidates = [];
    if (typeof explicit === "function") candidates.push([pageName + " (AppPageRefreshers)", explicit]);
    candidates.push(["refresh" + cap, window["refresh" + cap]]);
    candidates.push(["load" + cap, window["load" + cap]]);
    candidates.push(["load" + pageName, window["load" + pageName]]);

    for (const [name, fn] of candidates) {
      if (typeof fn === "function") {
        try { fn(); return name; }
        catch (e) { console.warn("refresh via " + name + " failed", e); }
      }
    }

    const pagePath = routes[raw] || routes[clean] || routes["/" + seg] || routes[defaultRoute];
    loadPage(pagePath || routes[defaultRoute]);
    return "loadPage";
  }

  /* ---------------------------------------------------------------------
   * Auth gate
   *
   * Every page calls its loaders at the top level of its inline script, so a
   * page loaded before the token is known fires its API calls unauthenticated.
   * UI.api() swallows the resulting 401, so the view simply renders empty and
   * the user is left staring at a blank dashboard until they press Refresh.
   *
   * One gate, owned here and signalled by auth.js from whichever path settles
   * first (restored session, or signed out). Only the FIRST navigation waits;
   * after that the token is already known and waiting would just add latency.
   *
   * AUTH_GATE_MS (declared at file scope) is the safety valve: if auth.js were
   * ever missing or broken the gate would never resolve and the app would hang
   * on a blank page - strictly worse than the bug being fixed. After the
   * timeout we proceed regardless.
   * ------------------------------------------------------------------- */
  let authReadyResolve = null;
  let authReady = false;
  const authReadyPromise = new Promise((res) => { authReadyResolve = res; });

  function signalAuthReady() {
    if (authReady) return;
    authReady = true;
    if (authReadyResolve) authReadyResolve();
  }

  function whenAuthReady() {
    return authReadyPromise;
  }

  /* ---------------------------------------------------------------------
   * Theme
   *
   * Light is the default; dark is an opt-in override via data-theme on
   * <html>. localStorage applies instantly (and pre-paint via the head
   * script in index.html); the server copy wins after sign-in.
   * ------------------------------------------------------------------- */
  const THEME_KEY = "mf_theme";

  function currentTheme() {
    try { return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"; }
    catch (e) { return "light"; }
  }

  function paintThemeButton() {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.textContent = document.documentElement.dataset.theme === "dark" ? "☀️ Light" : "🌙 Dark";
  }

  function applyTheme(which) {
    const theme = which === "dark" ? "dark" : "light";
    if (theme === "dark") document.documentElement.dataset.theme = "dark";
    else delete document.documentElement.dataset.theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    paintThemeButton();
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    // Persist per user; a failure keeps the local choice (a theme that
    // works here beats an error toast about looks).
    try {
      if (window.UI && window.Auth && window.Auth.getToken()) {
        window.UI.api("/api/dashboard/preferences", { method: "PUT", body: { theme: next } }).catch(() => {});
      }
    } catch (e) {}
  }

  function syncThemeFromServer() {
    try {
      if (!window.UI || !window.Auth || !window.Auth.getToken()) return;
      window.UI.api("/api/dashboard/preferences").then((p) => {
        if (p && (p.theme === "dark" || p.theme === "light")) applyTheme(p.theme);
      }).catch(() => {});
    } catch (e) {}
  }

  /* ---------------------------------------------------------------------
   * Smart auto-refresh
   *
   * Keeps the dashboard figures current without the user pressing Refresh. It
   * deliberately stays out of the way:
   *   - only the dashboard opts in, because it is read-only. Other pages have
   *     open-in-place editors (the inventory size editor, the challan detail
   *     panel) where an unrequested re-render would throw away what the user
   *     is doing.
   *   - it never fires while the tab is hidden, a modal is open, the user is
   *     typing into a field, or a fetch is already in flight.
   * Returning to the tab refreshes immediately, so a dashboard left open
   * overnight is correct the moment it is looked at.
   * ------------------------------------------------------------------- */
  const AUTO_REFRESH_PAGES = { dashboard: 60000 };
  let autoTimer = null;
  let autoRefreshInFlight = false;

  function isPageBusy() {
    return autoRefreshPauseReason() !== null;
  }

  /* Why a refresh is not happening right now, or null if it is free to fire.
     Having this separate from isPageBusy() is what lets the UI SAY why. A
     dashboard that quietly stops updating is indistinguishable from one that
     is not refreshing at all, which is exactly how a stale figure goes
     unnoticed.

     NOTE: `document.hidden` is deliberately NOT consulted here.

     It is a web-page concept, and this app is also a desktop WebView window,
     where "hidden" can be reported while the window is plainly in front of
     the user (an unfocused-but-visible window, or a host that never fires
     visibility events at all). Trusting it meant a single environmental
     quirk could switch auto-refresh off permanently, with nothing on screen
     to say why - which is the failure that produced the report this replaced.

     The cost of dropping it is one API call a minute while the window sits in
     the background, which is cheaper than showing a stale figure. The
     visibilitychange handler still refreshes the moment the tab is looked at,
     so a view left open overnight is correct on return either way. */
  function autoRefreshPauseReason() {
    const openDialog = findOpenOverlay();
    if (openDialog) return "a dialog is open";
    // Do not re-render out from under someone who is typing.
    const a = document.activeElement;
    if (a) {
      const tag = (a.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || a.isContentEditable) {
        return "a field has focus";
      }
    }
    return null;
  }

  /* Find an overlay that is actually OPEN.

     Checking only for `.modal-overlay` was not enough: overlays in this app
     are mostly written as `<div id="...-overlay" style="display:none">`, and
     that element stays in the DOM permanently, so it is found whether open or
     not - which is why the naive check both missed real dialogs and would
     block refresh forever if such an element were used on an opted-in page.
     So: find overlays by id or class, then look at whether each is visible. */
  function findOpenOverlay() {
    const sel = '.modal-overlay, [id$="-overlay"], [id*="overlay"], [id*="modal"]';
    let nodes;
    try { nodes = document.querySelectorAll(sel); }
    catch (e) { return null; }
    for (let i = 0; i < (nodes ? nodes.length : 0); i++) {
      const n = nodes[i];
      if (n.style && n.style.display === "none") continue;      // closed
      if (n.hidden) continue;
      // A closed overlay is usually also display:none via a class; treat an
      // explicit flex/grid/block style as open, and anything else as unknown
      // but present, so we err towards not refreshing over someone's dialog.
      return n;
    }
    return null;
  }

  let lastAutoRefreshAt = null;
  let lastAutoRefreshOutcome = null;

  function stopAutoRefresh() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  }

  function autoRefreshTick() {
    const paused = autoRefreshPauseReason();
    if (paused) {
      lastAutoRefreshOutcome = "paused: " + paused;
      notifyAutoRefreshStatus();
      return false;
    }
    if (autoRefreshInFlight) {
      lastAutoRefreshOutcome = "waiting for the previous refresh";
      notifyAutoRefreshStatus();
      return false;
    }
    autoRefreshInFlight = true;
    try {
      const how = refreshCurrentPage();
      lastAutoRefreshAt = new Date();
      lastAutoRefreshOutcome = "refreshed via " + how;
    }
    catch (e) {
      lastAutoRefreshOutcome = "failed: " + (e && e.message ? e.message : e);
      console.warn("auto-refresh failed", e);
    }
    finally {
      // Page loaders are fire-and-forget promises; release the guard on the
      // next tick so a slow response cannot stack up refreshes.
      setTimeout(() => {
        autoRefreshInFlight = false;
        notifyAutoRefreshStatus();
      }, 1000);
    }
    notifyAutoRefreshStatus();
    return true;
  }

  /* Lets a page render the auto-refresh state. Without this, "auto refresh is
     not working" is undiagnosable from the outside - which is precisely the
     report this came from. */
  function notifyAutoRefreshStatus() {
    if (typeof window.onAutoRefreshStatus === "function") {
      try {
        window.onAutoRefreshStatus({
          page: currentAutoRefreshPage(),
          everyMs: effectiveRefreshMs(currentAutoRefreshPage()),
          armed: !!autoTimer,
          lastRefreshAt: lastAutoRefreshAt,
          outcome: lastAutoRefreshOutcome,
        });
      } catch (e) { /* a page's own display must not break the timer */ }
    }
  }

  function currentAutoRefreshPage() {
    const raw = window.location.hash ? window.location.hash.replace("#", "") : defaultRoute;
    const clean = raw.split("?")[0] || "/";
    const seg = clean.split("/").pop() || "dashboard";
    return clean === "/" ? "dashboard" : seg;
  }

  /* Low-spec mode (dashboard Customize modal, per device — it describes
     this laptop, not the user): the dashboard tick stretches from 1 to 5
     minutes so an idle window stops churning. */
  function lowSpecMode() {
    try { return localStorage.getItem("dash_low_spec") === "1"; }
    catch (e) { return false; }
  }

  function effectiveRefreshMs(pageName) {
    const base = AUTO_REFRESH_PAGES[pageName] || 0;
    if (base && pageName === "dashboard" && lowSpecMode()) return 300000;
    return base;
  }

  function syncAutoRefresh(pageName) {
    stopAutoRefresh();
    const everyMs = effectiveRefreshMs(pageName);
    if (!everyMs) {
      notifyAutoRefreshStatus();
      return;
    }
    autoTimer = setInterval(autoRefreshTick, everyMs);
    lastAutoRefreshAt = null;
    lastAutoRefreshOutcome = null;
    notifyAutoRefreshStatus();
  }

  /* Called when the tab regains focus. Refreshes immediately if this page is
   * opted in and the user is not mid-edit, so a view left open overnight is
   * correct the instant it is looked at. */
  function shouldAutoRefreshNow(pageName) {
    if (!AUTO_REFRESH_PAGES[pageName]) return false;
    if (isPageBusy()) return false;
    autoRefreshTick();
    return true;
  }

  return { init, navigate, routes, refreshBackupStatus, refreshReportNav,
           refreshAlertBadge, refreshCurrentPage, toggleEditorMode, refreshLockSetting,
           whenAuthReady, signalAuthReady, stopAutoRefresh, syncAutoRefresh,
           shouldAutoRefreshNow, autoRefreshTick, autoRefreshPauseReason,
           toggleTheme, applyTheme,
           refreshEditorMode: applyEditorMode, isEditorModeOn: editorModeOn };
})();

// Coming back to the tab: the figures may be minutes or hours stale, so refresh
// straight away rather than waiting out the rest of the interval.
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !window.App) return;
  const raw = window.location.hash ? window.location.hash.replace("#", "") : "/dashboard";
  const clean = raw.split("?")[0] || "/";
  const seg = clean === "/" ? "dashboard" : (clean.split("/").pop() || "dashboard");
  window.App.shouldAutoRefreshNow(seg);
});

window.addEventListener("DOMContentLoaded", async () => {
  // Hold the first page load until we know whether a stored session is valid,
  // so no page fetches before the token is known. The timeout guarantees we
  // still boot if auth.js never signals.
  // NOTE: this handler sits OUTSIDE the App IIFE, so the gate helpers must be
  // reached through window.App - a bare whenAuthReady() here is a
  // ReferenceError and would stop App.init() from ever running.
  const gate = window.App.whenAuthReady();
  let released = false;
  const release = () => { if (!released) { released = true; window.App.signalAuthReady(); } };
  const timer = setTimeout(release, AUTH_GATE_MS);
  try {
    if (window.Auth && window.Auth.whenReady) {
      await Promise.race([window.Auth.whenReady(), gate]);
    } else {
      // auth.js absent: nothing will ever signal, so fall straight through.
      release();
    }
  } catch (e) {
    console.warn("auth gate failed, continuing", e);
  } finally {
    clearTimeout(timer);
  }
  release();
  window.App.init();
});
