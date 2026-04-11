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


def delete_folder_contents(folder):
    freed = 0
    errors = 0
    folder = Path(folder)
    if not folder.exists():
        return 0, 0
    for item in folder.iterdir():
        try:
            size = get_folder_size(item) if item.is_dir() else item.stat().st_size
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=False)
            else:
                item.unlink()
            freed += size
        except (OSError, PermissionError):
            errors += 1
    return freed, errors


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


def estimate_browser_cache():
    local   = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    total = 0
    for _, profile in _browser_profile_paths():
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
    for kind, profile in _browser_profile_paths():
        if kind == "chromium":
            total += (profile / "History").stat().st_size if (profile / "History").exists() else 0
        elif kind == "firefox":
            total += (profile / "places.sqlite").stat().st_size if (profile / "places.sqlite").exists() else 0
    return total


def estimate_cookies():
    total = 0
    for kind, profile in _browser_profile_paths():
        if kind == "chromium":
            total += (profile / "Cookies").stat().st_size if (profile / "Cookies").exists() else 0
        elif kind == "firefox":
            total += (profile / "cookies.sqlite").stat().st_size if (profile / "cookies.sqlite").exists() else 0
    return total


def _recent_files_dir():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"


def _purge_recent_shortcuts():
    """Supprime les raccourcis .lnk du dossier Recent. Retourne (count, freed, errors)."""
    count, freed, errors = 0, 0, []
    for f in _recent_files_dir().glob("*.lnk"):
        try:
            size = f.stat().st_size
            f.unlink()
            count += 1
            freed += size
        except Exception as e:
            errors.append(str(e))
    return count, freed, errors


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
    for kind, profile in _browser_profile_paths():
        freed = 0
        browser = profile.parent.parent.name if kind == "chromium" else "Firefox"
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
    if browser_totals:
        for browser, freed in browser_totals.items():
            log(f"Cache {browser} — {fmt_size(freed)} libérés")
    else:
        log("Cache navigateurs — déjà propre")
    return total


def task_browser_history(log):
    total = 0
    browser_totals = {}
    for kind, profile in _browser_profile_paths():
        browser = profile.parent.parent.name if kind == "chromium" else "Firefox"
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
    if browser_totals:
        for browser, freed in browser_totals.items():
            log(f"Historique {browser} — {fmt_size(freed)} libérés")
    else:
        log("Historique navigateurs — déjà propre")
    return total


def task_browser_cookies(log):
    total = 0
    browser_totals = {}
    for kind, profile in _browser_profile_paths():
        browser = profile.parent.parent.name if kind == "chromium" else "Firefox"
        if kind == "chromium":
            freed = _sqlite_clean(profile / "Cookies", ["DELETE FROM cookies"], log)
        elif kind == "firefox":
            freed = _sqlite_clean(profile / "cookies.sqlite", ["DELETE FROM moz_cookies"], log)
        else:
            freed = 0
        if freed:
            browser_totals[browser] = browser_totals.get(browser, 0) + freed
        total += freed
    if browser_totals:
        for browser, freed in browser_totals.items():
            log(f"Cookies {browser} — {fmt_size(freed)} libérés")
    else:
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
    freed = 0
    if d.exists():
        for item in d.glob("thumbcache_*.db"):
            try:
                size = item.stat().st_size
                item.unlink()
                freed += size
            except (OSError, PermissionError):
                pass
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
    total, count = 0, 0
    for d in search_dirs:
        if not d.exists():
            continue
        for ext in ["*.dmp", "*.mdmp"]:
            for f in list(d.glob(ext)):
                try:
                    size = f.stat().st_size
                    f.unlink()
                    total += size
                    count += 1
                except (OSError, PermissionError):
                    pass
    if total > 0:
        log(f"Fichiers crash — {count} fichier(s) supprimé(s), {fmt_size(total)} libérés")
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
        try:
            freed += fntcache.stat().st_size
            fntcache.unlink()
        except (OSError, PermissionError):
            pass

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
# Outils — Programmes au démarrage
# ──────────────────────────────────────────────────────────────────────────────

