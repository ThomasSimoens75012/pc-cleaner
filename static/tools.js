/* tools.js — Onglet Outils */

let toolsInitialized = false;

// Registre des streams SSE actifs par section
const _activeStreams = {};

function _cancelStream(sectionId) {
  const es = _activeStreams[sectionId];
  if (es) { es.close(); delete _activeStreams[sectionId]; }
}

// Stocke l'état d'origine des boutons transformés en bouton Annuler
const _cancelBtnStore = {};

function _showCancelBtn(btnEl, sectionId, onCancel) {
  if (_cancelBtnStore[sectionId]) return;
  _cancelBtnStore[sectionId] = { el: btnEl, onclick: btnEl.onclick };
  btnEl.innerHTML = `<span class="btn-icon">✕</span><span>Annuler</span>`;
  btnEl.classList.remove("btn-primary", "btn-running");
  btnEl.classList.add("btn-cancel");
  btnEl.disabled = false;
  btnEl.onclick = (e) => {
    e.stopPropagation();
    _cancelStream(sectionId);
    _removeCancelBtn(sectionId);
    onCancel();
  };
}

function _removeCancelBtn(sectionId) {
  const stored = _cancelBtnStore[sectionId];
  if (!stored) return;
  const btnEl = stored.el;
  btnEl.classList.remove("btn-cancel");
  btnEl.classList.add("btn-primary");
  btnEl.onclick = stored.onclick;
  delete _cancelBtnStore[sectionId];
}

// ── Sélecteur de dossier natif ────────────────────────────────────────────────

async function browseFolder(inputId) {
  try {
    const res  = await fetch("/api/browse-folder");
    const data = await res.json();
    if (data.folder) {
      const el = document.getElementById(inputId);
      if (el) el.value = data.folder;
    }
  } catch (e) {
    showToast("Erreur", "Impossible d'ouvrir le sélecteur de dossier.", "warn");
  }
}

// ── Helpers animation boutons ─────────────────────────────────────────────────

function _activityLabelFor(btn) {
  if (!btn) return "Analyse";
  if (btn.dataset.activity) return btn.dataset.activity;
  const section = btn.closest("section, .card, div")?.parentElement;
  let el = btn;
  while (el && el !== document.body) {
    const t = el.querySelector?.(".tool-section-title");
    if (t && t.textContent.trim()) return t.textContent.trim();
    el = el.parentElement;
  }
  const tabPanel = btn.closest(".tab-panel");
  const title = tabPanel?.querySelector(".page-title");
  return title ? title.textContent.trim() : "Analyse";
}

function _btnScan(btn, label = "Analyse…") {
  if (!btn) return;
  btn.dataset.idle = btn.innerHTML;
  btn.innerHTML = `<span class="btn-icon">⟳</span><span>${label}</span>`;
  btn.classList.add("btn-running");
  btn.disabled = true;
  _scanSpinnerShow(btn);
  if (typeof activityPush === "function" && !btn.hasAttribute("data-no-activity")) {
    btn._activityId = activityPush(_activityLabelFor(btn), "run", "en cours…", btn);
  }
}

function _scanSpinnerShow(btn) {
  const targetId = btn?.dataset?.logTarget;
  if (!targetId) return;
  const el = document.getElementById(targetId);
  if (!el) return;
  el.querySelectorAll(".scan-spinner-row").forEach(n => n.remove());
  const row = document.createElement("div");
  row.className = "scan-spinner-row";
  row.innerHTML = `<span class="scan-spinner"></span><span>Analyse en cours…</span>`;
  el.prepend(row);
}

function _scanSpinnerHide(btn) {
  const targetId = btn?.dataset?.logTarget;
  if (!targetId) return;
  const el = document.getElementById(targetId);
  if (!el) return;
  el.querySelectorAll(".scan-spinner-row").forEach(n => n.remove());
}

function _applyAdminLock(row, cb, needsAdmin) {
  if (!needsAdmin) return;
  row.classList.add("row-locked");
  cb.disabled = true;
  cb.checked = false;
  const badge = document.createElement("span");
  badge.className = "admin-badge";
  badge.textContent = "Admin requis";
  badge.title = "Relancez l'application en mode administrateur pour pouvoir supprimer cet élément.";
  row.appendChild(badge);
}

function _logAppend(logId, msg) {
  const logEl = document.getElementById(logId);
  if (!logEl) return;
  const d = document.createElement("div");
  d.className = "log-entry";
  d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
}

function _btnDone(btn, label) {
  if (!btn) return;
  btn.disabled = false;
  btn.classList.remove("btn-running");
  _scanSpinnerHide(btn);
  btn.innerHTML = `<span class="btn-icon">✓</span><span>${label}</span>`;
  btn.classList.add("btn-success");
  if (btn._activityId != null && typeof activityDone === "function") {
    activityDone(btn._activityId, label || "terminé");
    btn._activityId = null;
  }
  setTimeout(() => {
    btn.innerHTML = btn.dataset.idle || label;
    btn.classList.remove("btn-success");
  }, 2500);
}

function _btnReset(btn) {
  if (!btn) return;
  btn.disabled = false;
  btn.classList.remove("btn-running", "btn-success");
  if (btn.dataset.idle) btn.innerHTML = btn.dataset.idle;
  _scanSpinnerHide(btn);
  if (btn._activityId != null && typeof activityDone === "function") {
    activityDone(btn._activityId, "terminé");
    btn._activityId = null;
  }
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

function _skeleton(n = 3, withBtn = false) {
  const widths = [[55, 30], [45, 25], [62, 38], [50, 28]];
  return Array.from({ length: n }, (_, i) => {
    const [w1, w2] = widths[i % widths.length];
    const right = withBtn
      ? `<div class="skeleton-box" style="width:70px;height:26px;border-radius:7px"></div>`
      : `<div class="skeleton-box" style="width:50px;height:13px"></div>`;
    return `<div class="skeleton-row">
      <div class="skeleton-box" style="width:30px;height:30px;border-radius:7px"></div>
      <div style="flex:1">
        <div class="skeleton-box" style="width:${w1}%;height:13px;margin-bottom:6px"></div>
        <div class="skeleton-box" style="width:${w2}%;height:11px"></div>
      </div>
      ${right}
    </div>`;
  }).join("");
}

// ── Helper en-tête de liste unifié ────────────────────────────────────────────

function _makeSelHeader(el, { countText, deleteId, deleteLabel = "Supprimer la sélection", deleteFn, sortKeys = [], sortKey, sortDir, onSort, noSelAll = false }) {
  const header = document.createElement("div"); header.className = "reg-header";
  const left   = document.createElement("div"); left.className   = "list-header";

  if (!noSelAll) {
    const selAll = document.createElement("input"); selAll.type = "checkbox"; selAll.checked = true;
    selAll.className = "sel-all"; selAll.title = "Tout sélectionner / désélectionner";
    selAll.addEventListener("change", () => el.querySelectorAll("input[type=checkbox]").forEach(cb => { if (!cb.disabled) cb.checked = selAll.checked; }));
    left.appendChild(selAll);
  }

  const span = document.createElement("span"); span.textContent = countText;
  left.appendChild(span);

  if (sortKeys.length) {
    const sortDiv = document.createElement("div"); sortDiv.style.cssText = "display:flex;gap:3px;margin-left:6px;";
    sortKeys.forEach(([key, label]) => {
      const pill = document.createElement("span");
      pill.className = "tweak-filter-btn" + (sortKey === key ? " active" : "");
      pill.textContent = label + (sortKey === key ? (sortDir === -1 ? " ↓" : " ↑") : "");
      pill.addEventListener("click", () => onSort(key));
      sortDiv.appendChild(pill);
    });
    left.appendChild(sortDiv);
  }

  if (deleteFn) {
    const btnDel = document.createElement("button"); btnDel.className = "btn-ghost";
    if (deleteId) btnDel.id = deleteId;
    btnDel.style.cssText = "font-size:12px;padding:6px 12px;flex-shrink:0";
    btnDel.textContent = deleteLabel;
    btnDel.addEventListener("click", deleteFn);
    header.append(left, btnDel);
  } else {
    header.appendChild(left);
  }
  return header;
}

function initTools() {
  if (toolsInitialized) return;
  toolsInitialized = true;
  loadStartup();
  loadApps();
  loadExtensions();
  loadRestorePoints();
  loadPrivacy();
  _setDefaultInstallerFolder();
}

// ── Démarrage ─────────────────────────────────────────────────────────────────

let _startupEntries = [];

async function loadStartup() {
  const el = document.getElementById("startup-list");
  el.innerHTML = _skeleton(4);
  try {
    const res = await fetch("/api/autoruns");
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _startupEntries = data;
    renderStartup();
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderStartup() {
  const el = document.getElementById("startup-list");
  if (!_startupEntries.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }
  const isAdmin = window.IS_ADMIN === true || document.body.dataset.admin === "1";
  const sorted = [..._startupEntries].sort((a, b) => {
    const s = (a.source || "").localeCompare(b.source || "");
    return s || a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
  el.innerHTML = "";
  sorted.forEach(e => {
    const row = document.createElement("div");
    row.className = "tool-row";
    const needsAdmin = (e.id || "").startsWith("reg:HKLM");
    if (needsAdmin && !isAdmin) row.classList.add("tool-row-locked");

    const info = document.createElement("div");
    info.className = "tool-info";
    const nameD = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = e.name;
    const subD  = document.createElement("div"); subD.className  = "tool-sub";  subD.textContent  = e.command;
    info.append(nameD, subD);

    const meta  = document.createElement("div"); meta.className = "tool-meta";
    const badge = document.createElement("span"); badge.className = "source-badge"; badge.textContent = e.source;
    meta.appendChild(badge);
    if (needsAdmin && !isAdmin) {
      const adm = document.createElement("span");
      adm.className = "source-badge";
      adm.textContent = "admin";
      adm.title = "Relancez en administrateur pour modifier";
      meta.appendChild(adm);
    }

    const sw = document.createElement("div");
    sw.className = "switch" + (e.enabled ? " on" : "");
    if (needsAdmin && !isAdmin) {
      sw.classList.add("disabled");
      sw.title = "Droits administrateur requis";
    } else {
      sw.title = e.enabled ? "Désactiver" : "Activer";
      sw.addEventListener("click", () => toggleStartup(e, sw));
    }

    row.append(info, meta, sw);
    el.appendChild(row);
  });
}

async function toggleStartup(entry, swEl) {
  if (swEl.dataset.busy) return;
  const newEnabled = !swEl.classList.contains("on");
  swEl.dataset.busy = "1";
  try {
    const res  = await fetch("/api/autoruns/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: entry.id, enabled: newEnabled }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "Erreur registre");
    entry.enabled = newEnabled;
    renderStartup();
  } catch (e) {
    showToast("Erreur", "Impossible de modifier le démarrage.", "warn");
    delete swEl.dataset.busy;
  }
}

// ── Applications installées v2 ───────────────────────────────────────────────

let allApps     = [];
let appSortKey  = "size_bytes";
let appSortDir  = -1;
let _appFilter  = "all";   // all | broken | unused | big | category:X | source:winget/scoop/choco
let _appDeep    = false;

async function loadApps(deep = false) {
  _appDeep = deep;
  const el = document.getElementById("apps-list");
  el.innerHTML = _skeleton(6, true);
  const url = deep ? "/api/apps?deep=1" : "/api/apps";
  const actId = deep ? activityPush("Scan approfondi apps", "run", "Calcul des tailles réelles…") : null;
  try {
    const res = await fetch(url);
    allApps = await res.json();
    if (allApps.error) throw new Error(allApps.error);
    _renderAppFilters();
    renderApps();
    if (actId) activityDone(actId, `${allApps.length} apps scannées`);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
    if (actId) activityDone(actId, "Échec", "fail");
  }
}

function _monthsAgo(iso) {
  if (!iso) return null;
  const now = new Date();
  const then = new Date(iso);
  const ms = now - then;
  return ms / (1000 * 60 * 60 * 24 * 30);
}

function _appsMatchingFilter(filter) {
  if (filter === "all")     return allApps;
  if (filter === "broken")  return allApps.filter(a => a.broken);
  if (filter === "unused")  return allApps.filter(a => {
    const m = _monthsAgo(a.last_used);
    return m !== null && m >= 6;
  });
  if (filter === "big")     return allApps.filter(a => a.size_bytes >= 1024 * 1024 * 1024);
  if (filter === "no_pub")  return allApps.filter(a => !a.publisher);
  if (filter === "winget")  return allApps.filter(a => a.winget_id);
  if (filter.startsWith("category:")) {
    const cat = filter.slice(9);
    return allApps.filter(a => a.category === cat);
  }
  return allApps;
}

function _renderAppFilters() {
  const el = document.getElementById("apps-filters");
  if (!el) return;
  const cats = [...new Set(allApps.map(a => a.category))].sort();
  const broken = allApps.filter(a => a.broken).length;
  const unused = allApps.filter(a => { const m = _monthsAgo(a.last_used); return m !== null && m >= 6; }).length;
  const big    = allApps.filter(a => a.size_bytes >= 1024 * 1024 * 1024).length;
  const winget = allApps.filter(a => a.winget_id).length;

  const btn = (f, label, count) => `
    <button class="tweak-filter-btn${_appFilter === f ? " active" : ""}" onclick="setAppFilter('${f}')">
      ${label}${count != null ? ` <span class="c">${count}</span>` : ""}
    </button>`;

  let html = btn("all", "Tout", allApps.length);
  if (broken) html += btn("broken", "Cassées", broken);
  if (unused) html += btn("unused", "Inutilisées > 6 mois", unused);
  if (big)    html += btn("big",    "> 1 Go",  big);
  if (winget) html += btn("winget", "Winget",  winget);
  cats.forEach(c => {
    const n = allApps.filter(a => a.category === c).length;
    html += btn("category:" + c, c, n);
  });
  el.innerHTML = html;
  el.style.display = "flex";
}

function setAppFilter(f) {
  _appFilter = f;
  _renderAppFilters();
  renderApps();
}

function sortApps(key) {
  if (appSortKey === key) { appSortDir *= -1; }
  else { appSortKey = key; appSortDir = (key === "size_bytes" || key === "launch_count") ? -1 : 1; }
  renderApps();
}

function renderApps() {
  const el = document.getElementById("apps-list");
  const q = (document.getElementById("apps-search")?.value || "").toLowerCase();
  let list = _appsMatchingFilter(_appFilter);
  if (q) {
    list = list.filter(a =>
      a.name.toLowerCase().includes(q) ||
      (a.publisher || "").toLowerCase().includes(q));
  }
  if (!list.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    document.getElementById("apps-count").textContent = `0 sur ${allApps.length}`;
    return;
  }

  const sorted = [...list].sort((a, b) => {
    const av = a[appSortKey] ?? "";
    const bv = b[appSortKey] ?? "";
    if (typeof av === "string") return appSortDir * av.localeCompare(bv || "");
    return appSortDir * ((bv || 0) - (av || 0));
  });

  el.innerHTML = "";
  const cols = [
    { key: "name",         label: "Nom",         style: "flex:1;min-width:0" },
    { key: "publisher",    label: "Éditeur",     style: "min-width:100px;text-align:right" },
    { key: "size_bytes",   label: "Taille",      style: "min-width:80px;text-align:right" },
    { key: "last_used",    label: "Dern. util.", style: "min-width:90px;text-align:right" },
  ];
  const header = document.createElement("div");
  header.className = "tool-row tool-header";
  header.innerHTML = cols.map(c => `
    <div style="${c.style};cursor:pointer;user-select:none" onclick="sortApps('${c.key}')">
      <strong>${c.label}</strong>${appSortKey === c.key ? (appSortDir === -1 ? " ↓" : " ↑") : ""}
    </div>`).join("") + `<div style="width:110px"></div>`;
  el.appendChild(header);

  sorted.forEach(app => {
    const row = document.createElement("div");
    row.className = "tool-row";

    const bigApp = app.size_bytes >= 1024 * 1024 * 1024;
    const monthsAgo = _monthsAgo(app.last_used);
    const unused = monthsAgo !== null && monthsAgo >= 6;

    const badges = [];
    if (app.broken)     badges.push('<span style="font-size:9px;background:var(--red-bg);color:var(--red);padding:1px 5px;border-radius:3px;font-weight:600">CASSÉE</span>');
    if (unused)         badges.push(`<span style="font-size:9px;background:var(--amber-bg);color:var(--amber);padding:1px 5px;border-radius:3px;font-weight:600">${Math.round(monthsAgo)} mois</span>`);
    if (app.winget_id)  badges.push('<span style="font-size:9px;background:var(--bg3);color:var(--text-dim);padding:1px 5px;border-radius:3px">winget</span>');
    (app.extra_sources || []).forEach(s => badges.push(`<span style="font-size:9px;background:var(--bg3);color:var(--text-dim);padding:1px 5px;border-radius:3px">${s}</span>`));

    const nameDiv = document.createElement("div");
    nameDiv.className = "tool-info";
    nameDiv.innerHTML = `
      <div class="tool-name" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        ${app.name}${bigApp ? ' <span style="font-size:10px;color:var(--amber);font-weight:600">●</span>' : ''}
        ${badges.join(" ")}
      </div>
      <div class="tool-sub" style="font-size:10px;color:var(--text-dim)">${app.category}${app.version ? " · v" + app.version : ""}${app.launch_count ? " · " + app.launch_count + " lancements" : ""}</div>`;

    const pubDiv = document.createElement("div");
    pubDiv.className = "tool-meta dim";
    pubDiv.style.cssText = "min-width:100px;text-align:right;font-size:11px";
    pubDiv.textContent = app.publisher || "—";

    const sizeDiv = document.createElement("div");
    sizeDiv.className = "tool-meta";
    sizeDiv.style.cssText = `min-width:80px;text-align:right;font-weight:${bigApp ? "700" : "400"};color:${bigApp ? "var(--amber)" : "inherit"};font-variant-numeric:tabular-nums`;
    sizeDiv.textContent = app.size_fmt;
    if (app.size_source === "real") sizeDiv.title = "Taille réelle mesurée depuis InstallLocation";

    const luDiv = document.createElement("div");
    luDiv.className = "tool-meta dim";
    luDiv.style.cssText = "min-width:90px;text-align:right;font-size:11px";
    if (app.last_used) {
      const d = new Date(app.last_used);
      luDiv.textContent = d.toLocaleDateString("fr-FR", { month: "short", year: "numeric" });
    } else {
      luDiv.textContent = "—";
    }

    const actDiv = document.createElement("div");
    actDiv.style.cssText = "width:110px;text-align:right;flex-shrink:0;display:flex;flex-direction:column;gap:3px;align-items:flex-end";

    if (app.broken) {
      const rm = document.createElement("button");
      rm.className = "btn-ghost";
      rm.textContent = "Supprimer entrée";
      rm.style.cssText = "font-size:10px;padding:3px 8px";
      rm.title = "Supprime l'entrée orpheline du registre (l'exe n'existe plus)";
      rm.addEventListener("click", () => removeBrokenEntry(app, rm));
      actDiv.appendChild(rm);
    } else if (app.uninstall_string || app.winget_id) {
      const btn = document.createElement("button");
      btn.className = "btn-uninstall";
      btn.textContent = "Désinstaller";
      btn.addEventListener("click", () => uninstallApp(app, btn, false));
      actDiv.appendChild(btn);

      if (app.winget_id || app.quiet_uninstall) {
        const silent = document.createElement("button");
        silent.className = "btn-ghost";
        silent.textContent = "Silencieux";
        silent.style.cssText = "font-size:10px;padding:2px 8px";
        silent.title = "Désinstallation sans popup (winget ou QuietUninstallString)";
        silent.addEventListener("click", () => uninstallApp(app, silent, true));
        actDiv.appendChild(silent);
      }
    } else {
      actDiv.innerHTML = `<span class="dim" style="font-size:12px">—</span>`;
    }

    row.append(nameDiv, pubDiv, sizeDiv, luDiv, actDiv);
    el.appendChild(row);
  });

  document.getElementById("apps-count").textContent =
    `${sorted.length} sur ${allApps.length}${_appDeep ? " (tailles réelles)" : ""}`;
}

function filterApps() {
  renderApps();
}

async function uninstallApp(app, btn, silent) {
  const msg = silent
    ? `Désinstaller "${app.name}" en mode silencieux ? Aucune popup ne s'affichera.`
    : `Désinstaller "${app.name}" ? Windows ouvrira le programme de désinstallation.`;
  showConfirm(
    `Désinstaller "${app.name}" ?`,
    msg,
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "En cours…"; }
      const actId = activityPush("Désinstallation", "run", app.name, { tab: "outils" });
      try {
        const res  = await fetch("/api/apps/uninstall", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id:               app.id,
            winget_id:        app.winget_id,
            silent:           silent,
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          activityDone(actId, "Échec", "fail");
          showToast("Erreur", data.error || "Impossible de lancer la désinstallation.", "warn");
          if (btn) { btn.disabled = false; btn.textContent = silent ? "Silencieux" : "Désinstaller"; }
        } else {
          activityDone(actId, silent ? "Désinstallation silencieuse lancée" : "Désinstalleur ouvert");
          if (!silent) {
            showToast("Désinstallation lancée", `« ${app.name} »`, "success");
          }
          // Proposer le scan de résidus
          setTimeout(() => checkResiduals(app), 2000);
        }
      } catch (e) {
        activityDone(actId, "Échec", "fail");
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = silent ? "Silencieux" : "Désinstaller"; }
      }
    }
  );
}

async function removeBrokenEntry(app, btn) {
  if (!window.IS_ADMIN && app.reg_hive === "HKLM") {
    showToast("Droits admin requis", "Relancez en administrateur pour modifier HKLM.", "warn");
    return;
  }
  showConfirm(
    "Supprimer l'entrée registre ?",
    `Cette action retire "${app.name}" de la liste "Programmes et fonctionnalités" de Windows. Elle ne désinstalle rien (l'exe est déjà manquant).`,
    async () => {
      btn.disabled = true;
      try {
        const res = await fetch("/api/apps/remove-entry", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reg_hive: app.reg_hive, reg_path: app.reg_path }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || "Erreur");
        showToast("Entrée supprimée", app.name, "success");
        btn.closest(".tool-row")?.remove();
        allApps = allApps.filter(a => a.id !== app.id);
        _renderAppFilters();
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        btn.disabled = false;
      }
    }
  );
}

async function checkResiduals(app) {
  try {
    const res = await fetch("/api/apps/residuals", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: app.name, install_location: app.install_location }),
    });
    const data = await res.json();
    if (!data.items || !data.items.length) return;
    const paths = data.items.map(r => r.path).join("\n");
    if (confirm(`Résidus trouvés pour "${app.name}" (${data.total_fmt}) :\n\n${paths}\n\nEnvoyer à la corbeille ?`)) {
      const r = await fetch("/api/recycle-bin/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: data.items.map(i => i.path) }),
      });
      const rd = await r.json();
      if (rd.moved) {
        showToast("Résidus nettoyés", `${rd.moved} dossier(s) envoyés à la corbeille`, "success");
      }
    }
  } catch (e) {}
}

