/* tools.js — Onglet Outils */

let toolsInitialized = false;

// ── Helpers animation boutons ─────────────────────────────────────────────────

function _btnScan(btn, label = "Analyse…") {
  if (!btn) return;
  btn.dataset.idle = btn.innerHTML;
  btn.innerHTML = `<span class="btn-icon">⟳</span><span>${label}</span>`;
  btn.classList.add("btn-running");
  btn.disabled = true;
}

function _btnDone(btn, label, success = true) {
  if (!btn) return;
  btn.disabled = false;
  btn.classList.remove("btn-running");
  if (success) {
    btn.innerHTML = `<span class="btn-icon">✓</span><span>${label}</span>`;
    btn.classList.add("btn-success");
    setTimeout(() => {
      btn.innerHTML = btn.dataset.idle || label;
      btn.classList.remove("btn-success");
    }, 2500);
  } else {
    btn.innerHTML = `<span class="btn-icon">✕</span><span>${label}</span>`;
    btn.classList.add("btn-error");
    setTimeout(() => {
      btn.innerHTML = btn.dataset.idle || label;
      btn.classList.remove("btn-error");
    }, 2500);
  }
}

function _btnReset(btn) {
  if (!btn) return;
  btn.disabled = false;
  btn.classList.remove("btn-running", "btn-success", "btn-error");
  if (btn.dataset.idle) btn.innerHTML = btn.dataset.idle;
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

function initTools() {
  if (toolsInitialized) return;
  toolsInitialized = true;
  loadStartup();
  loadApps();
  loadExtensions();
  loadRestorePoints();
  loadPrivacy();
  loadHibernation();
  loadWindowsOld();
  _setDefaultInstallerFolder();
}

// ── Démarrage ─────────────────────────────────────────────────────────────────

let _startupEntries = [];

async function loadStartup() {
  const el = document.getElementById("startup-list");
  el.innerHTML = _skeleton(4);
  try {
    const res = await fetch("/api/startup");
    _startupEntries = await res.json();
    renderStartup();
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderStartup() {
  const el = document.getElementById("startup-list");
  if (!_startupEntries.length) {
    el.innerHTML = `<div class="tool-empty">Aucun programme au démarrage détecté.</div>`;
    return;
  }
  const sorted = [..._startupEntries].sort((a, b) => {
    if (a.enabled !== b.enabled) return b.enabled - a.enabled;
    return a.name.localeCompare(b.name);
  });
  el.innerHTML = "";
  sorted.forEach(e => {
    const row = document.createElement("div");
    row.className = "tool-row";

    const info = document.createElement("div");
    info.className = "tool-info";
    const nameD = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = e.name;
    const subD  = document.createElement("div"); subD.className  = "tool-sub";  subD.textContent  = e.command;
    info.append(nameD, subD);

    const meta  = document.createElement("div"); meta.className = "tool-meta";
    const badge = document.createElement("span"); badge.className = "source-badge"; badge.textContent = e.source;
    meta.appendChild(badge);

    const lbl   = document.createElement("label"); lbl.className = "toggle-wrap"; lbl.title = e.enabled ? "Désactiver" : "Activer";
    const track = document.createElement("div");   track.className = "toggle-track" + (e.enabled ? " on" : ""); track.id = `st-${CSS.escape(e.name)}`;
    const thumb = document.createElement("div");   thumb.className = "toggle-thumb";
    track.appendChild(thumb);
    track.addEventListener("click", () => toggleStartup(e, track));
    lbl.appendChild(track);

    row.append(info, meta, lbl);
    el.appendChild(row);
  });
}

async function toggleStartup(entry, trackEl) {
  if (trackEl.dataset.busy) return;
  const newEnabled = !trackEl.classList.contains("on");
  trackEl.dataset.busy = "1";
  trackEl.style.opacity = "0.5";
  try {
    const res = await fetch("/api/startup/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: entry.name, source: entry.source, enabled: newEnabled }),
    });
    if (!res.ok) throw new Error();
    entry.enabled = newEnabled;
    renderStartup();
  } catch (e) {
    showToast("Erreur", "Impossible de modifier le démarrage.", "warn");
    delete trackEl.dataset.busy;
    trackEl.style.opacity = "";
  }
}

// ── Applications installées ───────────────────────────────────────────────────

let allApps    = [];
let appSortKey = "size_kb";
let appSortDir = -1; // -1 desc, 1 asc

async function loadApps() {
  const el = document.getElementById("apps-list");
  el.innerHTML = _skeleton(6, true);
  try {
    const res = await fetch("/api/apps");
    allApps = await res.json();
    renderApps(allApps);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function sortApps(key) {
  if (appSortKey === key) { appSortDir *= -1; }
  else { appSortKey = key; appSortDir = key === "size_kb" ? -1 : 1; }
  const q = document.getElementById("apps-search")?.value.toLowerCase() || "";
  renderApps(q ? allApps.filter(a =>
    a.name.toLowerCase().includes(q) || (a.publisher || "").toLowerCase().includes(q)
  ) : allApps);
}

function renderApps(apps) {
  const el = document.getElementById("apps-list");
  if (!apps.length) {
    el.innerHTML = `<div class="tool-empty">Aucune application trouvée.</div>`;
    return;
  }
  el.innerHTML = "";

  // Tri
  const sorted = [...apps].sort((a, b) => {
    const av = a[appSortKey] ?? "";
    const bv = b[appSortKey] ?? "";
    if (typeof av === "string") return appSortDir * av.localeCompare(bv);
    return appSortDir * (bv - av);
  });

  // Header cliquable
  const cols = [
    { key: "name",      label: "Nom",     style: "flex:1;min-width:0" },
    { key: "publisher", label: "Éditeur", style: "min-width:100px;text-align:right" },
    { key: "size_kb",   label: "Taille",  style: "min-width:80px;text-align:right" },
    { key: "version",   label: "Version", style: "min-width:80px;text-align:right" },
  ];
  const header = document.createElement("div");
  header.className = "tool-row tool-header";
  header.innerHTML = cols.map(c => `
    <div style="${c.style};cursor:pointer;user-select:none" onclick="sortApps('${c.key}')">
      <strong>${c.label}</strong>${appSortKey === c.key ? (appSortDir === -1 ? " ↓" : " ↑") : ""}
    </div>`).join("") + `<div style="width:90px"></div>`;
  el.appendChild(header);

  sorted.forEach(app => {
    const row = document.createElement("div");
    row.className = "tool-row";
    const bigApp = app.size_kb > 500 * 1024;

    const nameDiv = document.createElement("div");
    nameDiv.className = "tool-info";
    nameDiv.innerHTML = `<div class="tool-name">${app.name}${app.size_kb > 1024*1024 ? ' <span style="font-size:10px;color:var(--amber);font-weight:600">●</span>' : ''}</div>`;

    const pubDiv = document.createElement("div");
    pubDiv.className = "tool-meta dim";
    pubDiv.style.cssText = "min-width:100px;text-align:right";
    pubDiv.textContent = app.publisher || "—";

    const sizeDiv = document.createElement("div");
    sizeDiv.className = "tool-meta";
    sizeDiv.style.cssText = `min-width:80px;text-align:right;font-weight:${bigApp ? "700" : "400"};color:${bigApp ? "var(--amber)" : "inherit"}`;
    sizeDiv.textContent = app.size_fmt;

    const verDiv = document.createElement("div");
    verDiv.className = "tool-meta dim";
    verDiv.style.cssText = "min-width:80px;text-align:right";
    verDiv.textContent = app.version || "—";

    const actDiv = document.createElement("div");
    actDiv.style.cssText = "width:90px;text-align:right;flex-shrink:0";

    if (app.uninstall_string) {
      const btn = document.createElement("button");
      btn.className = "btn-uninstall";
      btn.textContent = "Désinstaller";
      btn.addEventListener("click", () => uninstallApp(app.uninstall_string, app.name, btn));
      actDiv.appendChild(btn);
    } else {
      actDiv.innerHTML = `<span class="dim" style="font-size:12px">—</span>`;
    }

    row.append(nameDiv, pubDiv, sizeDiv, verDiv, actDiv);
    el.appendChild(row);
  });

  document.getElementById("apps-count").textContent = `${sorted.length} applications`;
}

function filterApps() {
  const q = document.getElementById("apps-search").value.toLowerCase();
  renderApps(q ? allApps.filter(a =>
    a.name.toLowerCase().includes(q) ||
    (a.publisher || "").toLowerCase().includes(q)
  ) : allApps);
}

async function uninstallApp(uninstallString, name, btn) {
  showConfirm(
    `Désinstaller "${name}" ?`,
    "Windows ouvrira le programme de désinstallation. L'application sera retirée de votre système.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "En cours…"; }
      try {
        const res  = await fetch("/api/apps/uninstall", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ uninstall_string: uninstallString }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          showToast("Erreur", data.error || "Impossible de lancer la désinstallation.", "warn");
          if (btn) { btn.disabled = false; btn.textContent = "Désinstaller"; }
        } else {
          showToast("Désinstallation lancée", `Windows a ouvert le programme de désinstallation de « ${name} »`, "success");
          if (btn) { btn.textContent = "Lancé ✓"; }
        }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Désinstaller"; }
      }
    }
  );
}

// ── Doublons ──────────────────────────────────────────────────────────────────

let duplicateGroups = [];

async function startDuplicateScan() {
  const folder  = document.getElementById("dupe-folder").value.trim();
  const minSize = parseInt(document.getElementById("dupe-minsize").value) || 100;
  if (!folder) { showToast("Dossier requis", "Entrez un chemin de dossier à analyser.", "warn"); return; }

  const logEl    = document.getElementById("dupe-log");
  const resultEl = document.getElementById("dupe-results");
  const btnEl    = document.getElementById("btn-scan-dupes");

  logEl.innerHTML = "";
  resultEl.innerHTML = "";
  duplicateGroups = [];
  _btnScan(btnEl, "Analyse…");

  const dupeLog = (msg) => {
    const d = document.createElement("div");
    d.className = "log-entry";
    d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  };

  try {
    const res  = await fetch("/api/duplicates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, min_size_kb: minSize }),
    });
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    dupeLog(item.msg);
      if (item.type === "result") renderDuplicates(item.groups, item.total_fmt);
      if (item.type === "done") {
        es.close();
        const n = duplicateGroups.length;
        _btnDone(btnEl, n > 0 ? `${n} groupe(s)` : "Aucun doublon ✓", n === 0);
      }
    };
    es.onerror = () => { es.close(); _btnReset(btnEl); };

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
    el.innerHTML = `<div class="tool-empty">Aucun doublon trouvé.</div>`;
    return;
  }

  el.innerHTML = `
    <div class="dupe-header">
      <span>${sorted.length} groupe(s) — ${totalFmt} récupérables</span>
      <button class="btn-ghost" onclick="deleteSelectedDupes()" id="btn-delete-dupes">
        Supprimer la sélection
      </button>
    </div>`;

  sorted.forEach((files, gi) => {
    const group = document.createElement("div");
    group.className = "dupe-group";

    // Index du fichier à conserver (0 par défaut)
    let keptIdx = 0;

    const groupSize = files.reduce((s, f) => s + f.size, 0);
    group.innerHTML = `<div class="dupe-group-title">${files.length} fichiers identiques — ${fmtBytesTools(groupSize)}</div>`;

    const renderRows = () => {
      // Vider les lignes existantes (sans toucher au titre)
      [...group.querySelectorAll(".dupe-row")].forEach(r => r.remove());

      files.forEach((f, fi) => {
        const row  = document.createElement("div");
        row.className = "dupe-row";
        const cbId = `dupe-${gi}-${fi}`;
        const isKept = fi === keptIdx;

        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path;
        cb.checked  = !isKept;
        cb.disabled = isKept;
        cb.title    = isKept ? "Ce fichier sera conservé" : "";

        const lbl = document.createElement("label");
        lbl.htmlFor = cbId; lbl.className = "dupe-path";
        lbl.style.opacity = isKept ? "0.55" : "";

        const sizeSpan = document.createElement("span");
        sizeSpan.className = "dupe-size"; sizeSpan.textContent = f.size_fmt;
        lbl.appendChild(sizeSpan);
        lbl.appendChild(document.createTextNode(" " + f.path));

        if (isKept) {
          const badge = document.createElement("span");
          badge.className = "source-badge";
          badge.style.cssText = "margin-left:6px;color:var(--green);border-color:var(--green)";
          badge.textContent = "↩ conservé";
          lbl.appendChild(badge);
        } else {
          // Bouton "Conserver celui-ci" sur les fichiers supprimables
          const keepBtn = document.createElement("button");
          keepBtn.className   = "btn-ghost";
          keepBtn.textContent = "Conserver celui-ci";
          keepBtn.style.cssText = "font-size:11px;padding:2px 8px;margin-left:8px;flex-shrink:0";
          keepBtn.addEventListener("click", (e) => {
            e.preventDefault();
            keptIdx = fi;
            renderRows();
          });
          row.append(cb, lbl, keepBtn);
          group.appendChild(row);
          return;
        }

        row.append(cb, lbl);
        group.appendChild(row);
      });
    };

    renderRows();
    el.appendChild(group);
  });
}

