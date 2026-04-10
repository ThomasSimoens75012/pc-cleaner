"""
cleaner.py — Fonctions de nettoyage + outils système Windows
"""

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import ctypes
from collections import defaultdict
from pathlib import Path


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
    try:
        for dirpath, _, filenames in os.walk(folder):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
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


def estimate_recent_files():
    return get_folder_size(Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent")


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
    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    freed, _ = delete_folder_contents(recent)
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
    import winreg
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
    import winreg
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
    import winreg
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

    duplicates = {h: files for h, files in hashes.items() if len(files) > 1}
    total_wasted = sum(
        sum(f["size"] for f in files[1:])
        for files in duplicates.values()
    )
    if log:
        log(f"{len(duplicates)} groupe(s) de doublons — {fmt_size(total_wasted)} récupérables.")
    return duplicates


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


# ──────────────────────────────────────────────────────────────────────────────
# Registre Windows — nettoyeur
# ──────────────────────────────────────────────────────────────────────────────

def scan_registry(log=None):
    """
    Analyse le registre pour détecter les entrées orphelines (valeurs seulement).
    Retourne une liste de dicts {id, category, hive, key, value_name, description}.
    """
    import winreg
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
    import winreg
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
    import json as _json
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
                        m = _json.load(f)
                    name = m.get("name", eid_dir.name)
                    # Résolution i18n __MSG_xxx__
                    if name.startswith("__MSG_"):
                        msg_key = name[6:].rstrip("_")
                        for lang in ["en", "fr"]:
                            mp = versions[-1] / "_locales" / lang / "messages.json"
                            if mp.exists():
                                try:
                                    msgs = _json.loads(mp.read_text(encoding="utf-8", errors="ignore"))
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
                data = _json.loads(ext_json.read_text(encoding="utf-8", errors="ignore"))
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
    """
    Retourne la liste des dossiers vides (sans aucun fichier dans tout le sous-arbre).
    Exclut les dossiers système et les points de jonction NTFS.
    """
    _SKIP = {"$Recycle.Bin", "System Volume Information", "Windows", "Program Files",
             "Program Files (x86)", "ProgramData"}
    results = []

    try:
        root = Path(folder).resolve()
    except Exception:
        return []

    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        if p.name in _SKIP:
            dirnames.clear()
            continue
        # Ignore les points de jonction / liens symboliques
        try:
            if p.is_symlink() or p.stat().st_file_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                continue
        except (OSError, AttributeError):
            pass
        try:
            # Un dossier est vide si aucun fichier ni sous-dossier non-vide dedans
            has_content = bool(filenames) or any(
                Path(dirpath, d) not in [Path(r["path"]) for r in results]
                and not any(Path(dirpath, d) == Path(r["path"]).parent for r in results)
                for d in dirnames
            )
        except Exception:
            continue

        # Recalcul simple : dossier vide = stat walk retourne 0 fichiers dans tout le sous-arbre
        total_files = sum(1 for _ in Path(dirpath).rglob("*") if Path(os.path.join(dirpath, _)).is_file() if False)
        # Approche directe et fiable
        try:
            total_files = sum(
                1 for entry in os.scandir(dirpath)
                if entry.is_file(follow_symlinks=False)
            )
            sub_dirs_empty = all(
                Path(os.path.join(dirpath, entry.name)) in {Path(r["path"]) for r in results}
                for entry in os.scandir(dirpath)
                if entry.is_dir(follow_symlinks=False)
            )
            if total_files == 0 and sub_dirs_empty and dirpath != str(root):
                results.append({"path": dirpath, "name": p.name, "needs_admin": is_admin_path(dirpath)})
        except (PermissionError, OSError):
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
    import winreg

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
            ["powershell", "-Command", ps_cmd],
            capture_output=True, timeout=10
        )
        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        if r.returncode != 0:
            return {"points": [], "requires_admin": True, "error": None}
        if not stdout or stdout == "null":
            return {"points": [], "requires_admin": False, "error": None}
        import json as _json
        from datetime import datetime as _dt
        raw = _json.loads(stdout)
        if isinstance(raw, dict):
            raw = [raw]
        points = []
        for p in raw:
            date_str = str(p.get("CT", ""))[:14]
            try:
                date_fmt = _dt.strptime(date_str, "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M")
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
    Récupère l'état S.M.A.R.T. des disques physiques via wmic.
    Retourne une liste de dicts {model, size, size_fmt, status, healthy}.
    """
    disks = []
    try:
        r = subprocess.run(
            ["wmic", "diskdrive", "get", "model,size,status"],
            capture_output=True, timeout=5
        )
        lines = r.stdout.decode("utf-8", errors="replace").strip().splitlines()
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            # Dernière colonne : status, avant-dernière : size, reste : model
            parts = line.rsplit(None, 2)
            if len(parts) < 3:
                continue
            model, size_raw, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
            try:
                size = int(size_raw)
            except ValueError:
                size = 0
            healthy = status.lower() == "ok"
            disks.append({
                "model":    model,
                "size":     size,
                "size_fmt": fmt_size(size),
                "status":   status,
                "healthy":  healthy,
            })
    except Exception:
        pass
    return disks


# ──────────────────────────────────────────────────────────────────────────────
# Mises à jour logicielles (winget)
# ──────────────────────────────────────────────────────────────────────────────

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
        enc    = "cp1252" if sys.platform == "win32" else "utf-8"
        output = r.stdout.decode(enc, errors="replace")
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

    # Fichiers récents (MRU Explorateur)
    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
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
        import winreg
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
        recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
        for f in recent.glob("*.lnk"):
            try:
                f.unlink()
                cleaned += 1
            except Exception as e:
                errors.append(str(e))

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
            import winreg
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