function openSettingsUri(uri) {
  fetch("/api/open-settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uri }),
  }).catch(() => {});
}

// ── Doublons (fichiers + dossiers unifiés) ───────────────────────────────────

let _dupeMode = "files"; // "files" | "folders"

function setDupeMode(mode) {
  _dupeMode = mode;
  document.getElementById("dupe-mode-files").classList.toggle("active", mode === "files");
  document.getElementById("dupe-mode-folders").classList.toggle("active", mode === "folders");
  const sizeInput = document.getElementById("dupe-minsize");
  const sizeLabel = document.getElementById("dupe-minsize-label");
  const show = mode === "files";
  sizeInput.style.display = show ? "" : "none";
  sizeLabel.style.display = show ? "" : "none";
  // Reset résultats quand on change de mode
  document.getElementById("dupe-results").innerHTML = "";
  document.getElementById("dupe-log").innerHTML = "";
}

function startDuplicateUnifiedScan() {
  if (_dupeMode === "folders") {
    startDuplicateFolderScan();
  } else {
    startDuplicateScan();
  }
}

let duplicateGroups = [];

async function startDuplicateScan() {
  const folder  = document.getElementById("dupe-folder").value.trim();
  const minSize = parseInt(document.getElementById("dupe-minsize").value) || 100;
  if (!folder) { showToast("Dossier requis", "Entrez un dossier à analyser.", "warn"); return; }

  const resultEl = document.getElementById("dupe-results");
  const btnEl    = document.getElementById("btn-scan-dupes");

  document.getElementById("dupe-log").innerHTML = "";
  resultEl.innerHTML = "";
  duplicateGroups = [];
  _btnScan(btnEl, "Analyse…");

  const dupeLog = (msg) => _logAppend("dupe-log", msg);

  try {
    const res  = await fetch("/api/duplicates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, min_size_kb: minSize }),
    });
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    _activeStreams["dupes"] = es;
    _showCancelBtn(btnEl, "dupes", () => { _btnReset(btnEl); document.getElementById("dupe-log").innerHTML = ""; });
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    dupeLog(item.msg);
      if (item.type === "result") renderDuplicates(item.groups, item.total_fmt);
      if (item.type === "done") {
        es.close(); _removeCancelBtn("dupes");
        _btnReset(btnEl);
      }
    };
    es.onerror = () => { es.close(); _removeCancelBtn("dupes"); _btnReset(btnEl); };

  } catch (err) {
    dupeLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderDuplicates(groups, totalFmt) {
  // Trier par espace gaspillé décroissant (les plus gros doublons en premier)
  const sorted = [...groups].sort((a, b) => {
    const wa = a.reduce((s, f) => s + f.size, 0);
    const wb = b.reduce((s, f) => s + f.size, 0);
    return wb - wa;
  });
  duplicateGroups = sorted;
  const el = document.getElementById("dupe-results");
  if (!sorted.length) {
    el.innerHTML = "";
    _logAppend("dupe-log", "Aucun résultat.");
    return;
  }

  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText: `${sorted.length} groupe(s) — ${totalFmt} récupérables`,
    deleteId:  "btn-delete-dupes",
    deleteFn:  deleteSelectedDupes,
  }));

  _watchSelSize(el, document.getElementById("btn-delete-dupes"));
  _renderBatched(sorted, (files, gi) => {
    const group = document.createElement("div");
    group.className = "dupe-group";
    const groupSize = files.reduce((s, f) => s + f.size, 0);
    group.innerHTML = `<div class="dupe-group-title">${files.length} fichiers identiques — ${fmtBytesTools(groupSize)}</div>`;
    let keptIdx = 0;
    const renderRows = () => {
      [...group.querySelectorAll(".dupe-row")].forEach(r => r.remove());
      files.forEach((f, fi) => {
        const row = document.createElement("div"); row.className = "dupe-row";
        const cbId = `dupe-${gi}-${fi}`; const isKept = fi === keptIdx;
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path; cb.dataset.size = f.size;
        cb.checked = !isKept; cb.disabled = isKept;
        cb.title = isKept ? "Ce fichier sera conservé" : "";
        const lbl = document.createElement("label"); lbl.htmlFor = cbId; lbl.className = "dupe-path"; lbl.style.opacity = isKept ? "0.55" : "";
        const sizeSpan = document.createElement("span"); sizeSpan.className = "dupe-size"; sizeSpan.textContent = f.size_fmt;
        lbl.appendChild(sizeSpan); lbl.appendChild(document.createTextNode(" " + f.path));
        if (isKept) {
          const badge = document.createElement("span"); badge.className = "source-badge";
          badge.style.cssText = "margin-left:6px;color:var(--green);border-color:var(--green)"; badge.textContent = "↩ conservé";
          lbl.appendChild(badge); row.append(cb, lbl);
        } else {
          const keepBtn = document.createElement("button"); keepBtn.className = "btn-ghost"; keepBtn.textContent = "Conserver celui-ci";
          keepBtn.style.cssText = "font-size:11px;padding:2px 8px;margin-left:8px;flex-shrink:0";
          keepBtn.addEventListener("click", (e) => { e.preventDefault(); keptIdx = fi; renderRows(); });
          row.append(cb, lbl, keepBtn);
        }
        _applyAdminLock(row, cb, f.needs_admin);
        group.appendChild(row);
      });
    };
    renderRows();
    return group;
  }, el);
}

// ── Dossiers dupliqués ────────────────────────────────────────────────────────

let duplicateFolderGroups = [];

// ── Helper : filter pills pour services/tasks ──────────────────────────────

const _SERVICE_CATEGORY_LABELS = {
  "all":                 "Tout voir",
  "telemetry":           "Télémétrie",
  "gaming":              "Gaming",
  "legacy":              "Legacy",
  "cloud_sync":          "Cloud sync",
  "privacy":             "Confidentialité",
  // Catégories dynamiques (mode expert)
  "protected":           "Protégés",
  "curated_disable":     "Recommandés",
  "microsoft_optional":  "Microsoft optionnel",
  "third_party":         "Tiers",
};

function _renderCategoryFilters(filterElId, items, currentFilter, onFilter) {
  const el = document.getElementById(filterElId);
  if (!el) return;
  el.style.display = items.length ? "flex" : "none";
  const counts = { all: items.length };
  for (const it of items) {
    const c = it.category || "legacy";
    counts[c] = (counts[c] || 0) + 1;
  }
  const order = ["all", "protected", "curated_disable", "telemetry", "gaming", "legacy", "cloud_sync", "privacy", "microsoft_optional", "third_party"];
  el.innerHTML = order
    .filter(t => t === "all" || counts[t])
    .map(t => {
      const label = _SERVICE_CATEGORY_LABELS[t] || t;
      const cls = t === currentFilter ? "active" : "";
      return `<button class="tweak-filter-btn ${cls}" data-filter="${t}">${label} <span class="c">${counts[t] || 0}</span></button>`;
    }).join("");
  // Bind clicks
  el.querySelectorAll(".tweak-filter-btn").forEach(b => {
    b.addEventListener("click", () => onFilter(b.dataset.filter));
  });
}

// ── Services Windows (admin) ─────────────────────────────────────────────────

let _services = [];
let _servicesFilter = "all";
let _servicesIsAdmin = false;

let _servicesMode = "curated";  // "curated" | "all"

async function loadServices(mode) {
  if (mode) _servicesMode = mode;
  const btn = document.getElementById("btn-load-services");
  const el  = document.getElementById("services-list");
  _btnScan(btn, "Chargement…");
  try {
    const res  = await fetch("/api/services?mode=" + encodeURIComponent(_servicesMode));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erreur serveur");
    _services = data.services || [];
    _servicesFilter = "all";
    _servicesIsAdmin = !!data.is_admin;
    _renderServices(data.is_admin);
    _renderTweakFilters();
    _renderTweakChart();
    // Met à jour les boutons de mode
    document.querySelectorAll(".svc-mode-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.mode === _servicesMode);
    });
    _btnReset(btn);
  } catch (e) {
    el.innerHTML = `<div class="tool-error" style="padding:20px">Erreur : ${e.message}</div>`;
    _btnReset(btn);
  }
}

function _renderServices(isAdmin) {
  const el = document.getElementById("services-list");
  const visible = _services.filter(s => s.exists);
  if (!visible.length) {
    el.innerHTML = `<div class="tool-empty" style="padding:20px">Aucun service détecté (tous déjà désactivés ou absents de cette version de Windows).</div>`;
    document.getElementById("services-filters").style.display = "none";
    return;
  }

  // Filter pills
  _renderCategoryFilters("services-filters", visible, _servicesFilter, (tag) => {
    _servicesFilter = tag;
    _renderServices(isAdmin);
  });

  const filtered = _servicesFilter === "all"
    ? visible
    : visible.filter(s => s.category === _servicesFilter);

  const bucket = { telemetry: [], gaming: [], legacy: [], cloud_sync: [], privacy: [] };
  filtered.forEach(s => { (bucket[s.category] || bucket.legacy).push(s); });
  const order = [
    ["telemetry",  "Télémétrie"],
    ["gaming",     "Gaming (si pas gamer)"],
    ["legacy",     "Fonctions legacy"],
    ["cloud_sync", "Synchronisation cloud"],
    ["privacy",    "Vie privée"],
  ];

  el.innerHTML = order.map(([cat, label]) => {
    const items = bucket[cat] || [];
    if (!items.length) return "";
    const bulkHtml = isAdmin
      ? `<span class="bulk">
          <button class="btn-ghost bulk-btn" onclick="bulkToggleServicesCategory('${cat}', false)">Tout désactiver</button>
          <button class="btn-ghost bulk-btn" onclick="bulkToggleServicesCategory('${cat}', true)">Tout activer</button>
        </span>`
      : "";
    return `
      <div class="tweak-group-title" style="padding:14px 16px 6px" data-svc-cat="${cat}">
        <span>${label}</span>
        ${bulkHtml}
      </div>
      ${items.map(s => _serviceRowHtml(s, isAdmin)).join("")}
    `;
  }).join("");
}

async function bulkToggleServicesCategory(category, targetEnabled) {
  const changes = _services
    .filter(s => s.exists && s.category === category && s.active !== targetEnabled)
    .map(s => ({ name: s.name, enabled: targetEnabled }));
  if (!changes.length) return;

  const btns = document.querySelectorAll(`.tweak-group-title[data-svc-cat="${category}"] .bulk button`);
  btns.forEach(b => b.disabled = true);

  try {
    const res  = await fetch("/api/services/set-batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    });
    const data = await res.json();
    (data.results || []).forEach(r => {
      if (!r.ok) return;
      const svc = _services.find(s => s.name === r.name);
      if (svc) svc.active = r.enabled;
      const row = document.querySelector(`#services-list .tweak-row[data-service="${r.name}"]`);
      if (row) {
        const cb = row.querySelector("input[type=checkbox]");
        if (cb) cb.checked = !!r.enabled;
        row.classList.add("tweak-ok");
        setTimeout(() => row.classList.remove("tweak-ok"), 600);
      }
    });
    _renderTweakChart();
    if (data.fail_count > 0) {
      showToast("Bulk services partiel", `${data.ok_count} appliqué(s), ${data.fail_count} échec(s)`, "warn");
    }
  } catch (e) {
    showToast("Erreur batch services", e.message, "warn");
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

function _serviceRowHtml(svc, isAdmin) {
  const locked = !isAdmin;
  return `
    <div class="tweak-row${locked ? ' row-locked' : ''}" data-service="${svc.name}" style="padding:10px 16px">
      <div class="tweak-info">
        <div class="tweak-label">${_escapeHtml(svc.label)}${locked ? ' <span class="admin-badge">Admin requis</span>' : ''}</div>
        <div class="tweak-desc">${_escapeHtml(svc.desc)}</div>
      </div>
      <label class="sw">
        <input type="checkbox" ${svc.active ? "checked" : ""} ${locked ? "disabled" : ""} onchange="toggleService('${svc.name}', this)">
        <span class="slider"></span>
      </label>
    </div>
  `;
}

async function toggleService(name, checkbox) {
  const row = checkbox.closest(".tweak-row");
  const sw = row.querySelector(".sw");
  sw.classList.add("busy");
  row.classList.remove("tweak-error");
  const errEl = row.querySelector(".tweak-err-msg");
  if (errEl) errEl.remove();
  const enabled = checkbox.checked;
  try {
    const res = await fetch("/api/services/set", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, enabled }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "Échec");
    const svc = _services.find(s => s.name === name);
    if (svc) svc.active = enabled;
    row.classList.add("tweak-ok");
    setTimeout(() => row.classList.remove("tweak-ok"), 600);
    if (typeof _renderTweakChart === "function") _renderTweakChart();
  } catch (e) {
    checkbox.checked = !checkbox.checked;
    row.classList.add("tweak-error");
    const msg = document.createElement("div");
    msg.className = "tweak-err-msg";
    msg.textContent = "Échec : " + e.message;
    row.appendChild(msg);
    setTimeout(() => {
      row.classList.remove("tweak-error");
      msg.remove();
    }, 5000);
  } finally {
    sw.classList.remove("busy");
  }
}

// ── Tâches planifiées (admin) ────────────────────────────────────────────────

let _scheduledTasks = [];
let _tasksFilter = "all";
let _tasksIsAdmin = false;

let _tasksMode = "curated";

async function loadScheduledTasks(mode) {
  if (mode) _tasksMode = mode;
  const btn = document.getElementById("btn-load-tasks");
  const el  = document.getElementById("tasks-list");
  _btnScan(btn, "Chargement…");
  try {
    const res  = await fetch("/api/scheduled-tasks?mode=" + encodeURIComponent(_tasksMode));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erreur serveur");
    _scheduledTasks = data.tasks || [];
    _tasksFilter = "all";
    _tasksIsAdmin = !!data.is_admin;
    _renderScheduledTasks(data.is_admin);
    _renderTweakFilters();
    _renderTweakChart();
    document.querySelectorAll(".task-mode-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.mode === _tasksMode);
    });
    _btnReset(btn);
  } catch (e) {
    el.innerHTML = `<div class="tool-error" style="padding:20px">Erreur : ${e.message}</div>`;
    _btnReset(btn);
  }
}

function _renderScheduledTasks(isAdmin) {
  const el = document.getElementById("tasks-list");
  const visible = _scheduledTasks.filter(t => t.exists);
  if (!visible.length) {
    el.innerHTML = `<div class="tool-empty" style="padding:20px">Aucune tâche détectée (toutes déjà désactivées ou absentes).</div>`;
    document.getElementById("tasks-filters").style.display = "none";
    return;
  }

  _renderCategoryFilters("tasks-filters", visible, _tasksFilter, (tag) => {
    _tasksFilter = tag;
    _renderScheduledTasks(isAdmin);
  });

  const filtered = _tasksFilter === "all"
    ? visible
    : visible.filter(t => t.category === _tasksFilter);

  const bucket = { telemetry: [], legacy: [], privacy: [] };
  filtered.forEach(t => { (bucket[t.category] || bucket.legacy).push(t); });
  const order = [
    ["telemetry", "Télémétrie"],
    ["legacy",    "Fonctions legacy"],
    ["privacy",   "Vie privée"],
  ];

  el.innerHTML = order.map(([cat, label]) => {
    const items = bucket[cat] || [];
    if (!items.length) return "";
    const bulkHtml = isAdmin
      ? `<span class="bulk">
          <button class="btn-ghost bulk-btn" onclick="bulkToggleTasksCategory('${cat}', false)">Tout désactiver</button>
          <button class="btn-ghost bulk-btn" onclick="bulkToggleTasksCategory('${cat}', true)">Tout activer</button>
        </span>`
      : "";
    return `
      <div class="tweak-group-title" style="padding:14px 16px 6px" data-task-cat="${cat}">
        <span>${label}</span>
        ${bulkHtml}
      </div>
      ${items.map(t => _scheduledTaskRowHtml(t, isAdmin)).join("")}
    `;
  }).join("");
}

async function bulkToggleTasksCategory(category, targetEnabled) {
  const changes = _scheduledTasks
    .filter(t => t.exists && t.category === category && t.active !== targetEnabled)
    .map(t => ({ path: t.path, enabled: targetEnabled }));
  if (!changes.length) return;

  const btns = document.querySelectorAll(`.tweak-group-title[data-task-cat="${category}"] .bulk button`);
  btns.forEach(b => b.disabled = true);

  try {
    const res  = await fetch("/api/scheduled-tasks/set-batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    });
    const data = await res.json();
    (data.results || []).forEach(r => {
      if (!r.ok) return;
      const t = _scheduledTasks.find(x => x.path === r.path);
      if (t) t.active = r.enabled;
      // Update in-place via data-task-path
      const row = document.querySelector(`#tasks-list .tweak-row[data-task-path="${CSS.escape(r.path)}"]`);
      if (row) {
        const cb = row.querySelector("input[type=checkbox]");
        if (cb) cb.checked = !!r.enabled;
        row.classList.add("tweak-ok");
        setTimeout(() => row.classList.remove("tweak-ok"), 600);
      }
    });
    if (data.fail_count > 0) {
      showToast("Bulk tâches partiel", `${data.ok_count} appliqué(s), ${data.fail_count} échec(s)`, "warn");
    }
  } catch (e) {
    showToast("Erreur batch tâches", e.message, "warn");
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

function _scheduledTaskRowHtml(task, isAdmin) {
  const locked = !isAdmin;
  const pathAttr = task.path.replace(/"/g, '&quot;');
  const pathJs   = task.path.replace(/"/g, '&quot;').replace(/'/g, "\\'");
  return `
    <div class="tweak-row${locked ? ' row-locked' : ''}" style="padding:10px 16px" data-task-path="${pathAttr}">
      <div class="tweak-info">
        <div class="tweak-label">${_escapeHtml(task.label)}${locked ? ' <span class="admin-badge">Admin requis</span>' : ''}</div>
        <div class="tweak-desc">${_escapeHtml(task.desc)}</div>
      </div>
      <label class="sw">
        <input type="checkbox" ${task.active ? "checked" : ""} ${locked ? "disabled" : ""} onchange="toggleScheduledTask('${pathJs}', this)">
        <span class="slider"></span>
      </label>
    </div>
  `;
}

async function toggleScheduledTask(path, checkbox) {
  const row = checkbox.closest(".tweak-row");
  const sw = row.querySelector(".sw");
  sw.classList.add("busy");
  const enabled = checkbox.checked;
  try {
    const res = await fetch("/api/scheduled-tasks/set", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, enabled }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "Échec");
    const t = _scheduledTasks.find(x => x.path === path);
    if (t) t.active = enabled;
    row.classList.add("tweak-ok");
    setTimeout(() => row.classList.remove("tweak-ok"), 600);
  } catch (e) {
    checkbox.checked = !checkbox.checked;
    row.classList.add("tweak-error");
    const msg = document.createElement("div");
    msg.className = "tweak-err-msg";
    msg.textContent = "Échec : " + e.message;
    row.appendChild(msg);
    setTimeout(() => {
      row.classList.remove("tweak-error");
      msg.remove();
    }, 5000);
  } finally {
    sw.classList.remove("busy");
  }
}

// ── Outils de réparation système ─────────────────────────────────────────────

let _repairActions = [];

async function loadRepairActions() {
  const btn = document.getElementById("btn-load-repair");
  const el  = document.getElementById("repair-list");
  _btnScan(btn, "Chargement…");
  try {
    const res  = await fetch("/api/repair/list");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erreur serveur");
    _repairActions = data.actions || [];
    _renderRepairActions(data.is_admin);
    _btnReset(btn);
  } catch (e) {
    el.innerHTML = `<div class="tool-error" style="padding:20px">Erreur : ${e.message}</div>`;
    _btnReset(btn);
  }
}

function _renderRepairActions(isAdmin) {
  const el = document.getElementById("repair-list");
  const order = [
    ["network", "Réseau"],
    ["store",   "Microsoft Store"],
    ["update",  "Windows Update"],
    ["shell",   "Explorateur / icônes"],
    ["system",  "Fichiers système (long)"],
  ];
  const html = order.map(([cat, label]) => {
    const items = _repairActions.filter(a => a.category === cat);
    if (!items.length) return "";
    return `
      <div class="tweak-group-title" style="padding:14px 16px 6px">${label}</div>
      ${items.map(a => _repairRowHtml(a, isAdmin)).join("")}
    `;
  }).join("");
  el.innerHTML = html;
}

function _repairRowHtml(action, isAdmin) {
  const locked = action.needs_admin && !isAdmin;
  const reboot = action.reboot_required ? ` <span class="admin-badge" style="background:var(--hover)">reboot requis</span>` : "";
  const streaming = action.streaming;
  const btnLabel = locked ? "Admin requis" : (streaming ? "Lancer (long)" : "Lancer");
  return `
    <div class="tweak-row${locked ? ' row-locked' : ''}" style="padding:10px 16px" data-repair-id="${action.id}">
      <div class="tweak-info">
        <div class="tweak-label">${_escapeHtml(action.label)}${reboot}</div>
        <div class="tweak-desc">${_escapeHtml(action.desc)}</div>
      </div>
      <button class="btn-ghost" ${locked ? "disabled" : ""}
              onclick="runRepairAction('${action.id}', ${streaming ? "true" : "false"})"
              style="flex-shrink:0;font-size:12px">
        ${btnLabel}
      </button>
    </div>
  `;
}

async function runRepairAction(actionId, isStreaming) {
  const row = document.querySelector(`.tweak-row[data-repair-id="${actionId}"]`);
  const btn = row?.querySelector("button");
  const label = row?.querySelector(".tweak-label")?.textContent || actionId;
  const originalText = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "En cours…"; }

  // Push activité avec target = bouton pour que goto/relance fonctionnent
  const activityId = (typeof activityPush === "function")
    ? activityPush(`Réparation — ${label}`, "run", "en cours…", btn)
    : null;

  _logAppend("repair-log", `▶ ${label}`);

  const finish = (status, meta) => {
    if (activityId != null && typeof activityDone === "function") {
      activityDone(activityId, meta, status);
    }
    if (btn) { btn.disabled = false; btn.textContent = originalText; }
  };

  if (isStreaming) {
    // SSE pour SFC / DISM
    try {
      const es = new EventSource(`/api/repair/stream/${actionId}`);
      es.onmessage = (e) => {
        const item = JSON.parse(e.data);
        if (item.type === "log") _logAppend("repair-log", "  " + item.msg);
        else if (item.type === "done") {
          _logAppend("repair-log", "✓ " + item.msg);
          es.close();
          finish("done", "terminé");
        } else if (item.type === "error") {
          _logAppend("repair-log", "✗ " + item.msg);
          es.close();
          finish("fail", item.msg);
        }
      };
      es.onerror = () => {
        es.close();
        _logAppend("repair-log", "✗ Connexion SSE perdue");
        finish("fail", "connexion perdue");
      };
    } catch (e) {
      _logAppend("repair-log", "✗ " + e.message);
      finish("fail", e.message);
    }
  } else {
    // Action simple
    try {
      const res  = await fetch("/api/repair/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: actionId }),
      });
      const data = await res.json();
      if (data.ok) {
        (data.output || "").split("\n").forEach(line => {
          if (line.trim()) _logAppend("repair-log", "  " + line);
        });
        _logAppend("repair-log", "✓ Terminé");
        finish("done", "terminé");
      } else {
        _logAppend("repair-log", "✗ " + (data.error || data.output || "Échec"));
        finish("fail", data.error || "échec");
      }
    } catch (e) {
      _logAppend("repair-log", "✗ " + e.message);
      finish("fail", e.message);
    }
  }
}

// ── Apps UWP pré-installées (debloat) ────────────────────────────────────────

let _uwpApps = [];
let _uwpFilter = "all";

const _UWP_FILTER_LABELS = {
  "all":    "Tout voir",
  "safe":   "Safe — aucun regret",
  "review": "À examiner",
};

async function startUwpScan() {
  const resultEl = document.getElementById("uwp-results");
  const btnEl    = document.getElementById("btn-scan-uwp");
  document.getElementById("uwp-log").innerHTML = "";
  resultEl.innerHTML = "";
  _uwpApps = [];
  _btnScan(btnEl, "Analyse…");

  try {
    const res  = await fetch("/api/uwp-apps");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erreur serveur");
    _btnReset(btnEl);
    renderUwpApps(data);
  } catch (e) {
    _logAppend("uwp-log", "Erreur : " + e.message);
    _btnReset(btnEl);
  }
}

function renderUwpApps(apps) {
  _uwpApps = apps;
  _uwpFilter = "all";
  _renderUwpList();
}

function _renderUwpFilters(installed) {
  const el = document.getElementById("uwp-filters");
  if (!el) return;
  el.style.display = installed.length ? "flex" : "none";
  const counts = {
    all:    installed.length,
    safe:   installed.filter(a => a.risk === "safe").length,
    review: installed.filter(a => a.risk === "review").length,
  };
  const tags = ["all", "safe", "review"];
  el.innerHTML = tags.map(t => {
    const cls = t === _uwpFilter ? "active" : "";
    return `<button class="tweak-filter-btn ${cls}" data-filter="${t}" onclick="setUwpFilter('${t}')">${_UWP_FILTER_LABELS[t]} <span class="c">${counts[t]}</span></button>`;
  }).join("");
}

function setUwpFilter(tag) {
  _uwpFilter = tag;
  _renderUwpList();
}

function _renderUwpList() {
  const installed = _uwpApps.filter(a => a.installed);
  const safe      = installed.filter(a => a.risk === "safe").length;
  const review    = installed.filter(a => a.risk === "review").length;

  _renderUwpFilters(installed);

  const el = document.getElementById("uwp-results");
  if (!installed.length) {
    el.innerHTML = "";
    _logAppend("uwp-log", "Aucune app bloat détectée.");
    return;
  }

  const filtered = _uwpFilter === "all"
    ? installed
    : installed.filter(a => a.risk === _uwpFilter);

  if (!el.querySelector(".sel-header")) {
    _logAppend("uwp-log", `${installed.length} app(s) bloat détectée(s) — ${safe} safe + ${review} à examiner.`);
  }

  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText: `${filtered.length} app(s) affichée(s) — cochées par défaut : niveau « safe »`,
    deleteId:  "btn-delete-uwp",
    deleteFn:  deleteSelectedUwp,
  }));
  _watchSelSize(el, document.getElementById("btn-delete-uwp"));

  // Groupe par risk, seulement les items qui passent le filtre
  const groups = [
    { id: "safe",   label: "Safe — bloat pur, aucun regret",              items: filtered.filter(a => a.risk === "safe") },
    { id: "review", label: "À examiner — peut être utile selon les cas",  items: filtered.filter(a => a.risk === "review") },
  ];

  for (const g of groups) {
    if (!g.items.length) continue;
    const gh = document.createElement("div");
    gh.className = "dupe-group-title";
    gh.textContent = `${g.label} · ${g.items.length}`;
    el.appendChild(gh);
    g.items.forEach((app, i) => {
      const row = document.createElement("div");
      row.className = "dupe-row";
      const cbId = `uwp-${g.id}-${i}`;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = cbId;
      cb.dataset.path = app.package_full_name;
      cb.checked = (g.id === "safe");
      const lbl = document.createElement("label");
      lbl.htmlFor = cbId;
      lbl.className = "dupe-path";
      lbl.style.flexDirection = "column";
      lbl.style.alignItems = "flex-start";
      lbl.style.gap = "2px";
      lbl.innerHTML = `
        <div><strong style="font-size:13px">${_escapeHtml(app.label)}</strong></div>
        <div style="font-size:11px;color:var(--text-dim)">${_escapeHtml(app.desc)}</div>
      `;
      row.append(cb, lbl);
      el.appendChild(row);
    });
  }
}

