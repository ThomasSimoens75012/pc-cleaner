"""
cleaner.py — Fonctions de nettoyage + outils système Windows
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import ctypes
import winreg
from collections import defaultdict
from pathlib import Path


def _ps_json(ps_command, timeout=10):
    """Exécute une commande PowerShell et retourne le JSON parsé sous forme de liste.

    Gère l'encodage UTF-8, la normalisation dict→list, et les erreurs.
    Retourne [] si échec, sortie vide, ou "null".
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_command],
            capture_output=True, timeout=timeout, creationflags=0x08000000,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out = r.stdout.decode("utf-8", errors="replace").strip()
    if not out or out == "null":
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def _decode_output(raw):
    """Décode la sortie brute d'un subprocess Windows.

    Essaie UTF-8 d'abord (PowerShell forcé en UTF-8), puis MBCS (cp1252 sur
    Windows FR) pour les outils natifs (DISM, SFC, schtasks, reg.exe, etc.).
    """
    try:
        return raw.decode("utf-8")
    except (UnicodeDecodeError, LookupError):
        return raw.decode("mbcs", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ──────────────────────────────────────────────────────────────────────────────

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_ADMIN_PATH_PREFIXES = tuple(
    p.lower() for p in (
        os.environ.get("ProgramFiles",      r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("SystemRoot",        r"C:\Windows"),
        r"C:\ProgramData",
        r"C:\Users\Default",     # profil template Windows, propriété SYSTEM
        r"C:\Users\Public",      # partagé entre utilisateurs, ACL restrictive
    )
)


def is_admin_path(path):
    """True si la suppression du chemin nécessite les droits administrateur
    que l'utilisateur courant ne possède pas. Renvoie toujours False si déjà admin."""
    if is_admin():
        return False
    try:
        return str(path).lower().startswith(_ADMIN_PATH_PREFIXES)
    except Exception:
        return False


def fmt_size(size_bytes):
    if size_bytes == 0:
        return "0 o"
    for unit in ("o", "Ko", "Mo", "Go"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} To"


def get_folder_size(folder):
    total = 0
    stack = [folder]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass
    return total


_RECYCLE_SESSIONS_DIR = Path(__file__).parent / "logs" / "recycle_sessions"


def _save_recycle_session(label, paths, freed):
    """Sauvegarde un manifeste décrivant un batch envoyé à la corbeille.

    Permet la restauration ultérieure via restore_recycle_session(id).
    """
    from datetime import datetime
    import uuid
    if not paths:
        return None
    try:
        _RECYCLE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    now = datetime.now()
    sid = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    manifest = {
        "id":         sid,
        "timestamp":  now.isoformat(),
        "label":      label,
        "count":      len(paths),
        "freed":      int(freed or 0),
        "paths":      [str(p) for p in paths],
    }
    try:
        with open(_RECYCLE_SESSIONS_DIR / f"{sid}.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    except Exception:
        return None
    return sid


def _recycle_many(paths, label="Nettoyage"):
    """Envoie un batch de chemins à la corbeille, retourne (freed_bytes, errors).

    Calcule la taille avant suppression, utilise SHFileOperationW en un seul
    appel, et sauvegarde un manifeste de session pour restauration ultérieure.
    """
    if not paths:
        return 0, []
    existing = []
    total = 0
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            size = get_folder_size(path) if path.is_dir() else path.stat().st_size
        except Exception:
            size = 0
        existing.append(str(path))
        total += size
    if not existing:
        return 0, []
    res = send_to_recycle_bin(existing)
    moved = res.get("moved", 0)
    errs = list(res.get("errors", []))
    if moved < len(existing):
        errs.append(f"{len(existing) - moved} élément(s) non déplacé(s)")
    freed = int(total * (moved / len(existing))) if existing else 0

    # Sauvegarde la session avec les chemins réellement supprimés (pas un slice
    # des N premiers — les échecs peuvent être à n'importe quelle position)
    if moved > 0:
        try:
            actually_moved = [p for p in existing if not Path(p).exists()]
            _save_recycle_session(label, actually_moved if actually_moved else existing, freed)
        except Exception:
            pass

    return freed, errs


def list_recycle_sessions(limit=50):
    """Liste les sessions de corbeille disponibles, du plus récent au plus ancien."""
    if not _RECYCLE_SESSIONS_DIR.exists():
        return []
    sessions = []
    for f in sorted(_RECYCLE_SESSIONS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            sessions.append({
                "id":        data.get("id"),
                "timestamp": data.get("timestamp"),
                "label":     data.get("label"),
                "count":     data.get("count"),
                "freed":     data.get("freed", 0),
                "freed_fmt": fmt_size(data.get("freed", 0)),
            })
        except Exception:
            continue
    return sessions


def restore_recycle_session(session_id):
    """Restaure un batch depuis la corbeille via Shell.Application COM.

    Retourne {"restored": int, "not_found": int, "errors": [str]}.
    """
    manifest_path = _RECYCLE_SESSIONS_DIR / f"{session_id}.json"
    if not manifest_path.exists():
        return {"restored": 0, "not_found": 0, "errors": ["Session introuvable"]}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        return {"restored": 0, "not_found": 0, "errors": [str(e)]}

    targets = [str(p).lower() for p in (manifest.get("paths") or [])]
    if not targets:
        return {"restored": 0, "not_found": 0, "errors": ["Aucun chemin dans la session"]}

    # Passe la liste par fichier temporaire (évite les problèmes d'escaping)
    import tempfile
    target_file = Path(tempfile.gettempdir()) / f"oc_restore_{session_id}.txt"
    try:
        with open(target_file, "w", encoding="utf-8") as f:
            for t in targets:
                f.write(t + "\n")
    except Exception as e:
        return {"restored": 0, "not_found": 0, "errors": [f"Écriture target list: {e}"]}

    ps_cmd = r"""
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $targets = @{}
    Get-Content -Path '__TARGET_FILE__' -Encoding UTF8 | ForEach-Object {
      if ($_.Trim()) { $targets[$_.Trim().ToLower()] = $true }
    }
    $rb = (New-Object -ComObject Shell.Application).NameSpace(10)
    $restored = 0
    $items = @($rb.Items())
    foreach ($item in $items) {
      $folder = $rb.GetDetailsOf($item, 1)
      $name   = $item.Name
      $full   = (Join-Path $folder $name).ToLower()
      if ($targets.ContainsKey($full)) {
        $verb = $item.Verbs() | Where-Object { $_.Name -match 'estaurer|estore|ndelete' } | Select-Object -First 1
        if ($verb) {
          try { $verb.DoIt(); $restored++ } catch { }
        }
      }
    }
    Write-Host ("RESTORED=" + $restored)
    """.replace("__TARGET_FILE__", str(target_file).replace("'", "''"))

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, timeout=60, creationflags=0x08000000,
        )
        out = _decode_output(r.stdout)
        err = _decode_output(r.stderr)
    except Exception as e:
        return {"restored": 0, "not_found": 0, "errors": [str(e)]}
    finally:
        try:
            target_file.unlink()
        except Exception:
            pass

    restored = 0
    m = re.search(r"RESTORED=(\d+)", out)
    if m:
        restored = int(m.group(1))

    not_found = len(targets) - restored
    errors = []
    if err.strip():
        errors.append(err.strip())

    # Supprime le manifeste si tout a été restauré
    if restored == len(targets) and not errors:
        try:
            manifest_path.unlink()
        except Exception:
            pass

    return {"restored": restored, "not_found": max(not_found, 0), "errors": errors}


def delete_recycle_session(session_id):
    """Supprime le manifeste d'une session sans toucher à la corbeille."""
    try:
        (_RECYCLE_SESSIONS_DIR / f"{session_id}.json").unlink()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_folder_contents(folder):
    """Envoie les enfants directs de `folder` à la corbeille Windows (batch unique).

    Retourne (freed_bytes, errors_count). Le batching via SHFileOperation est
    beaucoup plus rapide qu'un appel par fichier et permet la restauration
    manuelle depuis la corbeille.
    """
    folder = Path(folder)
    if not folder.exists():
        return 0, 0

    items = []
    total_size = 0
    try:
        for item in folder.iterdir():
            try:
                size = get_folder_size(item) if item.is_dir() else item.stat().st_size
                items.append(str(item))
                total_size += size
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        return 0, 0

    if not items:
        return 0, 0

    res = send_to_recycle_bin(items)
    moved = res.get("moved", 0)
    failed = res.get("failed", len(items) - moved)
    freed = int(total_size * (moved / len(items))) if items else 0
    return freed, failed


def get_disk_info():
    results = []
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                results.append({
                    "device": part.device, "total": u.total,
                    "used": u.used, "free": u.free,
                    "percent": round(u.percent, 1),
                    "total_fmt": fmt_size(u.total),
                    "free_fmt": fmt_size(u.free),
                })
            except (PermissionError, OSError):
                pass
    except ImportError:
        u = shutil.disk_usage("C:\\")
        pct = round(u.used / u.total * 100, 1)
        results.append({
            "device": "C:\\", "total": u.total, "used": u.used,
            "free": u.free, "percent": pct,
            "total_fmt": fmt_size(u.total), "free_fmt": fmt_size(u.free),
        })
    return results


# ── Nettoyage SQLite (historique / cookies navigateurs) ──────────────────────

def _sqlite_clean(db_path, queries, log):
    """
    Copie un fichier SQLite vers un temp, exécute des DELETE, recopie.
    Sûr même si le navigateur est ouvert (on n'écrit pas directement sur le fichier verrouillé).
    Retourne les octets libérés.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return 0

    tmp = Path(tempfile.mktemp(suffix=".db"))
    try:
        size_before = db_path.stat().st_size
        shutil.copy2(db_path, tmp)

        with sqlite3.connect(str(tmp)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
            for q in queries:
                try:
                    conn.execute(q)
                except sqlite3.OperationalError:
                    pass  # table absente dans certaines versions
            conn.commit()   # fermer la transaction avant VACUUM (obligatoire en SQLite)
            conn.execute("VACUUM")

        shutil.copy2(tmp, db_path)
        size_after = db_path.stat().st_size
        return max(0, size_before - size_after)

    except OSError as e:
        winerr = getattr(e, "winerror", 0)
        if isinstance(e, PermissionError) or winerr in (5, 1224):
            log(f"{db_path.name} verrouillé — fermez le navigateur et réessayez")
        else:
            log(f"{db_path.name} : {e}")
        return 0
    except Exception as e:
        log(f"{db_path.name} : {e}")
        return 0
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


_BROWSER_DATA_TYPES_CHROMIUM = [
    # (key, label, [relative paths/glob], description)
    ("cache",      "Cache",        ["Cache", "Code Cache", "GPUCache", "ShaderCache"], "Fichiers temporaires web"),
    ("cookies",    "Cookies",      ["Network/Cookies", "Network/Cookies-journal"],     "Sessions et préférences de sites"),
    ("history",    "Historique",   ["History", "History-journal", "Top Sites", "Visited Links"], "Historique de navigation"),
    ("downloads",  "Téléchargements", ["History"], "Liste des téléchargements (partagé avec History)"),
    ("sessions",   "Sessions",     ["Sessions", "Session Storage", "Current Session", "Current Tabs", "Last Session", "Last Tabs"], "Onglets restaurables"),
    ("passwords",  "Mots de passe", ["Login Data", "Login Data-journal", "Login Data For Account", "Login Data For Account-journal"], "Identifiants enregistrés (DANGER)"),
    ("autofill",   "Auto-remplissage", ["Web Data", "Web Data-journal"], "Formulaires, cartes, adresses"),
    ("local_storage", "Local Storage", ["Local Storage"], "Données de sites (localStorage/IndexedDB)"),
    ("service_workers", "Service Workers", ["Service Worker"], "Scripts offline des sites"),
]

_BROWSER_DATA_TYPES_FIREFOX = [
    ("cache",      "Cache",        ["cache2"],  "Fichiers temporaires web"),
    ("cookies",    "Cookies",      ["cookies.sqlite", "cookies.sqlite-wal"], "Sessions et préférences"),
    ("history",    "Historique",   ["places.sqlite", "places.sqlite-wal"],   "Historique et favoris"),
    ("sessions",   "Sessions",     ["sessionstore.jsonlz4", "sessionstore-backups"], "Onglets restaurables"),
    ("passwords",  "Mots de passe", ["logins.json", "key4.db"], "Identifiants enregistrés (DANGER)"),
    ("autofill",   "Auto-remplissage", ["formhistory.sqlite"], "Données de formulaires"),
    ("local_storage", "Storage", ["storage"], "Données de sites"),
]


def _browser_path_size(profile, rel):
    p = profile / rel
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    if p.is_dir():
        return get_folder_size(str(p))
    return 0


def get_browser_data_breakdown():
    """Détaille les données navigateur par profil et catégorie.

    Retourne une liste de {browser, profile, path, items: [{key, label, desc, size, size_fmt}]}.
    """
    out = []
    for kind, profile in _browser_profile_paths():
        types = _BROWSER_DATA_TYPES_CHROMIUM if kind == "chromium" else _BROWSER_DATA_TYPES_FIREFOX
        # Nom affichable : Chrome / Edge / Brave / Firefox + nom de profil
        parent_name = profile.parent.parent.name if kind == "chromium" else "Firefox"
        display_browser = parent_name
        if "Chrome" in str(profile):       display_browser = "Chrome"
        elif "Edge" in str(profile):       display_browser = "Edge"
        elif "Brave" in str(profile):      display_browser = "Brave"
        elif "Firefox" in str(profile):    display_browser = "Firefox"

        items = []
        for key, label, rels, desc in types:
            size = 0
            for rel in rels:
                size += _browser_path_size(profile, rel)
            items.append({
                "key":      key,
                "label":    label,
                "desc":     desc,
                "size":     size,
                "size_fmt": fmt_size(size),
                "sensitive": key in ("passwords", "autofill"),
            })
        out.append({
            "browser":  display_browser,
            "profile":  profile.name,
            "path":     str(profile),
            "kind":     kind,
            "items":    items,
        })
    return out


def clean_browser_data(selections):
    """Envoie à la corbeille les catégories sélectionnées par profil.

    selections : list de {path: str, keys: [str]}
    Retourne {deleted_bytes, errors}.
    """
    batch = []
    errors = []
    for sel in selections or []:
        profile_path = Path(sel.get("path", ""))
        keys = set(sel.get("keys") or [])
        if not profile_path.exists():
            errors.append(f"Profil introuvable: {profile_path}")
            continue
        kind = "firefox" if "Firefox" in str(profile_path) else "chromium"
        types = _BROWSER_DATA_TYPES_CHROMIUM if kind == "chromium" else _BROWSER_DATA_TYPES_FIREFOX
        for key, _label, rels, _desc in types:
            if key not in keys:
                continue
            for rel in rels:
                p = profile_path / rel
                if p.exists():
                    batch.append(str(p))

    freed, errs = _recycle_many(batch, label="Données navigateurs")
    errors.extend(errs)
    return {"deleted_bytes": freed, "deleted_fmt": fmt_size(freed), "errors": errors}


def _browser_profile_paths():
    """Retourne les chemins de profils pour Chrome, Edge et Firefox."""
    local   = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    profiles = []

    for base in [
        local / "Google"    / "Chrome"    / "User Data",
        local / "Microsoft" / "Edge"      / "User Data",
        local / "BraveSoftware" / "Brave-Browser" / "User Data",
    ]:
        if base.exists():
            for p in list(base.glob("Default")) + list(base.glob("Profile *")):
                if p.is_dir():
                    profiles.append(("chromium", p))

    ff_profiles = appdata / "Mozilla" / "Firefox" / "Profiles"
    if ff_profiles.exists():
        for p in ff_profiles.iterdir():
            if p.is_dir():
                profiles.append(("firefox", p))

    return profiles


# ──────────────────────────────────────────────────────────────────────────────
# Estimations
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_TEMP = r"C:\Windows\Temp"


def estimate_temp():
    targets = [os.environ.get("TEMP", ""), os.environ.get("TMP", ""), SYSTEM_TEMP]
    seen, total = set(), 0
    for folder in targets:
        resolved = str(Path(folder).resolve()) if folder else ""
        if resolved and resolved not in seen:
            seen.add(resolved)
            total += get_folder_size(resolved)
    return total


_BROWSER_PROCESS_MAP = {
    # Nom exe processus → noms de dossier profiles correspondants
    "chrome.exe":  ["Chrome"],
    "msedge.exe":  ["Edge"],
    "brave.exe":   ["Brave-Browser"],
    "firefox.exe": ["Firefox"],
    "opera.exe":   ["Opera"],
    "vivaldi.exe": ["Vivaldi"],
}


def _get_running_browsers():
    """Détecte les navigateurs actuellement ouverts. Retourne un set de noms de dossier."""
    try:
        import psutil
    except ImportError:
        return set()
    running = set()
    try:
        for proc in psutil.process_iter(["name"]):
            pname = (proc.info.get("name") or "").lower()
            for exe, folder_names in _BROWSER_PROCESS_MAP.items():
                if pname == exe:
                    running.update(folder_names)
    except Exception:
        pass
    return running


def _is_browser_profile_locked(profile_path, running_browsers):
    """Vérifie si un profil navigateur est verrouillé (navigateur ouvert)."""
    if not running_browsers:
        return False
    path_str = str(profile_path)
    for browser_dir in running_browsers:
        if browser_dir in path_str:
            return True
    return False


def get_locked_browsers_info():
    """Retourne des infos sur les navigateurs ouverts pour le frontend.

    Retourne {"locked": ["Chrome", ...], "message": str|None}.
    """
    running = _get_running_browsers()
    if not running:
        return {"locked": [], "message": None}
    names = sorted(running)
    return {
        "locked": names,
        "message": f"Navigateur(s) ouvert(s) : {', '.join(names)}. Fermez-les pour un nettoyage complet.",
    }


def estimate_browser_cache():
    local   = Path(os.environ.get("LOCALAPPDATA", ""))
    running = _get_running_browsers()
    total = 0
    for _, profile in _browser_profile_paths():
        if _is_browser_profile_locked(profile, running):
            continue  # Skip les profils verrouillés — pas comptés dans l'estimation
        for sub in ["Cache", "Code Cache", "GPUCache", "cache2"]:
            total += get_folder_size(profile / sub)
    return total


def estimate_recycle_bin():
    try:
        class SHQUERYRBINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong),
                        ("i64Size", ctypes.c_longlong),
                        ("i64NumItems", ctypes.c_longlong)]
        info = SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
        ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        return max(0, info.i64Size)
    except Exception:
        return 0


def estimate_thumbnails():
    d = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Explorer"
    if not d.exists():
        return 0
    return sum(p.stat().st_size for p in d.glob("thumbcache_*.db") if p.exists())


def estimate_prefetch():
    return get_folder_size(r"C:\Windows\Prefetch")


def estimate_windows_update():
    return get_folder_size(r"C:\Windows\SoftwareDistribution\Download")


def estimate_history():
    total = 0
    running = _get_running_browsers()
    for kind, profile in _browser_profile_paths():
        if _is_browser_profile_locked(profile, running):
            continue
        if kind == "chromium":
            total += (profile / "History").stat().st_size if (profile / "History").exists() else 0
        elif kind == "firefox":
            total += (profile / "places.sqlite").stat().st_size if (profile / "places.sqlite").exists() else 0
    return total


def estimate_cookies():
    total = 0
    running = _get_running_browsers()
    for kind, profile in _browser_profile_paths():
        if _is_browser_profile_locked(profile, running):
            continue
        if kind == "chromium":
            total += (profile / "Cookies").stat().st_size if (profile / "Cookies").exists() else 0
        elif kind == "firefox":
            total += (profile / "cookies.sqlite").stat().st_size if (profile / "cookies.sqlite").exists() else 0
    return total


def _recent_files_dir():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"


def _purge_recent_shortcuts():
    """Supprime les raccourcis .lnk du dossier Recent. Retourne (count, freed, errors)."""
    files = [str(f) for f in _recent_files_dir().glob("*.lnk")]
    freed, errors = _recycle_many(files, label="Fichiers récents")
    count = len(files) - len(errors)
    return max(count, 0), freed, errors


def estimate_recent_files():
    total = 0
    for f in _recent_files_dir().glob("*.lnk"):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def estimate_dumps():
    total = 0
    search_dirs = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "CrashDumps",
        Path(os.environ.get("TEMP", "")),
        Path(os.environ.get("USERPROFILE", "")),
        Path(r"C:\Windows\Minidump"),
    ]
    for d in search_dirs:
        if d.exists():
            for ext in ["*.dmp", "*.mdmp"]:
                for f in d.glob(ext):
                    try:
                        total += f.stat().st_size
                    except Exception:
                        pass
    return total


def estimate_event_logs():
    return get_folder_size(r"C:\Windows\System32\winevt\Logs")


def estimate_app_caches():
    appdata = Path(os.environ.get("APPDATA", ""))
    local   = Path(os.environ.get("LOCALAPPDATA", ""))
    paths = [
        appdata / "discord"           / "Cache" / "Cache_Data",
        local   / "Discord"           / "Cache" / "Cache_Data",
        appdata / "Microsoft" / "Teams" / "Cache",
        appdata / "Slack"             / "Cache" / "Cache_Data",
        appdata / "Spotify"           / "Data",
        local   / "WhatsApp"          / "Cache",
    ]
    return sum(get_folder_size(p) for p in paths if p.exists())


def estimate_font_cache():
    return get_folder_size(r"C:\Windows\ServiceProfiles\LocalService\AppData\Local\FontCache")


# ──────────────────────────────────────────────────────────────────────────────
# Fonctions de nettoyage
# ──────────────────────────────────────────────────────────────────────────────

def task_temp(log):
    targets = [os.environ.get("TEMP", ""), os.environ.get("TMP", ""), SYSTEM_TEMP]
    seen, total = set(), 0
    for folder in targets:
        resolved = str(Path(folder).resolve()) if folder else ""
        if resolved and resolved not in seen and Path(resolved).exists():
            seen.add(resolved)
            freed, _ = delete_folder_contents(resolved)
            total += freed
    if total > 0:
        log(f"Fichiers temporaires — {fmt_size(total)} libérés")
    else:
        log("Fichiers temporaires — déjà propre")
    return total


def task_browser_cache(log):
    total = 0
    browser_totals = {}
    running = _get_running_browsers()
    skipped = []
    for kind, profile in _browser_profile_paths():
        browser = profile.parent.parent.name if kind == "chromium" else "Firefox"
        if _is_browser_profile_locked(profile, running):
            if browser not in skipped:
                skipped.append(browser)
            continue
        freed = 0
        if kind == "chromium":
            for sub in ["Cache", "Code Cache", "GPUCache"]:
                f, _ = delete_folder_contents(profile / sub)
                freed += f
        elif kind == "firefox":
            f, _ = delete_folder_contents(profile / "cache2")
            freed += f
        if freed:
            browser_totals[browser] = browser_totals.get(browser, 0) + freed
        total += freed
    if skipped:
        log(f"Cache navigateurs — {', '.join(skipped)} ouvert(s), nettoyage impossible")
    if browser_totals:
        for browser, freed in browser_totals.items():
            log(f"Cache {browser} — {fmt_size(freed)} libérés")
    elif not skipped:
        log("Cache navigateurs — déjà propre")
    return total


def task_browser_history(log):
    total = 0
    browser_totals = {}
    running = _get_running_browsers()
    skipped = []
    for kind, profile in _browser_profile_paths():
        browser = profile.parent.parent.name if kind == "chromium" else "Firefox"
        if _is_browser_profile_locked(profile, running):
            if browser not in skipped:
                skipped.append(browser)
            continue
        if kind == "chromium":
            freed = _sqlite_clean(profile / "History", [
                "DELETE FROM urls",
                "DELETE FROM visits",
                "DELETE FROM keyword_search_terms",
                "DELETE FROM downloads",
                "DELETE FROM download_url_chains",
            ], log)
        elif kind == "firefox":
            freed = _sqlite_clean(profile / "places.sqlite", [
                "DELETE FROM moz_historyvisits",
                "DELETE FROM moz_inputhistory",
                "DELETE FROM moz_anno_attributes WHERE id NOT IN (SELECT anno_attribute_id FROM moz_annos)",
                "DELETE FROM moz_origins WHERE id NOT IN (SELECT origin_id FROM moz_places)",
            ], log)
        else:
            freed = 0
        if freed:
            browser_totals[browser] = browser_totals.get(browser, 0) + freed
        total += freed
    if skipped:
        log(f"Historique navigateurs — {', '.join(skipped)} ouvert(s), nettoyage impossible")
    if browser_totals:
        for browser, freed in browser_totals.items():
            log(f"Historique {browser} — {fmt_size(freed)} libérés")
    elif not skipped:
        log("Historique navigateurs — déjà propre")
    return total


def task_browser_cookies(log):
    total = 0
    browser_totals = {}
    running = _get_running_browsers()
    skipped = []
    for kind, profile in _browser_profile_paths():
        browser = profile.parent.parent.name if kind == "chromium" else "Firefox"
        if _is_browser_profile_locked(profile, running):
            if browser not in skipped:
                skipped.append(browser)
            continue
        if kind == "chromium":
            freed = _sqlite_clean(profile / "Cookies", ["DELETE FROM cookies"], log)
        elif kind == "firefox":
            freed = _sqlite_clean(profile / "cookies.sqlite", ["DELETE FROM moz_cookies"], log)
        else:
            freed = 0
        if freed:
            browser_totals[browser] = browser_totals.get(browser, 0) + freed
        total += freed
    if skipped:
        log(f"Cookies navigateurs — {', '.join(skipped)} ouvert(s), nettoyage impossible")
    if browser_totals:
        for browser, freed in browser_totals.items():
            log(f"Cookies {browser} — {fmt_size(freed)} libérés")
    elif not skipped:
        log("Cookies navigateurs — déjà propres")
    return total


def task_recycle_bin(log):
    before = estimate_recycle_bin()
    try:
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0007)
        if result == 0 or result == -2147418113:
            if before > 0:
                log(f"Corbeille — {fmt_size(before)} libérés")
            else:
                log("Corbeille — déjà vide")
        else:
            log("Corbeille — déjà vide")
    except Exception as e:
        log(f"Corbeille — erreur : {e}")
    return before


def task_dns(log):
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True, timeout=10)
        log("Cache DNS — vidé avec succès")
    except Exception as e:
        log(f"Cache DNS — erreur : {e}")
    return 0


def task_thumbnails(log):
    d = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Explorer"
    if not d.exists():
        log("Cache miniatures — déjà propre")
        return 0
    items = [str(f) for f in d.glob("thumbcache_*.db")]
    freed, _ = _recycle_many(items, label="Cache miniatures")
    if freed > 0:
        log(f"Cache miniatures — {fmt_size(freed)} libérés")
    else:
        log("Cache miniatures — déjà propre")
    return freed


def task_recent_files(log):
    _, freed, _ = _purge_recent_shortcuts()
    if freed > 0:
        log(f"Fichiers récents — {fmt_size(freed)} supprimés")
    else:
        log("Fichiers récents — déjà propre")
    return freed


def task_dumps(log):
    search_dirs = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "CrashDumps",
        Path(os.environ.get("TEMP", "")),
        Path(os.environ.get("USERPROFILE", "")),
        Path(r"C:\Windows\Minidump"),
    ]
    batch = []
    for d in search_dirs:
        if not d.exists():
            continue
        for ext in ["*.dmp", "*.mdmp"]:
            batch.extend(str(f) for f in d.glob(ext))
    total, _ = _recycle_many(batch, label="Fichiers crash")
    if total > 0:
        log(f"Fichiers crash — {len(batch)} fichier(s) supprimé(s), {fmt_size(total)} libérés")
    else:
        log("Fichiers crash — aucun trouvé")
    return total


def task_event_logs(log):
    if not is_admin():
        log("Journaux Windows — droits administrateur requis")
        return 0
    log_names = ["Application", "System", "Security", "Setup", "HardwareEvents"]
    cleared = 0
    for name in log_names:
        try:
            r = subprocess.run(["wevtutil", "cl", name],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                cleared += 1
        except Exception:
            pass
    log(f"Journaux Windows — {cleared}/{len(log_names)} journaux vidés")
    return 0


def task_app_caches(log):
    appdata = Path(os.environ.get("APPDATA", ""))
    local   = Path(os.environ.get("LOCALAPPDATA", ""))
    targets = {
        "Discord":  [appdata / "discord" / "Cache" / "Cache_Data",
                     local   / "Discord" / "Cache" / "Cache_Data"],
        "Teams":    [appdata / "Microsoft" / "Teams" / "Cache"],
        "Slack":    [appdata / "Slack" / "Cache" / "Cache_Data"],
        "Spotify":  [appdata / "Spotify" / "Data"],
        "WhatsApp": [local   / "WhatsApp" / "Cache"],
    }
    total = 0
    results = []
    for app_name, paths in targets.items():
        freed = sum(delete_folder_contents(p)[0] for p in paths if p.exists())
        if freed:
            results.append(f"{app_name} {fmt_size(freed)}")
        total += freed
    if results:
        log(f"Caches apps — {fmt_size(total)} libérés ({', '.join(results)})")
    else:
        log("Caches apps — déjà propres")
    return total


def task_font_cache(log):
    if not is_admin():
        log("Cache polices — droits administrateur requis")
        return 0
    subprocess.run(["net", "stop", "FontCache"],       capture_output=True, timeout=10)
    subprocess.run(["net", "stop", "FontCache3.0.0.0"], capture_output=True, timeout=10)

    cache_dir = Path(r"C:\Windows\ServiceProfiles\LocalService\AppData\Local\FontCache")
    freed, _ = delete_folder_contents(cache_dir)

    fntcache = Path(r"C:\Windows\System32\FNTCACHE.DAT")
    if fntcache.exists():
        f, _ = _recycle_many([str(fntcache)])
        freed += f

    subprocess.run(["net", "start", "FontCache"], capture_output=True, timeout=10)
    if freed > 0:
        log(f"Cache polices — {fmt_size(freed)} libérés")
    else:
        log("Cache polices — déjà propre")
    return freed


def task_prefetch(log):
    if not is_admin():
        log("Prefetch — droits administrateur requis")
        return 0
    freed, _ = delete_folder_contents(r"C:\Windows\Prefetch")
    if freed > 0:
        log(f"Prefetch — {fmt_size(freed)} libérés")
    else:
        log("Prefetch — déjà propre")
    return freed


def task_windows_update(log):
    if not is_admin():
        log("Windows Update — droits administrateur requis")
        return 0
    try:
        subprocess.run(["net", "stop", "wuauserv"], capture_output=True, timeout=15)
        freed, _ = delete_folder_contents(r"C:\Windows\SoftwareDistribution\Download")
        subprocess.run(["net", "start", "wuauserv"], capture_output=True, timeout=15)
        if freed > 0:
            log(f"Windows Update — {fmt_size(freed)} libérés")
        else:
            log("Windows Update — déjà propre")
        return freed
    except Exception as e:
        log(f"Windows Update — erreur : {e}")
        return 0


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Applications installées
# ──────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# Applications installées — v2 (filtrage système, détection cassées, UserAssist,
# catégorisation, merge winget/scoop/choco, uninstall silencieuse, résidus)
# ══════════════════════════════════════════════════════════════════════════════

_APP_CATEGORIES = [
    # (label, [mots-clés publisher/name — case-insensitive])
    # Les catégories les plus spécifiques d'abord pour éviter les faux positifs
    ("Développement",  ["jetbrains", "visual studio code", "microsoft visual c++",
                        "microsoft visual studio", "git for windows",
                        "python", "node.js", "nodejs", "npm", "docker", "github desktop",
                        "gitlab", "android studio", "cursor",
                        "sublime", "notepad++", "postman", "insomnia", "wireshark", "openssl"]),
    ("Jeux",           ["steam", "epic games", "riot", "ubisoft", "ea app", "origin", "battle.net",
                        "blizzard", "gog galaxy", "roblox", "minecraft", "league of legends",
                        "valorant"]),
    ("Multimédia",     ["spotify", "vlc", "obs studio", "audacity", "gimp", "inkscape", "blender",
                        "davinci", "adobe", "netflix", "plex", "kodi", "handbrake", "mkvtoolnix",
                        "paint.net", "photoshop", "lightroom", "premiere", "after effects"]),
    ("Productivité",   ["office", "microsoft word", "microsoft excel", "microsoft powerpoint",
                        "microsoft outlook", "microsoft teams", "slack", "zoom",
                        "notion", "obsidian", "libreoffice", "onenote", "todoist", "trello",
                        "1password", "bitwarden", "lastpass", "keepass", "evernote"]),
    ("Communication",  ["discord", "telegram", "whatsapp", "signal", "skype", "thunderbird"]),
    ("Navigateurs",    ["google chrome", "mozilla firefox", "microsoft edge", "brave", "opera",
                        "vivaldi", "tor browser"]),
    ("Sécurité",       ["antivirus", "bitdefender", "kaspersky", "norton", "mcafee",
                        "avast", "avg", "malwarebytes", "avira", "eset"]),
    ("Système",        ["microsoft .net", "redistributable", "runtime", "framework",
                        "nvidia", "amd software", "intel(r)", "realtek", "driver", "sdk"]),
]


def _categorize_app(name, publisher):
    """Classe une app en catégorie heuristique."""
    hay = (str(name) + " " + str(publisher)).lower()
    for label, keywords in _APP_CATEGORIES:
        for kw in keywords:
            if kw in hay:
                return label
    return "Autres"


_USERASSIST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"


def _rot13(s):
    result = []
    for c in s:
        if "a" <= c <= "z":
            result.append(chr((ord(c) - ord("a") + 13) % 26 + ord("a")))
        elif "A" <= c <= "Z":
            result.append(chr((ord(c) - ord("A") + 13) % 26 + ord("A")))
        else:
            result.append(c)
    return "".join(result)


def _filetime_to_datetime(filetime_int):
    """Convertit un FILETIME Windows (100ns depuis 1601) en datetime."""
    from datetime import datetime, timedelta
    if filetime_int == 0:
        return None
    try:
        # 11644473600 = secondes entre 1601-01-01 et 1970-01-01
        epoch_s = (filetime_int / 10_000_000) - 11644473600
        if epoch_s <= 0 or epoch_s > 4102444800:  # borne 2100
            return None
        return datetime.fromtimestamp(epoch_s)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_userassist_map():
    """Parse HKCU\\...\\UserAssist et retourne {exe_lowercase: {last_used, launch_count}}.

    Chaque valeur UserAssist contient une structure binaire :
    - bytes[4:8]   : launch count (uint32 LE)
    - bytes[60:68] : last run timestamp FILETIME (uint64 LE)
    Le nom de la valeur est ROT13-encodé et contient souvent un chemin.
    """
    import struct
    result = {}
    try:
        parent = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USERASSIST_KEY)
    except OSError:
        return result

    try:
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(parent, i)
                i += 1
            except OSError:
                break
            try:
                count_key = winreg.OpenKey(parent, f"{guid}\\Count")
            except OSError:
                continue
            try:
                j = 0
                while True:
                    try:
                        name, value, typ = winreg.EnumValue(count_key, j)
                        j += 1
                    except OSError:
                        break
                    if typ != winreg.REG_BINARY or not value or len(value) < 68:
                        continue
                    try:
                        launch_count = struct.unpack_from("<I", value, 4)[0]
                        ft = struct.unpack_from("<Q", value, 60)[0]
                    except struct.error:
                        continue
                    decoded = _rot13(name)
                    dt = _filetime_to_datetime(ft)
                    if dt is None and launch_count == 0:
                        continue
                    # Normaliser les chemins : UserAssist stocke souvent des
                    # identifiants GUID de dossier en début, on garde la fin
                    key_path = decoded.lower()
                    prev = result.get(key_path)
                    if not prev or (dt and prev.get("last_used") and dt > prev["last_used"]) or \
                       (dt and not prev.get("last_used")):
                        result[key_path] = {
                            "last_used":    dt,
                            "launch_count": launch_count,
                        }
            finally:
                winreg.CloseKey(count_key)
    finally:
        winreg.CloseKey(parent)

    return result


def _find_user_assist_match(exe_path, userassist_map):
    """Cherche une entrée UserAssist correspondant à un exe donné."""
    if not exe_path or not userassist_map:
        return None
    base = Path(exe_path).name.lower()
    full = str(exe_path).lower()
    # Match exact sur le nom du fichier
    for key, meta in userassist_map.items():
        if key.endswith(base) or full in key or key.endswith(full):
            return meta
    return None


def _detect_winget_apps():
    """Retourne un dict {normalized_name: winget_id} via `winget list`.

    Utilisé pour enrichir les entrées registry avec un winget_id exploitable
    pour une désinstallation silencieuse.
    """
    result = {}
    try:
        r = subprocess.run(
            ["winget", "list", "--accept-source-agreements", "--disable-interactivity"],
            capture_output=True, timeout=30, creationflags=0x08000000,
        )
        out = _decode_output(r.stdout)
    except Exception:
        return result

    lines = out.splitlines()
    sep_idx = next((i for i, l in enumerate(lines)
                    if len(l.rstrip()) > 20 and all(c == "-" for c in l.rstrip())), None)
    if sep_idx is None or sep_idx == 0:
        return result

    header_raw = lines[sep_idx - 1]
    header = header_raw.lstrip("\r-\\|/ \x1b")
    # Trouver les colonnes par position (identique à get_software_updates)
    cols = []
    k = 0
    while k < len(header):
        if header[k] != " ":
            j = k
            while j < len(header) and header[j] != " ":
                j += 1
            offset = len(header_raw) - len(header)
            cols.append(k + offset)
            k = j
        else:
            k += 1
    col_ranges = [(cols[i], cols[i + 1] if i + 1 < len(cols) else None)
                  for i in range(len(cols))]

    for line in lines[sep_idx + 1:]:
        if not line.strip() or all(c in "- " for c in line.strip()):
            continue
        parts = []
        for s, e in col_ranges:
            chunk = line[s:e].strip() if e else line[s:].strip()
            parts.append(chunk)
        if len(parts) < 2:
            continue
        name, wid = parts[0], parts[1]
        if not name or not wid or wid == "…":
            continue
        result[name.lower().strip()] = wid

    return result


def _detect_scoop_apps():
    """Détecte les apps installées via Scoop. Retourne un set de noms normalisés."""
    scoop_dir = Path(os.path.expandvars(r"%USERPROFILE%\scoop\apps"))
    if not scoop_dir.exists():
        return set()
    try:
        return {p.name.lower() for p in scoop_dir.iterdir() if p.is_dir() and p.name != "scoop"}
    except OSError:
        return set()


def _detect_choco_apps():
    """Détecte les apps installées via Chocolatey. Retourne un set de noms normalisés."""
    choco_dir = Path(os.path.expandvars(r"%ProgramData%\chocolatey\lib"))
    if not choco_dir.exists():
        return set()
    try:
        return {p.name.lower() for p in choco_dir.iterdir() if p.is_dir()}
    except OSError:
        return set()


def _exe_exists(uninstall_string):
    """Extrait le chemin de l'exécutable et vérifie son existence.

    Stratégie multi-passes pour gérer les UninstallString non quotés avec
    espaces (ex: C:\\Program Files (x86)\\Steam\\uninstall.exe) :
    1. shlex.split (fonctionne si quoté correctement)
    2. Regex .*\\.exe (extrait le plus long chemin terminant par .exe)
    3. La chaîne brute entière (dernier recours)
    """
    if not uninstall_string:
        return False
    s = uninstall_string.strip()

    # msiexec est toujours présent
    if "msiexec" in s.lower():
        return True

    # Passe 1 : shlex
    import shlex
    try:
        parts = shlex.split(s, posix=False)
        exe = parts[0].strip('"').strip("'") if parts else ""
        if exe and Path(exe).exists():
            return True
    except Exception:
        pass

    # Passe 2 : regex .*\.exe (gère les chemins avec espaces non quotés)
    m = re.search(r'^(.*?\.exe)', s, re.IGNORECASE)
    if m:
        exe = m.group(1).strip('"').strip("'")
        if exe and Path(exe).exists():
            return True

    # Passe 3 : le premier "mot" jusqu'à l'espace (dernier recours)
    first = s.split()[0].strip('"').strip("'") if s.split() else ""
    if first and Path(first).exists():
        return True

    return False


def _extract_exe_from_uninstall_string(uninstall_string):
    if not uninstall_string:
        return ""
    import shlex
    try:
        parts = shlex.split(uninstall_string, posix=False)
        return parts[0].strip('"').strip("'") if parts else ""
    except Exception:
        return ""


def get_installed_apps(deep=False):
    """Lit la liste des applications installées depuis le registre Windows.

    Version v2 :
    - filtrage SystemComponent / ParentKeyName / ReleaseType
    - détection des entrées cassées (exe inexistant)
    - enrichissement avec UserAssist (dernière utilisation, nombre de lancements)
    - catégorisation heuristique
    - merge avec winget/scoop/choco quand disponibles
    - si `deep=True` : taille réelle calculée depuis InstallLocation (plus lent)
    """
    apps = []
    seen_keys = set()

    uninstall_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    hive_label = {
        winreg.HKEY_LOCAL_MACHINE: "HKLM",
        winreg.HKEY_CURRENT_USER:  "HKCU",
    }

    # Sources externes (parallélisables mais simples en séquence)
    userassist = _parse_userassist_map()
    winget_map = _detect_winget_apps()
    scoop_set  = _detect_scoop_apps()
    choco_set  = _detect_choco_apps()

    excluded_release_types = {"Update", "Hotfix", "Security Update", "ServicePack"}

    for hive, path in uninstall_paths:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue

        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(key, i)
                i += 1
            except OSError:
                break

            try:
                sub = winreg.OpenKey(key, sub_name)
            except OSError:
                continue

            def _val(k, default=""):
                try:
                    return winreg.QueryValueEx(sub, k)[0]
                except Exception:
                    return default

            try:
                name = str(_val("DisplayName") or "").strip()
                if not name:
                    continue

                # Filtres système
                if int(_val("SystemComponent", 0) or 0) == 1:
                    continue
                if _val("ParentKeyName", ""):
                    continue
                release_type = str(_val("ReleaseType", "") or "")
                if release_type in excluded_release_types:
                    continue

                uninstall     = str(_val("UninstallString") or "")
                quiet_unins   = str(_val("QuietUninstallString") or "")
                version       = str(_val("DisplayVersion") or "")
                publisher     = str(_val("Publisher") or "")
                install_date  = str(_val("InstallDate") or "")
                install_loc   = str(_val("InstallLocation") or "")
                display_icon  = str(_val("DisplayIcon") or "")
                url_about     = str(_val("URLInfoAbout") or "")
                url_update    = str(_val("URLUpdateInfo") or "")
                help_link     = str(_val("HelpLink") or "")

                # Ignore les entrées sans aucun signe de vie
                if not uninstall and not version and not install_loc:
                    continue

                # Déduplication par clé registre unique (hive + sub_name)
                dedupe_key = f"{hive_label[hive]}\\{sub_name}"
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                # Taille estimated (en Ko selon spec registre)
                try:
                    size_kb = int(_val("EstimatedSize", 0) or 0)
                except (TypeError, ValueError):
                    size_kb = 0

                size_bytes = size_kb * 1024

                # Deep scan : taille réelle depuis InstallLocation
                if deep and install_loc and Path(install_loc).exists():
                    try:
                        real_size = get_folder_size(install_loc)
                        if real_size > 0:
                            size_bytes = real_size
                    except Exception:
                        pass

                # Détection entrée cassée
                exe_path = _extract_exe_from_uninstall_string(uninstall)
                broken = bool(uninstall) and not _exe_exists(uninstall)

                # UserAssist match
                ua_meta = None
                last_used_iso = None
                launch_count = 0
                if exe_path:
                    ua_meta = _find_user_assist_match(exe_path, userassist)
                if ua_meta is None and install_loc:
                    # Cherche n'importe quelle clé UserAssist contenant ce dossier
                    inst_lower = install_loc.lower()
                    for k, m in userassist.items():
                        if inst_lower in k:
                            ua_meta = m
                            break
                if ua_meta:
                    launch_count = ua_meta.get("launch_count", 0)
                    dt = ua_meta.get("last_used")
                    if dt:
                        last_used_iso = dt.isoformat()

                # Merge winget
                winget_id = winget_map.get(name.lower().strip(), "")

                # Merge scoop / choco
                extra_sources = []
                name_lower = name.lower()
                if scoop_set and any(s in name_lower or name_lower in s for s in scoop_set):
                    extra_sources.append("scoop")
                if choco_set and any(c in name_lower or name_lower in c for c in choco_set):
                    extra_sources.append("choco")

                apps.append({
                    "id":               dedupe_key,
                    "reg_hive":         hive_label[hive],
                    "reg_path":         f"{path}\\{sub_name}",
                    "name":             name,
                    "version":          version,
                    "publisher":        publisher,
                    "install_date":     install_date,
                    "install_location": install_loc,
                    "display_icon":     display_icon,
                    "size_kb":          int(size_bytes / 1024) if size_bytes else 0,
                    "size_fmt":         fmt_size(size_bytes) if size_bytes else "—",
                    "size_bytes":       size_bytes,
                    "size_source":      "real" if (deep and install_loc) else "estimated",
                    "uninstall_string": uninstall,
                    "quiet_uninstall":  quiet_unins,
                    "broken":           broken,
                    "category":         _categorize_app(name, publisher),
                    "last_used":        last_used_iso,
                    "launch_count":     launch_count,
                    "winget_id":        winget_id,
                    "extra_sources":    extra_sources,
                    "url_about":        url_about,
                    "url_update":       url_update,
                    "help_link":        help_link,
                    "exe_path":         exe_path,
                })
            finally:
                winreg.CloseKey(sub)

        winreg.CloseKey(key)

    # Tri par nom (par défaut)
    apps.sort(key=lambda x: x["name"].lower())
    return apps


def launch_uninstaller(uninstall_string, silent=False, winget_id="", quiet_uninstall=""):
    """Lance le désinstalleur d'une app installée.

    Préférence en mode silent :
    1. winget uninstall (si winget_id fourni)
    2. QuietUninstallString (si présent dans le registre)
    3. UninstallString avec heuristique (/S, /SILENT, /VERYSILENT, /quiet)
    Fallback : ShellExecuteW normal (GUI).
    """
    import shlex

    if silent and winget_id:
        try:
            r = subprocess.run(
                ["winget", "uninstall", "--id", winget_id, "--silent",
                 "--accept-source-agreements", "--disable-interactivity"],
                capture_output=True, timeout=300, creationflags=0x08000000,
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass

    if silent and quiet_uninstall:
        try:
            subprocess.Popen(quiet_uninstall, shell=True)
            return True
        except Exception:
            pass

    if silent and uninstall_string:
        # Heuristique : détecter le type d'installeur et ajouter le flag silencieux
        cmd = uninstall_string.strip()
        lower = cmd.lower()
        silent_cmd = None
        if "msiexec" in lower:
            # Remplace /i ou /x par /x /quiet /norestart
            silent_cmd = cmd.replace("/I", "/x").replace("/i", "/x")
            if "/quiet" not in lower and "/qn" not in lower:
                silent_cmd += " /quiet /norestart"
        elif "unins" in lower:  # Inno Setup
            silent_cmd = cmd + " /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
        else:  # NSIS et génériques
            silent_cmd = cmd + " /S"
        try:
            subprocess.Popen(silent_cmd, shell=True)
            return True
        except Exception:
            pass

    # GUI fallback
    try:
        parts = shlex.split(uninstall_string, posix=False)
        exe   = parts[0].strip('"').strip("'")
        args  = " ".join(parts[1:]) if len(parts) > 1 else None
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, args, None, 1)
        return int(ret) > 32
    except Exception:
        try:
            subprocess.Popen(uninstall_string, shell=True)
            return True
        except Exception:
            return False


def remove_uninstall_registry_entry(reg_hive, reg_path):
    """Supprime une entrée orpheline des clés Uninstall (pour les apps 'broken').

    reg_hive : "HKLM" | "HKCU"
    reg_path : chemin complet sous la hive (ex: SOFTWARE\\...\\Uninstall\\MyApp)
    """
    hive = winreg.HKEY_LOCAL_MACHINE if reg_hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        winreg.DeleteKey(hive, reg_path)
        return True, None
    except PermissionError:
        return False, "Droits administrateur requis"
    except FileNotFoundError:
        return False, "Entrée introuvable"
    except Exception as e:
        return False, str(e)


def find_app_residuals(app_name, install_location=""):
    """Cherche les résidus laissés par une app après désinstallation.

    Retourne une liste de {path, size, size_fmt, type}.
    """
    residuals = []

    # Variantes du nom pour matcher les dossiers
    name_variants = set()
    base = app_name.lower()
    name_variants.add(base)
    # Retire version/marquage courants
    cleaned = re.sub(r"\s*\(.*?\)\s*", "", base).strip()
    if cleaned and cleaned != base:
        name_variants.add(cleaned)
    # Premier mot uniquement (attention aux faux positifs — on filtre plus bas)
    first_word = base.split()[0] if base.split() else ""
    if len(first_word) >= 4:
        name_variants.add(first_word)

    search_roots = [
        Path(os.environ.get("APPDATA", "")),
        Path(os.environ.get("LOCALAPPDATA", "")),
        Path(os.environ.get("PROGRAMDATA", "")),
    ]

    for root in search_roots:
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                cname = child.name.lower()
                if cname in name_variants or any(v in cname for v in name_variants if len(v) >= 4):
                    try:
                        size = get_folder_size(child)
                        residuals.append({
                            "path":     str(child),
                            "size":     size,
                            "size_fmt": fmt_size(size),
                            "type":     "folder",
                        })
                    except Exception:
                        pass
        except OSError:
            pass

    # Dossier d'installation s'il existe encore
    if install_location and Path(install_location).exists():
        try:
            size = get_folder_size(install_location)
            residuals.append({
                "path":     install_location,
                "size":     size,
                "size_fmt": fmt_size(size),
                "type":     "install_dir",
            })
        except Exception:
            pass

    return residuals


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Doublons
# ──────────────────────────────────────────────────────────────────────────────

def _hash_partial(path, size=4096):
    """Hash rapide des premiers octets — filtre 95% des candidats restants."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(size))
    return h.hexdigest()


def _hash_full(path):
    """Hash MD5 complet en lisant par blocs de 256 Ko."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(folder, min_size_kb=100, log=None):
    """
    Scanne un dossier pour trouver les fichiers en doublon.

    Stratégie en 3 phases pour minimiser les lectures disque :
      1. Collecte via os.scandir (stat() gratuit sous Windows/NTFS)
      2. Groupement par taille — élimine ~85% des fichiers sans aucune lecture
      3. Hash partiel (4 Ko) — élimine ~95% des candidats restants
      4. Hash complet en parallèle (ThreadPoolExecutor) — seulement sur les vrais candidats

    Retourne un dict {hash: [{path, size, size_fmt}]}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    min_size = min_size_kb * 1024
    folder   = Path(folder)

    # ── Phase 1 : collecte par taille via scandir (stat inclus, pas de syscall sup.) ──
    if log:
        log("Scan du dossier en cours…")
    by_size = defaultdict(list)
    scanned = 0
    stack   = [folder]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in {"$Recycle.Bin", "System Volume Information"}:
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            scanned += 1
                            if size >= min_size:
                                by_size[size].append((entry.path, size))
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

    # Ne garder que les tailles avec 2+ fichiers
    candidates = [item for files in by_size.values() if len(files) > 1 for item in files]
    eliminated = scanned - len(candidates)
    if log:
        log(f"{scanned} fichiers scannés — {eliminated} éliminés par taille ({len(candidates)} candidats)")

    if not candidates:
        if log:
            log("Aucun doublon potentiel trouvé.")
        return {}

    # ── Phase 2 : hash partiel (4 Ko) en parallèle ────────────────────────────
    if log:
        log(f"Hash rapide de {len(candidates)} candidats…")

    def safe_partial(item):
        path, size = item
        try:
            return (path, size, _hash_partial(path))
        except (OSError, PermissionError):
            return None

    workers = min(8, len(candidates))
    by_partial = defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(safe_partial, candidates):
            if result:
                path, size, h = result
                by_partial[(size, h)].append((path, size))

    # Ne garder que les groupes avec 2+ fichiers après hash partiel
    deep_candidates = [item for files in by_partial.values() if len(files) > 1 for item in files]
    if log:
        pct = round((1 - len(deep_candidates) / max(len(candidates), 1)) * 100)
        log(f"Hash partiel : {pct}% supplémentaires éliminés ({len(deep_candidates)} à vérifier)")

    if not deep_candidates:
        if log:
            log("Aucun doublon confirmé.")
        return {}

    # ── Phase 3 : hash complet en parallèle — seulement les vrais candidats ──
    if log:
        log(f"Vérification complète de {len(deep_candidates)} fichier(s)…")

    def safe_full(item):
        path, size = item
        try:
            return (path, size, _hash_full(path))
        except (OSError, PermissionError):
            return None

    hashes = defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(safe_full, deep_candidates):
            if result:
                path, size, h = result
                hashes[h].append({
                    "path":        path,
                    "size":        size,
                    "size_fmt":    fmt_size(size),
                    "needs_admin": is_admin_path(path),
                })

    # Double filtre de sûreté :
    # 1. Mêmes dossier parent obligatoire (évite runtimes/DLL partagés inter-apps)
    # 2. Tous les noms doivent collapser vers une même "base" une fois les
    #    suffixes de copie stripés — sinon c'est probablement un multi-call
    #    binary (bash/sh, vim/rvim/ex...) ou deux fichiers distincts au contenu
    #    identique mais référencés par leurs noms respectifs (openssl.cnf +
    #    openssl.cnf.dist par exemple).
    duplicates = {}
    skipped_cross = 0
    skipped_names = 0
    for h, files in hashes.items():
        if len(files) < 2:
            continue
        parents = {str(Path(f["path"]).parent) for f in files}
        if len(parents) != 1:
            skipped_cross += 1
            continue
        bases = {_strip_copy_suffix(Path(f["path"]).name) for f in files}
        if len(bases) != 1:
            skipped_names += 1
            continue
        duplicates[h] = files

    total_wasted = sum(
        sum(f["size"] for f in files[1:])
        for files in duplicates.values()
    )
    if log:
        log(f"{len(duplicates)} groupe(s) de doublons confirmés — {fmt_size(total_wasted)} récupérables.")
        if skipped_cross:
            log(f"{skipped_cross} groupe(s) inter-dossiers ignorés (risque runtimes/DLL partagés).")
        if skipped_names:
            log(f"{skipped_names} groupe(s) à noms distincts ignorés (risque multi-call binaries).")
    return duplicates


_EXT_COPY_PATTERNS = [
    re.compile(r"\.bak$", re.I),   # config.bak -> config
    re.compile(r"\.old$", re.I),   # config.old -> config
    re.compile(r"~$"),              # notes~     -> notes
]
_STEM_COPY_PATTERNS = [
    re.compile(r" \(\d+\)$"),                       # photo (1)
    re.compile(r" - Copie(?: \(\d+\))?$", re.I),    # doc - Copie, - Copie (2)
    re.compile(r" - Copy(?: \(\d+\))?$", re.I),     # doc - Copy
    re.compile(r" copy(?: \d+)?$", re.I),            # doc copy
    re.compile(r"_copy(?:_\d+)?$", re.I),            # doc_copy
]


def _strip_copy_suffix(name):
    """Retire les suffixes de copie du nom de fichier pour obtenir la 'base'.

    photo (1).jpg  -> photo.jpg
    doc - Copie.pdf -> doc.pdf
    notes~         -> notes
    config.bak     -> config
    bash.exe       -> bash.exe (inchangé)
    """
    # 1. Extensions-suffixes appliquées sur le nom complet
    for pat in _EXT_COPY_PATTERNS:
        new, n = pat.subn("", name)
        if n:
            name = new
            break
    # 2. Suffixes appliqués sur le stem (avant l'extension)
    stem = Path(name).stem
    ext = Path(name).suffix
    changed = True
    while changed:
        changed = False
        for pat in _STEM_COPY_PATTERNS:
            new_stem, n = pat.subn("", stem)
            if n and new_stem != stem:
                stem = new_stem
                changed = True
                break
    return stem + ext


def delete_duplicate_files(paths):
    """Envoie les fichiers en doublon à la corbeille Windows."""
    return _recycle_many(paths, label="Fichiers dupliqués")


def find_duplicate_folders(folder, log=None):
    """Détecte les dossiers enfants d'un même parent qui ont un contenu identique.

    Stratégie :
      1. Walk l'arbre, calcule pour chaque dossier la liste récursive (rel, size, path)
      2. Pré-filtre : groupe les dossiers frères par (count, size, liste (rel,size))
      3. Hash complet uniquement des fichiers dans les groupes candidats
      4. Confirme avec la signature complète incluant les hashes

    Retourne {"groups": [...], "total": int, "total_fmt": str}.
    """
    from concurrent.futures import ThreadPoolExecutor
    import hashlib

    root = Path(folder)
    if not root.is_dir():
        return {"groups": [], "total": 0, "total_fmt": "0 o"}

    SKIP = {"$Recycle.Bin", "System Volume Information", ".git",
            "node_modules", "__pycache__", ".venv", "venv"}

    if log:
        log("Analyse de l'arborescence…")

    # Walk : collecte tous les dossiers
    all_dirs = []
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        all_dirs.append(Path(dirpath))

    # Bottom-up : contenus récursifs par dossier
    all_dirs.sort(key=lambda p: len(p.parts), reverse=True)
    dir_content = {}  # Path -> list of (rel_path, size, abs_path)
    for d in all_dirs:
        items = []
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            sz = entry.stat(follow_symlinks=False).st_size
                            items.append((entry.name, sz, entry.path))
                        elif entry.is_dir(follow_symlinks=False):
                            if entry.name in SKIP:
                                continue
                            sub = Path(entry.path)
                            sub_items = dir_content.get(sub, [])
                            for rel, sz, path in sub_items:
                                items.append((f"{entry.name}/{rel}", sz, path))
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass
        dir_content[d] = items

    # Pré-filtre : groupe les dossiers par (parent, count, total_size, signature rapide)
    sibling_groups = defaultdict(list)
    for d in all_dirs:
        if d == root:
            continue
        items = dir_content.get(d, [])
        if not items:
            continue
        total_size = sum(sz for _, sz, _ in items)
        if total_size == 0:
            continue
        quick_parts = tuple(sorted((rel, sz) for rel, sz, _ in items))
        key = (str(d.parent), len(items), total_size, quick_parts)
        sibling_groups[key].append(d)

    candidates = [dirs for dirs in sibling_groups.values() if len(dirs) > 1]
    if not candidates:
        if log:
            log("Aucun dossier dupliqué trouvé.")
        return {"groups": [], "total": 0, "total_fmt": "0 o"}

    if log:
        log(f"{len(candidates)} groupe(s) candidats — vérification par hash…")

    # Collecte tous les fichiers à hasher
    files_to_hash = set()
    for group in candidates:
        for d in group:
            for _, _, path in dir_content[d]:
                files_to_hash.add(path)

    hash_cache = {}

    def _hf(p):
        try:
            return p, _hash_full(p)
        except (OSError, PermissionError):
            return p, None

    workers = min(8, max(1, len(files_to_hash)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for p, h in pool.map(_hf, files_to_hash):
            hash_cache[p] = h

    # Regroupe par signature complète (incluant les hashes)
    duplicate_groups = []
    total_wasted = 0
    skipped_names = 0
    for group in candidates:
        by_full = defaultdict(list)
        for d in group:
            full_parts = tuple(sorted(
                (rel, sz, hash_cache.get(path, ""))
                for rel, sz, path in dir_content[d]
            ))
            sig = hashlib.md5(repr(full_parts).encode("utf-8", errors="replace")).hexdigest()
            by_full[sig].append(d)

        for sig, dirs in by_full.items():
            if len(dirs) < 2:
                continue
            # Filtre sûreté : les noms de dossiers doivent collapser vers la
            # même base après strip des suffixes de copie (évite les cas type
            # nb/no qui sont des locales distinctes au contenu identique).
            bases = {_strip_copy_suffix(d.name) for d in dirs}
            if len(bases) != 1:
                skipped_names += 1
                continue
            size = sum(sz for _, sz, _ in dir_content[dirs[0]])
            count = len(dir_content[dirs[0]])
            total_wasted += size * (len(dirs) - 1)
            duplicate_groups.append({
                "folders": [{
                    "path": str(d),
                    "size": size,
                    "size_fmt": fmt_size(size),
                    "file_count": count,
                    "needs_admin": is_admin_path(str(d)),
                } for d in sorted(dirs, key=str)],
                "size": size,
                "size_fmt": fmt_size(size),
                "file_count": count,
            })

    duplicate_groups.sort(key=lambda g: -g["size"])

    if log:
        log(f"{len(duplicate_groups)} dossier(s) dupliqué(s) — {fmt_size(total_wasted)} récupérables.")
        if skipped_names:
            log(f"{skipped_names} groupe(s) à noms distincts ignorés (risque locales/versions).")

    return {
        "groups": duplicate_groups,
        "total": total_wasted,
        "total_fmt": fmt_size(total_wasted),
    }


def delete_duplicate_folders(paths):
    """Envoie les dossiers dupliqués à la corbeille Windows."""
    return _recycle_many([p for p in paths if Path(p).is_dir()], label="Dossiers dupliqués")


# ──────────────────────────────────────────────────────────────────────────────
# Registre Windows — nettoyeur
# ──────────────────────────────────────────────────────────────────────────────

def scan_registry(log=None):
    """
    Analyse le registre pour détecter les entrées orphelines (valeurs seulement).
    Retourne une liste de dicts {id, category, hive, key, value_name, description}.
    """
    issues = []

    def _log(msg):
        if log: log(msg)

    # 1. DLL partagées manquantes
    _log("  Analyse des DLL partagées…")
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Windows\CurrentVersion\SharedDLLs",
                           0, winreg.KEY_READ)
        i = 0
        while True:
            try:
                name, _, _ = winreg.EnumValue(k, i)
                if name and not Path(name.strip('"')).exists():
                    issues.append({
                        "id": f"shareddll_{i}", "category": "DLL partagées",
                        "hive": "HKLM",
                        "key": r"SOFTWARE\Microsoft\Windows\CurrentVersion\SharedDLLs",
                        "value_name": name,
                        "description": f"Fichier introuvable : {name}",
                    })
                i += 1
            except OSError:
                break
        winreg.CloseKey(k)
    except OSError:
        pass

    # 2. App Paths invalides (feuilles sans sous-clés)
    _log("  Analyse des chemins d'applications…")
    base_ap = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_ap, 0, winreg.KEY_READ)
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(k, i)
                sub = winreg.OpenKey(k, sub_name, 0, winreg.KEY_READ)
                try:
                    path_val, _ = winreg.QueryValueEx(sub, "")
                    cleaned = os.path.expandvars(path_val.strip().strip('"'))
                    if cleaned and not Path(cleaned).exists():
                        issues.append({
                            "id": f"apppath_{i}", "category": "Chemins d'applications",
                            "hive": "HKLM", "key": f"{base_ap}\\{sub_name}",
                            "value_name": "__DELETE_KEY__",
                            "description": f"{sub_name} → introuvable : {cleaned}",
                        })
                except OSError:
                    pass
                winreg.CloseKey(sub)
                i += 1
            except OSError:
                break
        winreg.CloseKey(k)
    except OSError:
        pass

    # 3. MUICache — applications manquantes
    _log("  Analyse du cache d'interface…")
    muicache_path = r"SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\Shell\MUICache"
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, muicache_path, 0, winreg.KEY_READ)
        i = 0
        while True:
            try:
                name, _, _ = winreg.EnumValue(k, i)
                if name and not name.startswith("@") and ":" in name:
                    path_part = os.path.expandvars(name.split(",")[0].split("|")[0].strip())
                    if path_part and not Path(path_part).exists():
                        issues.append({
                            "id": f"muicache_{i}", "category": "Cache interface",
                            "hive": "HKCU", "key": muicache_path,
                            "value_name": name,
                            "description": f"Application introuvable : {path_part}",
                        })
                i += 1
            except OSError:
                break
        winreg.CloseKey(k)
    except OSError:
        pass

    _log(f"  {len(issues)} problème(s) trouvé(s).")
    return issues


def fix_registry_issues(issues, log=None):
    """Supprime les valeurs/clés de registre sélectionnées. Retourne (fixed, errors)."""
    hive_map = {
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKCR": winreg.HKEY_CLASSES_ROOT,
    }
    fixed, errors = 0, []

    for issue in issues:
        hive     = hive_map.get(issue.get("hive", "HKLM"))
        key_path = issue.get("key", "")
        val_name = issue.get("value_name")
        try:
            if val_name == "__DELETE_KEY__":
                parent = "\\".join(key_path.split("\\")[:-1])
                leaf   = key_path.split("\\")[-1]
                pk = winreg.OpenKey(hive, parent, 0, winreg.KEY_ALL_ACCESS)
                winreg.DeleteKey(pk, leaf)
                winreg.CloseKey(pk)
            else:
                k = winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(k, val_name)
                winreg.CloseKey(k)
            fixed += 1
            if log: log(f"  Supprimé : {issue.get('description', '')[:80]}")
        except OSError as e:
            errors.append(str(e))
            if log: log(f"  Erreur : {e}")

    return fixed, errors


# ──────────────────────────────────────────────────────────────────────────────
# Extensions navigateurs
# ──────────────────────────────────────────────────────────────────────────────

def get_browser_extensions():
    """
    Retourne {browser: [{id, name, version, description, profile, path}]}.
    Supporte Chrome, Edge, Brave (Chromium) et Firefox.
    """
    local   = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    result  = {}

    chromium = {
        "Chrome": local / "Google"         / "Chrome"         / "User Data",
        "Edge":   local / "Microsoft"      / "Edge"           / "User Data",
        "Brave":  local / "BraveSoftware"  / "Brave-Browser"  / "User Data",
    }

    for bname, user_data in chromium.items():
        if not user_data.exists():
            continue
        exts = []
        for profile in list(user_data.glob("Default")) + list(user_data.glob("Profile *")):
            ext_dir = profile / "Extensions"
            if not ext_dir.exists():
                continue
            for eid_dir in ext_dir.iterdir():
                if not eid_dir.is_dir():
                    continue
                versions = sorted([v for v in eid_dir.iterdir() if v.is_dir()], key=lambda v: v.name)
                if not versions:
                    continue
                manifest_path = versions[-1] / "manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    with open(manifest_path, encoding="utf-8", errors="ignore") as f:
                        m = json.load(f)
                    name = m.get("name", eid_dir.name)
                    # Résolution i18n __MSG_xxx__
                    if name.startswith("__MSG_"):
                        msg_key = name[6:].rstrip("_")
                        for lang in ["en", "fr"]:
                            mp = versions[-1] / "_locales" / lang / "messages.json"
                            if mp.exists():
                                try:
                                    msgs = json.loads(mp.read_text(encoding="utf-8", errors="ignore"))
                                    for k, v in msgs.items():
                                        if k.lower() == msg_key.lower():
                                            name = v.get("message", name)
                                            break
                                except Exception:
                                    pass
                                break
                    exts.append({
                        "id":          eid_dir.name,
                        "name":        name,
                        "version":     m.get("version", "?"),
                        "description": (m.get("description") or "")[:100],
                        "profile":     profile.name,
                        "path":        str(eid_dir),
                    })
                except Exception:
                    pass
        if exts:
            exts.sort(key=lambda e: e["name"].lower())
            result[bname] = exts

    # Firefox
    ff_base = appdata / "Mozilla" / "Firefox" / "Profiles"
    if ff_base.exists():
        ff_exts = []
        for profile in ff_base.iterdir():
            ext_json = profile / "extensions.json"
            if not ext_json.exists():
                continue
            try:
                data = json.loads(ext_json.read_text(encoding="utf-8", errors="ignore"))
                for addon in data.get("addons", []):
                    if addon.get("type") != "extension":
                        continue
                    ff_exts.append({
                        "id":          addon.get("id", ""),
                        "name":        addon.get("defaultLocale", {}).get("name", addon.get("id", "")),
                        "version":     addon.get("version", "?"),
                        "description": (addon.get("defaultLocale", {}).get("description") or "")[:100],
                        "profile":     profile.name,
                        "path":        addon.get("path", ""),
                        "enabled":     addon.get("active", True),
                    })
            except Exception:
                pass
        if ff_exts:
            ff_exts.sort(key=lambda e: e["name"].lower())
            result["Firefox"] = ff_exts

    return result


def remove_browser_extension(ext_path):
    """Supprime une extension Chromium en supprimant son dossier. Retourne (ok, error)."""
    try:
        p = Path(ext_path)
        if p.exists() and p.is_dir():
            res = send_to_recycle_bin([str(p)])
            if res.get("moved"):
                return True, None
            return False, (res.get("errors") or ["Échec de la mise en corbeille"])[0]
        return False, "Dossier introuvable"
    except Exception as e:
        return False, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# Raccourcis cassés
# ──────────────────────────────────────────────────────────────────────────────

def scan_shortcuts():
    """
    Scanne les emplacements courants (.lnk) et retourne les raccourcis
    pointant vers des cibles inexistantes.
    Retourne une liste de dicts {path, name, target, location}.
    """
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        return []

    locations = {
        "Bureau":      Path(os.path.expandvars(r"%USERPROFILE%\Desktop")),
        "Bureau (Public)": Path(os.path.expandvars(r"%PUBLIC%\Desktop")),
        "Menu Démarrer": Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")),
        "Menu Démarrer (Public)": Path(os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs")),
    }
    broken = []
    for loc_name, loc_path in locations.items():
        if not loc_path.exists():
            continue
        for lnk in loc_path.rglob("*.lnk"):
            try:
                sc     = shell.CreateShortCut(str(lnk))
                target = sc.Targetpath.strip()
                if target and not Path(target).exists():
                    broken.append({
                        "path":        str(lnk),
                        "name":        lnk.stem,
                        "target":      target,
                        "location":    loc_name,
                        "needs_admin": is_admin_path(lnk),
                    })
            except Exception:
                pass
    return broken


def delete_shortcuts(paths):
    """Envoie les raccourcis .lnk à la corbeille. Retourne (deleted, errors)."""
    _, errs = _recycle_many(paths, label="Raccourcis cassés")
    deleted = len(paths) - len(errs)
    return max(deleted, 0), errs


# ──────────────────────────────────────────────────────────────────────────────
# Grands fichiers
# ──────────────────────────────────────────────────────────────────────────────

def find_large_files(folder, min_size_bytes, log=None):
    """
    Parcourt folder récursivement via os.scandir (stat() gratuit sur NTFS)
    et retourne les fichiers >= min_size_bytes, triés par taille décroissante.
    Retourne une liste de dicts {path, name, size, size_fmt}.
    """
    _SKIP = {"$Recycle.Bin", "System Volume Information"}
    results = []
    scanned = 0
    stack   = [folder]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in _SKIP:
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            scanned += 1
                            if size >= min_size_bytes:
                                results.append({
                                    "path":        entry.path,
                                    "name":        entry.name,
                                    "size":        size,
                                    "size_fmt":    fmt_size(size),
                                    "needs_admin": is_admin_path(entry.path),
                                })
                    except (PermissionError, OSError):
                        pass
        except (PermissionError, OSError):
            pass
    if log:
        log(f"Grands fichiers — {len(results)} fichier(s) trouvé(s) sur {scanned} analysés")
    return sorted(results, key=lambda x: x["size"], reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# Dossiers vides
# ──────────────────────────────────────────────────────────────────────────────

def find_empty_folders(folder, log=None):
    _SKIP = {"$Recycle.Bin", "System Volume Information", "Windows", "Program Files",
             "Program Files (x86)", "ProgramData",
             "Default", "Public",          # profils Windows protégés
             "collab_low", "Low",           # sandboxes low-integrity (Chrome/Edge)
             ".git", "node_modules"}
    results = []

    try:
        root = str(Path(folder).resolve())
    except Exception:
        return []

    def _walk(path):
        # Retourne True si le dossier est entièrement vide (récursivement)
        has_file = False
        all_subs_empty = True
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            has_file = True
                        elif entry.is_dir(follow_symlinks=False):
                            if entry.name in _SKIP:
                                all_subs_empty = False
                                continue
                            # Ignore les points de jonction NTFS
                            try:
                                if entry.stat(follow_symlinks=False).st_file_attributes & 0x400:
                                    all_subs_empty = False
                                    continue
                            except (OSError, AttributeError):
                                pass
                            if not _walk(entry.path):
                                all_subs_empty = False
                    except (OSError, PermissionError):
                        all_subs_empty = False
        except (OSError, PermissionError):
            return False

        is_empty = not has_file and all_subs_empty
        if is_empty and path != root:
            results.append({"path": path, "name": os.path.basename(path), "needs_admin": is_admin_path(path)})
        return is_empty

    try:
        _walk(root)
    except RecursionError:
        pass

    results.sort(key=lambda x: x["path"])
    if log:
        log(f"Dossiers vides — {len(results)} trouvé(s)")
    return results


def delete_empty_folders(paths):
    """Envoie les dossiers vides à la corbeille. Retourne (deleted, errors)."""
    sorted_paths = sorted(paths, key=lambda x: x.count(os.sep), reverse=True)
    _, errs = _recycle_many(sorted_paths, label="Dossiers vides")
    deleted = len(sorted_paths) - len(errs)
    return max(deleted, 0), errs


# ──────────────────────────────────────────────────────────────────────────────
# Dossiers orphelins
# ──────────────────────────────────────────────────────────────────────────────

_ORPHAN_SCAN_ROOTS = [
    # AppData\Local exclu : trop de faux positifs (profils Chrome, caches, etc.)
    Path(os.environ.get("ProgramFiles",      r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
]

_ORPHAN_SYSTEM_SKIP = {
    # Dossiers systeme Windows
    "common files", "microsoft", "microsoft.net", "windows", "windows nt",
    "windowsapps", "windowspowershell", "windows defender",
    "windows media player", "windows photo viewer", "windows mail",
    "windows sidebar", "windows journal", "windows kits",
    "windows portable devices", "windows security",
    # Navigateurs et runtimes connus
    "internet explorer", "mozilla firefox", "google", "microsoft edge",
    # Outils developpeur
    "packaged_programs", "reference assemblies", "dotnet",
    "iis express", "iis", "msbuild", "nuget", "nodejs",
    "mingw", "mingw64", "git", "cmake", "llvm",
    # Dossiers perso
    "desktop", "documents", "downloads",
    # Composants systeme divers
    "vb", "vbs", "uninstall information",
}


def find_orphan_folders(log=None):
    """
    Détecte les dossiers dans Program Files / AppData qui n'ont plus
    d'entrée correspondante dans le registre Uninstall.
    Retourne une liste de dicts {path, name, size, size_fmt}.
    """

    # 1. Collecte toutes les InstallLocation connues depuis le registre
    known_locations: set[str] = set()
    known_names: set[str] = set()

    uninstall_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, reg_path in uninstall_paths:
        try:
            key = winreg.OpenKey(hive, reg_path)
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, i)
                    sub = winreg.OpenKey(key, sub_name)
                    def _val(k):
                        try: return str(winreg.QueryValueEx(sub, k)[0]).strip()
                        except: return ""
                    loc = _val("InstallLocation").rstrip("\\/").lower()
                    if loc:
                        known_locations.add(loc)
                        # Ajoute aussi les parents directs (cas des sous-dossiers)
                        known_locations.add(str(Path(loc).parent).lower())
                    name = _val("DisplayName").lower()
                    if name:
                        known_names.add(name)
                    winreg.CloseKey(sub)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass

    if log:
        log(f"Registre : {len(known_locations)} emplacements connus, {len(known_names)} noms connus")

    # 2. Parcourt les racines et détecte les dossiers orphelins
    orphans = []
    for root in _ORPHAN_SCAN_ROOTS:
        if not root.exists():
            continue
        try:
            for entry in os.scandir(root):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                name_lower = entry.name.lower()
                if name_lower in _ORPHAN_SYSTEM_SKIP:
                    continue
                path_lower = entry.path.lower()

                # Vérifie si ce dossier (ou un parent) est dans les emplacements connus
                matched = path_lower in known_locations
                if not matched:
                    # Correspondance stricte : le nom du dossier doit être contenu dans un
                    # nom d'app (pas l'inverse) avec une longueur minimale pour éviter les
                    # faux positifs sur des mots courants (ex. "git" dans "github desktop")
                    matched = any(
                        name_lower in app_name
                        for app_name in known_names
                        if len(name_lower) >= 5 and len(app_name) >= 5
                    )
                if not matched:
                    size = get_folder_size(entry.path)
                    if size > 0:   # ignore les dossiers vides (déjà gérés par find_empty_folders)
                        orphans.append({
                            "path":     entry.path,
                            "name":     entry.name,
                            "size":     size,
                            "size_fmt": fmt_size(size),
                        })
        except (PermissionError, OSError):
            pass

    orphans.sort(key=lambda x: x["size"], reverse=True)
    if log:
        log(f"Dossiers orphelins — {len(orphans)} candidat(s) trouvé(s)")
    return orphans


def delete_orphan_folders(paths):
    """Envoie les dossiers orphelins à la corbeille Windows. Retourne (deleted, errors)."""
    valid = [p for p in paths if Path(p).exists()]
    missing = [f"{p}: dossier introuvable" for p in paths if not Path(p).exists()]
    _, errs = _recycle_many(valid, label="Dossiers orphelins")
    deleted = len(valid) - len(errs)
    return max(deleted, 0), missing + errs


# ──────────────────────────────────────────────────────────────────────────────
# Points de restauration
# ──────────────────────────────────────────────────────────────────────────────

def list_restore_points():
    """
    Liste les points de restauration Windows via CIM (compatible PS 5.1 et PS 7).
    Retourne {"points": [...], "requires_admin": bool, "error": str|None}.
    """
    # Normalise la date en "yyyyMMddHHmmss" quel que soit le format PS (WMI string ou DateTime)
    ps_cmd = (
        "Get-CimInstance -Namespace root\\default -ClassName SystemRestore | "
        "Select-Object SequenceNumber, Description, "
        "@{N='CT';E={if($_.CreationTime -is [datetime])"
        "{$_.CreationTime.ToString('yyyyMMddHHmmss')}"
        "else{$_.CreationTime -replace '[^0-9].*',''}}} | "
        "ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, timeout=10, creationflags=0x08000000
        )
        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        if r.returncode != 0:
            return {"points": [], "requires_admin": True, "error": None}
        if not stdout or stdout == "null":
            return {"points": [], "requires_admin": False, "error": None}
        from datetime import datetime
        raw = json.loads(stdout)
        if isinstance(raw, dict):
            raw = [raw]
        points = []
        for p in raw:
            date_str = str(p.get("CT", ""))[:14]
            try:
                date_fmt = datetime.strptime(date_str, "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M")
            except Exception:
                date_fmt = date_str
            points.append({
                "id":          int(p.get("SequenceNumber", 0)),
                "description": p.get("Description", "—"),
                "date":        date_fmt,
            })
        return {"points": sorted(points, key=lambda x: x["id"], reverse=True),
                "requires_admin": False, "error": None}
    except FileNotFoundError:
        return {"points": [], "requires_admin": False, "error": "PowerShell introuvable"}
    except Exception as e:
        return {"points": [], "requires_admin": False, "error": str(e)}


def delete_restore_points(ids):
    """
    Supprime les points de restauration via SRRemoveRestorePoint (srclient.dll).
    Retourne (deleted, error).
    """
    if not ids:
        return 0, "Aucun identifiant fourni"
    try:
        import ctypes
        srclient = ctypes.windll.LoadLibrary("srclient.dll")
        deleted = 0
        for seq in ids:
            ret = srclient.SRRemoveRestorePoint(int(seq))
            if ret == 0:
                deleted += 1
        if deleted == 0:
            return 0, "Aucun point supprimé — droits administrateur requis ou identifiants invalides."
        return deleted, None
    except OSError:
        return 0, "srclient.dll introuvable (Windows requis)."
    except Exception as e:
        return 0, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# État S.M.A.R.T. des disques
# ──────────────────────────────────────────────────────────────────────────────

def get_disk_smart():
    """
    Récupère l'état S.M.A.R.T. des disques physiques via PowerShell Get-PhysicalDisk.
    Retourne une liste de dicts {model, size, size_fmt, status, healthy}.
    """
    disks = []
    try:
        data = _ps_json(
            "Get-PhysicalDisk | Select-Object FriendlyName,Size,HealthStatus | ConvertTo-Json -Compress",
            timeout=8,
        )
        for d in data:
            status = str(d.get("HealthStatus") or "").strip()
            try:
                size = int(d.get("Size") or 0)
            except (TypeError, ValueError):
                size = 0
            disks.append({
                "model":    str(d.get("FriendlyName") or "").strip(),
                "size":     size,
                "size_fmt": fmt_size(size),
                "status":   status or "Unknown",
                "healthy":  status.lower() in ("healthy", "intègre", "int\ufffdgre", "en bon état", "0"),
            })
    except Exception:
        pass
    return disks


# ──────────────────────────────────────────────────────────────────────────────
# Mises à jour logicielles (winget)
# ──────────────────────────────────────────────────────────────────────────────

_WINDOWS_TWEAKS = [
    # Barre des tâches
    # Note : les tweaks "widgets" (TaskbarDa) et "news_interests"
    # (ShellFeedsTaskbarViewMode) ont été retirés car Windows 11 24H2+ a
    # verrouillé ces valeurs du registre au niveau Microsoft. Aucune méthode
    # (HKCU, HKLM, reg.exe, PowerShell, même en admin) ne permet de les modifier.
    # Un bouton "Ouvrir les paramètres Widgets" est exposé à la place.
    {"id": "chat_teams", "label": "Chat Teams", "desc": "Icône bulle Teams dans la barre des tâches",
     "group": "taskbar", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "TaskbarMn", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "task_view", "label": "Bouton Task View", "desc": "Bouton timeline / bureaux virtuels dans la barre des tâches",
     "group": "taskbar", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "ShowTaskViewButton", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "search_box", "label": "Barre de recherche large", "desc": "Grande barre de recherche → icône uniquement",
     "group": "taskbar", "path": r"Software\Microsoft\Windows\CurrentVersion\Search",
     "name": "SearchboxTaskbarMode", "on_val": 2, "off_val": 1, "default_on": True},
    {"id": "taskbar_center", "label": "Barre des tâches centrée (style W11)", "desc": "Icônes centrées. OFF pour les aligner à gauche comme Windows 10",
     "group": "taskbar", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "TaskbarAl", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "hide_seconds_clock", "label": "Masquer les secondes dans l'horloge", "desc": "État par défaut. OFF pour afficher les secondes en continu (+CPU)",
     "group": "taskbar", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "ShowSecondsInSystemClock", "on_val": 0, "off_val": 1, "default_on": True},

    # Recherche
    {"id": "bing_search", "label": "Suggestions Bing dans le menu Démarrer", "desc": "Recherches web Bing qui polluent les résultats locaux",
     "group": "search", "path": r"Software\Policies\Microsoft\Windows\Explorer",
     "name": "DisableSearchBoxSuggestions", "on_val": 0, "off_val": 1, "default_on": True},
    {"id": "cortana", "label": "Cortana", "desc": "Assistant vocal Microsoft",
     "group": "search", "path": r"Software\Microsoft\Windows\CurrentVersion\Search",
     "name": "BingSearchEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "search_highlights", "label": "Tendances de recherche", "desc": "Icône globe avec trendings du jour / événements historiques dans la barre de recherche",
     "group": "search", "path": r"Software\Microsoft\Windows\CurrentVersion\SearchSettings",
     "name": "IsDynamicSearchBoxEnabled", "on_val": 1, "off_val": 0, "default_on": True},

    # IA & contenus imposés ("Winslop")
    {"id": "copilot", "label": "Copilot Windows", "desc": "Assistant IA Microsoft qui tourne en arrière-plan et consomme RAM/CPU",
     "group": "ai", "path": r"Software\Policies\Microsoft\Windows\WindowsCopilot",
     "name": "TurnOffWindowsCopilot", "on_val": 0, "off_val": 1, "default_on": True},
    {"id": "copilot_button", "label": "Bouton Copilot dans la barre des tâches", "desc": "Icône Copilot à côté de l'horloge",
     "group": "ai", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "ShowCopilotButton", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "edge_startup_boost", "label": "Préchargement Edge au démarrage", "desc": "Edge se charge en mémoire dès que Windows démarre",
     "group": "ai", "path": r"Software\Policies\Microsoft\Edge",
     "name": "StartupBoostEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "edge_background", "label": "Edge en arrière-plan après fermeture", "desc": "Edge continue de tourner en tâche de fond même après avoir fermé toutes les fenêtres",
     "group": "ai", "path": r"Software\Policies\Microsoft\Edge",
     "name": "BackgroundModeEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "ad_id", "label": "Identifiant publicitaire", "desc": "ID unique utilisé par les apps pour te pister entre usages",
     "group": "ai", "path": r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo",
     "name": "Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "tailored_experiences", "label": "Expériences personnalisées Microsoft", "desc": "Pubs et suggestions basées sur les données diagnostiques remontées par Windows",
     "group": "ai", "path": r"Software\Microsoft\Windows\CurrentVersion\Privacy",
     "name": "TailoredExperiencesWithDiagnosticDataEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "app_launch_tracking", "label": "Suivi des applications lancées", "desc": "Historique des apps pour les suggestions du menu Démarrer",
     "group": "ai", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "Start_TrackProgs", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "notepad_ai", "label": "IA dans le Bloc-notes", "desc": "Rewrite et suggestions IA dans Notepad",
     "group": "ai", "path": r"Software\Microsoft\Notepad",
     "name": "CoCreatorDisabled", "on_val": 0, "off_val": 1, "default_on": True},
    {"id": "edge_hub_sidebar", "label": "Edge — barre latérale Copilot/Outils", "desc": "Sidebar à droite dans Edge avec Copilot, jeux, outils",
     "group": "ai", "path": r"Software\Policies\Microsoft\Edge",
     "name": "HubsSidebarEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "edge_shopping", "label": "Edge — assistant d'achat", "desc": "Suggestions de coupons et comparaisons de prix intégrées à Edge",
     "group": "ai", "path": r"Software\Policies\Microsoft\Edge",
     "name": "EdgeShoppingAssistantEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "edge_personalization_reporting", "label": "Edge — ciblage publicitaire", "desc": "Personnalisation des pubs basée sur ton historique Edge",
     "group": "ai", "path": r"Software\Policies\Microsoft\Edge",
     "name": "PersonalizationReportingEnabled", "on_val": 1, "off_val": 0, "default_on": True},

    # Menu Démarrer & pubs
    {"id": "silent_apps", "label": "Installations silencieuses d'apps", "desc": "Candy Crush, TikTok et autres installés automatiquement",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SilentInstalledAppsEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "start_suggestions", "label": "Suggestions dans le menu Démarrer", "desc": "Apps suggérées dans la tuile du menu Démarrer",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SystemPaneSuggestionsEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "settings_suggestions", "label": "Contenu suggéré dans Paramètres", "desc": "Pubs et astuces dans l'application Paramètres",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SubscribedContent-338393Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "start_recommended", "label": "Fichiers recommandés dans le menu Démarrer", "desc": "Section « Recommandé » qui affiche vos fichiers et apps récents (Windows 11)",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "Start_TrackDocs", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "start_account_notifs", "label": "Notifications de compte dans Démarrer", "desc": "Bannières OneDrive / compte Microsoft dans le menu Démarrer",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "Start_AccountNotifications", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "start_irisxp", "label": "Suggestions aléatoires Démarrer (IrisXP)", "desc": "Promos rotatives dans le menu Démarrer",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SubscribedContent-338388Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "preinstalled_apps", "label": "Pré-installation d'apps après mises à jour", "desc": "Empêche Windows de recharger Candy Crush etc. à chaque update",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "PreInstalledAppsEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "oem_preinstalled", "label": "Apps OEM pré-installées", "desc": "Empêche la réinstallation d'apps partenaires OEM",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "OEMPreInstalledAppsEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "start_iris_recommendations", "label": "Recommandations Iris personnalisées", "desc": "Suggestions dynamiques basées sur l'usage dans le menu Démarrer",
     "group": "start", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "Start_IrisRecommendations", "on_val": 1, "off_val": 0, "default_on": True},

    # Écran de verrouillage & notifications
    {"id": "lockscreen_tips", "label": "Astuces sur l'écran de verrouillage", "desc": "Texte promo Windows Spotlight sur l'écran de verrouillage",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "RotatingLockScreenOverlayEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "soft_landing", "label": "Notifications « Conseils Windows »", "desc": "Popups de suggestions et astuces pendant l'utilisation",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SoftLandingEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "spotlight_lockscreen", "label": "Windows Spotlight (écran de verrouillage)", "desc": "Images rotatives Bing et pubs sur l'écran de verrouillage",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "RotatingLockScreenEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "welcome_experience", "label": "Écran d'accueil après mises à jour", "desc": "Plus d'écran « Welcome after update » vantant les nouveautés",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SubscribedContent-310093Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "finish_setup", "label": "Invite « Terminer la configuration »", "desc": "Écran post-MAJ qui force la liaison OneDrive / compte Microsoft",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement",
     "name": "ScoobeSystemSettingEnabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "tips_tricks", "label": "Notifications « Astuces et conseils »", "desc": "Popups aléatoires avec des tips Windows",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SubscribedContent-338389Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "general_content_delivery", "label": "Livraison de contenu global", "desc": "Master switch pour bloquer toutes les pubs système Microsoft",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "ContentDeliveryAllowed", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "sub_content_338387", "label": "Astuces amusantes sur l'écran de verrouillage", "desc": "Faits insolites et astuces affichés sous l'heure (ID 338387)",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SubscribedContent-338387Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "sub_content_353694", "label": "Suggestions d'apps dans Paramètres", "desc": "Bannière d'apps suggérées dans l'app Paramètres (ID 353694)",
     "group": "lockscreen", "path": r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
     "name": "SubscribedContent-353694Enabled", "on_val": 1, "off_val": 0, "default_on": True},

    # Explorateur
    {"id": "onedrive_ads", "label": "Pubs OneDrive dans l'Explorateur", "desc": "Notifications pour passer à OneDrive premium",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "ShowSyncProviderNotifications", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "show_recent", "label": "Fichiers récents dans Accès rapide", "desc": "Historique des fichiers récemment ouverts",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer",
     "name": "ShowRecent", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "show_frequent", "label": "Dossiers fréquents dans Accès rapide", "desc": "Dossiers les plus consultés dans l'Explorateur",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer",
     "name": "ShowFrequent", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "explorer_recommended", "label": "Recommandations dans l'Explorateur", "desc": "Section « Recommandé » de la page Accueil de l'Explorateur",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "ShowRecommendations", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "hide_file_ext", "label": "Masquer les extensions des fichiers connus", "desc": "Cache .exe, .pdf, .docx. OFF recommandé (sécurité anti-phishing)",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "HideFileExt", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "hide_hidden_files", "label": "Masquer les fichiers cachés", "desc": "Cache les fichiers avec l'attribut Hidden. OFF pour les voir",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "Hidden", "on_val": 2, "off_val": 1, "default_on": True},
    {"id": "launch_to_home", "label": "Ouvrir l'Explorateur sur Accueil", "desc": "OFF pour ouvrir sur « Ce PC » (plus sobre, pas de cloud/récents)",
     "group": "explorer", "path": r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
     "name": "LaunchTo", "on_val": 2, "off_val": 1, "default_on": True},

    # Vie privée & tâches de fond
    {"id": "activity_history", "label": "Historique d'activité (Timeline)", "desc": "Publication au cloud de tes activités pour la Timeline",
     "group": "privacy", "path": r"Software\Microsoft\Windows\CurrentVersion\Privacy",
     "name": "PublishUserActivities", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "cloud_clipboard", "label": "Presse-papiers cloud", "desc": "Synchronisation cross-device du presse-papiers via le compte MS",
     "group": "privacy", "path": r"Software\Microsoft\Clipboard",
     "name": "EnableCloudClipboard", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "inking_typing", "label": "Personnalisation saisie et écriture", "desc": "Envoi de données d'écriture manuscrite et frappe clavier à Microsoft",
     "group": "privacy", "path": r"Software\Microsoft\InputPersonalization",
     "name": "RestrictImplicitTextCollection", "on_val": 0, "off_val": 1, "default_on": True},
    {"id": "online_speech", "label": "Reconnaissance vocale en ligne", "desc": "Envoi de ta voix aux serveurs Microsoft pour la dictée cloud",
     "group": "privacy", "path": r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy",
     "name": "HasAccepted", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "game_dvr", "label": "Enregistrement Game DVR en arrière-plan", "desc": "Xbox Game Bar enregistre tes parties en continu",
     "group": "privacy", "path": r"System\GameConfigStore",
     "name": "GameDVR_Enabled", "on_val": 1, "off_val": 0, "default_on": True},
    {"id": "game_bar", "label": "Xbox Game Bar", "desc": "Overlay Win+G des jeux Xbox",
     "group": "privacy", "path": r"Software\Microsoft\GameBar",
     "name": "UseNexusForGameBarEnabled", "on_val": 1, "off_val": 0, "default_on": True},
]

_TWEAK_GROUPS = [
    ("ai",         "IA & contenus imposés"),
    ("taskbar",    "Barre des tâches"),
    ("search",     "Recherche & Cortana"),
    ("start",      "Menu Démarrer & pubs"),
    ("lockscreen", "Notifications & écran de verrouillage"),
    ("explorer",   "Explorateur de fichiers"),
    ("privacy",    "Vie privée & tâches de fond"),
]

def export_tweaks_reg():
    """Génère un fichier .reg à partir de l'état actuel des tweaks désactivés.

    Retourne {"content": str, "filename": str, "count": int}.
    """
    from datetime import datetime

    # Collecte les tweaks actuellement désactivés (valeur off présente)
    lines = [
        "Windows Registry Editor Version 5.00",
        "",
        f"; Configuration OpenCleaner — exportée le {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"; Tweaks désactivés : à appliquer sur n'importe quelle machine Windows",
        "; Utilisation : double-cliquer sur le fichier → confirmer → redémarrer Windows",
        "",
    ]

    # Grouper par chemin de registre (clé)
    from collections import defaultdict
    by_path = defaultdict(list)
    count_off = 0

    for tweak in _WINDOWS_TWEAKS:
        # Vérifie l'état actuel dans le registre
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, tweak["path"])
            try:
                val, _ = winreg.QueryValueEx(k, tweak["name"])
                is_off = (val == tweak["off_val"])
            except FileNotFoundError:
                is_off = False
            k.Close()
        except FileNotFoundError:
            is_off = False

        if is_off:
            by_path[tweak["path"]].append(tweak)
            count_off += 1

    for path, tweaks in sorted(by_path.items()):
        lines.append(f"[HKEY_CURRENT_USER\\{path}]")
        for t in tweaks:
            lines.append(f'; {t["label"]} — {t["desc"]}')
            lines.append(f'"{t["name"]}"=dword:{t["off_val"]:08x}')
        lines.append("")

    if count_off == 0:
        lines.append("; Aucun tweak désactivé — ce fichier est vide.")

    content = "\r\n".join(lines)
    filename = f"opencleaner-config-{datetime.now().strftime('%Y%m%d-%H%M')}.reg"
    return {"content": content, "filename": filename, "count": count_off}


# ══════════════════════════════════════════════════════════════════════════════
# Autoruns — programmes au démarrage de Windows
# ══════════════════════════════════════════════════════════════════════════════

_AUTORUN_REG_KEYS = [
    # (hive, subkey, label de la source)
    (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Run",        "HKCU\\Run"),
    (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\RunOnce",    "HKCU\\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run",        "HKLM\\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce",    "HKLM\\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM\\WOW64\\Run"),
]

# Dossiers de démarrage Windows
_AUTORUN_FOLDERS = [
    (os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),          "Startup utilisateur"),
    (os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp"),      "Startup commun"),
]

# Clé registre utilisée par Task Manager pour tracker les entrées Run désactivées
_AUTORUN_DISABLED_KEY_USER    = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_AUTORUN_DISABLED_KEY_USER32  = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"
_AUTORUN_DISABLED_KEY_FOLDER  = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"


def _read_autorun_disabled_flags():
    """Lit les flags enabled/disabled stockés par Task Manager sous StartupApproved.

    Format : valeur binaire, premier octet = 02 (enabled) / 03 (disabled).
    """
    flags = {}  # {name: "enabled"|"disabled"}
    for subkey in [_AUTORUN_DISABLED_KEY_USER, _AUTORUN_DISABLED_KEY_USER32, _AUTORUN_DISABLED_KEY_FOLDER]:
        for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            try:
                k = winreg.OpenKey(hive, subkey)
                i = 0
                while True:
                    try:
                        name, val, typ = winreg.EnumValue(k, i)
                        if typ == winreg.REG_BINARY and val:
                            first_byte = val[0]
                            flags[name.lower()] = "disabled" if first_byte in (2, 3) and first_byte == 3 else "enabled"
                        i += 1
                    except OSError:
                        break
                k.Close()
            except (FileNotFoundError, OSError):
                pass
    return flags


def get_autorun_entries():
    """Liste les programmes qui démarrent avec Windows.

    Retourne une liste d'entrées {id, name, source, command, path, enabled, type}.
    """
    entries = []
    disabled_flags = _read_autorun_disabled_flags()

    # 1. Entrées registre
    for hive, subkey, label in _AUTORUN_REG_KEYS:
        try:
            k = winreg.OpenKey(hive, subkey)
            i = 0
            while True:
                try:
                    name, val, typ = winreg.EnumValue(k, i)
                    if not name or not val:
                        i += 1
                        continue
                    command = str(val).strip()
                    is_disabled = disabled_flags.get(name.lower()) == "disabled"
                    entries.append({
                        "id":      f"reg:{label}:{name}",
                        "name":    name,
                        "source":  label,
                        "command": command,
                        "enabled": not is_disabled,
                        "type":    "registry",
                        "hive":    "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM",
                        "subkey":  subkey,
                        "reg_name": name,
                    })
                    i += 1
                except OSError:
                    break
            k.Close()
        except (FileNotFoundError, OSError):
            pass

    # 2. Dossiers Startup (raccourcis .lnk)
    for folder_path, label in _AUTORUN_FOLDERS:
        p = Path(folder_path)
        if not p.exists():
            continue
        try:
            for f in p.iterdir():
                if f.is_file() and f.suffix.lower() in (".lnk", ".url", ".bat", ".cmd", ".exe"):
                    is_disabled = disabled_flags.get(f.name.lower()) == "disabled"
                    entries.append({
                        "id":       f"folder:{f}",
                        "name":     f.stem,
                        "source":   label,
                        "command":  str(f),
                        "enabled":  not is_disabled,
                        "type":     "folder",
                        "file_path": str(f),
                    })
        except (OSError, PermissionError):
            pass

    entries.sort(key=lambda e: (e["source"], e["name"].lower()))
    return entries


def _set_autorun_approved(subkey_path, hive, value_name, enabled):
    """Met à jour le flag StartupApproved pour activer/désactiver sans supprimer."""
    flag_bytes = bytes([0x02 if enabled else 0x03] + [0] * 11)
    try:
        try:
            k = winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            k = winreg.CreateKey(hive, subkey_path)
        with k:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_BINARY, flag_bytes)
        return True, None
    except (OSError, PermissionError) as e:
        return False, str(e)


def set_autorun_enabled(entry_id, enabled):
    """Active ou désactive une entrée autorun.

    Pour les entrées registry : modifie StartupApproved\\Run (mécanisme utilisé
    par Task Manager). Pour les entrées folder : même logique via
    StartupApproved\\StartupFolder. Cela évite de supprimer l'entrée réelle.
    """
    parts = entry_id.split(":", 2)
    if len(parts) < 2:
        return False, "ID invalide"

    kind = parts[0]
    if kind == "reg":
        # reg:HKCU\Run:NomEntry
        src_label = parts[1] if len(parts) >= 2 else ""
        name = parts[2] if len(parts) >= 3 else ""
        # Trouver la source correspondante
        match = next((k for k in _AUTORUN_REG_KEYS if k[2] == src_label), None)
        if not match:
            return False, f"Source inconnue: {src_label}"
        hive = match[0]
        if hive == winreg.HKEY_CURRENT_USER:
            approved_path = _AUTORUN_DISABLED_KEY_USER
        elif "WOW6432Node" in match[1]:
            approved_path = _AUTORUN_DISABLED_KEY_USER32
        else:
            approved_path = _AUTORUN_DISABLED_KEY_USER
        return _set_autorun_approved(approved_path, hive, name, enabled)
    elif kind == "folder":
        # folder:<file_path>
        file_path = parts[1] if len(parts) >= 2 else ""
        if len(parts) > 2:
            file_path += ":" + parts[2]
        file_name = Path(file_path).name
        return _set_autorun_approved(_AUTORUN_DISABLED_KEY_FOLDER, winreg.HKEY_CURRENT_USER, file_name, enabled)
    return False, "Type inconnu"


def export_config_snapshot():
    """Capture l'état actuel de toutes les options réversibles (tweaks, services,
    tâches, autoruns) dans un dict sérialisable."""
    import platform
    from datetime import datetime

    snapshot = {
        "version":   1,
        "created_at": datetime.now().isoformat(),
        "hostname":   platform.node(),
        "windows":    get_windows_version(),
        "tweaks":     {},
        "services":   {},
        "tasks":      {},
        "autoruns":   {},
    }

    try:
        tw = get_windows_tweaks()
        for item in tw.get("items", []):
            snapshot["tweaks"][item["id"]] = bool(item["active"])
    except Exception:
        pass

    try:
        for s in get_services_state():
            snapshot["services"][s["name"]] = bool(s.get("enabled"))
    except Exception:
        pass

    try:
        for t in get_scheduled_tasks_state():
            snapshot["tasks"][t["path"]] = bool(t.get("enabled"))
    except Exception:
        pass

    try:
        for a in get_autorun_entries():
            snapshot["autoruns"][a["id"]] = bool(a.get("enabled"))
    except Exception:
        pass

    return snapshot


def import_config_snapshot(data, sections=None):
    """Applique un snapshot. `sections` restreint aux clés choisies (liste parmi
    tweaks/services/tasks/autoruns). Retourne un résumé {applied, skipped, errors}."""
    if not isinstance(data, dict):
        return {"applied": 0, "skipped": 0, "errors": ["Snapshot invalide"]}

    sections = sections or ["tweaks", "services", "tasks", "autoruns"]
    applied = 0
    skipped = 0
    errors = []

    if "tweaks" in sections:
        for tid, active in (data.get("tweaks") or {}).items():
            try:
                res = set_windows_tweak(tid, bool(active))
                ok, err = res if isinstance(res, tuple) else (bool(res), None)
                if ok:
                    applied += 1
                else:
                    skipped += 1
                    if err:
                        errors.append(f"tweak {tid}: {err}")
            except Exception as e:
                errors.append(f"tweak {tid}: {e}")

    if "services" in sections:
        for name, enabled in (data.get("services") or {}).items():
            try:
                ok, err = set_service_enabled(name, bool(enabled))
                if ok:
                    applied += 1
                else:
                    skipped += 1
                    if err:
                        errors.append(f"service {name}: {err}")
            except Exception as e:
                errors.append(f"service {name}: {e}")

    if "tasks" in sections:
        for path, enabled in (data.get("tasks") or {}).items():
            try:
                ok, err = set_scheduled_task_enabled(path, bool(enabled))
                if ok:
                    applied += 1
                else:
                    skipped += 1
                    if err:
                        errors.append(f"task {path}: {err}")
            except Exception as e:
                errors.append(f"task {path}: {e}")

    if "autoruns" in sections:
        for entry_id, enabled in (data.get("autoruns") or {}).items():
            try:
                ok, err = set_autorun_enabled(entry_id, bool(enabled))
                if ok:
                    applied += 1
                else:
                    skipped += 1
                    if err:
                        errors.append(f"autorun {entry_id}: {err}")
            except Exception as e:
                errors.append(f"autorun {entry_id}: {e}")

    return {"applied": applied, "skipped": skipped, "errors": errors}


# ══════════════════════════════════════════════════════════════════════════════
# Mode Gaming — bascule rapide + snapshot réversible
# ══════════════════════════════════════════════════════════════════════════════

_GAMING_STATE_PATH = Path(__file__).parent / "gaming_mode.json"

# Services à arrêter pendant une session de jeu (tous whitelistés plus haut)
_GAMING_SERVICES_TO_STOP = [
    "SysMain",       # Superfetch — I/O disque
    "WSearch",       # Windows Search indexer
    "DiagTrack",     # Telemetry
    "WerSvc",        # Error Reporting
    "MapsBroker",    # Downloaded Maps Manager
    "RetailDemo",    # Retail Demo
]

# Plan High Performance GUID (constant Windows)
_POWER_PLAN_HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def _get_active_power_plan():
    try:
        r = subprocess.run(
            ["powercfg", "/GETACTIVESCHEME"],
            capture_output=True, timeout=5, creationflags=0x08000000,
        )
        out = _decode_output(r.stdout)
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", out, re.I)
        return m.group(1) if m else None
    except Exception:
        return None


def _set_active_power_plan(guid):
    try:
        subprocess.run(
            ["powercfg", "/SETACTIVE", guid],
            capture_output=True, timeout=5, creationflags=0x08000000,
        )
        return True
    except Exception:
        return False


def get_gaming_mode_state():
    """Retourne {enabled: bool, saved_at: str|None}."""
    if _GAMING_STATE_PATH.exists():
        try:
            with open(_GAMING_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "enabled":  bool(data.get("enabled")),
                "saved_at": data.get("saved_at"),
                "services_count": len(data.get("services_prev", {})),
            }
        except Exception:
            pass
    return {"enabled": False, "saved_at": None, "services_count": 0}


def set_gaming_mode(enabled):
    """Active/désactive le mode gaming.

    Activation :
        - Capture l'état actuel (services + power plan)
        - Arrête les services listés
        - Bascule sur High Performance
    Désactivation :
        - Restaure services dans leur état précédent
        - Restaure le plan d'alimentation
    """
    import json as _json
    from datetime import datetime

    if enabled:
        # Garde : si le mode gaming est déjà actif, ne pas écraser le snapshot d'origine
        if _GAMING_STATE_PATH.exists():
            return {"ok": False, "error": "Mode gaming déjà actif — désactivez d'abord"}

        services_prev = {}
        try:
            states = {s["name"]: s for s in get_services_state()}
            for name in _GAMING_SERVICES_TO_STOP:
                st = states.get(name)
                if st and st.get("start_type"):
                    # Stocke le start_type exact (Manual/Automatic/Disabled) — pas juste bool
                    services_prev[name] = st.get("start_type", "manual")
        except Exception:
            pass

        prev_plan = _get_active_power_plan()

        applied = 0
        errors = []
        for name in list(services_prev.keys()):
            ok, err = set_service_enabled(name, False)
            if ok:
                applied += 1
            elif err:
                errors.append(f"{name}: {err}")

        _set_active_power_plan(_POWER_PLAN_HIGH_PERF)

        state = {
            "enabled":       True,
            "saved_at":      datetime.now().isoformat(),
            "services_prev": services_prev,
            "prev_plan":     prev_plan,
        }
        with open(_GAMING_STATE_PATH, "w", encoding="utf-8") as f:
            _json.dump(state, f, indent=2)
        return {"ok": True, "applied": applied, "errors": errors}

    # Désactivation
    if not _GAMING_STATE_PATH.exists():
        return {"ok": False, "error": "Aucun état gaming à restaurer"}
    try:
        with open(_GAMING_STATE_PATH, "r", encoding="utf-8") as f:
            state = _json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"Lecture état: {e}"}

    restored = 0
    errors = []
    for name, prev_state in (state.get("services_prev") or {}).items():
        # prev_state peut être un start_type string (v2) ou un bool (v1 legacy)
        if isinstance(prev_state, bool):
            enable = prev_state  # compat v1
        else:
            enable = str(prev_state).lower() not in ("disabled", "4")
        ok, err = set_service_enabled(name, enable)
        if ok:
            restored += 1
        elif err:
            errors.append(f"{name}: {err}")

    prev_plan = state.get("prev_plan")
    if prev_plan:
        _set_active_power_plan(prev_plan)

    try:
        _GAMING_STATE_PATH.unlink()
    except Exception:
        pass

    return {"ok": True, "restored": restored, "errors": errors}


# ══════════════════════════════════════════════════════════════════════════════
# Suppression sécurisée — vers la Corbeille Windows (annulable)
# ══════════════════════════════════════════════════════════════════════════════

def send_to_recycle_bin(paths):
    """Envoie une liste de chemins à la corbeille via SHFileOperationW.

    Retourne {moved: int, failed: int, errors: [str]}.
    """
    import ctypes
    from ctypes import wintypes

    if not paths:
        return {"moved": 0, "failed": 0, "errors": []}

    # Filtrer les chemins existants
    existing = [str(Path(p)) for p in paths if Path(p).exists()]
    if not existing:
        return {"moved": 0, "failed": 0, "errors": []}

    # SHFileOperation attend une chaîne double-null-terminated
    FO_DELETE             = 0x0003
    FOF_ALLOWUNDO         = 0x0040
    FOF_NOCONFIRMATION    = 0x0010
    FOF_NOERRORUI         = 0x0400
    FOF_SILENT            = 0x0004
    FOF_NOCONFIRMMKDIR    = 0x0200

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd",          wintypes.HWND),
            ("wFunc",         wintypes.UINT),
            ("pFrom",         wintypes.LPCWSTR),
            ("pTo",           wintypes.LPCWSTR),
            ("fFlags",        ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle",     wintypes.LPCWSTR),
        ]

    buffer = "\0".join(existing) + "\0\0"

    op = SHFILEOPSTRUCTW()
    op.hwnd   = 0
    op.wFunc  = FO_DELETE
    op.pFrom  = buffer
    op.pTo    = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT | FOF_NOCONFIRMMKDIR

    try:
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as e:
        return {"moved": 0, "failed": len(existing), "errors": [str(e)]}

    # SHFileOperationW renvoie 0 en cas de succès global
    if res != 0:
        return {"moved": 0, "failed": len(existing), "errors": [f"SHFileOperation code {res}"]}

    moved = sum(1 for p in existing if not Path(p).exists())
    return {"moved": moved, "failed": len(existing) - moved, "errors": []}


def open_recycle_bin():
    """Ouvre la Corbeille Windows dans l'Explorateur."""
    try:
        subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"],
                         creationflags=0x08000000)
        return True, None
    except Exception as e:
        return False, str(e)


def get_last_cleanup_info():
    """Retourne les informations de la dernière opération de nettoyage depuis history.json."""
    hist_path = Path(__file__).parent / "history.json"
    if not hist_path.exists():
        return None
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return None
    for entry in history:
        if entry.get("kind") in ("clean", "delete"):
            return entry
    return None


def generate_global_report():
    """Génère un rapport HTML autonome résumant l'état du PC.

    Inclut : health, disques, tweaks Personnalisation, services, tâches,
    autoruns, navigateurs, mises à jour (sans scanner Windows Update qui est lent).
    """
    import platform
    from datetime import datetime
    from html import escape

    def _safe(fn, default=None):
        try:
            return fn()
        except Exception as e:
            return {"_error": str(e)} if default is None else default

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    hostname = platform.node()
    win_info = _safe(get_windows_version, {}) or {}
    disk = _safe(get_disk_info, [])
    health = _safe(get_health_data, {}) or {}
    tweaks = _safe(get_windows_tweaks, {"items": []})
    tweak_items = tweaks.get("items", []) if isinstance(tweaks, dict) else []
    services = _safe(get_services_state, [])
    tasks = _safe(get_scheduled_tasks_state, [])
    autoruns = _safe(get_autorun_entries, [])
    browsers = _safe(get_browser_data_breakdown, [])
    apps = _safe(lambda: get_installed_apps(deep=False), [])

    def _h(v):
        return escape(str(v)) if v is not None else ""

    def _section(title, body_html):
        return f'<section><h2>{_h(title)}</h2>{body_html}</section>'

    def _table(headers, rows):
        th = "".join(f"<th>{_h(h)}</th>" for h in headers)
        trs = "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
            for r in rows
        )
        return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'

    # Disques
    disk_rows = [
        [_h(d.get("device")), _h(d.get("total_fmt")),
         fmt_size(d.get("used", 0)) if d.get("used") else "—",
         _h(d.get("free_fmt")), f"{d.get('percent', 0):.0f}%"]
        for d in (disk or [])
    ]
    disk_html = _table(["Volume", "Total", "Utilisé", "Libre", "%"], disk_rows) if disk_rows else "<p>Aucun volume détecté.</p>"

    # Santé
    health_html = ""
    if isinstance(health, dict):
        cpu = health.get("cpu_percent")
        ram_pct = health.get("ram_percent")
        ram_used = health.get("ram_used_fmt") or ""
        ram_total = health.get("ram_total_fmt") or ""
        score = health.get("score")
        if score is not None:
            health_html += f"<p>Score de santé : <strong>{score}</strong>/100</p>"
        parts = []
        if cpu is not None:
            parts.append(f"CPU : <strong>{cpu}%</strong>")
        if ram_pct is not None:
            parts.append(f"RAM : <strong>{ram_pct}%</strong>" + (f" ({ram_used} / {ram_total})" if ram_used else ""))
        if disk:
            d = disk[0]
            parts.append(f"Disque : <strong>{d.get('percent', 0):.0f}%</strong> ({d.get('free_fmt', '')} libres)")
        if parts:
            health_html += "<p>" + " — ".join(parts) + "</p>"
        top_procs = (health.get("top_processes") or [])[:10]
        if top_procs:
            tp_rows = [[_h(p.get("name")), f"{p.get('cpu', 0):.1f}", f"{p.get('memory_mb', 0):.0f}"] for p in top_procs]
            health_html += _table(["Processus", "CPU %", "RAM (Mo)"], tp_rows)

    # Tweaks — actifs vs inactifs
    tw_off = sum(1 for t in tweak_items if not t.get("active"))
    tw_total = len(tweak_items)
    tw_absent = sum(1 for t in tweak_items if not t.get("present", True))
    tweak_html = f"<p><strong>{tw_off}</strong> désactivés sur <strong>{tw_total}</strong>"
    if tw_absent:
        tweak_html += f" · {tw_absent} absent(s) de ce PC"
    tweak_html += "</p>"
    tweak_rows = [
        [_h(t.get("label")),
         _h(t.get("group")),
         "Absent" if not t.get("present", True) else ("✓ Désactivé" if not t.get("active") else "— Actif"),
         f"{t.get('impact', {}).get('ram_mb', 0)} Mo" if t.get('impact', {}).get('ram_mb') else "—"]
        for t in tweak_items
    ]
    if tweak_rows:
        tweak_html += _table(["Tweak", "Groupe", "État", "RAM"], tweak_rows)

    # Services
    svc_rows = [
        [_h(s.get("label") or s.get("name")),
         _h(s.get("desc") or ""),
         "✓ Actif" if s.get("active") else "✗ Désactivé",
         f"{s.get('impact', {}).get('ram_mb', 0)} Mo" if s.get('impact', {}).get('ram_mb') else "—"]
        for s in (services or [])
    ]
    svc_html = _table(["Service", "Description", "État", "RAM"], svc_rows) if svc_rows else "<p>Pas de données.</p>"

    # Tâches planifiées
    task_rows = [
        [_h(t.get("label") or t.get("path")),
         _h(t.get("desc") or ""),
         "✓ Actif" if t.get("active") else "✗ Désactivé"]
        for t in (tasks or []) if t.get("exists")
    ]
    tasks_html = _table(["Tâche", "Description", "État"], task_rows) if task_rows else "<p>Pas de données.</p>"

    # Autoruns
    ar_enabled = sum(1 for a in autoruns if a.get("enabled"))
    ar_html = f"<p><strong>{len(autoruns)}</strong> entrées, <strong>{ar_enabled}</strong> actives.</p>"
    ar_rows = [
        [_h(a.get("name")), _h(a.get("source")), _h(a.get("command", "")[:80]),
         "✓ Actif" if a.get("enabled") else "✗ Désactivé"]
        for a in (autoruns or [])
    ]
    if ar_rows:
        ar_html += _table(["Nom", "Source", "Commande", "État"], ar_rows)

    # Applications
    apps_broken = sum(1 for a in (apps or []) if a.get("broken"))
    apps_html = f"<p><strong>{len(apps or [])}</strong> applications installées"
    if apps_broken:
        apps_html += f" · <strong>{apps_broken} cassée(s)</strong>"
    apps_html += "</p>"
    app_rows = [
        [_h(a.get("name")), _h(a.get("publisher") or "—"), _h(a.get("version") or "—"),
         _h(a.get("size_fmt") or "—"), _h(a.get("category") or "—"),
         "Cassée" if a.get("broken") else "OK"]
        for a in (apps or [])[:80]
    ]
    if app_rows:
        apps_html += _table(["Nom", "Éditeur", "Version", "Taille", "Catégorie", "État"], app_rows)

    # Browsers
    br_rows = [
        [_h(b.get("browser")), _h(b.get("profile")),
         fmt_size(sum((i.get("size") or 0) for i in (b.get("items") or [])))]
        for b in (browsers or [])
    ]
    br_html = _table(["Navigateur", "Profil", "Données"], br_rows) if br_rows else "<p>Aucun profil détecté.</p>"

    style = """
    body { font-family: 'IBM Plex Sans', -apple-system, sans-serif; max-width: 980px; margin: 30px auto; padding: 0 20px; color: #37352f; background: #fff; }
    h1 { font-size: 24px; border-bottom: 2px solid #e9e9e7; padding-bottom: 10px; }
    h2 { font-size: 16px; margin-top: 28px; color: #37352f; border-bottom: 1px solid #e9e9e7; padding-bottom: 6px; }
    .meta { color: #787774; font-size: 12px; margin-bottom: 20px; }
    table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 12px; }
    th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #f1f1ef; }
    th { background: #f7f6f3; font-weight: 600; color: #787774; text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }
    tbody tr:hover { background: #f7f6f3; }
    .summary { display: flex; gap: 10px; flex-wrap: wrap; margin: 16px 0; }
    .summary .card { flex: 1; min-width: 140px; padding: 12px 16px; background: #f7f6f3; border-radius: 6px; border: 1px solid #e9e9e7; }
    .summary .v { font-size: 20px; font-weight: 700; }
    .summary .l { font-size: 10px; color: #787774; text-transform: uppercase; letter-spacing: 0.5px; }
    @media print { body { margin: 0; } }
    """

    win_caption = win_info.get("caption") or f"Windows {win_info.get('major', '?')}"
    health_score = health.get("score") if isinstance(health, dict) else "—"

    summary_cards = f'''
    <div class="summary">
      <div class="card"><div class="v">{_h(win_caption)}</div><div class="l">Système</div></div>
      <div class="card"><div class="v">{tw_off}/{tw_total}</div><div class="l">Tweaks désactivés</div></div>
      <div class="card"><div class="v">{len(apps or [])}</div><div class="l">Applications</div></div>
      <div class="card"><div class="v">{health_score}</div><div class="l">Score santé</div></div>
      <div class="card"><div class="v">{len(autoruns)}</div><div class="l">Autoruns</div></div>
      <div class="card"><div class="v">{len(browsers)}</div><div class="l">Profils nav.</div></div>
    </div>
    '''

    body = f"""
    <h1>Rapport OpenCleaner</h1>
    <div class="meta">Généré le {_h(now_str)} — Hôte : <strong>{_h(hostname)}</strong></div>
    {summary_cards}
    {_section("Disques", disk_html)}
    {_section("Santé système", health_html or "<p>Pas de données santé.</p>")}
    {_section("Applications installées", apps_html)}
    {_section("Personnalisation Windows", tweak_html)}
    {_section("Services", svc_html)}
    {_section("Tâches planifiées", tasks_html)}
    {_section("Démarrage (autoruns)", ar_html)}
    {_section("Navigateurs", br_html)}
    """

    html = f"<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'><title>Rapport OpenCleaner — {_h(hostname)}</title><style>{style}</style></head><body>{body}</body></html>"
    return {
        "html":     html,
        "filename": f"opencleaner-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html",
    }


def run_self_check():
    """Exécute un diagnostic rapide de l'état de l'app.

    Retourne une liste de {id, label, status: ok|warn|error, detail}.
    """
    from datetime import datetime
    checks = []

    # 1. Écriture HKCU
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "PCCSelfCheck", 0, winreg.REG_DWORD, 1)
        winreg.DeleteValue(k, "PCCSelfCheck")
        k.Close()
        checks.append({"id": "hkcu", "label": "Écriture HKCU", "status": "ok", "detail": "Le registre utilisateur est accessible en écriture"})
    except Exception as e:
        checks.append({"id": "hkcu", "label": "Écriture HKCU", "status": "error", "detail": str(e)})

    # 2. Fichier baseline — tente un scan si absent
    try:
        if not _BASELINE_PATH.exists():
            _refresh_tweak_baseline()
        if _BASELINE_PATH.exists():
            size = _BASELINE_PATH.stat().st_size
            baseline = _load_tweak_baseline()
            count = len(baseline)
            if count > 0:
                checks.append({"id": "baseline", "label": "Baseline mesures", "status": "ok",
                               "detail": f"{count} processus mesuré(s) ({size} octets)"})
            else:
                mapped = list(_TWEAK_PROCESSES.keys())
                checks.append({"id": "baseline", "label": "Baseline mesures", "status": "warn",
                               "detail": f"Fichier créé mais vide — aucun des {len(mapped)} processus surveillés n'est en cours d'exécution ({', '.join(mapped[:5])}…). C'est normal si ces features sont déjà désactivées."})
        else:
            checks.append({"id": "baseline", "label": "Baseline mesures", "status": "warn",
                           "detail": "Impossible de créer le fichier baseline"})
    except Exception as e:
        checks.append({"id": "baseline", "label": "Baseline mesures", "status": "error", "detail": str(e)})

    # 3. PowerShell dispo
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            capture_output=True, timeout=5, creationflags=0x08000000,
        )
        if r.returncode == 0:
            ver = r.stdout.decode("utf-8", errors="replace").strip()
            checks.append({"id": "ps", "label": "PowerShell", "status": "ok", "detail": f"Version {ver}"})
        else:
            checks.append({"id": "ps", "label": "PowerShell", "status": "error", "detail": "powershell.exe a retourné un code non-zéro"})
    except Exception as e:
        checks.append({"id": "ps", "label": "PowerShell", "status": "error", "detail": str(e)})

    # 4. psutil (mesures live)
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        checks.append({"id": "psutil", "label": "psutil (mesures)", "status": "ok",
                       "detail": f"CPU {cpu}%, RAM {mem.percent}% utilisée"})
    except Exception as e:
        checks.append({"id": "psutil", "label": "psutil (mesures)", "status": "error", "detail": str(e)})

    # 5. Admin status
    try:
        admin = is_admin()
        checks.append({"id": "admin", "label": "Droits administrateur", "status": "ok" if admin else "warn",
                       "detail": "Mode administrateur actif" if admin else "Mode standard — services/tâches/réparation admin sont bloqués"})
    except Exception as e:
        checks.append({"id": "admin", "label": "Droits administrateur", "status": "error", "detail": str(e)})

    # 6. Version Windows
    try:
        v = get_windows_version()
        status = "ok" if v["major"] >= 10 else "warn"
        checks.append({"id": "version", "label": "Version Windows", "status": status, "detail": v["caption"]})
    except Exception as e:
        checks.append({"id": "version", "label": "Version Windows", "status": "error", "detail": str(e)})

    # 7. Disque système (espace dispo)
    try:
        total, used, free = _get_disk_space("C:\\")
        pct_free = round((free / total) * 100, 1) if total else 0
        status = "ok" if pct_free > 10 else ("warn" if pct_free > 5 else "error")
        checks.append({"id": "disk", "label": "Espace disque C:", "status": status,
                       "detail": f"{pct_free}% libre ({fmt_size(free)} / {fmt_size(total)})"})
    except Exception as e:
        checks.append({"id": "disk", "label": "Espace disque C:", "status": "warn", "detail": "non déterminable"})

    # 8. Logs OpenCleaner
    try:
        log_dir = Path(__file__).parent / "logs"
        if log_dir.exists():
            logs = list(log_dir.glob("*.log"))
            total_size = sum(f.stat().st_size for f in logs)
            checks.append({"id": "logs", "label": "Logs OpenCleaner", "status": "ok",
                           "detail": f"{len(logs)} fichier(s), {fmt_size(total_size)}"})
        else:
            checks.append({"id": "logs", "label": "Logs OpenCleaner", "status": "warn", "detail": "Dossier logs/ absent"})
    except Exception as e:
        checks.append({"id": "logs", "label": "Logs OpenCleaner", "status": "warn", "detail": str(e)})

    # Résumé
    ok_count = sum(1 for c in checks if c["status"] == "ok")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    err_count = sum(1 for c in checks if c["status"] == "error")
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "checks":    checks,
        "summary":   {"ok": ok_count, "warn": warn_count, "error": err_count, "total": len(checks)},
    }


