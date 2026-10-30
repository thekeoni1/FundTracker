"""fundtracker schedule, with subprocess.run patched: no task is created."""

import subprocess
from pathlib import Path

import pytest

from fundtracker import cli, schedule

PYTHON = r"C:\Projects\FundTracker\venv\Scripts\python.exe"
PROJECT = Path(r"C:\Projects\FundTracker")
LOG = Path(r"C:\Data\refresh.log")


class Completed:
    def __init__(self, returncode=0, stdout="SUCCESS: done.", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(schedule.sys, "platform", "win32")
    monkeypatch.setattr(schedule.sys, "executable", PYTHON)
    monkeypatch.setattr(schedule.config, "PROJECT_ROOT", PROJECT)
    monkeypatch.setattr(schedule.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: seen.append(args) or Completed())
    return seen


def test_task_command_changes_to_the_project_and_appends_to_the_log():
    assert schedule.task_command(PYTHON, PROJECT, LOG) == (
        r'cmd /c cd /d "C:\Projects\FundTracker" && '
        r'"C:\Projects\FundTracker\venv\Scripts\python.exe" -m fundtracker refresh '
        r'>> "C:\Data\refresh.log" 2>&1')


def test_install_registers_a_weekday_0630_task(calls, tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "s.db"), "schedule", "install"]) == 0
    assert calls == [["schtasks", "/Create", "/TN", "FundTracker refresh",
                      "/TR", schedule.task_command(PYTHON, PROJECT, tmp_path / "refresh.log"),
                      "/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI", "/ST", "06:30", "/F"]]
    assert "runs weekdays at 06:30" in capsys.readouterr().out


def test_show_and_remove(calls, tmp_path):
    assert cli.main(["--db", str(tmp_path / "s.db"), "schedule", "show"]) == 0
    assert cli.main(["--db", str(tmp_path / "s.db"), "schedule", "remove"]) == 0
    assert calls == [["schtasks", "/Query", "/TN", "FundTracker refresh", "/V", "/FO", "LIST"],
                     ["schtasks", "/Delete", "/TN", "FundTracker refresh", "/F"]]


def test_show_without_a_task_says_so(monkeypatch, calls, capsys):
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: Completed(1, "", "ERROR: not found."))
    assert schedule.run("show") == 1
    assert "no task named 'FundTracker refresh'" in capsys.readouterr().out


def test_other_systems_get_the_cron_line(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("ran a command"))
    assert schedule.run("install", platform="linux") == 0
    out = capsys.readouterr().out
    assert "crontab -e" in out and "30 6 * * 1-5 cd " in out and "-m fundtracker refresh" in out