async function deleteSelectedDupes() {
  const checked = [...document.querySelectorAll("#dupe-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un fichier à supprimer.", "warn"); return; }

  // Vérification de sécurité : s'assurer qu'au moins un fichier de chaque groupe est conservé
  const groups = document.querySelectorAll(".dupe-group");
  for (const group of groups) {
    const allInGroup    = group.querySelectorAll("input[type=checkbox]");
    const checkedInGroup = group.querySelectorAll("input[type=checkbox]:checked");
    if (allInGroup.length > 0 && checkedInGroup.length >= allInGroup.length) {
      showToast("Action impossible", "Vous ne pouvez pas supprimer toutes les copies d'un groupe.", "warn");
      return;
    }
  }

  const paths = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-dupes");
  showConfirm(
    `Supprimer ${paths.length} fichier(s) ?`,
    "Cette action est irréversible. Les fichiers cochés seront définitivement supprimés du disque.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/duplicates/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Erreur serveur");
        const errCount = (data.errors || []).length;
        if (errCount > 0) {
          showToast("Suppression partielle", `${data.freed_fmt} libérés — ${errCount} fichier(s) inaccessible(s).`, "warn");
        } else {
          showToast("Doublons supprimés", data.freed_fmt + " libérés.", "success");
        }
        checked.forEach(c => c.closest(".dupe-row").remove());
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

function fmtBytesTools(b) {
  if (b === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(1) + " " + units[i];
}

// ── Registre ──────────────────────────────────────────────────────────────────

let registryIssues = [];

function startRegistryScan() {
  requireAdmin(_startRegistryScan);
}

async function _startRegistryScan() {
  const logEl    = document.getElementById("reg-log");
  const resultEl = document.getElementById("reg-results");
  const btnEl    = document.getElementById("btn-scan-reg");

  logEl.innerHTML = "";
  resultEl.innerHTML = "";
  registryIssues = [];
  _btnScan(btnEl, "Analyse…");

  const regLog = (msg) => {
    const d = document.createElement("div");
    d.className = "log-entry";
    d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  };

  try {
    const res = await fetch("/api/registry/scan", { method: "POST" });
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    regLog(item.msg);
      if (item.type === "result") renderRegistryIssues(item.issues);
      if (item.type === "done") {
        regLog(item.msg);
        es.close();
        const n = registryIssues.length;
        _btnDone(btnEl, n > 0 ? `${n} problème(s)` : "Registre propre ✓", n === 0);
      }
    };
    es.onerror = () => { es.close(); _btnReset(btnEl); };
  } catch (err) {
    regLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderRegistryIssues(issues) {
  registryIssues = issues;
  const el = document.getElementById("reg-results");

  if (!issues.length) {
    el.innerHTML = `<div class="tool-empty">Aucun problème de registre détecté.</div>`;
    return;
  }

  const categories = {};
  issues.forEach(iss => {
    if (!categories[iss.category]) categories[iss.category] = [];
    categories[iss.category].push(iss);
  });

  el.innerHTML = `
    <div class="reg-header">
      <span>${issues.length} problème(s) détecté(s)</span>
      <button class="btn-ghost" onclick="fixSelectedRegistry()" id="btn-fix-reg" style="font-size:12px;padding:6px 12px">
        Corriger la sélection
      </button>
    </div>`;

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
  const checked = [...document.querySelectorAll("#reg-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins une entrée à corriger.", "warn"); return; }

  const selected = checked.map(c => registryIssues[parseInt(c.dataset.idx)]).filter(Boolean);
  showConfirm(
    `Corriger ${selected.length} entrée(s) du registre ?`,
    "Les références sélectionnées seront supprimées du registre Windows. Cette action est sans risque pour votre système.",
    () => _doFixRegistry(selected, checked)
  );
}

async function _doFixRegistry(selected, checked) {
  const btnEl  = document.getElementById("btn-fix-reg");
  const logEl  = document.getElementById("reg-log");
  _btnScan(btnEl, "Correction…");

  const regLog = (msg) => {
    const d = document.createElement("div");
    d.className = "log-entry";
    d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  };

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
        _btnDone(btnEl, "Corrigé ✓", true);
        showToast("Registre nettoyé", item.msg, "success");
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
    el.innerHTML = `<div class="tool-empty">Aucune extension de navigateur trouvée.</div>`;
    return;
  }

  browsers.forEach(([browser, exts]) => {
    const section = document.createElement("div");
    section.className = "ext-browser-section";

    const icon   = { Chrome: "🟡", Edge: "🔵", Brave: "🦁", Firefox: "🦊" }[browser] || "🌐";
    const title  = document.createElement("div"); title.className = "ext-browser-title";
    const badge  = document.createElement("span"); badge.className = "reg-cat-count"; badge.textContent = exts.length;
    title.append(document.createTextNode(icon + " " + browser + " "), badge);
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

async function loadUpdates() {
  const el    = document.getElementById("updates-container");
  const btnEl = document.getElementById("btn-check-updates");
  el.innerHTML = _skeleton(4, true);
  _btnScan(btnEl, "Vérification…");

  try {
    const res  = await fetch("/api/updates");
    const data = await res.json();
    const count = (data.updates || []).length;
    _btnDone(btnEl, count > 0 ? `${count} mise(s) à jour` : "À jour ✓");
    renderUpdates(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
    _btnReset(btnEl);
  }
}

function renderUpdates(data) {
  const el = document.getElementById("updates-container");
  if (data.error) {
    el.innerHTML = `<div class="tool-error">${data.error}</div>`;
    return;
  }
  const updates = data.updates || [];
  if (!updates.length) {
    el.innerHTML = `<div class="tool-empty">✅ Tous vos logiciels sont à jour.</div>`;
    return;
  }

  el.innerHTML = "";
  const header = document.createElement("div");
  header.className = "reg-header";
  header.innerHTML = `<span>${updates.length} mise(s) à jour disponible(s)</span>`;
  el.appendChild(header);

  updates.forEach(u => {
    const row = document.createElement("div");
    row.className = "tool-row";

    const info = document.createElement("div"); info.className = "tool-info";
    const nameD = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = u.name;
    const subD  = document.createElement("div"); subD.className  = "tool-sub";
    subD.textContent = `v${u.version}  →  v${u.available}`;
    info.append(nameD, subD);

    const src  = document.createElement("div"); src.className = "tool-meta dim"; src.style.fontSize = "11px";
    src.textContent = u.source || "winget";

    const btn  = document.createElement("button"); btn.className = "btn-ghost"; btn.style.fontSize = "12px";
    btn.textContent = "Mettre à jour";
    btn.addEventListener("click", () => installUpdate(u.id, u.name, btn));

    row.append(info, src, btn);
    el.appendChild(row);
  });
}

async function installUpdate(pkgId, name, btn) {
  showConfirm(
    `Mettre à jour « ${name} » ?`,
    "winget va télécharger et installer la nouvelle version. Une fenêtre de terminal s'ouvrira.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "En cours…"; }
      try {
        const res  = await fetch("/api/updates/install", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: pkgId }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          showToast("Erreur", data.error || "Impossible de lancer winget.", "warn");
          if (btn) { btn.disabled = false; btn.textContent = "Mettre à jour"; }
        } else {
          showToast("Mise à jour lancée", `Installation de « ${name} » en cours dans une nouvelle fenêtre.`, "success");
          if (btn) { btn.textContent = "Lancé ✓"; }
        }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Mettre à jour"; }
      }
    }
  );
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
    _btnDone(btnEl, data.length > 0 ? `${data.length} raccourci(s)` : "Aucun cassé ✓", data.length === 0);
    renderShortcuts(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
    _btnReset(btnEl);
  }
}

function renderShortcuts(shortcuts) {
  const el = document.getElementById("shortcuts-results");
  if (!shortcuts.length) {
    el.innerHTML = `<div class="tool-empty">✅ Aucun raccourci cassé détecté.</div>`;
    return;
  }
  el.innerHTML = "";

  const header = document.createElement("div");
  header.className = "reg-header";
  const span = document.createElement("span"); span.textContent = `${shortcuts.length} raccourci(s) cassé(s)`;
  const btnFix = document.createElement("button"); btnFix.className = "btn-ghost";
  btnFix.id = "btn-delete-shortcuts";
  btnFix.style.cssText = "font-size:12px;padding:6px 12px";
  btnFix.textContent = "Supprimer la sélection";
  btnFix.addEventListener("click", deleteSelectedShortcuts);
  header.append(span, btnFix);
  el.appendChild(header);

  shortcuts.forEach((sc, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `sc-${i}`;

    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = sc.path; cb.checked = true;
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer;word-break:break-all";

    const nameSpan = document.createElement("span"); nameSpan.style.cssText = "font-weight:600;color:var(--text)"; nameSpan.textContent = sc.name;
    const locSpan  = document.createElement("span"); locSpan.className = "source-badge"; locSpan.style.marginLeft = "6px"; locSpan.textContent = sc.location;
    const tgtSpan  = document.createElement("span"); tgtSpan.style.color = "var(--text-dim)"; tgtSpan.textContent = sc.target;

    lbl.append(nameSpan, " ", locSpan, document.createElement("br"), tgtSpan);
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

async function deleteSelectedShortcuts() {
  const checked = [...document.querySelectorAll("#shortcuts-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un raccourci.", "warn"); return; }
  const paths = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-shortcuts");
  showConfirm(
    `Supprimer ${paths.length} raccourci(s) ?`,
    "Les fichiers .lnk sélectionnés seront définitivement supprimés. Cela n'affecte pas les applications elles-mêmes.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/shortcuts/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Erreur serveur");
        if (data.deleted === 0) {
          showToast("Aucun raccourci supprimé", "Les fichiers sont peut-être déjà absents ou verrouillés.", "warn");
        } else {
          const msg = data.errors > 0 ? `${data.deleted} supprimé(s), ${data.errors} échec(s).` : `${data.deleted} raccourci(s) supprimé(s).`;
          showToast("Raccourcis supprimés", msg, data.errors > 0 ? "warn" : "success");
        }
        checked.forEach(c => c.closest(".dupe-row").remove());
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

// ── Grands fichiers ───────────────────────────────────────────────────────────

async function startLargeFileScan() {
  const folder  = document.getElementById("lf-folder").value.trim();
  const minGb   = parseFloat(document.getElementById("lf-minsize").value) || 0.5;
  if (!folder) { showToast("Dossier requis", "Entrez un chemin à analyser.", "warn"); return; }

  const logEl    = document.getElementById("lf-log");
  const resultEl = document.getElementById("lf-results");
  const btnEl    = document.getElementById("btn-scan-lf");

  logEl.innerHTML = "";
  resultEl.innerHTML = "";
  _btnScan(btnEl, "Analyse…");

  const lfLog = (msg) => {
    const d = document.createElement("div"); d.className = "log-entry";
    d.innerHTML = `<span class="log-ts">${new Date().toLocaleTimeString("fr-FR")}</span><span class="log-msg">${msg}</span>`;
    logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight;
  };

  try {
    const res = await fetch("/api/largefiles", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, min_size_gb: minGb }),
    });
    if (!res.ok) { const e = await res.json(); showToast("Erreur", e.error, "warn"); btnEl.disabled = false; btnEl.textContent = "Analyser"; return; }
    const { job_id } = await res.json();

    const es = new EventSource(`/api/stream/${job_id}`);
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    lfLog(item.msg);
      if (item.type === "result") renderLargeFiles(item.files, item.total_fmt);
      if (item.type === "done") {
        es.close();
        _btnDone(btnEl, "Analyse terminée", true);
      }
    };
    es.onerror = () => { es.close(); _btnReset(btnEl); };
  } catch (err) {
    lfLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderLargeFiles(files, totalFmt) {
  const el = document.getElementById("lf-results");
  if (!files.length) {
    el.innerHTML = `<div class="tool-empty">Aucun fichier trouvé au-dessus du seuil.</div>`;
    return;
  }
  el.innerHTML = "";

  const header = document.createElement("div"); header.className = "reg-header";
  const span = document.createElement("span"); span.textContent = `${files.length} fichier(s) — ${totalFmt} au total`;
  const btnDel = document.createElement("button"); btnDel.className = "btn-ghost";
  btnDel.id = "btn-delete-lf";
  btnDel.style.cssText = "font-size:12px;padding:6px 12px"; btnDel.textContent = "Supprimer la sélection";
  btnDel.addEventListener("click", deleteSelectedLargeFiles);
  header.append(span, btnDel);
  el.appendChild(header);

  files.forEach((f, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `lf-${i}`;

    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path;
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer;word-break:break-all";

    const sizeSpan = document.createElement("span"); sizeSpan.className = "dupe-size"; sizeSpan.textContent = f.size_fmt;
    const nameSpan = document.createElement("span"); nameSpan.style.cssText = "font-weight:600;color:var(--text)"; nameSpan.textContent = f.name;
    const pathSpan = document.createElement("span"); pathSpan.style.color = "var(--text-dim)"; pathSpan.textContent = f.path;

    lbl.append(sizeSpan, " ", nameSpan, document.createElement("br"), pathSpan);
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

async function deleteSelectedLargeFiles() {
  const checked = [...document.querySelectorAll("#lf-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un fichier.", "warn"); return; }
  const paths = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-lf");
  showConfirm(
    `Supprimer ${paths.length} fichier(s) ?`,
    "Ces fichiers seront définitivement supprimés du disque. Cette action est irréversible.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/duplicates/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Erreur serveur");
        const errCount = (data.errors || []).length;
        if (errCount > 0) {
          showToast("Suppression partielle", `${data.freed_fmt} libérés — ${errCount} fichier(s) inaccessible(s).`, "warn");
        } else {
          showToast("Fichiers supprimés", data.freed_fmt + " libérés.", "success");
        }
        checked.forEach(c => c.closest(".dupe-row").remove());
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

// ── Analyse de l'espace disque ────────────────────────────────────────────────

let _daHistory   = [];   // pile de navigation : [{folder, items, total}]
let _daItems     = [];   // résultats courants
let _daTotal     = 0;
let _daEsActive  = null;

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
        _btnDone(btnEl, "Analyse terminée", true);
        es.close(); _daEsActive = null;
      }
      if (msg.type === "done" && !msg.items) {
        _btnDone(btnEl, "Terminé", true);
        es.close(); _daEsActive = null;
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
      <div class="da-icon">📁</div>
      <div class="da-name" style="flex:1"><div class="skeleton-box" style="width:${35+i*8}%;height:12px"></div></div>
      <div class="da-bar-wrap"><div class="da-bar" style="width:100%"></div></div>
      <div class="da-size"><div class="skeleton-box" style="width:45px;height:11px"></div></div>
    </div>`).join("");
}

function _renderDiskItems(items, total, folder) {
  const el = document.getElementById("da-results");
  if (!items.length) {
    el.innerHTML = `<div class="tool-empty">Dossier vide ou inaccessible.</div>`;
    return;
  }

  const maxSize = items[0]?.size || 1;
  el.innerHTML = "";

  items.forEach(item => {
    const row = document.createElement("div");
    row.className = "da-row" + (item.is_dir ? " da-dir" : "");

    const icon = document.createElement("div");
    icon.className = "da-icon";
    icon.textContent = item.is_dir ? "📁" : "📄";

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

// ── Windows.old ──────────────────────────────────────────────────────────────

async function loadWindowsOld() {
  const el = document.getElementById("windows-old-info");
  if (!el) return;
  try {
    const res  = await fetch("/api/windows-old");
    const data = await res.json();
    renderWindowsOld(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderWindowsOld(data) {
  const el = document.getElementById("windows-old-info");
  if (!data.exists) {
    el.innerHTML = `<div class="tool-empty">✅ Aucun dossier Windows.old détecté sur ce système.</div>`;
    return;
  }
  el.innerHTML = "";
  const row = document.createElement("div"); row.className = "tool-row"; row.style.padding = "14px 16px";
  const info = document.createElement("div"); info.className = "tool-info";
  const nameD = document.createElement("div"); nameD.className = "tool-name"; nameD.textContent = "C:\\Windows.old";
  const subD  = document.createElement("div"); subD.className  = "tool-sub";
  subD.textContent = `${data.size_fmt} — ancienne installation Windows, inutile si votre système fonctionne bien`;
  info.append(nameD, subD);
  const btn = document.createElement("button"); btn.className = "btn-ghost"; btn.style.cssText = "font-size:12px;flex-shrink:0;color:var(--danger,#e05)";
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
  const maxAge  = parseInt(document.getElementById("inst-age").value) || 90;
  if (!folder) { showToast("Dossier requis", "Entrez un dossier à analyser.", "warn"); return; }

  const resultEl = document.getElementById("inst-results");
  const btnEl    = document.getElementById("btn-scan-inst");
  resultEl.innerHTML = _skeleton(4);
  _btnScan(btnEl, "Analyse…");

  try {
    const res = await fetch("/api/old-installers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, max_age_days: maxAge }),
    });
    const data = await res.json();
    if (!res.ok) { showToast("Erreur", data.error, "warn"); _btnReset(btnEl); return; }
    _btnDone(btnEl, data.count > 0 ? `${data.count} fichier(s)` : "Aucun installer ancien ✓", data.count === 0);
    renderInstallers(data);
  } catch (e) {
    resultEl.innerHTML = `<div class="tool-error">Erreur : ${e.message}</div>`;
    _btnReset(btnEl);
  }
}

function renderInstallers(data) {
  const el = document.getElementById("inst-results");
  if (!data.files.length) {
    el.innerHTML = `<div class="tool-empty">✅ Aucun installer de plus de ${document.getElementById("inst-age").value} jours trouvé.</div>`;
    return;
  }
  el.innerHTML = "";

  const header = document.createElement("div"); header.className = "reg-header";
  const span = document.createElement("span"); span.textContent = `${data.count} fichier(s) — ${data.total_fmt}`;
  const btnDel = document.createElement("button"); btnDel.className = "btn-ghost";
  btnDel.id = "btn-delete-inst"; btnDel.style.cssText = "font-size:12px;padding:6px 12px";
  btnDel.textContent = "Supprimer la sélection";
  btnDel.addEventListener("click", deleteSelectedInstallers);
  header.append(span, btnDel);
  el.appendChild(header);

  data.files.forEach((f, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `inst-${i}`;
    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path; cb.checked = true;
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer;word-break:break-all";
    const sizeSpan = document.createElement("span"); sizeSpan.className = "dupe-size"; sizeSpan.textContent = f.size_fmt;
    const nameSpan = document.createElement("span"); nameSpan.style.cssText = "font-weight:600;color:var(--text)"; nameSpan.textContent = f.name;
    const ageSpan  = document.createElement("span"); ageSpan.style.color = "var(--text-dim)"; ageSpan.textContent = `${f.age_days} jours`;
    lbl.append(sizeSpan, " ", nameSpan, " — ", ageSpan);
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

async function deleteSelectedInstallers() {
  const checked = [...document.querySelectorAll("#inst-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un fichier.", "warn"); return; }
  const paths = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-inst");
  showConfirm(
    `Supprimer ${paths.length} fichier(s) ?`,
    "Ces fichiers d'installation seront définitivement supprimés. Vous devrez les re-télécharger si besoin.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/old-installers/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok || (!data.ok && !data.freed)) {
          showToast("Erreur", (data.errors || []).join(", ") || "Suppression impossible.", "warn");
        } else {
          const errCount = (data.errors || []).length;
          showToast("Fichiers supprimés", `${data.freed_fmt} libérés.`, errCount > 0 ? "warn" : "success");
          checked.forEach(c => c.closest(".dupe-row").remove());
        }
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

// ── Confidentialité ──────────────────────────────────────────────────────────

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
  if (!items.length) { el.innerHTML = `<div class="tool-empty">Aucun élément de confidentialité détecté.</div>`; return; }

  el.innerHTML = "";
  const header = document.createElement("div"); header.className = "reg-header";
  const span = document.createElement("span"); span.textContent = `${items.length} catégorie(s)`;
  const btnClean = document.createElement("button"); btnClean.className = "btn-ghost";
  btnClean.id = "btn-clean-privacy";
  btnClean.style.cssText = "font-size:12px;padding:6px 12px"; btnClean.textContent = "Nettoyer la sélection";
  btnClean.addEventListener("click", cleanSelectedPrivacy);
  header.append(span, btnClean);
  el.appendChild(header);

  items.forEach((item, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `priv-${i}`;
    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.id = item.id; cb.checked = item.count > 0;
    if (item.count === 0) { cb.disabled = true; }
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer";
    const nameSpan = document.createElement("span"); nameSpan.style.cssText = "font-weight:600;color:var(--text)"; nameSpan.textContent = item.label;
    const countSpan = document.createElement("span"); countSpan.className = "dupe-size"; countSpan.textContent = item.size_fmt;
    const descSpan  = document.createElement("span"); descSpan.style.color = "var(--text-dim)"; descSpan.textContent = item.desc;
    lbl.append(nameSpan, " ", countSpan, document.createElement("br"), descSpan);
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

async function cleanSelectedPrivacy() {
  const checked = [...document.querySelectorAll("#privacy-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un élément.", "warn"); return; }
  const ids = checked.map(c => c.dataset.id);
  const btn = document.getElementById("btn-clean-privacy");
  showConfirm(
    `Nettoyer ${ids.length} élément(s) de confidentialité ?`,
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
          showToast("Confidentialité nettoyée", `${data.cleaned} élément(s) supprimé(s).`, "success");
          loadPrivacy();
        }
        if (btn) { btn.disabled = false; btn.textContent = "Nettoyer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Nettoyer la sélection"; }
      }
    }
  );
}

// ── Fichier d'hibernation ─────────────────────────────────────────────────────

async function loadHibernation() {
  const el = document.getElementById("hiberfil-info");
  if (!el) return;
  try {
    const res  = await fetch("/api/hibernation");
    const data = await res.json();
    renderHibernation(data);
  } catch (e) {
    el.innerHTML = `<div class="tool-error">Erreur de chargement.</div>`;
  }
}

function renderHibernation(data) {
  const el = document.getElementById("hiberfil-info");
  if (!data.enabled) {
    el.innerHTML = `<div class="tool-empty">✅ L'hibernation est déjà désactivée — aucun fichier hiberfil.sys sur le disque.</div>`;
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
  if (!folder) { showToast("Dossier requis", "Entrez un chemin à analyser.", "warn"); return; }

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
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    addLog(item.msg);
      if (item.type === "result") { _emptyFolders = item.folders; renderEmptyFolders(item.folders); }
      if (item.type === "done") {
        es.close();
        _btnDone(btnEl, item.count > 0 ? `${item.count} trouvé(s)` : "Aucun dossier vide ✓", item.count === 0);
      }
    };
    es.onerror = () => { es.close(); _btnReset(btnEl); };
  } catch (err) {
    addLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderEmptyFolders(folders) {
  const el = document.getElementById("ef-results");
  if (!folders.length) {
    el.innerHTML = `<div class="tool-empty">✅ Aucun dossier vide trouvé.</div>`;
    return;
  }
  el.innerHTML = "";

  const header = document.createElement("div"); header.className = "reg-header";
  const span = document.createElement("span"); span.textContent = `${folders.length} dossier(s) vide(s)`;
  const btnDel = document.createElement("button"); btnDel.className = "btn-ghost";
  btnDel.id = "btn-delete-ef";
  btnDel.style.cssText = "font-size:12px;padding:6px 12px"; btnDel.textContent = "Supprimer la sélection";
  btnDel.addEventListener("click", deleteSelectedEmptyFolders);
  header.append(span, btnDel);
  el.appendChild(header);

  folders.forEach((f, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `ef-${i}`;
    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path; cb.checked = true;
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer;word-break:break-all";
    const nameSpan = document.createElement("span"); nameSpan.style.cssText = "font-weight:600;color:var(--text)"; nameSpan.textContent = f.name;
    const pathSpan = document.createElement("span"); pathSpan.style.color = "var(--text-dim)"; pathSpan.textContent = f.path;
    lbl.append(nameSpan, document.createElement("br"), pathSpan);
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

async function deleteSelectedEmptyFolders() {
  const checked = [...document.querySelectorAll("#ef-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un dossier.", "warn"); return; }
  const paths = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-ef");
  showConfirm(
    `Supprimer ${paths.length} dossier(s) vide(s) ?`,
    "Ces dossiers sont vides et seront définitivement supprimés.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/empty-folders/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          showToast("Erreur", (data.errors || []).join(", ") || "Suppression impossible.", "warn");
        } else {
          showToast("Dossiers supprimés", `${data.deleted} dossier(s) supprimé(s).`, "success");
          checked.forEach(c => c.closest(".dupe-row").remove());
        }
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
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
    es.onmessage = (e) => {
      const item = JSON.parse(e.data);
      if (item.type === "log")    addLog(item.msg);
      if (item.type === "result") renderOrphanFolders(item.folders, item.total_fmt);
      if (item.type === "done") {
        es.close();
        _btnDone(btnEl, item.count > 0 ? `${item.count} orphelin(s)` : "Aucun orphelin ✓", item.count === 0);
      }
    };
    es.onerror = () => { es.close(); _btnReset(btnEl); };
  } catch (err) {
    addLog("Erreur : " + err);
    _btnReset(btnEl);
  }
}

function renderOrphanFolders(folders, totalFmt) {
  const el = document.getElementById("orphan-results");
  if (!folders.length) {
    el.innerHTML = `<div class="tool-empty">✅ Aucun dossier orphelin détecté.</div>`;
    return;
  }
  el.innerHTML = "";

  const header = document.createElement("div"); header.className = "reg-header";
  const span = document.createElement("span"); span.textContent = `${folders.length} dossier(s) orphelin(s) — ${totalFmt || ""} potentiellement récupérables`;
  const btnDel = document.createElement("button"); btnDel.className = "btn-ghost";
  btnDel.id = "btn-delete-orphan";
  btnDel.style.cssText = "font-size:12px;padding:6px 12px"; btnDel.textContent = "Supprimer la sélection";
  btnDel.addEventListener("click", deleteSelectedOrphanFolders);
  header.append(span, btnDel);
  el.appendChild(header);

  folders.forEach((f, i) => {
    const row  = document.createElement("div"); row.className = "dupe-row";
    const cbId = `or-${i}`;
    const cb   = document.createElement("input"); cb.type = "checkbox"; cb.id = cbId; cb.dataset.path = f.path;
    const lbl  = document.createElement("label"); lbl.htmlFor = cbId;
    lbl.style.cssText = "flex:1;font-size:12px;color:var(--text-mid);cursor:pointer;word-break:break-all";
    const sizeSpan = document.createElement("span"); sizeSpan.className = "dupe-size"; sizeSpan.textContent = f.size_fmt;
    const nameSpan = document.createElement("span"); nameSpan.style.cssText = "font-weight:600;color:var(--text)"; nameSpan.textContent = f.name;
    const pathSpan = document.createElement("span"); pathSpan.style.color = "var(--text-dim)"; pathSpan.textContent = f.path;
    lbl.append(sizeSpan, " ", nameSpan, document.createElement("br"), pathSpan);
    row.append(cb, lbl);
    el.appendChild(row);
  });
}

async function deleteSelectedOrphanFolders() {
  const checked = [...document.querySelectorAll("#orphan-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un dossier.", "warn"); return; }
  const paths = checked.map(c => c.dataset.path);
  const btn = document.getElementById("btn-delete-orphan");
  showConfirm(
    `Supprimer ${paths.length} dossier(s) orphelin(s) ?`,
    "Assurez-vous que ces dossiers correspondent bien à des applications désinstallées. Cette action est irréversible.",
    async () => {
      if (btn) { btn.disabled = true; btn.textContent = "Suppression…"; }
      try {
        const res  = await fetch("/api/orphan-folders/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          const errMsg = (data.errors || []).join(", ") || data.error || "Suppression impossible.";
          showToast("Erreur", errMsg, "warn");
        } else {
          const errCount = (data.errors || []).length;
          const msg = errCount > 0
            ? `${data.deleted} supprimé(s), ${errCount} échec(s).`
            : `${data.deleted} dossier(s) supprimé(s).`;
          showToast("Dossiers supprimés", msg, errCount > 0 ? "warn" : "success");
          checked.forEach(c => c.closest(".dupe-row").remove());
        }
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}

// ── Points de restauration ────────────────────────────────────────────────────

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
    el.innerHTML = `<div class="tool-empty">🔒 Droits administrateur requis pour accéder aux points de restauration.<br>
      <span style="font-size:12px;color:var(--text-dim)">Relancez l'application en tant qu'administrateur.</span></div>`;
    return;
  }
  if (data.error) {
    el.innerHTML = `<div class="tool-error">${data.error}</div>`;
    return;
  }
  const points = data.points || [];
  if (!points.length) {
    el.innerHTML = `<div class="tool-empty">Aucun point de restauration trouvé.<br>
      <span style="font-size:12px;color:var(--text-dim)">La protection du système est peut-être désactivée.</span></div>`;
    return;
  }

  el.innerHTML = "";
  const header = document.createElement("div"); header.className = "reg-header";
  const span   = document.createElement("span"); span.textContent = `${points.length} point(s) de restauration`;
  const btnDel = document.createElement("button"); btnDel.className = "btn-ghost";
  btnDel.id = "btn-delete-rp";
  btnDel.style.cssText = "font-size:12px;padding:6px 12px"; btnDel.textContent = "Supprimer la sélection";
  btnDel.addEventListener("click", deleteSelectedRestorePoints);
  header.append(span, btnDel);
  el.appendChild(header);

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
  const checked = [...document.querySelectorAll("#rp-results input[type=checkbox]:checked")];
  if (!checked.length) { showToast("Aucune sélection", "Cochez au moins un point de restauration.", "warn"); return; }

  // Garde-fou : ne pas supprimer tous les points
  const allRp = document.querySelectorAll("#rp-results input[type=checkbox]");
  if (checked.length >= allRp.length) {
    showToast("Action impossible", "Conservez au moins un point de restauration.", "warn");
    return;
  }

  const ids = checked.map(c => parseInt(c.dataset.id));
  const btn = document.getElementById("btn-delete-rp");
  showConfirm(
    `Supprimer ${ids.length} point(s) de restauration ?`,
    "Ces points de restauration seront définitivement supprimés. Vous ne pourrez plus revenir à ces états.",
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
        showToast("Points supprimés", `${data.deleted} point(s) de restauration supprimé(s).`, "success");
        loadRestorePoints();
      } catch (e) {
        showToast("Erreur", e.message, "warn");
        if (btn) { btn.disabled = false; btn.textContent = "Supprimer la sélection"; }
      }
    }
  );
}
