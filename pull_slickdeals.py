"""
pull_slickdeals.py

Pulls the Slickdeals Frontpage (Hot Deals) RSS feed and merges new items into
data/deals.xml. Slickdeals has no self-serve public API, so this reads their
public RSS feed directly.

Behavior:
  - Every field present on a feed <item> is captured as-is (title, link,
    description, pubDate, guid, category, etc.) -- whatever Slickdeals sends.
  - Two extra fields are computed and stored alongside the raw ones:
      rating       -- parsed from "Thumb Score: +N" in the description
      thumbnail    -- first <img src="..."> found in the description HTML
      pubDateIso   -- pubDate normalized to ISO-8601 UTC, for reliable
                      sorting/filtering in the browser (RFC-822 dates are
                      a pain to parse consistently client-side)
  - Items are keyed by guid (falls back to link if guid is missing). If a
    key already exists in data/deals.xml, it is NOT duplicated -- only its
    rating/thumbnail are refreshed in place, since thumbs-up counts change
    over time but everything else about a deal doesn't.
  - After merging, any item whose pubDate is older than 48 hours is dropped.

Run standalone:
    python scripts/pull_slickdeals.py
"""

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

FEED_URL = "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"
DATA_PATH = Path(__file__).resolve().parent / "deals.xml"
MAX_AGE_HOURS = 48
REQUEST_TIMEOUT = 20
EXPIRED_PHRASE = "Heads up, this deal has expired."
PAGE_REQUEST_TIMEOUT = 15

THUMB_RE = re.compile(r"Thumb Score:\s*\+?(-?\d+)", re.IGNORECASE)
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
THUMB_IMG_URL_RE = re.compile(r'https?://[^\s"\'<>]+\.thumb[^\s"\'<>]*', re.IGNORECASE)


