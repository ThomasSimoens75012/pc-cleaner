"""
app.py — OpenCleaner (Flask local)
"""

import json
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from cleaner import (
    TASKS, fmt_size, get_disk_info,
    get_installed_apps, launch_uninstaller,
    remove_uninstall_registry_entry, find_app_residuals,
    find_duplicates, delete_duplicate_files,
    find_duplicate_folders, delete_duplicate_folders,
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
    scan_windows_installer_cache, launch_disk_cleanup,
    is_admin, is_admin_path,
    get_drivers, export_drivers_report, scan_windows_update_drivers,
    get_windows_tweaks, set_windows_tweak, get_tweak_presets,
    list_uwp_apps, remove_uwp_apps,
    get_services_state, set_service_enabled,
    get_scheduled_tasks_state, set_scheduled_task_enabled,
    list_repair_actions, run_repair_action, run_repair_action_stream,
    run_self_check, export_tweaks_reg,
    get_autorun_entries, set_autorun_enabled,
    export_config_snapshot, import_config_snapshot,
    get_gaming_mode_state, set_gaming_mode,
    get_update_center,
    get_browser_data_breakdown, clean_browser_data,
    generate_global_report,
    send_to_recycle_bin, open_recycle_bin, get_last_cleanup_info,
    list_recycle_sessions, restore_recycle_session, delete_recycle_session,
)


def _log_delete(op, summary, errors):
    app.logger.info("%s — %s, %d erreur(s)", op, summary, len(errors or []))
    for e in (errors or []):
        app.logger.warning("  échec: %s", e)


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

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_handler = RotatingFileHandler(_LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
app.logger.addHandler(_handler)
app.logger.setLevel(logging.INFO)

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


def _save_history_entry(freed_bytes, kind="clean", label="Nettoyage", tasks=None, details=None):
    """Sauvegarde une entrée dans history.json.

    kind : clean | uninstall | delete | repair | tweak
    label : libellé lisible
    tasks : liste optionnelle de task IDs (pour le nettoyage principal)
    details : dict libre (ex: nombre de fichiers supprimés, process killed, etc.)
    """
    history = _load_history()
    entry = {
        "date":        datetime.now().isoformat(),
        "kind":        kind,
        "label":       label,
        "freed_bytes": int(freed_bytes or 0),
        "freed_fmt":   fmt_size(freed_bytes or 0),
    }
    if tasks:
        entry["tasks"] = tasks
    if details:
        entry["details"] = details
    history.insert(0, entry)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[:100], f, indent=2)


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


@app.route("/api/windows-tweaks/export-reg")
def api_windows_tweaks_export_reg():
    try:
        report = export_tweaks_reg()
    except Exception as e:
        app.logger.exception("export-reg error")
        return jsonify({"error": str(e)}), 500
    # UTF-16 LE BOM is what .reg files use — encode accordingly
    content_bytes = ("\ufeff" + report["content"]).encode("utf-16-le")
    resp = Response(content_bytes, mimetype="application/x-registry; charset=utf-16")
    resp.headers["Content-Disposition"] = f'attachment; filename="{report["filename"]}"'
    return resp


@app.route("/api/self-check")
def api_self_check():
    try:
        return jsonify(run_self_check())
    except Exception as e:
        app.logger.exception("self-check error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/autoruns")
def api_autoruns():
    try:
        return jsonify(get_autorun_entries())
    except Exception as e:
        app.logger.exception("autoruns error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/autoruns/set", methods=["POST"])
def api_autoruns_set():
    data = request.get_json(force=True) or {}
    entry_id = data.get("id")
    enabled = bool(data.get("enabled"))
    if not entry_id:
        return jsonify({"ok": False, "error": "id manquant"}), 400
    if not is_admin() and entry_id.startswith("reg:HKLM"):
        return jsonify({"ok": False, "error": "Droits administrateur requis pour HKLM"}), 403
    ok, err = set_autorun_enabled(entry_id, enabled)
    if not ok:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify({"ok": True})