def _get_disk_space(path):
    """Retourne (total, used, free) en octets pour un drive/path."""
    import shutil as _sh
    total, used, free = _sh.disk_usage(path)
    return total, used, free


def get_windows_version():
    """Retourne les infos de version Windows via le registre.

    Returns : {"major": 10|11, "build": int, "display_version": "25H2"|..., "caption": str}
    """
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        try:
            build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
        except Exception:
            build = 0
        try:
            display_version, _ = winreg.QueryValueEx(k, "DisplayVersion")
        except FileNotFoundError:
            display_version = ""
        try:
            ubr, _ = winreg.QueryValueEx(k, "UBR")
        except FileNotFoundError:
            ubr = 0
        k.Close()
    except Exception:
        return {"major": 11, "build": 0, "display_version": "", "caption": "Windows (version inconnue)"}

    # Windows 11 = build ≥ 22000
    major = 11 if build >= 22000 else 10
    caption = f"Windows {major}"
    if display_version:
        caption += f" {display_version}"
    caption += f" (build {build}.{ubr})" if ubr else f" (build {build})"
    return {
        "major":           major,
        "build":           build,
        "display_version": display_version,
        "caption":         caption,
    }


# Tweaks réservés à Windows 11 (absents ou inopérants sur W10)
_TWEAK_W11_ONLY = {
    "copilot",
    "copilot_button",
    "notepad_ai",
    "start_recommended",
    "start_iris_recommendations",
    "start_irisxp",
    "explorer_recommended",
    "search_highlights",
    "taskbar_center",
    "sub_content_338387",
    "sub_content_353694",
}


