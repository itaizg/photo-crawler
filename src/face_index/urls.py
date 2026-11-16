"""URL normalization and crawl scope (FR-002, FR-007, research R5)."""

import re
from collections.abc import Mapping
from typing import Final
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from face_index.config import TRACKING_PARAMS, TRACKING_PREFIXES, Site

_DEFAULT_PORTS: Final[Mapping[str, int]] = {"http": 80, "https": 443}
_SLASHES: Final[re.Pattern[str]] = re.compile(r"/{2,}")
# Characters a logged path or query key keeps as is. Everything else (control characters such as
# CR/LF, spaces, non-ASCII) is percent-encoded, so a crawled URL cannot forge a log line.
_LOG_SAFE_PATH: Final[str] = "/%:@!$&'()*+,;=-._~"
_LOG_SAFE_KEY: Final[str] = "%:@!$'()*+,;-._~/?[]"


def _netloc(host: str, port: int | None, scheme: str) -> str:
    host = f"[{host}]" if ":" in host else host
    if port is None or _DEFAULT_PORTS.get(scheme) == port:
        return host
    return f"{host}:{port}"


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def normalize_page_url(url: str) -> str:
    """Canonical page URL: https, lowercase host, no fragment, no tracking params, sorted query."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    port = parts.port
    if port == _DEFAULT_PORTS.get(scheme):
        port = None  # an explicit default port must not survive the switch to https
    if scheme == "http":
        scheme = "https"
    host = (parts.hostname or "").lower()
    path = _SLASHES.sub("/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query_items = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    ]
    query = urlencode(sorted(query_items))
    return urlunsplit((scheme, _netloc(host, port, scheme), path, query, ""))


def normalize_image_url(url: str) -> str:
    """Canonical image URL: lowercase host, no fragment. The query is kept (it names renditions)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    return urlunsplit((scheme, _netloc(host, parts.port, scheme), parts.path, parts.query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def in_scope(url: str, site: Site) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and (parts.hostname or "").lower() in site.page_hosts


def for_log(url: str) -> str:
    """A URL safe to log: no userinfo, and every query value masked (signed URLs carry secrets).

    Query keys are kept as sent (still percent-encoded, never decoded), so a log line still tells
    renditions apart by their parameter names; a bare query token with no ``=`` is masked whole.
    Path and keys are percent-encoded where needed, so the result is one printable line.
    """
    try:
        parts = urlsplit(url)
        netloc = _netloc(parts.hostname or "", parts.port, parts.scheme) if parts.netloc else ""
    except ValueError:  # malformed (bad port, bad IPv6): log only what cannot leak a secret
        return "<malformed url>"
    query = "&".join(_masked_pair(pair) for pair in parts.query.split("&") if pair)
    path = quote(parts.path, safe=_LOG_SAFE_PATH)
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _masked_pair(pair: str) -> str:
    key, has_value, _ = pair.partition("=")
    return f"{quote(key, safe=_LOG_SAFE_KEY)}=…" if has_value else "…"
