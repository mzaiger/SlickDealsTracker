"""
pull_slickexpiration.py

Checks whether deals currently in deals.xml have expired, and writes the
result to expired.xml, keyed by <title>. This is intentionally split out
from pull_slickdeals.py: this script visits every deal's own page (one
request per unexpired deal), which is much slower and heavier than the
single feed request pull_slickdeals.py makes, so it runs on its own
schedule and never blocks new deals from showing up quickly.

Behavior:
  - Reads deals.xml to get the current (title -> link) set. deals.xml is
    read-only here; this script never writes to it.
  - Reads any existing expired.xml so already-expired deals aren't
    re-checked -- once a deal is marked expired it stays expired (deals
    don't come back from expired), which keeps request volume bounded.
  - For every title in deals.xml not already known-expired, fetches the
    deal's own page and looks for Slickdeals' expired-deal notice.
  - Entries for titles no longer present in deals.xml (aged out after 48h)
    are dropped from expired.xml so it never grows unbounded.
  - Matching is by <title> rather than guid/link so the front-end can join
    deals.xml and expired.xml together purely on the title text.

Run standalone:
    python pull_slickexpiration.py
"""

import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

DEALS_PATH = Path(__file__).resolve().parent / "deals.xml"
EXPIRED_PATH = Path(__file__).resolve().parent / "expired.xml"
EXPIRED_PHRASE = "Heads up, this deal has expired."
PAGE_REQUEST_TIMEOUT = 15


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
        title = (title_el.text or "").strip() if title_el is not None else ""
        expired = (expired_el.text or "").strip() if expired_el is not None else ""
        if title:
            existing[title] = {"title": title, "expired": expired}
    return existing


def check_expired(link):
    """Visit a deal's own page and look for Slickdeals' expired-deal notice.

    Returns True/False when the check succeeds, or None if the page couldn't
    be fetched -- callers should leave the existing expired value untouched
    in that case rather than guessing.
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
    return EXPIRED_PHRASE in resp.text


def refresh(deals, existing):
    """Check expiration for every deal title not already known-expired, and
    drop entries whose title has aged out of deals.xml."""
    checked, newly_expired, dropped = 0, 0, 0
    result = {}

    for title in deals:
        entry = existing.get(title)
        if entry and entry.get("expired") == "true":
            # Already known expired -- deals don't un-expire, skip the fetch.
            result[title] = entry
            continue

        link = deals.get(title, "")
        is_expired = check_expired(link)
        checked += 1

        if is_expired is None:
            # Fetch failed -- carry forward whatever we had (or "false" if
            # this is the first time we've seen this title) rather than
            # guessing.
            result[title] = entry or {"title": title, "expired": "false"}
            continue

        if is_expired:
            newly_expired += 1
        result[title] = {"title": title, "expired": "true" if is_expired else "false"}

    dropped = len(existing) - len(set(existing) & set(deals))

    return result, checked, newly_expired, dropped


def write_xml(expired_dict):
    root = ET.Element("expirations")
    root.set("generated", datetime.now(timezone.utc).isoformat())

    for fields in expired_dict.values():
        exp_el = ET.SubElement(root, "expiration")
        for tag in ("title", "expired"):
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
        f"checked {checked} pages for expiration ({newly_expired} newly expired) | "
        f"dropped {dropped} aged-out titles | "
        f"total stored: {len(final)}"
    )


if __name__ == "__main__":
    main()