# Catégories d'effet (orthogonales aux groupes d'affichage)
_TWEAK_TAGS = [
    ("performance", "Performance"),
    ("telemetry",   "Télémétrie"),
    ("privacy",     "Confidentialité"),
    ("ads",         "Publicités"),
    ("cosmetic",    "Cosmétique"),
    ("security",    "Sécurité"),
]

# Mapping tweak → noms de processus Windows associés (en minuscules).
# Sert à mesurer la RAM réelle via psutil. On ne mappe que les features dont
# les processus sont clairement attribuables (pas msedge.exe qui est partagé
# avec le navigateur utilisateur, pas SearchHost.exe qui sert aussi à la
# recherche Windows générale).
_TWEAK_PROCESSES = {
    # IA / Copilot
    "copilot":              ["copilot.exe", "microsoft.copilot.native.exe", "copilotruntime.exe"],
    # Gaming
    "game_dvr":             ["broadcastdvrserver.exe", "gamesvr.exe"],
    "game_bar":             ["gamebar.exe", "gamebarft.exe", "gamebarelevatedft_plus.exe"],
    # Edge en arrière-plan (msedge.exe partagé mais les --type= de fond sont identifiables)
    "edge_startup_boost":   ["msedge.exe"],
    # Windows Search / Indexation
    "search_highlights":    ["searchhost.exe", "searchprotocolhost.exe", "searchfilterhost.exe", "searchindexer.exe"],
    # Widgets / News feed
    "widgets":              ["widgets.exe", "widgetservice.exe"],
    # Cortana legacy
    "cortana":              ["cortana.exe"],
    # OneDrive sync
    "onedrive_startup":     ["onedrive.exe"],
    # Phone Link
    "phone_link":           ["phoneexperiencehost.exe", "yourphone.exe"],
}

