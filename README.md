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
  by `<title>`. While it's already on the page, it also scrapes the live
  vote count (`dealScore`) and stores it in `<rating>`, so the front-end
  can show an up-to-date count instead of the one-time snapshot captured
  when the deal was first pulled from the RSS feed. Splitting expiration
  out keeps the fast RSS pull (one request) decoupled from the slower
  per-deal page-fetch check.
- **`index.html`** is the front-end: sortable/filterable deal cards with
  heat-meter bars, thumbnails, rating pill badges, and expired banners.
  A favorites system stores full deal snapshots in `localStorage`.

## Automation

Two independent GitHub Actions workflows, each in its own concurrency
group so one never blocks the other:

| Workflow | Trigger | Behavior |
|---|---|---|
| `.github/workflows/pull-deals.yml` | `repository_dispatch` (e.g. every ~15 min externally) or manual | Runs `pull_slickdeals.py`; cancels an in-progress run if a newer one starts, since each run is fast and cheap |
| `.github/workflows/pull-expiration.yml` | `repository_dispatch` or manual | Runs `pull_slickexpiration.py`; queues rather than cancels (a full sweep fetches every unexpired deal's page concurrently, in a couple of minutes) |

Both commit their output file back to the repo, rebasing onto the other's
push if needed to avoid non-fast-forward rejections.

**GitHub secrets required: none.** Slickdeals' RSS feed and individual
deal pages are public — no API key or authentication needed.

## Timing (Google Apps Script triggers)

Both workflows are triggered externally rather than on a GitHub Actions
cron schedule, using two Google Apps Script functions that each fire a
`repository_dispatch` event against the repo:

| Function | Event type dispatched | Fires |
|---|---|---|
| `triggerSlickDealsWorkflow` | `trigger-deals-pull` | `pull-deals.yml` |
| `triggerSlickDealsExpirationWorkflow` | `trigger-expiration-pull` | `pull-expiration.yml` |

```javascript
function triggerSlickDealsWorkflow() {
    const token = PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN");
    const url = "https://api.github.com/repos/mzaiger/SlickDealsTracker/dispatches";

  const options = {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + token,
      "Accept": "application/vnd.github.v3+json",
      "User-Agent": "Google-Apps-Script-Automation"
    },
    contentType: "application/json",
    payload: JSON.stringify({ event_type: "trigger-deals-pull" })
  };

  try {
    UrlFetchApp.fetch(url, options);
    Logger.log("SlickDealsTracker workflow dispatched successfully!");
  } catch (e) {
    Logger.log("Failed to dispatch workflow: " + e.toString());
  }
}
```

```javascript
function triggerSlickDealsExpirationWorkflow() {
    const token = PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN");
    const url = "https://api.github.com/repos/mzaiger/SlickDealsTracker/dispatches";

  const options = {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + token,
      "Accept": "application/vnd.github.v3+json",
      "User-Agent": "Google-Apps-Script-Automation"
    },
    contentType: "application/json",
    payload: JSON.stringify({ event_type: "trigger-expiration-pull" })
  };

  try {
    UrlFetchApp.fetch(url, options);
    Logger.log("SlickDealsTracker workflow dispatched successfully!");
  } catch (e) {
    Logger.log("Failed to dispatch workflow: " + e.toString());
  }
}
```

**Setup:**

1. Go to [script.google.com](https://script.google.com/home/), create (or
   open) the project, and paste in both functions above.
2. Store the GitHub token as a script property rather than hardcoding it:
   **Project Settings → Script Properties → Add script property**, name
   it `GITHUB_TOKEN`, and set it to a GitHub personal access token scoped
   to trigger repo dispatches (`repo` scope) on
   `mzaiger/SlickDealsTracker`. The script reads it at runtime via
   `PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN")`,
   so the token never appears in the script body.
3. Add time-driven triggers for each function independently: in the Apps
   Script editor, **Triggers** (clock icon) → **Add Trigger** →
   function `triggerSlickDealsWorkflow`, event source **Time-driven**,
   type **Minutes timer → Every 15 minutes** (matching the `pull-deals.yml`
   cadence noted above). Repeat for `triggerSlickDealsExpirationWorkflow`
   on whatever cadence makes sense for the slower expiration sweep.
4. On the GitHub side, `pull-deals.yml` and `pull-expiration.yml` already
   listen for `repository_dispatch` — just make sure their `types:` match
   the `event_type` values dispatched above:

   ```yaml
   on:
     repository_dispatch:
       types: [trigger-deals-pull]      # pull-deals.yml
   ```

   ```yaml
   on:
     repository_dispatch:
       types: [trigger-expiration-pull] # pull-expiration.yml
   ```

Rotate the `GITHUB_TOKEN` script property periodically like any personal
access token, and never paste the raw token into the script code or
commit it anywhere.

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