@app.route("/api/config/export")
def api_config_export():
    try:
        snapshot = export_config_snapshot()
    except Exception as e:
        app.logger.exception("config export error")
        return jsonify({"error": str(e)}), 500
    content = json.dumps(snapshot, indent=2, ensure_ascii=False)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    resp = Response(content, mimetype="application/json; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="opencleaner-config-{stamp}.json"'
    return resp


@app.route("/api/config/import", methods=["POST"])
def api_config_import():
    data = request.get_json(force=True) or {}
    snapshot = data.get("snapshot")
    sections = data.get("sections")
    if not isinstance(snapshot, dict):
        return jsonify({"error": "snapshot manquant ou invalide"}), 400
    try:
        result = import_config_snapshot(snapshot, sections=sections)
    except Exception as e:
        app.logger.exception("config import error")
        return jsonify({"error": str(e)}), 500
    _save_history_entry(
        0,
        kind="restore",
        label="Restauration configuration",
        details={
            "applied": result.get("applied", 0),
            "skipped": result.get("skipped", 0),
            "errors":  len(result.get("errors", [])),
        },
    )
    return jsonify(result)


@app.route("/api/recycle-sessions")
def api_recycle_sessions():
    try:
        return jsonify(list_recycle_sessions())
    except Exception as e:
        app.logger.exception("recycle-sessions error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/recycle-sessions/restore", methods=["POST"])
def api_recycle_sessions_restore():
    data = request.get_json(force=True) or {}
    sid = (data.get("id") or "").strip()
    if not sid:
        return jsonify({"error": "id manquant"}), 400
    try:
        result = restore_recycle_session(sid)
    except Exception as e:
        app.logger.exception("recycle-sessions restore error")
        return jsonify({"error": str(e)}), 500
    _save_history_entry(
        0,
        kind="restore",
        label="Restauration corbeille",
        details={
            "restored":  result.get("restored", 0),
            "not_found": result.get("not_found", 0),
            "errors":    len(result.get("errors", [])),
        },
    )
    return jsonify(result)


@app.route("/api/recycle-sessions/<sid>", methods=["DELETE"])
def api_recycle_sessions_delete(sid):
    ok, err = delete_recycle_session(sid)
    return jsonify({"ok": ok, "error": err})


@app.route("/api/undo/last")
def api_undo_last():
    info = get_last_cleanup_info()
    return jsonify({"last": info, "has_undo": info is not None})


@app.route("/api/undo/open-recycle-bin", methods=["POST"])
def api_open_recycle_bin():
    ok, err = open_recycle_bin()
    return jsonify({"ok": ok, "error": err})


@app.route("/api/recycle-bin/send", methods=["POST"])
def api_recycle_bin_send():
    data = request.get_json(force=True) or {}
    paths = data.get("paths") or []
    if not paths:
        return jsonify({"error": "Aucun chemin"}), 400
    reject = _reject_if_admin_paths(paths)
    if reject is not None:
        return reject
    try:
        result = send_to_recycle_bin(paths)
    except Exception as e:
        app.logger.exception("recycle-bin/send error")
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/api/report")
def api_report():
    try:
        report = generate_global_report()
    except Exception as e:
        app.logger.exception("report error")
        return jsonify({"error": str(e)}), 500
    resp = Response(report["html"], mimetype="text/html; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{report["filename"]}"'
    return resp


@app.route("/api/browser-data")
def api_browser_data():
    try:
        return jsonify(get_browser_data_breakdown())
    except Exception as e:
        app.logger.exception("browser-data error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/browser-data/clean", methods=["POST"])
def api_browser_data_clean():
    data = request.get_json(force=True) or {}
    selections = data.get("selections") or []
    if not selections:
        return jsonify({"error": "Aucune sélection"}), 400
    try:
        result = clean_browser_data(selections)
    except Exception as e:
        app.logger.exception("browser-data clean error")
        return jsonify({"error": str(e)}), 500
    _save_history_entry(
        result.get("deleted_bytes", 0),
        kind="clean",
        label="Nettoyage navigateurs (granulaire)",
        details={
            "profiles": len(selections),
            "errors":   len(result.get("errors", [])),
        },
    )
    return jsonify(result)


@app.route("/api/update-center")
def api_update_center():
    try:
        return jsonify(get_update_center())
    except Exception as e:
        app.logger.exception("update-center error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/gaming-mode")
def api_gaming_mode_get():
    return jsonify(get_gaming_mode_state())


@app.route("/api/gaming-mode", methods=["POST"])
def api_gaming_mode_set():
    if not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis"}), 403
    data = request.get_json(force=True) or {}
    try:
        result = set_gaming_mode(bool(data.get("enabled")))
    except Exception as e:
        app.logger.exception("gaming mode error")
        return jsonify({"ok": False, "error": str(e)}), 500
    if result.get("ok"):
        _save_history_entry(
            0,
            kind="gaming",
            label=("Mode gaming activé" if data.get("enabled") else "Mode gaming désactivé"),
            details={
                "applied": result.get("applied"),
                "restored": result.get("restored"),
                "errors": len(result.get("errors", [])),
            },
        )
    return jsonify(result)


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.svg"))


@app.route("/api/open-settings", methods=["POST"])
def api_open_settings():
    data = request.get_json(force=True) or {}
    uri = (data.get("uri") or "").strip()
    if not uri.startswith("ms-settings:"):
        return jsonify({"ok": False, "error": "URI invalide"}), 400
    try:
        os.startfile(uri)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
# Outils — Applications installées
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/apps")
def api_apps():
    deep = request.args.get("deep", "0") == "1"
    try:
        return jsonify(get_installed_apps(deep=deep))
    except Exception as e:
        app.logger.exception("apps error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/apps/uninstall", methods=["POST"])
def api_uninstall():
    data = request.get_json(force=True) or {}
    string      = data.get("uninstall_string") or ""
    silent      = bool(data.get("silent"))
    winget_id   = data.get("winget_id") or ""
    quiet_unins = data.get("quiet_uninstall") or ""
    if not string and not winget_id:
        return jsonify({"error": "uninstall_string ou winget_id requis"}), 400
    ok = launch_uninstaller(string, silent=silent, winget_id=winget_id, quiet_uninstall=quiet_unins)
    return jsonify({"ok": ok})


@app.route("/api/apps/remove-entry", methods=["POST"])
def api_apps_remove_entry():
    if not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis pour modifier HKLM"}), 403
    data = request.get_json(force=True) or {}
    reg_hive = data.get("reg_hive") or ""
    reg_path = data.get("reg_path") or ""
    if reg_hive not in ("HKLM", "HKCU") or not reg_path:
        return jsonify({"ok": False, "error": "reg_hive/reg_path invalides"}), 400
    ok, err = remove_uninstall_registry_entry(reg_hive, reg_path)
    return jsonify({"ok": ok, "error": err})


@app.route("/api/apps/residuals", methods=["POST"])
def api_apps_residuals():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    install_loc = data.get("install_location") or ""
    if not name:
        return jsonify({"error": "name requis"}), 400
    try:
        items = find_app_residuals(name, install_loc)
    except Exception as e:
        app.logger.exception("residuals error")
        return jsonify({"error": str(e)}), 500
    total = sum(r["size"] for r in items)
    return jsonify({"items": items, "total": total, "total_fmt": fmt_size(total)})


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
    _log_delete("duplicates/delete", f"{len(paths)} fichier(s), {fmt_size(freed)} libérés", errors)
    _save_history_entry(freed, kind="delete", label="Fichiers dupliqués", details={"count": len(paths), "errors": len(errors or [])})
    return jsonify({"freed": freed, "freed_fmt": fmt_size(freed), "errors": errors})


@app.route("/api/duplicate-folders", methods=["POST"])
def api_duplicate_folders():
    data   = request.get_json(force=True) or {}
    folder = (data.get("folder") or "").strip()
    if not folder or not Path(folder).is_dir():
        return jsonify({"error": "Dossier invalide ou introuvable."}), 400
    try:
        return jsonify(find_duplicate_folders(folder))
    except Exception as e:
        app.logger.exception("duplicate-folders error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/duplicate-folders/delete", methods=["POST"])
def api_duplicate_folders_delete():
    data  = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"error": "Aucun dossier sélectionné."}), 400
    rejected = _reject_if_admin_paths(paths)
    if rejected:
        return rejected
    freed, errors = delete_duplicate_folders(paths)
    _log_delete("duplicate-folders/delete", f"{len(paths)} dossier(s), {fmt_size(freed)} libérés", errors)
    _save_history_entry(freed, kind="delete", label="Dossiers dupliqués", details={"count": len(paths), "errors": len(errors or [])})
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
    r = send_to_recycle_bin(paths)
    deleted, errors = r["moved"], r["errors"]
    _log_delete("shortcuts/delete", f"{deleted} supprimé(s)", errors)
    _save_history_entry(0, kind="delete", label="Raccourcis cassés", details={"count": deleted, "errors": len(errors or [])})
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
        app.logger.exception("largefiles scan error")
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
    r = send_to_recycle_bin(paths)
    deleted, errors = r["moved"], r["errors"]
    _log_delete("empty-folders/delete", f"{deleted} supprimé(s)", errors)
    _save_history_entry(0, kind="delete", label="Dossiers vides", details={"count": deleted, "errors": len(errors or [])})
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
        app.logger.exception("empty-folders scan error")
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
    r = send_to_recycle_bin(paths)
    deleted, errors = r["moved"], r["errors"]
    _log_delete("orphan-folders/delete", f"{deleted} supprimé(s)", errors)
    _save_history_entry(0, kind="delete", label="Dossiers orphelins", details={"count": deleted, "errors": len(errors or [])})
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
        app.logger.exception("orphan-folders scan error")
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
    _log_delete("old-installers/delete", f"{len(paths)} fichier(s), {fmt_size(freed)} libérés", errors)
    _save_history_entry(freed, kind="delete", label="Anciens installers", details={"count": len(paths), "errors": len(errors or [])})
    return jsonify({"ok": freed > 0 or not errors, "freed": freed,
                    "freed_fmt": fmt_size(freed), "errors": errors})


