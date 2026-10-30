import io

import pytest
import requests

from fundtracker import config
from fundtracker.edgar.client import EdgarClient, EdgarHTTPError, TokenBucket

URL = "https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/index.json"
AGENT = "Test Co tests@testco.com"


class FakeClock:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_response(status, body=b"{}", headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.raw = io.BytesIO(body)
    response.headers.update(headers or {})
    response.url = URL
    return response


class FakeServer:
    """Stands in for requests.Session.request, answering from a script of statuses."""

    def __init__(self, clock, statuses=(200,), body=b'{"directory": {}}'):
        self.clock = clock
        self.statuses = list(statuses)
        self.body = body
        self.calls = []

    def __call__(self, session, method, url, **kwargs):
        self.calls.append(
            {"time": self.clock(), "method": method, "url": url,
             "headers": dict(session.headers), "kwargs": kwargs}
        )
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return make_response(status, self.body)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def server(monkeypatch, clock):
    fake = FakeServer(clock)

    def request(session, method, url, **kwargs):  # a plain function, so it binds the session
        return fake(session, method, url, **kwargs)

    monkeypatch.setattr(requests.Session, "request", request)
    return fake


@pytest.fixture
def client(clock, tmp_path):
    return EdgarClient(user_agent=AGENT, rate=5, raw_dir=tmp_path,
                       clock=clock, sleep=clock.sleep)


def test_sets_sec_headers_and_timeout(client, server):
    client.get(URL)
    call = server.calls[0]
    assert call["method"] == "GET"
    assert call["headers"]["User-Agent"] == AGENT
    assert call["headers"]["Accept-Encoding"] == "gzip, deflate"
    assert call["kwargs"]["timeout"] == 30


def test_user_agent_defaults_to_config(monkeypatch, server):
    monkeypatch.setenv("FUNDTRACKER_USER_AGENT", "Config Co config@configco.com")
    EdgarClient(rate=5).get(URL)
    assert server.calls[0]["headers"]["User-Agent"] == "Config Co config@configco.com"


def test_rate_limit_spaces_requests(client, server, clock):
    n, rate = 10, 5
    start = clock()
    for _ in range(n):
        client.get(URL)
    assert clock() - start >= (n - 1) / rate - 1e-9
    gaps = [b["time"] - a["time"] for a, b in zip(server.calls, server.calls[1:])]
    assert min(gaps) >= 1 / rate - 1e-9


def test_token_bucket_does_not_wait_after_idle_time(clock):
    bucket = TokenBucket(5, clock=clock, sleep=clock.sleep)
    bucket.acquire()
    clock.now += 10
    bucket.acquire()
    assert clock.sleeps == []


def test_429_then_200_retries_once(client, server, clock):
    server.statuses = [429, 200]
    response = client.get(URL)
    assert response.status_code == 200
    assert len(server.calls) == 2
    assert 1.0 in clock.sleeps  # first backoff step


def test_retry_after_header_is_honoured(client, monkeypatch, clock):
    answers = [make_response(429, headers={"Retry-After": "7"}), make_response(200)]
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: answers.pop(0))
    client.get(URL)
    assert 7.0 in clock.sleeps


def test_five_500s_raise(client, server, clock):
    server.statuses = [500]
    with pytest.raises(EdgarHTTPError) as excinfo:
        client.get(URL)
    assert len(server.calls) == 5
    assert excinfo.value.last_status == 500
    backoffs = [s for s in clock.sleeps if s >= 1.0]
    assert backoffs == [1.0, 2.0, 4.0, 8.0]


def test_403_is_retried(client, server):
    server.statuses = [403, 200]
    assert client.get(URL).status_code == 200
    assert len(server.calls) == 2


def test_connection_error_is_retried(client, monkeypatch):
    answers = [requests.ConnectionError("reset"), make_response(200)]

    def flaky(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests.Session, "request", flaky)
    assert client.get(URL).status_code == 200


def test_404_is_returned_without_retry(client, server):
    server.statuses = [404]
    assert client.get(URL).status_code == 404
    assert len(server.calls) == 1


def test_get_cached_requests_once(client, server):
    first = client.get_cached(URL)
    second = client.get_cached(URL)
    assert first == second == server.body
    assert len(server.calls) == 1
    path = client.cache_path(URL)
    assert path.read_bytes() == server.body
    assert path.name.endswith("_000215881726000001_index.json")


def test_get_bypasses_cache(client, server):
    client.get_cached(URL)
    client.get(URL)
    assert len(server.calls) == 2


def test_get_cached_does_not_store_errors(client, server):
    server.statuses = [404]
    with pytest.raises(requests.HTTPError):
        client.get_cached(URL)
    assert not client.is_cached(URL)


def test_cache_keys_differ_by_url(client):
    other = URL.replace("000001", "000002")
    assert client.cache_path(URL) != client.cache_path(other)


@pytest.mark.parametrize("value", ["", "Your Name your.email@example.com", "justaname"])
def test_user_agent_rejects_missing_or_placeholder(monkeypatch, value):
    monkeypatch.setattr(config, "_ENV_FILE_VALUES", {})
    monkeypatch.setenv("FUNDTRACKER_USER_AGENT", value)
    with pytest.raises(config.ConfigError):
        config.user_agent()


def test_user_agent_read_from_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nFUNDTRACKER_USER_AGENT="File Co file@fileco.com"\n')
    monkeypatch.delenv("FUNDTRACKER_USER_AGENT", raising=False)
    monkeypatch.setattr(config, "_ENV_FILE_VALUES", config._read_env_file(env_file))
    assert config.user_agent() == "File Co file@fileco.com"


def test_stream_flag_is_passed_through(client, server):
    client.get(URL, stream=True)
    assert server.calls[0]["kwargs"]["stream"] is True