def get_startup_entries():
    """
    Retourne la liste des programmes au démarrage depuis le registre.
    Lit aussi l'état activé/désactivé depuis StartupApproved.
    """
    entries = []

    run_keys = [
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",      "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",      "HKLM"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM32"),
    ]
    approved_keys = {
        "HKCU":   (winreg.HKEY_CURRENT_USER,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
        "HKLM":   (winreg.HKEY_LOCAL_MACHINE,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
        "HKLM32": (winreg.HKEY_LOCAL_MACHINE,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"),
    }

    # Pré-charge les états approuvés
    approved = {}
    for source, (hive, subkey) in approved_keys.items():
        try:
            key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                    # Premier octet: 0x02 = activé, 0x03 = désactivé
                    approved[(source, name)] = (data[0] == 0x02) if data else True
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass

    for hive, subkey, source in run_keys:
        try:
            key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    enabled = approved.get((source, name), True)
                    entries.append({
                        "name":     name,
                        "command":  value,
                        "source":   source,
                        "key_path": subkey,
                        "enabled":  enabled,
                    })
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass

    return sorted(entries, key=lambda e: e["name"].lower())


def set_startup_entry(name, source, enabled):
    """Active ou désactive un programme au démarrage via StartupApproved."""
    approved_map = {
        "HKCU":   (winreg.HKEY_CURRENT_USER,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
        "HKLM":   (winreg.HKEY_LOCAL_MACHINE,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
        "HKLM32": (winreg.HKEY_LOCAL_MACHINE,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"),
    }
    if source not in approved_map:
        return False
    hive, subkey = approved_map[source]
    try:
        key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE)
        # 12 octets : premier octet 0x02=activé, 0x03=désactivé, reste = zeros
        data = bytes([0x02 if enabled else 0x03]) + b"\x00" * 11
        winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Outils — Applications installées
# ──────────────────────────────────────────────────────────────────────────────

def get_installed_apps():
    """Lit la liste des applications installées depuis le registre Windows."""
    apps = []
    seen = set()

    uninstall_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for hive, path in uninstall_paths:
        try:
            key = winreg.OpenKey(hive, path)
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, i)
                    sub = winreg.OpenKey(key, sub_name)

                    def val(k, default=""):
                        try:
                            return str(winreg.QueryValueEx(sub, k)[0])
                        except Exception:
                            return default

                    name = val("DisplayName")
                    if not name or name in seen:
                        i += 1
                        winreg.CloseKey(sub)
                        continue

                    # Ignore les entrées système sans désinstalleur
                    uninstall = val("UninstallString")
                    if not uninstall and not val("DisplayVersion"):
                        i += 1
                        winreg.CloseKey(sub)
                        continue

                    seen.add(name)
                    size_kb = 0
                    try:
                        size_kb = int(winreg.QueryValueEx(sub, "EstimatedSize")[0])
                    except Exception:
                        pass

                    apps.append({
                        "name":             name,
                        "version":          val("DisplayVersion"),
                        "publisher":        val("Publisher"),
                        "install_date":     val("InstallDate"),
                        "size_kb":          size_kb,
                        "size_fmt":         fmt_size(size_kb * 1024) if size_kb else "—",
                        "uninstall_string": uninstall,
                    })
                    winreg.CloseKey(sub)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass

    apps.sort(key=lambda x: x["name"].lower())
    return apps


def launch_uninstaller(uninstall_string):
    """
    Lance le désinstalleur via ShellExecuteW (déclenche l'UAC si nécessaire).
    Fallback sur Popen si l'appel COM échoue.
    """
    import shlex
    try:
        parts = shlex.split(uninstall_string, posix=False)
        exe   = parts[0].strip('"').strip("'")
        args  = " ".join(parts[1:]) if len(parts) > 1 else None
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, args, None, 1)
        return int(ret) > 32   # >32 = succès selon l'API Win32
    except Exception:
        try:
            subprocess.Popen(uninstall_string, shell=True)
            return True
        except Exception:
            return False


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
    """Supprime une liste de fichiers (chemins en doublon sélectionnés par l'utilisateur)."""
    freed = 0
    errors = []
    for path in paths:
        try:
            p = Path(path)
            freed += p.stat().st_size
            p.unlink()
        except Exception as e:
            errors.append(str(e))
    return freed, errors


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
    """Supprime récursivement une liste de dossiers."""
    import shutil
    freed = 0
    errors = []
    for path in paths:
        try:
            p = Path(path)
            if not p.is_dir():
                errors.append(f"{path}: n'est pas un dossier")
                continue
            # Calcule la taille avant suppression
            size = 0
            for r, _dirs, files in os.walk(p):
                for f in files:
                    try:
                        size += (Path(r) / f).stat().st_size
                    except Exception:
                        pass
            shutil.rmtree(p)
            freed += size
        except Exception as e:
            errors.append(f"{path}: {e}")
    return freed, errors


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
            shutil.rmtree(p)
            return True, None
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
    """Supprime les raccourcis .lnk sélectionnés. Retourne (deleted, errors)."""
    deleted, errors = 0, 0
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
            deleted += 1
        except Exception:
            errors += 1
    return deleted, errors


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
    """Supprime les dossiers vides. Retourne (deleted, errors)."""
    deleted, errors = 0, []
    # Trie du plus profond au moins profond pour supprimer les enfants d'abord
    for p in sorted(paths, key=lambda x: x.count(os.sep), reverse=True):
        try:
            Path(p).rmdir()
            deleted += 1
        except PermissionError:
            errors.append(f"{p} : accès refusé (droits administrateur requis)")
        except OSError as e:
            if getattr(e, "winerror", None) == 145:  # ERROR_DIR_NOT_EMPTY
                errors.append(f"{p} : dossier non vide")
            else:
                errors.append(f"{p} : {e.strerror or e}")
        except Exception as e:
            errors.append(f"{p} : {e}")
    return deleted, errors


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
    """Supprime les dossiers orphelins sélectionnés. Retourne (deleted, errors).

    Gère les fichiers read-only (courant dans Program Files) en chmod + retry.
    """
    import stat

    def _on_rm_error(func, path, exc_info):
        # Tente de retirer le read-only puis re-essaie l'operation
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            raise  # laisse remonter l'exception originale

    deleted, errors = 0, []
    for p in paths:
        try:
            if not Path(p).exists():
                errors.append(f"{p}: dossier introuvable")
                continue
            shutil.rmtree(p, onerror=_on_rm_error)
            deleted += 1
        except PermissionError:
            errors.append(
                f"{Path(p).name} : accès refusé (droits administrateur requis)"
            )
        except OSError as e:
            winerr = getattr(e, "winerror", 0)
            if winerr == 5:  # ACCESS_DENIED
                errors.append(
                    f"{Path(p).name} : accès refusé (droits administrateur requis)"
                )
            elif winerr == 32:  # SHARING_VIOLATION
                errors.append(
                    f"{Path(p).name} : fichier en cours d'utilisation"
                )
            else:
                errors.append(f"{Path(p).name} : {e}")
        except Exception as e:
            errors.append(f"{Path(p).name} : {e}")
    return deleted, errors


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
                "healthy":  status.lower() == "healthy",
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
        out = r.stdout.decode("utf-8", errors="replace")
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
        services_prev = {}
        try:
            states = {s["name"]: s for s in get_services_state()}
            for name in _GAMING_SERVICES_TO_STOP:
                st = states.get(name)
                if st and st.get("enabled") is not None:
                    services_prev[name] = bool(st.get("enabled"))
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
    for name, prev_enabled in (state.get("services_prev") or {}).items():
        ok, err = set_service_enabled(name, bool(prev_enabled))
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

    # 2. Fichier baseline
    try:
        if _BASELINE_PATH.exists():
            size = _BASELINE_PATH.stat().st_size
            baseline = _load_tweak_baseline()
            count = len(baseline)
            checks.append({"id": "baseline", "label": "Baseline mesures", "status": "ok",
                           "detail": f"{count} entrée(s) mesurée(s) stockées ({size} octets)"})
        else:
            checks.append({"id": "baseline", "label": "Baseline mesures", "status": "warn",
                           "detail": "Aucune mesure encore collectée (fichier absent)"})
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
    "copilot":   ["copilot.exe", "microsoft.copilot.native.exe", "copilotruntime.exe"],
    "game_dvr":  ["broadcastdvrserver.exe", "gamesvr.exe"],
    "game_bar":  ["gamebar.exe", "gamebarft.exe", "gamebarelevatedft_plus.exe"],
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


def get_windows_tweaks():
    result = {"groups": [], "items": []}
    for gid, glabel in _TWEAK_GROUPS:
        result["groups"].append({"id": gid, "label": glabel})

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

        result["items"].append({
            "id":    t["id"],
            "label": t["label"],
            "desc":  t["desc"],
            "group": t["group"],
            "active": active,
            "tags":   tags,
            "min_windows": min_win,
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
        out.append({
            "id":           pid,
            "label":        data["label"],
            "desc":         data["desc"],
            "count":        len(tweaks_off) + len(services_off) + len(tasks_off),
            "tweaks_off":   tweaks_off,
            "services_off": services_off,
            "tasks_off":    tasks_off,
        })
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
        err = r.stderr.decode("utf-8", errors="replace").strip() or "reg.exe a échoué"
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
        out = r.stdout.decode("utf-8", errors="replace").strip()
        err = r.stderr.decode("utf-8", errors="replace").strip()
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
    for t in targets:
        if t.is_file():
            try:
                t.unlink()
                steps.append(f"Supprimé : {t.name}")
            except Exception as e:
                steps.append(f"Erreur {t.name} : {e}")
        elif t.is_dir():
            try:
                for f in t.glob("iconcache*"):
                    try: f.unlink()
                    except: pass
                for f in t.glob("thumbcache*"):
                    try: f.unlink()
                    except: pass
                steps.append(f"Nettoyé : {t.name}")
            except Exception as e:
                steps.append(f"Erreur {t.name} : {e}")
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
            text=True, encoding="utf-8", errors="replace",
            creationflags=0x08000000,
        )
        for line in proc.stdout:
            line = line.rstrip()
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
        is_disabled = start == "disabled"
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


def set_service_enabled(service_name, enabled):
    """Active ou désactive un service. Nécessite admin.

    enabled=True  → StartupType Manual (safe default, ne force pas Automatic)
    enabled=False → StartupType Disabled
    """
    if service_name not in {s["name"] for s in _WINDOWS_SERVICES_TO_DISABLE}:
        return False, "Service non whitelisté"
    target = "Manual" if enabled else "Disabled"
    ps_cmd = f"Set-Service -Name '{service_name}' -StartupType {target} -ErrorAction Stop"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, timeout=15, creationflags=0x08000000,
        )
        if r.returncode == 0:
            return True, None
        err = r.stderr.decode("utf-8", errors="replace").strip()
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
                out = r.stdout.decode("utf-8", errors="replace")
                # CSV format: "TaskName","Next Run Time","Status"
                if "Disabled" in out or "Désactivé" in out or "D\u00e9sactiv\u00e9" in out:
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


def set_scheduled_task_enabled(task_path, enabled):
    """Active ou désactive une tâche planifiée. Nécessite admin pour les tâches système."""
    if task_path not in {t["path"] for t in _SCHEDULED_TASKS_TO_DISABLE}:
        return False, "Tâche non whitelistée"
    action = "/ENABLE" if enabled else "/DISABLE"
    try:
        r = subprocess.run(
            ["schtasks", "/Change", "/TN", task_path, action],
            capture_output=True, timeout=10, creationflags=0x08000000,
        )
        if r.returncode == 0:
            return True, None
        err = r.stderr.decode("utf-8", errors="replace").strip()
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
        err = r.stderr.decode("utf-8", errors="replace").strip()
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
        output = r.stdout.decode("utf-8", errors="replace")
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

    recent = _recent_files_dir()
    if recent.exists():
        files = list(recent.glob("*.lnk"))
        size  = sum(f.stat().st_size for f in files if f.is_file())
        items.append({
            "id":    "recent_files",
            "label": "Fichiers récents",
            "desc":  "Raccourcis vers les fichiers ouverts récemment (Explorateur Windows)",
            "count": len(files),
            "size":  size,
            "size_fmt": fmt_size(size),
        })

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

    if "recent_files" in ids:
        count, _, errs = _purge_recent_shortcuts()
        cleaned += count
        errors.extend(errs)

    if "jump_lists" in ids:
        jl_dirs = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent" / "AutomaticDestinations",
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent" / "CustomDestinations",
        ]
        for d in jl_dirs:
            if d.exists():
                for f in d.iterdir():
                    if f.is_file():
                        try:
                            f.unlink()
                            cleaned += 1
                        except Exception as e:
                            errors.append(str(e))

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
        return r.returncode == 0, r.stderr.decode("utf-8", errors="replace").strip()
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
        err = r.stderr.decode("utf-8", errors="replace").strip()
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
    """Supprime les fichiers sélectionnés. Retourne (freed_bytes, errors)."""
    freed, errors = 0, []
    for p in paths:
        try:
            size = Path(p).stat().st_size
            Path(p).unlink()
            freed += size
        except Exception as e:
            errors.append(f"{p}: {e}")
    return freed, errors


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
        entries = get_startup_entries()
        n = len([e for e in entries if e["enabled"]])
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
        "desc": "%TEMP%, %TMP%, C:\\Windows\\Temp",
        "admin": True,  "default": True,  "group": "system",
        "fn": task_temp,   "estimate_fn": estimate_temp,
    },
    {
        "id": "recycle",   "label": "Corbeille",
        "desc": "Vide définitivement la corbeille",
        "admin": False, "default": True,  "group": "system",
        "fn": task_recycle_bin, "estimate_fn": estimate_recycle_bin,
    },
    {
        "id": "dns",       "label": "Cache DNS",
        "desc": "ipconfig /flushdns",
        "admin": False, "default": True,  "group": "system",
        "fn": task_dns,    "estimate_fn": lambda: 0,
    },
    {
        "id": "recent",    "label": "Fichiers récents Windows",
        "desc": "Liste des fichiers ouverts récemment (menu Démarrer)",
        "admin": False, "default": True,  "group": "system",
        "fn": task_recent_files, "estimate_fn": estimate_recent_files,
    },
    {
        "id": "dumps",     "label": "Fichiers de vidage mémoire",
        "desc": "Fichiers .dmp et .mdmp (crash dumps)",
        "admin": False, "default": False, "group": "system",
        "fn": task_dumps,  "estimate_fn": estimate_dumps,
    },
    {
        "id": "prefetch",  "label": "Prefetch Windows",
        "desc": "C:\\Windows\\Prefetch",
        "admin": True,  "default": False, "group": "system",
        "fn": task_prefetch, "estimate_fn": estimate_prefetch,
    },
    {
        "id": "wu",        "label": "Cache Windows Update",
        "desc": "SoftwareDistribution\\Download",
        "admin": True,  "default": False, "group": "system",
        "fn": task_windows_update, "estimate_fn": estimate_windows_update,
    },
    {
        "id": "eventlogs", "label": "Journaux d'événements",
        "desc": "Application, Système, Sécurité, Installation",
        "admin": True,  "default": False, "group": "system",
        "fn": task_event_logs, "estimate_fn": estimate_event_logs,
    },
    {
        "id": "fontcache", "label": "Cache des polices",
        "desc": "Cache FontCache Windows — reconstruit automatiquement",
        "admin": True,  "default": False, "group": "system",
        "fn": task_font_cache, "estimate_fn": estimate_font_cache,
    },
    # ── Navigateurs ───────────────────────────────────────────────────────────
    {
        "id": "browser",   "label": "Cache navigateurs",
        "desc": "Chrome, Edge, Brave, Firefox — mots de passe protégés",
        "admin": False, "default": True,  "group": "browser",
        "fn": task_browser_cache, "estimate_fn": estimate_browser_cache,
    },
    {
        "id": "history",   "label": "Historique de navigation",
        "desc": "URLs visitées, recherches, téléchargements",
        "admin": False, "default": False, "group": "browser",
        "fn": task_browser_history, "estimate_fn": estimate_history,
    },
    {
        "id": "cookies",   "label": "Cookies",
        "desc": "Supprime tous les cookies (déconnexion des sites)",
        "admin": False, "default": False, "group": "browser",
        "fn": task_browser_cookies, "estimate_fn": estimate_cookies,
    },
    # ── Applications ──────────────────────────────────────────────────────────
    {
        "id": "thumbnails","label": "Cache miniatures",
        "desc": "Fichiers thumbcache_*.db de l'Explorateur",
        "admin": False, "default": True,  "group": "apps",
        "fn": task_thumbnails, "estimate_fn": estimate_thumbnails,
    },
    {
        "id": "appcache",  "label": "Cache applications",
        "desc": "Discord, Teams, Slack, Spotify, WhatsApp",
        "admin": False, "default": True,  "group": "apps",
        "fn": task_app_caches, "estimate_fn": estimate_app_caches,
    },
]