async function deleteSelectedUwp() {
  const checked = [...document.querySelectorAll("#uwp-results input[type=checkbox]:checked:not(.sel-all)")];
  if (!checked.length) {
    showToast("Aucune sélection", "Cochez au moins une app.", "warn");
    return;
  }
  const packages = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-uwp");
  showConfirm(
    `Désinstaller ${packages.length} app(s) ?`,
    `Les applications cochées seront désinstallées pour votre utilisateur Windows. L'opération est réversible via le Microsoft Store.`,
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Désinstallation…"; }
      _logAppend("uwp-log", `Désinstallation de ${packages.length} app(s)…`);
      try {
        const res  = await fetch("/api/uwp-apps/remove", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ packages }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Erreur serveur");
        _logAppend("uwp-log", `${data.ok_count} désinstallée(s), ${data.fail_count} échec(s).`);
        (data.results || []).forEach(r => {
          if (!r.ok) {
            _logAppend("uwp-log", `  échec ${r.package} : ${r.error}`);
          }
        });
        if (data.ok_count > 0) {
          showToast("Désinstallation terminée", `${data.ok_count} app(s) supprimée(s).`, "success");
        }
        // Re-scan
        setTimeout(() => startUwpScan(), 500);
      } catch (e) {
        _logAppend("uwp-log", "Erreur : " + e.message);
        showToast("Erreur", e.message, "warn");
      } finally {
        if (btn) { btn.disabled = false; }
      }
    },
  );
}

async function startDuplicateFolderScan() {
  const folder = document.getElementById("dupe-folder").value.trim();
  if (!folder) { showToast("Dossier requis", "Entrez un dossier à analyser.", "warn"); return; }

  const resultEl = document.getElementById("dupe-results");
  const btnEl    = document.getElementById("btn-scan-dupes");

  document.getElementById("dupe-log").innerHTML = "";
  resultEl.innerHTML = "";
  duplicateFolderGroups = [];
  _btnScan(btnEl, "Analyse…");
  _logAppend("dupe-log", "Scan en cours (peut prendre une minute sur un gros dossier)…");

  try {
    const res  = await fetch("/api/duplicate-folders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    const data = await res.json();
    if (!res.ok) { _logAppend("dupe-log", "Erreur : " + (data.error || res.status)); _btnReset(btnEl); return; }
    _btnReset(btnEl);
    renderDuplicateFolders(data.groups || [], data.total_fmt || "0 o");
  } catch (e) {
    _logAppend("dupe-log", "Erreur : " + e.message);
    _btnReset(btnEl);
  }
}

function renderDuplicateFolders(groups, totalFmt) {
  duplicateFolderGroups = groups;
  const el = document.getElementById("dupe-results");
  if (!groups.length) {
    el.innerHTML = "";
    _logAppend("dupe-log", "Aucun résultat.");
    return;
  }
  _logAppend("dupe-log", `${groups.length} groupe(s) trouvé(s) — ${totalFmt} récupérables.`);

  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText: `${groups.length} groupe(s) — ${totalFmt} récupérables`,
    deleteId:  "btn-delete-dupdir",
    deleteFn:  deleteSelectedDupeFolders,
  }));

  _watchSelSize(el, document.getElementById("btn-delete-dupdir"));
  _renderBatched(groups, (g, gi) => {
    const group = document.createElement("div");
    group.className = "dupe-group";
    group.innerHTML = `<div class="dupe-group-title">${g.folders.length} dossiers identiques — ${g.file_count} fichier(s) — ${g.size_fmt}</div>`;
    let keptIdx = 0;
    const renderRows = () => {
      [...group.querySelectorAll(".dupe-row")].forEach(r => r.remove());
      g.folders.forEach((f, fi) => {
        const row = document.createElement("div"); row.className = "dupe-row";
        const cbId = `dupdir-${gi}-${fi}`; const isKept = fi === keptIdx;
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path; cb.dataset.size = f.size;
        cb.checked = !isKept; cb.disabled = isKept;
        cb.title = isKept ? "Ce dossier sera conservé" : "";
        const lbl = document.createElement("label"); lbl.htmlFor = cbId; lbl.className = "dupe-path"; lbl.style.opacity = isKept ? "0.55" : "";
        const sizeSpan = document.createElement("span"); sizeSpan.className = "dupe-size"; sizeSpan.textContent = f.size_fmt;
        lbl.appendChild(sizeSpan); lbl.appendChild(document.createTextNode(" " + f.path));
        if (isKept) {
          const badge = document.createElement("span"); badge.className = "source-badge";
          badge.style.cssText = "margin-left:6px;color:var(--green);border-color:var(--green)"; badge.textContent = "↩ conservé";
          lbl.appendChild(badge); row.append(cb, lbl);
        } else {
          const keepBtn = document.createElement("button"); keepBtn.className = "btn-ghost"; keepBtn.textContent = "Conserver celui-ci";
          keepBtn.style.cssText = "font-size:11px;padding:2px 8px;margin-left:8px;flex-shrink:0";
          keepBtn.addEventListener("click", (e) => { e.preventDefault(); keptIdx = fi; renderRows(); });
          row.append(cb, lbl, keepBtn);
        }
        _applyAdminLock(row, cb, f.needs_admin);
        group.appendChild(row);
      });
    };
    renderRows();
    return group;
  }, el);
}

function deleteSelectedDupeFolders() {
  _deleteSelected({
    resultsId: "dupe-results",
    btnId:     "btn-delete-dupdir",
    endpoint:  "/api/duplicate-folders/delete",
    confirmBody: (n, size) =>
      `${n} dossier(s) seront définitivement supprimés avec tout leur contenu. Espace récupéré estimé : ${fmtBytesTools(size)}.`,
    preCheck: () => {
      for (const group of document.querySelectorAll("#dupe-results .dupe-group")) {
        const all = group.querySelectorAll("input[type=checkbox]");
        const checkedIn = group.querySelectorAll("input[type=checkbox]:checked:not(.sel-all)");
        if (all.length > 0 && checkedIn.length >= all.length) {
          showToast("Action impossible", "Vous ne pouvez pas supprimer tous les dossiers d'un groupe.", "warn");
          return false;
        }
      }
      return true;
    },
  });
}

function deleteSelectedDupes() {
  _deleteSelected({
    resultsId: "dupe-results",
    btnId:     "btn-delete-dupes",
    endpoint:  "/api/duplicates/delete",
    confirmBody: (n, size) =>
      `Les fichiers cochés seront définitivement supprimés du disque. Espace récupéré estimé : ${fmtBytesTools(size)}.`,
    preCheck: () => {
      for (const group of document.querySelectorAll(".dupe-group")) {
        const all = group.querySelectorAll("input[type=checkbox]");
        const checkedIn = group.querySelectorAll("input[type=checkbox]:checked:not(.sel-all)");
        if (all.length > 0 && checkedIn.length >= all.length) {
          showToast("Action impossible", "Vous ne pouvez pas supprimer toutes les copies d'un groupe.", "warn");
          return false;
        }
      }
      return true;
    },
  });
}

