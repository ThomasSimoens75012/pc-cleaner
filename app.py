"""
app.py — OpenCleaner (Flask local)
"""

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from cleaner import (
    TASKS, fmt_size, get_disk_info,
    get_startup_entries, set_startup_entry,
    get_installed_apps, launch_uninstaller,
    find_duplicates, delete_duplicate_files,
    scan_registry, fix_registry_issues,
    get_browser_extensions, remove_browser_extension,
    get_health_data,
    scan_shortcuts, delete_shortcuts,
    find_large_files,
    find_empty_folders, delete_empty_folders,
    find_orphan_folders, delete_orphan_folders,
    list_restore_points, delete_restore_points,
    get_software_updates,
    get_privacy_items, clean_privacy_items,
    get_hibernation_info, disable_hibernation,
    scan_disk_level,
    get_windows_old_info, delete_windows_old,
    find_old_installers, delete_installer_files,
    is_admin, is_admin_path,
)


def _reject_if_admin_paths(paths):
    """Retourne une Response 403 si des chemins protégés sont présents et que l'user n'est pas admin. Sinon None."""
    if is_admin():
        return None
    if any(is_admin_path(p) for p in paths):
        return jsonify({
            "error": "Droits administrateur requis pour certains des chemins sélectionnés. Relancez l'application en mode administrateur."
        }), 403
    return None

app = Flask(__name__)

JOBS: dict       = {}
JOBS_LOCK        = threading.Lock()
_TASK_MAP        = {t["id"]: t for t in TASKS}
HISTORY_FILE     = Path(__file__).parent / "history.json"


def _load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_history_entry(freed_bytes):
    history = _load_history()
    history.insert(0, {
        "date":       datetime.now().isoformat(),
        "freed_bytes": freed_bytes,
        "freed_fmt":  fmt_size(freed_bytes),
    })
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[:50], f, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# Routes principales
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    tasks_json = json.dumps([
        {"id": t["id"], "label": t["label"], "desc": t["desc"],
         "admin": t["admin"], "default": t["default"], "group": t["group"]}
        for t in TASKS
    ])
    downloads = str(Path.home() / "Downloads")
    return render_template("index.html", tasks_json=tasks_json,
                           is_admin=is_admin(), downloads_folder=downloads)


@app.route("/api/sizes")
def api_sizes():
    result = {}
    def estimate(task):
        try:
            size = task["estimate_fn"]()
            return task["id"], {"bytes": size, "fmt": fmt_size(size)}
        except Exception:
            return task["id"], {"bytes": 0, "fmt": "—"}
    with ThreadPoolExecutor(max_workers=len(TASKS)) as ex:
        for tid, data in [f.result() for f in as_completed(
            {ex.submit(estimate, t): t for t in TASKS}
        )]:
            result[tid] = data
    return jsonify(result)


@app.route("/api/disk")
def api_disk():
    return jsonify(get_disk_info())


