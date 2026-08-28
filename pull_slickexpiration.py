"""
pull_slickexpiration.py

Checks whether deals currently in deals.xml have expired, and writes the
result to expired.xml, keyed by <title>. This is intentionally split out
from pull_slickdeals.py: this script visits every deal's own page (one
request per unexpired deal), which is much slower and heavier than the
single feed request pull_slickdeals.py makes, so it runs on its own
schedule and never blocks new deals from showing up quickly.

While it's already on the deal's own page for the expiration check, it
also scrapes the live vote count (Slickdeals' `dealScore` meta tag) and
stores it alongside the expiration flag. deals.xml only ever has the vote
count as of the moment the deal was first pulled from the RSS feed, so
this is the one place the tracker gets an updated count.

Behavior:
  - Reads deals.xml to get the current (title -> link) set. deals.xml is
    read-only here; this script never writes to it.
  - Reads any existing expired.xml so already-expired deals aren't
    re-checked -- once a deal is marked expired it stays expired (deals
    don't come back from expired), which keeps request volume bounded.
    Its vote count is left frozen at whatever was last captured too.
  - For every title in deals.xml not already known-expired, fetches the
    deal's own page and reads Slickdeals' `expired` meta tag, plus the
    current vote count off the `dealScore` meta tag.
  - Entries for titles no longer present in deals.xml (aged out after 48h)
    are dropped from expired.xml so it never grows unbounded.
  - Matching is by <title> rather than guid/link so the front-end can join
    deals.xml and expired.xml together purely on the title text.

Run standalone:
    python pull_slickexpiration.py
"""

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

DEALS_PATH = Path(__file__).resolve().parent / "deals.xml"
EXPIRED_PATH = Path(__file__).resolve().parent / "expired.xml"
PAGE_REQUEST_TIMEOUT = 15
EXPIRED_TAG_RE = re.compile(r'<meta\b[^>]*\bname=["\']expired["\'][^>]*>', re.IGNORECASE)
EXPIRED_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']')
DEAL_SCORE_TAG_RE = re.compile(r'<meta\b[^>]*\bname=["\']dealScore["\'][^>]*>', re.IGNORECASE)
DEAL_SCORE_CONTENT_RE = re.compile(r'content=["\'](-?\d+)["\']')


def load_deals():
    """Return a dict of {title: link} for every deal currently in
    deals.xml. Returns {} if deals.xml doesn't exist yet."""
    if not DEALS_PATH.exists():
        return {}

    tree = ET.parse(DEALS_PATH)
    deals = {}
    for deal_el in tree.getroot().findall("deal"):
        title_el = deal_el.find("title")
        link_el = deal_el.find("link")
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        if title:
            deals[title] = link
    return deals


def load_existing_expired():
    """Load expired.xml into a dict keyed by title. Returns {} if the file
    doesn't exist yet."""
    if not EXPIRED_PATH.exists():
        return {}

    tree = ET.parse(EXPIRED_PATH)
    existing = {}
    for exp_el in tree.getroot().findall("expiration"):
        title_el = exp_el.find("title")
        expired_el = exp_el.find("expired")
        rating_el = exp_el.find("rating")
        title = (title_el.text or "").strip() if title_el is not None else ""
        expired = (expired_el.text or "").strip() if expired_el is not None else ""
        rating = (rating_el.text or "").strip() if rating_el is not None else ""
        if title:
            existing[title] = {"title": title, "expired": expired, "rating": rating}
    return existing


def extract_expired(html):
    """Read Slickdeals' own `expired` meta tag off a deal page.

    Slickdeals renders its "this deal has expired" banner client-side with
    JS, so it's never present in the server HTML `requests` sees -- but the
    page's <head> already carries the flag as a plain meta tag, same as
    dealScore. Returns True/False, or None if the tag is missing/unparseable
    (caller should treat that as "couldn't determine" rather than "false").
    """
    tag_match = EXPIRED_TAG_RE.search(html)
    if not tag_match:
        return None
    content_match = EXPIRED_CONTENT_RE.search(tag_match.group(0))
    if not content_match:
        return None
    value = content_match.group(1).strip().lower()
    if value in ("yes", "true", "1"):
        return True
    if value in ("no", "false", "0"):
        return False
    return None