function fmtBytesTools(b) {
  if (b === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(1) + " " + units[i];
}

function _renderBatched(items, rowFn, container, batchSize, onDone) {
  batchSize = batchSize || 50;
  // Annule tout rendu en cours sur ce container
  const token = Symbol();
  container._renderToken = token;
  let i = 0;
  function next() {
    if (container._renderToken !== token) return;
    const frag = document.createDocumentFragment();
    const end = Math.min(i + batchSize, items.length);
    for (; i < end; i++) frag.appendChild(rowFn(items[i], i));
    container.appendChild(frag);
    if (i < items.length) requestAnimationFrame(next);
    else if (onDone) onDone();
  }
  requestAnimationFrame(next);
}

function _watchSelSize(el, btnEl) {
  if (!btnEl) return;
  const update = () => {
    const checked = [...el.querySelectorAll("input[type=checkbox]:checked:not(.sel-all)")];
    const total = checked.reduce((s, c) => s + (parseInt(c.dataset.size) || 0), 0);
    btnEl.textContent = total > 0
      ? `Supprimer la sélection (${fmtBytesTools(total)})`
      : "Supprimer la sélection";
  };
  if (el._selSizeHandler) el.removeEventListener("change", el._selSizeHandler);
  el._selSizeHandler = update;
  el.addEventListener("change", update);
  update();
}

function _makeFileRow(item, i, idPrefix, opts) {
  // opts: { showSize: bool, showPath: bool (default true), extraRight: (item)=>Element|null }
  opts = opts || {};
  const showPath = opts.showPath !== false;
  const row = document.createElement("div"); row.className = "dupe-row";
  const cbId = `${idPrefix}-${i}`;
  const cb = document.createElement("input");
  cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = item.path; cb.checked = true;
  if (item.size != null) cb.dataset.size = item.size;
  const lbl = document.createElement("label"); lbl.htmlFor = cbId; lbl.className = "sel-label";
  if (opts.showSize && item.size_fmt) {
    const sizeSpan = document.createElement("span");
    sizeSpan.className = "dupe-size"; sizeSpan.textContent = item.size_fmt;
    lbl.append(sizeSpan, " ");
  }
  const nameSpan = document.createElement("span"); nameSpan.className = "sel-name";
  nameSpan.textContent = item.name;
  lbl.appendChild(nameSpan);
  if (opts.extraRight) {
    const extra = opts.extraRight(item);
    if (extra) lbl.append(" — ", extra);
  }
  if (showPath) {
    const pathSpan = document.createElement("span"); pathSpan.className = "sel-dim";
    pathSpan.textContent = item.path;
    lbl.append(document.createElement("br"), pathSpan);
  }
  row.append(cb, lbl);
  _applyAdminLock(row, cb, item.needs_admin);
  return row;
}

async function _deleteSelected(opts) {
  // opts: { resultsId, btnId, endpoint, confirmBody, rowSelector, afterDelete, preCheck }
  const checked = [...document.querySelectorAll(
    `#${opts.resultsId} input[type=checkbox]:checked:not(.sel-all)`
  )].filter(c => !c.disabled);
  if (!checked.length) {
    showToast("Aucune sélection", "Cochez au moins un élément.", "warn");
    return;
  }
  if (opts.preCheck && opts.preCheck() === false) return;
  const paths = checked.map(c => c.dataset.path);
  const totalSize = checked.reduce((s, c) => s + (parseInt(c.dataset.size) || 0), 0);
  const btn = document.getElementById(opts.btnId);
  const confirmBody = typeof opts.confirmBody === "function"
    ? opts.confirmBody(paths.length, totalSize)
    : opts.confirmBody;
  showConfirm(
    `Supprimer ${paths.length} élément(s) ?`,
    confirmBody,
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch(opts.endpoint, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Erreur serveur");
        const errCount = (data.errors || []).length;
        const deleted = data.deleted != null ? data.deleted : (paths.length - errCount);
        const freedFmt = data.freed_fmt;
        if (errCount > 0) {
          const sub = freedFmt
            ? `${deleted} supprimé(s) — ${freedFmt} libérés — ${errCount} échec(s).`
            : `${deleted} supprimé(s) — ${errCount} échec(s).`;
          showToast("Suppression partielle", sub, "warn");
        } else {
          const sub = freedFmt
            ? `${deleted} supprimé(s) — ${freedFmt} libérés.`
            : `${deleted} supprimé(s).`;
          showToast("Suppression terminée", sub, "success");
        }
        checked.forEach(c => {
          const row = c.closest(opts.rowSelector || ".dupe-row");
          if (row) row.remove();
        });
        if (opts.afterDelete) opts.afterDelete(data);
      } catch (e) {
        showToast("Erreur", e.message, "warn");
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

// ── Registre ──────────────────────────────────────────────────────────────────

let registryIssues = [];

function startRegistryScan() {
  requireAdmin(_startRegistryScan);
}

async function _startRegistryScan() {
  const resultEl = document.getElementById("reg-results");
  const btnEl    = document.getElementById("btn-scan-reg");

  document.getElementById("reg-log").innerHTML = "";
  resultEl.innerHTML = "";
  registryIssues = [];
  _btnScan(btnEl, "Analyse…");

  const regLog = (msg) => _logAppend("reg-log", msg);

  try {
    const res = await fetch("/api/registry/scan", { method: "POST" });
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    _activeStreams["reg"] = es;
    _showCancelBtn(btnEl, "reg", () => { _btnReset(btnEl); });
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    regLog(item.msg);
      if (item.type === "result") renderRegistryIssues(item.issues);
      if (item.type === "done") {
        regLog(item.msg);
        es.close(); _removeCancelBtn("reg");
        _btnReset(btnEl);
      }
    };
    es.onerror = () => { es.close(); _removeCancelBtn("reg"); _btnReset(btnEl); };
  } catch (err) {
    regLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderRegistryIssues(issues) {
  registryIssues = issues;
  const el = document.getElementById("reg-results");

  if (!issues.length) {
    el.innerHTML = "";
    _logAppend("reg-log", "Aucun résultat.");
    return;
  }

  const categories = {};
  issues.forEach(iss => {
    if (!categories[iss.category]) categories[iss.category] = [];
    categories[iss.category].push(iss);
  });

  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText:   `${issues.length} problème(s) détecté(s)`,
    deleteId:    "btn-fix-reg",
    deleteLabel: "Supprimer la sélection",
    deleteFn:    fixSelectedRegistry,
  }));

  Object.entries(categories).forEach(([cat, catIssues]) => {
    const section = document.createElement("div");
    section.className = "reg-category";
    section.innerHTML = `<div class="reg-cat-title">${cat} <span class="reg-cat-count">${catIssues.length}</span></div>`;

    catIssues.forEach((iss, i) => {
      const row   = document.createElement("div");
      row.className = "dupe-row";
      const cbId  = `reg-${cat.replace(/\s/g,'')}-${i}`;

      const cb    = document.createElement("input");
      cb.type = "checkbox"; cb.id = cbId; cb.dataset.idx = registryIssues.indexOf(iss); cb.checked = true;

      const lbl   = document.createElement("label");
      lbl.htmlFor = cbId;
      lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer;word-break:break-all";

      const descSpan = document.createElement("span");
      descSpan.style.cssText = "font-weight:600;color:var(--text)";
      descSpan.textContent   = iss.description;

      const keySpan  = document.createElement("span");
      keySpan.style.color = "var(--text-dim)";
      keySpan.textContent = iss.key + (iss.value_name && iss.value_name !== "__DELETE_KEY__" ? " → " + iss.value_name : "");

      lbl.append(descSpan, document.createElement("br"), keySpan);
      row.append(cb, lbl);
      section.appendChild(row);
    });
    el.appendChild(section);
  });
}

async function fixSelectedRegistry() {
  const checked = [...document.querySelectorAll("#reg-results input[type=checkbox]:checked:not(.sel-all)")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un élément.", "warn"); return; }

  const selected = checked.map(c => registryIssues[parseInt(c.dataset.idx)]).filter(Boolean);
  showConfirm(
    `Supprimer ${selected.length} élément(s) ?`,
    "Les références sélectionnées seront supprimées du registre Windows. Cette action est sans risque pour votre système.",
    () => _doFixRegistry(selected, checked)
  );
}

async function _doFixRegistry(selected, checked) {
  const btnEl  = document.getElementById("btn-fix-reg");
  _btnScan(btnEl, "Correction…");

  const regLog = (msg) => _logAppend("reg-log", msg);

  try {
    const res = await fetch("/api/registry/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ issues: selected }),
    });
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")  regLog(item.msg);
      if (item.type === "done") {
        regLog(item.msg);
        es.close();
        checked.forEach(c => c.closest(".dupe-row").remove());
        _btnDone(btnEl, "Terminé");
        showToast("Suppression terminée", item.msg, "success");
      }
    };
    es.onerror = () => { es.close(); _btnReset(btnEl); };
  } catch (err) {
    regLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

// ── Extensions navigateurs ────────────────────────────────────────────────────

async function loadExtensions() {
  const el = document.getElementById("ext-container");
  el.innerHTML = _skeleton(3, true);
  try {
    const res  = await fetch("/api/extensions");
    const data = await res.json();
    renderExtensions(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderExtensions(data) {
  const el = document.getElementById("ext-container");
  el.innerHTML = "";

  const browsers = Object.entries(data).filter(([, exts]) => exts.length > 0);
  if (!browsers.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }

  // Logos navigateurs — SVG monochromes (Lucide-style)
  const BROWSER_META = {
    "Chrome":        { label: "Chrome",  svg: '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><line x1="21.17" y1="8" x2="12" y2="8"/><line x1="3.95" y1="6.06" x2="8.54" y2="14"/><line x1="10.88" y1="21.94" x2="15.46" y2="14"/></svg>' },
    "Edge":          { label: "Edge",    svg: '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><circle cx="12" cy="12" r="10"/><path d="M6 14c2-3 4-3 6 0s4 3 6 0"/></svg>' },
    "Brave-Browser": { label: "Brave",   svg: '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>' },
    "Firefox":       { label: "Firefox", svg: '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>' },
  };

  browsers.forEach(([browser, exts]) => {
    const section = document.createElement("div");
    section.className = "ext-browser-section";

    const meta  = BROWSER_META[browser] || { label: browser, svg: '' };
    const title = document.createElement("div"); title.className = "ext-browser-title";
    title.innerHTML = `${meta.svg}<span style="margin-left:6px">${meta.label}</span>`;

    const badge = document.createElement("span"); badge.className = "reg-cat-count"; badge.textContent = exts.length;
    badge.style.marginLeft = "auto";
    title.appendChild(badge);
    section.appendChild(title);

    exts.forEach(ext => {
      const row = document.createElement("div");
      row.className = "tool-row ext-row";

      const info   = document.createElement("div"); info.className = "tool-info";
      const nameD  = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = ext.name;
      const subD   = document.createElement("div"); subD.className  = "tool-sub";
      subD.textContent = (ext.description || ext.id) + " — v" + (ext.version || "?");
      info.append(nameD, subD);

      const profD  = document.createElement("div"); profD.className = "tool-meta dim";
      profD.style.fontSize = "11px"; profD.textContent = ext.profile || "";

      const btn    = document.createElement("button"); btn.className = "btn-uninstall"; btn.textContent = "Supprimer";
      btn.addEventListener("click", () => removeExtension(ext.path, ext.name, btn));

      row.append(info, profD, btn);
      section.appendChild(row);
    });
    el.appendChild(section);
  });
}

async function removeExtension(path, name, btn) {
  showConfirm(
    `Supprimer l'extension "${name}" ?`,
    "Le dossier de l'extension sera définitivement supprimé du disque. Le navigateur devra être redémarré.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "En cours…"; }
      try {
        const res  = await fetch("/api/extensions/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path }),
        });
        const data = await res.json();
        if (data.ok) {
          showToast("Extension supprimée", `« ${name} » a été retirée du disque.`, "success");
          loadExtensions();
        } else {
          showToast("Erreur", data.error || "Suppression impossible.", "warn");
          if (btn) { btn.disabled = false; btn.textContent = "Supprimer"; }
        }
      } catch (e) {
        showToast("Erreur de connexion", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer"; }
      }
    }
  );
}

// ── Mises à jour logicielles ──────────────────────────────────────────────────

async function openRecycleBin() {
  try {
    const res = await fetch("/api/undo/open-recycle-bin", { method: "POST" });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Erreur");
  } catch (e) {
    showToast("Corbeille", e.message, "warn");
  }
}

async function loadInstallerCache() {
  const btn = document.getElementById("btn-scan-wic");
  const container = document.getElementById("wic-container");
  _btnScan(btn, "Scan…");
  container.innerHTML = `<div class="tool-loading">Analyse de C:\\Windows\\Installer…</div>`;
  try {
    const res = await fetch("/api/windows-installer-cache");
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    document.getElementById("btn-cleanmgr").style.display = "";

    let html = `
      <div style="padding:14px 16px;border-bottom:1px solid var(--border)">
        <div style="font-size:18px;font-weight:700;color:${data.total > 5*1024*1024*1024 ? 'var(--amber)' : 'var(--text)'};font-variant-numeric:tabular-nums">
          ${data.total_fmt}
        </div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:2px">${data.count} fichiers MSI/MSP</div>
      </div>
      <div class="callout callout-yellow" style="margin:12px 16px">
        <div class="callout-icon">
          <svg class="icon icon-lg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <div class="callout-content">
          <strong>Pourquoi pas de bouton supprimer ?</strong> Ces fichiers sont utilisés par Windows pour réparer Office, Adobe, et autres apps lors de mises à jour. Les supprimer manuellement peut casser ces réparations. <strong>Utilisez « Nettoyage de disque »</strong> (cleanmgr) — l'outil Microsoft qui sait identifier et supprimer uniquement les packages vraiment obsolètes.
        </div>
      </div>`;

    if (data.items && data.items.length) {
      html += `<div style="padding:0 16px 14px"><div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Top ${data.items.length} fichiers</div>`;
      data.items.forEach(it => {
        html += `
          <div style="display:flex;align-items:center;gap:10px;padding:5px 0;font-size:12px;border-bottom:1px solid var(--border)">
            <div style="flex:1;font-family:'IBM Plex Mono',monospace;color:var(--text-dim)">${it.name}</div>
            <div style="font-variant-numeric:tabular-nums">${it.size_fmt}</div>
            <span style="font-size:9px;background:var(--bg3);color:var(--text-dim);padding:1px 5px;border-radius:3px">${it.type}</span>
          </div>`;
      });
      html += `</div>`;
    }
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="tool-error">Erreur : ${e.message}</div>`;
  } finally {
    _btnReset(btn);
  }
}

async function openDiskCleanup() {
  try {
    const res = await fetch("/api/disk-cleanup", { method: "POST" });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Erreur");
    showToast("Nettoyage de disque", "L'outil Microsoft a été lancé. Cochez « Fichiers Windows Installer » pour nettoyer ce cache.", "success");
  } catch (e) {
    showToast("Erreur", e.message, "warn");
  }
}

async function downloadGlobalReport() {
  const actId = activityPush("Rapport global", "run", "Génération…");
  try {
    const res = await fetch("/api/report");
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || ("HTTP " + res.status));
    }
    const disp = res.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename="([^"]+)"/);
    const filename = m ? m[1] : "opencleaner-report.html";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    activityDone(actId, `${filename} téléchargé`);
  } catch (e) {
    activityDone(actId, "Échec", "fail");
    showToast("Rapport", e.message, "warn");
  }
}

let _bdData = [];

async function loadBrowserData() {
  const btn = document.getElementById("btn-bd-scan");
  const container = document.getElementById("bd-container");
  _btnScan(btn, "Scan…");
  container.innerHTML = `<div class="tool-loading">Analyse des profils…</div>`;
  try {
    const res = await fetch("/api/browser-data");
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _bdData = data;
    _renderBrowserData();
  } catch (e) {
    container.innerHTML = `<div class="tool-error">Erreur : ${e.message}</div>`;
  } finally {
    _btnReset(btn);
  }
}

function _renderBrowserData() {
  const container = document.getElementById("bd-container");
  const cleanBtn  = document.getElementById("btn-bd-clean");
  if (!_bdData.length) {
    container.innerHTML = `<div class="tool-empty">Aucun profil navigateur détecté.</div>`;
    cleanBtn.style.display = "none";
    return;
  }
  container.innerHTML = "";
  _bdData.forEach((prof, pi) => {
    const block = document.createElement("div");
    block.className = "bd-profile";
    block.style.cssText = "padding:12px 16px;border-bottom:1px solid var(--border)";

    const head = document.createElement("div");
    head.style.cssText = "display:flex;align-items:center;gap:10px;margin-bottom:8px";
    const title = document.createElement("div");
    title.style.cssText = "flex:1;font-size:13px;font-weight:600";
    const totalSize = prof.items.reduce((s, i) => s + i.size, 0);
    title.textContent = `${prof.browser} — ${prof.profile}`;
    const sizeEl = document.createElement("div");
    sizeEl.style.cssText = "font-size:11px;color:var(--text-dim);font-variant-numeric:tabular-nums";
    sizeEl.textContent = fmtBytesTools(totalSize);
    head.append(title, sizeEl);
    block.appendChild(head);

    prof.items.forEach((it, ii) => {
      if (it.size === 0) return;
      const row = document.createElement("label");
      row.style.cssText = "display:flex;align-items:center;gap:10px;padding:5px 0;font-size:12px;cursor:pointer";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.dataset.pi = pi;
      cb.dataset.key = it.key;
      cb.onchange = _updateBdCleanBtn;
      if (!it.sensitive) cb.checked = it.key === "cache";

      const label = document.createElement("div");
      label.style.flex = "1";
      label.innerHTML = `<strong>${it.label}</strong> <span style="color:var(--text-dim);font-size:11px">— ${it.desc}</span>`;
      if (it.sensitive) {
        label.innerHTML += ` <span style="color:var(--red);font-size:10px;font-weight:600">SENSIBLE</span>`;
      }
      const s = document.createElement("div");
      s.style.cssText = "font-size:11px;color:var(--text-dim);font-variant-numeric:tabular-nums;min-width:60px;text-align:right";
      s.textContent = it.size_fmt;
      row.append(cb, label, s);
      block.appendChild(row);
    });
    container.appendChild(block);
  });
  cleanBtn.style.display = "";
  _updateBdCleanBtn();
}

function _updateBdCleanBtn() {
  const btn = document.getElementById("btn-bd-clean");
  const checked = document.querySelectorAll("#bd-container input[type=checkbox]:checked");
  btn.textContent = checked.length ? `Nettoyer ${checked.length} élément(s)` : "Nettoyer la sélection";
  btn.disabled = checked.length === 0;
}

async function cleanBrowserData() {
  const checked = Array.from(document.querySelectorAll("#bd-container input[type=checkbox]:checked"));
  if (!checked.length) return;
  const byProfile = new Map();
  checked.forEach(cb => {
    const pi = +cb.dataset.pi;
    const prof = _bdData[pi];
    if (!prof) return;
    if (!byProfile.has(pi)) byProfile.set(pi, { path: prof.path, keys: [] });
    byProfile.get(pi).keys.push(cb.dataset.key);
  });
  const selections = Array.from(byProfile.values());
  const hasSensitive = checked.some(cb => ["passwords", "autofill"].includes(cb.dataset.key));
  const msg = hasSensitive
    ? "Vous allez supprimer des données SENSIBLES (mots de passe / auto-remplissage). Cette action est irréversible. Continuer ?"
    : "Supprimer les données sélectionnées ?";
  if (!confirm(msg)) return;

  const actId = activityPush("Nettoyage navigateurs", "run", "Suppression…", { tab: "outils" });
  try {
    const res = await fetch("/api/browser-data/clean", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selections }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    activityDone(actId, `${data.deleted_fmt} libérés`);
    loadBrowserData();
  } catch (e) {
    activityDone(actId, "Échec", "fail");
    showToast("Nettoyage navigateurs", e.message, "warn");
  }
}

async function loadUpdateCenter() {
  const btn = document.getElementById("btn-uc-scan");
  const container = document.getElementById("uc-container");
  const summary = document.getElementById("uc-summary");
  _btnScan(btn, "Scan…");
  container.innerHTML = `<div class="tool-loading">Analyse Windows + pilotes + logiciels… (peut prendre 1–3 min)</div>`;
  const actId = activityPush("Centre de mises à jour", "run", "Recherche en cours…", { tab: "outils" });
  try {
    const res = await fetch("/api/update-center");
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _renderUpdateCenter(data);
    summary.style.display = "flex";
    activityDone(actId, `${data.total} mise(s) à jour trouvée(s)`);
  } catch (e) {
    container.innerHTML = `<div class="tool-error">Erreur : ${e.message}</div>`;
    activityDone(actId, "Échec", "fail");
  } finally {
    _btnReset(btn);
  }
}

function _renderUpdateCenter(data) {
  const container = document.getElementById("uc-container");
  const summary = document.getElementById("uc-summary");

  const setTile = (kind, block) => {
    const tile = summary.querySelector(`.uc-tile[data-kind="${kind}"]`);
    if (!tile) return;
    const countEl = tile.querySelector(".uc-count");
    tile.classList.remove("uc-has-updates", "uc-error");
    if (block && block.error) {
      tile.classList.add("uc-error");
      countEl.textContent = "Erreur";
      tile.title = block.error;
    } else {
      const n = (block && block.updates) ? block.updates.length : 0;
      countEl.textContent = n;
      if (n > 0) tile.classList.add("uc-has-updates");
      tile.title = `${n} mise(s) à jour`;
    }
  };
  setTile("windows",  data.windows);
  setTile("drivers",  data.drivers);
  setTile("software", data.software);

  container.innerHTML = "";
  const renderGroup = (title, block, fmt) => {
    if (!block) return;
    const group = document.createElement("div");
    group.className = "uc-group";
    const titleEl = document.createElement("div");
    titleEl.className = "uc-group-title";
    titleEl.textContent = title;
    group.appendChild(titleEl);

    if (block.error) {
      const err = document.createElement("div");
      err.className = "tool-error";
      err.textContent = block.error;
      group.appendChild(err);
    } else if (!block.updates || !block.updates.length) {
      const ok = document.createElement("div");
      ok.className = "tool-empty";
      ok.textContent = "Aucune mise à jour disponible.";
      group.appendChild(ok);
    } else {
      block.updates.slice(0, 50).forEach(u => {
        const row = document.createElement("div");
        row.className = "uc-item";
        const t = document.createElement("div");
        t.className = "uc-item-title";
        t.textContent = fmt.title(u);
        const s = document.createElement("div");
        s.className = "uc-item-sub";
        s.textContent = fmt.sub(u);
        row.append(t, s);
        group.appendChild(row);
      });
      if (block.updates.length > 50) {
        const more = document.createElement("div");
        more.className = "uc-item-sub";
        more.textContent = `+ ${block.updates.length - 50} autres…`;
        group.appendChild(more);
      }
    }
    container.appendChild(group);
  };

  renderGroup("Windows", data.windows, {
    title: u => u.title || "Mise à jour",
    sub:   u => {
      const size = u.sizeBytes ? fmtBytesTools(u.sizeBytes) : "";
      const sev  = u.severity || "";
      return [sev, size].filter(Boolean).join(" — ");
    },
  });
  renderGroup("Pilotes", data.drivers, {
    title: u => u.title || u.driverModel || "Pilote",
    sub:   u => {
      const size = u.sizeBytes ? fmtBytesTools(u.sizeBytes) : "";
      return [u.driverClass, u.driverDate, size].filter(Boolean).join(" — ");
    },
  });
  renderGroup("Logiciels", data.software, {
    title: u => u.name || u.id || "Logiciel",
    sub:   u => {
      const from = u.version || "";
      const to   = u.available || "";
      return from && to ? `${from} → ${to}` : (from || to);
    },
  });
}

// ── Raccourcis cassés ─────────────────────────────────────────────────────────

async function loadShortcuts() {
  const el    = document.getElementById("shortcuts-results");
  const btnEl = document.getElementById("btn-scan-shortcuts");
  el.innerHTML = _skeleton(3);
  _btnScan(btnEl, "Analyse…");

  try {
    const res  = await fetch("/api/shortcuts");
    const data = await res.json();
    _btnReset(btnEl);
    renderShortcuts(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
    _btnReset(btnEl);
  }
}

function renderShortcuts(shortcuts) {
  const el = document.getElementById("shortcuts-results");
  if (!shortcuts.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }
  el.innerHTML = "";

  el.appendChild(_makeSelHeader(el, {
    countText:   `${shortcuts.length} raccourci(s) cassé(s)`,
    deleteId:    "btn-delete-shortcuts",
    deleteFn:    deleteSelectedShortcuts,
  }));

  shortcuts.forEach((sc, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `sc-${i}`;
    const cb   = document.createElement("input");
    cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = sc.path; cb.checked = true;
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId; lbl.className = "sel-label";
    const nameSpan = document.createElement("span"); nameSpan.className = "sel-name"; nameSpan.textContent = sc.name;
    const locSpan  = document.createElement("span"); locSpan.className = "source-badge"; locSpan.style.marginLeft = "6px"; locSpan.textContent = sc.location;
    const tgtSpan  = document.createElement("span"); tgtSpan.className = "sel-dim"; tgtSpan.textContent = sc.target;
    lbl.append(nameSpan, " ", locSpan, document.createElement("br"), tgtSpan);
    row.append(cb, lbl);
    _applyAdminLock(row, cb, sc.needs_admin);
    el.appendChild(row);
  });
}

function deleteSelectedShortcuts() {
  _deleteSelected({
    resultsId: "shortcuts-results",
    btnId:     "btn-delete-shortcuts",
    endpoint:  "/api/shortcuts/delete",
    confirmBody: "Les fichiers .lnk sélectionnés seront définitivement supprimés. Cela n'affecte pas les applications elles-mêmes.",
  });
}

// ── Grands fichiers ───────────────────────────────────────────────────────────

let _lfFiles = [], _lfTotalFmt = "", _lfSortKey = "size", _lfSortDir = -1;

async function startLargeFileScan() {
  const folder  = document.getElementById("lf-folder").value.trim();
  const minGb   = parseFloat(document.getElementById("lf-minsize").value) || 0.5;
  if (!folder) { showToast("Dossier requis", "Entrez un dossier à analyser.", "warn"); return; }

  const resultEl = document.getElementById("lf-results");
  const btnEl    = document.getElementById("btn-scan-lf");

  document.getElementById("lf-log").innerHTML = "";
  resultEl.innerHTML = "";
  _btnScan(btnEl, "Analyse…");

  const lfLog = (msg) => _logAppend("lf-log", msg);

  try {
    const res = await fetch("/api/largefiles", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, min_size_gb: minGb }),
    });
    if (!res.ok) { const e = await res.json(); showToast("Erreur", e.error, "warn"); btnEl.disabled = false; btnEl.textContent = "Analyser"; return; }
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    _activeStreams["lf"] = es;
    _showCancelBtn(btnEl, "lf", () => { _btnReset(btnEl); document.getElementById("lf-log").innerHTML = ""; });
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    lfLog(item.msg);
      if (item.type === "result") renderLargeFiles(item.files, item.total_fmt);
      if (item.type === "done") {
        es.close(); _removeCancelBtn("lf");
        _btnReset(btnEl);
      }
    };
    es.onerror = () => { es.close(); _removeCancelBtn("lf"); _btnReset(btnEl); };
  } catch (err) {
    lfLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderLargeFiles(files, totalFmt) {
  if (!files.length) {
    document.getElementById("lf-results").innerHTML = "";
    _logAppend("lf-log", "Aucun résultat.");
    return;
  }
  _lfFiles = files; _lfTotalFmt = totalFmt;
  _lfSortKey = "size"; _lfSortDir = -1;
  _renderLargeFiles();
}

function _renderLargeFiles() {
  const el = document.getElementById("lf-results");
  const files = [..._lfFiles].sort((a, b) =>
    _lfSortKey === "size" ? _lfSortDir * (b.size - a.size) : _lfSortDir * a.name.localeCompare(b.name)
  );
  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText:   `${_lfFiles.length} fichier(s) — ${_lfTotalFmt} au total`,
    deleteId:    "btn-delete-lf",
    deleteFn:    deleteSelectedLargeFiles,
    sortKeys:    [["size","Taille"],["name","Nom"]],
    sortKey:     _lfSortKey, sortDir: _lfSortDir,
    onSort: (key) => { _lfSortKey === key ? _lfSortDir *= -1 : (_lfSortKey = key, _lfSortDir = -1); _renderLargeFiles(); },
  }));

  _watchSelSize(el, document.getElementById("btn-delete-lf"));
  _renderBatched(files, (f, i) => _makeFileRow(f, i, "lf", { showSize: true }), el);
}

function deleteSelectedLargeFiles() {
  _deleteSelected({
    resultsId: "lf-results",
    btnId:     "btn-delete-lf",
    endpoint:  "/api/recycle-bin/send",
    confirmBody: (n, size) =>
      `Ces fichiers seront envoyés à la corbeille. Espace récupéré estimé : ${fmtBytesTools(size)}.`,
  });
}

// ── Analyse de l'espace disque ────────────────────────────────────────────────

let _daHistory   = [];   // pile de navigation : [{folder, items, total}]
let _daItems     = [];   // résultats courants
let _daTotal     = 0;
let _daEsActive  = null;
let _daSortKey   = "size";   // "size" | "name"
let _daSortDir   = -1;       // -1 desc, 1 asc
let _daView      = "list";   // "list" | "treemap"

function setDiskView(view) {
  _daView = view;
  document.querySelectorAll(".da-view-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  const folder = document.getElementById("da-folder")?.value || "";
  if (_daItems.length) _renderDiskItems(_daItems, _daTotal, folder);
}

function startDiskAnalysis(folder) {
  const inputEl = document.getElementById("da-folder");
  folder = folder || inputEl.value.trim() || "C:\\";
  inputEl.value = folder;
  _runDiskAnalysis(folder, true);
}

function _runDiskAnalysis(folder, resetHistory) {
  if (_daEsActive) { _daEsActive.close(); _daEsActive = null; }
  if (resetHistory) _daHistory = [];

  const resultEl = document.getElementById("da-results");
  const btnEl    = document.getElementById("btn-scan-da");
  resultEl.innerHTML = _daSkeleton();
  _btnScan(btnEl, "Analyse…");
  _daItems = [];
  _daTotal = 0;

  _updateBreadcrumb(folder);

  fetch("/api/disk-analysis", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder }),
  })
  .then(r => r.json())
  .then(({ job_id }) => {
    const es = new EventSource(`/api/stream/${job_id}`);
    _daEsActive = es;
    _activeStreams["da"] = es;
    _showCancelBtn(btnEl, "da", () => {
      _btnReset(btnEl);
      document.getElementById("da-results").innerHTML = "";
    });

    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);

      if (msg.type === "item") {
        _daItems.push(msg.item);
        _daTotal += msg.item.size;
        _renderDiskItems(_daItems, _daTotal, folder);
      }
      if (msg.type === "result") {
        _daItems  = msg.items;
        _daTotal  = msg.total;
        _renderDiskItems(_daItems, _daTotal, folder);
        _btnReset(btnEl);
        es.close(); _daEsActive = null; _removeCancelBtn("da");
      }
      if (msg.type === "done" && !msg.items) {
        _btnReset(btnEl);
        es.close(); _daEsActive = null; _removeCancelBtn("da");
      }
    };
    es.onerror = () => {
      es.close(); _daEsActive = null; _btnReset(btnEl);
    };
  })
  .catch(err => {
    resultEl.innerHTML = `<div class="tool-error">Erreur : ${err.message}</div>`;
    _btnReset(btnEl);
  });
}

function _daSkeleton() {
  return Array.from({length: 6}, (_, i) => `
    <div class="da-loading">
      <div class="da-icon"><svg class="icon" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M20 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></div>
      <div class="da-name" style="flex:1"><div class="skeleton-box" style="width:${35+i*8}%;height:12px"></div></div>
      <div class="da-bar-wrap"><div class="da-bar" style="width:100%"></div></div>
      <div class="da-size"><div class="skeleton-box" style="width:45px;height:11px"></div></div>
    </div>`).join("");
}

function _daSortItems(items) {
  return [...items].sort((a, b) => {
    if (_daSortKey === "name") return _daSortDir * a.name.localeCompare(b.name);
    // _daSortDir -1 = taille décroissante (défaut), 1 = croissante
    return _daSortDir === -1 ? (b.size - a.size) : (a.size - b.size);
  });
}

function _daSortBy(key) {
  if (_daSortKey === key) { _daSortDir *= -1; }
  else { _daSortKey = key; _daSortDir = key === "size" ? -1 : 1; }
  _renderDiskItems(_daItems, _daTotal, document.getElementById("da-folder")?.value || "");
}

function _renderDiskItems(items, total, folder) {
  const el = document.getElementById("da-results");
  if (!items.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }
  if (_daView === "treemap") {
    _renderDiskTreemap(items, total, folder);
    return;
  }

  const sorted  = _daSortItems(items);
  const maxSize = sorted[0]?.size || 1;
  el.innerHTML = "";

  // En-tête de tri
  const hdr = document.createElement("div");
  hdr.className = "tool-row tool-header";
  hdr.style.cssText = "font-size:11px;padding:4px 10px;gap:8px";
  [["name","Nom"],["size","Taille"]].forEach(([key, label]) => {
    const span = document.createElement("span");
    span.style.cssText = "cursor:pointer;user-select:none;" + (key === "name" ? "flex:1" : "min-width:70px;text-align:right");
    span.innerHTML = `<strong>${label}</strong>${_daSortKey === key ? (_daSortDir === -1 ? " ↓" : " ↑") : ""}`;
    span.addEventListener("click", () => _daSortBy(key));
    hdr.appendChild(span);
  });
  el.appendChild(hdr);

  sorted.forEach(item => {
    const row = document.createElement("div");
    row.className = "da-row" + (item.is_dir ? " da-dir" : "");

    const icon = document.createElement("div");
    icon.className = "da-icon";
    icon.innerHTML = item.is_dir
      ? '<svg class="icon" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M20 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
      : '<svg class="icon" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';

    const name = document.createElement("div");
    name.className = "da-name";
    name.textContent = item.name;

    const barWrap = document.createElement("div"); barWrap.className = "da-bar-wrap";
    const bar     = document.createElement("div"); bar.className = "da-bar";
    bar.style.width = maxSize > 0 ? (item.size / maxSize * 100) + "%" : "0%";
    barWrap.appendChild(bar);

    const sizeEl = document.createElement("div"); sizeEl.className = "da-size"; sizeEl.textContent = item.size_fmt;
    const pct    = total > 0 ? (item.size / total * 100).toFixed(1) : 0;
    const pctEl  = document.createElement("div"); pctEl.className = "da-pct"; pctEl.textContent = pct + "%";

    row.append(icon, name, barWrap, sizeEl, pctEl);

    if (item.is_dir) {
      row.addEventListener("click", () => {
        _daHistory.push({ folder, items: [..._daItems], total: _daTotal });
        document.getElementById("da-folder").value = item.path;
        _runDiskAnalysis(item.path, false);
      });
    }

    el.appendChild(row);
  });
}

function _updateBreadcrumb(folder) {
  const el = document.getElementById("da-breadcrumb");
  if (_daHistory.length === 0) { el.style.display = "none"; return; }

  el.style.display = "block";
  el.innerHTML = "";

  // Bouton retour
  const back = document.createElement("span");
  back.style.cssText = "cursor:pointer;color:var(--accent);margin-right:8px";
  back.textContent = "← Retour";
  back.addEventListener("click", () => {
    const prev = _daHistory.pop();
    if (!prev) return;
    document.getElementById("da-folder").value = prev.folder;
    _daItems = prev.items;
    _daTotal = prev.total;
    _renderDiskItems(_daItems, _daTotal, prev.folder);
    _updateBreadcrumb(prev.folder);
  });
  el.appendChild(back);

  // Chemin complet
  const parts = folder.replace(/\\/g, "/").split("/").filter(Boolean);
  parts.forEach((part, i) => {
    const sep = document.createTextNode(i === 0 ? "" : " › ");
    el.appendChild(sep);
    const span = document.createElement("span");
    span.textContent = part;
    span.style.color = i === parts.length - 1 ? "var(--text)" : "var(--text-dim)";
    el.appendChild(span);
  });
}

// Squarified treemap — Bruls, Huijbregts & van Wijk (2000)
// Chaque item = { name, path, size, size_fmt, is_dir }
function _renderDiskTreemap(items, total, folder) {
  const el = document.getElementById("da-results");
  el.innerHTML = "";
  const container = document.createElement("div");
  container.className = "da-treemap";
  el.appendChild(container);

  // Attendre le layout pour connaître la largeur réelle
  requestAnimationFrame(() => {
    const W = container.clientWidth;
    const H = container.clientHeight;
    if (W <= 0 || H <= 0) return;

    // Ne garder que les items avec une taille > 0
    const positive = items.filter(i => i.size > 0);
    if (!positive.length) {
      container.innerHTML = `<div class="tool-empty" style="padding:20px">Aucun contenu mesurable.</div>`;
      return;
    }

    // Plafonner à ~80 items pour éviter un rendu illisible
    const sorted = [...positive].sort((a, b) => b.size - a.size).slice(0, 80);
    const sum = sorted.reduce((s, i) => s + i.size, 0);
    // Quantiles pour intensité de couleur (w : 1 à 5)
    const sizes = sorted.map(i => i.size).sort((a, b) => a - b);
    const q = (p) => sizes[Math.min(sizes.length - 1, Math.floor(sizes.length * p))];
    const qs = [q(0.2), q(0.4), q(0.6), q(0.8)];
    const intensityOf = (size) => {
      if (size >= qs[3]) return 5;
      if (size >= qs[2]) return 4;
      if (size >= qs[1]) return 3;
      if (size >= qs[0]) return 2;
      return 1;
    };

    // Surface totale du container normalisée à la somme des tailles
    const scale = (W * H) / sum;
    const scaled = sorted.map(i => ({ ...i, _a: i.size * scale }));

    // Squarified layout
    const rects = [];
    _squarify(scaled, [], { x: 0, y: 0, w: W, h: H }, rects);

    rects.forEach(r => {
      const tile = document.createElement("div");
      tile.className = "da-tile" + (r.item.is_dir ? " da-tile-dir" : "");
      if (r.w < 80 || r.h < 40) tile.classList.add("da-small");
      if (r.w < 40 || r.h < 24) tile.classList.add("da-tiny");
      tile.style.left   = r.x + "px";
      tile.style.top    = r.y + "px";
      tile.style.width  = r.w + "px";
      tile.style.height = r.h + "px";
      tile.dataset.w = String(intensityOf(r.item.size));
      tile.title = `${r.item.name}\n${r.item.size_fmt}${total > 0 ? ` — ${(r.item.size / total * 100).toFixed(1)}%` : ""}`;

      const name = document.createElement("div");
      name.className = "da-tile-name";
      name.textContent = r.item.name;
      const size = document.createElement("div");
      size.className = "da-tile-size";
      size.textContent = r.item.size_fmt;
      tile.append(name, size);

      if (r.item.is_dir) {
        tile.addEventListener("click", () => {
          _daHistory.push({ folder, items: [..._daItems], total: _daTotal });
          document.getElementById("da-folder").value = r.item.path;
          _runDiskAnalysis(r.item.path, false);
        });
      }
      container.appendChild(tile);
    });
  });
}

function _squarify(children, row, rect, out) {
  if (!children.length) {
    _layoutRow(row, rect, out);
    return;
  }
  const shortest = Math.min(rect.w, rect.h);
  const next = children[0];
  const newRow = row.concat([next]);
  if (row.length === 0 || _worst(row, shortest) >= _worst(newRow, shortest)) {
    _squarify(children.slice(1), newRow, rect, out);
  } else {
    const newRect = _layoutRow(row, rect, out);
    _squarify(children, [], newRect, out);
  }
}

function _worst(row, w) {
  if (!row.length) return Infinity;
  const s = row.reduce((a, r) => a + r._a, 0);
  const rMax = Math.max(...row.map(r => r._a));
  const rMin = Math.min(...row.map(r => r._a));
  const w2 = w * w;
  const s2 = s * s;
  return Math.max((w2 * rMax) / s2, s2 / (w2 * rMin));
}

function _layoutRow(row, rect, out) {
  if (!row.length) return rect;
  const s = row.reduce((a, r) => a + r._a, 0);
  if (rect.w >= rect.h) {
    // On pose la row sur le côté gauche (colonne verticale)
    const rowW = s / rect.h;
    let yOff = 0;
    row.forEach(r => {
      const h = r._a / rowW;
      out.push({ item: r, x: rect.x, y: rect.y + yOff, w: rowW, h });
      yOff += h;
    });
    return { x: rect.x + rowW, y: rect.y, w: rect.w - rowW, h: rect.h };
  } else {
    // On pose la row sur le haut (ligne horizontale)
    const rowH = s / rect.w;
    let xOff = 0;
    row.forEach(r => {
      const w = r._a / rowH;
      out.push({ item: r, x: rect.x + xOff, y: rect.y, w, h: rowH });
      xOff += w;
    });
    return { x: rect.x, y: rect.y + rowH, w: rect.w, h: rect.h - rowH };
  }
}

// ── Windows.old ──────────────────────────────────────────────────────────────

async function loadWindowsOld() {
  const el = document.getElementById("windows-old-info");
  if (!el) return;
  const btnEl = document.getElementById("btn-scan-winold");
  document.getElementById("winold-log").innerHTML = "";
  el.innerHTML = "";
  _btnScan(btnEl, "Analyse…");
  try {
    const res  = await fetch("/api/windows-old");
    const data = await res.json();
    renderWindowsOld(data);
    _btnReset(btnEl);
  } catch (e) {
    el.innerHTML = "";
    _logAppend("winold-log", "Erreur de chargement.");
    _btnReset(btnEl);
  }
}

function renderWindowsOld(data) {
  const el = document.getElementById("windows-old-info");
  if (!data.exists) {
    el.innerHTML = "";
    _logAppend("winold-log", "Aucun résultat.");
    return;
  }
  el.innerHTML = "";
  const row = document.createElement("div"); row.className = "tool-row"; row.style.padding = "14px 16px";
  const info = document.createElement("div"); info.className = "tool-info";
  const nameD = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = "C:\\Windows.old";
  const subD  = document.createElement("div"); subD.className  = "tool-sub";
  subD.textContent = `${data.size_fmt} — ancienne installation Windows, inutile si votre système fonctionne bien`;
  info.append(nameD, subD);
  const btn = document.createElement("button"); btn.className = "btn-ghost"; btn.style.cssText = "font-size:12px;flex-shrink:0;color:var(--red)";
  btn.textContent = `Supprimer (libérer ${data.size_fmt})`;
  btn.addEventListener("click", () => deleteWindowsOld(btn, data.size_fmt));
  row.append(info, btn);
  el.appendChild(row);
}

async function deleteWindowsOld(btn, sizeFmt) {
  showConfirm(
    "Supprimer Windows.old ?",
    `L'ancienne installation Windows (${sizeFmt}) sera définitivement supprimée. Vous ne pourrez plus revenir à la version précédente de Windows.`,
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/windows-old/delete", { method: "POST" });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          showToast("Erreur", data.error || "Suppression impossible.", "warn");
          if (btn) { btn.disabled = false; btn.textContent = `Supprimer (libérer ${sizeFmt})`; }
        } else {
          showToast("Windows.old supprimé", `${sizeFmt} libérés.`, "success");
          loadWindowsOld();
        }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = `Supprimer (libérer ${sizeFmt})`; }
      }
    }
  );
}

// ── Anciens installers ────────────────────────────────────────────────────────

function _setDefaultInstallerFolder() {
  // Le dossier est déjà pré-rempli côté Jinja — rien à faire
}

async function startInstallerScan() {
  const folder  = document.getElementById("inst-folder").value.trim();
  if (!folder) { showToast("Dossier requis", "Entrez un dossier à analyser.", "warn"); return; }

  const resultEl = document.getElementById("inst-results");
  const btnEl    = document.getElementById("btn-scan-inst");
  resultEl.innerHTML = _skeleton(4);
  _btnScan(btnEl, "Analyse…");

  try {
    const res = await fetch("/api/old-installers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    const data = await res.json();
    if (!res.ok) { showToast("Erreur", data.error, "warn"); _btnReset(btnEl); return; }
    _btnReset(btnEl);
    renderInstallers(data);
  } catch (e) {
    resultEl.innerHTML = `<div class="tool-error">Erreur : ${e.message}</div>`;
    _btnReset(btnEl);
  }
}

function renderInstallers(data) {
  if (!data.files.length) {
    document.getElementById("inst-results").innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }
  _instFiles = data.files; _instTotalFmt = data.total_fmt;
  _instSortKey = "size"; _instSortDir = -1;
  _renderInstallers();
}

function _renderInstallers() {
  const el = document.getElementById("inst-results");
  const files = [..._instFiles].sort((a, b) =>
    _instSortKey === "size" ? _instSortDir * (b.size - a.size) :
    _instSortKey === "age"  ? _instSortDir * (b.age_days - a.age_days) :
    _instSortDir * a.name.localeCompare(b.name)
  );
  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText:   `${_instFiles.length} fichier(s) — ${_instTotalFmt}`,
    deleteId:    "btn-delete-inst",
    deleteFn:    deleteSelectedInstallers,
    sortKeys:    [["size","Taille"],["age","Âge"],["name","Nom"]],
    sortKey:     _instSortKey, sortDir: _instSortDir,
    onSort: (key) => { _instSortKey === key ? _instSortDir *= -1 : (_instSortKey = key, _instSortDir = -1); _renderInstallers(); },
  }));

  _watchSelSize(el, document.getElementById("btn-delete-inst"));
  _renderBatched(files, (f, i) => _makeFileRow(f, i, "inst", {
    showSize: true,
    showPath: false,
    extraRight: (it) => {
      const age = document.createElement("span");
      age.className = "sel-dim";
      age.textContent = `${it.age_days} jours`;
      return age;
    },
  }), el);
}

