/* PC Cleaner — app.js */

// TASKS est injecté via var dans index.html — ne pas redéclarer ici
let sizes   = {};
let cleaning = false;

const GROUPS = {
  system:  { label: "Système",      cls: "s-sys", grpCls: "grp-sys" },
  browser: { label: "Navigateurs",  cls: "s-nav", grpCls: "grp-nav" },
  apps:    { label: "Applications", cls: "s-app", grpCls: "grp-app" },
};

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  renderTasks();
  loadSizes();
  loadDisk();
  loadHistory();
  updateClock();
  setInterval(updateClock, 10_000);
  setTimeout(loadHealthBadge, 2500);

  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-primary, .btn-ghost, .btn-uninstall");
    if (!btn || btn.disabled) return;
    const r    = btn.getBoundingClientRect();
    const size = Math.max(btn.offsetWidth, btn.offsetHeight) * 2.5;
    const wave = document.createElement("span");
    wave.className = "ripple-wave";
    wave.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX-r.left-size/2}px;top:${e.clientY-r.top-size/2}px`;
    btn.appendChild(wave);
    wave.addEventListener("animationend", () => wave.remove());
  });
});

// ── Horloge ──────────────────────────────────────────────────────────────────
function updateClock() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

// ── Persistance des tâches cochées ───────────────────────────────────────────
function saveCheckedState() {
  localStorage.setItem("pcc-tasks", JSON.stringify(
    TASKS.map(t => ({ id: t.id, checked: t.checked }))
  ));
}

// ── Tâches ───────────────────────────────────────────────────────────────────
function renderTasks() {
  const container = document.getElementById("task-list");
  container.innerHTML = "";

  const grouped = {};
  TASKS.forEach(t => {
    if (!grouped[t.group]) grouped[t.group] = [];
    grouped[t.group].push(t);
  });

  Object.entries(GROUPS).forEach(([groupKey, meta]) => {
    const tasks = grouped[groupKey] || [];
    if (!tasks.length) return;

    const groupBytes = tasks.reduce((s, t) => s + (sizes[t.id]?.bytes || 0), 0);

    // En-tête de groupe (h3 Notion-style, uppercase + total à droite)
    const head = document.createElement("div");
    head.className = `grp-head ${meta.cls}`;
    head.innerHTML = `
      <div class="grp-name">${meta.label}</div>
      <div class="grp-total">
        <div class="grp-total-val">${groupBytes > 0 ? fmtBytes(groupBytes) : "—"}</div>
      </div>`;
    container.appendChild(head);

    // Lignes de tâches
    const grpDiv = document.createElement("div");
    grpDiv.className = meta.grpCls;

    tasks.forEach(t => {
      const locked = !window.IS_ADMIN && t.admin;
      const item = document.createElement("div");
      item.className = "task-item" + (t.checked && !locked ? " on" : "") + (locked ? " locked" : "");
      item.id = "task-row-" + t.id;
      if (!locked) item.onclick = () => toggleTask(t.id);

      const badge = (t.admin && !window.IS_ADMIN)
        ? `<span class="badge badge-admin">Admin requis</span>`
        : "";

      const sizeInfo = sizes[t.id];
      const sizeHtml = sizeInfo
        ? `<div class="size-val ${sizeColorClass(sizeInfo.bytes)}">${sizeInfo.fmt}</div>
           <div class="size-sub">${sizeInfo.bytes > 0 ? "libérable" : ""}</div>`
        : `<div class="size-val sz-load">…</div>`;

      item.innerHTML = `
        <div class="toggle-track"><div class="toggle-thumb"></div></div>
        <div class="task-info">
          <div class="task-name">${t.label} ${badge}</div>
          <div class="task-desc">${t.desc}</div>
        </div>
        <div class="size-wrap">${sizeHtml}</div>`;
      grpDiv.appendChild(item);
    });

    container.appendChild(grpDiv);
  });

  updateTotal();
  updateSidebar();
}

function toggleTask(id) {
  if (cleaning) return;
  const t = TASKS.find(x => x.id === id);
  if (t) { t.checked = !t.checked; renderTasks(); saveCheckedState(); }
}

function checkAll()   { if (!cleaning) { TASKS.forEach(t => { if (window.IS_ADMIN || !t.admin) t.checked = true;  }); renderTasks(); saveCheckedState(); } }
function uncheckAll() { if (!cleaning) { TASKS.forEach(t => t.checked = false); renderTasks(); saveCheckedState(); } }

function sizeColorClass(bytes) {
  if (!bytes || bytes === 0) return "sz-zero";
  if (bytes > 500 * 1024 * 1024) return "sz-big";
  if (bytes >  50 * 1024 * 1024) return "sz-med";
  return "sz-sml";
}

// ── Tri des tâches par taille ─────────────────────────────────────────────────
function sortTasksBySize() {
  const groupOrder = { system: 0, browser: 1, apps: 2 };
  TASKS.sort((a, b) => {
    const gDiff = (groupOrder[a.group] ?? 3) - (groupOrder[b.group] ?? 3);
    if (gDiff !== 0) return gDiff;
    return (sizes[b.id]?.bytes || 0) - (sizes[a.id]?.bytes || 0);
  });
}

// ── Sidebar (no-op : la sidebar a été remplacée par stat-grid) ───────────────
function updateSidebar() { /* legacy : remplacé par updateStatTotal() */ }

// ── Stat principale + callout ────────────────────────────────────────────────
function updateStatTotal() {
  const checked     = TASKS.filter(t => t.checked);
  const totalBytes  = checked.reduce((s, t) => s + (sizes[t.id]?.bytes || 0), 0);
  const maxBytes    = TASKS.reduce((s, t) => s + (sizes[t.id]?.bytes || 0), 0);
  const sizeKnown   = TASKS.some(t => sizes[t.id]);

  const statVal     = document.getElementById("stat-total");
  const statMeta    = document.getElementById("stat-total-meta");
  const calloutT    = document.getElementById("callout-title");
  const calloutD    = document.getElementById("callout-desc");

  if (!sizeKnown) {
    if (statVal)  { statVal.textContent  = "…"; statVal.classList.add("dim"); }
    if (statMeta) statMeta.textContent  = "Calcul en cours…";
    if (calloutT) calloutT.textContent  = "Calcul de l'espace récupérable…";
    if (calloutD) calloutD.textContent  = "Patientez quelques instants";
    return;
  }

  if (statVal) {
    if (maxBytes > 0) {
      statVal.innerHTML = fmtBytes(maxBytes).replace(/ ([^\s]+)$/, '<span class="unit"> $1</span>');
      statVal.classList.remove("dim");
    } else {
      statVal.textContent = "0 Go";
      statVal.classList.add("dim");
    }
  }
  if (statMeta) {
    statMeta.textContent = `sur ${TASKS.length} tâche${TASKS.length > 1 ? "s" : ""} au total`;
  }

  if (calloutT) {
    if (checked.length === 0) {
      calloutT.textContent = "Aucune tâche sélectionnée";
    } else if (totalBytes === 0) {
      calloutT.textContent = "Système déjà propre";
    } else {
      calloutT.textContent = `Prêt à libérer ${fmtBytes(totalBytes)}`;
    }
  }
  if (calloutD) {
    calloutD.textContent = checked.length === 0
      ? "Cochez au moins une catégorie ci-dessus"
      : `${checked.length} tâche${checked.length > 1 ? "s" : ""} cochée${checked.length > 1 ? "s" : ""} · cliquez pour exécuter`;
  }
}

// ── Total estimé (compat : délègue à updateStatTotal) ────────────────────────
function updateTotal() { updateStatTotal(); }

function fmtBytes(b) {
  if (b === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(1) + " " + units[i];
}

// ── API : tailles ─────────────────────────────────────────────────────────────
async function loadSizes() {
  setSizesLoading(true);
  try {
    const res = await fetch("/api/sizes");
    sizes = await res.json();
    sortTasksBySize();
    renderTasks();
  } catch (e) {
    addLog("Impossible de récupérer les tailles estimées.", "warn");
  } finally {
    setSizesLoading(false);
  }
}

function setSizesLoading(loading) {
  const btn = document.getElementById("btn-refresh");
  if (!btn) return;
  if (loading) { _btnScan(btn, "Calcul…"); } else { _btnReset(btn); }
}

// ── API : disque ─────────────────────────────────────────────────────────────
async function loadDisk() {
  try {
    const res    = await fetch("/api/disk");
    const drives = await res.json();
    renderDisk(drives);
  } catch (e) { /* silencieux */ }
}

function renderDisk(drives) {
  const container = document.getElementById("disk-container");
  if (!container || !drives.length) return;
  container.innerHTML = "";
  drives.forEach(d => {
    const pct      = d.percent;
    const barClass = pct > 85 ? "danger" : pct > 60 ? "warn" : "";
    const div      = document.createElement("div");
    div.className  = "disk-card";
    div.innerHTML  = `
      <div class="disk-left">
        <div class="dk-name">${d.device}</div>
        <div class="dk-free">${d.free_fmt} libres</div>
      </div>
      <div class="disk-right">
        <div class="bar-bg"><div class="bar-fill ${barClass}" id="disk-bar-${d.device.replace(/\\/g,'')}" style="width:${pct}%"></div></div>
        <div class="bar-stats"><span>${pct}% utilisé</span><span>${d.total_fmt}</span></div>
      </div>`;
    container.appendChild(div);
  });

  // Mise à jour de la stat "Disque principal" (premier disque retourné)
  const main = drives[0];
  const sv = document.getElementById("stat-disk");
  const sm = document.getElementById("stat-disk-meta");
  if (sv) {
    sv.innerHTML = `${main.percent}<span class="unit">%</span>`;
    sv.classList.remove("dim");
  }
  if (sm) sm.textContent = `${main.device} · ${main.free_fmt} libres sur ${main.total_fmt}`;
}

// ── Historique ────────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const res  = await fetch("/api/history");
    const data = await res.json();
    renderHistoryHint(data);
  } catch (e) {}
}

function renderHistoryHint(data) {
  const sb = document.getElementById("sb-last-scan");
  if (!data || !data.length) {
    if (sb) sb.textContent = "jamais";
    return;
  }
  const last = data[0];
  const ago  = fmtAgo(new Date(last.date));

  const sv = document.getElementById("stat-history");
  const sm = document.getElementById("stat-history-meta");
  if (sv) {
    sv.textContent = ago;
    sv.classList.remove("dim");
  }
  if (sm) sm.textContent = `${last.freed_fmt} libérés`;

  if (sb) sb.textContent = ago;
}

function fmtAgo(date) {
  const diff = Date.now() - date.getTime();
  const min  = Math.floor(diff / 60000);
  const hrs  = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (days > 1)   return `il y a ${days} jours`;
  if (days === 1) return "hier";
  if (hrs >= 1)   return `il y a ${hrs}h`;
  if (min >= 1)   return `il y a ${min} min`;
  return "à l'instant";
}

// ── Confirmation modale générique ────────────────────────────────────────────
function showConfirm(title, body, onOk) {
  const overlay = document.getElementById("confirm-overlay");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").textContent  = body;
  const btn = document.getElementById("confirm-ok");
  btn.onclick = () => { _closeConfirm(); onOk(); };
  overlay.style.display = "flex";
}
function _closeConfirm() {
  const el = document.getElementById("confirm-overlay");
  if (el) el.style.display = "none";
}

// ── Panneau d'activité ────────────────────────────────────────────────────────
const ACTIVITY_MAX = 20;
let _activity = [];
let _activitySeq = 0;

function _activityFmtTime(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return s + " s";
  const m = Math.round(s / 60);
  if (m < 60) return m + " min";
  return Math.round(m / 60) + " h";
}

const ACTIVITY_TAB_LABELS = {
  nettoyage: "Nettoyage",
  outils:    "Outils",
  sante:     "Santé",
  pilotes:   "Pilotes",
  perso:     "Personnalisation",
};

function _activityCategoryFor(target) {
  if (!target || !target.closest) return { id: "autres", label: "Autres" };
  const panel = target.closest(".tab-panel");
  if (!panel) return { id: "autres", label: "Autres" };
  const id = panel.id.replace(/^tab-/, "");
  return { id, label: ACTIVITY_TAB_LABELS[id] || id };
}

function _activityGroupsMap() {
  try { return JSON.parse(localStorage.getItem("pcc-activity-groups") || "{}") || {}; }
  catch (e) { return {}; }
}
function _activityGroupOpen(catId) {
  return _activityGroupsMap()[catId] !== false;
}
function activityToggleGroup(catId) {
  const map = _activityGroupsMap();
  map[catId] = _activityGroupOpen(catId) ? false : true;
  try { localStorage.setItem("pcc-activity-groups", JSON.stringify(map)); } catch (e) {}
  _activityRender();
}

function _activityMarkHtml(status) {
  if (status === "run") {
    return `<span class="mark"><span class="dot"></span></span>`;
  }
  if (status === "fail") {
    return `<span class="mark">×</span>`;
  }
  return `<span class="mark"><span class="check-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 12 10 17 19 7"/></svg></span></span>`;
}

const _ICON_RELAUNCH = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`;
const _ICON_GOTO     = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 17L17 7M9 7h8v8"/></svg>`;
const _ICON_DISMISS  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 6l12 12M6 18L18 6"/></svg>`;

