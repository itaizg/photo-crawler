"""Every default and limit, in one place (constitution V and VI).

DAG params and environment variables only override these values.
"""

import os
from typing import Final, LiteralString

from face_index.models import Record

__version__: Final[str] = "0.1.0"


class Site(Record):
    name: str
    seed_url: str
    page_hosts: frozenset[str]


SITES: tuple[Site, ...] = (
    Site(
        name="cnn", seed_url="https://edition.cnn.com/", page_hosts=frozenset({"edition.cnn.com"})
    ),
    Site(
        name="people",
        seed_url="https://people.com/",
        page_hosts=frozenset({"people.com", "www.people.com"}),
    ),
)

# Crawl (FR-002, FR-003, FR-005, FR-005a)
DEFAULT_DEPTH: Final[int] = 1
MAX_DEPTH: Final[int] = 10
DEFAULT_PAGE_CAP: int | None = 50
DEFAULT_PAGE_RATE: Final[float] = 1.0
DEFAULT_IMAGE_RATE: Final[float] = 5.0
MAX_PAGE_RATE: Final[float] = 10.0
MAX_IMAGE_RATE: Final[float] = 20.0
MIN_RATE: Final[float] = (
    0.1  # one request per 10 s; a smaller rate would hold a host's shared slot for long
)

# HTTP (research R4)
USER_AGENT: Final[str] = f"photo-crawler/{__version__} (+https://github.com/itaizg/photo-crawler)"
ROBOTS_TOKEN: Final[LiteralString] = "photo-crawler"
CONNECT_TIMEOUT: Final[float] = 10.0
READ_TIMEOUT: Final[float] = 20.0
DNS_TIMEOUT: Final[float] = 10.0  # seconds to wait for one name lookup
ROBOTS_RETRY_SECONDS: Final[float] = 300.0  # how long a robots.txt outage is trusted as "deny all"
MAX_REDIRECTS: Final[int] = 5
MAX_DOWNLOAD_BYTES: Final[int] = 20 * 1024 * 1024
MAX_PAGE_BYTES: Final[int] = 10 * 1024 * 1024
MAX_ROBOTS_BYTES: Final[int] = 512 * 1024
IMAGE_ACCEPT: Final[LiteralString] = "image/jpeg,image/png,image/webp,image/gif;q=0.9,*/*;q=0.5"
PAGE_ACCEPT: Final[LiteralString] = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
TRACKING_PARAMS: Final[frozenset[str]] = frozenset(
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
TRACKING_PREFIXES: Final[tuple[str, ...]] = ("utm_",)

# Database (both adapters): fail fast instead of hanging on an unreachable server
DB_CONNECT_TIMEOUT: Final[int] = 10  # seconds
DB_STATEMENT_TIMEOUT_MS: Final[int] = 300_000

# Normalization (FR-008, FR-010, FR-010a, research R7)
MAX_SIDE: Final[int] = 1024
MIN_SIDE: Final[int] = 64
JPEG_QUALITY: Final[int] = 90
MAX_PIXELS: Final[int] = 50_000_000

# Detection and retries (research R3, R11a)
DETECT_BATCH_SIZE: Final[int] = 50
MAX_DETECT_BATCHES: Final[int] = (
    1000  # below Airflow's default mapping limit (1024); the rest waits a run
)
MAX_ATTEMPTS: Final[int] = 3
# How long a duplicate detection task waits for another worker's lock on the same image before
# leaving the image pending for the next run.
DETECT_LOCK_TIMEOUT_S: Final[int] = 120
MAX_XCOM_BYTES: Final[int] = 10 * 1024 * 1024


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
