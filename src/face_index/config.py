"""Every default and limit, in one place (constitution V and VI).

DAG params and environment variables only override these values.
"""

import os
from dataclasses import dataclass

__version__ = "0.1.0"


@dataclass(frozen=True, slots=True)
class Site:
    name: str
    seed_url: str
    page_hosts: frozenset[str]


SITES: tuple[Site, ...] = (
    Site("cnn", "https://edition.cnn.com/", frozenset({"edition.cnn.com"})),
    Site("people", "https://people.com/", frozenset({"people.com", "www.people.com"})),
)

# Crawl (FR-002, FR-003, FR-005, FR-005a)
DEFAULT_DEPTH = 1
MAX_DEPTH = 10
DEFAULT_PAGE_CAP: int | None = 50
DEFAULT_PAGE_RATE = 1.0
DEFAULT_IMAGE_RATE = 5.0
MAX_PAGE_RATE = 10.0
MAX_IMAGE_RATE = 20.0

# HTTP (research R4)
USER_AGENT = f"photo-crawler/{__version__} (+https://github.com/itaizg/photo-crawler)"
ROBOTS_TOKEN = "photo-crawler"
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
IMAGE_ACCEPT = "image/jpeg,image/png,image/webp,image/gif;q=0.9,*/*;q=0.5"
PAGE_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "cid",
        "ref",
        "ref_src",
        "taid",
        "iid",
    }
)
TRACKING_PREFIXES = ("utm_",)

# Database (both adapters): fail fast instead of hanging on an unreachable server
DB_CONNECT_TIMEOUT = 10  # seconds
DB_STATEMENT_TIMEOUT_MS = 300_000

# Normalization (FR-008, FR-010, FR-010a, research R7)
MAX_SIDE = 1024
MIN_SIDE = 64
JPEG_QUALITY = 90
MAX_PIXELS = 50_000_000

# Detection and retries (research R3, R11a)
DETECT_BATCH_SIZE = 50
MAX_ATTEMPTS = 3
MAX_XCOM_BYTES = 10 * 1024 * 1024


def database_dsn() -> str:
    """The `faces` database DSN. Never log the returned value."""
    dsn = os.environ.get("FACE_INDEX_DSN")
    if not dsn:
        raise RuntimeError("FACE_INDEX_DSN is not set")
    return dsn


def site_by_name(name: str) -> Site:
    for site in SITES:
        if site.name == name:
            return site
    raise KeyError(f"unknown site {name!r}")