function _activityRowHtml(a, now) {
  const cls = a.status === "run" ? "" : a.status === "fail" ? "fail" : "done";
  const hasProgress = a.status === "run" && typeof a.progress === "number";
  const metaText = a.meta || (a.status === "run" ? "en cours" : a.status === "fail" ? "échec" : "");
  const meta = hasProgress ? `${Math.round(a.progress)}%` : metaText;
  const time = a.doneAt ? _activityFmtTime(now - a.doneAt) : "";
  const fresh = a._fresh ? " fresh" : "";
  const clickable = a.target ? " clickable" : "";
  const onclick = a.target ? ` onclick="activityGoto(${a.id})"` : "";
  const bar = hasProgress
    ? `<div class="progress-bar" style="width:${a.progress}%"></div>`
    : "";

  const btns = [];
  const isClickable = a.target && a.target.tagName === "BUTTON";
  if (a.status === "run") {
    if (a.target) btns.push(`<button class="act-btn" onclick="event.stopPropagation();activityGoto(${a.id})" title="Aller à la tâche">${_ICON_GOTO}</button>`);
    btns.push(`<button class="act-btn" onclick="event.stopPropagation();activityDismiss(${a.id})" title="Annuler">${_ICON_DISMISS}</button>`);
  } else {
    if (isClickable) {
      btns.push(`<button class="act-btn" onclick="event.stopPropagation();activityRelaunch(${a.id})" title="Relancer">${_ICON_RELAUNCH}</button>`);
    }
    if (a.target) {
      btns.push(`<button class="act-btn" onclick="event.stopPropagation();activityGoto(${a.id})" title="Aller à la tâche">${_ICON_GOTO}</button>`);
    }
    btns.push(`<button class="act-btn" onclick="event.stopPropagation();activityDismiss(${a.id})" title="Retirer">${_ICON_DISMISS}</button>`);
  }

  return `<div class="activity-row ${cls}${fresh}${clickable}" data-id="${a.id}"${onclick}>
    ${_activityMarkHtml(a.status)}
    <span class="name">${_activityEscape(a.name)}</span>
    <span class="meta">${_activityEscape(meta)}</span>
    <span class="time">${time}</span>
    <span class="actions">${btns.join("")}</span>
    ${bar}
  </div>`;
}