# ──────────────────────────────────────────────────────────────────────────────
# Nettoyage (SSE)
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/clean", methods=["POST"])
def api_clean():
    data     = request.get_json(force=True) or {}
    task_ids = [tid for tid in data.get("tasks", []) if tid in _TASK_MAP]
    if not task_ids:
        return jsonify({"error": "Aucune tâche valide."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_job, args=(job_id, task_ids), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
def api_stream(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        def nf():
            yield f'data: {json.dumps({"type":"error","msg":"Job introuvable."})}\n\n'
        return Response(nf(), mimetype="text/event-stream", status=404)

    def generate():
        q = job["queue"]
        try:
            yield ": connected\n\n"
            while True:
                try:
                    item = q.get(timeout=1)
                    yield f"data: {json.dumps(item)}\n\n"
                    if item.get("type") == "done":
                        break
                except queue.Empty:
                    if job.get("done"):
                        break
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Démarrage
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/startup")
def api_startup():
    try:
        return jsonify(get_startup_entries())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/startup/toggle", methods=["POST"])
def api_startup_toggle():
    data    = request.get_json(force=True) or {}
    name    = data.get("name")
    source  = data.get("source")
    enabled = bool(data.get("enabled"))
    if not name or not source:
        return jsonify({"error": "name et source requis"}), 400
    ok = set_startup_entry(name, source, enabled)
    return jsonify({"ok": ok})


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Applications installées
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/apps")
def api_apps():
    try:
        return jsonify(get_installed_apps())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/apps/uninstall", methods=["POST"])
def api_uninstall():
    data   = request.get_json(force=True) or {}
    string = data.get("uninstall_string")
    if not string:
        return jsonify({"error": "uninstall_string requis"}), 400
    ok = launch_uninstaller(string)
    return jsonify({"ok": ok})


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Doublons
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/duplicates", methods=["POST"])
def api_duplicates():
    data       = request.get_json(force=True) or {}
    folder     = data.get("folder", "")
    min_size   = int(data.get("min_size_kb", 100))
    if not folder or not Path(folder).exists():
        return jsonify({"error": "Dossier invalide ou introuvable."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_duplicates, args=(job_id, folder, min_size), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/duplicates/delete", methods=["POST"])
def api_duplicates_delete():
    data  = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "Aucun fichier sélectionné."}), 400
    rejected = _reject_if_admin_paths(paths)
    if rejected:
        return rejected
    freed, errors = delete_duplicate_files(paths)
    return jsonify({"freed": freed, "freed_fmt": fmt_size(freed), "errors": errors})


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Registre
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/registry/scan", methods=["POST"])
def api_registry_scan():
    job_id = _create_job()
    threading.Thread(target=_run_registry_scan, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/registry/fix", methods=["POST"])
def api_registry_fix():
    data   = request.get_json(force=True) or {}
    issues = data.get("issues", [])
    if not issues:
        return jsonify({"error": "Aucun problème sélectionné."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_registry_fix, args=(job_id, issues), daemon=True).start()
    return jsonify({"job_id": job_id})


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Extensions navigateurs
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/extensions")
def api_extensions():
    try:
        return jsonify(get_browser_extensions())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/extensions/remove", methods=["POST"])
def api_extensions_remove():
    data = request.get_json(force=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"error": "path requis"}), 400
    ok, error = remove_browser_extension(path)
    return jsonify({"ok": ok, "error": error})


# ──────────────────────────────────────────────────────────────────────────────
# Santé système
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/history")
def api_history():
    return jsonify(_load_history())


@app.route("/api/shortcuts")
def api_shortcuts():
    return jsonify(scan_shortcuts())


@app.route("/api/shortcuts/delete", methods=["POST"])
def api_shortcuts_delete():
    data  = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "Aucun chemin fourni."}), 400
    rejected = _reject_if_admin_paths(paths)
    if rejected:
        return rejected
    deleted, errors = delete_shortcuts(paths)
    return jsonify({"deleted": deleted, "errors": errors})


@app.route("/api/largefiles", methods=["POST"])
def api_largefiles():
    data     = request.get_json(force=True) or {}
    folder   = data.get("folder", "")
    min_gb   = float(data.get("min_size_gb", 0.5))
    min_bytes = int(min_gb * 1024 ** 3)
    if not folder or not Path(folder).exists():
        return jsonify({"error": "Dossier invalide ou introuvable."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_largefiles, args=(job_id, folder, min_bytes), daemon=True).start()
    return jsonify({"job_id": job_id})


def _run_largefiles(job_id, folder, min_bytes):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]

    def log(msg):
        q.put({"type": "log", "msg": msg})

    try:
        results = find_large_files(folder, min_bytes, log)
        total = sum(f["size"] for f in results)
        q.put({"type": "result", "files": results,
               "total_fmt": fmt_size(total), "count": len(results)})
        q.put({"type": "done", "msg": f"{len(results)} fichier(s) — {fmt_size(total)} au total.",
               "freed_bytes": 0, "freed_fmt": "—"})
    except Exception as e:
        log(f"Erreur : {e}")
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "0 o"})
    finally:
        job["done"] = True


@app.route("/api/empty-folders", methods=["POST"])
def api_empty_folders():
    data   = request.get_json(force=True) or {}
    folder = data.get("folder", "")
    if not folder or not Path(folder).exists():
        return jsonify({"error": "Dossier invalide ou introuvable."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_empty_folders, args=(job_id, folder), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/empty-folders/delete", methods=["POST"])
def api_empty_folders_delete():
    data  = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "Aucun chemin fourni."}), 400
    rejected = _reject_if_admin_paths(paths)
    if rejected:
        return rejected
    deleted, errors = delete_empty_folders(paths)
    return jsonify({"ok": deleted > 0, "deleted": deleted, "errors": errors})


