/*
 * Shared "my picks" storage for the CFB / NFL / Picks pages.
 *
 * Picks are stored one cookie per game (pick_<sport>_<gameId>) rather than
 * one giant cookie, so any page can cheaply enumerate every active pick for
 * a sport without needing to know the full game list up front. Value is a
 * tiny JSON blob: {"market":"spread"|"moneyline","side":"home"|"away"}.
 * Only one pick is allowed per game -- selecting a new option overwrites
 * the old one, and clicking the active option again clears it.
 *
 * Picks intentionally do NOT store the team name, line, or odds -- those
 * are looked up live from the day's dashboard JSON at render time, so a
 * pick always reflects the latest number even if odds move.
 */

const PICK_COOKIE_PREFIX = 'pick_';
const PICK_COOKIE_DAYS = 210;

function _pickCookieName(sport, gameId) {
  return `${PICK_COOKIE_PREFIX}${sport}_${gameId}`;
}

function _setCookie(name, value, days) {
  const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Lax`;
}

function _deleteCookie(name) {
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; SameSite=Lax`;
}

function getPick(sport, gameId) {
  const target = _pickCookieName(sport, gameId) + '=';
  const parts = document.cookie.split(';');
  for (let raw of parts) {
    raw = raw.trim();
    if (raw.startsWith(target)) {
      try { return JSON.parse(decodeURIComponent(raw.slice(target.length))); }
      catch (e) { return null; }
    }
  }
  return null;
}

function getAllPicks(sport) {
  const prefix = `${PICK_COOKIE_PREFIX}${sport}_`;
  const out = {};
  document.cookie.split(';').forEach(raw => {
    raw = raw.trim();
    const eq = raw.indexOf('=');
    if (eq === -1) return;
    const name = raw.slice(0, eq);
    if (!name.startsWith(prefix)) return;
    const gameId = name.slice(prefix.length);
    try { out[gameId] = JSON.parse(decodeURIComponent(raw.slice(eq + 1))); }
    catch (e) { /* ignore malformed cookie */ }
  });
  return out;
}

// Toggle a pick: clicking the already-active option clears it, clicking any
// other option overwrites it (only one pick allowed per game at a time).
function togglePick(sport, gameId, market, side) {
  const current = getPick(sport, gameId);
  if (current && current.market === market && current.side === side) {
    _deleteCookie(_pickCookieName(sport, gameId));
    return null;
  }
  _setCookie(_pickCookieName(sport, gameId), JSON.stringify({ market, side }), PICK_COOKIE_DAYS);
  return { market, side };
}

// The 4-button toolbar (away/home x spread/moneyline) for one game card.
function renderPickToolbar(sport, g) {
  const pick = getPick(sport, g.id);
  const opts = [
    { market: 'spread', side: 'away', label: `${g.away_team} ATS` },
    { market: 'spread', side: 'home', label: `${g.home_team} ATS` },
    { market: 'moneyline', side: 'away', label: `${g.away_team} ML` },
    { market: 'moneyline', side: 'home', label: `${g.home_team} ML` },
  ];
  const btns = opts.map(o => {
    const active = pick && pick.market === o.market && pick.side === o.side;
    return `<button type="button" class="pick-btn${active ? ' active' : ''}" data-sport="${sport}" data-game="${g.id}" data-market="${o.market}" data-side="${o.side}">${active ? '\u2605 ' : ''}${o.label}</button>`;
  }).join('');
  return `<div class="pick-toolbar">${btns}</div>`;
}

// CSS class to drop on an odds-table cell so the picked market/side lights
// up yellow wherever it appears (both the DraftKings and FanDuel rows).
function pickCellClass(sport, g, market, side) {
  const pick = getPick(sport, g.id);
  return (pick && pick.market === market && pick.side === side) ? 'picked' : '';
}

// Click-delegation for every .pick-btn inside containerEl. `onPick` runs
// after each toggle so the caller can re-render with the new cookie state.
function attachPickHandlers(containerEl, onPick) {
  containerEl.addEventListener('click', (e) => {
    const btn = e.target.closest('.pick-btn');
    if (!btn || !containerEl.contains(btn)) return;
    const { sport, game, market, side } = btn.dataset;
    togglePick(sport, game, market, side);
    if (typeof onPick === 'function') onPick();
  });
}

/*
 * Gemini Prediction Summary block, shared across all three pages.
 *
 * Every game with a "gemini_prediction" field (attached by the Python
 * builders when DK/FanDuel odds are posted) gets a collapsed toggle
 * button showing the confidence score, which expands to the full
 * winner / ATS / analysis panel. Uses click-delegation on whatever
 * container the games were rendered into, same pattern as
 * attachPickHandlers, so it survives re-renders without re-binding.
 */