function deleteSelectedInstallers() {
  _deleteSelected({
    resultsId: "inst-results",
    btnId:     "btn-delete-inst",
    endpoint:  "/api/old-installers/delete",
    confirmBody: (n, size) =>
      `Ces fichiers d'installation seront définitivement supprimés. Espace récupéré estimé : ${fmtBytesTools(size)}.`,
  });
}

// ── Confidentialité ──────────────────────────────────────────────────────────

let _instFiles = [], _instTotalFmt = "", _instSortKey = "size", _instSortDir = -1;

let _privacyItems = [];

async function loadPrivacy() {
  const el = document.getElementById("privacy-results");
  if (!el) return;
  el.innerHTML = _skeleton(4);
  try {
    const res  = await fetch("/api/privacy");
    _privacyItems = await res.json();
    renderPrivacy(_privacyItems);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderPrivacy(items) {
  const el = document.getElementById("privacy-results");
  if (!items.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }

  // Ne garder que les categories qui ont quelque chose a nettoyer
  const active = items.filter(i => i.count > 0);
  if (!active.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }

  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText:   `${active.length} catégorie(s)`,
    deleteId:    "btn-clean-privacy",
    deleteLabel: "Supprimer la sélection",
    deleteFn:    cleanSelectedPrivacy,
  }));

  active.forEach((item, i) => {
    const row = document.createElement("div");
    row.className = "tool-row";
    row.style.cursor = "pointer";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = `priv-${i}`;
    cb.dataset.id = item.id;
    cb.checked = true;
    cb.style.accentColor = "var(--accent)";
    cb.style.flexShrink = "0";

    const info = document.createElement("div");
    info.className = "tool-info";
    const name = document.createElement("div");
    name.className = "tool-name";
    name.textContent = item.label;
    const desc = document.createElement("div");
    desc.className = "tool-sub";
    desc.textContent = item.desc;
    desc.style.maxWidth = "none";
    info.append(name, desc);

    const meta = document.createElement("div");
    meta.className = "tool-meta";
    meta.textContent = item.size_fmt;

    // Clic sur toute la ligne toggle la case
    row.addEventListener("click", (e) => {
      if (e.target !== cb) cb.checked = !cb.checked;
    });

    row.append(cb, info, meta);
    el.appendChild(row);
  });
}

