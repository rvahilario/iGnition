"""Tests for process termination helpers."""
import subprocess
import sys
import time

import psutil
import pytest

from ignition.core.process_killer import (
    graceful_terminate_process,
    kill_by_exe_path,
    terminate_process,
    terminate_process_tree,
)


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _is_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.1)
    return False


class TestTerminateProcess:
    def test_kills_running_process(self):
        proc = _spawn_sleeper()
        try:
            terminate_process(proc.pid)
            assert _wait_dead(proc.pid), "process should be dead"
        finally:
            proc.kill()
            proc.wait()

    def test_noop_on_dead_pid(self):
        proc = _spawn_sleeper()
        pid = proc.pid
        proc.kill()
        proc.wait()
        terminate_process(pid)  # must not raise


class TestTerminateProcessTree:
    def test_kills_parent_and_child(self):
        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "time.sleep(60)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)  # let child spawn
        try:
            children = psutil.Process(parent.pid).children(recursive=True)
            terminate_process_tree(parent.pid)
            assert _wait_dead(parent.pid), "parent should be dead"
            for child in children:
                assert _wait_dead(child.pid), f"child {child.pid} should be dead"
        finally:
            parent.kill()
            parent.wait()

    def test_noop_on_dead_pid(self):
        proc = _spawn_sleeper()
        pid = proc.pid
        proc.kill()
        proc.wait()
        terminate_process_tree(pid)  # must not raise


class TestGracefulTerminateProcess:
    def test_kills_with_zero_grace(self):
        proc = _spawn_sleeper()
        try:
            graceful_terminate_process(proc.pid, grace_seconds=0)
            assert _wait_dead(proc.pid)
        finally:
            proc.kill()
            proc.wait()

    def test_kills_with_positive_grace(self):
        proc = _spawn_sleeper()
        try:
            graceful_terminate_process(proc.pid, grace_seconds=2)
            assert _wait_dead(proc.pid, timeout=6)
        finally:
            proc.kill()
            proc.wait()


class TestKillByExePath:
    def test_returns_false_for_nonexistent_exe(self):
        found = kill_by_exe_path(r"C:\nonexistent\definitely_not_running.exe", 0)
        assert found is False

    def test_returns_false_for_empty_path(self):
        assert kill_by_exe_path("", 0) is False

    def test_finds_matching_process_and_calls_terminate(self, monkeypatch):
        """Verifies kill_by_exe_path finds the right PID and calls graceful_terminate."""
        import ignition.core.process_killer as pk

        fake_proc = type("P", (), {"info": {"pid": 42, "exe": sys.executable}})()

        killed_pids = []
        monkeypatch.setattr(
            "psutil.process_iter",
            lambda attrs=None: iter([fake_proc]),
        )
        monkeypatch.setattr(pk, "graceful_terminate_process", lambda pid, grace: killed_pids.append(pid))

        from ignition.core.process_utils import normalize_windows_path
        found = kill_by_exe_path(sys.executable, grace_seconds=0)

        assert found is True
        assert 42 in killed_pids

    def test_no_match_returns_false(self, monkeypatch):
        import ignition.core.process_killer as pk

        fake_proc = type("P", (), {"info": {"pid": 99, "exe": r"C:\other\app.exe"}})()
        monkeypatch.setattr("psutil.process_iter", lambda attrs=None: iter([fake_proc]))

        found = kill_by_exe_path(r"C:\target\app.exe", grace_seconds=0)
        assert found is False

    def test_kills_specific_pid_not_self(self):
        """spawn a new process, kill it by PID directly — verifies kill plumbing works."""
        proc = _spawn_sleeper()
        try:
            terminate_process(proc.pid)
            assert _wait_dead(proc.pid)
        finally:
            proc.kill()
            proc.wait()
