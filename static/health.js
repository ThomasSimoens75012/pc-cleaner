/* health.js — Onglet Santé système */

// Mapping des icônes monochromes (Lucide-style) par id de métrique.
// Remplace les émojis renvoyés par l'API pour cohérence visuelle.
const HEALTH_ICONS = {
  disk:     '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg>',
  temp:     '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>',
  browser:  '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
  startup:  '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  recycle:  '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M7 19h10"/><path d="M9 19V9"/><path d="M15 19V9"/><path d="M5 9h14l-1 12H6z"/><path d="M9 5h6l1 4H8z"/></svg>',
  appcache: '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  smart:    '<svg class="icon icon-lg" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
};

let healthInitialized = false;

function initHealth() {
  if (healthInitialized) return;
  healthInitialized = true;
  if (window._healthCache) {
    renderHealth(window._healthCache);
    updateHealthBadge(window._healthCache);
  }
}

async function loadHealth() {
  const metricsEl = document.getElementById("health-metrics");
  const btnEl     = document.getElementById("btn-scan-health");
  const logEl     = document.getElementById("health-log");
  if (logEl) logEl.innerHTML = "";
  if (metricsEl) metricsEl.innerHTML = "";
  _btnScan(btnEl, "Analyse…");

  const ringFill = document.getElementById("health-ring-fill");
  const scoreVal = document.getElementById("health-score-val");
  const scoreLbl = document.getElementById("health-score-label");
  if (ringFill) ringFill.style.strokeDashoffset = 339;
  if (scoreVal) scoreVal.textContent = "…";
  if (scoreLbl) scoreLbl.textContent = "";

  try {
    const res  = await fetch("/api/health");
    const data = await res.json();
    window._healthCache = data;
    renderHealth(data);
    updateHealthBadge(data);
    _btnReset(btnEl);
  } catch (e) {
    _logAppend("health-log", "Erreur de chargement.");
    _btnReset(btnEl);
  }
}

function updateHealthBadge(data) {
  const pct   = Math.round((data.score / data.max) * 100);
  const color = pct >= 80 ? "var(--green)" : pct >= 50 ? "var(--amber)" : "var(--red)";
  const badge = document.getElementById("health-badge");
  if (badge) { badge.textContent = pct + "%"; badge.style.color = color; }
  // Page-property du score Santé
  const prop = document.getElementById("prop-health-score");
  if (prop) { prop.textContent = pct + "%"; prop.style.color = color; }
}

function renderHealth(data) {
  const pct   = Math.round((data.score / data.max) * 100);
  const color = pct >= 80 ? "var(--green)" : pct >= 50 ? "var(--amber)" : "var(--red)";
  const C     = 2 * Math.PI * 54; // r=54
  const offset = C - (pct / 100) * C;

  const ringFill = document.getElementById("health-ring-fill");
  const scoreVal = document.getElementById("health-score-val");
  const scoreLbl = document.getElementById("health-score-label");

  if (ringFill) {
    ringFill.style.strokeDasharray  = C;
    ringFill.style.strokeDashoffset = offset;
    ringFill.setAttribute("stroke", color);
  }
  if (scoreVal) { scoreVal.textContent = pct + "%"; scoreVal.style.color = color; }
  if (scoreLbl) {
    scoreLbl.textContent = pct >= 80 ? "Excellent" : pct >= 50 ? "À améliorer" : "Attention";
    scoreLbl.style.color = color;
  }

  updateHealthBadge(data);

  // Métriques — triées par score croissant (pires en premier)
  const metricsEl = document.getElementById("health-metrics");
  if (!metricsEl) return;
  metricsEl.innerHTML = "";

  const sorted = [...data.metrics].sort((a, b) => (a.score / a.max) - (b.score / b.max));

  sorted.forEach(m => {
    const mPct      = Math.round((m.score / m.max) * 100);
    const statusCls = m.status === "good" ? "hm-good" : m.status === "warn" ? "hm-warn" : "hm-bad";
    const hasAction = m.action && m.status !== "good";

    const iconHtml = HEALTH_ICONS[m.id] || `<span>${m.icon || ""}</span>`;
    const card = document.createElement("div");
    card.className = "health-metric-card";
    card.innerHTML = `
      <div class="hm-icon">${iconHtml}</div>
      <div class="hm-info">
        <div class="hm-label">${m.label}</div>
        <div class="hm-detail">${m.detail}</div>
      </div>
      <div class="hm-bar-wrap">
        ${hasAction ? `<button class="btn-ghost hm-fix-btn" onclick="quickFixHealth('${m.action}')">Nettoyer</button>` : ""}
        <div class="hm-bar-bg"><div class="hm-bar-fill ${statusCls}" style="width:${mPct}%"></div></div>
        <div class="hm-pct ${statusCls}">${mPct}%</div>
      </div>`;
    metricsEl.appendChild(card);
  });
}

function quickFixHealth(taskId) {
  const task = (typeof TASKS !== "undefined") && TASKS.find(t => t.id === taskId);
  if (!task) return;

  // Basculer vers l'onglet nettoyage
  if (typeof switchTab === "function") switchTab("nettoyage");

  // Cocher uniquement cette tâche
  if (typeof TASKS !== "undefined") {
    TASKS.forEach(t => t.checked = false);
    task.checked = true;
    if (typeof renderTasks === "function") renderTasks();
    if (typeof saveCheckedState === "function") saveCheckedState();
    if (typeof showCleanPreview === "function") showCleanPreview([task]);
  }
}