def _run_empty_folders(job_id, folder):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]
    q.put({"type": "start", "msg": f"Analyse de '{folder}'…"})
    try:
        results = find_empty_folders(folder, log=lambda m: q.put({"type": "log", "msg": m}))
        q.put({"type": "result", "folders": results, "count": len(results)})
        q.put({"type": "done", "msg": f"{len(results)} dossier(s) vide(s) trouvé(s).",
               "count": len(results), "freed_bytes": 0, "freed_fmt": "—"})
    except Exception as e:
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "—"})
    finally:
        job["done"] = True


@app.route("/api/orphan-folders", methods=["POST"])
def api_orphan_folders():
    job_id = _create_job()
    threading.Thread(target=_run_orphan_folders, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/orphan-folders/delete", methods=["POST"])
def api_orphan_folders_delete():
    data  = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "Aucun chemin fourni."}), 400
    deleted, errors = delete_orphan_folders(paths)
    return jsonify({"ok": deleted > 0, "deleted": deleted, "errors": errors})


def _run_orphan_folders(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]
    q.put({"type": "start", "msg": "Analyse des dossiers orphelins…"})
    try:
        results = find_orphan_folders(log=lambda m: q.put({"type": "log", "msg": m}))
        total   = sum(f["size"] for f in results)
        q.put({"type": "result", "folders": results, "count": len(results),
               "total_fmt": fmt_size(total)})
        q.put({"type": "done", "msg": f"{len(results)} dossier(s) orphelin(s) — {fmt_size(total)} récupérables.",
               "count": len(results), "freed_bytes": total, "freed_fmt": fmt_size(total)})
    except Exception as e:
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "—"})
    finally:
        job["done"] = True


@app.route("/api/privacy")
def api_privacy():
    return jsonify(get_privacy_items())


@app.route("/api/privacy/clean", methods=["POST"])
def api_privacy_clean():
    data = request.get_json(force=True) or {}
    ids  = data.get("ids", [])
    if not ids:
        return jsonify({"error": "Aucun élément sélectionné."}), 400
    cleaned, errors = clean_privacy_items(ids)
    return jsonify({"ok": cleaned > 0, "cleaned": cleaned, "errors": errors})


@app.route("/api/hibernation")
def api_hibernation():
    return jsonify(get_hibernation_info())


@app.route("/api/hibernation/disable", methods=["POST"])
def api_hibernation_disable():
    if not is_admin():
        return jsonify({"error": "Droits administrateur requis."}), 403
    ok, err = disable_hibernation()
    return jsonify({"ok": ok, "error": err if not ok else None})


@app.route("/api/disk-analysis", methods=["POST"])
def api_disk_analysis():
    data   = request.get_json(force=True) or {}
    folder = data.get("folder", "C:\\")
    if not Path(folder).exists():
        return jsonify({"error": "Dossier introuvable."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_disk_analysis, args=(job_id, folder), daemon=True).start()
    return jsonify({"job_id": job_id})


def _run_disk_analysis(job_id, folder):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]
    q.put({"type": "start", "msg": f"Analyse de {folder}…"})
    results = []

    def on_item(item):
        results.append(item)
        q.put({"type": "item", "item": item})

    try:
        scan_disk_level(folder, on_item=on_item)
        total = sum(r["size"] for r in results)
        # Recalcule les pourcentages maintenant qu'on a le total
        for r in results:
            r["pct"] = round(r["size"] / total * 100, 1) if total else 0
        q.put({"type": "result", "items": results,
               "total": total, "total_fmt": fmt_size(total), "folder": folder})
        q.put({"type": "done", "msg": f"Analyse terminée — {fmt_size(total)} analysés.",
               "freed_bytes": 0, "freed_fmt": "—"})
    except Exception as e:
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "—"})
    finally:
        job["done"] = True


@app.route("/api/windows-old")
def api_windows_old():
    return jsonify(get_windows_old_info())


@app.route("/api/windows-old/delete", methods=["POST"])
def api_windows_old_delete():
    if not is_admin():
        return jsonify({"error": "Droits administrateur requis."}), 403
    ok, err = delete_windows_old()
    return jsonify({"ok": ok, "error": err if not ok else None})


@app.route("/api/old-installers", methods=["POST"])
def api_old_installers():
    data    = request.get_json(force=True) or {}
    folder  = data.get("folder", "")
    max_age = int(data.get("max_age_days", 90))
    if not folder or not Path(folder).exists():
        return jsonify({"error": "Dossier invalide ou introuvable."}), 400
    results = find_old_installers(folder, max_age)
    total   = sum(f["size"] for f in results)
    return jsonify({"files": results, "count": len(results),
                    "total": total, "total_fmt": fmt_size(total)})


