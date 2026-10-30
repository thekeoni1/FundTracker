"""Rate-limited, retrying, disk-cached HTTP client for SEC EDGAR.

SEC allows 10 requests per second per client and answers 403 to requests
without a real User-Agent (docs/edgar-formd-reference.md section 1). This
client spaces requests evenly at the configured rate (5 per second by
default), retries throttling and server errors with exponential backoff, and
can keep response bodies on disk so immutable archive files are fetched once.

Run as a module to fetch one URL:

    python -m fundtracker.edgar.client <url>
"""

import argparse
import hashlib
import logging
import re
import sys
import time
from urllib.parse import urlsplit

import requests

from fundtracker import config

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


class EdgarHTTPError(RuntimeError):
    """A request still failed after every retry."""

    def __init__(self, url, tries, last_status=None, last_error=None):
        self.url = url
        self.tries = tries
        self.last_status = last_status
        self.last_error = last_error
        reason = f"HTTP {last_status}" if last_status is not None else repr(last_error)
        super().__init__(f"GET {url} failed after {tries} tries: {reason}")


class TokenBucket:
    """Allows `rate` acquisitions per second, with at most `capacity` in a burst.

    The default capacity of 1 means no bursts at all: consecutive requests are
    at least 1/rate seconds apart. clock and sleep are injectable for tests.
    """

    def __init__(self, rate, capacity=1, clock=time.monotonic, sleep=time.sleep):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._updated = clock()

    def acquire(self):
        while True:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            self._sleep((1 - self._tokens) / self.rate)


class EdgarClient:
    """A requests.Session with SEC headers, rate limiting, retries and a disk cache.

    get(url) always hits the network. get_cached(url) serves the body from
    RAW_DIR when present and otherwise fetches and stores it; use it only for
    content that never changes, such as files under /Archives/edgar/data/.
    """

    def __init__(
        self,
        user_agent=None,
        rate=None,
        raw_dir=None,
        timeout=30,
        max_tries=5,
        backoff_base=1.0,
        session=None,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.raw_dir = raw_dir if raw_dir is not None else config.RAW_DIR
        self.timeout = timeout
        self.max_tries = max_tries
        self.backoff_base = backoff_base
        self._sleep = sleep
        self.limiter = TokenBucket(
            rate if rate is not None else config.RATE_LIMIT_PER_SEC, clock=clock, sleep=sleep
        )
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent or config.user_agent(),
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def get(self, url, stream=False):
        """GET url, retrying 403, 429, 5xx and connection errors.

        Returns the requests.Response for any other status, including 404, so
        callers can treat a missing daily index as data rather than failure.
        Raises EdgarHTTPError once max_tries attempts have all failed. With
        stream=True the body is not read yet; the caller iterates and closes it.
        """
        last_status = last_error = None
        for attempt in range(1, self.max_tries + 1):
            self.limiter.acquire()
            try:
                response = self.session.get(url, timeout=self.timeout, stream=stream)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_status, last_error = None, exc
                retry_after = None
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                last_status, last_error = response.status_code, None
                retry_after = _retry_after_seconds(response)
                response.close()
            if attempt == self.max_tries:
                break
            delay = self.backoff_base * 2 ** (attempt - 1)
            if retry_after is not None:
                delay = max(delay, retry_after)
            log.warning(
                "GET %s failed (%s), try %d of %d, retrying in %.1fs",
                url,
                last_status or last_error,
                attempt,
                self.max_tries,
                delay,
            )
            self._sleep(delay)
        raise EdgarHTTPError(url, self.max_tries, last_status, last_error)

    def cache_path(self, url):
        """Where get_cached keeps the body of url."""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.raw_dir / f"{digest}_{_readable_suffix(url)}"

    def is_cached(self, url):
        return self.cache_path(url).is_file()

    def get_cached(self, url):
        """Return the body of url as bytes, from disk when it was fetched before.

        Only 200 responses are stored. Anything else raises requests.HTTPError
        and leaves the cache untouched.
        """
        path = self.cache_path(url)
        if path.is_file():
            return path.read_bytes()
        response = self.get(url)
        response.raise_for_status()
        body = response.content
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return body


def _retry_after_seconds(response):
    value = response.headers.get("Retry-After", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _readable_suffix(url, max_len=80):
    """The last two path segments of url, made safe for a file name.

    For .../000215881726000001/index.json this gives
    000215881726000001_index.json, so a cached file can be found by eye.
    """
    parts = [p for p in urlsplit(url).path.split("/") if p]
    suffix = "_".join(parts[-2:]) or "root"
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", suffix)
    return suffix[-max_len:]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m fundtracker.edgar.client",
        description="Fetch one EDGAR URL through the cache and print the body.",
    )
    parser.add_argument("url")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        client = EdgarClient()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    from_cache = client.is_cached(args.url)
    try:
        body = client.get_cached(args.url)
    except (requests.HTTPError, EdgarHTTPError) as exc:
        print(exc, file=sys.stderr)
        return 1

    sys.stdout.buffer.write(body)
    if not body.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    sys.stdout.flush()
    source = "served from cache" if from_cache else "fetched from EDGAR and cached"
    print(f"{source}: {client.cache_path(args.url)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