function _activityRender() {
  const list = document.getElementById("activity-list");
  const count = document.getElementById("activity-count");
  if (!list) return;
  if (!_activity.length) {
    list.innerHTML = `<div class="activity-empty">Aucune activité pour l'instant.</div>`;
  } else {
    const groups = new Map();
    const order = [];
    for (const a of _activity) {
      const cat = _activityCategoryFor(a.target);
      if (!groups.has(cat.id)) {
        groups.set(cat.id, { id: cat.id, label: cat.label, items: [] });
        order.push(cat.id);
      }
      groups.get(cat.id).items.push(a);
    }
    const now = Date.now();
    list.innerHTML = order.map(cid => {
      const g = groups.get(cid);
      const isOpen = _activityGroupOpen(cid);
      const miniDots = g.items.slice(0, 5).map(a => {
        const c = a.status === "run" ? "run" : a.status === "fail" ? "fail" : "done";
        return `<span class="mini-dot ${c}"></span>`;
      }).join("");
      const body = isOpen
        ? `<div class="activity-group-body">${g.items.map(a => _activityRowHtml(a, now)).join("")}</div>`
        : "";
      return `<div class="activity-group ${isOpen ? "open" : ""}" data-cat="${cid}">
        <div class="activity-group-head" onclick="activityToggleGroup('${cid}')">
          <span class="chev">▸</span>
          <span class="group-label">${_activityEscape(g.label)}</span>
          <div class="mini-dots">${miniDots}</div>
          <span class="group-count">${g.items.length}</span>
        </div>
        ${body}
      </div>`;
    }).join("");
    _activity.forEach(a => { a._fresh = false; });
  }
  count.textContent = _activity.length + (_activity.length > 1 ? " entrées" : " entrée");

  const running = _activity.filter(a => a.status === "run").length;
  const ok = _activity.filter(a => a.status === "done").length;
  const runEl = document.getElementById("ac-run");
  const okEl  = document.getElementById("ac-ok");
  runEl.style.display = running ? "flex" : "none";
  okEl.style.display  = ok ? "flex" : "none";
  document.getElementById("ac-run-n").textContent = running;
  document.getElementById("ac-ok-n").textContent  = ok;
}