_BASELINE_PATH = Path(__file__).parent / "tweak_baseline.json"
_LIVE_SCAN_CACHE = {"value": None, "at": 0}
_LIVE_SCAN_TTL = 30  # secondes


def _scan_live_tweak_measurements():
    """Scanne les processus actifs et retourne {tid: {ram_mb, procs}}.

    Cache résultat pendant _LIVE_SCAN_TTL secondes pour limiter la latence.
    """
    import time
    now = time.time()
    cache = _LIVE_SCAN_CACHE
    if cache["value"] is not None and (now - cache["at"]) < _LIVE_SCAN_TTL:
        return cache["value"]

    import psutil
    by_name = {}
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            mem  = info.get("memory_info")
            if name and mem:
                by_name.setdefault(name, []).append(mem.rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    result = {}
    for tid, names in _TWEAK_PROCESSES.items():
        total_rss   = 0
        total_procs = 0
        for n in names:
            for rss in by_name.get(n.lower(), []):
                total_rss   += rss
                total_procs += 1
        if total_procs > 0:
            result[tid] = {
                "ram_mb": round(total_rss / (1024 * 1024)),
                "procs":  total_procs,
            }

    cache["value"] = result
    cache["at"]    = now
    return result


def _load_tweak_baseline():
    try:
        if _BASELINE_PATH.exists():
            return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_tweak_baseline(data):
    try:
        _BASELINE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _refresh_tweak_baseline():
    """Met à jour baseline.json avec les mesures live actuelles.

    Règle importante : on n'écrase jamais une mesure existante par 0.
    Si un process était là avant (et mesuré), sa valeur reste dans le fichier
    même si l'utilisateur l'a depuis désactivé.
    """
    from datetime import datetime
    live     = _scan_live_tweak_measurements()
    baseline = _load_tweak_baseline()
    updated  = False
    for tid, measurement in live.items():
        if measurement["ram_mb"] > 0:
            baseline[tid] = {
                "ram_mb":      measurement["ram_mb"],
                "procs":       measurement["procs"],
                "measured_at": datetime.now().isoformat(timespec="seconds"),
            }
            updated = True
    if updated:
        _save_tweak_baseline(baseline)
    return baseline


# Impact estimé par tweak : (ram_mb, processes, tags)
# Les chiffres sont des estimations moyennes basées sur des mesures publiques.
# Écrasés par les mesures live réelles si le process est matchable par
# _TWEAK_PROCESSES et actuellement actif (voir _refresh_tweak_baseline).
_TWEAK_IMPACTS = {
    # IA — impact performance majeur
    "copilot":                     (250, 2, ["performance", "privacy"]),
    "copilot_button":              (0,   0, ["cosmetic"]),
    "edge_startup_boost":          (350, 1, ["performance"]),
    "edge_background":             (150, 0, ["performance"]),
    "edge_hub_sidebar":            (0,   0, ["privacy"]),
    "edge_shopping":               (0,   0, ["privacy", "ads"]),
    "edge_personalization_reporting": (0, 0, ["telemetry"]),
    "ad_id":                       (0,   0, ["telemetry"]),
    "tailored_experiences":        (0,   0, ["telemetry"]),
    "app_launch_tracking":         (0,   0, ["telemetry"]),
    "notepad_ai":                  (0,   0, ["privacy"]),

    # Taskbar — cosmétique
    "chat_teams":                  (0,   0, ["cosmetic"]),
    "task_view":                   (0,   0, ["cosmetic"]),
    "search_box":                  (0,   0, ["cosmetic"]),
    "taskbar_center":              (0,   0, ["cosmetic"]),
    "hide_seconds_clock":          (0,   0, ["cosmetic"]),

    # Search — Cortana = vrai process en background
    "bing_search":                 (0,   0, ["privacy", "ads"]),
    "cortana":                     (120, 1, ["performance", "privacy"]),
    "search_highlights":           (0,   0, ["privacy", "ads"]),

    # Start — principalement pubs
    "silent_apps":                 (0,   0, ["ads"]),
    "start_suggestions":           (0,   0, ["ads"]),
    "settings_suggestions":        (0,   0, ["ads"]),
    "start_recommended":           (0,   0, ["privacy", "ads"]),
    "start_account_notifs":        (0,   0, ["ads"]),
    "start_irisxp":                (0,   0, ["ads"]),
    "preinstalled_apps":           (0,   0, ["ads"]),
    "oem_preinstalled":            (0,   0, ["ads"]),
    "start_iris_recommendations":  (0,   0, ["privacy", "ads"]),

    # Lockscreen — pubs et notifications
    "lockscreen_tips":             (0,   0, ["ads"]),
    "soft_landing":                (0,   0, ["ads"]),
    "spotlight_lockscreen":        (0,   0, ["ads"]),
    "welcome_experience":          (0,   0, ["ads"]),
    "finish_setup":                (0,   0, ["ads"]),
    "tips_tricks":                 (0,   0, ["ads"]),
    "general_content_delivery":    (0,   0, ["ads"]),
    "sub_content_338387":          (0,   0, ["ads"]),
    "sub_content_353694":          (0,   0, ["ads"]),

    # Explorer — cosmétique + sécurité
    "onedrive_ads":                (0,   0, ["ads"]),
    "show_recent":                 (0,   0, ["privacy", "cosmetic"]),
    "show_frequent":               (0,   0, ["privacy", "cosmetic"]),
    "explorer_recommended":        (0,   0, ["privacy", "ads"]),
    "hide_file_ext":               (0,   0, ["security", "cosmetic"]),
    "hide_hidden_files":           (0,   0, ["cosmetic"]),
    "launch_to_home":              (0,   0, ["cosmetic"]),

    # Vie privée & tâches de fond — Game Bar/DVR = vrais process
    "activity_history":            (5,   0, ["telemetry"]),
    "cloud_clipboard":             (10,  0, ["privacy"]),
    "inking_typing":               (0,   0, ["telemetry"]),
    "online_speech":               (0,   0, ["telemetry"]),
    "game_dvr":                    (80,  1, ["performance", "privacy"]),
    "game_bar":                    (100, 1, ["performance"]),
}


def _detect_feature_presence():
    """Détecte quelles features sont réellement installées sur ce PC.

    Retourne un dict {tweak_id: True/False}. Les tweaks non listés sont
    considérés comme présents (pure modification registre = toujours applicable).
    """
    presence = {}

    # Copilot — besoin du package UWP OU d'un exe
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "if (Get-AppxPackage -Name '*copilot*' -ErrorAction SilentlyContinue) {'1'} else {'0'}"],
            capture_output=True, timeout=10, creationflags=0x08000000,
        )
        has_copilot = "1" in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        has_copilot = True  # en cas de doute, ne pas greyer
    presence["copilot"] = has_copilot
    presence["copilot_button"] = has_copilot

    # Cortana
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "if (Get-AppxPackage -Name 'Microsoft.549981C3F5F10' -ErrorAction SilentlyContinue) {'1'} else {'0'}"],
            capture_output=True, timeout=10, creationflags=0x08000000,
        )
        presence["cortana"] = "1" in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        presence["cortana"] = True

    # Game Bar UWP
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "if (Get-AppxPackage -Name 'Microsoft.XboxGamingOverlay' -ErrorAction SilentlyContinue) {'1'} else {'0'}"],
            capture_output=True, timeout=10, creationflags=0x08000000,
        )
        has_gamebar = "1" in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        has_gamebar = True
    presence["game_bar"] = has_gamebar
    presence["game_dvr"] = has_gamebar  # Game DVR dépend de la Game Bar

    # OneDrive
    presence["onedrive_startup"] = bool(Path(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\OneDrive\OneDrive.exe")).exists())

    # Edge (presque toujours présent mais vérifie quand même)
    edge_exe = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    has_edge = edge_exe.exists()
    for eid in ("edge_startup_boost", "edge_background", "edge_hub_sidebar",
                "edge_shopping", "edge_personalization_reporting"):
        presence[eid] = has_edge

    # Phone Link
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "if (Get-AppxPackage -Name 'Microsoft.YourPhone' -ErrorAction SilentlyContinue) {'1'} else {'0'}"],
            capture_output=True, timeout=10, creationflags=0x08000000,
        )
        presence["phone_link"] = "1" in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        presence["phone_link"] = True

    return presence


