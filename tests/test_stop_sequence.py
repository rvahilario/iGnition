"""Integration tests for the iRacing stop sequence (app termination)."""
import subprocess
import sys
import time
import threading
from unittest.mock import MagicMock, patch

import psutil
import pytest

import ignition.core.ignition_controller as _ctrl_mod
from ignition.core.ignition_controller import IgnitionController, RunningApp
from ignition.core.models import AppConfig, ManagedApp, Profile
from ignition.core.config_store import ConfigStore


def _make_app(**kwargs) -> ManagedApp:
    defaults = dict(
        name="TestApp",
        executable_path=sys.executable,
        kill_on_iracing_exit=True,
        kill_process_tree=False,
        shutdown_grace_seconds=0.0,
        restart_on_crash=False,
    )
    defaults.update(kwargs)
    return ManagedApp.create(**{k: v for k, v in defaults.items() if k in ("name", "executable_path")})


def _make_controller(tmp_path) -> IgnitionController:
    store = MagicMock(spec=ConfigStore)
    profile = Profile.create_default()
    cfg = AppConfig.default()
    cfg.profiles = [profile]
    cfg.active_profile_id = profile.profile_id
    store.config = cfg
    store.paths = MagicMock()
    store.paths.session_history_file = tmp_path / "history.json"
    return IgnitionController(store)


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


class TestStopAllManaged:
    def test_kills_tracked_process(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        proc = _spawn_sleeper()
        app = _make_app()

        try:
            with ctrl._lock:
                ctrl._iracing_running = True
                ctrl._running[app.app_id] = RunningApp(
                    app=app, pid=proc.pid, started_at_monotonic=time.monotonic()
                )

            ctrl._stop_all_managed(reason="iracing-exit")

            assert _wait_dead(proc.pid), "process should have been killed"
        finally:
            proc.kill()
            proc.wait()

    def test_skip_app_with_kill_on_exit_false(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        proc = _spawn_sleeper()
        app = _make_app(name="KeepAlive")
        app = ManagedApp(
            app_id=app.app_id, name=app.name, executable_path=app.executable_path,
            kill_on_iracing_exit=False,
            kill_process_tree=False, shutdown_grace_seconds=0.0, restart_on_crash=False,
        )

        try:
            with ctrl._lock:
                ctrl._running[app.app_id] = RunningApp(
                    app=app, pid=proc.pid, started_at_monotonic=time.monotonic()
                )

            ctrl._stop_all_managed(reason="iracing-exit")

            assert _is_alive(proc.pid), "process should still be running"
        finally:
            proc.kill()
            proc.wait()

    def test_stale_pid_falls_back_to_exe_path(self, tmp_path, monkeypatch):
        """If stored PID is dead, _terminate calls kill_by_exe_path as fallback."""
        ctrl = _make_controller(tmp_path)
        app = _make_app()

        killed_by_exe: list[str] = []
        monkeypatch.setattr(
            _ctrl_mod, "kill_by_exe_path",
            lambda exe, grace: killed_by_exe.append(exe) or True,
        )

        dead_pid = 999998
        with ctrl._lock:
            ctrl._iracing_running = True
            ctrl._running[app.app_id] = RunningApp(
                app=app, pid=dead_pid, started_at_monotonic=time.monotonic()
            )

        ctrl._stop_all_managed(reason="iracing-exit")

        assert any(app.executable_path in p for p in killed_by_exe), (
            "kill_by_exe_path should have been called with the app's exe path"
        )

    def test_dead_tracked_apps_killed_by_exe_path(self, tmp_path, monkeypatch):
        """Apps removed by watchdog are still killed via exe path on iRacing exit."""
        ctrl = _make_controller(tmp_path)
        app = _make_app()

        killed_by_exe: list[str] = []
        monkeypatch.setattr(
            _ctrl_mod, "kill_by_exe_path",
            lambda exe, grace: killed_by_exe.append(exe) or True,
        )

        with ctrl._lock:
            ctrl._iracing_running = True
            ctrl._dead_tracked_apps.append(
                RunningApp(app=app, pid=999999, started_at_monotonic=time.monotonic())
            )

        ctrl._stop_all_managed(reason="iracing-exit")

        assert any(app.executable_path in p for p in killed_by_exe), (
            "kill_by_exe_path should have been called for dead_tracked_apps"
        )

    def test_running_cleared_after_stop(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        proc = _spawn_sleeper()
        app = _make_app()

        try:
            with ctrl._lock:
                ctrl._iracing_running = True
                ctrl._running[app.app_id] = RunningApp(
                    app=app, pid=proc.pid, started_at_monotonic=time.monotonic()
                )

            ctrl._stop_all_managed(reason="iracing-exit")

            with ctrl._lock:
                assert len(ctrl._running) == 0
                assert len(ctrl._dead_tracked_apps) == 0
        finally:
            proc.kill()
            proc.wait()


class TestStartAppRaceCondition:
    def test_start_app_aborts_when_iracing_stopped(self, tmp_path):
        """_start_app must not launch when _iracing_running is False."""
        ctrl = _make_controller(tmp_path)
        app = _make_app()

        with ctrl._lock:
            ctrl._iracing_running = False

        launched_pids = []
        original_launch = __import__(
            "ignition.core.app_launcher", fromlist=["launch_executable"]
        ).launch_executable

        with patch("ignition.core.ignition_controller.launch_executable") as mock_launch:
            mock_launch.return_value = MagicMock(pid=12345)
            ctrl._start_app(app)
            mock_launch.assert_not_called()

    def test_start_app_proceeds_when_iracing_running(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        app = _make_app()

        with ctrl._lock:
            ctrl._iracing_running = True

        with patch("ignition.core.ignition_controller.launch_executable") as mock_launch:
            mock_launch.return_value = MagicMock(pid=12345)
            ctrl._start_app(app)
            mock_launch.assert_called_once()


class TestTerminateHelper:
    def test_returns_true_for_live_pid(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        proc = _spawn_sleeper()
        app = _make_app()
        running = RunningApp(app=app, pid=proc.pid, started_at_monotonic=time.monotonic())

        try:
            result = ctrl._terminate(running)
            assert result is True
        finally:
            proc.kill()
            proc.wait()

    def test_returns_false_for_dead_pid_no_matching_exe(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        app = MagicMock()
        app.shutdown_grace_seconds = 0.0
        app.kill_process_tree = False
        app.executable_path = r"C:\nonexistent\nothing.exe"
        running = RunningApp(app=app, pid=999999, started_at_monotonic=time.monotonic())

        result = ctrl._terminate(running)
        assert result is False
