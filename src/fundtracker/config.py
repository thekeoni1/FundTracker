"""Settings read from the environment and a .env file in the project root.

The process environment wins over .env, so a one-off override needs no file
edit. Paths are resolved against the project root, not the working directory,
so the CLI behaves the same wherever it is run from.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# The value shipped in .env.example. Anything still containing it is unset.
PLACEHOLDER_USER_AGENT = "Your Name your.email@example.com"


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def _read_env_file(path):
    """Parse KEY=VALUE lines. Blank lines and # comments are skipped."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


_ENV_FILE_VALUES = _read_env_file(ENV_FILE)


def _setting(name, default=None):
    return os.environ.get(name, _ENV_FILE_VALUES.get(name, default))


def _resolve(path_text):
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


# The database and the caches live under DATA_DIR. FUNDTRACKER_DATA_DIR moves
# them, for example out of a synced folder such as OneDrive.
DATA_DIR = _resolve(_setting("FUNDTRACKER_DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
DATASETS_DIR = DATA_DIR / "datasets"
DB_PATH = DATA_DIR / "fundtracker.db"
# Workbooks and CSVs are for the user to open, so they stay in the project
# whatever DATA_DIR is, unless FUNDTRACKER_EXPORT_DIR says otherwise.
EXPORT_DIR = _resolve(_setting("FUNDTRACKER_EXPORT_DIR", "data/exports"))
RATE_LIMIT_PER_SEC = float(_setting("FUNDTRACKER_RATE_LIMIT_PER_SEC", "5"))


def _outreach_source(text):
    """An https URL as is, a path resolved like the others, None when unset."""
    text = (text or "").strip()
    if not text:
        return None
    return text if text.lower().startswith("https://") else _resolve(text)


# The outreach sheet's MASTER tab as CSV, read back by outreach.pull: a downloaded
# file (recommended) or the tab published to the web. Unset means no read-back.
OUTREACH_CSV = _outreach_source(_setting("FUNDTRACKER_OUTREACH_CSV"))


def user_agent():
    """Return the SEC User-Agent, or raise ConfigError explaining how to set it.

    SEC requires "Company Name email" and answers 403 to anything generic, so
    a missing or placeholder value stops here rather than at the first request.
    Read on call, not at import, so modules that never touch EDGAR still load.
    """
    value = (_setting("FUNDTRACKER_USER_AGENT") or "").strip()
    how_to_fix = (
        f"Copy .env.example to {ENV_FILE} and set FUNDTRACKER_USER_AGENT to your "
        'name or company and a contact email, e.g. "Jane Smith jane@firm.com". '
        "SEC rejects requests without a real one."
    )
    if not value:
        raise ConfigError("FUNDTRACKER_USER_AGENT is not set. " + how_to_fix)
    if PLACEHOLDER_USER_AGENT in value or "example.com" in value:
        raise ConfigError("FUNDTRACKER_USER_AGENT is still the placeholder. " + how_to_fix)
    if "@" not in value or " " not in value:
        raise ConfigError(
            f"FUNDTRACKER_USER_AGENT {value!r} does not look like a name and an email. "
            + how_to_fix
        )
    return value