def get_windows_tweaks():
    result = {"groups": [], "items": []}
    for gid, glabel in _TWEAK_GROUPS:
        result["groups"].append({"id": gid, "label": glabel})

    # Détection de présence des features sur ce PC
    feature_presence = _detect_feature_presence()

    # Regroupe les tweaks par path pour n'ouvrir chaque clé qu'une fois
    by_path = defaultdict(list)
    for t in _WINDOWS_TWEAKS:
        by_path[t["path"]].append(t)

    values = {}
    for path, tweaks in by_path.items():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                for t in tweaks:
                    try:
                        val, _ = winreg.QueryValueEx(k, t["name"])
                        values[t["id"]] = int(val)
                    except FileNotFoundError:
                        values[t["id"]] = None
        except (FileNotFoundError, OSError):
            for t in tweaks:
                values[t["id"]] = None

    # Rafraîchit la baseline depuis les mesures live (process actuellement en
    # mémoire). N'écrase jamais une valeur existante par 0.
    baseline = _refresh_tweak_baseline()

    for t in _WINDOWS_TWEAKS:
        current = values.get(t["id"])
        active = bool(t.get("default_on", True)) if current is None else (current == t["on_val"])
        est_ram, est_procs, tags = _TWEAK_IMPACTS.get(t["id"], (0, 0, []))

        # Priorité des sources : baseline mesurée > estimation hardcodée
        tid = t["id"]
        if tid in baseline:
            ram_mb      = baseline[tid].get("ram_mb", est_ram)
            procs       = baseline[tid].get("procs", est_procs)
            source      = "measured"
            measured_at = baseline[tid].get("measured_at")
        else:
            ram_mb      = est_ram
            procs       = est_procs
            source      = "estimate"
            measured_at = None

        min_win = 11 if tid in _TWEAK_W11_ONLY else 10

        # Présence : True si la feature existe sur ce PC (par défaut True
        # pour les tweaks purement registre sans composant spécifique)
        present = feature_presence.get(tid, True)

        result["items"].append({
            "id":    t["id"],
            "label": t["label"],
            "desc":  t["desc"],
            "group": t["group"],
            "active": active,
            "tags":   tags,
            "min_windows": min_win,
            "present": present,
            "impact": {
                "ram_mb":      ram_mb,
                "processes":   procs,
                "source":      source,
                "measured_at": measured_at,
            },
        })
    result["windows_version"] = get_windows_version()
    return result


# Presets one-click — chaque preset couvre 3 types d'objets :
#   tweaks_off    : IDs de tweaks (HKCU) à désactiver
#   services_off  : noms de services Windows à désactiver (admin requis)
#   tasks_off     : chemins de tâches planifiées à désactiver (admin requis)
# Le frontend applique les 3 via les endpoints /set-batch respectifs.
_PRESET_STANDARD_TWEAKS = [
    # Pubs et promotions
    "silent_apps", "start_suggestions", "settings_suggestions",
    "start_account_notifs", "start_irisxp", "preinstalled_apps",
    "oem_preinstalled", "start_iris_recommendations",
    "lockscreen_tips", "soft_landing", "welcome_experience",
    "finish_setup", "tips_tricks", "onedrive_ads",
    "sub_content_338387", "sub_content_353694",
    # Cosmétique peu controversé
    "copilot_button", "explorer_recommended",
    # Privacy légère
    "ad_id", "tailored_experiences",
    # Edge promos
    "edge_shopping",
]

_PRESET_AGGRESSIVE_TWEAKS = _PRESET_STANDARD_TWEAKS + [
    # Performance
    "copilot", "edge_startup_boost", "edge_background",
    "cortana", "bing_search", "search_highlights",
    "game_dvr", "game_bar",
    "start_recommended", "spotlight_lockscreen",
    "general_content_delivery", "notepad_ai",
    "app_launch_tracking", "edge_personalization_reporting",
    "edge_hub_sidebar",
    # Explorer
    "show_recent", "show_frequent",
]

_PRESET_PARANOID_TWEAKS = _PRESET_AGGRESSIVE_TWEAKS + [
    "activity_history", "cloud_clipboard",
    "inking_typing", "online_speech",
]

_TWEAK_PRESETS = {
    "standard": {
        "label": "Standard",
        "desc":  "Coupe les pubs, suggestions, notifications promo. Zéro risque.",
        "tweaks_off":   _PRESET_STANDARD_TWEAKS,
        "services_off": [],
        "tasks_off":    [],
    },
    "aggressive": {
        "label": "Agressif",
        "desc":  "Standard + Copilot, Edge boost, Cortana, Game Bar, Xbox. Gros gain RAM.",
        "tweaks_off":   _PRESET_AGGRESSIVE_TWEAKS,
        "services_off": [
            # Xbox gaming (si pas gamer)
            "XblAuthManager", "XblGameSave", "XboxNetApiSvc",
            # Legacy
            "MapsBroker", "RetailDemo", "WMPNetworkSvc", "Fax",
            "RemoteRegistry",
            # Géolocalisation
            "lfsvc",
        ],
        "tasks_off": [
            # Legacy maps
            r"\Microsoft\Windows\Maps\MapsUpdateTask",
            r"\Microsoft\Windows\Maps\MapsToastTask",
        ],
    },
    "gaming": {
        "label": "Performance Max",
        "desc":  "Désactive tout ce qui consomme des ressources en arrière-plan + plan High Performance. Idéal pour le gaming.",
        "tweaks_off":   _PRESET_AGGRESSIVE_TWEAKS,
        "services_off": [
            "SysMain", "WSearch", "DiagTrack", "WerSvc", "MapsBroker", "RetailDemo",
        ],
        "tasks_off":    [],
        "power_plan":   "high_performance",
    },
    "paranoid": {
        "label": "Paranoïaque",
        "desc":  "Agressif + toute la telemetry et data collection. Coupe DiagTrack et les tâches CEIP.",
        "tweaks_off":   _PRESET_PARANOID_TWEAKS,
        "services_off": [
            # Xbox
            "XblAuthManager", "XblGameSave", "XboxNetApiSvc",
            # Legacy
            "MapsBroker", "RetailDemo", "WMPNetworkSvc", "Fax",
            "RemoteRegistry", "lfsvc",
            # Télémétrie
            "DiagTrack", "dmwappushservice", "WerSvc",
            # Cloud sync (assume le user ne les utilise pas)
            "CDPUserSvc", "OneSyncSvc",
        ],
        "tasks_off": [
            # Télémétrie complète
            r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
            r"\Microsoft\Windows\Application Experience\ProgramDataUpdater",
            r"\Microsoft\Windows\Application Experience\PcaPatchDbTask",
            r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
            r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
            r"\Microsoft\Windows\Autochk\Proxy",
            r"\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
            r"\Microsoft\Windows\Feedback\Siuf\DmClient",
            r"\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload",
            r"\Microsoft\Windows\Windows Error Reporting\QueueReporting",
            r"\Microsoft\Windows\PushToInstall\Registration",
            # Legacy
            r"\Microsoft\Windows\Maps\MapsUpdateTask",
            r"\Microsoft\Windows\Maps\MapsToastTask",
        ],
    },
}


def get_tweak_presets():
    """Retourne les presets disponibles pour l'UI.

    Chaque preset inclut maintenant tweaks_off + services_off + tasks_off.
    Le count total est la somme des 3.
    """
    out = []
    for pid, data in _TWEAK_PRESETS.items():
        tweaks_off   = data.get("tweaks_off",   [])
        services_off = data.get("services_off", [])
        tasks_off    = data.get("tasks_off",    [])
        entry = {
            "id":           pid,
            "label":        data["label"],
            "desc":         data["desc"],
            "count":        len(tweaks_off) + len(services_off) + len(tasks_off),
            "tweaks_off":   tweaks_off,
            "services_off": services_off,
            "tasks_off":    tasks_off,
        }
        if data.get("power_plan"):
            entry["power_plan"] = data["power_plan"]
        out.append(entry)
    return out


def set_windows_tweak(tweak_id, active):
    tweak = next((t for t in _WINDOWS_TWEAKS if t["id"] == tweak_id), None)
    if not tweak:
        return False, "Tweak inconnu"
    target = tweak["on_val"] if active else tweak["off_val"]
    # 1) Essai API winreg (rapide, la majorité des clés)
    try:
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, tweak["path"], 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, tweak["path"])
        with k:
            winreg.SetValueEx(k, tweak["name"], 0, winreg.REG_DWORD, target)
        return True, None
    except (OSError, PermissionError):
        pass
    # 2) Fallback reg.exe — contourne les clés verrouillées par Explorer (TaskbarDa, ShellFeedsTaskbarViewMode…)
    try:
        r = subprocess.run(
            ["reg", "add", "HKCU\\" + tweak["path"], "/v", tweak["name"],
             "/t", "REG_DWORD", "/d", str(target), "/f"],
            capture_output=True, timeout=8, creationflags=0x08000000
        )
        if r.returncode == 0:
            return True, None
        err = _decode_output(r.stderr).strip() or "reg.exe a échoué"
        return False, err
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# Outils de réparation système
# ══════════════════════════════════════════════════════════════════════════════

# Chaque action = {id, label, desc, cmd, needs_admin, est_duration_sec}
# cmd est une liste passée à subprocess.run. Les commandes sont **curées** —
# aucune édition utilisateur possible. Les actions "longues" (SFC, DISM) sont
# exécutées via SSE pour streamer leur sortie.
_REPAIR_ACTIONS = [
    {"id": "flush_dns",          "label": "Vider le cache DNS",
     "desc": "Efface le cache de résolution DNS local (règle souvent les problèmes réseau)",
     "cmd": ["ipconfig", "/flushdns"],
     "needs_admin": False, "duration": 2, "category": "network"},

    {"id": "reset_winsock",      "label": "Réinitialiser Winsock",
     "desc": "Remet la pile de sockets Windows à zéro (règle les problèmes réseau persistants)",
     "cmd": ["netsh", "winsock", "reset"],
     "needs_admin": True, "duration": 5, "reboot_required": True, "category": "network"},

    {"id": "reset_tcpip",        "label": "Réinitialiser la pile TCP/IP",
     "desc": "Réinitialise la configuration IP de Windows",
     "cmd": ["netsh", "int", "ip", "reset"],
     "needs_admin": True, "duration": 5, "reboot_required": True, "category": "network"},

    {"id": "release_renew_ip",   "label": "Renouveler l'adresse IP",
     "desc": "Libère et renouvelle l'adresse IP DHCP",
     "cmd": ["powershell", "-NoProfile", "-Command",
             "ipconfig /release; ipconfig /renew"],
     "needs_admin": False, "duration": 10, "category": "network"},

    {"id": "wsreset",            "label": "Vider le cache du Microsoft Store",
     "desc": "Remet à zéro le Store Windows (corrige les erreurs d'installation)",
     "cmd": ["wsreset.exe", "-i"],
     "needs_admin": False, "duration": 5, "category": "store"},

    {"id": "reset_windows_update","label": "Réinitialiser Windows Update",
     "desc": "Arrête BITS/wuauserv, supprime SoftwareDistribution, redémarre les services",
     "cmd": None,  # multi-step custom
     "needs_admin": True, "duration": 15, "category": "update"},

    {"id": "rebuild_icon_cache", "label": "Reconstruire le cache d'icônes",
     "desc": "Force Windows à régénérer toutes les icônes (utile si icônes cassées)",
     "cmd": None,  # multi-step custom
     "needs_admin": False, "duration": 10, "category": "shell"},

    {"id": "sfc_scan",           "label": "Scan SFC (vérification fichiers système)",
     "desc": "Analyse et répare les fichiers système Windows corrompus. Long (~10 min)",
     "cmd": ["sfc", "/scannow"],
     "needs_admin": True, "duration": 600, "category": "system", "streaming": True},

    {"id": "dism_restore",       "label": "DISM Restore Health",
     "desc": "Répare l'image système Windows via DISM. Très long (~15-30 min)",
     "cmd": ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"],
     "needs_admin": True, "duration": 1200, "category": "system", "streaming": True},
]


def list_repair_actions():
    """Retourne la liste des actions de réparation avec métadonnées."""
    return [
        {k: v for k, v in a.items() if k != "cmd"}
        for a in _REPAIR_ACTIONS
    ]


def _run_repair_simple(cmd, timeout=60):
    """Exécute une commande simple et retourne (ok, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=0x08000000,
        )
        out = _decode_output(r.stdout).strip()
        err = _decode_output(r.stderr).strip()
        return r.returncode == 0, out, err
    except subprocess.TimeoutExpired:
        return False, "", f"Timeout après {timeout}s"
    except Exception as e:
        return False, "", str(e)


def _run_reset_windows_update():
    """Réinitialise Windows Update en plusieurs étapes."""
    steps = []
    # 1. Arrêter les services
    for svc in ["bits", "wuauserv", "appidsvc", "cryptsvc"]:
        ok, _, err = _run_repair_simple(["net", "stop", svc], timeout=15)
        steps.append(f"Arrêt {svc} : {'OK' if ok else 'déjà arrêté ou erreur'}")
    # 2. Supprimer SoftwareDistribution
    import shutil as _sh
    sd = Path(r"C:\Windows\SoftwareDistribution")
    if sd.exists():
        try:
            _sh.rmtree(sd, ignore_errors=True)
            steps.append("Suppression SoftwareDistribution : OK")
        except Exception as e:
            steps.append(f"Suppression SoftwareDistribution : échec ({e})")
    # 3. Redémarrer les services
    for svc in ["cryptsvc", "appidsvc", "wuauserv", "bits"]:
        ok, _, err = _run_repair_simple(["net", "start", svc], timeout=15)
        steps.append(f"Démarrage {svc} : {'OK' if ok else 'erreur'}")
    return steps


def _run_rebuild_icon_cache():
    """Reconstruit le cache d'icônes Windows."""
    steps = []
    # 1. Tuer explorer.exe
    _run_repair_simple(["taskkill", "/f", "/im", "explorer.exe"], timeout=5)
    steps.append("Explorer tué")
    # 2. Supprimer les fichiers de cache
    import os as _os
    localappdata = _os.environ.get("LOCALAPPDATA", "")
    targets = [
        Path(localappdata) / "IconCache.db",
        Path(localappdata) / "Microsoft" / "Windows" / "Explorer",
    ]
    batch = []
    for t in targets:
        if t.is_file():
            batch.append(str(t))
            steps.append(f"Prévu : {t.name}")
        elif t.is_dir():
            batch.extend(str(f) for f in t.glob("iconcache*"))
            batch.extend(str(f) for f in t.glob("thumbcache*"))
            steps.append(f"Nettoyé : {t.name}")
    if batch:
        _, errs = _recycle_many(batch, label="Cache icônes/miniatures")
        if errs:
            steps.append(f"{len(errs)} erreur(s) corbeille")
    # 3. Relancer explorer
    try:
        subprocess.Popen(["explorer.exe"], creationflags=0x08000000)
        steps.append("Explorer relancé")
    except Exception as e:
        steps.append(f"Erreur relance explorer : {e}")
    return steps