function renderGeminiBlock(g) {
  const p = g.gemini_prediction;
  if (!p) return '';

  const fmt = (v) => (v !== undefined && v !== null && v !== '') ? `${v}%` : '—';

  // Main pick confidence
  const conf = fmt(p.confidence);

  // ATS confidence: use a dedicated field if your Python builder provides one,
  // otherwise fall back to the main confidence so it's never blank.
  const atsRaw = (p.ats_confidence !== undefined && p.ats_confidence !== null) ? p.ats_confidence
               : (p.ats_conf       !== undefined && p.ats_conf       !== null) ? p.ats_conf
               : p.confidence;
  const atsConf = fmt(atsRaw);

  return `<button type="button" class="gemini-toggle" aria-expanded="false">
    <span class="gemini-toggle-icon">&#10024;</span>
    <span class="gemini-toggle-main">Gemini Prediction Summary</span>
    <span class="gemini-toggle-trailing">
      <span class="gemini-picks-summary">
        <span class="gemini-toggle-winner">Pick: ${p.winner || 'TBD'} (${conf})</span>
        ${p.ats_pick ? `<span class="gemini-toggle-ats">ATS Pick: ${p.ats_pick} (${atsConf})</span>` : ''}
      </span>
      <span class="gemini-caret">▾</span>
    </span>
  </button>
  <div class="gemini-panel" hidden>
    <div class="gemini-panel-row"><span>Winner</span> <span class="gemini-value">${p.winner || '—'}</span></div>
    ${p.ats_pick ? `<div class="gemini-panel-row"><span>ATS Pick</span> <span class="gemini-value">${p.ats_pick}</span></div>` : ''}
    <div class="gemini-panel-row"><span>Confidence</span> <span class="gemini-value">${conf}</span></div>
    ${p.ats_pick ? `<div class="gemini-panel-row"><span>ATS Confidence</span> <span class="gemini-value">${atsConf}</span></div>` : ''}
    <p class="gemini-analysis">${p.analysis || ''}</p>
  </div>`;
}

function attachGeminiHandlers(containerEl) {
  containerEl.addEventListener('click', (e) => {
    const btn = e.target.closest('.gemini-toggle');
    if (!btn || !containerEl.contains(btn)) return;
    const panel = btn.nextElementSibling;
    if (!panel || !panel.classList.contains('gemini-panel')) return;
    const isOpen = !panel.hidden;
    panel.hidden = isOpen;
    btn.classList.toggle('open', !isOpen);
    btn.setAttribute('aria-expanded', String(!isOpen));
  });
}

/*
 * Tiles / Rows view toggle, shared across all three pages.
 *
 * If the person has never picked a mode, it auto-picks Rows on wider
 * (desktop-ish) screens and Tiles on narrow (mobile) screens. Once they
 * click a toggle button, that explicit choice is remembered in
 * localStorage and wins from then on, on every page, until they change it
 * again or clear site data.
 */

const VIEW_MODE_KEY = 'fb_view_mode';
const VIEW_MODE_DESKTOP_BREAKPOINT = '(min-width: 860px)';

function getStoredViewMode() {
  const stored = localStorage.getItem(VIEW_MODE_KEY);
  return (stored === 'tiles' || stored === 'rows') ? stored : null;
}

function getEffectiveViewMode() {
  const stored = getStoredViewMode();
  if (stored) return stored;
  return window.matchMedia(VIEW_MODE_DESKTOP_BREAKPOINT).matches ? 'rows' : 'tiles';
}

function applyViewMode(mode) {
  document.body.classList.toggle('row-view', mode === 'rows');
  document.querySelectorAll('.view-toggle .view-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
}

function setViewMode(mode) {
  localStorage.setItem(VIEW_MODE_KEY, mode);
  applyViewMode(mode);
}

// Call once per page, after the nav (with its .view-toggle buttons) is in
// the DOM. Doesn't depend on board data having loaded.
function initViewToggle() {
  applyViewMode(getEffectiveViewMode());
  document.querySelectorAll('.view-toggle .view-btn').forEach(btn => {
    btn.addEventListener('click', () => setViewMode(btn.dataset.mode));
  });
  // If the person never explicitly chose a mode, keep following the
  // desktop/mobile default as the window is resized.
  window.matchMedia(VIEW_MODE_DESKTOP_BREAKPOINT).addEventListener('change', () => {
    if (!getStoredViewMode()) applyViewMode(getEffectiveViewMode());
  });
}

/*
 * Live scores overlay -- shared by index.html / nfl.html / picks.html.
 *
 * data/scores.json is written hourly by scripts/fetch_scores.py, separate
 * from the once-a-day dashboard builds, so scores can refresh far more
 * often without hitting SharpAPI's/Gemini's tighter rate limits. It's
 * shaped as {"cfb": {"<gameId>": {...}}, "nfl": {"<gameId>": {...}}} and
 * simply merged in here at render time by game id -- games with no entry
 * (not started yet) render exactly as before, with no score line.
 */

const SCORES_URL = 'data/scores.json';

// Fetches data/scores.json and returns its `cfb` or `nfl` lookup (by game
// id). Never throws -- if the file is missing or malformed (e.g. the
// hourly workflow hasn't run yet), returns {} so the rest of the page
// renders normally with no score lines.
async function fetchScores(sport) {
  try {
    const res = await fetch(SCORES_URL, { cache: 'no-store' });
    if (!res.ok) return {};
    const data = await res.json();
    return data[sport] || {};
  } catch (e) {
    return {};
  }
}

// Renders the "line 2" score badge for a game, or '' if no score yet.
// `scores` is the {gameId: {...}} lookup from fetchScores(). Away team is
// listed first to match the "Away @ Home" order used in the matchup line.
function renderScoreLine(g, scores) {
  const s = scores && scores[String(g.id)];
  if (!s || s.home_score === null || s.away_score === null) return '';

  const isLive = s.status === 'in_progress';
  const isFinal = s.status === 'final';
  const cls = isLive ? 'score-line live' : isFinal ? 'score-line final' : 'score-line';
  const label = isLive
    ? `<span class="live-dot"></span>LIVE${s.status_detail ? ' \u00b7 ' + s.status_detail : ''}`
    : 'FINAL';

  return `<span class="${cls}">${label} &mdash; ${g.away_team} ${s.away_score}, ${g.home_team} ${s.home_score}</span>`;
}
