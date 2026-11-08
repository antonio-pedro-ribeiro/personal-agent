"""HTTP fetching for adapters. Relative paths and file:// URLs read local files, so sources
can point at fixtures or exported calendars."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx

from personal_agent import __version__

USER_AGENT = (
    f"personal-agent/{__version__} (+https://github.com/antonio-pedro-ribeiro/personal-agent)"
)
TIMEOUT_S = 20.0


class Http(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


class HttpService:
    def __init__(self, base_dir: Path, timeout: float = TIMEOUT_S) -> None:
        self.base_dir = base_dir
        self.timeout = timeout

    def get_bytes(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            response = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return response.content
        if parsed.scheme == "file":
            return Path(url2pathname(unquote(parsed.path))).read_bytes()
        # Plain path (absolute, or relative to the config directory). A one-letter "scheme"
        # is a Windows drive letter such as C:\.
        if parsed.scheme and len(parsed.scheme) > 1:
            raise ValueError(f"unsupported URL scheme: {url}")
        path = Path(url)
        if not path.is_absolute():
            path = self.base_dir / path
        return path.read_bytes()