@app.route("/api/old-installers/delete", methods=["POST"])
def api_old_installers_delete():
    data  = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "Aucun fichier sélectionné."}), 400
    rejected = _reject_if_admin_paths(paths)
    if rejected:
        return rejected
    freed, errors = delete_installer_files(paths)
    return jsonify({"ok": freed > 0 or not errors, "freed": freed,
                    "freed_fmt": fmt_size(freed), "errors": errors})


@app.route("/api/relaunch-admin", methods=["POST"])
def api_relaunch_admin():
    if is_admin():
        return jsonify({"ok": True, "already": True})
    _relaunch_as_admin()
    threading.Timer(0.6, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


@app.route("/api/restore-points")
def api_restore_points():
    return jsonify(list_restore_points())


@app.route("/api/restore-points/delete", methods=["POST"])
def api_restore_points_delete():
    data = request.get_json(force=True) or {}
    ids  = data.get("ids", [])
    if not ids:
        return jsonify({"error": "Aucun identifiant fourni."}), 400
    deleted, error = delete_restore_points(ids)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"deleted": deleted})


@app.route("/api/updates")
def api_updates():
    return jsonify(get_software_updates())


@app.route("/api/updates/install", methods=["POST"])
def api_updates_install():
    data        = request.get_json(force=True) or {}
    pkg_id      = data.get("id", "").strip()
    old_version = data.get("old_version", "").strip()
    if not pkg_id:
        return jsonify({"error": "Identifiant de paquet requis."}), 400
    job_id = _create_job()
    threading.Thread(target=_run_update_install,
                     args=(job_id, pkg_id, old_version), daemon=True).start()
    return jsonify({"job_id": job_id})


def _run_update_install(job_id, pkg_id, old_version):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q   = job["queue"]
    enc = "utf-8"

    def log(msg):
        q.put({"type": "log", "msg": msg})

    def run_winget(args):
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        for raw in proc.stdout:
            text = raw.decode(enc, errors="replace").rstrip()
            if text:
                log(text)
        proc.wait()
        return proc.returncode

    try:
        log("Lancement de la mise à jour…")
        code = run_winget([
            "winget", "upgrade", "--id", pkg_id,
            "--accept-source-agreements", "--accept-package-agreements",
        ])
        if code == 0:
            q.put({"type": "done", "ok": True, "rollback": False,
                   "msg": "Mise à jour installée avec succès."})
        else:
            if old_version and old_version not in ("?", "Unknown", "Inconnu"):
                log(f"Échec (code {code}). Restauration de la version {old_version}…")
                rb = run_winget([
                    "winget", "install", "--id", pkg_id,
                    "--version", old_version,
                    "--accept-source-agreements", "--accept-package-agreements",
                ])
                if rb == 0:
                    q.put({"type": "done", "ok": False, "rollback": True,
                           "msg": f"Mise à jour échouée — version {old_version} restaurée."})
                else:
                    q.put({"type": "done", "ok": False, "rollback": False,
                           "msg": "Mise à jour échouée et restauration impossible. Réinstallez manuellement."})
            else:
                q.put({"type": "done", "ok": False, "rollback": False,
                       "msg": f"Mise à jour échouée (code {code})."})
    except FileNotFoundError:
        q.put({"type": "done", "ok": False, "rollback": False,
               "msg": "winget introuvable sur ce système."})
    except Exception as e:
        q.put({"type": "done", "ok": False, "rollback": False, "msg": str(e)})
    finally:
        job["done"] = True


@app.route("/api/browse-folder")
def api_browse_folder():
    try:
        import tkinter as _tk
        from tkinter import filedialog as _fd
        root = _tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = _fd.askdirectory(title="Choisir un dossier")
        root.destroy()
        return jsonify({"folder": path.replace("/", "\\") if path else ""})
    except Exception as e:
        return jsonify({"folder": "", "error": str(e)})


@app.route("/api/health")
def api_health():
    try:
        return jsonify(get_health_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# Workers
# ──────────────────────────────────────────────────────────────────────────────

def _create_job():
    job_id = str(uuid.uuid4())
    job = {"queue": queue.Queue(), "done": False}
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_cleanup_job, args=(job_id,), daemon=True).start()
    return job_id


