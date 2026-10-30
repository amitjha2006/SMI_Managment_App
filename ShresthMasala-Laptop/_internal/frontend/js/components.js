/**
 * Reusable frontend components / helpers
 */
window.UI = (function () {
  // Notification system - enhanced with actions and auto-dismiss
  function notify(message, type = "info", duration, actions) {
    if (duration == null) duration = type === "error" ? 8000 : 3500;
    const container = document.getElementById("toast-container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    const iconMap = { success: "OK", error: "X", warning: "!", info: "i" };
    el.innerHTML = `
      <span class="toast-icon" aria-hidden="true">${iconMap[type] || iconMap.info}</span>
      <span class="toast-message">${message}</span>
      <div class="toast-actions"></div>
      <button class="toast-close" aria-label="Dismiss">&times;</button>
    `;
    const actionsEl = el.querySelector(".toast-actions");
    if (Array.isArray(actions)) {
      actions.forEach(a => {
        const btn = document.createElement("button");
        btn.className = `btn btn-sm ${a.class || "btn-secondary"}`;
        btn.textContent = a.label;
        btn.addEventListener("click", () => {
          if (typeof a.onClick === "function") a.onClick();
          el.remove();
        });
        actionsEl.appendChild(btn);
      });
    }
    el.querySelector(".toast-close").addEventListener("click", () => el.remove());
    container.appendChild(el);
    const timer = setTimeout(() => {
      el.style.transition = "opacity 0.3s, transform 0.3s";
      el.style.opacity = "0";
      el.style.transform = "translateY(10px)";
      setTimeout(() => el.remove(), 350);
    }, duration);
    // Pause on hover
    el.addEventListener("mouseenter", () => clearTimeout(timer));
    el.addEventListener("mouseleave", () => {
      clearTimeout(timer);
      const remaining = duration; // simplified
      setTimeout(() => {
        el.style.transition = "opacity 0.3s, transform 0.3s";
        el.style.opacity = "0";
        el.style.transform = "translateY(10px)";
        setTimeout(() => el.remove(), 350);
      }, Math.min(remaining, 3500));
    });
  }

  // Turn any API error payload into a readable message.
  function formatError(data, fallback) {
    const d = data && data.detail !== undefined ? data.detail : data;
    if (d === undefined || d === null || d === "") {
      return fallback || data.message || "Request failed";
    }
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      const parts = d.map(e => {
        if (typeof e === "string") return e;
        if (e && typeof e === "object") {
          let field = "";
          if (Array.isArray(e.loc) && e.loc.length) field = String(e.loc[e.loc.length - 1]);
          return (field ? field + ": " : "") + (e.msg || e.message || JSON.stringify(e));
        }
        return String(e);
      }).filter(Boolean);
      return parts.join("; ") || fallback || "Validation failed";
    }
    if (typeof d === "object") return d.message || fallback || "Request failed";
    return String(d);
  }

  // API wrapper (same-origin to FastAPI; carries auth token when signed in)
  async function api(path, opts = {}) {
    const url = path.startsWith("http") ? path : path;
    const auth = (window.Auth && window.Auth.authHeaders) ? window.Auth.authHeaders() : {};
    const options = { ...opts };
    const b = options.body;
    const isBinary = (typeof FormData !== "undefined" && b instanceof FormData)
      || (typeof Blob !== "undefined" && b instanceof Blob)
      || (typeof URLSearchParams !== "undefined" && b instanceof URLSearchParams)
      || (typeof ArrayBuffer !== "undefined" && b instanceof ArrayBuffer)
      || ArrayBuffer.isView(b);
    if (b && typeof b === "object" && !isBinary) {
      options.body = JSON.stringify(b);
    }
    const method = (options.method || "GET").toUpperCase();
    const cache = options.cache
      || ((method === "GET" || method === "HEAD") ? "no-store" : undefined);
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...auth, ...options.headers },
      ...options,
      ...(cache ? { cache } : {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      // Proactive hint for the most common blocked-edit cause: Editor Mode is
      // off and every PUT/PATCH/DELETE needs it on (rbac_guard in main.py).
      if (response.status === 403 && ["PUT", "PATCH", "DELETE"].includes(method)) {
        const editorOff = window.App && window.App.isEditorModeOn && !window.App.isEditorModeOn();
        if (editorOff && window.UI && window.UI.notify) {
          window.UI.notify(
            "Editor Mode is off — switch it on in the header before editing or deleting records.",
            "warning", 7000);
        }
      }
      throw new Error(formatError(data, `Request failed (${response.status})`));
    }
    return data;
  }

  // Financial year starts April 1 (India). Returns FY start as a Date.
  function fyStartDate() {
    const now = new Date();
    const year = now.getMonth() + 1 >= 4 ? now.getFullYear() : now.getFullYear() - 1;
    return new Date(year, 3, 1);
  }

  // True if the given date string falls in the current financial year.
  function inCurrentFy(dateStr) {
    if (!dateStr) return false;
    return new Date(dateStr) >= fyStartDate();
  }

  // FY start as local "YYYY-MM-DD" for date inputs.
  function fyStartStr() {
    const d = fyStartDate();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }

  // Date-range preset for the "Current FY only" checkbox.
  function applyFyStart(fromId, toId) {
    const from = document.getElementById(fromId);
    if (!from) return;
    from.value = fyStartStr();
    const to = document.getElementById(toId);
    if (to) to.value = "";
  }

  // Saved filter state can carry an upper bound from an earlier FY; from > to
  // hides every row, so drop the upper bound instead of showing an empty list.
  function dropInvertedRange(fromId, toId) {
    const from = document.getElementById(fromId);
    const to = document.getElementById(toId);
    if (from && to && from.value && to.value && to.value < from.value) {
      to.value = "";
    }
  }

  // True if dateStr is within [fromStr, toStr] (inclusive, local days).
  function inDateRange(dateStr, fromStr, toStr) {
    if (!fromStr && !toStr) return true;
    if (!dateStr) return false;
    const t = new Date(dateStr).getTime();
    if (fromStr && t < new Date(fromStr + "T00:00:00").getTime()) return false;
    if (toStr && t > new Date(toStr + "T23:59:59").getTime()) return false;
    return true;
  }

  // Prefill a list search box from a ?search= deep link (global search).
  function applySearchDeepLink(boxId) {
    const m = (window.location.hash || "").match(/[?&]search=([^&]*)/);
    if (!m) return "";
    const term = decodeURIComponent(m[1].replace(/\+/g, " ")).trim();
    if (!term) return "";
    const el = document.getElementById(boxId);
    if (el) el.value = term;
    return term;
  }

  // Business date with the small created-time beneath, always India time.
  const IST_ZONE = "Asia/Kolkata";
  function asUtcDate(v) {
    // IST-native parsing (IST-everywhere rule): stored values are naive IST
    // wall time, so an offset-less value gets +05:30 rather than Z. Values
    // that already carry an offset pass through untouched; rendering in
    // Asia/Kolkata converts them to IST.
    if (v == null || v === "") return null;
    if (v instanceof Date) return v;
    const s = String(v).trim().replace(" ", "T");
    if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)) return new Date(s);
    return new Date(s + "+05:30");
  }
  // One date shape for the whole system: "05-Mar-2026".
  function fmtDay(v) {
    const d = v instanceof Date ? v : asUtcDate(v);
    if (!d || isNaN(d.getTime())) return "--";
    return d.toLocaleDateString("en-IN", {
      timeZone: IST_ZONE, day: "2-digit", month: "short", year: "numeric",
    }).replace(/ /g, "-");
  }
  function dateWithTime(dateIso, createdIso) {
    const d = asUtcDate(dateIso);
    if (!d || isNaN(d.getTime())) return "--";
    const day = fmtDay(d);
    const c = asUtcDate(createdIso);
    if (!c || isNaN(c.getTime())) return day;
    // Hide the time stamp when it's midnight (00:00:00) — no real time to show.
    if (c.getHours() === 0 && c.getMinutes() === 0 && c.getSeconds() === 0) return day;
    const time = c.toLocaleTimeString(undefined, {
      timeZone: IST_ZONE, hour: "numeric", minute: "2-digit", hour12: true,
    }).toUpperCase();
    return `${day}<br><small style="font-size:11px; color:var(--color-text-muted); white-space:nowrap;">${time} IST</small>`;
  }
  // Plain-text sibling of dateWithTime for messages and other non-HTML
  // targets (WhatsApp text, titles, clipboard): same IST instant, no tags.
  function dateTimeText(v) {
    const d = asUtcDate(v);
    if (!d || isNaN(d.getTime())) return "--";
    const day = fmtDay(d);
    const time = d.toLocaleTimeString(undefined, {
      timeZone: IST_ZONE, hour: "numeric", minute: "2-digit", hour12: true,
    }).toUpperCase();
    if (d.getHours() === 0 && d.getMinutes() === 0 && d.getSeconds() === 0) {
      return day;
    }
    return `${day}, ${time} IST`;
  }
  // Format date for reports - returns only date if time is midnight, otherwise date + time
  function fmtDateTime(v) {
    const d = asUtcDate(v);
    if (!d || isNaN(d.getTime())) return "--";
    const day = fmtDay(d);
    if (d.getHours() === 0 && d.getMinutes() === 0 && d.getSeconds() === 0) {
      return day;
    }
    const time = d.toLocaleTimeString(undefined, {
      timeZone: IST_ZONE, hour: "numeric", minute: "2-digit", hour12: true,
    }).toUpperCase();
    return `${day}, ${time} IST`;
  }

  // Authenticated file download
  async function download(url, filename) {
    const auth = (window.Auth && window.Auth.authHeaders) ? window.Auth.authHeaders() : {};
    const res = await fetch(url, { headers: auth });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(formatError(data, "Download failed"));
    }
    await downloadBlob(await res.blob(), filename);
  }
  // Save an already-built Blob
  async function downloadBlob(blob, filename) {
    try {
      const bridge = window.pywebview && window.pywebview.api && window.pywebview.api.save_file;
      if (bridge) {
        const buf = await blob.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let bin = "";
        const CHUNK = 8192;
        for (let i = 0; i < bytes.length; i += CHUNK) {
          bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
        }
        const result = await window.pywebview.api.save_file(filename || "download", btoa(bin));
        if (result && result.ok) return;
        if (result && result.cancelled) return;
        throw new Error((result && result.error) || "Save cancelled");
      }
    } catch (bridgeErr) {
      if (bridgeErr && bridgeErr.message === "Save cancelled") return;
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
        throw bridgeErr instanceof Error ? bridgeErr : new Error(String(bridgeErr));
      }
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || "download";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  // Copy text to the clipboard
  async function copyText(t) {
    const text = String(t == null ? "" : t);
    if (!text) return false;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        return !!ok;
      } catch (e2) { return false; }
    }
  }

  // Parse a pack-size name into kilograms
  function parseSizeNameToKg(text) {
    const m = String(text == null ? "" : text).trim().replace(",", ".")
      .match(/^(\d+(?:\.\d+)?)\s*(kg|kgs|kilo|kilos|g|gm|gms|gram|grams)\.?$/i);
    if (!m) return null;
    const n = parseFloat(m[1]);
    if (!(n > 0)) return null;
    const unit = m[2].toLowerCase();
    const kg = (unit === "g" || unit === "gm" || unit === "gms" ||
      unit === "gram" || unit === "grams") ? n / 1000 : n;
    return Math.round(kg * 1000000) / 1000000;
  }

  // Canonical size name for a weight
  function suggestSizeName(kg) {
    const n = parseFloat(kg) || 0;
    if (!(n > 0)) return "";
    if (n < 1) {
      const g = Math.round(n * 1000 * 1000) / 1000;
      return String(g).replace(/\.0+$/, "") + "g";
    }
    const k = Math.round(n * 1000) / 1000;
    return String(k).replace(/\.0+$/, "") + "kg";
  }

  // Keep a size-name field and its weight field in sync
  function bindSizeWeightAuto(nameEl, weightEl) {
    if (!nameEl || !weightEl) return;
    if (nameEl.dataset.sizeweight === "1") return;
    nameEl.dataset.sizeweight = "1";
    let lastAuto = null;
    let syncing = false;
    const weightPristine = () =>
      weightEl.value === "" || weightEl.value === lastAuto ||
      weightEl.value === weightEl.defaultValue;
    nameEl.addEventListener("input", () => {
      if (syncing) return;
      const kg = parseSizeNameToKg(nameEl.value);
      if (kg == null || !weightPristine()) return;
      syncing = true;
      try {
        weightEl.value = String(kg);
        lastAuto = weightEl.value;
      } finally { syncing = false; }
    });
    weightEl.addEventListener("input", () => {
      if (syncing) return;
      if (weightEl.value !== "" && weightEl.value !== lastAuto) lastAuto = null;
      if (parseSizeNameToKg(nameEl.value) != null) return;
      const suggestion = suggestSizeName(weightEl.value);
      if (!suggestion) return;
      if (nameEl.value.trim() !== "") return;
      syncing = true;
      try { nameEl.value = suggestion; } finally { syncing = false; }
    });
  }

  // Bulk loose is sold by the kg (1 unit = 1 kg)
  function fixLooseWeight(nameEl, weightEl) {
    if (!nameEl || !weightEl) return;
    if (nameEl.dataset.looseweight === "1") return;
    nameEl.dataset.looseweight = "1";
    nameEl.addEventListener("input", () => {
      const nm = String(nameEl.value == null ? "" : nameEl.value).trim().replace(/\s+/g, " ").toLowerCase();
      if (nm === "loose" || nm === "loose powder" || nm === "loose masala") {
        if (String(weightEl.value) !== "1") weightEl.value = "1";
      }
    });
  }

  // Reset a dragged dialog box
  function resetDraggable(box) {
    if (!box) return;
    box.style.position = "";
    box.style.margin = "";
    box.style.left = "";
    box.style.top = "";
    box.style.zIndex = "";
  }

  // Searchable combobox for long master lists
  function quickSelect(selectEl, placeholder) {
    if (!selectEl || selectEl.tagName !== "SELECT") return;
    if (selectEl.dataset.quickselect === "1" && selectEl._qs) {
      try { selectEl._qs.refresh(); } catch (e) {}
      return;
    }
    selectEl.dataset.quickselect = "1";
    const parent = selectEl.parentNode;
    if (parent && parent.style && !parent.style.position) {
      try {
        if (window.getComputedStyle(parent).position === "static") parent.style.position = "relative";
      } catch (e) {}
    }
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = placeholder || "Type to find...";
    input.className = "quickselect-search";
    input.setAttribute("aria-label", placeholder || "Type to find");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");
    input.style.cssText = "flex:1; min-width:0; padding:6px 10px;border:1px solid var(--color-border);border-radius:6px;font-size:13px;";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.textContent = "v";
    toggle.title = "Show all options";
    toggle.setAttribute("aria-label", "Show all options");
    toggle.style.cssText = "flex:0 0 32px; padding:6px 0; border:1px solid var(--color-border); border-radius:6px; background:var(--color-surface-alt, #f5f8fc); font-size:13px; cursor:pointer;";
    const wrap = document.createElement("div");
    wrap.className = "quickselect-wrap";
    wrap.style.cssText = "display:flex; gap:6px; align-items:center; width:100%; margin-bottom:4px;";
    parent.insertBefore(wrap, selectEl);
    wrap.appendChild(input);
    wrap.appendChild(toggle);
    const panel = document.createElement("div");
    panel.className = "quickselect-panel";
    panel.setAttribute("role", "listbox");
    panel.style.cssText = "display:none; position:absolute; top:100%; left:0; right:0; background:#fff; border:1px solid var(--color-border); border-radius:6px; margin-top:2px; max-height:220px; overflow:auto; z-index:50; box-shadow:0 8px 24px rgba(0,0,0,0.12);";
    parent.insertBefore(panel, selectEl);
    selectEl.style.display = "none";
    try {
      const origFocus = selectEl.focus.bind(selectEl);
      selectEl.focus = () => input.focus();
      selectEl._qsOrigFocus = origFocus;
    } catch (e) {}
    if (selectEl.required) { try { input.required = true; } catch (e) {} }

    const escH = s => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    let activeIdx = -1;
    let current = [];

    function allOpts() { return Array.from(selectEl.options); }
    function matchesFor(q) {
      const query = (q || "").trim().toLowerCase();
      const real = allOpts().filter(o => o.value && o.value !== "__new__" && (!query || (o.text || "").toLowerCase().includes(query)));
      const addNew = allOpts().find(o => o.value === "__new__");
      const out = real.slice(0, 50);
      if (addNew) out.push(addNew);
      return { out, real };
    }
    function hidePanel() {
      panel.style.display = "none";
      input.setAttribute("aria-expanded", "false");
      activeIdx = -1;
      window.removeEventListener("scroll", onWindowScroll, true);
      window.removeEventListener("resize", onWindowResize);
    }
    function onWindowScroll(e) {
      if (!panel.contains(e.target)) hidePanel();
    }
    function onWindowResize() { hidePanel(); }
    function placePanel() {
      const r = wrap.getBoundingClientRect();
      const roomBelow = window.innerHeight - r.bottom - 12;
      const roomAbove = r.top - 12;
      const maxWidth = Math.max(200, window.innerWidth - r.left - 12);
      window.addEventListener("scroll", onWindowScroll, true);
      window.addEventListener("resize", onWindowResize);
      panel.style.position = "fixed";
      panel.style.right = "auto";
      panel.style.left = r.left + "px";
      panel.style.marginTop = "0";
      panel.style.width = Math.min(Math.max(r.width, 280), maxWidth) + "px";
      const shownWidth = panel.getBoundingClientRect().width;
      if (r.left + shownWidth > window.innerWidth - 8) {
        panel.style.left = Math.max(8, window.innerWidth - shownWidth - 8) + "px";
      }
      if (roomBelow < 160 && roomAbove > roomBelow) {
        panel.style.top = "auto";
        panel.style.bottom = (window.innerHeight - r.top + 4) + "px";
        panel.style.maxHeight = Math.min(320, Math.max(120, roomAbove)) + "px";
      } else {
        panel.style.bottom = "auto";
        panel.style.top = (r.bottom + 4) + "px";
        panel.style.maxHeight = Math.min(320, Math.max(120, roomBelow)) + "px";
      }
    }
    function render() {
      const { out, real } = matchesFor(input.value);
      current = out;
      activeIdx = -1;
      if (!out.length) {
        panel.innerHTML = '<div style="padding:8px 12px; font-size:13px; color:var(--color-text-muted);">No matches.</div>';
        panel.style.display = "block";
        placePanel();
        input.setAttribute("aria-expanded", "true");
        return;
      }
      panel.innerHTML = out.map((o, i) =>
        `<div role="option" data-idx="${i}" data-value="${escH(o.value)}" style="padding:8px 12px; font-size:13px; cursor:pointer;">${escH(o.text)}</div>`
      ).join("") + (real.length > 10
        ? `<div style="padding:6px 12px; font-size:11px; color:var(--color-text-muted); border-top:1px solid var(--color-border); position:sticky; bottom:0; background:inherit;">${real.length} matches -- keep typing to narrow</div>`
        : "");
      panel.style.display = "block";
      placePanel();
      input.setAttribute("aria-expanded", "true");
    }
    function pickByValue(val) {
      const opt = allOpts().find(o => String(o.value) === String(val));
      if (!opt) return;
      if (selectEl.value !== opt.value) {
        selectEl.value = opt.value;
        try { selectEl.dispatchEvent(new Event("change", { bubbles: true })); }
        catch (e) { selectEl.dispatchEvent(new Event("change")); }
      } else {
        input.value = opt.text;
      }
      hidePanel();
    }
    function syncFromSelect() {
      const sel = selectEl.selectedOptions && selectEl.selectedOptions[0];
      if (sel && sel.value) {
        if (input.value !== sel.text) input.value = sel.text;
      } else if (!selectEl.value) {
        if (document.activeElement !== input) input.value = "";
      }
      try { input.disabled = !!selectEl.disabled; } catch (e) {}
    }
    function refresh() {
      syncFromSelect();
      if (panel.style.display === "block") render();
    }

    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      if (!q) {
        if (selectEl.value) {
          selectEl.value = "";
          try { selectEl.dispatchEvent(new Event("change", { bubbles: true })); }
          catch (e) { selectEl.dispatchEvent(new Event("change")); }
        }
        hidePanel();
        return;
      }
      render();
    });
    input.addEventListener("focus", () => {
      syncFromSelect();
      if (input.value.trim()) render();
      else hidePanel();
    });
    input.addEventListener("click", () => {
      if (!input.value.trim()) render();
    });
    input.addEventListener("blur", () => {
      setTimeout(() => {
        try {
          if (!selectEl.value && input.value.trim()) {
            const want = input.value.trim().toLowerCase();
            const hit = allOpts().find(o => o.value && o.value !== "__new__" && (o.text || "").trim().toLowerCase() === want);
            if (hit) {
              selectEl.value = hit.value;
              try { selectEl.dispatchEvent(new Event("change", { bubbles: true })); }
              catch (e) { selectEl.dispatchEvent(new Event("change")); }
            }
          }
        } catch (e) {}
        hidePanel();
      }, 150);
    });
    input.addEventListener("keydown", (e) => {
      if (panel.style.display === "none") return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const dir = e.key === "ArrowDown" ? 1 : -1;
        activeIdx = (activeIdx + dir + current.length) % Math.max(current.length, 1);
        Array.from(panel.children).forEach((row, i) => {
          row.style.background = i === activeIdx ? "#f5f8fc" : "";
          if (i === activeIdx) row.setAttribute("aria-selected", "true");
          else row.removeAttribute("aria-selected");
        });
      } else if (e.key === "Enter") {
        if (activeIdx >= 0 && current[activeIdx]) {
          e.preventDefault();
          pickByValue(current[activeIdx].value);
        }
      } else if (e.key === "Escape") {
        hidePanel();
        input.blur();
      } else if (e.key === "Tab") {
        hidePanel();
      }
    });
    panel.addEventListener("mousedown", (e) => {
      const row = e.target && e.target.closest ? e.target.closest("[data-value]") : null;
      if (!row) return;
      e.preventDefault();
      pickByValue(row.dataset.value);
    });
    panel.addEventListener("mousemove", (e) => {
      const row = e.target && e.target.closest ? e.target.closest("[data-value]") : null;
      if (!row) return;
      Array.from(panel.children).forEach(r => { r.style.background = ""; });
      row.style.background = "#f5f8fc";
    });
    toggle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      if (panel.style.display === "block") { hidePanel(); return; }
      try { input.focus({ preventScroll: true }); } catch (err) { try { input.focus(); } catch (e2) {} }
      render();
    });
    selectEl.addEventListener("change", () => { syncFromSelect(); });
    try {
      const proto = Object.getPrototypeOf(selectEl);
      const desc = proto && Object.getOwnPropertyDescriptor(proto, "value");
      if (desc && desc.set && !selectEl._qsValuePatched) {
        selectEl._qsValuePatched = true;
        Object.defineProperty(selectEl, "value", {
          get() { return desc.get.call(this); },
          set(v) { desc.set.call(this, v); try { syncFromSelect(); } catch (e) {} },
          configurable: true,
        });
      }
    } catch (e) {}
    try {
      const mo = new MutationObserver(() => { refresh(); });
      mo.observe(selectEl, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
      selectEl._qsObserver = mo;
    } catch (e) {}
    try {
      const form = selectEl.form;
      if (form && !form._qsResetBound) {
        form._qsResetBound = true;
        form.addEventListener("reset", () => { setTimeout(() => {
          try {
            form.querySelectorAll("select[data-quickselect='1']").forEach(s => {
              if (s._qs) s._qs.refresh();
            });
          } catch (e) {}
        }, 0); });
      }
    } catch (e) {}
    selectEl._qs = { input, panel, refresh, syncFromSelect };
    syncFromSelect();
  }

  // Suggestion dropdown for free-text table search boxes
  function searchSuggest(inputEl, provider, opts) {
    if (!inputEl || inputEl.dataset.searchSuggest === "1") return;
    inputEl.dataset.searchSuggest = "1";
    try { inputEl.setAttribute("autocomplete", "off"); } catch (e) {}
    const parent = inputEl.parentNode;
    if (parent && !parent.style.position) {
      try {
        if (window.getComputedStyle(parent).position === "static") parent.style.position = "relative";
      } catch (e) {}
    }
    const maxItems = (opts && opts.max) || 60;
    const onChange = (opts && typeof opts.onChange === "function") ? opts.onChange : null;
    const panel = document.createElement("div");
    panel.className = "search-suggest-panel";
    panel.setAttribute("role", "listbox");
    panel.style.cssText = "display:none; position:absolute; " +
      "background:var(--color-surface, #fff); color:var(--color-text, #17324a); " +
      "border:1px solid var(--color-border); border-radius:6px; " +
      "width:max-content; min-width:120px; max-width:280px; " +
      "max-height:240px; overflow:auto; z-index:60; box-shadow:0 8px 24px rgba(0,0,0,0.12);";
    if (inputEl.nextSibling) parent.insertBefore(panel, inputEl.nextSibling);
    else parent.appendChild(panel);
    function sizePanel() {
      const r = inputEl.getBoundingClientRect();
      const room = Math.max(160, window.innerWidth - r.left - 12);
      panel.style.left = inputEl.offsetLeft + "px";
      panel.style.top = (inputEl.offsetTop + inputEl.offsetHeight + 2) + "px";
      panel.style.maxWidth = Math.min(room, Math.max(240, Math.round(r.width) + 40)) + "px";
    }
    let suppress = false;
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
    const labelOf = (n) => String((n && typeof n === "object" && n.label != null) ? n.label : n);
    function hide() { panel.style.display = "none"; panel.innerHTML = ""; }
    function pick(label) {
      inputEl.value = label;
      hide();
      suppress = true;
      try { inputEl.dispatchEvent(new Event("input", { bubbles: true })); } catch (e) {}
      if (onChange) { try { onChange(label); } catch (e) {} }
    }
    function render() {
      let source = [];
      try { source = provider() || []; } catch (e) { source = []; }
      const q = (inputEl.value || "").trim().toLowerCase();
      const hits = source.filter((n) => {
        const t = labelOf(n).toLowerCase();
        return !q || t.includes(q);
      });
      if (!hits.length) { hide(); return; }
      const shown = hits.slice(0, maxItems);
      panel.innerHTML = shown.map((n) => {
        const label = labelOf(n);
        return `<div role="option" data-label="${esc(label)}" ` +
          `style="padding:7px 12px; font-size:13px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" ` +
          `onmouseenter="this.style.background='var(--color-surface-alt, #f5f8fc)'" ` +
          `onmouseleave="this.style.background=''">${esc(label)}</div>`;
      }).join("") +
        (hits.length > shown.length
          ? `<div style="padding:6px 12px; font-size:11px; color:var(--color-text-muted); border-top:1px solid var(--color-border);">... ${hits.length - shown.length} more -- keep typing</div>`
          : "");
      sizePanel();
      panel.style.display = "block";
      panel.querySelectorAll("[data-label]").forEach((el) => {
        el.addEventListener("mousedown", (ev) => {
          ev.preventDefault();
          pick(el.dataset.label);
        });
      });
    }
    inputEl.addEventListener("input", () => {
      if (suppress) { suppress = false; return; }
      render();
    });
    inputEl.addEventListener("focus", render);
    inputEl.addEventListener("click", render);
    inputEl.addEventListener("keydown", (e) => { if (e && e.key === "Escape") hide(); });
    inputEl.addEventListener("blur", () => { setTimeout(hide, 150); });
  }

  // Shared timestamp key for date sorting
  function tsKey(v, t) {
    const d = asUtcDate(v);
    if (!d || isNaN(d.getTime())) return null;
    if (t != null && t !== "") {
      const tt = asUtcDate(t);
      if (tt && !isNaN(tt.getTime())) {
        d.setHours(tt.getHours(), tt.getMinutes(), tt.getSeconds(), tt.getMilliseconds());
      }
    }
    return d.getTime();
  }

  // Shared FY document-number key for header-click sorting
  function docNumKey(v) {
    const s = String(v == null ? "" : v);
    const m = s.match(/(\d+)-(\d+)\/(\d+)\s*$/);
    if (m) return parseInt(m[1], 10) * 1000000 + parseInt(m[3], 10);
    const t = s.match(/(\d+)\s*$/);
    return t ? parseInt(t[1], 10) : 0;
  }

  // Sort rows by key
  function sortRows(rows, key, dir, valFn) {
    if (!key || !dir) return rows || [];
    const arr = (rows || []).slice();
    const blank = v => v === undefined || v === null || v === "" || v === "--";
    const numOf = v => {
      if (blank(v)) return null;
      const n = parseFloat(String(v).replace(/[^0-9.\-]/g, ""));
      return isNaN(n) ? null : n;
    };
    arr.sort((a, b) => {
      const av = valFn ? valFn(a) : a[key], bv = valFn ? valFn(b) : b[key];
      const an = numOf(av), bn = numOf(bv);
      let cmp;
      if (an !== null && bn !== null) cmp = an - bn;
      else if (blank(av) && blank(bv)) cmp = 0;
      else if (blank(av)) cmp = 1;
      else if (blank(bv)) cmp = -1;
      else cmp = String(av).localeCompare(String(bv));
      return cmp * dir;
    });
    return arr;
  }

  function sortArrow(state, key) {
    if (!state || state.key !== key) return "";
    return state.dir === -1 ? " v" : " ^";
  }

  function renderTable(container, rows, columns, options) {
    if (!container) return;
    const keys = columns ? columns.map((c) => (typeof c === "string" ? c : c.key)) : (rows[0] ? Object.keys(rows[0]) : []);
    const headers = columns ? columns.map((c) => (typeof c === "string" ? c : c.label || c.key)) : keys;
    const sortable = !options || options.sortable !== false;
    const rowClass = options && options.rowClass;
    let pageSize = (options && options.pageSize) || 0;
    try {
      if (pageSize > 8 && localStorage.getItem("dash_low_spec") === "1") pageSize = 8;
    } catch (e) {}
    const state = (sortable && container._sortState) || {};
    let display = rows || [];
    const sortValFn = (sortable && options && options.sortValue) || {};
    if (sortable && state.key) display = sortRows(display, state.key, state.dir, sortValFn[state.key]);
    let page = 1, pages = 1;
    if (pageSize > 0) {
      pages = Math.max(1, Math.ceil(display.length / pageSize));
      page = Math.min(Math.max(1, container._page || 1), pages);
      container._page = page;
      display = display.slice((page - 1) * pageSize, page * pageSize);
    }
    const html = [
      `<table><thead><tr>${headers.map((h, i) => {
        const k = keys[i];
        if (sortable && k && k !== "actions") {
          return `<th data-sortkey="${k}" style="cursor:pointer; user-select:none;" title="Sort by ${h}">${h}${sortArrow(state, k)}</th>`;
        }
        return `<th>${h}</th>`;
      }).join("")}</tr></thead>`,
      `<tbody>${display
        .map(
          (r) => {
            const cls = rowClass ? rowClass(r) : "";
            return `<tr${cls ? ` class="${cls}"` : ""}>${keys
              .map((k, i) => {
                const col = columns && columns[i];
                const val = r[k] !== undefined && r[k] !== null ? r[k] : "--";
                return col && typeof col.render === "function"
                  ? `<td>${col.render(val, r)}</td>`
                  : `<td>${val}</td>`;
              })
              .join("")}</tr>`;
          }
        )
        .join("")}</tbody></table>`,
    ].join("");
    container.innerHTML = `<div class="table-wrap">${html}</div>` +
      (pages > 1
        ? `<div style="display:flex; gap:8px; align-items:center; margin-top:8px; font-size:13px;">` +
          `<button class="btn btn-sm btn-secondary" data-pg="-1"${page <= 1 ? " disabled" : ""}><- Prev</button>` +
          `<span style="color:var(--color-text-muted);">Page ${page} of ${pages}</span>` +
          `<button class="btn btn-sm btn-secondary" data-pg="1"${page >= pages ? " disabled" : ""}>Next -></button></div>`
        : "");
    container.querySelectorAll("button[data-pg]").forEach(b => {
      b.addEventListener("click", () => {
        container._page = Math.min(pages, Math.max(1, page + parseInt(b.dataset.pg, 10)));
        renderTable(container, rows, columns, options);
      });
    });
    if (sortable) {
      container.querySelectorAll("th[data-sortkey]").forEach(th => {
        th.addEventListener("click", () => {
          const k = th.dataset.sortkey;
          const cur = container._sortState || {};
          if (cur.key === k) cur.dir = cur.dir === 1 ? -1 : 1;
          else { cur.key = k; cur.dir = 1; }
          container._sortState = cur;
          renderTable(container, rows, columns, options);
        });
      });
    }
    // Enable keyboard navigation for table rows
    enableTableKeyboardNav(container);
  }

  // Enable keyboard navigation for table rows (Arrow keys, Enter to click first action)
  function enableTableKeyboardNav(container) {
    const table = container.querySelector("table");
    if (!table) return;
    const rows = table.querySelectorAll("tbody tr");
    if (!rows.length) return;
    let focusedIdx = -1;
    table.setAttribute("tabindex", "0");
    table.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        focusedIdx = Math.min(focusedIdx + 1, rows.length - 1);
        updateFocus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        focusedIdx = Math.max(focusedIdx - 1, 0);
        updateFocus();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        const row = rows[focusedIdx];
        if (row) {
          const actionBtn = row.querySelector(".row-actions .btn");
          if (actionBtn) actionBtn.click();
        }
      } else if (e.key === "Escape") {
        table.blur();
        focusedIdx = -1;
        updateFocus();
      }
    });
    table.addEventListener("focus", () => {
      if (focusedIdx === -1 && rows.length) focusedIdx = 0;
      updateFocus();
    });
    table.addEventListener("blur", () => {
      focusedIdx = -1;
      updateFocus();
    });
    function updateFocus() {
      rows.forEach((r, i) => {
        if (i === focusedIdx) {
          r.style.outline = "2px solid var(--color-primary)";
          r.style.outlineOffset = "-2px";
          r.scrollIntoView({ block: "nearest" });
        } else {
          r.style.outline = "";
          r.style.outlineOffset = "";
        }
      });
    }
  }

  // Information dialog
  function infoDialog(title, message) {
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:200;display:flex;align-items:center;justify-content:center;padding:24px;";
    overlay.innerHTML = `
      <div class="dlg-box" style="background:#fff;border-radius:8px;padding:28px;width:100%;max-width:420px;box-shadow:0 20px 40px rgba(0,0,0,0.2);font-family:inherit;">
        <h3 data-drag-handle style="display:block;margin-bottom:10px;font-size:18px;font-weight:600;color:#101a2e;"></h3>
        <p style="margin-bottom:20px;color:#5b6b84;font-size:14px;white-space:pre-line;"></p>
        <div style="display:flex;gap:10px;justify-content:flex-end;">
          <button class="btn btn-primary" id="dlg-ok">OK</button>
        </div>
      </div>`;
    overlay.querySelector("h3").textContent = title || "Cannot do that right now";
    overlay.querySelector("p").textContent = message || "";
    overlay.querySelector("#dlg-ok").addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") overlay.remove();
    });
    document.body.appendChild(overlay);
    const btn = overlay.querySelector("#dlg-ok");
    if (btn) btn.focus();
  }

  // Blocked-action explainer
  function installBlockExplainer() {
    if (window.__mfBlockExplainer) return;
    window.__mfBlockExplainer = true;
    document.addEventListener("click", function (e) {
      const el = e.target && e.target.closest
        ? e.target.closest("[data-block-reason],[data-approve],.btn-danger")
        : null;
      if (!el || !document.body.contains(el)) return;
      const reason = el.getAttribute && el.getAttribute("data-block-reason");
      let message = reason || null;
      if (!message) {
        const editorOff = document.body.classList.contains("editor-off");
        const gated = el.hasAttribute("data-approve") || el.classList.contains("btn-danger");
        if (!(editorOff && gated && el.closest(".card"))) return;
        message = "Editor Mode is off. Switch it on from the padlock button in the top bar, then try again. Nothing was changed.";
      }
      e.preventDefault();
      e.stopPropagation();
      infoDialog("Action unavailable", message);
    }, true);
  }
  installBlockExplainer();

  // Simple confirmation dialog
  function confirmDialog(title, message, onConfirm) {
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:200;display:flex;align-items:center;justify-content:center;padding:24px;";
    overlay.innerHTML = `
      <div class="dlg-box" style="background:#fff;border-radius:8px;padding:28px;width:100%;max-width:420px;box-shadow:0 20px 40px rgba(0,0,0,0.2);font-family:inherit;">
        <h3 data-drag-handle style="display:block;margin-bottom:10px;font-size:18px;font-weight:600;color:#101a2e;">${title}</h3>
        <p style="margin-bottom:20px;color:#5b6b84;font-size:14px;">${message}</p>
        <div style="display:flex;gap:10px;justify-content:flex-end;">
          <button class="btn btn-secondary" id="dlg-cancel">Cancel</button>
          <button class="btn btn-danger" id="dlg-confirm">Confirm</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector("#dlg-cancel").addEventListener("click", () => overlay.remove());
    overlay.querySelector("#dlg-confirm").addEventListener("click", () => {
      overlay.remove();
      if (typeof onConfirm === "function") onConfirm();
    });
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });
    overlay.tabIndex = -1;
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") overlay.remove();
    });
    document.body.appendChild(overlay);
    const cancelBtn = overlay.querySelector("#dlg-cancel");
    if (cancelBtn) cancelBtn.focus();
    else overlay.focus();
  }

  // Themed prompt
  function promptDialog(title, label, defaultValue, onOk) {
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:200;display:flex;align-items:center;justify-content:center;padding:24px;";
    overlay.innerHTML = `
      <div class="dlg-box" style="background:#fff;border-radius:8px;padding:28px;width:100%;max-width:420px;box-shadow:0 20px 40px rgba(0,0,0,0.2);font-family:inherit;">
        <h3 data-drag-handle style="display:block;margin-bottom:10px;font-size:18px;font-weight:600;color:#101a2e;"></h3>
        <label style="display:block;font-size:12px;font-weight:600;color:#5b6b84;text-transform:uppercase;margin-bottom:6px;"></label>
        <input id="dlg-input" type="text" style="width:100%;box-sizing:border-box;margin-bottom:20px;" />
        <div style="display:flex;gap:10px;justify-content:flex-end;">
          <button class="btn btn-secondary" id="dlg-cancel">Cancel</button>
          <button class="btn btn-primary" id="dlg-ok">Save</button>
        </div>
      </div>`;
    overlay.querySelector("h3").textContent = title || "";
    overlay.querySelector("label").textContent = label || "";
    const input = overlay.querySelector("#dlg-input");
    input.value = defaultValue == null ? "" : String(defaultValue);
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      overlay.remove();
      if (!ok) return;
      const v = input.value.trim();
      if (!v) return;
      if (typeof onOk === "function") onOk(v);
    };
    overlay.querySelector("#dlg-cancel").addEventListener("click", () => done(false));
    overlay.querySelector("#dlg-ok").addEventListener("click", () => done(true));
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) done(false);
    });
    overlay.tabIndex = -1;
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") done(false);
      if (e.key === "Enter") { e.preventDefault(); done(true); }
    });
    document.body.appendChild(overlay);
    input.focus();
    input.select();
  }

  /* ---------- Mandatory change note (everywhere) ---------- */
  const _REASON_MIN = 5;
  function _reasonUrl(url, note) {
    const sep = url.indexOf("?") >= 0 ? "&" : "?";
    return url + sep + "reason=" + encodeURIComponent(note);
  }
  function reasonNeeded(method, url) {
    if (/\/auth\//i.test(url) || /^\/?import(\/|$)/i.test(url)) return false;
    if (/\/notifications\//i.test(url)) return false;
    if (/\/api\/dashboard\//i.test(url)) return false;
    if (/\/dashboard\/share/i.test(url)) return false;
    if (/^(put|patch|delete)$/i.test(method)) {
      return (
        /^\/payments\/\d+($|\?)/i.test(url) ||
        /^\/supplier-payments\/\d+($|\?)/i.test(url) ||
        /^\/payroll\/payments\/\d+($|\?)/i.test(url) ||
        /^\/expenses\/\d+($|\?)/i.test(url)
      );
    }
    if (/^post$/i.test(method)) {
      return (
        /^\/payments\/\d+\/restore($|\?)/i.test(url) ||
        /^\/supplier-payments\/\d+\/restore($|\?)/i.test(url) ||
        /^\/expenses\/\d+\/restore($|\?)/i.test(url) ||
        /^\/customers\/\d+\/cash\/\d+\/void($|\?)/i.test(url) ||
        /^\/suppliers\/\d+\/cash\/\d+\/void($|\?)/i.test(url)
      );
    }
    return false;
  }
  function reasonPrompt(title, hint) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:2000;display:flex;align-items:center;justify-content:center;padding:24px;";
      overlay.innerHTML = `
        <div class="dlg-box" style="background:#fff;border-radius:8px;padding:28px;width:100%;max-width:440px;box-shadow:0 20px 40px rgba(0,0,0,0.2);font-family:inherit;">
          <h3 data-drag-handle style="display:block;margin-bottom:8px;font-size:18px;font-weight:600;color:#101a2e;"></h3>
          <p style="margin-bottom:10px;color:#5b6b84;font-size:13px;"></p>
          <textarea id="dlg-reason" rows="3" spellcheck="true" placeholder="What changed and why? e.g. paper said 120/kg, typed 110" style="width:100%;box-sizing:border-box;margin-bottom:8px;"></textarea>
          <div id="dlg-reason-hint" style="font-size:12px;color:var(--color-danger, #b91c1c); min-height:16px; margin-bottom:10px;"></div>
          <div style="display:flex;gap:10px;justify-content:flex-end;">
            <button class="btn btn-secondary" id="dlg-cancel">Cancel</button>
            <button class="btn btn-primary" id="dlg-ok" disabled>Save change</button>
          </div>
        </div>`;
      overlay.querySelector("h3").textContent = title || "Why is this record being changed?";
      overlay.querySelector("p").textContent =
        hint || "The note is saved in the audit history alongside who and when.";
      document.body.appendChild(overlay);
      const area = overlay.querySelector("#dlg-reason");
      const hintEl = overlay.querySelector("#dlg-reason-hint");
      const ok = overlay.querySelector("#dlg-ok");
      let settled = false;
      const done = (note) => {
        if (settled) return;
        settled = true;
        overlay.remove();
        resolve(note);
      };
      const validate = () => {
        const v = area.value.trim();
        const good = v.length >= _REASON_MIN;
        ok.disabled = !good;
        hintEl.textContent = good
          ? ""
          : v
            ? "A little more detail, please (at least " + _REASON_MIN + " characters)."
            : "";
        return good;
      };
      area.addEventListener("input", validate);
      area.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && validate()) {
          e.preventDefault();
          done(area.value.trim());
        }
        if (e.key === "Escape") done(null);
      });
      ok.addEventListener("click", () => { if (validate()) done(area.value.trim()); });
      overlay.querySelector("#dlg-cancel").addEventListener("click", () => done(null));
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) done(null);
      });
      area.focus();
    });
  }
  function installReasonGate() {
    if (window.__mfReasonGate) return;
    window.__mfReasonGate = true;
    const send = window.fetch.bind(window);
    window.fetch = function (input, init) {
      let url = typeof input === "string" ? input : (input && input.url) || "";
      let method = ((init && init.method) || (input && input.method) || "GET") + "";
      method = method.trim();
      const body = init && init.body;
      if (reasonNeeded(method, url)) {
        const hasReason =
          /[?&]reason=/.test(url) ||
          (typeof body === "string" && /['"]reason['"]\s*:/.test(body));
        if (!hasReason) {
          return reasonPrompt().then((note) => {
            if (!note) {
              throw new Error(
                "Change cancelled -- a written reason is required for every change."
              );
            }
            const newUrl = _reasonUrl(url, note);
            if (typeof input === "string") return send(newUrl, init);
            if (input instanceof Request) return send(new Request(newUrl, input), init);
            return send(newUrl, init);
          });
        }
      }
      return send(input, init);
    };
  }
  installReasonGate();

  // Inline field errors
  function clearFieldErrors(form) {
    if (!form || !form.querySelectorAll) return;
    form.querySelectorAll("[data-field-error]").forEach(el => el.remove());
    form.querySelectorAll(".field-invalid").forEach(el => el.classList.remove("field-invalid"));
  }
  function _fieldMessage(sup, text) {
    const el = document.createElement("div");
    el.setAttribute("data-field-error", "1");
    el.style.cssText = "font-size:12px; color:var(--color-danger); margin-top:4px;";
    el.textContent = text;
    if (sup && sup.parentNode) {
      if (sup.nextSibling) sup.parentNode.insertBefore(el, sup.nextSibling);
      else sup.parentNode.appendChild(el);
    }
    return el;
  }
  function showFieldErrors(form, detail, mapping) {
    const items = Array.isArray(detail) ? detail : (detail && typeof detail === "object" ? [detail] : []);
    let marked = 0;
    items.forEach(e => {
      let field = "";
      if (e && Array.isArray(e.loc) && e.loc.length) field = String(e.loc[e.loc.length - 1]);
      else if (typeof e === "string") return;
      const id = mapping && mapping[field];
      if (!id || !form) return;
      const input = form.querySelector("#" + CSS.escape(id));
      if (!input) return;
      input.classList.add("field-invalid");
      _fieldMessage(input, (field ? field + ": " : "") + (e.msg || e.message || "invalid value"));
      marked++;
    });
    return marked > 0;
  }

  // Submit guard
  function submitGuard(event) {
    let btn = event && (event.submitter ||
      (event.target && event.target.querySelector && event.target.querySelector('[type="submit"],button:not([type])')));
    if (!btn && event && event.type === "click" && event.target && event.target.closest) {
      btn = event.target.closest("button");
    }
    if (btn) {
      if (btn.dataset && btn.dataset.saving === "1") return null;
      if (btn.dataset) btn.dataset.saving = "1";
      btn.disabled = true;
    }
    let released = false;
    return function release() {
      if (released) return;
      released = true;
      if (btn) {
        if (btn.dataset) delete btn.dataset.saving;
        btn.disabled = false;
      }
    };
  }

  // Page loader helper
  function setLoading(btn, isLoading) {
    if (!btn) return;
    if (isLoading) {
      btn.setAttribute("data-original", btn.innerHTML);
      btn.innerHTML = `<span style="display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:6px;vertical-align:middle;"></span> Working...`;
      btn.disabled = true;
    } else {
      btn.innerHTML = btn.getAttribute("data-original") || btn.innerHTML;
      btn.disabled = false;
    }
  }

  // Persist small UI filter state
  function saveFilterState(key, state) {
    try { localStorage.setItem("mf_filter_" + key, JSON.stringify(state || {})); }
    catch (e) { /* private mode / quota */ }
  }
  function loadFilterState(key) {
    try { return JSON.parse(localStorage.getItem("mf_filter_" + key) || "null"); }
    catch (e) { return null; }
  }

  // Shared money display: Indian rupee sign, two decimals
  function money(v) {
    if (v === null || v === undefined || v === "") return "--";
    const n = parseFloat(v);
    if (isNaN(n)) return "--";
    return "₹ " + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Shared quantity/weight display that always carries its unit.
  // e.g. qtyUnit(98.001, "kg") -> "98.001 kg"; null -> "--"
  function qtyUnit(v, unit) {
    if (v === null || v === undefined || v === "") return "--";
    const n = parseFloat(v);
    if (isNaN(n)) return "--";
    return String(Math.round(n * 1000) / 1000) + " " + (unit || "");
  }

  // Shared quantity/weight/percent display
  function trimNum(v) {
    if (v === null || v === undefined || v === "") return "--";
    const n = parseFloat(v);
    if (isNaN(n)) return "--";
    return String(Math.round(n * 1000) / 1000);
  }

  // Shared alphabetical order for dropdown options
  function sortByName(rows) {
    return (rows || []).slice().sort((a, b) =>
      String(a && a.name != null ? a.name : "").localeCompare(
        String(b && b.name != null ? b.name : ""), undefined, { sensitivity: "base" }));
  }

  // Shared quantity display, max 2 decimals
  function qty2(v) {
    if (v === null || v === undefined || v === "") return "--";
    const n = parseFloat(v);
    if (isNaN(n)) return "--";
    return String(Math.round(n * 100) / 100);
  }

  // Shared figure box
  function figBox(text, n) {
    const v = parseFloat(n);
    if (isNaN(v) || v === 0) return text;
    return `<span class="fig-box${v < 0 ? " fig-neg" : ""}">${text}</span>`;
  }

  // ---- Keyboard Navigation Helpers ----

  // Install global keyboard shortcuts:
  // - Ctrl+N: New record (context-aware: challan on challans page, invoice on invoices page, etc.)
  // - Esc: Close any open modal/overlay
  // - Enter in table: trigger first action button
  // - Tab: fluid navigation between form fields
  function installKeyboardShortcuts() {
    if (window.__mfKeyboardShortcuts) return;
    window.__mfKeyboardShortcuts = true;

    document.addEventListener("keydown", (e) => {
      // Ignore if typing in input/textarea/select
      const t = e.target;
      const isInput = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (isInput && !(e.key === "Escape")) return;

      // Ctrl+N -- New record (context-aware)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        handleNewRecordShortcut();
        return;
      }

      // Escape -- Close topmost modal/overlay
      if (e.key === "Escape") {
        closeTopmostModal();
        return;
      }
    }, true); // capture phase to catch before other handlers

    // Also handle modal close on Escape for any modal
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        // Close modal-overlay
        const modal = document.querySelector(".modal-overlay[style*='display: flex'], .modal-overlay:not([style*='display: none'])");
        if (modal && modal.style.display !== "none") {
          const closeBtn = modal.querySelector(".modal-close, .btn-danger-outline, .btn-secondary");
          if (closeBtn) closeBtn.click();
        }
        // Close any element with id ending in -overlay that's visible
        document.querySelectorAll("[id$='-overlay']").forEach(el => {
          if (el.style.display !== "none" && !el.hidden) {
            const closeBtn = el.querySelector(".modal-close, [onclick*='close'], [onclick*='cancel'], .btn-danger-outline, .btn-secondary");
            if (closeBtn) closeBtn.click();
          }
        });
      }
    });
  }

  // Context-aware new record shortcut
  function handleNewRecordShortcut() {
    const hash = window.location.hash || "";
    if (hash.includes("/challans")) {
      if (typeof window.openNewChallanForm === "function") window.openNewChallanForm();
    } else if (hash.includes("/invoices")) {
      if (typeof window.openNewInvoiceForm === "function") window.openNewInvoiceForm();
    } else if (hash.includes("/customers")) {
      if (typeof window.openNewCustomerForm === "function") window.openNewCustomerForm();
    } else if (hash.includes("/suppliers")) {
      if (typeof window.openNewSupplierForm === "function") window.openNewSupplierForm();
    } else if (hash.includes("/products")) {
      if (typeof window.openNewProductForm === "function") window.openNewProductForm();
    } else if (hash.includes("/raw-materials")) {
      if (typeof window.openNewRawMaterialForm === "function") window.openNewRawMaterialForm();
    } else if (hash.includes("/production")) {
      if (typeof window.openNewProductionForm === "function") window.openNewProductionForm();
    } else if (hash.includes("/expenses")) {
      if (typeof window.openNewExpenseForm === "function") window.openNewExpenseForm();
    } else if (hash.includes("/employees")) {
      if (typeof window.openNewEmployeeForm === "function") window.openNewEmployeeForm();
    } else if (hash.includes("/dashboard")) {
      // Dashboard default: new challan
      if (typeof window.openNewChallanForm === "function") window.openNewChallanForm();
    } else {
      notify("No 'New' action on this page. Go to Challans, Invoices, Customers, etc.", "info");
    }
  }

  // Close the topmost visible modal/overlay
  function closeTopmostModal() {
    // Priority order: modal-overlay > specific overlays > dialog boxes
    const modal = document.querySelector(".modal-overlay:not([style*='display: none'])");
    if (modal && modal.style.display !== "none") {
      const closeBtn = modal.querySelector(".modal-close, .btn-danger-outline, .btn-secondary");
      if (closeBtn) { closeBtn.click(); return; }
    }
    // Specific known overlays
    const overlays = [
      "recvpick-overlay", "challan-pay-overlay", "invoice-pay-overlay",
      "dispatch-overlay", "import-preview-overlay", "bulk-action-overlay"
    ];
    for (const id of overlays) {
      const el = document.getElementById(id);
      if (el && el.style.display !== "none") {
        const closeBtn = el.querySelector(".modal-close, .btn-danger-outline, .btn-secondary, [onclick*='close'], [onclick*='cancel']");
        if (closeBtn) { closeBtn.click(); return; }
      }
    }
    // Generic: any .dlg-box with a close button
    const dlg = document.querySelector(".dlg-box:not([style*='display: none'])");
    if (dlg) {
      const closeBtn = dlg.querySelector(".btn-secondary, .btn-danger-outline, #dlg-cancel, #dlg-ok");
      if (closeBtn) { closeBtn.click(); return; }
    }
  }

  // Install once
  installKeyboardShortcuts();

  // ---- Date Input Helpers (IST consistency) ----

  // Ensure a date input respects IST (prevents browser timezone shift)
  function bindISTDateInput(inputEl) {
    if (!inputEl || inputEl.type !== "date") return;
    if (inputEl.dataset.istBound === "1") return;
    inputEl.dataset.istBound = "1";
    // On blur, ensure the value is a valid date string
    inputEl.addEventListener("blur", () => {
      const v = inputEl.value;
      if (!v) return;
      // Validate format YYYY-MM-DD
      if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) return;
      // Parse as local (IST) to verify it's a valid date
      const d = new Date(v + "T00:00:00+05:30");
      if (isNaN(d.getTime())) {
        inputEl.value = "";
        notify("Invalid date", "error");
      }
    });
  }

  // Get today's date in IST as YYYY-MM-DD for date inputs
  function todayIST() {
    const now = new Date();
    const ist = new Date(now.toLocaleString("en-US", { timeZone: IST_ZONE }));
    const yyyy = ist.getFullYear();
    const mm = String(ist.getMonth() + 1).padStart(2, "0");
    const dd = String(ist.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }

  return { notify, api, renderTable, confirmDialog, infoDialog, promptDialog, setLoading, submitGuard,
    fieldError: showFieldErrors, clearFieldErrors,
    fyStartDate, inCurrentFy, fyStartStr, applyFyStart, dropInvertedRange, inDateRange,
    applySearchDeepLink, dateWithTime, dateTimeText, fmtDateTime, fmtDay,
    quickSelect, searchSuggest, sortRows, sortArrow, tsKey, money, trimNum, qty2, qtyUnit, figBox,
    sortByName, docNumKey, formatError, download, downloadBlob, copyText,
    parseSizeNameToKg, suggestSizeName, bindSizeWeightAuto, fixLooseWeight,
    resetDraggable, saveFilterState, loadFilterState,
    // New exports
    bindISTDateInput, todayIST, handleNewRecordShortcut, closeTopmostModal,
    enableTableKeyboardNav
  };
})();

// Spell-check every textarea
document.addEventListener("focusin", (e) => {
  const t = e.target;
  if (t && t.tagName === "TEXTAREA") t.spellcheck = true;
});

// Right-click Copy
(function () {
  let menu = null;
  function hideMenu() { if (menu) { menu.remove(); menu = null; } }
  document.addEventListener("contextmenu", (e) => {
    const t = e.target;
    const tag = (t && t.tagName ? t.tagName : "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select"
        || (t && t.isContentEditable)
        || (t && t.type === "password")) return;
    let sel = "";
    try { sel = window.getSelection ? window.getSelection().toString() : ""; }
    catch (err) { sel = ""; }
    if (!sel) return;
    e.preventDefault();
    hideMenu();
    menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.style.left = Math.min(e.clientX, window.innerWidth - 150) + "px";
    menu.style.top = Math.min(e.clientY, window.innerHeight - 60) + "px";
    const item = document.createElement("button");
    item.type = "button";
    item.className = "ctx-item";
    item.textContent = "Copy";
    item.addEventListener("click", async () => {
      const ok = window.UI && window.UI.copyText
        ? await window.UI.copyText(sel) : false;
      if (window.UI) {
        window.UI.notify(
          ok ? "Copied to clipboard." : "Copy failed - select the text and press Ctrl+C.",
          ok ? "success" : "error");
      }
      hideMenu();
    });
    menu.appendChild(item);
    document.body.appendChild(menu);
  });
  document.addEventListener("click", (e) => { if (menu && !menu.contains(e.target)) hideMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });
  window.addEventListener("blur", hideMenu);
})();

// Draggable popups
(function () {
  document.addEventListener("pointerdown", (e) => {
    const handle = e.target && e.target.closest
      ? e.target.closest("[data-drag-handle]") : null;
    if (!handle) return;
    if (e.target.closest("button")) return;
    if (e.pointerType === "mouse" && e.button !== undefined && e.button !== 0) return;
    const box = handle.closest(".modal-card, .dlg-box") || handle.parentElement;
    if (!box) return;
    e.preventDefault();
    const r = box.getBoundingClientRect();
    box.style.position = "fixed";
    box.style.margin = "0";
    box.style.left = r.left + "px";
    box.style.top = r.top + "px";
    box.style.zIndex = "201";
    const startX = e.clientX, startY = e.clientY;
    const origL = r.left, origT = r.top, w = r.width;
    handle.style.cursor = "grabbing";
    const move = (ev) => {
      const nl = Math.min(Math.max(origL + ev.clientX - startX, -w + 60),
        window.innerWidth - 60);
      const nt = Math.min(Math.max(origT + ev.clientY - startY, 0),
        window.innerHeight - 40);
      box.style.left = nl + "px";
      box.style.top = nt + "px";
    };
    const up = () => {
      handle.style.cursor = "";
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  });
})();

// Global client-error capture
(function () {
  let reported = 0;
  window.addEventListener("error", (event) => {
    try {
      if (reported >= 5) return;
      const msg = (event && event.message) || "Unknown page error";
      if (/client-log|notifications/.test(String(event && event.filename || ""))) return;
      reported++;
      const detail = `${event.filename || ""}:${event.lineno || ""} :: ${event.error && event.error.stack ? event.error.stack.split("\n").slice(0, 3).join(" | ") : ""}`;
      fetch("/notifications/client-log", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" },
          (window.Auth && window.Auth.authHeaders) ? window.Auth.authHeaders() : {}),
        body: JSON.stringify({ message: String(msg).slice(0, 500), detail: detail.slice(0, 2000) }),
      }).catch(() => {});
    } catch (e) { /* never break the page */ }
  });
})();

// Spin keyframes
if (!document.getElementById("ui-spin-style")) {
  const s = document.createElement("style");
  s.id = "ui-spin-style";
  s.textContent = `@keyframes spin { to { transform: rotate(360deg); } }`;
  document.head.appendChild(s);
}