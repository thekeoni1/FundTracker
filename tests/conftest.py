import pytest
import requests


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that reaches requests without patching it first."""

    def refuse(self, method, url, *args, **kwargs):
        raise AssertionError(f"test tried to reach the network: {method} {url}")

    monkeypatch.setattr(requests.Session, "request", refuse)