def _cleanup_job(job_id, delay=300):
    time.sleep(delay)
    with JOBS_LOCK:
        JOBS.pop(job_id, None)


def _run_job(job_id, task_ids):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]
    total_freed = 0

    def log(msg):
        q.put({"type": "log", "msg": msg})

    selected = [_TASK_MAP[tid] for tid in task_ids if tid in _TASK_MAP]
    q.put({"type": "start", "msg": f"Démarrage — {len(selected)} tâche(s)."})

    for i, task in enumerate(selected):
        q.put({"type": "progress", "step": i, "total": len(selected), "label": task["label"]})
        try:
            freed = task["fn"](log)
            total_freed += freed or 0
        except Exception as e:
            log(f"  Erreur dans '{task['label']}' : {e}")

    if total_freed > 0:
        _save_history_entry(total_freed)
    q.put({"type": "done", "msg": f"Terminé — {fmt_size(total_freed)} libérés.",
           "freed_bytes": total_freed, "freed_fmt": fmt_size(total_freed)})
    job["done"] = True


def _run_registry_scan(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]

    def log(msg):
        q.put({"type": "log", "msg": msg})

    q.put({"type": "start", "msg": "Analyse du registre en cours…"})
    try:
        issues = scan_registry(log)
        q.put({"type": "result", "issues": issues, "count": len(issues)})
        q.put({"type": "done", "msg": f"Analyse terminée — {len(issues)} problème(s) trouvé(s).",
               "freed_bytes": 0, "freed_fmt": "—"})
    except Exception as e:
        log(f"Erreur : {e}")
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "0 o"})
    finally:
        job["done"] = True


def _run_registry_fix(job_id, issues):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]

    def log(msg):
        q.put({"type": "log", "msg": msg})

    q.put({"type": "start", "msg": f"Correction de {len(issues)} entrée(s)…"})
    try:
        fixed, errors = fix_registry_issues(issues, log)
        q.put({"type": "done", "msg": f"Terminé — {fixed} corrigé(s), {len(errors)} erreur(s).",
               "freed_bytes": 0, "freed_fmt": "—"})
    except Exception as e:
        log(f"Erreur : {e}")
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "0 o"})
    finally:
        job["done"] = True


def _run_duplicates(job_id, folder, min_size_kb):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return
    q = job["queue"]

    def log(msg):
        q.put({"type": "log", "msg": msg})

    q.put({"type": "start", "msg": f"Analyse de '{folder}'..."})
    try:
        groups = find_duplicates(folder, min_size_kb, log)
        total_wasted = sum(
            sum(f["size"] for f in files[1:])
            for files in groups.values()
        )
        q.put({
            "type":         "result",
            "groups":       list(groups.values()),
            "total_wasted": total_wasted,
            "total_fmt":    fmt_size(total_wasted),
        })
        q.put({"type": "done", "msg": f"Analyse terminée — {fmt_size(total_wasted)} récupérables.",
               "freed_bytes": total_wasted, "freed_fmt": fmt_size(total_wasted)})
    except Exception as e:
        log(f"Erreur : {e}")
        q.put({"type": "done", "msg": f"Erreur : {e}", "freed_bytes": 0, "freed_fmt": "0 o"})
    finally:
        job["done"] = True


def _relaunch_as_admin():
    import ctypes, sys
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        " ".join(f'"{Path(a).resolve() if i == 0 else a}"' for i, a in enumerate(sys.argv)),
        None, 1,
    )


@app.route("/api/quit", methods=["POST"])
def api_quit():
    """Arrête l'application proprement."""
    import threading as _th
    _th.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


def _run():
    """Démarre Flask et affiche l'URL dans la console."""
    import socket as _sock

    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    url = f"http://127.0.0.1:{port}/"
    mode = "[Administrateur]" if is_admin() else "[Mode standard]"

    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║                                              ║")
    print(f"  ║   OpenCleaner {mode:<30} ║")
    print("  ║                                              ║")
    print(f"  ║   Ouvrez votre navigateur sur :              ║")
    print(f"  ║   {url:<42} ║")
    print("  ║                                              ║")
    print("  ║   Pour arrêter : cliquez sur « Quitter »     ║")
    print("  ║   dans l'application, ou Ctrl+C ici.         ║")
    print("  ║                                              ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()

    app.run(host='127.0.0.1', port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    _run()
