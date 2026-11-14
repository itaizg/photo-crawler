"""The safe HTTP fetcher (FR-003, FR-004, FR-007a, FR-010a, research R4).

Every request, and every redirect hop, is checked *before* it is sent: scheme and port, crawl
scope (pages only), robots.txt, address safety, and a pacer slot. The connection goes to the IP
that passed the address check, with the original name in the ``Host`` header and as the TLS
server name, so DNS cannot change between the check and the connect. Each host name gets its own
connection pool, so two names that share an IP never share a TLS connection, and no cookies are
kept between requests.
"""

import codecs
import ipaddress
import logging
import socket
from collections.abc import Callable
from contextlib import closing
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from face_index import config
from face_index.errors import Disallowed, FetchRefused, ItemError
from face_index.models import ImageResponse, PageResponse, RequestKind
from face_index.ports import Pacer, Resolver

log = logging.getLogger(__name__)

_REDIRECTS = {301, 302, 303, 307, 308}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class SystemResolver:
    def resolve(self, host: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ItemError(f"dns error: {exc.strerror}") from exc
        return sorted({str(info[4][0]) for info in infos})


class _RobotsRule:
    """robots.txt outcome for one host (RFC 9309)."""

    def __init__(self, parser: RobotFileParser | None, allow_all: bool) -> None:
        self._parser = parser
        self._allow_all = allow_all

    def allows(self, url: str) -> bool:
        if self._parser is None:
            return self._allow_all
        return self._parser.can_fetch(config.ROBOTS_TOKEN, url)


class HttpFetcher:
    def __init__(
        self,
        pacer: Pacer,
        resolver: Resolver | None = None,
        transport: httpx.BaseTransport | None = None,
        scope: Callable[[str], bool] | None = None,
        max_download_bytes: int = config.MAX_DOWNLOAD_BYTES,
    ) -> None:
        self._pacer = pacer
        self._resolver = resolver or SystemResolver()
        self._scope = scope
        self._max_download = max_download_bytes
        self._transport = transport
        self._robots: dict[tuple[str, str], _RobotsRule] = {}
        # One client per (scheme, host name): connections are pooled by the pinned IP, so a
        # shared pool would let a second name on the same IP reuse the first name's TLS session.
        self._clients: dict[tuple[str, str], httpx.Client] = {}

    # -- public API -------------------------------------------------------------------------

    def get_page(self, url: str) -> PageResponse:
        final_url, response = self._open(url, "page", config.PAGE_ACCEPT, check_scope=True)
        with closing(response):
            if response.status_code != 200:
                raise ItemError(f"http {response.status_code}", final_url)
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                return PageResponse(url=final_url, content_type=content_type, text=None)
            body = self._read(response, config.MAX_PAGE_BYTES, final_url)
            return PageResponse(
                url=final_url,
                content_type=content_type,
                text=_decode(body, response.charset_encoding),
            )

    def get_image(self, url: str) -> ImageResponse:
        final_url, response = self._open(url, "image", config.IMAGE_ACCEPT, check_scope=False)
        with closing(response):
            if response.status_code != 200:
                raise ItemError(f"http {response.status_code}", final_url)
            body = self._read(response, self._max_download, final_url)
            return ImageResponse(
                url=final_url, content_type=response.headers.get("content-type"), body=body
            )

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    # -- internals --------------------------------------------------------------------------

    def _open(
        self, url: str, kind: RequestKind, accept: str, *, check_scope: bool
    ) -> tuple[str, httpx.Response]:
        current = url
        for _ in range(config.MAX_REDIRECTS + 1):
            # Pages are only fetched inside a site's scope; without one, no page is in scope.
            if check_scope and (self._scope is None or not self._scope(current)):
                raise Disallowed("out_of_scope", current)
            host = self._checked_host(current)
            self._vetted_ip(host, current)  # refuse non-public targets before any robots lookup
            if not self._robots_rule(current).allows(current):
                raise Disallowed("robots_disallowed", current)
            response = self._send(current, kind, accept)
            location = response.headers.get("location")
            if response.status_code in _REDIRECTS and location:
                response.close()
                current = urljoin(current, location)
                continue
            return current, response
        raise ItemError("too many redirects", url)

    def _checked_host(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in _DEFAULT_PORTS:
            raise FetchRefused("unsupported scheme", url)
        try:
            port = parts.port
        except ValueError as exc:
            raise FetchRefused("bad port", url) from exc
        if port not in (None, _DEFAULT_PORTS[parts.scheme]):
            raise FetchRefused("non-standard port", url)
        host = (parts.hostname or "").lower()
        if not host:
            raise FetchRefused("missing host", url)
        return host

    def _vetted_ip(self, host: str, url: str) -> str:
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            addresses = self._resolver.resolve(host)
        if not addresses:
            raise ItemError("dns returned no address", url)
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise FetchRefused("non-public address", url)
        return addresses[0]

    def _send(self, url: str, kind: RequestKind, accept: str) -> httpx.Response:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        ip = self._vetted_ip(host, url)
        self._pacer.wait(host, kind)
        pinned_url = httpx.URL(url).copy_with(host=ip)
        extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
        client = self._client_for(parts.scheme, host)
        request = client.build_request(
            "GET",
            pinned_url,
            headers={"Host": f"[{host}]" if ":" in host else host, "Accept": accept},
            extensions=extensions,
        )
        try:
            response = client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise ItemError("timeout", url) from exc
        except httpx.TransportError as exc:
            raise ItemError(f"network error: {type(exc).__name__}", url) from exc
        client.cookies.clear()  # the crawler never sends cookies back
        return response

    def _client_for(self, scheme: str, host: str) -> httpx.Client:
        client = self._clients.get((scheme, host))
        if client is None:
            client = httpx.Client(
                transport=self._transport,
                follow_redirects=False,
                timeout=httpx.Timeout(config.READ_TIMEOUT, connect=config.CONNECT_TIMEOUT),
                headers={"User-Agent": config.USER_AGENT},
            )
            self._clients[(scheme, host)] = client
        return client

    def _read(self, response: httpx.Response, limit: int, url: str) -> bytes:
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise FetchRefused("download too large", url)
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise FetchRefused("download too large", url)
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ItemError("timeout", url) from exc
        except httpx.TransportError as exc:
            raise ItemError(f"network error: {type(exc).__name__}", url) from exc
        return b"".join(chunks)

    def _robots_rule(self, url: str) -> _RobotsRule:
        parts = urlsplit(url)
        key = (parts.scheme, (parts.hostname or "").lower())  # robots.txt is per scheme and host
        rule = self._robots.get(key)
        if rule is None:
            rule = self._fetch_robots(url)
            self._robots[key] = rule
        return rule

    def _fetch_robots(self, url: str) -> _RobotsRule:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        current = robots_url
        try:
            for _ in range(config.MAX_REDIRECTS + 1):
                self._checked_host(current)
                response = self._send(current, "page", "text/plain")
                with closing(response):
                    location = response.headers.get("location")
                    if response.status_code in _REDIRECTS and location:
                        current = urljoin(current, location)
                        continue
                    status = response.status_code
                    if 200 <= status < 300:
                        text = self._read(response, config.MAX_ROBOTS_BYTES, current).decode(
                            "utf-8", errors="replace"
                        )
                        parser = RobotFileParser()
                        parser.parse(text.splitlines())
                        return _RobotsRule(parser, allow_all=True)
                    if 400 <= status < 500:
                        return _RobotsRule(None, allow_all=True)
                    log.warning("robots unavailable host=%s status=%s", parts.hostname, status)
                    return _RobotsRule(None, allow_all=False)
        except ItemError as exc:
            log.warning("robots unavailable host=%s reason=%s", parts.hostname, exc.reason)
            return _RobotsRule(None, allow_all=False)
        log.warning("robots unavailable host=%s reason=too many redirects", parts.hostname)
        return _RobotsRule(None, allow_all=False)


def _decode(body: bytes, charset: str | None) -> str:
    """Decode a page with its declared charset, falling back to UTF-8 for unknown labels."""
    encoding = "utf-8"
    if charset:
        try:
            encoding = codecs.lookup(charset).name
        except LookupError:
            log.info("unknown charset %r, decoding as utf-8", charset)
    return body.decode(encoding, errors="replace")
