import os
from pathlib import Path

import psutil


def normalize_windows_path(path: str) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except OSError:
        return os.path.normcase(os.path.abspath(path)).lower()


def any_process_name_running(process_names: list[str]) -> bool:
    if not process_names:
        return False
    wanted = {n.strip().lower() for n in process_names if n.strip()}
    if not wanted:
        return False

    for proc in psutil.process_iter(attrs=["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in wanted:
            return True
    return False


def find_pid_by_process_name(process_name: str) -> int | None:
    """Return the PID of a running process whose name matches (case-insensitive), or None."""
    if not process_name:
        return None
    target = process_name.strip().lower()
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == target:
                return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def find_pid_by_exe(exe_path: str) -> int | None:
    """Return the PID of a running process whose exe path matches, or None."""
    if not exe_path:
        return None
    target = normalize_windows_path(exe_path)
    for proc in psutil.process_iter(attrs=["pid", "exe"]):
        try:
            exe = proc.info.get("exe")
            if exe and normalize_windows_path(exe) == target:
                return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return None


def any_process_exe_running(exe_path: str) -> bool:
    if not exe_path:
        return False
    target = normalize_windows_path(exe_path)

    for proc in psutil.process_iter(attrs=["exe"]):
        try:
            exe = proc.info.get("exe")
            if not exe:
                continue
            if normalize_windows_path(exe) == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return False