def fetch_feed_items():
    """Download the RSS feed and return a list of dicts, one per <item>,
    preserving every child tag found (namespaces stripped for simplicity)."""
    resp = requests.get(
        FEED_URL,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 (compatible; slickdeals-tracker/1.0)"},
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    items = []
    for item_el in root.iter("item"):
        fields = {}
        for child in item_el:
            tag = child.tag.split("}")[-1]  # strip any namespace
            text = (child.text or "").strip()
            if tag in fields:
                # Duplicate tag (e.g. multiple <category>) -- keep first, ignore rest.
                continue
            fields[tag] = text
        if fields:
            items.append(fields)
    return items


def enrich(fields):
    """Add computed rating / thumbnail / pubDateIso to a raw feed item dict."""
    description = fields.get("description", "")
    content_encoded = fields.get("encoded", "")  # <content:encoded>, namespace stripped

    # Thumb Score is usually stored in <content:encoded>, not <description>.
    thumb_match = THUMB_RE.search(content_encoded)
    if not thumb_match:
        thumb_match = THUMB_RE.search(description)

    fields["rating"] = thumb_match.group(1) if thumb_match else ""

    # Prefer the first URL containing ".thumb" inside <content:encoded> -- that's
    # Slickdeals' dedicated thumbnail-sized image. Fall back to the first <img> in
    # <description> if content:encoded has no ".thumb" URL (or the tag is missing).
    thumb_url_match = THUMB_IMG_URL_RE.search(content_encoded)
    if not thumb_url_match:
        thumb_url_match = THUMB_IMG_URL_RE.search(description)
    if thumb_url_match:
        fields["thumbnail"] = thumb_url_match.group(0)
    else:
        img_match = IMG_RE.search(description)
        fields["thumbnail"] = img_match.group(1) if img_match else ""

    # content:encoded is often the full post body (large) -- we only needed it to
    # scrape the thumbnail above, so drop it before this item gets persisted to
    # keep data stored in the xml lean.
    fields.pop("encoded", None)

    pub_raw = fields.get("pubDate", "")
    try:
        dt = parsedate_to_datetime(pub_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        fields["pubDateIso"] = dt.astimezone(timezone.utc).isoformat()
    except Exception:
        fields["pubDateIso"] = ""

    return fields


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


def refresh_expired_flags(deals_dict):
    """Check expired status for every deal that isn't already flagged expired.

    Once a deal is marked expired it stays expired -- deals don't come back
    from expired, so there's no need to keep re-fetching those pages on
    every run. This keeps request volume bounded as the feed grows instead
    of re-checking the whole 48-hour window every 15 minutes.
    """
    checked, newly_expired = 0, 0
    for fields in deals_dict.values():
        if fields.get("expired") == "true":
            continue
        result = check_expired(fields.get("link"))
        checked += 1
        if result is None:
            # Fetch failed -- leave whatever expired value (or lack of one)
            # was already stored.
            continue
        if result:
            newly_expired += 1
        fields["expired"] = "true" if result else "false"
    return checked, newly_expired



    return fields.get("guid") or fields.get("link")


def load_existing():
    """Load data/deals.xml into a dict keyed by guid/link. Returns {} if the
    file doesn't exist yet."""
    if not DATA_PATH.exists():
        return {}

    tree = ET.parse(DATA_PATH)
    existing = {}
    for deal_el in tree.getroot().findall("deal"):
        fields = {child.tag: (child.text or "") for child in deal_el}
        key = item_key(fields)
        if key:
            existing[key] = fields
    return existing


def merge(existing, new_items):
    added, updated = 0, 0
    for raw in new_items:
        fields = enrich(dict(raw))
        key = item_key(fields)
        if not key:
            continue

        if key in existing:
            # Not a new deal -- just refresh the mutable rating/thumbnail.
            if existing[key].get("rating") != fields.get("rating"):
                updated += 1
            existing[key]["rating"] = fields.get("rating", existing[key].get("rating", ""))
            if fields.get("thumbnail"):
                existing[key]["thumbnail"] = fields["thumbnail"]
        else:
            existing[key] = fields
            added += 1

    return existing, added, updated


def truncate_old(existing):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    kept = {}
    dropped = 0
    for key, fields in existing.items():
        iso = fields.get("pubDateIso", "")
        try:
            dt = datetime.fromisoformat(iso)
        except Exception:
            # If we can't parse a date, keep it rather than silently losing data.
            kept[key] = fields
            continue
        if dt >= cutoff:
            kept[key] = fields
        else:
            dropped += 1
    return kept, dropped


def write_xml(deals_dict):
    root = ET.Element("deals")
    root.set("generated", datetime.now(timezone.utc).isoformat())

    # Newest first.
    ordered = sorted(
        deals_dict.values(),
        key=lambda f: f.get("pubDateIso", ""),
        reverse=True,
    )

    for fields in ordered:
        deal_el = ET.SubElement(root, "deal")
        for tag, value in fields.items():
            child = ET.SubElement(deal_el, tag)
            child.text = value

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(DATA_PATH, encoding="utf-8", xml_declaration=True)


def main():
    try:
        raw_items = fetch_feed_items()
    except Exception as e:
        print(f"Failed to fetch/parse feed: {e}", file=sys.stderr)
        sys.exit(1)

    if not raw_items:
        print("Feed returned no items -- leaving existing data/deals.xml untouched.")
        sys.exit(0)

    existing = load_existing()
    merged, added, updated = merge(existing, raw_items)
    final, dropped = truncate_old(merged)
    checked, newly_expired = refresh_expired_flags(final)
    write_xml(final)

    print(
        f"Fetched {len(raw_items)} feed items | "
        f"added {added} new | refreshed {updated} ratings | "
        f"dropped {dropped} older than {MAX_AGE_HOURS}h | "
        f"checked {checked} pages for expiration ({newly_expired} newly expired) | "
        f"total stored: {len(final)}"
    )


if __name__ == "__main__":
    main()
