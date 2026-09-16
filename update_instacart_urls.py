"""
update_instacart_urls.py

Sets (or refreshes) an "INSTACART_URL" field on every item in
candidate_pool.json, built from that item's own PRODUCT_NAME -- not
just the Kroger-sourced rows kroger_new_items.py adds going forward,
but every row already in the pool, including the original 2022
Walmart-CSV ones.

No network calls, no API key -- this is a pure local transform of
PRODUCT_NAME into an Instacart search-results URL:
    https://www.instacart.com/store/s?k=<product+name+with+pluses>
using the exact same build_instacart_search_url() logic
kroger_new_items.py uses (apostrophes dropped outright, e.g.
"Callender's" -> "Callenders" rather than "Callender s"; everything
else non-alphanumeric turned into spaces; lowercased; words joined
with "+").

By default this OVERWRITES any existing INSTACART_URL on every row
(the whole point is "update all the urls for all products" -- a
recompute from the current PRODUCT_NAME, not a one-time fill-in-the-
blanks). Use --only-missing if you'd rather only fill rows that don't
have one yet.

Usage:
    python update_instacart_urls.py                  # recompute for every row, in place
    python update_instacart_urls.py --dry-run         # show what would change, write nothing
    python update_instacart_urls.py --only-missing    # only fill rows with no INSTACART_URL yet
    python update_instacart_urls.py --input other.json --output other.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_INPUT = "candidate_pool.json"
DEFAULT_OUTPUT = "candidate_pool.json"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def build_instacart_search_url(text):
    """Same logic as kroger_new_items.py's build_instacart_search_url():
    truncates at the first comma first (size/count/variant detail after
    a comma tends to over-narrow the search), keeps apostrophes as a
    literal "%27" in place (so a possessive like "Callender's" becomes
    "Callender%27s", not "Callender s" or "Callenders"), replaces
    everything else that isn't a letter/digit/space with a space,
    lowercases, and joins words with "+"."""
    text = (text or "").split(",", 1)[0]
    placeholder = "\x00"  # stands in for an apostrophe so the strip-special-chars
                          # step below doesn't touch it before it becomes %27
    cleaned = text.replace("'", placeholder).replace("\u2019", placeholder)
    cleaned = re.sub(r"[^a-zA-Z0-9\s" + placeholder + r"]", " ", cleaned)
    words = cleaned.lower().split()
    joined = "+".join(words).replace(placeholder, "%27")
    return f"https://www.instacart.com/store/s?k={joined}" if words else ""


def load_pool(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_pool(path, pool):
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2, ensure_ascii=False)
        f.write("\n")
    Path(tmp_path).replace(path)  # atomic-ish swap, same pattern as the other scripts here


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to candidate_pool.json (input)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path to write results (default: same as input)")
    parser.add_argument("--only-missing", action="store_true",
                         help="Only set INSTACART_URL on rows that don't already have one, "
                              "instead of recomputing it for every row")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would change without writing anything")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    pool = load_pool(in_path)
    log(f"Loaded {len(pool)} pool item(s) from {in_path}.")

    updated = 0
    unchanged = 0
    cleared_no_name = 0

    for item in pool:
        if args.only_missing and item.get("INSTACART_URL"):
            unchanged += 1
            continue

        product_name = item.get("PRODUCT_NAME") or item.get("name") or ""
        new_url = build_instacart_search_url(product_name)
        old_url = item.get("INSTACART_URL")

        if not new_url:
            cleared_no_name += 1
            continue  # no product name to build a query from -- leave whatever was there alone

        if new_url != old_url:
            if not args.dry_run:
                item["INSTACART_URL"] = new_url
            updated += 1
        else:
            unchanged += 1

    log(f"{'Would update' if args.dry_run else 'Updated'} {updated} row(s), "
        f"{unchanged} already correct/skipped, {cleared_no_name} had no PRODUCT_NAME to build a URL from.")

    if args.dry_run:
        log("Dry run -- nothing written.")
        return

    save_pool(out_path, pool)
    log(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
