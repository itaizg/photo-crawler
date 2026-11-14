# Mission Brief — Drop Page Links to Images a Page No Longer Shows

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: [TODO.md](../../TODO.md), "Pages keep links to images they no longer show" (Sourcery review of #5, 2026-11-11).

## Mission

> "return a list of all pages on the site that contain an image of that same character."

The key word is "contain": a page should be returned only if it shows the image now. Today the download step adds `page_image` links and refreshes `last_seen_at`, but never removes one. A home page re-crawled every day keeps collecting links to images it showed weeks ago, and search returns it for people it no longer shows.

## Goals

1. **Remove stale links**: after a page is re-crawled, its links that were not seen in that crawl stop counting as "page contains image".
2. **Leads follow**: if the removed link was the page's lead (`is_lead`), the page has no lead until the next crawl declares one.
3. **Report it**: the `summarize` table counts the links removed.

## Constraints

- **Only fully parsed pages**: links are removed only for pages fetched and parsed successfully in this run. A page that failed, was skipped by robots.txt or was cut off by `page_cap` keeps its links.
- **Images stay**: removing a link never deletes the image or its faces. They may still be linked from other pages (549 images are shared today), and keeping them avoids re-downloading them if they come back.
- **Atomic per page**: the page's new link set replaces the old one in one transaction, next to the existing lead-replacement logic in `store.link`.
- **Read contract**: if search is the side that filters instead, [index-read.md](../../specs/001-image-indexer/contracts/index-read.md) must say so, and feature 002 must implement it.

## Non-Goals

- Deleting orphaned images, i.e. images no page links to any more. That is a separate retention decision.
- Tracking link history (when a page showed what).
- Detecting changes on pages that were not re-crawled.

## Success Criteria

1. A page re-crawled without image X no longer has a `page_image` row for X, or search ignores that row.
2. A page whose crawl failed keeps all its links.
3. The offline smoke test covers it: the mock home page drops an image between runs, and the link is gone after the second run.
4. The quickstart §3 checks still pass, including the "one lead per page" check.

## Open Questions

- **Where it happens**: delete in the pipeline (the index stays exact), or keep the rows and have search ignore links with `last_seen_at < page.last_crawled_at` (cheaper, and keeps history)?
- **Timing**: within one run, `link` is called per image. Removal must happen once per page, after all its images are processed. Does it belong in `download`, or in a short per-page finalize step?