def extract_rating(html):
    """Pull the current vote count off a deal page's `dealScore` meta tag.

    Returns an int, or None if the tag is missing or unparseable -- callers
    should fall back to the previous known rating in that case rather than
    guessing.
    """
    tag_match = DEAL_SCORE_TAG_RE.search(html)
    if not tag_match:
        return None
    content_match = DEAL_SCORE_CONTENT_RE.search(tag_match.group(0))
    if not content_match:
        return None
    try:
        return int(content_match.group(1))
    except ValueError:
        return None


def fetch_deal_page(link):
    """Visit a deal's own page for its expiration state and current vote
    count.

    Returns {"expired": bool, "rating": int|None} when the fetch succeeds
    and the expired meta tag could be read, or None if the page couldn't be
    fetched or the expired state couldn't be determined -- callers should
    leave existing values untouched in that case rather than guessing.
    """
    if not link:
        return None
    try:
        resp = requests.get(
            link,
            timeout=PAGE_REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; slickdeals-tracker/1.0)"},
        )
        resp.raise_for_status()
    except Exception:
        return None

    is_expired = extract_expired(resp.text)
    if is_expired is None:
        return None

    return {
        "expired": is_expired,
        "rating": extract_rating(resp.text),
    }


def refresh(deals, existing):
    """Check expiration (and current vote count) for every deal title not
    already known-expired, and drop entries whose title has aged out of
    deals.xml."""
    checked, newly_expired, dropped = 0, 0, 0
    result = {}

    for title in deals:
        entry = existing.get(title)
        if entry and entry.get("expired") == "true":
            # Already known expired -- deals don't un-expire, skip the
            # fetch. Its vote count stays frozen at whatever we last saw.
            result[title] = entry
            continue

        link = deals.get(title, "")
        page = fetch_deal_page(link)
        checked += 1

        if page is None:
            # Fetch failed -- carry forward whatever we had (or blanks if
            # this is the first time we've seen this title) rather than
            # guessing.
            result[title] = entry or {"title": title, "expired": "false", "rating": ""}
            continue

        is_expired = page["expired"]
        if is_expired:
            newly_expired += 1

        # Fall back to the previous rating if this page fetch didn't turn
        # up a dealScore meta tag, so a transient parse miss doesn't blank
        # out a vote count we already had.
        rating = page["rating"]
        if rating is None:
            rating = entry.get("rating", "") if entry else ""
        else:
            rating = str(rating)

        result[title] = {
            "title": title,
            "expired": "true" if is_expired else "false",
            "rating": rating,
        }

    dropped = len(existing) - len(set(existing) & set(deals))

    return result, checked, newly_expired, dropped


def write_xml(expired_dict):
    root = ET.Element("expirations")
    root.set("generated", datetime.now(timezone.utc).isoformat())

    for fields in expired_dict.values():
        exp_el = ET.SubElement(root, "expiration")
        for tag in ("title", "expired", "rating"):
            child = ET.SubElement(exp_el, tag)
            child.text = fields.get(tag, "")

    EXPIRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(EXPIRED_PATH, encoding="utf-8", xml_declaration=True)


def main():
    deals = load_deals()
    if not deals:
        print("deals.xml has no deals yet -- nothing to check.")
        sys.exit(0)

    existing = load_existing_expired()
    final, checked, newly_expired, dropped = refresh(deals, existing)
    write_xml(final)

    print(
        f"deals.xml has {len(deals)} titles | "
        f"checked {checked} pages for expiration + vote count ({newly_expired} newly expired) | "
        f"dropped {dropped} aged-out titles | "
        f"total stored: {len(final)}"
    )


if __name__ == "__main__":
    main()