function _activityEscape(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function activityPush(name, status = "run", meta = "", target = null) {
  const id = ++_activitySeq;
  _activity.unshift({
    id, name, status, meta, target,
    progress: null,
    startedAt: Date.now(),
    doneAt: status !== "run" ? Date.now() : null,
    _fresh: true,
  });
  if (_activity.length > ACTIVITY_MAX) _activity.length = ACTIVITY_MAX;
  _activityRender();
  return id;
}

function activityProgress(id, pct, meta) {
  const a = _activity.find(x => x.id === id);
  if (!a) return;
  a.progress = Math.max(0, Math.min(100, pct));
  if (meta !== undefined) a.meta = meta;
  _activityRender();
}

function activityDismiss(id) {
  const i = _activity.findIndex(a => a.id === id);
  if (i >= 0) {
    _activity.splice(i, 1);
    _activityRender();
  }
}

function activityGoto(id) {
  const a = _activity.find(x => x.id === id);
  if (!a || !a.target) return;
  const el = a.target;

  // Support des plain objects {tab: "outils"} en plus des éléments DOM
  if (el && typeof el === "object" && el.tab && !(el instanceof HTMLElement)) {
    const sbBtn = document.querySelector(`.sb-item[data-tab="${el.tab}"]`);
    if (sbBtn && typeof switchTab === "function") {
      switchTab(el.tab, sbBtn);
    }
    return;
  }

  const panel = el.closest?.(".tab-panel");
  if (panel) {
    const tabId = panel.id.replace(/^tab-/, "");
    const sbBtn = document.querySelector(`.sb-item[data-tab="${tabId}"]`);
    if (sbBtn && typeof switchTab === "function") {
      switchTab(tabId, sbBtn);
    }
  }
  setTimeout(() => {
    try {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("activity-flash");
      setTimeout(() => el.classList.remove("activity-flash"), 1200);
    } catch (e) {}
  }, 60);
}

function activityRelaunch(id) {
  const a = _activity.find(x => x.id === id);
  if (!a || !a.target) return;
  const el = a.target;

  // Support des plain objects {tab: "outils"}
  if (el && typeof el === "object" && el.tab && !(el instanceof HTMLElement)) {
    const sbBtn = document.querySelector(`.sb-item[data-tab="${el.tab}"]`);
    if (sbBtn && typeof switchTab === "function") {
      switchTab(el.tab, sbBtn);
    }
    return;
  }

  const panel = el.closest?.(".tab-panel");
  if (panel) {
    const tabId = panel.id.replace(/^tab-/, "");
    const sbBtn = document.querySelector(`.sb-item[data-tab="${tabId}"]`);
    if (sbBtn && typeof switchTab === "function") {
      switchTab(tabId, sbBtn);
    }
  }
  activityDismiss(id);
  setTimeout(() => { try { el.click(); } catch (e) {} }, 80);
}

function activityUpdate(id, meta) {
  const a = _activity.find(x => x.id === id);
  if (!a) return;
  a.meta = meta;
  _activityRender();
}

function activityDone(id, meta, status = "done") {
  const a = _activity.find(x => x.id === id);
  if (!a) return;
  a.status = status;
  a.meta = meta ?? a.meta;
  a.doneAt = Date.now();
  a._fresh = true;
  _activityRender();
}

function activityLog(name, meta = "", status = "done") {
  // Utilise l'onglet actif comme contexte par défaut pour que l'entrée
  // tombe dans la bonne catégorie au lieu de "Autres".
  const activeTab = document.querySelector(".tab-panel.active");
  activityPush(name, status, meta, activeTab || null);
}

function clearActivity() {
  _activity = [];
  _activityRender();
}

// ── État flottant : position, taille, dock ────────────────────────────────────
const ACTIVITY_STATE_KEY = "pcc-activity-state";
const ACTIVITY_DEFAULTS = { x: null, y: null, w: 300, h: 280, docked: true };
let _activityState = { ...ACTIVITY_DEFAULTS };

function _saveActivityState() {
  try { localStorage.setItem(ACTIVITY_STATE_KEY, JSON.stringify(_activityState)); } catch (e) {}
}
function _loadActivityState() {
  try {
    const raw = localStorage.getItem(ACTIVITY_STATE_KEY);
    if (raw) Object.assign(_activityState, JSON.parse(raw));
  } catch (e) {}
}
function _applyActivityState() {
  const panel = document.getElementById("activity-panel");
  if (!panel) return;
  panel.classList.toggle("docked", !!_activityState.docked);
  if (_activityState.docked) {
    panel.style.left = "";
    panel.style.top = "";
    panel.style.right = "";
    panel.style.bottom = "";
    panel.style.width = "";
    panel.style.height = "";
    return;
  }
  const w = _activityState.w;
  const h = _activityState.h;
  // Défaut "première ouverture" : aligné sur la rail dockée (haut-droite)
  const x = _activityState.x != null
    ? _activityState.x
    : Math.max(0, window.innerWidth - w);
  const y = _activityState.y != null
    ? _activityState.y
    : 140;
  panel.style.left = x + "px";
  panel.style.top = y + "px";
  panel.style.right = "auto";
  panel.style.bottom = "auto";
  panel.style.width = w + "px";
  panel.style.height = h + "px";
}

function toggleActivityDock() {
  const panel = document.getElementById("activity-panel");
  const wasDocked = _activityState.docked;
  // Si on dédocke pour la première fois, capturer la position de la rail
  // pour que le panneau apparaisse à l'endroit où il était masqué.
  if (wasDocked && panel && (_activityState.x == null || _activityState.y == null)) {
    const rect = panel.getBoundingClientRect();
    const w = _activityState.w;
    _activityState.x = Math.max(0, rect.right - w);
    _activityState.y = rect.top;
  }
  _activityState.docked = !wasDocked;
  _applyActivityState();
  _saveActivityState();
}

function _initActivityInteractions() {
  const panel = document.getElementById("activity-panel");
  if (!panel) return;
  const head = document.getElementById("activity-drag-handle");
  const handles = panel.querySelectorAll(".activity-resize-h");
  const MIN_W = 220, MIN_H = 140;
  let op = null;

  head.addEventListener("mousedown", (e) => {
    if (_activityState.docked) return;
    if (e.target.closest("button")) return;
    const rect = panel.getBoundingClientRect();
    op = { type: "drag", offX: e.clientX - rect.left, offY: e.clientY - rect.top };
    panel.classList.add("dragging");
    e.preventDefault();
  });

  handles.forEach(h => {
    h.addEventListener("mousedown", (e) => {
      if (_activityState.docked) return;
      const rect = panel.getBoundingClientRect();
      op = {
        type:   "resize",
        dir:    h.dataset.dir,
        startX: e.clientX,
        startY: e.clientY,
        x0:     rect.left,
        y0:     rect.top,
        w0:     rect.width,
        h0:     rect.height,
      };
      panel.classList.add("resizing");
      e.preventDefault();
      e.stopPropagation();
    });
  });

  document.addEventListener("mousemove", (e) => {
    if (!op) return;
    if (op.type === "drag") {
      let x = e.clientX - op.offX;
      let y = e.clientY - op.offY;
      const maxX = window.innerWidth - panel.offsetWidth;
      const maxY = window.innerHeight - 40;
      x = Math.max(0, Math.min(maxX, x));
      y = Math.max(0, Math.min(maxY, y));
      _activityState.x = x;
      _activityState.y = y;
      panel.style.left = x + "px";
      panel.style.top = y + "px";
      panel.style.right = "auto";
    } else if (op.type === "resize") {
      const dx = e.clientX - op.startX;
      const dy = e.clientY - op.startY;
      let x = op.x0, y = op.y0, w = op.w0, h = op.h0;
      const d = op.dir;
      if (d.includes("e")) w = op.w0 + dx;
      if (d.includes("w")) { w = op.w0 - dx; x = op.x0 + dx; }
      if (d.includes("s")) h = op.h0 + dy;
      if (d.includes("n")) { h = op.h0 - dy; y = op.y0 + dy; }
      // Clamp min en corrigeant la position pour les handles ouest/nord
      if (w < MIN_W) {
        if (d.includes("w")) x -= (MIN_W - w);
        w = MIN_W;
      }
      if (h < MIN_H) {
        if (d.includes("n")) y -= (MIN_H - h);
        h = MIN_H;
      }
      // Clamp max et bornes écran
      const maxW = window.innerWidth - 40;
      const maxH = window.innerHeight - 40;
      if (w > maxW) {
        if (d.includes("w")) x += (w - maxW);
        w = maxW;
      }
      if (h > maxH) {
        if (d.includes("n")) y += (h - maxH);
        h = maxH;
      }
      x = Math.max(0, Math.min(window.innerWidth - w, x));
      y = Math.max(0, Math.min(window.innerHeight - 40, y));
      _activityState.x = x;
      _activityState.y = y;
      _activityState.w = w;
      _activityState.h = h;
      panel.style.left   = x + "px";
      panel.style.top    = y + "px";
      panel.style.width  = w + "px";
      panel.style.height = h + "px";
    }
  });

  document.addEventListener("mouseup", () => {
    if (!op) return;
    op = null;
    panel.classList.remove("dragging", "resizing");
    _saveActivityState();
  });

  // Clic sur la tête ancrée → libère
  head.addEventListener("click", (e) => {
    if (!_activityState.docked) return;
    if (e.target.closest("button")) return;
    toggleActivityDock();
  });
}

(function _activityInit() {
  _loadActivityState();
  _applyActivityState();
  _initActivityInteractions();
  setInterval(() => { if (_activity.some(a => a.doneAt)) _activityRender(); }, 15000);
})();

// ── Toast (compatibilité — tout est routé vers le panneau d'activité) ────────
function showToast(title, sub, type = "success", duration = 4500) {
  const status = (type === "warn" || type === "error") ? "fail" : "done";
  activityLog(title, sub || "", status);
  // Si le panneau est replié et qu'une erreur arrive, on le déplie brièvement
  // pour que l'utilisateur ne rate pas un échec.
  if (status === "fail") {
    const panel = document.getElementById("activity-panel");
    if (panel?.classList.contains("docked")) {
      _activityState.docked = false;
      _applyActivityState();
      _saveActivityState();
    }
  }
}

// ── Animation disque ──────────────────────────────────────────────────────────
function animateDiskBars() {
  document.querySelectorAll(".bar-fill").forEach(bar => {
    const orig = bar.style.background;
    bar.style.transition = "background .5s ease";
    bar.style.background = "var(--green)";
    setTimeout(() => { bar.style.background = orig; }, 1800);
  });
}

// ── Élévation admin ──────────────────────────────────────────────────────────
function requireAdmin(callback) {
  if (window.IS_ADMIN) { callback(); return; }
  showConfirm(
    "Droits administrateur requis",
    "Cette fonctionnalité nécessite les droits administrateur. L'application va se relancer avec les droits nécessaires.",
    async () => {
      await fetch("/api/relaunch-admin", { method: "POST" });
      window.close();
    }
  );
}

async function relancerAdmin() {
  await fetch("/api/relaunch-admin", { method: "POST" });
  window.close();
}

// ── Badge santé ───────────────────────────────────────────────────────────────
async function loadHealthBadge() {
  try {
    const res  = await fetch("/api/health");
    const data = await res.json();
    const pct  = Math.round((data.score / data.max) * 100);
    const color = pct >= 80 ? "var(--green)" : pct >= 50 ? "var(--amber)" : "var(--red)";
    const badge = document.getElementById("health-badge");
    if (badge) { badge.textContent = pct + "%"; badge.style.color = color; }
    // Pré-charger les données pour l'onglet santé
    window._healthCache = data;
  } catch (e) {}
}

// ── Modal aperçu ──────────────────────────────────────────────────────────────
async function showCleanPreview(selected) {
  const listEl    = document.getElementById("modal-list");
  const totalEl   = document.getElementById("modal-total-val");
  const overlayEl = document.getElementById("modal-overlay");

  listEl.innerHTML = "";
  let total = 0;

  // Vérifie si des navigateurs sont ouverts (bloque le nettoyage de leurs données)
  const hasBrowserTask = selected.some(t => ["browser", "history", "cookies"].includes(t.id));
  if (hasBrowserTask) {
    try {
      const res = await fetch("/api/locked-browsers");
      const info = await res.json();
      if (info.locked && info.locked.length) {
        const warn = document.createElement("div");
        warn.style.cssText = "padding:10px 14px;margin-bottom:8px;border-radius:4px;background:var(--amber-bg);border:1px solid var(--amber);font-size:12px;color:var(--text)";
        warn.innerHTML = `<strong>⚠ ${info.locked.join(", ")}</strong> ouvert(s) — leur cache/historique/cookies sera ignoré et non comptabilisé. Fermez-les pour un nettoyage complet.`;
        listEl.appendChild(warn);
      }
    } catch (e) {}
  }

  selected.forEach(t => {
    const bytes = sizes[t.id]?.bytes || 0;
    total += bytes;
    const row = document.createElement("div");
    row.className = "modal-row";
    row.innerHTML = `
      <div class="modal-row-name">
        <span>${t.label}</span>
      </div>
      <span class="modal-row-size">${bytes > 0 ? fmtBytes(bytes) : "—"}</span>`;
    listEl.appendChild(row);
  });

  totalEl.textContent = total > 0 ? "≈ " + fmtBytes(total) : "Déjà propre";
  overlayEl.style.display = "flex";
}

function closePreview() {
  const el = document.getElementById("modal-overlay");
  if (el) el.style.display = "none";
}

async function confirmClean() {
  closePreview();
  await _doClean();
}

// ── Nettoyage ────────────────────────────────────────────────────────────────
function startClean() {
  if (cleaning) return;
  const selected = TASKS.filter(t => t.checked);
  if (!selected.length) { addLog("Aucune catégorie sélectionnée.", "warn"); return; }
  showCleanPreview(selected);
}

async function _doClean() {
  const selected = TASKS.filter(t => t.checked && (window.IS_ADMIN || !t.admin)).map(t => t.id);
  if (!selected.length) return;

  cleaning = true;
  setCleaningUI(true);
  clearLog();

  const activityId = activityPush("Nettoyage", "run", "démarrage…", document.getElementById("btn-clean"));

  let jobId;
  try {
    const res  = await fetch("/api/clean", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tasks: selected }),
    });
    const data = await res.json();
    if (data.error) {
      addLog(data.error, "warn");
      activityDone(activityId, "échec", "fail");
      setCleaningUI(false); cleaning = false; return;
    }
    jobId = data.job_id;
  } catch (e) {
    addLog("Erreur de connexion au serveur.", "warn");
    activityDone(activityId, "échec", "fail");
    setCleaningUI(false); cleaning = false; return;
  }

  const es = new EventSource(`/api/stream/${jobId}`);
  es.onmessage = (e) => {
    const item = JSON.parse(e.data);
    if (item.type === "start") {
      addLog(item.msg); setProgress(0, item.msg);
      activityProgress(activityId, 0, item.msg);
    }
    else if (item.type === "progress") {
      const pct = Math.round((item.step / item.total) * 100);
      setProgress(pct, item.label + "…");
      activityProgress(activityId, pct, item.label);
    }
    else if (item.type === "log")  { addLog(item.msg); }
    else if (item.type === "done") {
      setProgress(100, "Terminé");
      addLog(item.msg, "ok");
      const freed = item.freed_bytes || 0;
      activityDone(activityId, freed > 0 ? fmtBytes(freed) + " libérés" : "déjà propre");
      es.close();
      onCleanDone(freed);
    } else if (item.type === "error") {
      addLog(item.msg, "warn");
      activityDone(activityId, "échec", "fail");
      es.close(); setCleaningUI(false); cleaning = false;
    }
  };
  es.onerror = () => {
    addLog("Connexion SSE interrompue.", "warn");
    activityDone(activityId, "connexion perdue", "fail");
    es.close(); setCleaningUI(false); cleaning = false;
  };
}