@app.route("/api/windows-installer-cache")
def api_windows_installer_cache():
    try:
        return jsonify(scan_windows_installer_cache())
    except Exception as e:
        app.logger.exception("windows-installer-cache error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/disk-cleanup", methods=["POST"])
def api_disk_cleanup():
    ok, err = launch_disk_cleanup()
    return jsonify({"ok": ok, "error": err})


@app.route("/api/windows-tweaks")
def api_windows_tweaks():
    try:
        return jsonify(get_windows_tweaks())
    except Exception as e:
        app.logger.exception("windows-tweaks error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/windows-tweaks/set", methods=["POST"])
def api_windows_tweaks_set():
    data = request.get_json(force=True) or {}
    tweak_id = data.get("id")
    active   = bool(data.get("active"))
    if not tweak_id:
        return jsonify({"error": "id manquant"}), 400
    ok, err = set_windows_tweak(tweak_id, active)
    if ok:
        app.logger.info("windows-tweaks/set — %s → %s", tweak_id, "on" if active else "off")
        return jsonify({"ok": True})
    app.logger.warning("windows-tweaks/set — %s failed: %s", tweak_id, err)
    return jsonify({"ok": False, "error": err}), 500


@app.route("/api/windows-tweaks/presets")
def api_windows_tweaks_presets():
    try:
        return jsonify({"presets": get_tweak_presets()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/windows-tweaks/set-batch", methods=["POST"])
def api_windows_tweaks_set_batch():
    data    = request.get_json(force=True) or {}
    changes = data.get("changes") or []
    results = []
    ok_count, fail_count = 0, 0
    for change in changes:
        tid = change.get("id")
        active = bool(change.get("active"))
        if not tid:
            results.append({"id": tid, "ok": False, "error": "id manquant"})
            fail_count += 1
            continue
        ok, err = set_windows_tweak(tid, active)
        results.append({"id": tid, "ok": ok, "error": err, "active": active})
        if ok:
            ok_count += 1
        else:
            fail_count += 1
    app.logger.info("windows-tweaks/set-batch — %d ok, %d échec(s)", ok_count, fail_count)
    return jsonify({"ok": fail_count == 0, "results": results, "ok_count": ok_count, "fail_count": fail_count})


@app.route("/api/services")
def api_services_list():
    try:
        return jsonify({"services": get_services_state(), "is_admin": is_admin()})
    except Exception as e:
        app.logger.exception("services list error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/services/set", methods=["POST"])
def api_services_set():
    if not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis"}), 403
    data = request.get_json(force=True) or {}
    name    = data.get("name")
    enabled = bool(data.get("enabled"))
    if not name:
        return jsonify({"ok": False, "error": "name manquant"}), 400
    ok, err = set_service_enabled(name, enabled)
    if ok:
        app.logger.info("services/set — %s → %s", name, "enabled" if enabled else "disabled")
        return jsonify({"ok": True})
    app.logger.warning("services/set — %s failed: %s", name, err)
    return jsonify({"ok": False, "error": err}), 500


@app.route("/api/services/set-batch", methods=["POST"])
def api_services_set_batch():
    if not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis"}), 403
    data    = request.get_json(force=True) or {}
    changes = data.get("changes") or []
    results = []
    ok_count, fail_count = 0, 0
    for change in changes:
        name    = change.get("name")
        enabled = bool(change.get("enabled"))
        if not name:
            results.append({"name": name, "ok": False, "error": "name manquant"})
            fail_count += 1
            continue
        ok, err = set_service_enabled(name, enabled)
        results.append({"name": name, "ok": ok, "error": err, "enabled": enabled})
        if ok:
            ok_count += 1
        else:
            fail_count += 1
    app.logger.info("services/set-batch — %d ok, %d échec(s)", ok_count, fail_count)
    return jsonify({"ok": fail_count == 0, "results": results, "ok_count": ok_count, "fail_count": fail_count})


@app.route("/api/scheduled-tasks")
def api_scheduled_tasks_list():
    try:
        return jsonify({"tasks": get_scheduled_tasks_state(), "is_admin": is_admin()})
    except Exception as e:
        app.logger.exception("scheduled-tasks list error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scheduled-tasks/set", methods=["POST"])
def api_scheduled_tasks_set():
    if not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis"}), 403
    data = request.get_json(force=True) or {}
    path    = data.get("path")
    enabled = bool(data.get("enabled"))
    if not path:
        return jsonify({"ok": False, "error": "path manquant"}), 400
    ok, err = set_scheduled_task_enabled(path, enabled)
    if ok:
        app.logger.info("scheduled-tasks/set — %s → %s", path, "enabled" if enabled else "disabled")
        return jsonify({"ok": True})
    app.logger.warning("scheduled-tasks/set — %s failed: %s", path, err)
    return jsonify({"ok": False, "error": err}), 500


@app.route("/api/scheduled-tasks/set-batch", methods=["POST"])
def api_scheduled_tasks_set_batch():
    if not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis"}), 403
    data    = request.get_json(force=True) or {}
    changes = data.get("changes") or []
    results = []
    ok_count, fail_count = 0, 0
    for change in changes:
        path    = change.get("path")
        enabled = bool(change.get("enabled"))
        if not path:
            results.append({"path": path, "ok": False, "error": "path manquant"})
            fail_count += 1
            continue
        ok, err = set_scheduled_task_enabled(path, enabled)
        results.append({"path": path, "ok": ok, "error": err, "enabled": enabled})
        if ok:
            ok_count += 1
        else:
            fail_count += 1
    app.logger.info("scheduled-tasks/set-batch — %d ok, %d échec(s)", ok_count, fail_count)
    return jsonify({"ok": fail_count == 0, "results": results, "ok_count": ok_count, "fail_count": fail_count})


@app.route("/api/repair/list")
def api_repair_list():
    try:
        return jsonify({"actions": list_repair_actions(), "is_admin": is_admin()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repair/run", methods=["POST"])
def api_repair_run():
    data = request.get_json(force=True) or {}
    action_id = data.get("id")
    if not action_id:
        return jsonify({"ok": False, "error": "id manquant"}), 400
    # Check admin pour les actions qui le nécessitent
    action = next((a for a in list_repair_actions() if a["id"] == action_id), None)
    if action and action.get("needs_admin") and not is_admin():
        return jsonify({"ok": False, "error": "Droits administrateur requis"}), 403
    try:
        result = run_repair_action(action_id)
        app.logger.info("repair/run — %s → ok=%s", action_id, result.get("ok"))
        return jsonify(result)
    except Exception as e:
        app.logger.exception("repair run error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/repair/stream/<action_id>")
def api_repair_stream(action_id):
    action = next((a for a in list_repair_actions() if a["id"] == action_id), None)
    if not action:
        return jsonify({"error": "Action inconnue"}), 404
    if action.get("needs_admin") and not is_admin():
        return jsonify({"error": "Droits administrateur requis"}), 403

    def _stream():
        for event in run_repair_action_stream(action_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return Response(_stream(), mimetype="text/event-stream")


@app.route("/api/uwp-apps")
def api_uwp_apps():
    try:
        return jsonify(list_uwp_apps())
    except Exception as e:
        app.logger.exception("uwp-apps list error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/uwp-apps/remove", methods=["POST"])
def api_uwp_apps_remove():
    data = request.get_json(force=True) or {}
    packages = data.get("packages") or []
    if not packages:
        return jsonify({"error": "Aucun package sélectionné"}), 400
    try:
        result = remove_uwp_apps(packages)
        app.logger.info("uwp-apps/remove — %d ok, %d échec(s)", result["ok_count"], result["fail_count"])
        return jsonify(result)
    except Exception as e:
        app.logger.exception("uwp-apps remove error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/drivers")
def api_drivers():
    try:
        return jsonify(get_drivers())
    except Exception as e:
        app.logger.exception("drivers error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/drivers/export")
def api_drivers_export():
    fmt = (request.args.get("format") or "html").lower()
    if fmt not in ("html", "txt", "json"):
        fmt = "html"
    try:
        report = export_drivers_report(fmt)
    except Exception as e:
        app.logger.exception("drivers export error")
        return jsonify({"error": str(e)}), 500
    resp = Response(report["content"], mimetype=report["mimetype"])
    resp.headers["Content-Disposition"] = f'attachment; filename="{report["filename"]}"'
    return resp


@app.route("/api/drivers/wu-scan", methods=["POST"])
def api_drivers_wu_scan():
    try:
        return jsonify(scan_windows_update_drivers())
    except Exception as e:
        app.logger.exception("wu-scan error")
        return jsonify({"updates": [], "error": str(e)}), 500


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
        task_labels = [t["label"] for t in selected]
        _save_history_entry(total_freed, kind="clean", label="Nettoyage principal", tasks=task_labels)
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

    import webbrowser as _wb, threading as _th
    _th.Timer(1.0, lambda: _wb.open(url)).start()

    app.run(host='127.0.0.1', port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    _run()
