"""
app.py — PC Cleaner (PyQt6 + Flask interne)
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
    list_restore_points, delete_restore_points,
    get_software_updates,
    is_admin,
)

app = Flask(__name__)

JOBS: dict       = {}
JOBS_LOCK        = threading.Lock()
_TASK_MAP        = {t["id"]: t for t in TASKS}
SCHEDULE_FILE    = Path(__file__).parent / "schedule.json"
HISTORY_FILE     = Path(__file__).parent / "history.json"
_scheduler_lock  = threading.Lock()
_scheduler_running = False


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
    return render_template("index.html", tasks_json=tasks_json, is_admin=is_admin())


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
    data = request.get_json(force=True) or {}
    pkg_id = data.get("id", "").strip()
    if not pkg_id:
        return jsonify({"error": "Identifiant de paquet requis."}), 400
    try:
        subprocess.Popen(
            ["winget", "upgrade", "--id", pkg_id,
             "--accept-source-agreements", "--accept-package-agreements"],
            creationflags=0x00000010,  # CREATE_NEW_CONSOLE
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def api_health():
    try:
        return jsonify(get_health_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# Planificateur
# ──────────────────────────────────────────────────────────────────────────────

def _default_schedule():
    return {
        "enabled":  False,
        "interval": "daily",
        "time":     "02:00",
        "tasks":    ["temp", "browser", "recycle", "dns", "thumbnails"],
        "last_run": None,
    }


def _load_schedule():
    if SCHEDULE_FILE.exists():
        try:
            with open(SCHEDULE_FILE) as f:
                data = json.load(f)
                # Fusionne avec les valeurs par défaut (robustesse)
                defaults = _default_schedule()
                defaults.update(data)
                return defaults
        except Exception:
            pass
    return _default_schedule()


def _save_schedule(config):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(config, f, indent=2)


@app.route("/api/schedule", methods=["GET"])
def api_schedule_get():
    config = _load_schedule()
    # Calcule la prochaine exécution pour l'affichage
    config["next_run"] = _compute_next_run(config)
    return jsonify(config)


@app.route("/api/schedule", methods=["POST"])
def api_schedule_post():
    data = request.get_json(force=True) or {}
    config = _load_schedule()
    for key in ["enabled", "interval", "time", "tasks"]:
        if key in data:
            config[key] = data[key]
    _save_schedule(config)
    return jsonify({"ok": True, "next_run": _compute_next_run(config)})


def _compute_next_run(config):
    """Calcule la prochaine exécution planifiée (chaîne ISO 8601)."""
    from datetime import timedelta
    if not config.get("enabled"):
        return None
    try:
        now      = datetime.now()
        h, m     = map(int, config["time"].split(":"))
        interval = config["interval"]
        last     = datetime.fromisoformat(config["last_run"]) if config.get("last_run") else None
        today    = now.replace(hour=h, minute=m, second=0, microsecond=0)

        if interval == "hourly":
            if last is None:
                return today.isoformat()
            next_dt = last.replace(minute=m, second=0, microsecond=0)
            while next_dt <= now:
                next_dt += timedelta(hours=1)
            return next_dt.isoformat()

        elif interval == "daily":
            if today > now:
                return today.isoformat()
            return (today + timedelta(days=1)).isoformat()

        elif interval == "weekly":
            days_ahead = 7 - now.weekday()
            return (today + timedelta(days=days_ahead)).isoformat()

    except Exception:
        pass
    return None


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


# ──────────────────────────────────────────────────────────────────────────────
# Planificateur (thread de fond)
# ──────────────────────────────────────────────────────────────────────────────

def _scheduler_thread():
    global _scheduler_running
    while True:
        time.sleep(60)
        with _scheduler_lock:
            if _scheduler_running:
                continue
        config = _load_schedule()
        if not config.get("enabled"):
            continue
        try:
            now   = datetime.now()
            h, m  = map(int, config["time"].split(":"))
            last  = datetime.fromisoformat(config["last_run"]) if config.get("last_run") else None

            should_run = False
            if config["interval"] == "hourly":
                should_run = last is None or (now - last).total_seconds() >= 3600
            elif config["interval"] == "daily":
                today_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
                should_run = now >= today_time and (last is None or last.date() < now.date())
            elif config["interval"] == "weekly":
                should_run = last is None or (now - last).days >= 7

            if should_run:
                task_ids = [tid for tid in config.get("tasks", []) if tid in _TASK_MAP]
                if task_ids:
                    with _scheduler_lock:
                        _scheduler_running = True
                    job_id = _create_job()
                    t = threading.Thread(target=_run_scheduled_job,
                                         args=(job_id, task_ids, config), daemon=True)
                    t.start()
        except Exception:
            pass


def _run_scheduled_job(job_id, task_ids, config):
    global _scheduler_running
    try:
        _run_job(job_id, task_ids)
        config["last_run"] = datetime.now().isoformat()
        _save_schedule(config)
    finally:
        with _scheduler_lock:
            _scheduler_running = False


# ──────────────────────────────────────────────────────────────────────────────

def _relaunch_as_admin():
    import ctypes, sys
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        " ".join(f'"{Path(a).resolve() if i == 0 else a}"' for i, a in enumerate(sys.argv)),
        None, 1,
    )


def _run_desktop():
    """Démarre Flask en interne et l'affiche dans une fenêtre Qt native (PyQt6 + WebEngine)."""
    import socket as _sock
    import sys as _sys
    import urllib.request as _ur
    import time as _t
    import os as _os

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
    from PyQt6.QtCore import QUrl

    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    threading.Thread(
        target=lambda: app.run(
            debug=False, threaded=True,
            port=port, host='127.0.0.1',
            use_reloader=False
        ),
        daemon=True
    ).start()

    for _ in range(50):
        try:
            _ur.urlopen(f'http://127.0.0.1:{port}/', timeout=0.3)
            break
        except Exception:
            _t.sleep(0.1)

    qt = QApplication.instance() or QApplication(_sys.argv)
    qt.setApplicationName('PC Cleaner')

    view = QWebEngineView()
    view.setWindowTitle('PC Cleaner')
    view.resize(1300, 860)
    view.setMinimumSize(960, 600)

    profile = QWebEngineProfile.defaultProfile()
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

    view.load(QUrl(f'http://127.0.0.1:{port}/'))
    view.show()

    qt.lastWindowClosed.connect(lambda: _os._exit(0))
    _sys.exit(qt.exec())


if __name__ == "__main__":
    threading.Thread(target=_scheduler_thread, daemon=True).start()
    mode = "[Administrateur]" if is_admin() else "[Mode standard]"
    print(f"\n  PC Cleaner {mode}\n")
    _run_desktop()
