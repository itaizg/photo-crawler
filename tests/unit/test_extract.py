"""Image and link extraction (research R6). The HTML snippets mirror the markup patterns seen on
edition.cnn.com and people.com (responsive <picture>, lazy-loaded <img>, og:image), kept small and
synthetic so no third-party pages are committed."""

from face_index.extract import best_srcset_candidate, extract_page

BASE = "https://edition.cnn.com/2026/11/10/world/story"

CNN_LIKE = """
<html><head>
  <meta property="og:image" content="https://media.cnn.com/api/v1/images/lead.jpg?c=16x9">
</head><body>
  <a href="/world">World</a> <a href="https://edition.cnn.com/politics#top">Politics</a>
  <a href="#comments">skip</a> <a href="javascript:void(0)">js</a>
  <picture>
    <source srcset="https://media.cnn.com/a.jpg?w=320 320w,
                    https://media.cnn.com/a.jpg?w=1280 1280w">
    <img src="https://media.cnn.com/a.jpg?w=100" alt="">
  </picture>
  <img src="/relative/b.jpg">
  <img src="data:image/gif;base64,R0lGOD" data-src="https://media.cnn.com/lazy.jpg">
</body></html>
"""

PEOPLE_LIKE = """
<html><head><meta name="twitter:image" content="/thmb/lead.jpg"></head><body>
  <img data-srcset="/thmb/x-400.jpg 400w, /thmb/x-1200.jpg 1200w" src="/thmb/placeholder.gif">
  <img data-original="/thmb/old.jpg">
  <img data-lazy-src="/thmb/lazy2.jpg">
  <div class="hero" style="background-image: url('/thmb/hero.jpg')"></div>
  <div style="background: image-set(url(/thmb/s.jpg) 1x, url(/thmb/l.jpg) 2x) no-repeat"></div>
  <noscript><img src="/thmb/noscript.jpg"></noscript>
</body></html>
"""


def test_cnn_like_page() -> None:
    page = extract_page(CNN_LIKE, BASE)
    assert page.lead_ref == "https://media.cnn.com/api/v1/images/lead.jpg?c=16x9"
    assert "https://media.cnn.com/a.jpg?w=1280" in page.image_refs
    assert "https://edition.cnn.com/relative/b.jpg" in page.image_refs
    assert "https://media.cnn.com/lazy.jpg" in page.image_refs
    assert page.lead_ref in page.image_refs
    assert not any(ref.startswith("data:") for ref in page.image_refs)
    assert page.links == ("https://edition.cnn.com/world", "https://edition.cnn.com/politics#top")


def test_people_like_page() -> None:
    page = extract_page(PEOPLE_LIKE, "https://people.com/story")
    refs = set(page.image_refs)
    assert page.lead_ref == "https://people.com/thmb/lead.jpg"  # twitter:image fallback
    assert "https://people.com/thmb/x-1200.jpg" in refs
    assert "https://people.com/thmb/old.jpg" in refs
    assert "https://people.com/thmb/lazy2.jpg" in refs
    assert "https://people.com/thmb/hero.jpg" in refs
    assert "https://people.com/thmb/l.jpg" in refs  # last image-set candidate
    assert "https://people.com/thmb/noscript.jpg" in refs


def test_refs_are_unique_and_ordered() -> None:
    html = '<img src="/a.jpg"><img src="/a.jpg"><img src="/b.jpg">'
    page = extract_page(html, "https://people.com/")
    assert page.image_refs == ("https://people.com/a.jpg", "https://people.com/b.jpg")
    assert page.lead_ref is None


def test_best_srcset_candidate() -> None:
    assert best_srcset_candidate("a.jpg 1x, b.jpg 2x") == "b.jpg"
    assert best_srcset_candidate("a.jpg 300w, b.jpg 100w") == "a.jpg"
    assert best_srcset_candidate("a.jpg 3x, b.jpg 100w") == "b.jpg"  # w beats x
    assert best_srcset_candidate("only.jpg") == "only.jpg"
    assert best_srcset_candidate("") is None


def test_srcset_urls_may_contain_commas() -> None:
    # Seen on edition.cnn.com: CDN transform parameters separated by commas inside the URL.
    srcset = (
        "https://media.cnn.com/i/c_fill,g_auto,w_400/x.jpg 400w, "
        "https://media.cnn.com/i/c_fill,g_auto,w_1200/x.jpg 1200w"
    )
    assert best_srcset_candidate(srcset) == "https://media.cnn.com/i/c_fill,g_auto,w_1200/x.jpg"
    assert best_srcset_candidate("a.jpg 1x,b.jpg 2x") == "b.jpg"
