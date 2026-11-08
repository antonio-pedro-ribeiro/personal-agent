"""HTTP fetching for adapters. Relative paths and file:// URLs read local files, so sources
can point at fixtures or exported calendars.

Web requests are polite: one request per second per host, and ``allowed()`` checks
robots.txt (cached per host) for this agent's user agent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import url2pathname
from urllib.robotparser import RobotFileParser

import httpx

from personal_agent import __version__

USER_AGENT = (
    f"personal-agent/{__version__} (+https://github.com/antonio-pedro-ribeiro/personal-agent)"
)
ROBOTS_AGENT = "personal-agent"
TIMEOUT_S = 20.0
MIN_INTERVAL_S = 1.0


@dataclass
class Response:
    url: str  # final URL after redirects (or the path for local files)
    content: bytes
    content_type: str = ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Http(Protocol):
    def get(self, url: str) -> Response: ...

    def get_bytes(self, url: str) -> bytes: ...

    def allowed(self, url: str) -> bool: ...


class HttpService:
    def __init__(self, base_dir: Path, timeout: float = TIMEOUT_S) -> None:
        self.base_dir = base_dir
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def get(self, url: str) -> Response:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            self._throttle(parsed.netloc)
            response = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return Response(
                url=str(response.url),
                content=response.content,
                content_type=response.headers.get("content-type", ""),
            )
        if parsed.scheme == "file":
            path = Path(url2pathname(unquote(parsed.path)))
        # A one-letter "scheme" is a Windows drive letter such as C:\.
        elif parsed.scheme and len(parsed.scheme) > 1:
            raise ValueError(f"unsupported URL scheme: {url}")
        else:
            path = Path(url)
            if not path.is_absolute():
                path = self.base_dir / path
        return Response(url=str(path), content=path.read_bytes(), content_type=_guess_type(path))

    def get_bytes(self, url: str) -> bytes:
        return self.get(url).content

    def resolve(self, url: str) -> str | None:
        """The target of a redirect link (one HEAD request, falling back to GET), read from the
        ``Location`` header without fetching the target; None if ``url`` doesn't redirect."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        headers = {"User-Agent": USER_AGENT}
        try:
            for method in ("HEAD", "GET"):
                self._throttle(parsed.netloc)
                r = httpx.request(method, url, timeout=self.timeout, headers=headers)
                if r.is_redirect and (location := r.headers.get("location")):
                    return urljoin(url, str(location))
                if r.status_code not in (405, 501):
                    return None
        except httpx.HTTPError:
            return None
        return None

    def allowed(self, url: str) -> bool:
        """robots.txt check; unreachable or missing robots.txt means allowed."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True
        if parsed.netloc not in self._robots:
            robots: RobotFileParser | None = None
            try:
                self._throttle(parsed.netloc)
                r = httpx.get(
                    urljoin(url, "/robots.txt"),
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                )
                if r.status_code == 200:
                    robots = RobotFileParser()
                    robots.parse(r.text.splitlines())
            except httpx.HTTPError:
                robots = None
            self._robots[parsed.netloc] = robots
        robots = self._robots[parsed.netloc]
        return robots is None or robots.can_fetch(ROBOTS_AGENT, url)

    def _throttle(self, host: str) -> None:
        wait = self._last.get(host, 0.0) + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()


def _guess_type(path: Path) -> str:
    return {
        ".ics": "text/calendar",
        ".xml": "application/xml",
        ".rss": "application/rss+xml",
        ".atom": "application/atom+xml",
        ".html": "text/html",
        ".htm": "text/html",
    }.get(path.suffix.lower(), "")
