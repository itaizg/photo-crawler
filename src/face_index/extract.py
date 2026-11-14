"""HTML → links, image references and the lead image (FR-006, FR-006a, research R6)."""

import logging
import re
from typing import TypeIs
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from face_index.models import PageExtract
from face_index.urls import for_log

log = logging.getLogger(__name__)

_LAZY_SRC = ("data-src", "data-original", "data-lazy-src")
_SRCSET = ("srcset", "data-srcset")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_CSS_BACKGROUND = re.compile(r"background(?:-image)?\s*:([^;]+)", re.IGNORECASE)
_DESCRIPTOR = re.compile(r"^(\d+(?:\.\d+)?)([wx])$")


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if isinstance(value, list):
        value = " ".join(value)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _srcset_candidates(srcset: str) -> list[tuple[str, str]]:
    """Split a srcset into ``(url, descriptor)`` pairs, per the HTML spec's parsing rules.

    URLs may themselves contain commas (CDN transforms such as ``c_fill,f_webp``), so a comma
    only separates candidates when it follows a descriptor or ends a URL.
    """
    candidates: list[tuple[str, str]] = []
    position, length = 0, len(srcset)
    while position < length:
        while position < length and (srcset[position].isspace() or srcset[position] == ","):
            position += 1
        start = position
        while position < length and not srcset[position].isspace():
            position += 1
        url = srcset[start:position]
        if not url:
            break
        if url.endswith(","):
            candidates.append((url.rstrip(","), "1x"))
            continue
        start = position
        while position < length and srcset[position] != ",":
            position += 1
        descriptor = srcset[start:position].strip() or "1x"
        candidates.append((url, descriptor.split()[0]))
    return candidates


def best_srcset_candidate(srcset: str) -> str | None:
    """Largest candidate by ``w`` descriptor, then by ``x``; plain URLs count as ``1x``."""
    best: tuple[int, float, str] | None = None
    for url, descriptor in _srcset_candidates(srcset):
        match = _DESCRIPTOR.match(descriptor)
        if not match:
            continue
        rank = (1 if match.group(2) == "w" else 0, float(match.group(1)), url)
        if best is None or rank[:2] > best[:2]:
            best = rank
    return best[2] if best else None


def _css_urls(style: str) -> list[str]:
    urls: list[str] = []
    for declaration in _CSS_BACKGROUND.findall(style):
        found = [m.group(2) for m in _CSS_URL.finditer(declaration)]
        if found:
            # In image-set(...) the last candidate is the largest by convention.
            urls.append(found[-1])
    return urls


def _usable(url: str | None) -> TypeIs[str]:
    return bool(url) and not url.lower().startswith(("data:", "javascript:", "about:"))


def _from_srcset(tag: Tag) -> str | None:
    for name in _SRCSET:
        srcset = _attr(tag, name)
        if srcset:
            return best_srcset_candidate(srcset)
    return None


def _from_img(img: Tag) -> str | None:
    for candidate in (_from_srcset(img), *(_attr(img, a) for a in _LAZY_SRC), _attr(img, "src")):
        if _usable(candidate):
            return candidate
    return None


def _image_refs(soup: BeautifulSoup) -> list[str]:
    from_img = (_from_img(img) for img in soup.find_all("img") if isinstance(img, Tag))
    from_sources = (_from_srcset(source) for source in soup.select("picture source"))
    from_styles = (
        url
        for tag in soup.find_all(style=True)
        if isinstance(tag, Tag)
        for url in _css_urls(_attr(tag, "style") or "")
    )
    return [ref for ref in (*from_img, *from_sources, *from_styles) if _usable(ref)]


def _lead_ref(soup: BeautifulSoup) -> str | None:
    for selector in (
        'meta[property="og:image"]',
        'meta[name="og:image"]',
        'meta[name="twitter:image"]',
    ):
        meta = soup.select_one(selector)
        if meta is not None:
            content = _attr(meta, "content")
            if _usable(content):
                return content
    return None


def _unique(urls: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(urls))


def extract_page(html: str, base_url: str) -> PageExtract:
    soup = BeautifulSoup(html, "lxml")
    # <noscript> holds the real <img> for many lazy-loading setups; parse it as HTML.
    for noscript in soup.find_all("noscript"):
        if isinstance(noscript, Tag) and noscript.string:
            noscript.replace_with(BeautifulSoup(noscript.string, "lxml"))

    base_tag = soup.find("base", href=True)
    base = base_url
    if isinstance(base_tag, Tag):
        base = _join(base_url, _attr(base_tag, "href") or "") or base_url

    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = _attr(anchor, "href") if isinstance(anchor, Tag) else None
        if _usable(href) and not href.startswith("#") and (link := _join(base, href)):
            links.append(link)
    images = [url for ref in _image_refs(soup) if (url := _join(base, ref))]
    lead = _lead_ref(soup)
    lead_url = _join(base, lead) if lead else None
    if lead_url:
        images.append(lead_url)
    return PageExtract(links=_unique(links), image_refs=_unique(images), lead_ref=lead_url)


def _join(base: str, ref: str) -> str | None:
    """Resolve ``ref`` against ``base``; a malformed URL (e.g. a bad IPv6 host) is skipped."""
    try:
        return urljoin(base, ref)
    except ValueError as exc:
        log.info("url skipped url=%s reason=malformed (%s)", for_log(ref), exc)
        return None