function onCleanDone(freedBytes) {
  cleaning = false;
  const btn = document.getElementById("btn-clean");
  if (btn) {
    const label = freedBytes > 0 ? `${fmtBytes(freedBytes)} libérés` : "Déjà propre";
    btn.innerHTML = `<span class="btn-icon">✓</span><span>${label}</span>`;
    btn.classList.remove("btn-running");
    btn.classList.add("btn-success");
    btn.disabled = false;
    setTimeout(() => {
      btn.innerHTML = "Nettoyer maintenant";
      btn.classList.remove("btn-success");
    }, 3000);
  }
  setTimeout(hideProgress, 1500);

  TASKS.filter(t => t.checked).forEach(t => {
    sizes[t.id] = { bytes: 0, fmt: "0 o" };
  });
  sortTasksBySize();
  renderTasks();
  loadDisk().then(animateDiskBars);
  loadHistory();

  window._healthCache = null;
  if (typeof healthInitialized !== "undefined") healthInitialized = false;
  loadHealthBadge();
}

// ── Progression ──────────────────────────────────────────────────────────────
function setProgress(pct, label) {
  const wrap = document.getElementById("progress-wrap");
  const fill = document.getElementById("progress-fill");
  const lbl  = document.getElementById("progress-label");
  if (!wrap) return;
  wrap.style.display = "block";
  if (fill) fill.style.width = pct + "%";
  if (lbl)  lbl.textContent  = label || "";
}
function hideProgress() {
  const wrap = document.getElementById("progress-wrap");
  if (wrap) wrap.style.display = "none";
  const fill = document.getElementById("progress-fill");
  if (fill) fill.style.width = "0%";
}

// ── UI states ─────────────────────────────────────────────────────────────────
function setCleaningUI(active) {
  const btn = document.getElementById("btn-clean");
  if (!btn) return;
  if (active) {
    btn.innerHTML = `<span class="btn-icon">⟳</span><span>Nettoyage en cours…</span>`;
    btn.classList.add("btn-running");
    btn.disabled = true;
  } else {
    btn.classList.remove("btn-running");
    btn.disabled = false;
    setTimeout(hideProgress, 1500);
  }
}

// ── Journal ──────────────────────────────────────────────────────────────────
function addLog(msg, type) {
  const box = document.getElementById("log-body");
  if (!box) return;
  const ts  = new Date().toLocaleTimeString("fr-FR");
  const cls = type === "ok" ? "log-ok" : type === "warn" ? "log-warn" : "log-msg";
  const el  = document.createElement("div");
  el.className = "log-entry";
  el.innerHTML = `<span class="log-ts">${ts}</span><span class="${cls}">${msg}</span>`;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}
function clearLog() {
  const box = document.getElementById("log-body");
  if (box) box.innerHTML = "";
}
