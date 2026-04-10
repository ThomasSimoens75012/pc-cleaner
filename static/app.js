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
    if (totalBytes > 0) {
      statVal.innerHTML = fmtBytes(totalBytes).replace(/ ([^\s]+)$/, '<span class="unit"> $1</span>');
      statVal.classList.remove("dim");
    } else {
      statVal.textContent = "0 Go";
      statVal.classList.add("dim");
    }
  }
  if (statMeta) {
    statMeta.textContent = checked.length === 0
      ? "Aucune tâche sélectionnée"
      : `${checked.length} tâche${checked.length > 1 ? "s" : ""} sélectionnée${checked.length > 1 ? "s" : ""}`;
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

  // Mise à jour de la propriété "X disque(s) détecté(s)" dans le page-header
  const propCount = document.getElementById("prop-disks-count");
  if (propCount) {
    const n = drives.length;
    propCount.textContent = n;
    const wrapper = propCount.closest(".prop");
    if (wrapper) {
      // remplacer le texte après le <strong> pour gérer le pluriel
      const lastNode = wrapper.lastChild;
      if (lastNode && lastNode.nodeType === Node.TEXT_NODE) {
        lastNode.textContent = ` disque${n > 1 ? "s" : ""} détecté${n > 1 ? "s" : ""}`;
      }
    }
  }
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
  if (!data || !data.length) {
    // Pas d'historique : remet les propriétés sur "Jamais"
    const prop = document.getElementById("prop-last-scan");
    if (prop) prop.textContent = "jamais";
    const sb = document.getElementById("sb-last-scan");
    if (sb) sb.textContent = "jamais";
    return;
  }
  const last = data[0];
  const ago  = fmtAgo(new Date(last.date));

  // Stat "Dernier nettoyage"
  const sv = document.getElementById("stat-history");
  const sm = document.getElementById("stat-history-meta");
  if (sv) {
    sv.textContent = ago;
    sv.classList.remove("dim");
  }
  if (sm) sm.textContent = `${last.freed_fmt} libérés`;

  // Propriétés du page-header + sidebar
  const prop = document.getElementById("prop-last-scan");
  if (prop) prop.textContent = ago;
  const sb = document.getElementById("sb-last-scan");
  if (sb) sb.textContent = ago;

  // Ancien hint (caché — info redondante avec les stats/props)
  const el = document.getElementById("history-hint");
  if (el) el.style.display = "none";
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

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(title, sub, type = "success", duration = 4500) {
  document.querySelectorAll(".toast").forEach(t => t.remove());
  const icons = { success: "✓", warn: "!", error: "✕", info: "i" };
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] ?? "✓"}</span>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      ${sub ? `<div class="toast-sub">${sub}</div>` : ""}
    </div>
    <span class="toast-close" onclick="this.parentElement.remove()">×</span>`;
  document.body.appendChild(toast);
  setTimeout(() => {
    if (!toast.parentElement) return;
    toast.style.animation = "toast-in .35s ease reverse forwards";
    setTimeout(() => toast.remove(), 340);
  }, duration);
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
function showCleanPreview(selected) {
  const listEl    = document.getElementById("modal-list");
  const totalEl   = document.getElementById("modal-total-val");
  const overlayEl = document.getElementById("modal-overlay");

  listEl.innerHTML = "";
  let total = 0;

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

  let jobId;
  try {
    const res  = await fetch("/api/clean", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tasks: selected }),
    });
    const data = await res.json();
    if (data.error) { addLog(data.error, "warn"); setCleaningUI(false); cleaning = false; return; }
    jobId = data.job_id;
  } catch (e) {
    addLog("Erreur de connexion au serveur.", "warn"); setCleaningUI(false); cleaning = false; return;
  }

  const es = new EventSource(`/api/stream/${jobId}`);
  es.onmessage = (e) => {
    const item = JSON.parse(e.data);
    if (item.type === "start")    { addLog(item.msg); setProgress(0, item.msg); }
    else if (item.type === "progress") { setProgress(Math.round((item.step / item.total) * 100), item.label + "…"); }
    else if (item.type === "log")  { addLog(item.msg); }
    else if (item.type === "done") {
      setProgress(100, "Terminé");
      addLog(item.msg, "ok");
      es.close();
      onCleanDone(item.freed_bytes || 0);
    } else if (item.type === "error") {
      addLog(item.msg, "warn"); es.close(); setCleaningUI(false); cleaning = false;
    }
  };
  es.onerror = () => { addLog("Connexion SSE interrompue.", "warn"); es.close(); setCleaningUI(false); cleaning = false; };
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

  if (freedBytes > 0) {
    showToast("Nettoyage terminé", fmtBytes(freedBytes) + " libérés sur votre disque", "success");
  } else {
    showToast("Nettoyage terminé", "Le système était déjà propre", "success");
  }

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