def run_repair_action(action_id):
    """Exécute une action de réparation (mode non-streaming).

    Retourne {"ok": bool, "output": str, "steps": list}.
    Pour les actions longues (SFC/DISM), utiliser run_repair_action_stream.
    """
    action = next((a for a in _REPAIR_ACTIONS if a["id"] == action_id), None)
    if not action:
        return {"ok": False, "output": "Action inconnue"}

    # Actions custom multi-étapes
    if action_id == "reset_windows_update":
        steps = _run_reset_windows_update()
        return {"ok": True, "output": "\n".join(steps), "steps": steps}
    if action_id == "rebuild_icon_cache":
        steps = _run_rebuild_icon_cache()
        return {"ok": True, "output": "\n".join(steps), "steps": steps}

    # Actions simples (single subprocess)
    if action.get("cmd"):
        ok, out, err = _run_repair_simple(action["cmd"], timeout=action.get("duration", 60) + 10)
        output = out if out else err
        return {"ok": ok, "output": output or ("OK" if ok else "Échec")}
    return {"ok": False, "output": "Commande non définie"}


def run_repair_action_stream(action_id):
    """Générateur pour SSE : stream l'output d'une action longue (SFC, DISM).

    Yield des événements dict {type: "log"|"done"|"error", msg: str}.
    """
    action = next((a for a in _REPAIR_ACTIONS if a["id"] == action_id), None)
    if not action or not action.get("cmd"):
        yield {"type": "error", "msg": "Action inconnue ou sans commande directe"}
        return

    yield {"type": "log", "msg": f"Lancement : {' '.join(action['cmd'])}"}
    try:
        proc = subprocess.Popen(
            action["cmd"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000,
        )
        for raw_line in proc.stdout:
            line = _decode_output(raw_line).rstrip()
            if line:
                yield {"type": "log", "msg": line}
        proc.wait()
        if proc.returncode == 0:
            yield {"type": "done", "msg": "Terminé avec succès"}
        else:
            yield {"type": "error", "msg": f"Code retour {proc.returncode}"}
    except Exception as e:
        yield {"type": "error", "msg": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# Services Windows & tâches planifiées (debloat)
# ══════════════════════════════════════════════════════════════════════════════

_WINDOWS_SERVICES_TO_DISABLE = [
    # Telemetry
    {"name": "DiagTrack",        "label": "Expérience utilisateur connectée et télémétrie",
     "desc": "Collecte et envoie les données de diagnostic à Microsoft. Gros gain RAM (40-80 Mo)",
     "category": "telemetry",    "risk": "safe", "ram_mb": 60},
    {"name": "dmwappushservice", "label": "Service de routage WAP push",
     "desc": "Route les messages push utilisés par la télémétrie",
     "category": "telemetry",    "risk": "safe", "ram_mb": 5},
    {"name": "WerSvc",           "label": "Rapport d'erreurs Windows",
     "desc": "Envoie les rapports de plantage à Microsoft",
     "category": "telemetry",    "risk": "safe", "ram_mb": 10},
    {"name": "DPS",              "label": "Stratégie de diagnostic",
     "desc": "Détection de problèmes Windows, dépendances diagnostics",
     "category": "telemetry",    "risk": "review", "ram_mb": 30},
    {"name": "PcaSvc",           "label": "Assistant Compatibilité des programmes",
     "desc": "Surveille la compatibilité des apps, remonte des données d'usage",
     "category": "telemetry",    "risk": "review", "ram_mb": 15},

    # Legacy
    {"name": "MapsBroker",       "label": "Gestionnaire de téléchargement de cartes",
     "desc": "Télécharge les cartes hors connexion de l'app Cartes",
     "category": "legacy",       "risk": "safe", "ram_mb": 15},
    {"name": "RetailDemo",       "label": "Mode démo magasin",
     "desc": "Service de démonstration en magasin, inutile en perso",
     "category": "legacy",       "risk": "safe", "ram_mb": 0},
    {"name": "WMPNetworkSvc",    "label": "Partage réseau Windows Media Player",
     "desc": "Partage DLNA/UPnP des bibliothèques WMP",
     "category": "legacy",       "risk": "safe", "ram_mb": 10},
    {"name": "Fax",              "label": "Télécopie (Fax)",
     "desc": "Service d'envoi et réception de fax via modem",
     "category": "legacy",       "risk": "safe", "ram_mb": 0},
    {"name": "RemoteRegistry",   "label": "Registre à distance",
     "desc": "Permet la modification du registre depuis une autre machine",
     "category": "privacy",      "risk": "safe", "ram_mb": 0},

    # Gaming (à désactiver si pas gamer)
    {"name": "XblAuthManager",   "label": "Xbox Live — Authentification",
     "desc": "Sign-in Xbox Live, inutile sans jeux Xbox/Game Pass",
     "category": "gaming",       "risk": "safe", "ram_mb": 10},
    {"name": "XblGameSave",      "label": "Xbox Live — Sauvegardes",
     "desc": "Synchro cloud des sauvegardes de jeux Xbox",
     "category": "gaming",       "risk": "safe", "ram_mb": 5},
    {"name": "XboxNetApiSvc",    "label": "Xbox Live — Réseau",
     "desc": "Accès réseau multijoueur pour apps Xbox",
     "category": "gaming",       "risk": "safe", "ram_mb": 5},
    {"name": "XboxGipSvc",       "label": "Accessoires Xbox",
     "desc": "Support des manettes Xbox pour l'app Accessoires",
     "category": "gaming",       "risk": "review", "ram_mb": 5},

    # Privacy / Cloud
    {"name": "lfsvc",            "label": "Géolocalisation",
     "desc": "Fournit la position géographique aux applications",
     "category": "privacy",      "risk": "safe", "ram_mb": 10},
    {"name": "CDPUserSvc",       "label": "Plateforme des appareils connectés",
     "desc": "Synchro multi-appareils via compte Microsoft (Timeline, clipboard cloud)",
     "category": "cloud_sync",   "risk": "review", "ram_mb": 25},
    {"name": "OneSyncSvc",       "label": "Hôte de synchronisation",
     "desc": "Sync Courrier/Contacts/Calendrier avec le cloud Microsoft",
     "category": "cloud_sync",   "risk": "review", "ram_mb": 20},
]


_SCHEDULED_TASKS_TO_DISABLE = [
    # Telemetry / Compatibility Appraiser
    {"path": r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
     "label": "Compatibility Appraiser",
     "desc": "Collecte les données de compatibilité applicative pour la télémétrie",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Application Experience\ProgramDataUpdater",
     "label": "ProgramDataUpdater",
     "desc": "Met à jour les données d'usage des programmes pour la télémétrie",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Application Experience\PcaPatchDbTask",
     "label": "PCA Patch DB",
     "desc": "Met à jour la base de l'assistant compatibilité",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
     "label": "CEIP Consolidator",
     "desc": "Envoie périodiquement les données CEIP à Microsoft",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
     "label": "CEIP USB",
     "desc": "Remonte les données d'usage des périphériques USB",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Autochk\Proxy",
     "label": "Autochk Proxy",
     "desc": "Collecte et envoie les données SQM d'Autochk",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
     "label": "Disk Diagnostic Data Collector",
     "desc": "Collecte les données SMART et les envoie à Microsoft",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Feedback\Siuf\DmClient",
     "label": "Feedback Siuf DmClient",
     "desc": "Remonte les données de feedback utilisateur",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload",
     "label": "Feedback Siuf Scenario",
     "desc": "Télécharge les scénarios de collecte de feedback",
     "category": "telemetry", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Windows Error Reporting\QueueReporting",
     "label": "Error Reporting Queue",
     "desc": "Envoie la file des rapports d'erreurs à Microsoft",
     "category": "telemetry", "risk": "safe"},
    # Legacy
    {"path": r"\Microsoft\Windows\Maps\MapsUpdateTask",
     "label": "Maps Update",
     "desc": "Met à jour les cartes hors connexion en arrière-plan",
     "category": "legacy", "risk": "safe"},
    {"path": r"\Microsoft\Windows\Maps\MapsToastTask",
     "label": "Maps Toast",
     "desc": "Notifications de l'application Cartes",
     "category": "legacy", "risk": "safe"},
    {"path": r"\Microsoft\Windows\PushToInstall\Registration",
     "label": "PushToInstall Registration",
     "desc": "Permet l'installation d'apps poussées depuis le Store distant",
     "category": "privacy", "risk": "safe"},
]


def get_services_state():
    """Retourne l'état des services de la liste curée via PowerShell Get-Service."""
    names = [s["name"] for s in _WINDOWS_SERVICES_TO_DISABLE]
    ps_array = ",".join(f"'{n}'" for n in names)
    ps_cmd = (
        f"$names = @({ps_array}); "
        "$result = @(); "
        "foreach ($n in $names) { "
        "  try { "
        "    $s = Get-Service -Name $n -ErrorAction Stop; "
        "    $result += [PSCustomObject]@{ "
        "      Name = $n; "
        "      Status = $s.Status.ToString(); "
        "      StartType = $s.StartType.ToString(); "
        "      Exists = $true "
        "    } "
        "  } catch { "
        "    $result += [PSCustomObject]@{ Name = $n; Exists = $false } "
        "  } "
        "}; "
        "$result | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_cmd],
            capture_output=True, timeout=15, creationflags=0x08000000,
        )
        raw = r.stdout.decode("utf-8", errors="replace").strip()
        data = json.loads(raw) if raw and raw != "null" else []
        if isinstance(data, dict):
            data = [data]
    except Exception:
        data = []

    by_name = {d.get("Name"): d for d in data}
    result = []
    for svc in _WINDOWS_SERVICES_TO_DISABLE:
        state = by_name.get(svc["name"], {})
        exists   = bool(state.get("Exists"))
        start    = (state.get("StartType") or "").lower()
        status   = (state.get("Status") or "").lower()
        # "active" = service actuellement configuré pour démarrer (Automatic / Manual)
        # "disabled" = StartType = Disabled
        is_disabled = start in ("disabled", "4")  # PS peut sérialiser l'enum en int
        result.append({
            "name":     svc["name"],
            "label":    svc["label"],
            "desc":     svc["desc"],
            "category": svc["category"],
            "risk":     svc["risk"],
            "exists":   exists,
            "active":   exists and not is_disabled,
            "status":   status,
            "start_type": start,
            "needs_admin": True,
            "impact":   {"ram_mb": svc.get("ram_mb", 0)},
        })
    return result


# Services qu'on REFUSE absolument de toucher (sécurité de base de Windows)
_SERVICES_PROTECTED = {
    "RpcSs", "RpcEptMapper", "DcomLaunch",  # RPC core
    "PlugPlay", "Power", "Schedule",          # PnP, alimentation, scheduler
    "Themes", "AudioSrv", "AudioEndpointBuilder",
    "EventLog", "EventSystem",
    "Dnscache", "Dhcp", "NlaSvc", "netprofm", "iphlpsvc",  # réseau
    "BFE", "MpsSvc",                          # firewall
    "wscsvc", "WinDefend", "SecurityHealthService", "Sense",  # sécurité
    "BITS", "wuauserv", "TrustedInstaller", "msiserver",       # MAJ Windows
    "LSM", "Winmgmt",                         # WMI/session
    "ProfSvc", "UserManager", "UmRdpService", "TermService",
    "CryptSvc", "KeyIso",                     # cryptographie
    "Spooler",                                # impression (pas critique mais user attend)
}


def _classify_service(name, display_name, description):
    """Classifie un service pour aide à la décision utilisateur."""
    if name in _SERVICES_PROTECTED:
        return "protected"
    if name in {s["name"] for s in _WINDOWS_SERVICES_TO_DISABLE}:
        return "curated_disable"
    desc = (description or "").lower()
    disp = (display_name or "").lower()
    if name.startswith("Microsoft") or "microsoft" in disp:
        return "microsoft_optional"
    # Heuristique : services dont le path contient 'system32' ou 'Microsoft'
    return "third_party"


def get_all_services_dynamic():
    """Retourne TOUS les services Windows avec classification.

    Plus exhaustif que get_services_state() qui se limite à la liste curée.
    Utilisé pour le mode "expert".
    """
    ps_cmd = (
        "Get-Service | ForEach-Object { "
        "  $svc = $_; "
        "  try { "
        "    $wmi = Get-CimInstance Win32_Service -Filter \"Name='$($svc.Name)'\" -ErrorAction Stop; "
        "    [PSCustomObject]@{ "
        "      Name = $svc.Name; "
        "      DisplayName = $svc.DisplayName; "
        "      Status = $svc.Status.ToString(); "
        "      StartType = $svc.StartType.ToString(); "
        "      Description = $wmi.Description; "
        "      PathName = $wmi.PathName "
        "    } "
        "  } catch { "
        "    [PSCustomObject]@{ "
        "      Name = $svc.Name; "
        "      DisplayName = $svc.DisplayName; "
        "      Status = $svc.Status.ToString(); "
        "      StartType = $svc.StartType.ToString(); "
        "      Description = $null; "
        "      PathName = $null "
        "    } "
        "  } "
        "} | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_cmd],
            capture_output=True, timeout=60, creationflags=0x08000000,
        )
        raw = r.stdout.decode("utf-8", errors="replace").strip()
        data = json.loads(raw) if raw and raw != "null" else []
        if isinstance(data, dict):
            data = [data]
    except Exception as e:
        return {"items": [], "error": str(e)}

    curated_set = {s["name"] for s in _WINDOWS_SERVICES_TO_DISABLE}
    curated_meta = {s["name"]: s for s in _WINDOWS_SERVICES_TO_DISABLE}

    items = []
    for svc in data:
        name = svc.get("Name") or ""
        display = svc.get("DisplayName") or name
        desc = svc.get("Description") or ""
        start = (svc.get("StartType") or "").lower()
        status = (svc.get("Status") or "").lower()
        category = _classify_service(name, display, desc)
        meta = curated_meta.get(name, {})
        items.append({
            "name":         name,
            "label":        display,
            "desc":         meta.get("desc") or desc,
            "category":     category,
            "curated":      name in curated_set,
            "status":       status,
            "start_type":   start,
            "active":       start not in ("disabled", "4"),
            "exists":       True,
            "risk":         meta.get("risk") or ("low" if category == "third_party" else "medium"),
            "needs_admin":  True,
            "impact":       {"ram_mb": meta.get("ram_mb", 0)},
        })

    items.sort(key=lambda x: (
        {"protected": 0, "curated_disable": 1, "microsoft_optional": 2, "third_party": 3}.get(x["category"], 4),
        x["label"].lower(),
    ))
    return {"items": items, "error": None}


def set_service_enabled(service_name, enabled):
    """Active ou désactive un service. Nécessite admin.

    enabled=True  → StartupType Manual (safe default, ne force pas Automatic)
    enabled=False → StartupType Disabled

    Bloque les services dans _SERVICES_PROTECTED. Le reste est autorisé
    (incluant les services hors whitelist via le mode dynamique).
    """
    if service_name in _SERVICES_PROTECTED:
        return False, "Service protégé : modification refusée"
    # Validation anti-injection : nom de service = alphanum + _ . uniquement
    if not re.fullmatch(r'[A-Za-z0-9_.]+', service_name):
        return False, "Nom de service invalide"
    target = "Manual" if enabled else "Disabled"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Set-Service", "-Name", service_name, "-StartupType", target, "-ErrorAction", "Stop"],
            capture_output=True, timeout=15, creationflags=0x08000000,
        )
        if r.returncode == 0:
            return True, None
        err = _decode_output(r.stderr).strip()
        return False, err or "Set-Service a échoué"
    except Exception as e:
        return False, str(e)


def get_scheduled_tasks_state():
    """Retourne l'état des tâches planifiées curées via schtasks /Query."""
    result = []
    for task in _SCHEDULED_TASKS_TO_DISABLE:
        state = "unknown"
        exists = False
        try:
            r = subprocess.run(
                ["schtasks", "/Query", "/TN", task["path"], "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5, creationflags=0x08000000,
            )
            if r.returncode == 0:
                exists = True
                out = _decode_output(r.stdout)
                # CSV format: "TaskName","Next Run Time","Status" — tolérant FR/EN
                if "Disabled" in out or "sactiv" in out.lower():
                    state = "disabled"
                elif "Ready" in out or "Running" in out or "Prêt" in out or "En cours" in out:
                    state = "enabled"
        except Exception:
            pass
        result.append({
            "path":     task["path"],
            "label":    task["label"],
            "desc":     task["desc"],
            "category": task["category"],
            "risk":     task["risk"],
            "exists":   exists,
            "active":   exists and state != "disabled",
            "state":    state,
            "needs_admin": True,
        })
    return result


# Préfixes de tâches planifiées qu'on REFUSE de toucher
_TASKS_PROTECTED_PREFIXES = (
    "\\Microsoft\\Windows\\Defrag\\",
    "\\Microsoft\\Windows\\Servicing\\",
    "\\Microsoft\\Windows\\WindowsUpdate\\",
    "\\Microsoft\\Windows\\TPM\\",
    "\\Microsoft\\Windows\\BitLocker",
    "\\Microsoft\\Windows\\Time Synchronization\\",
    "\\Microsoft\\Windows\\TaskScheduler\\",
    "\\Microsoft\\Windows\\Plug and Play\\",
)


def get_all_scheduled_tasks_dynamic():
    """Retourne toutes les tâches planifiées via schtasks /Query /FO CSV /V.

    Plus exhaustif que get_scheduled_tasks_state() qui se limite à la liste curée.
    """
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/V"],
            capture_output=True, timeout=30, creationflags=0x08000000,
        )
        if r.returncode != 0:
            return {"items": [], "error": _decode_output(r.stderr)}
        out = _decode_output(r.stdout)
    except Exception as e:
        return {"items": [], "error": str(e)}

    import csv
    import io
    items = []
    curated_set = {t["path"] for t in _SCHEDULED_TASKS_TO_DISABLE}
    curated_meta = {t["path"]: t for t in _SCHEDULED_TASKS_TO_DISABLE}

    # Parse CSV en index par position (les headers sont localisés FR/EN)
    # Colonnes /V (verbose) :
    #  0 HostName, 1 TaskName, 2 Next Run, 3 Status, 4 Logon Mode, 5 Last Run,
    #  6 Last Result, 7 Author, 8 Task To Run, 9 Start In, 10 Comment,
    #  11 Scheduled Task State, ..., 14 Run As User
    reader = csv.reader(io.StringIO(out))
    rows = list(reader)
    if not rows:
        return {"items": [], "error": "Sortie vide"}

    seen_paths = set()
    for row in rows[1:]:  # skip header
        if len(row) < 12:
            continue
        path = (row[1] or "").strip()
        if not path or path.startswith("Nom") or path == "TaskName":
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)

        status         = (row[3] or "").strip().lower()
        last_run       = (row[5] or "").strip()
        author         = (row[7] or "").strip()
        comment        = (row[10] or "").strip() if len(row) > 10 else ""
        scheduled_type = (row[11] or "").strip().lower() if len(row) > 11 else ""
        run_as         = (row[14] or "").strip() if len(row) > 14 else ""

        # État : "disabled" si le scheduled state contient "disabled" ou équivalent FR.
        # Le décodage UTF-8 peut introduire des U+FFFD à la place du é, donc on
        # matche sur "sactiv" (commun à "Désactivée" / "Désactivé") sans le préfixe.
        st_norm = scheduled_type.replace("\ufffd", "").lower()
        is_disabled = "disable" in st_norm or "sactiv" in st_norm

        # Catégorisation
        if any(path.startswith(p) for p in _TASKS_PROTECTED_PREFIXES):
            category = "protected"
        elif path in curated_set:
            category = "curated_disable"
        elif path.startswith("\\Microsoft\\"):
            category = "microsoft_optional"
        else:
            category = "third_party"

        meta = curated_meta.get(path, {})
        items.append({
            "path":         path,
            "label":        meta.get("label") or path.split("\\")[-1] or path,
            "desc":         meta.get("desc") or comment or "",
            "author":       author,
            "run_as":       run_as,
            "last_run":     last_run,
            "category":     category,
            "curated":      path in curated_set,
            "exists":       True,
            "active":       not is_disabled,
            "state":        "disabled" if is_disabled else "enabled",
            "risk":         meta.get("risk") or "low",
            "needs_admin":  True,
        })

    items.sort(key=lambda x: (
        {"protected": 0, "curated_disable": 1, "microsoft_optional": 2, "third_party": 3}.get(x["category"], 4),
        x["path"].lower(),
    ))
    return {"items": items, "error": None}


def set_scheduled_task_enabled(task_path, enabled):
    """Active ou désactive une tâche planifiée. Nécessite admin pour les tâches système.

    Bloque les tâches dans les préfixes protégés. Le reste est autorisé.
    """
    if any(task_path.startswith(p) for p in _TASKS_PROTECTED_PREFIXES):
        return False, "Tâche protégée : modification refusée"
    # Validation anti-injection : chemin de tâche = backslash + alphanum + espaces + ponctuation simple
    if not re.fullmatch(r'[\\A-Za-z0-9 _.()-]+', task_path):
        return False, "Chemin de tâche invalide"
    action = "/ENABLE" if enabled else "/DISABLE"
    try:
        r = subprocess.run(
            ["schtasks", "/Change", "/TN", task_path, action],
            capture_output=True, timeout=10, creationflags=0x08000000,
        )
        if r.returncode == 0:
            return True, None
        err = _decode_output(r.stderr).strip()
        return False, err or "schtasks a échoué"
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# Apps UWP pré-installées (debloat)
# ══════════════════════════════════════════════════════════════════════════════

# Liste curée d'apps UWP "bloat" sur Windows 11 24H2/25H2.
# Sources croisées : Win11Debloat, WinUtil, Sophia Script, SophiApp.
# Risk levels :
#   "safe"   — bloat pur, aucun effet de bord
#   "review" — certains utilisateurs peuvent vouloir garder
_UWP_BLOAT_APPS = [
    # ---------- SAFE : aucun regret possible ----------
    {"id": "bing_news",       "label": "Actualités (Bing News)",
     "desc": "Flux d'articles MSN, lié au widget actualités",
     "pattern": "Microsoft.BingNews", "risk": "safe"},
    {"id": "bing_weather",    "label": "Météo (Bing Weather)",
     "desc": "App Météo Microsoft, données MSN",
     "pattern": "Microsoft.BingWeather", "risk": "safe"},
    {"id": "get_help",        "label": "Obtenir de l'aide",
     "desc": "Assistant d'aide en ligne Microsoft",
     "pattern": "Microsoft.GetHelp", "risk": "safe"},
    {"id": "get_started",     "label": "Conseils (Get Started)",
     "desc": "Tutoriel de bienvenue Windows, inutile après config",
     "pattern": "Microsoft.Getstarted", "risk": "safe"},
    {"id": "office_hub",      "label": "Get Office (Office Hub)",
     "desc": "Raccourci promo vers Microsoft 365",
     "pattern": "Microsoft.MicrosoftOfficeHub", "risk": "safe"},
    {"id": "skype",           "label": "Skype",
     "desc": "Client Skype préinstallé, déprécié par Microsoft",
     "pattern": "Microsoft.SkypeApp", "risk": "safe"},
    {"id": "feedback_hub",    "label": "Hub de commentaires",
     "desc": "Remontée de bugs et suggestions à Microsoft",
     "pattern": "Microsoft.WindowsFeedbackHub", "risk": "safe"},
    {"id": "3d_viewer",       "label": "Visionneuse 3D",
     "desc": "Ancien viewer de modèles 3D, résiduel",
     "pattern": "Microsoft.Microsoft3DViewer", "risk": "safe"},
    {"id": "paint3d",         "label": "Paint 3D",
     "desc": "Version 3D de Paint, dépréciée",
     "pattern": "Microsoft.MSPaint", "risk": "safe"},
    {"id": "mixed_reality",   "label": "Portail Réalité Mixte",
     "desc": "Windows Mixed Reality Portal, abandonné en 2024",
     "pattern": "Microsoft.MixedReality.Portal", "risk": "safe"},
    {"id": "oneconnect",      "label": "Mobile Plans",
     "desc": "Gestionnaire de forfaits cellulaires (eSIM)",
     "pattern": "Microsoft.OneConnect", "risk": "safe"},
    {"id": "wallet",          "label": "Microsoft Wallet",
     "desc": "Ancien portefeuille Microsoft, déprécié",
     "pattern": "Microsoft.Wallet", "risk": "safe"},
    {"id": "print3d",         "label": "Print 3D",
     "desc": "Impression 3D UWP, déprécié",
     "pattern": "Microsoft.Print3D", "risk": "safe"},
    {"id": "clipchamp",       "label": "Clipchamp",
     "desc": "Éditeur vidéo Microsoft, préinstallé depuis 22H2",
     "pattern": "Clipchamp.Clipchamp", "risk": "safe"},
    {"id": "power_automate",  "label": "Power Automate Desktop",
     "desc": "Outil d'automatisation RPA, rarement utilisé en perso",
     "pattern": "Microsoft.PowerAutomateDesktop", "risk": "safe"},
    {"id": "family",          "label": "Famille Microsoft",
     "desc": "Contrôle parental Microsoft Family Safety",
     "pattern": "MicrosoftCorporationII.MicrosoftFamily", "risk": "safe"},
    {"id": "xbox_speech",     "label": "Xbox Speech To Text Overlay",
     "desc": "Voix vers texte pour jeux Xbox",
     "pattern": "Microsoft.XboxSpeechToTextOverlay", "risk": "safe"},

    # ---------- REVIEW : certains utilisateurs peuvent vouloir garder ----------
    {"id": "solitaire",       "label": "Solitaire Collection",
     "desc": "Suite de jeux de cartes Microsoft",
     "pattern": "Microsoft.MicrosoftSolitaireCollection", "risk": "review"},
    {"id": "todo",            "label": "Microsoft To Do",
     "desc": "Gestionnaire de tâches cloud (sync OneDrive)",
     "pattern": "Microsoft.Todos", "risk": "review"},
    {"id": "teams_consumer",  "label": "Teams (version grand public)",
     "desc": "Chat Teams consumer — pas la version pro/Office",
     "pattern": "MicrosoftTeams", "risk": "review"},
    {"id": "your_phone",      "label": "Mobile connecté (Phone Link)",
     "desc": "Liaison Windows ↔ smartphone Android/iOS",
     "pattern": "Microsoft.YourPhone", "risk": "review"},
    {"id": "quick_assist",    "label": "Assistance rapide",
     "desc": "Prise en main à distance, utile en dépannage",
     "pattern": "MicrosoftCorporationII.QuickAssist", "risk": "review"},
    {"id": "zune_music",      "label": "Groove Musique / Media Player Legacy",
     "desc": "Ancien lecteur audio",
     "pattern": "Microsoft.ZuneMusic", "risk": "review"},
    {"id": "zune_video",      "label": "Films et TV",
     "desc": "Lecteur vidéo et store de films",
     "pattern": "Microsoft.ZuneVideo", "risk": "review"},
    {"id": "maps",            "label": "Cartes Windows",
     "desc": "App Cartes Windows, dépréciée en 2025",
     "pattern": "Microsoft.WindowsMaps", "risk": "review"},
    {"id": "people",          "label": "Contacts (People)",
     "desc": "Gestionnaire de contacts UWP",
     "pattern": "Microsoft.People", "risk": "review"},
    {"id": "sticky_notes",    "label": "Pense-bête (Sticky Notes)",
     "desc": "Notes adhésives sur le bureau, sync OneNote",
     "pattern": "Microsoft.MicrosoftStickyNotes", "risk": "review"},
    {"id": "alarms",          "label": "Horloge et alarmes",
     "desc": "Timers, alarmes, focus sessions",
     "pattern": "Microsoft.WindowsAlarms", "risk": "review"},
    {"id": "sound_recorder",  "label": "Enregistreur vocal",
     "desc": "Enregistreur audio UWP",
     "pattern": "Microsoft.WindowsSoundRecorder", "risk": "review"},
    {"id": "mail_calendar",   "label": "Courrier et Calendrier (legacy)",
     "desc": "Ancienne app, remplacée par le nouveau Outlook sur 24H2",
     "pattern": "microsoft.windowscommunicationsapps", "risk": "review"},
    {"id": "new_outlook",     "label": "Nouveau Outlook (web)",
     "desc": "Nouveau client Outlook web-based poussé par Microsoft",
     "pattern": "Microsoft.OutlookForWindows", "risk": "review"},
    # Gaming
    {"id": "xbox_gaming_app", "label": "Application Xbox",
     "desc": "Client Xbox / Game Pass sur PC",
     "pattern": "Microsoft.GamingApp", "risk": "review"},
    {"id": "xbox_game_overlay","label": "Xbox Game Bar Overlay",
     "desc": "Surcouche Game Bar (Win+G)",
     "pattern": "Microsoft.XboxGameOverlay", "risk": "review"},
    {"id": "xbox_gamebar",    "label": "Xbox Game Bar",
     "desc": "Barre de jeu Windows (capture, perfs)",
     "pattern": "Microsoft.XboxGamingOverlay", "risk": "review"},
    {"id": "xbox_tcui",       "label": "Xbox TCUI",
     "desc": "Interface commune Xbox (parties, invitations)",
     "pattern": "Microsoft.Xbox.TCUI", "risk": "review"},
]


