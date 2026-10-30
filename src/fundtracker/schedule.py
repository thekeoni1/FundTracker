"""fundtracker schedule install|show|remove: an optional weekday refresh through Task Scheduler.

The task runs fundtracker refresh at 06:30 Monday to Friday under the
current user, with the project folder as the working directory and its
output appended to refresh.log in DATA_DIR. schtasks cannot set a working
directory, so the task's command is a cmd /c line that changes to it
first. On other systems the command prints the cron line to add instead.
"""

import subprocess
import sys
from pathlib import Path

from fundtracker import config

TASK_NAME = "FundTracker refresh"
TIME = "06:30"
DAYS = "MON,TUE,WED,THU,FRI"


def log_path():
    return Path(config.DATA_DIR) / "refresh.log"


def task_command(python=None, project=None, log=None):
    """The command the task runs, as one string for schtasks /TR."""
    python, project, log = python or sys.executable, project or config.PROJECT_ROOT, log or log_path()
    return f'cmd /c cd /d "{project}" && "{python}" -m fundtracker refresh >> "{log}" 2>&1'


def install_args(command):
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command,
            "/SC", "WEEKLY", "/D", DAYS, "/ST", TIME, "/F"]


def show_args():
    return ["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"]


def remove_args():
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def cron_line(python=None, project=None, log=None):
    python, project, log = python or sys.executable, project or config.PROJECT_ROOT, log or log_path()
    hour, minute = TIME.split(":")
    return f'{int(minute)} {int(hour)} * * 1-5 cd "{project}" && "{python}" -m fundtracker refresh >> "{log}" 2>&1'


def run(action, platform=None):
    """Run install, show or remove and return an exit code, printing what schtasks says."""
    if (platform or sys.platform) != "win32":
        print("Task Scheduler is Windows only. To refresh on weekdays at 06:30, add this line "
              "with crontab -e:")
        print(cron_line())
        return 0
    args = {"install": lambda: install_args(task_command()), "show": show_args,
            "remove": remove_args}[action]()
    if action == "install":
        log_path().parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(args, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode and action != "install":
        print(f"no task named {TASK_NAME!r} ({output})")
        return 1
    print(output)
    if action == "install" and result.returncode == 0:
        print(f"runs weekdays at {TIME}: {task_command()}")
    return result.returncode
