# SlickDealsTracker

A hot-deals tracker built on top of Slickdeals' public RSS feed (Slickdeals
has no self-serve public API), with a Python backend and a static HTML
front-end.

## How it works

- **`pull_slickdeals.py`** pulls the Slickdeals Frontpage (Hot Deals) RSS
  feed and merges new items into `deals.xml`. Every field on a feed item
  is captured as-is, plus two computed extras: `rating` (parsed from the
  "Thumb Score" in the description) and `thumbnail` (first image found in
  the description HTML). This script only appends/updates deals — it does
  not check expiration.
- **`pull_slickexpiration.py`** checks each unexpired deal's own page for
  expiration and writes results to `expired.xml`, matched to `deals.xml`
  by `<title>`. Splitting expiration out keeps the fast RSS pull
  (one request) decoupled from the slower per-deal page-fetch check.
- **`index.html`** is the front-end: sortable/filterable deal cards with
  heat-meter bars, thumbnails, rating pill badges, and expired banners.
  A favorites system stores full deal snapshots in `localStorage`.

## Automation

Two independent GitHub Actions workflows, each in its own concurrency
group so one never blocks the other:

| Workflow | Trigger | Behavior |
|---|---|---|
| `.github/workflows/pull-deals.yml` | `repository_dispatch` (e.g. every ~15 min externally) or manual | Runs `pull_slickdeals.py`; cancels an in-progress run if a newer one starts, since each run is fast and cheap |
| `.github/workflows/pull-expiration.yml` | `repository_dispatch` or manual | Runs `pull_slickexpiration.py`; queues rather than cancels, since a full per-deal expiration sweep can take a while |

Both commit their output file back to the repo, rebasing onto the other's
push if needed to avoid non-fast-forward rejections.

**GitHub secrets required: none.** Slickdeals' RSS feed and individual
deal pages are public — no API key or authentication needed.

## Running locally

```bash
pip install requests
python pull_slickdeals.py
python pull_slickexpiration.py
```

Then open `index.html` (or serve the folder with
`python -m http.server`) to view the deal cards.

## Structure

```
SlickDealsTracker-main/
├── pull_slickdeals.py        # RSS pull -> deals.xml
├── pull_slickexpiration.py   # per-deal expiration check -> expired.xml
├── deals.xml                 # scraped deal data
├── expired.xml               # expiration status, matched by title
├── index.html                 # front-end
└── .github/workflows/
    ├── pull-deals.yml
    └── pull-expiration.yml
```
