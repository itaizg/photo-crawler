"""URL normalization and crawl scope (FR-002, FR-007, research R5)."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from face_index.config import TRACKING_PARAMS, TRACKING_PREFIXES, Site

_DEFAULT_PORTS = {"http": 80, "https": 443}
_SLASHES = re.compile(r"/{2,}")


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
    return urlunsplit((scheme, _netloc(host, parts.port, scheme), path, query, ""))


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