async function cleanSelectedPrivacy() {
  const checked = [...document.querySelectorAll("#privacy-results input[type=checkbox]:checked:not(.sel-all)")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un élément.", "warn"); return; }
  const ids = checked.map(c => c.dataset.id);
  const btn = document.getElementById("btn-clean-privacy");
  showConfirm(
    `Supprimer ${ids.length} élément(s) ?`,
    "L'historique sélectionné sera effacé. Cette action ne supprime pas de fichiers personnels.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Nettoyage…"; }
      try {
        const res  = await fetch("/api/privacy/clean", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          showToast("Erreur", (data.errors || []).join(", ") || data.error || "Nettoyage impossible.", "warn");
        } else {
          showToast("Suppression terminée", `${data.cleaned} supprimé(s).`, "success");
          loadPrivacy();
        }
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

// ── Fichier d'hibernation ─────────────────────────────────────────────────────

async function loadHibernation() {
  const el = document.getElementById("hiberfil-info");
  if (!el) return;
  const btnEl = document.getElementById("btn-scan-hiber");
  document.getElementById("hiber-log").innerHTML = "";
  el.innerHTML = "";
  _btnScan(btnEl, "Analyse…");
  try {
    const res  = await fetch("/api/hibernation");
    const data = await res.json();
    renderHibernation(data);
    _btnReset(btnEl);
  } catch (e) {
    el.innerHTML = "";
    _logAppend("hiber-log", "Erreur de chargement.");
    _btnReset(btnEl);
  }
}

function renderHibernation(data) {
  const el = document.getElementById("hiberfil-info");
  if (!data.enabled) {
    el.innerHTML = "";
    _logAppend("hiber-log", "Aucun résultat.");
    return;
  }
  el.innerHTML = "";
  const row = document.createElement("div"); row.className = "tool-row"; row.style.padding = "14px 16px";
  const info = document.createElement("div"); info.className = "tool-info";
  const nameD = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = "C:\\hiberfil.sys";
  const subD  = document.createElement("div"); subD.className  = "tool-sub";
  subD.textContent = `${data.size_fmt} — sauvegarde de la RAM pour la veille prolongée`;
  info.append(nameD, subD);

  const btn = document.createElement("button"); btn.className = "btn-ghost"; btn.style.cssText = "font-size:12px;flex-shrink:0";
  btn.textContent = `Désactiver l'hibernation (libérer ${data.size_fmt})`;
  btn.addEventListener("click", () => disableHibernation(btn, data.size_fmt));
  row.append(info, btn);
  el.appendChild(row);
}

async function disableHibernation(btn, sizeFmt) {
  showConfirm(
    `Désactiver l'hibernation ?`,
    `hiberfil.sys (${sizeFmt}) sera supprimé et la veille prolongée ne sera plus disponible. Vous pouvez la réactiver à tout moment via les options d'alimentation.`,
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Désactivation…"; }
      try {
        const res  = await fetch("/api/hibernation/disable", { method: "POST" });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          showToast("Erreur", data.error || "Impossible de désactiver l'hibernation.", "warn");
          if (btn) { btn.disabled = false; btn.textContent = `Désactiver l'hibernation (libérer ${sizeFmt})`; }
        } else {
          showToast("Hibernation désactivée", `${sizeFmt} libérés — hiberfil.sys supprimé.`, "success");
          loadHibernation();
        }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = `Désactiver l'hibernation (libérer ${sizeFmt})`; }
      }
    }
  );
}

// ── Dossiers vides ────────────────────────────────────────────────────────────

let _emptyFolders = [];

async function startEmptyFolderScan() {
  const folder  = document.getElementById("ef-folder").value.trim();
  if (!folder) { showToast("Dossier requis", "Entrez un dossier à analyser.", "warn"); return; }

  const logEl    = document.getElementById("ef-log");
  const resultEl = document.getElementById("ef-results");
  const btnEl    = document.getElementById("btn-scan-ef");

  logEl.innerHTML = "";
  resultEl.innerHTML = "";
  _emptyFolders = [];
  _btnScan(btnEl, "Analyse…");

  const addLog = (msg) => {
    const d = document.createElement("div"); d.className = "log-entry";
    d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
    logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight;
  };

  try {
    const res = await fetch("/api/empty-folders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    if (!res.ok) { const e = await res.json(); showToast("Erreur", e.error, "warn"); _btnReset(btnEl); return; }
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    _activeStreams["ef"] = es;
    _showCancelBtn(btnEl, "ef", () => { _btnReset(btnEl); document.getElementById("ef-log").innerHTML = ""; });
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    addLog(item.msg);
      if (item.type === "result") { _emptyFolders = item.folders; renderEmptyFolders(item.folders); }
      if (item.type === "done") {
        es.close(); _removeCancelBtn("ef");
        _btnReset(btnEl);
      }
    };
    es.onerror = () => { es.close(); _removeCancelBtn("ef"); _btnReset(btnEl); };
  } catch (err) {
    addLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderEmptyFolders(folders) {
  const el = document.getElementById("ef-results");
  if (!folders.length) {
    el.innerHTML = "";
    _logAppend("ef-log", "Aucun résultat.");
    return;
  }
  el.innerHTML = "";

  el.appendChild(_makeSelHeader(el, {
    countText:   `${folders.length} dossier(s) vide(s)`,
    deleteId:    "btn-delete-ef",
    deleteFn:    deleteSelectedEmptyFolders,
  }));

  _renderBatched(folders, (f, i) => _makeFileRow(f, i, "ef", {}), el);
}

function deleteSelectedEmptyFolders() {
  _deleteSelected({
    resultsId: "ef-results",
    btnId:     "btn-delete-ef",
    endpoint:  "/api/empty-folders/delete",
    confirmBody: "Ces dossiers sont vides et seront définitivement supprimés.",
  });
}

// ── Dossiers orphelins ────────────────────────────────────────────────────────

async function startOrphanScan() {
  const logEl    = document.getElementById("orphan-log");
  const resultEl = document.getElementById("orphan-results");
  const btnEl    = document.getElementById("btn-scan-orphan");

  logEl.innerHTML = "";
  resultEl.innerHTML = _skeleton(3);
  _btnScan(btnEl, "Analyse…");

  const addLog = (msg) => {
    const d = document.createElement("div"); d.className = "log-entry";
    d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
    logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight;
  };

  try {
    const res = await fetch("/api/orphan-folders", { method: "POST" });
    if (!res.ok) { const e = await res.json(); showToast("Erreur", e.error, "warn"); _btnReset(btnEl); return; }
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    _activeStreams["orphan"] = es;
    _showCancelBtn(btnEl, "orphan", () => { _btnReset(btnEl); document.getElementById("orphan-log").innerHTML = ""; });
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    addLog(item.msg);
      if (item.type === "result") renderOrphanFolders(item.folders, item.total_fmt);
      if (item.type === "done") {
        es.close(); _removeCancelBtn("orphan");
        _btnReset(btnEl);
      }
    };
    es.onerror = () => { es.close(); _removeCancelBtn("orphan"); _btnReset(btnEl); };
  } catch (err) {
    addLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderOrphanFolders(folders, totalFmt) {
  if (!folders.length) {
    document.getElementById("orphan-results").innerHTML = "";
    _logAppend("orphan-log", "Aucun résultat.");
    return;
  }
  _orphanFolders = folders; _orphanTotalFmt = totalFmt || "";
  _orphanSortKey = "size"; _orphanSortDir = -1;
  _renderOrphanFolders();
}

function _renderOrphanFolders() {
  const el = document.getElementById("orphan-results");
  const folders = [..._orphanFolders].sort((a, b) =>
    _orphanSortKey === "size" ? _orphanSortDir * (b.size - a.size) : _orphanSortDir * a.name.localeCompare(b.name)
  );
  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText:   `${_orphanFolders.length} dossier(s) orphelin(s)${_orphanTotalFmt ? " — " + _orphanTotalFmt + " récupérables" : ""}`,
    deleteId:    "btn-delete-orphan",
    deleteFn:    deleteSelectedOrphanFolders,
    sortKeys:    [["size","Taille"],["name","Nom"]],
    sortKey:     _orphanSortKey, sortDir: _orphanSortDir,
    onSort: (key) => { _orphanSortKey === key ? _orphanSortDir *= -1 : (_orphanSortKey = key, _orphanSortDir = -1); _renderOrphanFolders(); },
  }));

  _watchSelSize(el, document.getElementById("btn-delete-orphan"));
  _renderBatched(folders, (f, i) => _makeFileRow(f, i, "or", { showSize: true }), el);
}

function deleteSelectedOrphanFolders() {
  _deleteSelected({
    resultsId: "orphan-results",
    btnId:     "btn-delete-orphan",
    endpoint:  "/api/orphan-folders/delete",
    confirmBody: (n, size) =>
      `Assurez-vous que ces dossiers correspondent bien à des applications désinstallées. Espace récupéré estimé : ${fmtBytesTools(size)}.`,
  });
}

// ── Personnalisation Windows ──────────────────────────────────────────────────

let _tweaksLoaded = false;

let _tweakItems      = [];
let _tweakGroups     = [];
let _tweakFilter     = "all";
let _windowsVersion  = { major: 11, build: 0, display_version: "", caption: "" };

function _detectedWindowsMajor() {
  // Mode fake via localStorage pour tester le rendu W10 sur une machine W11.
  // Depuis la console : localStorage.setItem('pcc-fake-windows','10') puis switchTab('perso')
  const fake = localStorage.getItem("pcc-fake-windows");
  if (fake) return parseInt(fake, 10);
  return _windowsVersion.major || 11;
}

function fakeWindowsVersion(major) {
  if (major === null || major === undefined) {
    localStorage.removeItem("pcc-fake-windows");
  } else {
    localStorage.setItem("pcc-fake-windows", String(major));
  }
  // Re-render complet si les tweaks sont déjà chargés
  if (_tweakItems.length) {
    _renderTweaks();
    _renderTweakFilters();
    _renderTweakChart();
    _renderVersionBanner();
  }
}

function _isTweakCompatible(item) {
  const minWin = item.min_windows || 10;
  return _detectedWindowsMajor() >= minWin;
}

function _renderVersionBanner() {
  const el = document.getElementById("version-banner");
  if (!el) return;
  const detected = _detectedWindowsMajor();
  const real     = _windowsVersion.major || 11;
  const caption  = _windowsVersion.caption || "Windows";
  const isFake   = detected !== real;
  const incompatCount = _tweakItems.filter(i => !_isTweakCompatible(i) || i.present === false).length;

  el.classList.toggle("has-incompat", incompatCount > 0);
  el.style.display = "flex";

  const fakeLabel = isFake
    ? ` <span style="color:var(--amber)">[simulé W${detected}]</span>`
    : "";
  const incompatNote = incompatCount > 0
    ? `<span style="color:var(--text-mid)">— ${incompatCount} tweak(s) Windows 11 uniquement sont grisés</span>`
    : `<span style="color:var(--green)">— tous les tweaks sont compatibles</span>`;

  const fakeBtn = isFake
    ? `<button class="fake-btn" onclick="fakeWindowsVersion(null)">retirer simulation</button>`
    : (detected === 11
        ? `<button class="fake-btn" onclick="fakeWindowsVersion(10)">simuler W10</button>`
        : `<button class="fake-btn" onclick="fakeWindowsVersion(11)">simuler W11</button>`);

  el.innerHTML = `
    <span class="vb-icon">détecté</span>
    <strong>${_escapeHtml(caption)}</strong>${fakeLabel}
    ${incompatNote}
    ${fakeBtn}
  `;
}

const _TWEAK_TAG_LABELS = {
  "all":             "Tout voir",
  "performance":     "Performance",
  "telemetry":       "Télémétrie",
  "privacy":         "Confidentialité",
  "ads":             "Publicités",
  "cosmetic":        "Cosmétique",
  "security":        "Sécurité",
  "services":        "Services",
  "scheduled_tasks": "Tâches planifiées",
};

async function loadWindowsTweaks() {
  if (_tweaksLoaded) return;
  const el = document.getElementById("tweaks-list");
  if (!el) return;
  try {
    const res  = await fetch("/api/windows-tweaks");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erreur serveur");
    _tweakItems     = data.items || [];
    _tweakGroups    = data.groups || [];
    _windowsVersion = data.windows_version || { major: 11, build: 0, display_version: "", caption: "" };
    _renderVersionBanner();
    _renderTweaks();
    _renderTweakFilters();
    _renderTweakChart();
    _loadTweakPresets();
    _tweaksLoaded = true;
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur : ${e.message}</div>`;
  }
}

let _tweakPresetsCache = [];

async function _loadTweakPresets() {
  try {
    const res  = await fetch("/api/windows-tweaks/presets");
    const data = await res.json();
    const el   = document.getElementById("tweak-presets");
    if (!el || !data.presets) return;
    _tweakPresetsCache = data.presets;
    el.innerHTML = data.presets.map(p => `
      <button class="btn-ghost tweak-preset-btn"
              title="${_escapeHtml(p.desc)}"
              onclick="applyTweakPreset('${p.id}')">
        ${_escapeHtml(p.label)} <span style="color:var(--text-dim);font-size:11px;margin-left:4px">${p.count}</span>
      </button>
    `).join("");
  } catch (e) {}
}

async function loadGamingMode() {
  try {
    const res = await fetch("/api/gaming-mode");
    const data = await res.json();
    _renderGamingMode(data);
  } catch (e) {
    console.warn("[gaming] load failed:", e);
  }
}

function _renderGamingMode(data) {
  const btn = document.getElementById("btn-gaming-toggle");
  const stateEl = document.getElementById("gaming-state");
  const card = document.getElementById("gaming-card");
  if (!btn || !stateEl) return;
  if (data.enabled) {
    btn.textContent = "Désactiver";
    btn.classList.remove("btn-primary");
    btn.classList.add("btn-ghost");
    if (card) card.style.borderLeftColor = "var(--green)";
    const when = data.saved_at ? new Date(data.saved_at).toLocaleString("fr-FR") : "—";
    stateEl.innerHTML = `<strong style="color:var(--green)">Actif</strong> depuis ${when} — ${data.services_count} services arrêtés. Cliquez sur <em>Désactiver</em> pour restaurer l'état précédent.`;
  } else {
    btn.textContent = "Activer";
    btn.classList.remove("btn-ghost");
    btn.classList.add("btn-primary");
    if (card) card.style.borderLeftColor = "var(--text)";
    stateEl.innerHTML = `Arrête SysMain, WSearch, DiagTrack, WerSvc, MapsBroker, RetailDemo et bascule sur le plan <strong>High Performance</strong>. Entièrement réversible.`;
  }
}

async function toggleGamingMode() {
  if (!window.IS_ADMIN) {
    showToast("Droits administrateur requis", "Relancez OpenCleaner en administrateur.", "warn");
    return;
  }
  const btn = document.getElementById("btn-gaming-toggle");
  const currentlyOn = btn.textContent.trim() === "Désactiver";
  const newEnabled = !currentlyOn;
  btn.disabled = true;
  const actId = activityPush(
    newEnabled ? "Activation mode gaming" : "Restauration configuration",
    "run",
    "En cours…",
    { tab: "perso" }
  );
  try {
    const res = await fetch("/api/gaming-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: newEnabled }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || "Erreur");
    activityDone(actId, newEnabled
      ? `${data.applied || 0} services arrêtés`
      : `${data.restored || 0} services restaurés`);
    loadGamingMode();
    if (typeof loadServices === "function") loadServices();
  } catch (e) {
    activityDone(actId, "Échec", "fail");
    showToast("Mode gaming", e.message, "warn");
  } finally {
    btn.disabled = false;
  }
}

async function exportConfigSnapshot() {
  try {
    const res = await fetch("/api/config/export");
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || ("HTTP " + res.status));
    }
    const disp = res.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename="([^"]+)"/);
    const filename = m ? m[1] : "opencleaner-config.json";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("Sauvegarde créée", `${filename} téléchargé`, "success");
  } catch (e) {
    showToast("Sauvegarde impossible", e.message, "warn");
  }
}

function triggerImportConfig() {
  const input = document.getElementById("config-import-file");
  if (input) {
    input.value = "";
    input.click();
  }
}

async function handleImportConfigFile(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  let snapshot;
  try {
    const text = await file.text();
    snapshot = JSON.parse(text);
  } catch (e) {
    showToast("Fichier invalide", "Impossible de lire le JSON.", "warn");
    return;
  }
  if (!snapshot || typeof snapshot !== "object") {
    showToast("Fichier invalide", "Format non reconnu.", "warn");
    return;
  }

  const counts = {
    tweaks:   Object.keys(snapshot.tweaks   || {}).length,
    services: Object.keys(snapshot.services || {}).length,
    tasks:    Object.keys(snapshot.tasks    || {}).length,
    autoruns: Object.keys(snapshot.autoruns || {}).length,
  };
  const total = counts.tweaks + counts.services + counts.tasks + counts.autoruns;
  if (!total) {
    showToast("Rien à restaurer", "Snapshot vide.", "warn");
    return;
  }

  const summary = `Restaurer cette configuration ?\n\n` +
    `• ${counts.tweaks} tweaks\n` +
    `• ${counts.services} services\n` +
    `• ${counts.tasks} tâches planifiées\n` +
    `• ${counts.autoruns} autoruns\n\n` +
    `Date : ${snapshot.created_at || "inconnue"}\n` +
    `Hôte : ${snapshot.hostname || "inconnu"}`;
  if (!confirm(summary)) return;

  const actId = activityPush("Restauration configuration", "run", "Application en cours…", { tab: "outils" });
  try {
    const res = await fetch("/api/config/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    activityDone(actId, `${data.applied} appliqués, ${data.skipped} ignorés`);
    if (data.errors && data.errors.length) {
      console.warn("[restore] errors:", data.errors);
    }
    // Rafraîchit les panneaux impactés
    if (typeof loadWindowsTweaks === "function") loadWindowsTweaks();
    if (typeof loadServices === "function") loadServices();
    if (typeof loadScheduledTasks === "function") loadScheduledTasks();
    if (typeof loadStartup === "function") loadStartup();
  } catch (e) {
    activityDone(actId, "Échec", "fail");
    showToast("Restauration impossible", e.message, "warn");
  }
}

async function exportTweaksReg() {
  try {
    const res = await fetch("/api/windows-tweaks/export-reg");
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || ("HTTP " + res.status));
    }
    const disp = res.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename="([^"]+)"/);
    const filename = m ? m[1] : "opencleaner-config.reg";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("Fichier .reg exporté", `${filename} téléchargé`, "success");
  } catch (e) {
    showToast("Export .reg impossible", e.message, "warn");
  }
}

async function applyTweakPreset(presetId) {
  const preset = _tweakPresetsCache.find(p => p.id === presetId);
  if (!preset) return;

  const tweaksOff   = preset.tweaks_off   || [];
  const servicesOff = preset.services_off || [];
  const tasksOff    = preset.tasks_off    || [];
  const totalCount  = tweaksOff.length + servicesOff.length + tasksOff.length;

  const needsAdmin = (servicesOff.length > 0 || tasksOff.length > 0);
  const adminNote = needsAdmin
    ? `\n\n⚠ Les services (${servicesOff.length}) et tâches planifiées (${tasksOff.length}) nécessitent les droits administrateur. Si tu n'es pas admin, seules les ${tweaksOff.length} tweaks seront appliqués.`
    : "";

  showConfirm(
    `Appliquer le preset « ${preset.label} » ?`,
    `${totalCount} fonctionnalité(s) vont être désactivées au total : ${tweaksOff.length} tweaks, ${servicesOff.length} services, ${tasksOff.length} tâches planifiées.${adminNote}`,
    async () => {
      let okCount = 0;
      let failCount = 0;

      // 1. Tweaks (filtre aussi les incompatibles avec la version détectée)
      const tweakChanges = tweaksOff
        .map(id => ({ id, active: false }))
        .filter(c => {
          const item = _tweakItems.find(i => i.id === c.id);
          return item && item.active && _isTweakCompatible(item);
        });
      if (tweakChanges.length) {
        try {
          const res  = await fetch("/api/windows-tweaks/set-batch", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ changes: tweakChanges }),
          });
          const data = await res.json();
          (data.results || []).forEach(r => {
            if (!r.ok) { failCount++; return; }
            okCount++;
            const item = _tweakItems.find(i => i.id === r.id);
            if (item) item.active = false;
            const row = document.querySelector(`#tweaks-list .tweak-row[data-id="${r.id}"]`);
            if (row) {
              const cb = row.querySelector("input[type=checkbox]");
              if (cb) cb.checked = false;
              row.classList.add("tweak-ok");
              setTimeout(() => row.classList.remove("tweak-ok"), 600);
            }
          });
        } catch (e) {
          failCount += tweakChanges.length;
        }
      }

      // 2. Services (skip si non chargé ou non-admin)
      if (servicesOff.length && typeof _services !== "undefined" && _services.length) {
        const svcChanges = servicesOff
          .map(name => ({ name, enabled: false }))
          .filter(c => {
            const s = _services.find(x => x.name === c.name);
            return s && s.exists && s.active;
          });
        if (svcChanges.length) {
          try {
            const res  = await fetch("/api/services/set-batch", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ changes: svcChanges }),
            });
            const data = await res.json();
            if (res.ok) {
              (data.results || []).forEach(r => {
                if (!r.ok) { failCount++; return; }
                okCount++;
                const svc = _services.find(s => s.name === r.name);
                if (svc) svc.active = false;
                const row = document.querySelector(`#services-list .tweak-row[data-service="${r.name}"]`);
                if (row) {
                  const cb = row.querySelector("input[type=checkbox]");
                  if (cb) cb.checked = false;
                  row.classList.add("tweak-ok");
                  setTimeout(() => row.classList.remove("tweak-ok"), 600);
                }
              });
            } else {
              // 403 non-admin probable
              failCount += svcChanges.length;
            }
          } catch (e) {
            failCount += svcChanges.length;
          }
        }
      }

      // 3. Tâches planifiées
      if (tasksOff.length && typeof _scheduledTasks !== "undefined" && _scheduledTasks.length) {
        const taskChanges = tasksOff
          .map(path => ({ path, enabled: false }))
          .filter(c => {
            const t = _scheduledTasks.find(x => x.path === c.path);
            return t && t.exists && t.active;
          });
        if (taskChanges.length) {
          try {
            const res  = await fetch("/api/scheduled-tasks/set-batch", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ changes: taskChanges }),
            });
            const data = await res.json();
            if (res.ok) {
              (data.results || []).forEach(r => {
                if (!r.ok) { failCount++; return; }
                okCount++;
                const t = _scheduledTasks.find(x => x.path === r.path);
                if (t) t.active = false;
                const row = document.querySelector(`#tasks-list .tweak-row[data-task-path="${CSS.escape(r.path)}"]`);
                if (row) {
                  const cb = row.querySelector("input[type=checkbox]");
                  if (cb) cb.checked = false;
                  row.classList.add("tweak-ok");
                  setTimeout(() => row.classList.remove("tweak-ok"), 600);
                }
              });
            } else {
              failCount += taskChanges.length;
            }
          } catch (e) {
            failCount += taskChanges.length;
          }
        }
      }

      _renderTweakChart();

      if (okCount === 0 && failCount === 0) {
        showToast("Rien à faire", "Toutes les fonctionnalités de ce preset sont déjà dans l'état cible.", "info");
      } else if (failCount === 0) {
        showToast("Preset appliqué", `${okCount} fonctionnalité(s) désactivée(s).`, "success");
      } else {
        showToast("Preset partiel", `${okCount} appliqué(s), ${failCount} échec(s) (admin requis pour services/tâches ?).`, "warn");
      }
    },
  );
}