def list_uwp_apps():
    """Liste les apps UWP debloat détectées comme installées sur le système.

    Retourne une liste avec `installed: bool` et `package_full_name` si présent.
    """
    # Construit un tableau PowerShell avec tous les patterns, fait un seul
    # appel au lieu de N (plus rapide).
    patterns = [a["pattern"] for a in _UWP_BLOAT_APPS]
    ps_array = ",".join(f"'{p}'" for p in patterns)
    ps_cmd = (
        f"$patterns = @({ps_array}); "
        "$result = @(); "
        "foreach ($p in $patterns) { "
        "  $pkg = Get-AppxPackage -Name \"*$p*\" -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "  if ($pkg) { "
        "    $result += [PSCustomObject]@{ "
        "      Pattern = $p; "
        "      PackageFullName = $pkg.PackageFullName; "
        "      Name = $pkg.Name; "
        "      Publisher = $pkg.Publisher "
        "    } "
        "  } "
        "}; "
        "$result | ConvertTo-Json -Depth 3 -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_cmd],
            capture_output=True, timeout=30, creationflags=0x08000000,
        )
        raw = r.stdout.decode("utf-8", errors="replace").strip()
        data = json.loads(raw) if raw and raw != "null" else []
        if isinstance(data, dict):
            data = [data]
    except Exception:
        data = []

    by_pattern = {d.get("Pattern"): d for d in data if d.get("Pattern")}
    result = []
    for app in _UWP_BLOAT_APPS:
        pkg = by_pattern.get(app["pattern"])
        result.append({
            "id":                app["id"],
            "label":             app["label"],
            "desc":              app["desc"],
            "pattern":           app["pattern"],
            "risk":              app["risk"],
            "installed":         pkg is not None,
            "package_full_name": pkg.get("PackageFullName") if pkg else None,
            "publisher":         (pkg.get("Publisher") or "").split(",")[0].replace("CN=", "") if pkg else None,
        })
    return result


def remove_uwp_app(package_full_name):
    """Supprime une app UWP via Remove-AppxPackage (user scope)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Remove-AppxPackage -Package '{package_full_name}' -ErrorAction Stop"],
            capture_output=True, timeout=120, creationflags=0x08000000,
        )
        if r.returncode == 0:
            return True, None
        err = _decode_output(r.stderr).strip()
        return False, err or "Échec Remove-AppxPackage"
    except subprocess.TimeoutExpired:
        return False, "Timeout (>120 s)"
    except Exception as e:
        return False, str(e)


def remove_uwp_apps(package_full_names):
    """Bulk remove. Retourne liste de résultats par package."""
    results = []
    ok_count = 0
    for pfn in package_full_names:
        ok, err = remove_uwp_app(pfn)
        results.append({"package": pfn, "ok": ok, "error": err})
        if ok:
            ok_count += 1
    return {"ok_count": ok_count, "fail_count": len(package_full_names) - ok_count, "results": results}


def get_drivers():
    _CLASS_MAP = {
        "display":       "display",
        "media":         "media",
        "audioendpoint": "media",
        "net":           "net",
        "diskdrive":     "disk",
        "volume":        "disk",
        "hdc":           "disk",
        "usbstor":       "usb",
        "usb":           "usb",
        "bluetooth":     "bluetooth",
        "image":         "camera",
        "camera":        "camera",
        "keyboard":      "keyboard",
        "mouse":         "mouse",
        "hidclass":      "mouse",
        "battery":       "battery",
        "processor":     "cpu",
        "printer":       "printer",
        "printqueue":    "printer",
        "system":        "system",
        "computer":      "system",
    }
    ps_cmd = (
        "Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName } | "
        "Select-Object "
        "@{n='name';e={$_.DeviceName}}, "
        "@{n='version';e={$_.DriverVersion}}, "
        "@{n='date';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}else{''}}}, "
        "@{n='manufacturer';e={if($_.Manufacturer){$_.Manufacturer}else{''}}}, "
        "@{n='class';e={if($_.DeviceClass){$_.DeviceClass}else{''}}} "
        "| ConvertTo-Json -Compress"
    )
    data = _ps_json(ps_cmd, timeout=30)
    result = []
    for d in data:
        name = (d.get("name") or "").strip()
        if not name:
            continue
        cls = (d.get("class") or "").strip().lower()
        result.append({
            "name":         name,
            "version":      (d.get("version") or "").strip(),
            "date":         (d.get("date") or "").strip(),
            "manufacturer": (d.get("manufacturer") or "").strip(),
            "class_key":    _CLASS_MAP.get(cls, "other"),
        })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


def _collect_drivers_data():
    """Collecte les infos système et la liste des pilotes. Retourne un dict brut."""
    ps_cmd = r"""
    $sys  = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, SystemFamily, TotalPhysicalMemory
    $bios = Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion, ReleaseDate, Manufacturer, SerialNumber
    $cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1 Name, NumberOfCores, NumberOfLogicalProcessors
    $os   = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture
    $mb   = Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product, Version
    $drv  = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName } |
      Select-Object @{n='name';e={$_.DeviceName}},
                    @{n='manufacturer';e={$_.Manufacturer}},
                    @{n='version';e={$_.DriverVersion}},
                    @{n='date';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}else{''}}},
                    @{n='class';e={$_.DeviceClass}},
                    @{n='hwid';e={$_.DeviceID}},
                    @{n='inf';e={$_.InfName}}
    @{ sys=$sys; bios=$bios; cpu=$cpu; os=$os; mb=$mb; drv=@($drv) } | ConvertTo-Json -Depth 4 -Compress
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_cmd],
            capture_output=True, timeout=60, creationflags=0x08000000,
        )
        raw = r.stdout.decode("utf-8", errors="replace").strip()
        raw_data = json.loads(raw) if raw else {}
    except Exception:
        raw_data = {}

    def _fmt_bytes(n):
        try:
            n = int(n)
        except Exception:
            return ""
        for u in ["o", "Ko", "Mo", "Go", "To"]:
            if n < 1024:
                return f"{n:.0f} {u}"
            n /= 1024
        return f"{n:.0f} Po"

    sys_info  = raw_data.get("sys")  or {}
    bios_info = raw_data.get("bios") or {}
    cpu_info  = raw_data.get("cpu")  or {}
    os_info   = raw_data.get("os")   or {}
    mb_info   = raw_data.get("mb")   or {}
    drivers   = raw_data.get("drv")  or []

    drivers = sorted(drivers, key=lambda d: ((d.get("class") or "Autre"), (d.get("name") or "").lower()))

    machine = {
        "manufacturer":  sys_info.get("Manufacturer"),
        "model":         sys_info.get("Model"),
        "family":        sys_info.get("SystemFamily"),
        "serial":        bios_info.get("SerialNumber"),
        "memory":        _fmt_bytes(sys_info.get("TotalPhysicalMemory")),
        "cpu":           cpu_info.get("Name"),
        "cpu_cores":     cpu_info.get("NumberOfCores"),
        "cpu_logical":   cpu_info.get("NumberOfLogicalProcessors"),
        "motherboard":   (f'{mb_info.get("Manufacturer") or ""} {mb_info.get("Product") or ""}').strip() or None,
        "bios":          bios_info.get("SMBIOSBIOSVersion"),
        "os":            os_info.get("Caption"),
        "os_arch":       os_info.get("OSArchitecture"),
        "os_version":    os_info.get("Version"),
        "os_build":      os_info.get("BuildNumber"),
    }

    return {"machine": machine, "drivers": drivers}


