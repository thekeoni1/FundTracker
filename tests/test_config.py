"""FUNDTRACKER_DATA_DIR moves the database and caches; exports stay in the project."""

import importlib

import pytest

from fundtracker import config


@pytest.fixture
def reload_config(monkeypatch):
    def reload(**env):
        for key in ("FUNDTRACKER_DATA_DIR", "FUNDTRACKER_EXPORT_DIR"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield reload
    monkeypatch.undo()
    importlib.reload(config)


def test_data_dir_override_moves_db_raw_and_datasets(reload_config, tmp_path):
    c = reload_config(FUNDTRACKER_DATA_DIR=str(tmp_path / "FundTrackerData"))
    assert c.DATA_DIR == tmp_path / "FundTrackerData"
    assert c.DB_PATH == tmp_path / "FundTrackerData" / "fundtracker.db"
    assert c.RAW_DIR == tmp_path / "FundTrackerData" / "raw"
    assert c.DATASETS_DIR == tmp_path / "FundTrackerData" / "datasets"
    assert c.EXPORT_DIR == c.PROJECT_ROOT / "data" / "exports"


def test_defaults_and_relative_paths_resolve_against_the_project(reload_config):
    # The environment wins over .env, so this holds whatever the real .env says.
    c = reload_config(FUNDTRACKER_DATA_DIR="data")
    assert c.DATA_DIR == c.PROJECT_ROOT / "data"
    assert c.DB_PATH == c.PROJECT_ROOT / "data" / "fundtracker.db"
    c = reload_config(FUNDTRACKER_EXPORT_DIR="out")
    assert c.EXPORT_DIR == c.PROJECT_ROOT / "out"