const _TWEAK_GROUP_DESCS = {
  ai:         "Copilot, Edge IA, contenus recommandés par Microsoft. Désactiver libère de la RAM et réduit le trafic réseau.",
  taskbar:    "Widgets météo/news, position de la barre, raccourcis imposés.",
  search:     "Indexation Windows Search, suggestions de recherche. Impact direct sur les I/O disque et la RAM.",
  start:      "Publicités dans le menu Démarrer, suggestions d'apps du Store, recommandations.",
  lockscreen: "Tips et publicités sur l'écran de verrouillage, fréquence de feedback.",
  explorer:   "Fichiers récents, extensions masquées, fichiers cachés, page d'accueil.",
  privacy:    "Historique d'activité, presse-papiers cloud, frappe clavier, enregistrement Game DVR/Bar.",
};

let _tweakGroupsOpen = {};  // {groupId: true/false}

function _renderTweaks() {
  const el = document.getElementById("tweaks-list");
  el.innerHTML = "";
  _tweakGroups.forEach(g => {
    const items = _tweakItems.filter(i => i.group === g.id);
    if (!items.length) return;

    // Métriques agrégées
    const compatItems = items.filter(i => (i.present !== false) && ((_detectedWindowsMajor?.() || 11) >= (i.min_windows || 10)));
    const totalRam    = compatItems.reduce((s, i) => s + ((i.impact || {}).ram_mb || 0), 0);
    const totalProcs  = compatItems.reduce((s, i) => s + ((i.impact || {}).processes || 0), 0);
    const activeCount = compatItems.filter(i => i.active).length;
    const offCount    = compatItems.filter(i => !i.active).length;
    const absentCount = items.length - compatItems.length;

    const isOpen = !!_tweakGroupsOpen[g.id];

    // En-tête cliquable (accordéon)
    const section = document.createElement("div");
    section.className = "tweak-group-section";
    section.dataset.group = g.id;

    const gh = document.createElement("div");
    gh.className = "tweak-group-title tweak-group-accordion" + (isOpen ? " open" : "");
    gh.dataset.group = g.id;

    // Ligne 1 : titre + chevron + bulk
    const topLine = document.createElement("div");
    topLine.className = "tweak-group-top";
    topLine.innerHTML = `
      <svg class="tweak-group-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      <span class="tweak-group-name">${_escapeHtml(g.label)}</span>
      <span class="tweak-group-stats">
        ${totalRam ? `<span>${totalRam} Mo RAM</span>` : ""}
        ${totalProcs ? `<span>${totalProcs} proc.</span>` : ""}
        <span>${offCount}/${compatItems.length} désactivé${offCount > 1 ? "s" : ""}</span>
        ${absentCount ? `<span>${absentCount} absent${absentCount > 1 ? "s" : ""}</span>` : ""}
      </span>
      <span class="bulk" onclick="event.stopPropagation()">
        <button class="btn-ghost bulk-btn" onclick="bulkToggleGroup('${g.id}', false)">Tout désactiver</button>
        <button class="btn-ghost bulk-btn" onclick="bulkToggleGroup('${g.id}', true)">Tout activer</button>
      </span>
    `;

    gh.appendChild(topLine);

    // Ligne 2 : description du groupe
    const descLine = document.createElement("div");
    descLine.className = "tweak-group-desc";
    descLine.textContent = _TWEAK_GROUP_DESCS[g.id] || "";
    gh.appendChild(descLine);

    gh.addEventListener("click", (e) => {
      if (e.target.closest(".bulk")) return;
      _tweakGroupsOpen[g.id] = !_tweakGroupsOpen[g.id];
      const body = section.querySelector(".tweak-group-body");
      const open = _tweakGroupsOpen[g.id];
      gh.classList.toggle("open", open);
      if (body) body.style.display = open ? "" : "none";
    });

    section.appendChild(gh);

    // Corps (les rows individuelles)
    const body = document.createElement("div");
    body.className = "tweak-group-body";
    body.style.display = isOpen ? "" : "none";
    items.forEach(it => body.appendChild(_tweakRow(it)));
    section.appendChild(body);

    el.appendChild(section);
  });
  _applyTweakFilter();
}

async function bulkToggleGroup(groupId, targetActive) {
  // Récupère les items du groupe qui sont (a) dans _tweakItems, (b) visibles
  // selon le filtre courant, et (c) dans un état différent de la cible.
  const rows = [...document.querySelectorAll(`#tweaks-list .tweak-row[data-group="${groupId}"]`)]
    .filter(r => !r.classList.contains("tweak-hidden"));
  const changes = [];
  for (const row of rows) {
    const id = row.dataset.id;
    const item = _tweakItems.find(i => i.id === id);
    if (!item) continue;
    if (item.active !== targetActive) {
      changes.push({ id, active: targetActive });
    }
  }
  if (!changes.length) return;

  // Trouve et désactive temporairement les boutons bulk du groupe
  const btns = document.querySelectorAll(`#tweaks-list .tweak-group-title[data-group="${groupId}"] .bulk button`);
  btns.forEach(b => b.disabled = true);

  try {
    const res = await fetch("/api/windows-tweaks/set-batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    });
    const data = await res.json();
    (data.results || []).forEach(r => {
      if (!r.ok) return;
      const item = _tweakItems.find(i => i.id === r.id);
      if (item) item.active = r.active;
      // Met à jour le checkbox DOM correspondant
      const row = document.querySelector(`#tweaks-list .tweak-row[data-id="${r.id}"]`);
      if (row) {
        const cb = row.querySelector("input[type=checkbox]");
        if (cb) cb.checked = !!r.active;
        row.classList.add("tweak-ok");
        setTimeout(() => row.classList.remove("tweak-ok"), 600);
      }
    });
    _renderTweakChart();
    if (data.fail_count > 0) {
      showToast("Bulk partiel", `${data.ok_count} appliqué(s), ${data.fail_count} échec(s)`, "warn");
    }
  } catch (e) {
    showToast("Erreur batch", e.message, "warn");
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

function _escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function _renderTweakFilters() {
  const el = document.getElementById("tweak-filters");
  if (!el) return;
  const tags = ["all", "performance", "telemetry", "privacy", "ads", "cosmetic", "security", "services", "scheduled_tasks"];
  const counts = {};
  for (const t of tags) counts[t] = 0;
  // Ne compter que les tweaks compatibles avec la version détectée
  for (const it of _tweakItems) {
    if (!_isTweakCompatible(it)) continue;
    counts["all"]++;
    for (const tag of (it.tags || [])) {
      if (tag in counts) counts[tag]++;
    }
  }
  // Services et tâches : compte dépendant du chargement lazy
  const svcCount  = (typeof _services       !== "undefined") ? _services.filter(s => s.exists).length       : 0;
  const taskCount = (typeof _scheduledTasks !== "undefined") ? _scheduledTasks.filter(t => t.exists).length : 0;
  counts["services"]        = svcCount;
  counts["scheduled_tasks"] = taskCount;
  counts["all"] += svcCount + taskCount;

  el.innerHTML = tags.map(t => {
    const label = _TWEAK_TAG_LABELS[t] || t;
    const cls = t === _tweakFilter ? "active" : "";
    const c = counts[t];
    const countHtml = (t === "services" || t === "scheduled_tasks") && c === 0
      ? ""  // pas de count si pas encore chargé
      : `<span class="c">${c}</span>`;
    return `<button class="tweak-filter-btn ${cls}" data-filter="${t}" onclick="setTweakFilter('${t}')">${label} ${countHtml}</button>`;
  }).join("");
}

function setTweakFilter(tag) {
  _tweakFilter = tag;
  document.querySelectorAll(".tweak-filter-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.filter === tag);
  });
  _applyTweakFilter();
}

// Mapping : un tag du main filter bar → catégorie dans services/tasks.
// Les catégories services/tasks sont : telemetry, gaming, legacy, cloud_sync, privacy.
// Les tags tweak sont : performance, telemetry, privacy, ads, cosmetic, security.
// Seuls telemetry et privacy matchent directement. Les autres tags tweak
// n'ont pas d'équivalent côté services → on affiche tout (filter = all).
function _tweakTagToServiceCategory(tweakTag) {
  if (tweakTag === "telemetry") return "telemetry";
  if (tweakTag === "privacy")   return "privacy";
  return "all";
}

function _applyTweakFilter() {
  const filter = _tweakFilter;
  const isServicesFilter = filter === "services";
  const isTasksFilter    = filter === "scheduled_tasks";

  // Cards à masquer/montrer selon le filtre
  const presetsCard    = document.querySelector('#tab-perso .card:has(#tweak-presets)');
  const chartCard      = document.querySelector('#tab-perso .card:has(#tweak-chart)');
  const tweaksListCard = document.querySelector('#tab-perso .card:has(#tweaks-list)');
  const servicesCard   = document.querySelector('#tab-perso .card:has(#services-list)');
  const tasksCard      = document.querySelector('#tab-perso .card:has(#tasks-list)');
  const show = el => { if (el) el.style.display = ""; };
  const hide = el => { if (el) el.style.display = "none"; };

  if (isServicesFilter) {
    hide(presetsCard); hide(chartCard); hide(tweaksListCard); hide(tasksCard);
    show(servicesCard);
    _servicesFilter = "all";
    if (typeof _renderServices === "function" && _services.length) _renderServices(_servicesIsAdmin);
    servicesCard?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (isTasksFilter) {
    hide(presetsCard); hide(chartCard); hide(tweaksListCard); hide(servicesCard);
    show(tasksCard);
    _tasksFilter = "all";
    if (typeof _renderScheduledTasks === "function" && _scheduledTasks.length) _renderScheduledTasks(_tasksIsAdmin);
    tasksCard?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  // Filtre tag classique : restaure tout, puis applique par tag sur les tweak-rows
  show(presetsCard); show(chartCard); show(tweaksListCard); show(servicesCard); show(tasksCard);

  const rows = document.querySelectorAll("#tweaks-list .tweak-row");
  rows.forEach(r => {
    if (filter === "all") {
      r.classList.remove("tweak-hidden");
      return;
    }
    const tags = (r.dataset.tags || "").split(",");
    r.classList.toggle("tweak-hidden", !tags.includes(filter));
  });
  // Masquer les titres de groupe sans items visibles
  document.querySelectorAll(".tweak-group-title").forEach(gh => {
    const groupId = gh.dataset.group;
    if (!groupId) return;
    const hasVisible = [...document.querySelectorAll(`#tweaks-list .tweak-row[data-group="${groupId}"]`)]
      .some(r => !r.classList.contains("tweak-hidden"));
    gh.classList.toggle("tweak-hidden", !hasVisible);
  });

  // Propager le filtre aux sections services/tasks si les catégories matchent
  const targetCategory = _tweakTagToServiceCategory(filter);
  if (typeof _services !== "undefined" && _services.length) {
    _servicesFilter = targetCategory;
    _renderServices(_servicesIsAdmin);
  }
  if (typeof _scheduledTasks !== "undefined" && _scheduledTasks.length) {
    _tasksFilter = targetCategory;
    _renderScheduledTasks(_tasksIsAdmin);
  }
}

function _renderTweakChart() {
  const chart = document.getElementById("tweak-chart");
  const note  = document.getElementById("tweak-chart-note");
  if (!chart) return;

  // Baseline = "tout activé" (Windows stock)
  // Projection = "configuration actuelle" (ce qui est encore actif)
  // Les barres diminuent quand l'utilisateur désactive des features.
  // Inclut les tweaks compatibles + services (si chargés). Les tâches
  // planifiées n'ont pas d'impact RAM direct.
  let baseRam = 0, baseProc = 0, projRam = 0, projProc = 0;
  for (const it of _tweakItems) {
    if (!_isTweakCompatible(it)) continue;  // exclure les W11-only sur W10
    const ram  = (it.impact && it.impact.ram_mb)    || 0;
    const proc = (it.impact && it.impact.processes) || 0;
    baseRam  += ram;
    baseProc += proc;
    if (it.active) {
      projRam  += ram;
      projProc += proc;
    }
  }

  // Services (peut être vide si pas encore chargé)
  const svcList = (typeof _services !== "undefined") ? _services.filter(s => s.exists) : [];
  for (const s of svcList) {
    const ram = (s.impact && s.impact.ram_mb) || 0;
    baseRam += ram;
    baseProc += 1;  // chaque service = 1 process potentiel
    if (s.active) {
      projRam += ram;
      projProc += 1;
    }
  }

  const compatibleTweaks = _tweakItems.filter(_isTweakCompatible);
  const tweakTotalCount  = compatibleTweaks.length;
  const tweakActiveCount = compatibleTweaks.filter(i => i.active).length;
  const svcTotalCount    = svcList.length;
  const svcActiveCount   = svcList.filter(s => s.active).length;
  const taskList = (typeof _scheduledTasks !== "undefined") ? _scheduledTasks.filter(t => t.exists) : [];
  const taskTotalCount   = taskList.length;
  const taskActiveCount  = taskList.filter(t => t.active).length;

  const totalCount  = tweakTotalCount + svcTotalCount + taskTotalCount;
  const activeCount = tweakActiveCount + svcActiveCount + taskActiveCount;
  const savedRam     = baseRam  - projRam;
  const savedProc    = baseProc - projProc;
  const savedCount   = totalCount - activeCount;

  const metrics = [
    {
      label: "RAM bloat<br>(Mo)",
      before: baseRam,
      after:  projRam,
      max:    Math.max(baseRam, 100),
      fmt:    v => Math.round(v).toString(),
      suffix: " Mo",
      saved:  savedRam,
      savedFmt: v => `-${Math.round(v)} Mo`,
    },
    {
      label: "Processus<br>bloat",
      before: baseProc,
      after:  projProc,
      max:    Math.max(baseProc, 4),
      fmt:    v => Math.round(v).toString(),
      suffix: "",
      saved:  savedProc,
      savedFmt: v => `-${Math.round(v)}`,
    },
    {
      label: "Fonctions<br>actives",
      before: totalCount,
      after:  activeCount,
      max:    totalCount,
      fmt:    v => `${Math.round(v)}`,
      suffix: "",
      saved:  savedCount,
      savedFmt: v => `-${Math.round(v)}`,
    },
  ];

  chart.innerHTML = `
    <div class="tweak-chart">
      ${metrics.map(m => {
        const hBefore = Math.max(1, (m.before / m.max) * 100);
        const hAfter  = Math.max(1, (m.after  / m.max) * 100);
        const deltaHtml = m.saved > 0
          ? `<div class="tweak-delta">${m.savedFmt(m.saved)}</div>`
          : `<div class="tweak-delta" style="color:var(--text-dim);font-weight:400">—</div>`;
        return `
          <div class="tweak-col-group">
            <div class="tweak-bars">
              <div class="tweak-col" style="height:${hBefore}%"><span class="v">${m.fmt(m.before)}${m.suffix}</span></div>
              <div class="tweak-col after" style="height:${hAfter}%"><span class="v">${m.fmt(m.after)}${m.suffix}</span></div>
            </div>
            <div class="tweak-lbl-col">${m.label}</div>
            ${deltaHtml}
          </div>
        `;
      }).join("")}
    </div>
    <div class="tweak-chart-legend">
      <span><span class="leg-dot b"></span>Windows stock (tout activé)</span>
      <span><span class="leg-dot a"></span>Ta configuration actuelle</span>
    </div>
  `;

  if (note) {
    const measured = _tweakItems.filter(i => i.impact && i.impact.source === "measured").length;
    const sourceLabel = measured > 0
      ? `<span style="color:var(--green)">${measured} valeur(s) mesurée(s) sur ta machine</span>`
      : `<span style="color:var(--text-dim)">estimations moyennes en ligne</span>`;
    note.innerHTML = `${savedCount} / ${totalCount} fonctions désactivées<br>≈ ${Math.round(savedRam)} Mo libérés<br>${sourceLabel}`;
  }
}

// ── Panneau latéral de détail V6 ──────────────────────────────────────────────

const _TWEAK_DETAILS = {
  // IA
  copilot:                     { tradeoff: "L'assistant IA Copilot ne sera plus accessible. Pas d'impact si déjà absent du PC.", extra: "Économie significative de RAM si Copilot était actif." },
  copilot_button:              { tradeoff: "Le bouton Copilot disparaît de la barre des tâches. Purement cosmétique.", extra: "" },
  // Edge
  edge_startup_boost:          { tradeoff: "Edge mettra 1-2 secondes de plus à s'ouvrir au premier lancement. Libère ~350 Mo de RAM quand Edge est fermé.", extra: "Edge pré-charge des processus au démarrage de Windows." },
  edge_background:             { tradeoff: "Les notifications web d'Edge ne fonctionneront plus quand le navigateur est fermé.", extra: "Réduit les processus msedge.exe en arrière-plan." },
  edge_hub_sidebar:            { tradeoff: "La barre latérale Bing/Discover d'Edge disparaît. Aucun impact performance.", extra: "Confidentialité : réduit les requêtes vers Microsoft." },
  edge_shopping:               { tradeoff: "Les suggestions de prix et coupons dans Edge sont désactivées.", extra: "Confidentialité uniquement." },
  edge_personalization_reporting: { tradeoff: "Edge n'envoie plus de données de navigation à Microsoft pour la personnalisation.", extra: "Confidentialité uniquement, aucun impact performance." },
  // Télémétrie
  ad_id:                       { tradeoff: "Les publicités dans les apps Microsoft seront moins ciblées. Aucun impact performance.", extra: "Confidentialité : Windows ne suit plus vos centres d'intérêt." },
  tailored_experiences:        { tradeoff: "Les suggestions personnalisées de Windows (tips, recommandations) disparaissent.", extra: "Confidentialité uniquement." },
  app_launch_tracking:         { tradeoff: "Le menu Démarrer ne triera plus les apps par fréquence d'utilisation.", extra: "Confidentialité : Microsoft ne sait plus quelles apps vous lancez." },
  diagnostic_data:             { tradeoff: "Windows envoie uniquement les données de diagnostic obligatoires au lieu de l'ensemble complet.", extra: "Réduit légèrement le trafic réseau." },
  feedback_frequency:          { tradeoff: "Windows ne vous demandera plus votre avis via des pop-ups de feedback.", extra: "" },
  // Notifications
  tips_suggestions:            { tradeoff: "Plus de tips 'Saviez-vous que...' ni de suggestions d'apps Microsoft.", extra: "Réduit les distractions." },
  lockscreen_tips:             { tradeoff: "L'écran de verrouillage n'affiche plus de suggestions ni de publicités.", extra: "" },
  start_suggestions:           { tradeoff: "Le menu Démarrer ne suggère plus d'apps du Microsoft Store.", extra: "Réduit les publicités intégrées." },
  // IA - Notepad
  notepad_ai:                  { tradeoff: "Désactive les fonctions IA dans le Bloc-notes Windows 11.", extra: "Confidentialité." },
  // Recherche & widgets
  search_highlights:           { tradeoff: "La recherche dans le menu Démarrer sera plus lente (pas d'index). Réduit significativement l'activité disque en arrière-plan.", extra: "Impact fort sur les portables (I/O disque + batterie)." },
  widgets:                     { tradeoff: "Le panneau météo/actualités disparaît de la barre des tâches. Accessible via navigateur web.", extra: "" },
  // UI / Cosmétique
  taskbar_center:              { tradeoff: "La barre des tâches s'aligne à gauche au lieu du centre (style Windows 10).", extra: "Purement cosmétique." },
  start_recommended:           { tradeoff: "Masque la section 'Recommandé' du menu Démarrer.", extra: "Cosmétique." },
  start_iris_recommendations:  { tradeoff: "Supprime les recommandations Iris (publicités) du menu Démarrer.", extra: "" },
  start_irisxp:                { tradeoff: "Supprime les suggestions IA du menu Démarrer.", extra: "" },
  explorer_recommended:        { tradeoff: "L'accueil de l'Explorateur ne montre plus les fichiers recommandés.", extra: "Confidentialité." },
  // Explorateur
  show_frequent:               { tradeoff: "L'Explorateur ne montre plus les dossiers fréquemment accédés.", extra: "Confidentialité." },
  hide_file_ext:               { tradeoff: "Les extensions de fichiers (.exe, .pdf, .docx) deviennent visibles. Recommandé pour la sécurité.", extra: "Sécurité : évite les pièges type document.pdf.exe." },
  hide_hidden_files:           { tradeoff: "Les fichiers cachés deviennent visibles dans l'Explorateur.", extra: "Utile pour le dépannage et le développement." },
  launch_to_home:              { tradeoff: "L'Explorateur s'ouvre sur 'Ce PC' au lieu de la page d'accueil.", extra: "Cosmétique." },
  // Vie privée
  activity_history:            { tradeoff: "Windows ne garde plus l'historique des apps et fichiers ouverts.", extra: "Confidentialité : la timeline est désactivée." },
  cloud_clipboard:             { tradeoff: "Le presse-papiers ne se synchronise plus entre vos appareils Microsoft.", extra: "Confidentialité." },
  inking_typing:               { tradeoff: "Windows n'envoie plus de données de frappe clavier à Microsoft.", extra: "Télémétrie uniquement." },
  online_speech:               { tradeoff: "La reconnaissance vocale en ligne est désactivée. Cortana/dictée ne fonctionneront plus.", extra: "Confidentialité." },
  // Gaming
  game_dvr:                    { tradeoff: "L'enregistrement automatique des jeux en arrière-plan est désactivé. Vous pouvez toujours enregistrer manuellement.", extra: "Libère ~80 Mo de RAM et réduit l'utilisation GPU pendant le jeu." },
  game_bar:                    { tradeoff: "La Xbox Game Bar (Win+G) est désactivée. Plus d'overlay en jeu.", extra: "Libère ~100 Mo de RAM en jeu." },
  // OneDrive
  onedrive_startup:            { tradeoff: "OneDrive ne démarre plus avec Windows. Vos fichiers ne seront plus synchronisés automatiquement.", extra: "Libère de la RAM et réduit l'activité réseau/disque." },
  // Phone Link
  phone_link:                  { tradeoff: "Lien avec le téléphone ne démarre plus. Les notifications et SMS de votre téléphone ne s'afficheront plus sur le PC.", extra: "" },
};

// Détails pour les services (enrichis à partir de la liste curée)
const _SERVICE_DETAILS = {
  SysMain:     { tradeoff: "Le lancement des applications fréquentes peut être légèrement plus lent (pas de pré-chargement en RAM).", extra: "Réduit significativement l'activité disque en arrière-plan." },
  WSearch:     { tradeoff: "La recherche dans l'Explorateur et Outlook sera plus lente (pas d'index).", extra: "Libère de la RAM et réduit les lectures disque." },
  DiagTrack:   { tradeoff: "Windows n'envoie plus de données de télémétrie avancée. Aucun impact fonctionnel.", extra: "" },
  WerSvc:      { tradeoff: "Les rapports de crash ne seront plus envoyés à Microsoft. Aucun impact utilisateur.", extra: "" },
  MapsBroker:  { tradeoff: "Les cartes hors-connexion ne se mettent plus à jour. Aucun impact si vous n'utilisez pas l'app Cartes.", extra: "" },
  RetailDemo:  { tradeoff: "Service de démo en magasin. Aucun impact sur un PC personnel.", extra: "" },
  XblAuthManager: { tradeoff: "L'authentification Xbox Live est désactivée. Les jeux Xbox/Game Pass peuvent ne plus fonctionner.", extra: "" },
  XblGameSave:  { tradeoff: "La synchronisation cloud des sauvegardes Xbox est désactivée.", extra: "" },
  XboxNetApiSvc: { tradeoff: "Les services réseau Xbox sont désactivés. Le multijoueur Xbox peut être impacté.", extra: "" },
  XboxGipSvc:   { tradeoff: "Le support des manettes et accessoires Xbox est désactivé.", extra: "" },
};

function _showDetailPanel(panelId, title, desc, stats, tradeoff, source) {
  const panel = document.getElementById(panelId);
  if (!panel) return;

  let html = `<div class="detail-panel-title">${_escapeHtml(title)}</div>`;
  html += `<div class="detail-panel-desc">${_escapeHtml(desc)}</div>`;

  for (const [label, value] of stats) {
    const cls = (value === "—" || !value) ? "detail-stat-val none" : "detail-stat-val";
    html += `<div class="detail-stat"><span class="detail-stat-label">${label}</span><span class="${cls}">${value || "—"}</span></div>`;
  }

  if (source) {
    html += `<div class="detail-source">${source}</div>`;
  }
  if (tradeoff) {
    html += `<div class="detail-tradeoff"><strong>Compromis :</strong> ${_escapeHtml(tradeoff)}</div>`;
  }
  panel.innerHTML = html;
}

function _selectTweakDetail(item) {
  // Déselectionne l'ancien
  document.querySelectorAll("#tweaks-list .tweak-row.selected-detail").forEach(r => r.classList.remove("selected-detail"));
  // Sélectionne la nouvelle ligne
  const row = document.querySelector(`#tweaks-list .tweak-row[data-id="${CSS.escape(item.id)}"]`);
  if (row) row.classList.add("selected-detail");

  const detail = _TWEAK_DETAILS[item.id] || {};
  const impact = item.impact || {};
  const ramVal = impact.ram_mb ? `${impact.ram_mb} Mo` : "—";
  const procsVal = impact.processes ? `${impact.processes}` : "—";
  const sourceText = impact.source === "measured"
    ? `Mesuré sur ce PC${impact.measured_at ? " le " + new Date(impact.measured_at).toLocaleDateString("fr-FR") : ""}`
    : impact.ram_mb ? "Estimation moyenne (pas de mesure locale)" : "";

  const present = item.present !== false;
  const compatible = (_detectedWindowsMajor?.() || 11) >= (item.min_windows || 10);

  let status = "";
  if (!present) status = "Absent de ce PC";
  else if (!compatible) status = "Windows 11 uniquement";
  else if (item.active) status = "Activé (état par défaut)";
  else status = "Désactivé par OpenCleaner";

  _showDetailPanel("tweak-detail-panel", item.label, item.desc, [
    ["État", status],
    ["RAM libérée", ramVal],
    ["Processus", procsVal],
    ["Espace disque", "—"],
    ["Catégorie", (item.tags || []).join(", ") || "—"],
  ], detail.tradeoff || "", sourceText + (detail.extra ? "<br>" + detail.extra : ""));
}

function _tweakRow(item) {
  const row = document.createElement("div");
  const compatible = _isTweakCompatible(item);
  const present = item.present !== false;  // absent seulement si explicitement false
  const enabled = compatible && present;
  row.className = "tweak-row" + (!compatible ? " row-incompatible" : "") + (!present ? " row-absent" : "");
  row.dataset.group = item.group;
  row.dataset.tags  = (item.tags || []).join(",");
  row.dataset.id    = item.id;
  const info = document.createElement("div");
  info.className = "tweak-info";
  const lbl = document.createElement("div");
  lbl.className = "tweak-label";
  if (!present) {
    lbl.innerHTML = `${_escapeHtml(item.label)} <span class="incompat-badge" style="background:var(--bg3);color:var(--text-dim)">Absent de ce PC</span>`;
  } else if (!compatible) {
    lbl.innerHTML = `${_escapeHtml(item.label)} <span class="incompat-badge">Windows 11 uniquement</span>`;
  } else {
    lbl.textContent = item.label;
  }
  const desc = document.createElement("div");
  desc.className = "tweak-desc";
  desc.textContent = item.desc;
  info.append(lbl, desc);

  const sw = document.createElement("label");
  sw.className = "sw";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!item.active;
  cb.disabled = !enabled;
  const slider = document.createElement("span");
  slider.className = "slider";
  sw.append(cb, slider);

  cb.addEventListener("change", async () => {
    sw.classList.add("busy");
    row.classList.remove("tweak-error");
    const errEl = row.querySelector(".tweak-err-msg");
    if (errEl) errEl.remove();
    const active = cb.checked;
    try {
      const res = await fetch("/api/windows-tweaks/set", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id, active }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Échec");
      item.active = active;
      // Feedback inline : flash discret de validation sur la ligne
      row.classList.add("tweak-ok");
      setTimeout(() => row.classList.remove("tweak-ok"), 600);
      // Recalcule le chart prédictif (impact estimé)
      if (typeof _renderTweakChart === "function") _renderTweakChart();
    } catch (e) {
      cb.checked = !cb.checked;
      // Feedback inline d'erreur : bordure rouge + message sous la ligne
      row.classList.add("tweak-error");
      const msg = document.createElement("div");
      msg.className = "tweak-err-msg";
      msg.textContent = "Échec : " + e.message;
      row.appendChild(msg);
      setTimeout(() => {
        row.classList.remove("tweak-error");
        msg.remove();
      }, 5000);
    } finally {
      sw.classList.remove("busy");
    }
  });

  // Click sur la ligne → affiche le panneau latéral
  row.addEventListener("click", (e) => {
    // Ne pas interférer avec le switch
    if (e.target.closest(".sw") || e.target.closest("input")) return;
    _selectTweakDetail(item);
  });

  row.append(info, sw);
  return row;
}

// ── Pilotes ───────────────────────────────────────────────────────────────────

let _drivers = [], _driversFilter = "all";

const _DRIVER_CATEGORIES = [
  ["display",   "Affichage"],
  ["media",     "Audio"],
  ["net",       "Réseau"],
  ["disk",      "Stockage"],
  ["usb",       "USB"],
  ["bluetooth", "Bluetooth"],
  ["camera",    "Caméra"],
  ["keyboard",  "Clavier"],
  ["mouse",     "Souris"],
  ["battery",   "Batterie"],
  ["cpu",       "Processeur"],
  ["printer",   "Impression"],
  ["system",    "Système"],
  ["other",     "Autres"],
];

const _DRIVER_ICONS = {
  display:   `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`,
  media:     `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`,
  net:       `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>`,
  disk:      `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
  usb:       `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2v8l4 4-4 4v4"/><path d="M18 2v8l-4 4 4 4v4"/><line x1="12" y1="6" x2="12" y2="18"/></svg>`,
  bluetooth: `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6.5 6.5 17.5 17.5 12 23 12 1 17.5 6.5 6.5 17.5"/></svg>`,
  camera:    `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`,
  keyboard:  `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="12" rx="2"/><line x1="6" y1="10" x2="6.01" y2="10"/><line x1="10" y1="10" x2="10.01" y2="10"/><line x1="14" y1="10" x2="14.01" y2="10"/><line x1="18" y1="10" x2="18.01" y2="10"/><line x1="8" y1="14" x2="16" y2="14"/></svg>`,
  mouse:     `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="7"/><line x1="12" y1="2" x2="12" y2="10"/></svg>`,
  battery:   `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="6" width="18" height="12" rx="2"/><line x1="23" y1="13" x2="23" y2="11"/></svg>`,
  cpu:       `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>`,
  printer:   `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>`,
  system:    `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>`,
  other:     `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="12" y1="8" x2="12" y2="16"/></svg>`,
};

async function startDriversScan() {
  const logEl    = document.getElementById("drivers-log");
  const resultEl = document.getElementById("drivers-results");
  const btnEl    = document.getElementById("btn-scan-drivers");
  logEl.innerHTML = "";
  resultEl.innerHTML = _skeleton(4);
  _btnScan(btnEl, "Analyse…");
  try {
    const res  = await fetch("/api/drivers");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erreur serveur");
    _drivers = data;
    _driversFilter = "all";
    if (!_drivers.length) {
      resultEl.innerHTML = "";
      _logAppend("drivers-log", "Aucun résultat.");
    } else {
      _logAppend("drivers-log", `${_drivers.length} pilote(s) trouvé(s).`);
      _renderDrivers();
    }
  } catch (e) {
    _logAppend("drivers-log", "Erreur : " + e.message);
    resultEl.innerHTML = "";
  }
  _btnReset(btnEl);
}

async function exportDriversReport(fmt = "html") {
  const label = fmt.toUpperCase();
  const target = document.getElementById("btn-scan-drivers");
  const jobId = activityPush(`Export rapport pilotes (${label})`, "run", "génération…", target);
  _logAppend("drivers-log", `Génération du rapport ${label}…`);
  try {
    const res = await fetch("/api/drivers/export?format=" + encodeURIComponent(fmt));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || ("HTTP " + res.status));
    }
    const disp = res.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename="([^"]+)"/);
    const filename = m ? m[1] : "rapport-pilotes." + fmt;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    activityDone(jobId, "téléchargé");
    _logAppend("drivers-log", "Rapport exporté : " + filename);
  } catch (e) {
    activityDone(jobId, "échec", "fail");
    _logAppend("drivers-log", "Erreur export : " + e.message);
    showToast("Export impossible", e.message, "warn");
  }
}