def _render_drivers_html(data):
    from datetime import datetime

    def esc(v):
        s = "" if v is None else str(v)
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    m = data["machine"]
    drivers = data["drivers"]

    by_class = defaultdict(list)
    for d in drivers:
        by_class[(d.get("class") or "Autre").strip() or "Autre"].append(d)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def row(label, value):
        if not value:
            return ""
        return f'<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>'

    cores_str = (f'{m.get("cpu_cores") or ""} physiques / {m.get("cpu_logical") or ""} logiques'
                 if m.get("cpu_cores") else "")
    machine_rows = "".join([
        row("Fabricant",       m.get("manufacturer")),
        row("Modèle",          m.get("model")),
        row("Famille",         m.get("family")),
        row("Numéro de série", m.get("serial")),
        row("Mémoire",         m.get("memory")),
        row("Processeur",      m.get("cpu")),
        row("Cœurs",           cores_str),
        row("Carte mère",      m.get("motherboard")),
        row("BIOS",            m.get("bios")),
        row("Système",         f'{m.get("os") or ""} ({m.get("os_arch") or ""})' if m.get("os") else ""),
        row("Build",           f'{m.get("os_version") or ""} — {m.get("os_build") or ""}' if m.get("os_version") else ""),
    ])

    sections = []
    for cls in sorted(by_class.keys()):
        items = by_class[cls]
        rows_html = "".join(
            f'<tr>'
            f'<td class="n">{esc(d.get("name"))}</td>'
            f'<td>{esc(d.get("manufacturer"))}</td>'
            f'<td class="m">{esc(d.get("version"))}</td>'
            f'<td class="m">{esc(d.get("date"))}</td>'
            f'<td class="hw">{esc(d.get("hwid"))}</td>'
            f'</tr>'
            for d in items
        )
        sections.append(
            f'<h3>{esc(cls)} <span class="count">{len(items)}</span></h3>'
            f'<table class="drv"><thead><tr>'
            f'<th>Nom</th><th>Fabricant</th><th>Version</th><th>Date</th><th>Hardware ID</th>'
            f'</tr></thead><tbody>{rows_html}</tbody></table>'
        )

    hwids = "\n".join(d.get("hwid") or "" for d in drivers if d.get("hwid"))

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport pilotes — {esc(m.get("model") or "PC")}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, 'Segoe UI', sans-serif;
    background: #f7f6f3; color: #37352f;
    max-width: 1100px; margin: 0 auto; padding: 40px 32px 80px;
    font-size: 14px; line-height: 1.55;
  }}
  h1 {{ font-size: 24px; margin-bottom: 4px; }}
  .meta {{ color: #9b9a97; font-size: 12px; margin-bottom: 32px; }}
  h2 {{ font-size: 16px; margin: 32px 0 12px; font-weight: 600; }}
  h3 {{ font-size: 13px; margin: 24px 0 8px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; color: #6b6b6b; }}
  h3 .count {{ color: #9b9a97; font-weight: 400; margin-left: 6px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e9e9e7; border-radius: 4px; overflow: hidden; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #f0eeea; font-size: 12px; vertical-align: top; }}
  thead th {{ background: #fafaf8; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .3px; color: #6b6b6b; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  table.info th {{ width: 180px; color: #6b6b6b; font-weight: 500; background: #fafaf8; }}
  table.drv td.n {{ font-weight: 500; }}
  table.drv td.m, table.drv td.hw {{ font-family: 'Consolas', 'SF Mono', monospace; font-size: 11px; color: #6b6b6b; }}
  table.drv td.hw {{ word-break: break-all; max-width: 340px; }}
  pre {{
    background: #fff; border: 1px solid #e9e9e7; border-radius: 4px;
    padding: 14px; font-size: 11px; overflow-x: auto;
    font-family: 'Consolas', 'SF Mono', monospace;
  }}
</style>
</head>
<body>
  <h1>Rapport pilotes</h1>
  <div class="meta">Généré par OpenCleaner · {esc(now)}</div>

  <h2>Informations système</h2>
  <table class="info"><tbody>{machine_rows}</tbody></table>

  <h2>Pilotes installés ({len(drivers)})</h2>
  {"".join(sections) if sections else "<p>Aucun pilote récupéré.</p>"}

  <h2>Liste des Hardware IDs</h2>
  <pre>{esc(hwids)}</pre>
</body>
</html>"""


def _render_drivers_txt(data):
    from datetime import datetime

    m = data["machine"]
    drivers = data["drivers"]
    out = []
    out.append("=" * 70)
    out.append("  RAPPORT PILOTES — OpenCleaner")
    out.append("  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    out.append("=" * 70)
    out.append("")

    out.append("INFORMATIONS SYSTÈME")
    out.append("-" * 70)
    def line(label, value):
        if value:
            out.append(f"  {label:<18}{value}")
    line("Fabricant",       m.get("manufacturer"))
    line("Modèle",          m.get("model"))
    line("Famille",         m.get("family"))
    line("Numéro de série", m.get("serial"))
    line("Mémoire",         m.get("memory"))
    line("Processeur",      m.get("cpu"))
    if m.get("cpu_cores"):
        line("Cœurs", f'{m.get("cpu_cores")} physiques / {m.get("cpu_logical")} logiques')
    line("Carte mère",      m.get("motherboard"))
    line("BIOS",            m.get("bios"))
    if m.get("os"):
        line("Système", f'{m.get("os")} ({m.get("os_arch") or ""})')
    if m.get("os_version"):
        line("Build",   f'{m.get("os_version")} — {m.get("os_build") or ""}')
    out.append("")

    by_class = defaultdict(list)
    for d in drivers:
        by_class[(d.get("class") or "Autre").strip() or "Autre"].append(d)

    out.append(f"PILOTES INSTALLÉS ({len(drivers)})")
    out.append("-" * 70)
    for cls in sorted(by_class.keys()):
        items = by_class[cls]
        out.append("")
        out.append(f"[{cls}] — {len(items)}")
        for d in items:
            name = d.get("name") or ""
            mfr  = d.get("manufacturer") or ""
            ver  = d.get("version") or ""
            dt   = d.get("date") or ""
            hwid = d.get("hwid") or ""
            out.append(f"  • {name}")
            if mfr:  out.append(f"      Fabricant : {mfr}")
            if ver:  out.append(f"      Version   : {ver}")
            if dt:   out.append(f"      Date      : {dt}")
            if hwid: out.append(f"      HWID      : {hwid}")
    out.append("")

    hwids = [d.get("hwid") for d in drivers if d.get("hwid")]
    out.append("HARDWARE IDS (copier-coller)")
    out.append("-" * 70)
    out.extend(hwids)
    out.append("")
    return "\n".join(out)


def _render_drivers_json(data):
    from datetime import datetime
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator":    "OpenCleaner",
        "machine":      data["machine"],
        "drivers":      data["drivers"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_drivers_report(fmt="html"):
    """Génère un rapport des pilotes dans le format demandé.

    fmt ∈ {"html", "txt", "json"}. Retourne {"content", "filename", "mimetype"}.
    """
    from datetime import datetime

    data = _collect_drivers_data()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    if fmt == "txt":
        return {
            "content":  _render_drivers_txt(data),
            "filename": f"rapport-pilotes-{stamp}.txt",
            "mimetype": "text/plain; charset=utf-8",
        }
    if fmt == "json":
        return {
            "content":  _render_drivers_json(data),
            "filename": f"rapport-pilotes-{stamp}.json",
            "mimetype": "application/json; charset=utf-8",
        }
    return {
        "content":  _render_drivers_html(data),
        "filename": f"rapport-pilotes-{stamp}.html",
        "mimetype": "text/html; charset=utf-8",
    }


def scan_windows_updates_system():
    """Recherche les mises à jour Windows qualité/sécurité via COM.

    Retourne {"updates": [...], "error": str|None}.
    """
    ps_cmd = r"""
    $ErrorActionPreference = 'Stop'
    try {
      $s = New-Object -ComObject Microsoft.Update.Session
      $searcher = $s.CreateUpdateSearcher()
      $result = $searcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")
      $out = @()
      foreach ($u in $result.Updates) {
        $out += [PSCustomObject]@{
          title        = $u.Title
          description  = $u.Description
          severity     = $u.MsrcSeverity
          kbIds        = @($u.KBArticleIDs)
          sizeBytes    = [int64]$u.MaxDownloadSize
          isSecurity   = ($u.Categories | Where-Object { $_.Name -match 'Security|Sécurité' }) -ne $null
        }
      }
      @{ updates = @($out); error = $null } | ConvertTo-Json -Depth 4 -Compress
    } catch {
      @{ updates = @(); error = $_.Exception.Message } | ConvertTo-Json -Depth 4 -Compress
    }
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_cmd],
            capture_output=True, timeout=180, creationflags=0x08000000,
        )
        raw = r.stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return {"updates": [], "error": "Réponse vide du service Windows Update."}
        data = json.loads(raw)
        data["updates"] = data.get("updates") or []
        return data
    except subprocess.TimeoutExpired:
        return {"updates": [], "error": "La recherche a dépassé 3 minutes."}
    except Exception as e:
        return {"updates": [], "error": str(e)}


def get_update_center():
    """Agrège toutes les sources de mises à jour en une seule vue.

    Retourne {"windows": {...}, "drivers": {...}, "software": {...}, "total": int}.
    """
    wu = scan_windows_updates_system()
    dr = scan_windows_update_drivers()
    sw = get_software_updates()

    def _count(d):
        return len((d or {}).get("updates") or [])

    return {
        "windows":  wu,
        "drivers":  dr,
        "software": sw,
        "counts": {
            "windows":  _count(wu),
            "drivers":  _count(dr),
            "software": _count(sw),
        },
        "total": _count(wu) + _count(dr) + _count(sw),
    }


def scan_windows_update_drivers():
    """Recherche les mises à jour de pilotes via l'API COM Windows Update.

    Retourne {"updates": [...], "error": str|None}.
    """
    ps_cmd = r"""
    $ErrorActionPreference = 'Stop'
    try {
      $s = New-Object -ComObject Microsoft.Update.Session
      $searcher = $s.CreateUpdateSearcher()
      $searcher.ServerSelection = 3
      $searcher.ServiceID = '7971f918-a847-4430-9279-4a52d1efe18d'
      $result = $searcher.Search("IsInstalled=0 and Type='Driver'")
      $out = @()
      foreach ($u in $result.Updates) {
        $out += [PSCustomObject]@{
          title        = $u.Title
          description  = $u.Description
          driverClass  = $u.DriverClass
          driverModel  = $u.DriverModel
          driverDate   = if ($u.DriverVerDate) { $u.DriverVerDate.ToString('yyyy-MM-dd') } else { '' }
          sizeBytes    = [int64]$u.MaxDownloadSize
        }
      }
      @{ updates = @($out); error = $null } | ConvertTo-Json -Depth 4 -Compress
    } catch {
      @{ updates = @(); error = $_.Exception.Message } | ConvertTo-Json -Depth 4 -Compress
    }
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_cmd],
            capture_output=True, timeout=180, creationflags=0x08000000,
        )
        raw = r.stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return {"updates": [], "error": "Réponse vide du service Windows Update."}
        data = json.loads(raw)
        data["updates"] = data.get("updates") or []
        return data
    except subprocess.TimeoutExpired:
        return {"updates": [], "error": "La recherche a dépassé 3 minutes."}
    except Exception as e:
        return {"updates": [], "error": str(e)}


def get_software_updates():
    """
    Retourne les logiciels avec mises à jour disponibles via winget.
    Retourne {"updates": [...], "error": str|None}.
    """
    try:
        r = subprocess.run(
            ["winget", "upgrade",
             "--accept-source-agreements", "--disable-interactivity"],
            capture_output=True, timeout=30
        )
        output = _decode_output(r.stdout)
        lines  = output.splitlines()

        # Trouver la ligne séparateur : uniquement des tirets, au moins 20 caractères
        sep_idx = next((i for i, l in enumerate(lines)
                        if len(l.rstrip()) > 20 and all(c == "-" for c in l.rstrip())), None)
        if sep_idx is None or sep_idx == 0:
            return {"updates": [], "error": None}

        # Utiliser la ligne d'en-tête (avant le séparateur) pour calculer les colonnes.
        # Winget peut préfixer des caractères de spinner (\r, -, \, |) — on les retire.
        header_raw = lines[sep_idx - 1]
        # Supprimer les caractères de spinner en début de ligne
        header = header_raw.lstrip("\r-\\|/ \x1b")
        # Trouver les mots-clés attendus (FR ou EN)
        keywords = ["Nom", "Name", "ID", "Id", "Version", "Disponible", "Available", "Source"]
        # Calculer les positions de début de chaque colonne à partir de l'en-tête
        cols = []
        i = 0
        while i < len(header):
            if header[i] != " ":
                j = i
                while j < len(header) and header[j] != " ":
                    j += 1
                # Ajuster l'offset à la position dans la ligne brute
                offset = len(header_raw) - len(header)
                cols.append(i + offset)
                i = j
            else:
                i += 1
        # Convertir en intervalles (start, end)
        col_ranges = [(cols[k], cols[k + 1] if k + 1 < len(cols) else None)
                      for k in range(len(cols))]

        updates = []
        for line in lines[sep_idx + 1:]:
            if not line.strip() or all(c in "- " for c in line.strip()):
                continue
            parts = []
            for s, e in col_ranges:
                chunk = line[s:e].strip() if e else line[s:].strip()
                parts.append(chunk)
            # Attend au moins : Nom, ID, Version, Disponible
            if len(parts) >= 4 and parts[0] and parts[3] and parts[3] != parts[2]:
                updates.append({
                    "name":      parts[0],
                    "id":        parts[1] if len(parts) > 1 else "",
                    "version":   parts[2] if len(parts) > 2 else "?",
                    "available": parts[3],
                    "source":    parts[4] if len(parts) > 4 else "winget",
                })
        return {"updates": updates, "error": None}
    except FileNotFoundError:
        return {"updates": [], "error": "winget introuvable — Windows 11 requis"}
    except subprocess.TimeoutExpired:
        return {"updates": [], "error": "Délai dépassé — vérifiez votre connexion"}
    except Exception as e:
        return {"updates": [], "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Nettoyage confidentialité
# ──────────────────────────────────────────────────────────────────────────────

def get_privacy_items():
    """
    Retourne la liste des éléments de confidentialité nettoyables avec leur taille estimée.
    """
    items = []

    # Note : les fichiers récents (%APPDATA%\Microsoft\Windows\Recent\*.lnk) sont
    # nettoyés par task_recent_files dans l'onglet Nettoyage principal — pas
    # ré-exposés ici pour éviter le doublon UI.

    # Jump Lists
    jl_dirs = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent" / "AutomaticDestinations",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent" / "CustomDestinations",
    ]
    jl_files = []
    jl_size  = 0
    for d in jl_dirs:
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    try:
                        jl_size += f.stat().st_size
                        jl_files.append(f)
                    except OSError:
                        pass
    items.append({
        "id":    "jump_lists",
        "label": "Jump Lists",
        "desc":  "Historique des fichiers récents visibles au clic droit sur la barre des tâches",
        "count": len(jl_files),
        "size":  jl_size,
        "size_fmt": fmt_size(jl_size),
    })

    # Recherches récentes dans l'Explorateur
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths",
                             0, winreg.KEY_READ)
        count = 0
        i = 0
        while True:
            try:
                winreg.EnumValue(key, i)
                count += 1
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        items.append({
            "id":    "explorer_searches",
            "label": "Historique barre d'adresse",
            "desc":  "Chemins tapés dans la barre d'adresse de l'Explorateur Windows",
            "count": count,
            "size":  0,
            "size_fmt": f"{count} entrée(s)",
        })
    except Exception:
        pass

    # Presse-papier — n'afficher que si le presse-papier contient quelque chose
    try:
        import ctypes
        if ctypes.windll.user32.OpenClipboard(0):
            n_formats = ctypes.windll.user32.CountClipboardFormats()
            ctypes.windll.user32.CloseClipboard()
            if n_formats > 0:
                items.append({
                    "id":    "clipboard",
                    "label": "Presse-papier",
                    "desc":  "Contenu actuellement copié dans le presse-papier",
                    "count": n_formats,
                    "size":  0,
                    "size_fmt": f"{n_formats} format(s)",
                })
    except Exception:
        pass

    return items


def clean_privacy_items(ids):
    """
    Nettoie les éléments de confidentialité sélectionnés.
    Retourne (cleaned_count, errors).
    """
    cleaned, errors = 0, []

    if "jump_lists" in ids:
        jl_dirs = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent" / "AutomaticDestinations",
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent" / "CustomDestinations",
        ]
        batch = []
        for d in jl_dirs:
            if d.exists():
                for f in d.iterdir():
                    if f.is_file():
                        batch.append(str(f))
        _, errs = _recycle_many(batch, label="Jump Lists")
        cleaned += len(batch) - len(errs)
        errors.extend(errs)

    if "explorer_searches" in ids:
        try:
            # Bug fix : il faut KEY_READ ET KEY_WRITE pour pouvoir
            # enumerer (EnumValue) puis supprimer (DeleteValue).
            # KEY_SET_VALUE seul ne permet pas EnumValue.
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths",
                                 0, winreg.KEY_READ | winreg.KEY_WRITE)
            # Collecter tous les noms d'abord (EnumValue ne marche pas
            # bien en meme temps que DeleteValue)
            names = []
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, i)
                    names.append(name)
                    i += 1
                except OSError:
                    break
            # Ensuite supprimer
            for name in names:
                try:
                    winreg.DeleteValue(key, name)
                    cleaned += 1
                except Exception as e:
                    errors.append(f"TypedPaths/{name}: {e}")
            winreg.CloseKey(key)
        except Exception as e:
            errors.append(f"Historique barre d'adresse : {e}")

    if "clipboard" in ids:
        try:
            import ctypes
            ctypes.windll.user32.OpenClipboard(0)
            ctypes.windll.user32.EmptyClipboard()
            ctypes.windll.user32.CloseClipboard()
            cleaned += 1
        except Exception as e:
            errors.append(f"Presse-papier : {e}")

    return cleaned, errors


# ──────────────────────────────────────────────────────────────────────────────
# Fichier d'hibernation
# ──────────────────────────────────────────────────────────────────────────────

def get_hibernation_info():
    """Retourne l'état et la taille du fichier d'hibernation."""
    hiberfil = Path("C:/hiberfil.sys")
    enabled  = hiberfil.exists()
    size     = 0
    if enabled:
        try:
            size = hiberfil.stat().st_size
        except OSError:
            pass
    return {"enabled": enabled, "size": size, "size_fmt": fmt_size(size) if size else "—"}


def disable_hibernation():
    """Désactive l'hibernation via powercfg (supprime hiberfil.sys). Requiert admin."""
    try:
        r = subprocess.run(
            ["powercfg", "/hibernate", "off"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0, _decode_output(r.stderr).strip()
    except Exception as e:
        return False, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# Analyse intelligente de l'espace disque
# ──────────────────────────────────────────────────────────────────────────────

_DISK_SKIP = {"$Recycle.Bin", "System Volume Information", "Recovery",
              "Config.Msi", "MSOCache"}


def scan_disk_level(folder, on_item=None):
    """
    Scanne les enfants directs de folder, calcule leur taille en parallèle.
    Appelle on_item({name, path, size, size_fmt, is_dir}) dès qu'un résultat est prêt.
    Retourne la liste complète triée par taille décroissante.
    """
    folder = Path(folder)
    entries = []
    try:
        for entry in os.scandir(folder):
            if entry.name in _DISK_SKIP:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                # Ignore les points de jonction (Windows.old, etc. peuvent être des junctions)
                if entry.stat(follow_symlinks=False).st_file_attributes & 0x400:
                    continue
                entries.append((entry.name, entry.path, is_dir))
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        pass

    results = []

    def _measure(name, path, is_dir):
        if is_dir:
            import time as _time
            total = 0
            deadline = _time.monotonic() + 5.0  # max 5s par dossier
            try:
                for dirpath, dirs, filenames in os.walk(path):
                    if _time.monotonic() > deadline:
                        dirs.clear()  # stop la recursion
                        break
                    for f in filenames:
                        try:
                            total += os.path.getsize(os.path.join(dirpath, f))
                        except (OSError, PermissionError):
                            pass
            except (OSError, PermissionError):
                pass
            size = total
        else:
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = 0
        item = {"name": name, "path": path, "size": size,
                "size_fmt": fmt_size(size), "is_dir": is_dir}
        if on_item:
            on_item(item)
        return item

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_measure, n, p, d): (n, p, d) for n, p, d in entries}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass

    results.sort(key=lambda x: x["size"], reverse=True)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Analyse intelligente — détection complète de l'espace récupérable
# ──────────────────────────────────────────────────────────────────────────────

_SMART_SKIP_NAMES = {
    # Système / OS
    "$Recycle.Bin", "System Volume Information", "Recovery", "Config.Msi",
    "MSOCache", "Windows", "PerfLogs", "ProgramData",
    "Program Files", "Program Files (x86)",
    # Profils techniques
    "AppData", "Default", "Public", "All Users", "Default User",
    # Caches déjà couverts par les tâches de nettoyage
    ".cache", ".npm", ".nuget", ".gradle", ".m2", ".cargo",
}

# Dossiers qu'on ne SKIP pas mais qu'on détecte spécialement comme projets dev
_DEV_MARKERS = {"package.json", "requirements.txt", "Cargo.toml", "go.mod",
                "pom.xml", "build.gradle", "composer.json", "Gemfile", "*.sln"}
_DEV_BLOAT_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox",
                   "dist", "build", "target", "bin", "obj", ".next", ".nuxt"}

# Patterns connus de caches jeux/apps
_GAME_CACHE_PATTERNS = {
    # Steam
    "userdata":             "cache_jeux",
    "screenshots":          "cache_jeux",
    "Steam/steamapps/shadercache": "cache_jeux",
    # OBS
    "OBS Studio":           "cache_jeux",
    # Windows GameDVR / Captures
    "Captures":             "cache_jeux",
    "GameDVR":              "cache_jeux",
    # NVIDIA
    "NVIDIA Corporation/NV_Cache": "cache_jeux",
    "NVIDIA/GeForce Experience/CameraRecording": "cache_jeux",
}

_SMART_CATEGORY_MAP = {
    "photos": {
        ".jpg", ".jpeg", ".png", ".heic", ".heif", ".raw", ".cr2", ".nef",
        ".arw", ".dng", ".tiff", ".tif", ".bmp", ".webp", ".svg",
    },
    "videos": {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
        ".mpeg", ".mpg", ".3gp", ".ts", ".vob",
    },
    "musique": {
        ".mp3", ".flac", ".wav", ".aac", ".ogg", ".wma", ".m4a", ".opus",
    },
    "archives": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".img",
    },
    "documents": {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".odt", ".ods", ".odp", ".txt", ".csv", ".rtf",
    },
    "jeux_iso": {
        ".iso", ".img", ".bin", ".cue", ".nrg", ".mdf",
    },
    "sauvegardes": {
        ".bak", ".old", ".backup", ".bkp", ".tmp",
    },
}

_INSTALLER_EXTS = {".exe", ".msi", ".msp"}

# Inversé : extension → catégorie (première correspondance gagne)
_EXT_TO_CAT = {}
for _cat, _exts in _SMART_CATEGORY_MAP.items():
    for _ext in _exts:
        _EXT_TO_CAT.setdefault(_ext, _cat)


def _confidence_for(item):
    """Attribue un score de confiance : 'sûr', 'probable', 'à vérifier'."""
    cat = item.get("category", "")
    # Sûr : caches régénérables, installers déjà installés, caches jeux
    if cat in ("projet_dev", "cache_jeux", "installer_installé"):
        return "sûr"
    # À vérifier : contenu personnel potentiellement important
    if cat in ("photos", "documents", "musique"):
        return "à vérifier"
    # Probable : le reste (vidéos, archives, ISOs, sauvegardes, mixte)
    return "probable"


def _detect_dev_project(folder_path):
    """Détecte si un dossier est un projet dev avec du bloat régénérable.

    Retourne (is_dev, bloat_size) — bloat_size = taille des node_modules/.venv/etc.
    """
    is_dev = False
    bloat_size = 0
    try:
        children = set(os.listdir(folder_path))
    except (OSError, PermissionError):
        return False, 0
    # Vérifier les marqueurs de projet
    for marker in _DEV_MARKERS:
        if marker.startswith("*"):
            if any(f.endswith(marker[1:]) for f in children):
                is_dev = True
                break
        elif marker in children:
            is_dev = True
            break
    if not is_dev:
        return False, 0
    # Mesurer le bloat (node_modules, .venv, etc.)
    for bloat_name in _DEV_BLOAT_DIRS:
        bloat_path = os.path.join(folder_path, bloat_name)
        if os.path.isdir(bloat_path):
            try:
                import time as _t
                deadline = _t.monotonic() + 5.0
                for dp, ds, fs in os.walk(bloat_path):
                    if _t.monotonic() > deadline:
                        ds.clear()
                        break
                    for f in fs:
                        try:
                            bloat_size += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
            except OSError:
                pass
    return True, bloat_size


def _classify_folder(folder_path, deadline):
    """Parcourt un dossier et retourne (taille, last_access, catégorie dominante, nb_fichiers)."""
    import time as _time
    counts = {}   # catégorie → taille cumulée
    total_size = 0
    latest_access = 0
    file_count = 0
    try:
        for dirpath, dirs, filenames in os.walk(folder_path):
            if _time.monotonic() > deadline:
                dirs.clear()
                break
            # Sauter les sous-dossiers techniques mais PAS node_modules/.venv (on les mesure)
            dirs[:] = [d for d in dirs if d not in _SMART_SKIP_NAMES]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    st = os.stat(fpath)
                    size = st.st_size
                    total_size += size
                    file_count += 1
                    atime = st.st_mtime
                    if atime > latest_access:
                        latest_access = atime
                    ext = os.path.splitext(fname)[1].lower()
                    cat = _EXT_TO_CAT.get(ext, "autre")
                    counts[cat] = counts.get(cat, 0) + size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    # Catégorie dominante = celle qui pèse le plus
    if counts:
        dominant = max(counts, key=counts.get)
        if dominant == "autre" and counts[dominant] > total_size * 0.6:
            dominant = "mixte"
    else:
        dominant = "mixte"
    return total_size, latest_access, dominant, file_count


def _get_installed_app_names():
    """Récupère les noms d'apps installées (cache léger pour le scan)."""
    try:
        apps = get_installed_apps()
        return {a["name"].lower() for a in apps if a.get("name")}
    except Exception:
        return set()


def _scan_installers_in_downloads(min_age_days, installed_names, on_item, results):
    """Scanne le dossier Downloads pour les .exe/.msi déjà installés."""
    import time as _time
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return
    cutoff = _time.time() - (min_age_days * 86400)
    already = {r["path"] for r in results}
    try:
        for entry in os.scandir(downloads):
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in _INSTALLER_EXTS:
                    continue
                st = entry.stat(follow_symlinks=False)
                if st.st_size < 1_000_000:  # < 1 Mo, pas intéressant
                    continue
                atime = st.st_mtime
                if atime >= cutoff or atime == 0:
                    continue
                if entry.path in already:
                    continue
                # Vérifier si le nom du fichier matche une app installée
                name_lower = os.path.splitext(entry.name)[0].lower()
                # Nettoyage du nom : retirer version, tirets, underscores
                clean = re.sub(r'[-_]?(v?\d[\d.]*|setup|install|x64|x86|win|installer).*', '',
                               name_lower, flags=re.I).strip(" -_")
                matched = any(clean and clean in app_name for app_name in installed_names)
                if not matched:
                    continue
                days_ago = int((_time.time() - atime) / 86400)
                item = {
                    "name":        entry.name,
                    "path":        entry.path,
                    "size":        st.st_size,
                    "size_fmt":    fmt_size(st.st_size),
                    "category":    "installer_installé",
                    "file_count":  1,
                    "is_file":     True,
                    "last_access": atime,
                    "days_ago":    days_ago,
                    "needs_admin": False,
                    "confidence":  "sûr",
                    "hint":        "Logiciel déjà installé",
                }
                if on_item:
                    on_item(item)
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        pass


def _scan_game_caches(min_size, on_item, results):
    """Détecte les caches jeux/captures connus."""
    import time as _time
    already = {r["path"] for r in results}
    # Chemins à vérifier
    home = str(Path.home())
    candidates = [
        os.path.join(home, "Videos", "Captures"),
        os.path.join(home, "Videos", "OBS"),
        os.path.join(home, "Videos", "Radeon ReLive"),
        os.path.join(home, "Videos", "NVIDIA"),
        os.path.join(home, "Pictures", "Screenshots"),
        os.path.join(home, "AppData", "Local", "NVIDIA Corporation", "NV_Cache"),
        os.path.join(home, "AppData", "Local", "NVIDIA", "GLCache"),
        os.path.join(home, "AppData", "LocalLow", "NVIDIA", "PerDriverVersion", "DXCache"),
    ]
    # Steam screenshots
    steam_path = os.path.join("C:\\", "Program Files (x86)", "Steam", "userdata")
    if os.path.isdir(steam_path):
        try:
            for uid in os.listdir(steam_path):
                ss = os.path.join(steam_path, uid, "760", "remote")
                if os.path.isdir(ss):
                    candidates.append(ss)
        except OSError:
            pass

    for path in candidates:
        if not os.path.isdir(path) or path in already:
            continue
        deadline = _time.monotonic() + 5.0
        size, last_access, _, file_count = _classify_folder(path, deadline)
        if size < min_size:
            continue
        days_ago = int((_time.time() - last_access) / 86400) if last_access > 0 else 0
        item = {
            "name":        os.path.basename(path),
            "path":        path,
            "size":        size,
            "size_fmt":    fmt_size(size),
            "category":    "cache_jeux",
            "file_count":  file_count,
            "is_file":     False,
            "last_access": last_access,
            "days_ago":    days_ago,
            "needs_admin": is_admin_path(path),
            "confidence":  "sûr",
            "hint":        "Cache régénérable automatiquement",
        }
        already.add(path)
        if on_item:
            on_item(item)


def scan_smart_analysis(min_size=500_000_000, min_age_days=180, on_item=None, on_log=None):
    """Scan multi-disques intelligent — walk récursif complet.

    Parcourt TOUS les fichiers de TOUS les disques en un seul os.walk par racine.
    Accumule taille + dernier accès + catégorie par dossier, puis émet ceux qui
    dépassent les seuils. Détecte aussi les gros fichiers isolés, les projets dev
    abandonnés, les installers inutiles et les caches jeux.
    """
    import time as _time
    import psutil

    def _log(msg):
        if on_log:
            on_log(msg)

    cutoff = _time.time() - (min_age_days * 86400)
    results = []
    already = set()

    _log("Chargement de la liste des logiciels installés…")
    installed_names = _get_installed_app_names()
    _log(f"{len(installed_names)} logiciel(s) détecté(s)")

    roots = []
    try:
        for part in psutil.disk_partitions(all=False):
            if part.fstype:
                roots.append(part.mountpoint)
    except Exception:
        roots = ["C:\\"]

    def _emit(item):
        if item["path"] in already:
            return
        if "confidence" not in item:
            item["confidence"] = _confidence_for(item)
        already.add(item["path"])
        results.append(item)
        if on_item:
            on_item(item)

    # ── Walk récursif complet ────────────────────────────────────────────────
    for root in roots:
        _log(f"Scan {root}")
        # dir_info[path] = {size, latest, counts{cat→bytes}, file_count, depth}
        dir_info = {}
        big_files = []   # (fpath, fname, size, atime, cat) — émis après les dossiers
        log_counter = 0

        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                # Filtrer les dossiers à ignorer
                dirnames[:] = [
                    d for d in dirnames
                    if d not in _SMART_SKIP_NAMES
                    and not _is_junction(os.path.join(dirpath, d))
                ]

                depth = dirpath.replace(root, "").count(os.sep)

                # Log de progression (pas trop fréquent)
                log_counter += 1
                if log_counter % 200 == 0 or depth <= 1:
                    rel = dirpath.replace(root, "") or "\\"
                    if len(rel) > 60:
                        rel = rel[:28] + "…" + rel[-28:]
                    _log(f"  {root}{rel}  ({len(results)} trouvaille(s))")

                # Accumuler les fichiers de ce dossier
                dir_size = 0
                dir_latest = 0
                dir_counts = {}
                dir_fcount = 0

                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        st = os.stat(fpath)
                        size = st.st_size
                        atime = st.st_mtime
                    except (OSError, PermissionError):
                        continue

                    dir_size += size
                    dir_fcount += 1
                    if atime > dir_latest:
                        dir_latest = atime

                    ext = os.path.splitext(fname)[1].lower()
                    cat = _EXT_TO_CAT.get(ext, "autre")
                    dir_counts[cat] = dir_counts.get(cat, 0) + size

                    # Gros fichier isolé ? (collecté, émis après les dossiers)
                    if size >= min_size and 0 < atime < cutoff:
                        big_files.append((fpath, fname, size, atime, cat))

                dir_info[dirpath] = {
                    "size": dir_size, "latest": dir_latest,
                    "counts": dir_counts, "fcount": dir_fcount,
                }

        except (OSError, PermissionError):
            pass

        # ── Remonter les tailles bottom-up ───────────────────────────────────
        # Trier les chemins du plus profond au plus court
        sorted_dirs = sorted(dir_info.keys(), key=len, reverse=True)
        for d in sorted_dirs:
            parent = os.path.dirname(d)
            if parent in dir_info and parent != d:
                dir_info[parent]["size"] += dir_info[d]["size"]
                dir_info[parent]["fcount"] += dir_info[d]["fcount"]
                p_latest = dir_info[parent]["latest"]
                c_latest = dir_info[d]["latest"]
                if c_latest > p_latest:
                    dir_info[parent]["latest"] = c_latest
                for cat, sz in dir_info[d]["counts"].items():
                    dir_info[parent]["counts"][cat] = dir_info[parent]["counts"].get(cat, 0) + sz

        # ── Émettre les gros dossiers dormants ───────────────────────────────
        # Passe 1 : projets dev (du plus court au plus long — le projet
        #           absorbe tout son sous-arbre y compris node_modules)
        emitted = set()
        for d in sorted(dir_info.keys(), key=len):
            info = dir_info[d]
            if d == root or d in already:
                continue
            if info["latest"] >= cutoff or info["latest"] == 0:
                continue
            # Skip si déjà couvert par un parent émis
            if any(d.startswith(ed + os.sep) for ed in emitted):
                continue
            is_dev, bloat_size = _detect_dev_project(d)
            if is_dev and bloat_size >= min_size:
                days_ago = int((_time.time() - info["latest"]) / 86400)
                _emit({
                    "name":        os.path.basename(d),
                    "path":        d,
                    "size":        bloat_size,
                    "size_fmt":    fmt_size(bloat_size),
                    "category":    "projet_dev",
                    "file_count":  info["fcount"],
                    "is_file":     False,
                    "last_access": info["latest"],
                    "days_ago":    days_ago,
                    "needs_admin": is_admin_path(d),
                    "confidence":  "sûr",
                    "hint":        "node_modules / .venv régénérables",
                })
                emitted.add(d)
                _subtract_from_ancestors(d, bloat_size, dir_info, root)

        # Passe 2 : dossiers + gros fichiers, bottom-up.
        # On insère les gros fichiers dans dir_info comme pseudo-entrées
        # pour qu'ils participent à la soustraction.
        for fpath, fname, size, atime, cat in big_files:
            dir_info[fpath] = {
                "size": size, "latest": atime,
                "counts": {cat: size}, "fcount": 1,
                "_is_file": True, "_name": fname,
            }

        for d in sorted(dir_info.keys(), key=len, reverse=True):
            info = dir_info[d]
            if d == root or d in already:
                continue
            if info["size"] < min_size:
                continue
            if info["latest"] >= cutoff or info["latest"] == 0:
                continue
            # Skip si couvert par un parent déjà émis
            if any(d.startswith(ed + os.sep) for ed in emitted):
                continue

            is_file = info.get("_is_file", False)

            if is_file:
                fname = info["_name"]
                ext = os.path.splitext(fname)[1].lower()
                cat = _EXT_TO_CAT.get(ext, "autre")
                days_ago = int((_time.time() - info["latest"]) / 86400)
                _emit({
                    "name":        fname,
                    "path":        d,
                    "size":        info["size"],
                    "size_fmt":    fmt_size(info["size"]),
                    "category":    cat,
                    "file_count":  1,
                    "is_file":     True,
                    "last_access": info["latest"],
                    "days_ago":    days_ago,
                    "needs_admin": is_admin_path(d),
                })
            else:
                counts = info["counts"]
                if counts:
                    dominant = max(counts, key=counts.get)
                    if dominant == "autre" and counts[dominant] > info["size"] * 0.6:
                        dominant = "mixte"
                else:
                    dominant = "mixte"

                days_ago = int((_time.time() - info["latest"]) / 86400)
                _emit({
                    "name":        os.path.basename(d),
                    "path":        d,
                    "size":        info["size"],
                    "size_fmt":    fmt_size(info["size"]),
                    "category":    dominant,
                    "file_count":  info["fcount"],
                    "is_file":     False,
                    "last_access": info["latest"],
                    "days_ago":    days_ago,
                    "needs_admin": is_admin_path(d),
                })

            emitted.add(d)
            # Soustraire du parent pour ne pas double-compter
            _subtract_from_ancestors(d, info["size"], dir_info, root)

        _log(f"Scan {root} terminé — {len(results)} trouvaille(s) au total")

    # ── Scans spécialisés ────────────────────────────────────────────────────
    _log("Recherche d'installers inutiles dans Downloads…")
    _scan_installers_in_downloads(min_age_days, installed_names,
                                  lambda item: _emit(item), results)

    _log("Recherche de caches jeux et captures…")
    _scan_game_caches(min_size, lambda item: _emit(item), results)

    results.sort(key=lambda x: x["size"], reverse=True)
    return results


def _subtract_from_ancestors(path, size, dir_info, root):
    """Soustrait une taille de tous les ancêtres d'un chemin."""
    parent = os.path.dirname(path)
    while parent and parent != root and parent in dir_info:
        dir_info[parent]["size"] -= size
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            break
        parent = next_parent


def _is_junction(path):
    """Vérifie si un chemin est un junction/reparse point."""
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes & 0x400)
    except (OSError, PermissionError):
        return True  # en cas de doute, skip


# ──────────────────────────────────────────────────────────────────────────────
# Windows.old
# ──────────────────────────────────────────────────────────────────────────────

def get_windows_old_info():
    """Retourne la présence et la taille de C:\\Windows.old."""
    p = Path("C:/Windows.old")
    if not p.exists():
        return {"exists": False, "size": 0, "size_fmt": "—"}
    size = get_folder_size(str(p))
    return {"exists": True, "size": size, "size_fmt": fmt_size(size)}


def delete_windows_old():
    """Supprime C:\\Windows.old via rd (gère les permissions NTFS). Requiert admin."""
    try:
        r = subprocess.run(
            ["cmd", "/c", "rd", "/s", "/q", r"C:\Windows.old"],
            capture_output=True, timeout=120,
        )
        if r.returncode == 0:
            return True, None
        err = _decode_output(r.stderr).strip()
        return False, err or "Suppression échouée."
    except Exception as e:
        return False, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# Anciens installers
# ──────────────────────────────────────────────────────────────────────────────

_INSTALLER_EXTS = {".exe", ".msi", ".msp", ".iso", ".img", ".zip", ".7z", ".rar"}


def find_old_installers(folder, max_age_days=90, log=None):
    """
    Cherche les fichiers d'installation anciens dans folder.
    Retourne une liste triée par taille décroissante.
    """
    from datetime import datetime, timedelta
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    results = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if Path(entry.name).suffix.lower() not in _INSTALLER_EXTS:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    if st.st_mtime < cutoff:
                        age_days = int((datetime.now().timestamp() - st.st_mtime) / 86400)
                        results.append({
                            "path":        entry.path,
                            "name":        entry.name,
                            "size":        st.st_size,
                            "size_fmt":    fmt_size(st.st_size),
                            "age_days":    age_days,
                            "needs_admin": is_admin_path(entry.path),
                        })
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError) as e:
        if log:
            log(f"Erreur : {e}")
    results.sort(key=lambda x: x["size"], reverse=True)
    if log:
        total = sum(f["size"] for f in results)
        log(f"{len(results)} fichier(s) trouvé(s) — {fmt_size(total)} récupérables")
    return results


def delete_installer_files(paths):
    """Envoie les anciens installeurs à la corbeille Windows."""
    return _recycle_many(paths, label="Anciens installeurs")


def scan_windows_installer_cache():
    """Mesure la taille du cache Windows Installer (C:\\Windows\\Installer\\*.msi/.msp).

    Ce dossier stocke les packages MSI/MSP utilisés par Windows pour les
    réparations/mises à jour. Il peut atteindre 10-30 Go chez les gros
    utilisateurs d'Office/Adobe. Sa suppression manuelle est RISQUÉE car
    elle peut casser les futures réparations/mises à jour — on se contente
    de mesurer et rediriger vers Nettoyage de disque (cleanmgr.exe) qui
    sait identifier les packages vraiment obsolètes.

    Retourne {items (top 30), total, total_fmt, count, error}.
    """
    cache = Path(r"C:\Windows\Installer")
    if not cache.exists():
        return {"items": [], "total": 0, "count": 0, "error": "Dossier introuvable"}

    items = []
    total = 0
    count = 0
    try:
        for entry in cache.iterdir():
            if not entry.is_file():
                continue
            suffix = entry.suffix.lower()
            if suffix not in (".msi", ".msp"):
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            total += size
            count += 1
            items.append({
                "path":     str(entry),
                "name":     entry.name,
                "size":     size,
                "size_fmt": fmt_size(size),
                "type":     suffix[1:],
            })
    except OSError as e:
        return {"items": [], "total": 0, "count": 0, "error": str(e)}

    items.sort(key=lambda x: x["size"], reverse=True)
    return {
        "items":     items[:30],  # top 30 pour l'affichage
        "total":     total,
        "total_fmt": fmt_size(total),
        "count":     count,
        "error":     None,
    }


def launch_disk_cleanup():
    """Lance cleanmgr.exe (Nettoyage de disque Windows) qui sait nettoyer
    le cache Windows Installer de façon sûre via l'API Microsoft."""
    try:
        subprocess.Popen(["cleanmgr.exe"], creationflags=0x08000000)
        return True, None
    except Exception as e:
        return False, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# Bilan de santé
# ──────────────────────────────────────────────────────────────────────────────

def get_health_data():
    """Calcule un score de santé (0-100) et retourne les métriques détaillées."""
    metrics = []
    total   = 0

    # Disque (20 pts)
    try:
        import psutil
        parts = [p for p in psutil.disk_partitions(all=False) if p.mountpoint]
        if parts:
            u = psutil.disk_usage(parts[0].mountpoint)
            free_pct = u.free / u.total * 100
            if free_pct >= 20:   pts, st = 20, "good"
            elif free_pct >= 10: pts, st = 10, "warn"
            else:                pts, st = 0,  "bad"
            total += pts
            metrics.append({"id": "disk", "label": "Espace disque", "icon": "💾",
                             "status": st, "score": pts, "max": 20,
                             "value": fmt_size(u.free) + " libres",
                             "detail": f"{round(100 - free_pct)}% du disque utilisé",
                             "action": None})
    except Exception:
        pass

    # Fichiers temporaires (20 pts)
    try:
        sz = estimate_temp()
        if sz < 100 * 1024**2:   pts, st = 20, "good"
        elif sz < 500 * 1024**2: pts, st = 10, "warn"
        else:                     pts, st = 0,  "bad"
        total += pts
        metrics.append({"id": "temp", "label": "Fichiers temporaires", "icon": "🗑",
                         "status": st, "score": pts, "max": 20,
                         "value": fmt_size(sz),
                         "detail": "%TEMP%, %TMP%, C:\\Windows\\Temp",
                         "action": "temp"})
    except Exception:
        pass

    # Cache navigateurs (20 pts)
    try:
        sz = estimate_browser_cache()
        if sz < 100 * 1024**2:   pts, st = 20, "good"
        elif sz < 500 * 1024**2: pts, st = 10, "warn"
        else:                     pts, st = 0,  "bad"
        total += pts
        metrics.append({"id": "browser", "label": "Cache navigateurs", "icon": "🌐",
                         "status": st, "score": pts, "max": 20,
                         "value": fmt_size(sz),
                         "detail": "Chrome, Edge, Firefox, Brave",
                         "action": "browser"})
    except Exception:
        pass

    # Programmes au démarrage (20 pts)
    try:
        entries = get_autorun_entries()
        n = len([e for e in entries if e.get("enabled")])
        if n <= 5:    pts, st = 20, "good"
        elif n <= 15: pts, st = 10, "warn"
        else:         pts, st = 5,  "bad"
        total += pts
        metrics.append({"id": "startup", "label": "Démarrage", "icon": "🚀",
                         "status": st, "score": pts, "max": 20,
                         "value": f"{n} programme{'s' if n > 1 else ''} actif{'s' if n > 1 else ''}",
                         "detail": "Programmes lancés automatiquement avec Windows",
                         "action": None})
    except Exception:
        pass

    # Corbeille (10 pts)
    try:
        sz = estimate_recycle_bin()
        if sz == 0:                 pts, st = 10, "good"
        elif sz < 100 * 1024**2:    pts, st = 5,  "warn"
        else:                        pts, st = 0,  "bad"
        total += pts
        metrics.append({"id": "recycle", "label": "Corbeille", "icon": "♻️",
                         "status": st, "score": pts, "max": 10,
                         "value": fmt_size(sz) if sz > 0 else "Vide",
                         "detail": "Fichiers en attente de suppression définitive",
                         "action": "recycle"})
    except Exception:
        pass

    # Cache applications (10 pts)
    try:
        sz = estimate_app_caches()
        if sz < 50 * 1024**2:    pts, st = 10, "good"
        elif sz < 200 * 1024**2: pts, st = 5,  "warn"
        else:                     pts, st = 0,  "bad"
        total += pts
        metrics.append({"id": "appcache", "label": "Cache applications", "icon": "📦",
                         "status": st, "score": pts, "max": 10,
                         "value": fmt_size(sz),
                         "detail": "Discord, Teams, Slack, Spotify…",
                         "action": "appcache"})
    except Exception:
        pass

    # Santé disque S.M.A.R.T. (10 pts)
    try:
        disks = get_disk_smart()
        if disks:
            all_ok = all(d["healthy"] for d in disks)
            any_bad = any(not d["healthy"] and d["status"].lower() not in ("unknown", "") for d in disks)
            if all_ok:         pts, st = 10, "good"
            elif not any_bad:  pts, st = 5,  "warn"
            else:              pts, st = 0,  "bad"
            total += pts
            detail = ", ".join(d["model"][:28] for d in disks[:2])
            metrics.append({"id": "smart", "label": "Santé du disque", "icon": "🖥️",
                             "status": st, "score": pts, "max": 10,
                             "value": "OK" if all_ok else "Attention",
                             "detail": detail or "Disque physique",
                             "action": None})
    except Exception:
        pass

    max_score = sum(m["max"] for m in metrics)
    return {"score": total, "max": max_score, "metrics": metrics}


# ──────────────────────────────────────────────────────────────────────────────
# Registre des tâches de nettoyage
# ──────────────────────────────────────────────────────────────────────────────

TASKS = [
    # ── Système ───────────────────────────────────────────────────────────────
    {
        "id": "temp",      "label": "Fichiers temporaires",
        "desc": "Libère de l'espace disque en supprimant les fichiers temporaires générés par Windows et les applications. Sans effet sur les performances — ces fichiers sont recréés à la demande.",
        "admin": True,  "default": True,  "group": "system",
        "fn": task_temp,   "estimate_fn": estimate_temp,
        "impact": "disque",
    },
    {
        "id": "recycle",   "label": "Corbeille",
        "desc": "Vide définitivement la corbeille Windows. Les fichiers ne seront plus récupérables après cette opération.",
        "admin": False, "default": True,  "group": "system",
        "fn": task_recycle_bin, "estimate_fn": estimate_recycle_bin,
        "impact": "disque",
    },
    {
        "id": "dns",       "label": "Cache DNS",
        "desc": "Vide le cache de résolution de noms de domaine. Utile si un site a changé d'adresse ou si la résolution est lente. Se reconstruit automatiquement.",
        "admin": False, "default": True,  "group": "system",
        "fn": task_dns,    "estimate_fn": lambda: 0,
        "impact": "réseau",
    },
    {
        "id": "recent",    "label": "Fichiers récents Windows",
        "desc": "Supprime la liste des fichiers ouverts récemment (menu Démarrer, Accès rapide). N'efface pas les fichiers eux-mêmes, juste les raccourcis.",
        "admin": False, "default": True,  "group": "system",
        "fn": task_recent_files, "estimate_fn": estimate_recent_files,
        "impact": "confidentialité",
    },
    {
        "id": "dumps",     "label": "Fichiers de vidage mémoire",
        "desc": "Supprime les fichiers .dmp générés lors des crashs Windows ou applicatifs. Utile pour le débogage mais rarement nécessaire au quotidien.",
        "admin": False, "default": False, "group": "system",
        "fn": task_dumps,  "estimate_fn": estimate_dumps,
        "impact": "disque",
    },
    {
        "id": "prefetch",  "label": "Prefetch Windows",
        "desc": "Efface le cache de pré-chargement Windows (C:\\Windows\\Prefetch). Accélère les lancements fréquents — le vider force Windows à le reconstruire, ce qui peut ralentir temporairement les premiers lancements.",
        "admin": True,  "default": False, "group": "system",
        "fn": task_prefetch, "estimate_fn": estimate_prefetch,
        "impact": "disque",
    },
    {
        "id": "wu",        "label": "Cache Windows Update",
        "desc": "Supprime les fichiers de mises à jour déjà installées. Libère souvent plusieurs Go. Les prochaines mises à jour seront re-téléchargées si nécessaire.",
        "admin": True,  "default": False, "group": "system",
        "fn": task_windows_update, "estimate_fn": estimate_windows_update,
        "impact": "disque",
    },
    {
        "id": "eventlogs", "label": "Journaux d'événements",
        "desc": "Vide les journaux Windows (Application, Système, Sécurité). Utile si les logs sont volumineux. Attention : les informations de diagnostic passé seront perdues.",
        "admin": True,  "default": False, "group": "system",
        "fn": task_event_logs, "estimate_fn": estimate_event_logs,
        "impact": "disque",
    },
    {
        "id": "fontcache", "label": "Cache des polices",
        "desc": "Supprime le cache de rendu des polices Windows. Résout les problèmes d'affichage de polices corrompues. Se reconstruit automatiquement au redémarrage.",
        "admin": True,  "default": False, "group": "system",
        "fn": task_font_cache, "estimate_fn": estimate_font_cache,
        "impact": "disque",
    },
    # ── Navigateurs ───────────────────────────────────────────────────────────
    {
        "id": "browser",   "label": "Cache navigateurs",
        "desc": "Supprime les fichiers temporaires web (images, scripts, CSS) de Chrome, Edge, Brave et Firefox. Vos mots de passe et sessions sont protégés. Les sites se rechargeront plus lentement la première visite.",
        "admin": False, "default": True,  "group": "browser",
        "fn": task_browser_cache, "estimate_fn": estimate_browser_cache,
        "impact": "disque",
    },
    {
        "id": "history",   "label": "Historique de navigation",
        "desc": "Efface la liste des sites visités, les recherches effectuées et la liste des téléchargements. Ne supprime pas les fichiers téléchargés eux-mêmes.",
        "admin": False, "default": False, "group": "browser",
        "fn": task_browser_history, "estimate_fn": estimate_history,
        "impact": "confidentialité",
    },
    {
        "id": "cookies",   "label": "Cookies",
        "desc": "Supprime tous les cookies de navigation. Conséquence : vous serez déconnecté de tous les sites web (Gmail, Facebook, etc.). Les identifiants enregistrés dans le gestionnaire de mots de passe ne sont pas touchés.",
        "admin": False, "default": False, "group": "browser",
        "fn": task_browser_cookies, "estimate_fn": estimate_cookies,
        "impact": "confidentialité",
    },
    # ── Applications ──────────────────────────────────────────────────────────
    {
        "id": "thumbnails","label": "Cache miniatures",
        "desc": "Supprime les fichiers thumbcache de l'Explorateur Windows. Résout les miniatures corrompues ou obsolètes. Se reconstruit automatiquement en parcourant vos dossiers.",
        "admin": False, "default": True,  "group": "apps",
        "fn": task_thumbnails, "estimate_fn": estimate_thumbnails,
        "impact": "disque",
    },
    {
        "id": "appcache",  "label": "Cache applications",
        "desc": "Supprime le cache de Discord, Teams, Slack, Spotify et WhatsApp. Ces applications recréeront leur cache au prochain lancement — les conversations et fichiers ne sont pas touchés.",
        "admin": False, "default": True,  "group": "apps",
        "fn": task_app_caches, "estimate_fn": estimate_app_caches,
        "impact": "disque",
    },
]