async function scanWindowsUpdateDrivers() {
  const btn = document.getElementById("btn-wu-scan");
  _btnScan(btn, "Recherche…");
  _logAppend("drivers-log", "Interrogation de Windows Update (peut prendre 1 à 2 minutes)…");
  let finalMeta = "terminé";
  let finalStatus = "done";
  try {
    const res = await fetch("/api/drivers/wu-scan", { method: "POST" });
    const data = await res.json();
    if (data.error) {
      finalMeta = "échec"; finalStatus = "fail";
      _logAppend("drivers-log", "Erreur : " + data.error);
      showToast("Windows Update", data.error, "warn");
      return;
    }
    const updates = data.updates || [];
    if (!updates.length) {
      finalMeta = "0 résultat";
      _logAppend("drivers-log", "Aucune mise à jour de pilote proposée par Windows Update.");
      return;
    }
    finalMeta = updates.length + " trouvé" + (updates.length > 1 ? "s" : "");
    _logAppend("drivers-log", updates.length + " mise(s) à jour trouvée(s) :");
    updates.forEach(u => {
      const size = u.sizeBytes ? " · " + fmtBytes(u.sizeBytes) : "";
      const date = u.driverDate ? " · " + u.driverDate : "";
      _logAppend("drivers-log", "  · " + u.title + size + date);
    });
    _logAppend("drivers-log", "Pour installer : ouvrez Paramètres → Windows Update → Options avancées → Mises à jour facultatives.");
  } catch (e) {
    finalMeta = "échec"; finalStatus = "fail";
    _logAppend("drivers-log", "Erreur : " + e.message);
    showToast("Windows Update", e.message, "warn");
  } finally {
    if (btn._activityId != null) {
      activityDone(btn._activityId, finalMeta, finalStatus);
      btn._activityId = null;
    }
    _btnReset(btn);
  }
}

function _renderDrivers() {
  const el = document.getElementById("drivers-results");
  el.innerHTML = "";

  const groups = {};
  _drivers.forEach(d => {
    const k = d.class_key || "other";
    (groups[k] = groups[k] || []).push(d);
  });

  const header = document.createElement("div");
  header.className = "drivers-header";
  const topRow = document.createElement("div");
  topRow.className = "drivers-header-top";
  const totalSpan = document.createElement("span");
  totalSpan.textContent = `${_drivers.length} pilote(s) — ${Object.keys(groups).length} catégorie(s)`;
  topRow.appendChild(totalSpan);

  const pillsRow = document.createElement("div");
  pillsRow.className = "drivers-header-pills";
  const mkPill = (key, label, count) => {
    const pill = document.createElement("span");
    pill.className = "tweak-filter-btn" + (_driversFilter === key ? " active" : "");
    pill.textContent = count != null ? `${label} (${count})` : label;
    pill.addEventListener("click", () => { _driversFilter = key; _renderDrivers(); });
    return pill;
  };
  pillsRow.appendChild(mkPill("all", "Tous", _drivers.length));
  _DRIVER_CATEGORIES.forEach(([k, label]) => {
    if (groups[k]) pillsRow.appendChild(mkPill(k, label, groups[k].length));
  });

  header.append(topRow, pillsRow);
  el.appendChild(header);

  const visibleCats = _driversFilter === "all"
    ? _DRIVER_CATEGORIES.filter(([k]) => groups[k])
    : _DRIVER_CATEGORIES.filter(([k]) => k === _driversFilter && groups[k]);

  const renderItems = [];
  visibleCats.forEach(([k, label]) => {
    const items = [...groups[k]].sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    renderItems.push({ type: "group", key: k, label, count: items.length });
    items.forEach(d => renderItems.push({ type: "row", data: d }));
  });

  _renderBatched(renderItems, (item) => {
    if (item.type === "group") {
      const gh = document.createElement("div");
      gh.className = "driver-group-head";
      const iconWrap = document.createElement("span");
      iconWrap.className = "driver-group-icon";
      iconWrap.innerHTML = _DRIVER_ICONS[item.key] || _DRIVER_ICONS.other;
      const title = document.createElement("span");
      title.className = "driver-group-title";
      title.textContent = item.label;
      const count = document.createElement("span");
      count.className = "driver-group-count";
      count.textContent = `${item.count}`;
      gh.append(iconWrap, title, count);
      return gh;
    }
    const d = item.data;
    const row = document.createElement("div");
    row.className = "driver-row";
    const info = document.createElement("div");
    info.className = "driver-info";
    const name = document.createElement("div");
    name.className = "driver-name";
    name.textContent = d.name;
    const mfr = document.createElement("div");
    mfr.className = "driver-mfr";
    mfr.textContent = d.manufacturer || "—";
    info.append(name, mfr);
    const meta = document.createElement("div");
    meta.className = "driver-meta";
    if (d.version) {
      const ver = document.createElement("span");
      ver.className = "driver-ver";
      ver.textContent = d.version;
      meta.appendChild(ver);
    }
    if (d.date) {
      const dt = document.createElement("span");
      dt.textContent = d.date;
      meta.appendChild(dt);
    }
    row.append(info, meta);
    return row;
  }, el);
}

// ── Points de restauration ────────────────────────────────────────────────────

let _orphanFolders = [], _orphanTotalFmt = "", _orphanSortKey = "size", _orphanSortDir = -1;

async function loadRestorePoints() {
  const el = document.getElementById("rp-results");
  if (!el) return;
  el.innerHTML = _skeleton(3);
  try {
    const res  = await fetch("/api/restore-points");
    const data = await res.json();
    renderRestorePoints(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderRestorePoints(data) {
  const el = document.getElementById("rp-results");

  if (data.requires_admin) {
    el.innerHTML = `<div class="tool-empty">Droits administrateur requis pour accéder aux points de restauration.<br>
      <span style="font-size:12px;color:var(--text-dim)">Relancez l'application en tant qu'administrateur.</span></div>`;
    return;
  }
  if (data.error) {
    el.innerHTML = `<div class="tool-error">${data.error}</div>`;
    return;
  }
  const points = data.points || [];
  if (!points.length) {
    el.innerHTML = `<div class="tool-empty">Aucun résultat.</div>`;
    return;
  }

  el.innerHTML = "";
  el.appendChild(_makeSelHeader(el, {
    countText:   `${points.length} point(s) de restauration`,
    deleteId:    "btn-delete-rp",
    deleteFn:    deleteSelectedRestorePoints,
  }));

  points.forEach((p, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `rp-${i}`;

    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.id = p.id;
    if (i > 0) cb.checked = true;  // Conserver le plus récent non coché par défaut

    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer";

    const descSpan = document.createElement("span"); descSpan.style.cssText = "font-weight:600;color:var(--text)"; descSpan.textContent = p.description;
    const metaSpan = document.createElement("span"); metaSpan.style.color = "var(--text-dim)"; metaSpan.textContent = ` — ${p.date}`;
    if (i === 0) {
      const badge = document.createElement("span"); badge.className = "source-badge"; badge.style.marginLeft = "6px"; badge.textContent = "Plus récent";
      lbl.append(descSpan, metaSpan, " ", badge);
    } else {
      lbl.append(descSpan, metaSpan);
    }
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

function deleteSelectedRestorePoints() {
  requireAdmin(_deleteSelectedRestorePoints);
}

async function _deleteSelectedRestorePoints() {
  const checked = [...document.querySelectorAll("#rp-results input[type=checkbox]:checked:not(.sel-all)")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un élément.", "warn"); return; }

  const ids = checked.map(c => parseInt(c.dataset.id));
  const btn = document.getElementById("btn-delete-rp");
  showConfirm(
    `Supprimer ${ids.length} élément(s) ?`,
    "Ces points seront définitivement supprimés. Assurez-vous de conserver au moins un point récent si vous souhaitez pouvoir restaurer votre système.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/restore-points/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids }),
        });
        const data = await res.json();
        if (data.error) {
          showToast("Erreur", data.error, "warn");
          if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
          return;
        }
        showToast("Suppression terminée", `${data.deleted} supprimé(s).`, "success");
        loadRestorePoints();
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}
