# Progress

## M0 Recon: done (2026-09-30)
- **Built:** `etl.py` source adapters (cache + 1 req/s), `config/teams.json`, `config/eras.json`, `DATA_NOTES.md`, venv, `.gitignore`.
- **Fetched:** Understat league payloads 2014–2026, one match, one player, one team; football-data CSVs 2014–2026 (in `data/raw/`, gitignored).
- **Verified:** Understat serves JSON via XHR, not embedded; 38 Liverpool fixtures in all 13 seasons; 7 team-name mismatches mapped; Iraola appointment date confirmed.
- **Scope changes:** Pinnacle closing odds are missing for 17 Liverpool 2025/26 matches and all of 2026/27, so M1 uses a fallback chain (Pinnacle → market average → Betfair Exchange) with a per-match `market_source`. Average/Max closing columns don't exist before 2019/20, but Pinnacle covers those seasons.
- **Environment:** only Python 3.10 is installed locally (brief says 3.11+). Code avoids 3.11-only features; CI will use 3.11. Corporate TLS inspection is handled with `truststore` (certificate verification stays on).
- **Open:** Klopp exit and Slot start/end dates are seed values. M0 has no tests defined.

## M1 ETL: done (2026-09-30)
- **Built:** full `etl.py` (`python etl.py` fetches, caches, builds; `--no-fetch` rebuilds from cache) and `tests/test_etl.py`. 7 parquet tables in `data/processed/` (1.6 MB). Fetched 13 league payloads, 461 match payloads, 260 team payloads, 13 CSVs; all cached, never re-fetched for completed seasons.
- **Verified (11 tests pass):** 38 matches per completed season and current season = matches played (5); every match joins exactly once to football-data (date ±1 day, score agrees, hard failure otherwise); all team names map; no duplicate match/shot/roster ids; goals from shots + own goals = final score for every match; player goals + opponents' own goals = team goals per season; roster-derived player-season totals equal Understat's season table; 20 clubs per season in the league table.
- **Scope change (needs your eye):** the brief's "sum of shot xG = team xG ±0.02" is false in the source: 158 of 922 team-matches differ, always upwards, up to 0.88. I made shot-sum xG the primary `xg`/`xga` and kept Understat's figure as `xg_reported`. The test was rewritten to assert this documented behaviour instead of ±0.02 everywhere. Details in DATA_NOTES.md.
- **Market source:** Pinnacle close everywhere except 17 matches in 2025/26 and the 5 in 2026/27, which use the market-average close.
- **Open:** league reference lines use Understat's reported team xG (~1-2% below shot sums). Local Python is 3.10.

## M2 Metrics: done (2026-09-30)
- **Built:** `metrics.json` (63-metric registry), `config/settings.json` (de-vig method, baseline seasons, min sample, takeaway thresholds), `metrics.py`, `tests/test_metrics.py`.
  - `change()` implements the brief's rules (null for null/zero baseline, pp for pct/prob, sentiment from `higher_is_better`).
  - Exact xG simulation by convolution.
  - Proportional and Shin de-vig.
  - Market-expected points, Brier/log loss/reliability, flat-stake P&L, mispriced runs.
  - Rule-based takeaways and the external-model CSV hook.
- **Verified (45 tests pass across ETL + metrics):** 13.3 vs 12.2 = +9.0%; xGA down = good; convolution equals brute-force enumeration (fixed and random small matches); de-vig probabilities in (0,1), sum to 1 ±1e-9, and Shin agrees with proportional within 0.002 on a low-margin book; W+D+L and points arithmetic; every takeaway's cited numbers are recomputed from data in the tests (all seasons, all eras); form is never positive below 7 points from the last five.
- **Cross-check result:** simulated season xPts are within 0.66 of Understat's xPts for all 13 seasons (the brief allowed ±2.0), which independently validates the shot data and the simulation.
- **Decisions to note:**
  - Added an optional `signed` flag to the registry. Goal difference, xG difference and "minus xG" metrics can cross zero, so they show an absolute difference instead of a percentage, which is what prevents "-117%".
  - Percentage change is also null for a negative baseline.
  - The 2015-16 baseline has only one prior season (2014-15) and is labelled that way; 2014-15 has none.
  - "Lowest/highest since" takeaways are only produced for genuinely notable cases (best-since, or worst on record).
  - Player metrics use `min_sample` = 450 minutes; team metrics use 10 matches.
- **Open:** takeaway thresholds (3 goals / 3 points / 10%) are judgement calls and live in `config/settings.json`.

## M3 Shell + Overview: done (2026-09-30)
- **Built:** `build.py` (processed data → dashboard JSON → inlined `dist/index.html`, 334 KB, fails over 5 MB), `template/index.html` (shell, hash routing with shareable state, season and manager-era filters, light/dark themes from CSS custom properties, ⓘ tooltips from the registry, responsive layout), the Overview page, and `tests/test_frontend.py`. `dist/` is git-ignored (CI will build it).
- **Overview contents:** record card (integers, actual), expected card (xPts, market-expected points, xG, xGA, labelled "not actual"), rolling 10-match xG difference chart with hover, crosshair and a table view, last five results, rule-based takeaways (card omitted if no rule fires), vs-baseline table stating the baseline. Titles and subtitles are generated from the selection.
- **Design decision:** all numbers, baselines, changes and takeaways are precomputed in Python per (season, era) selection (32 of them), and the browser only formats them, so there is one implementation of each calculation.
- **Verified (58 tests pass: 13 frontend, 45 ETL/metrics):** every page × every season (+ all) renders with zero console errors and no NaN/undefined text; after switching season no other season label is visible (baseline and takeaway citations are marked and exempt); displayed record values equal the build JSON for every season × era; the small-sample badge shows for 2026-27 (5 matches) and small era slices and not otherwise, with significance takeaways suppressed; baseline panel shows N/A (never 0%) for the first season; arrow follows the sign and colour follows `higher_is_better`; every ⓘ text equals its registry description; hash routing round-trips and invalid params fall back; theme toggle works; no horizontal overflow at 390px.
- **Screenshots reviewed** at 1440×900 and 390×844 (default, all-seasons, 2026-27 small sample, dark). Fixed from review: uneven tile wrapping and stranded ⓘ icons, chart end-label collision, crowded mobile axis ticks, oversized mobile table padding.
- **Environment notes:** the Playwright Chromium download is blocked by the corporate proxy, so tests fall back to system Chrome (`channel="chrome"`); CI uses the bundled Chromium. D3 and Google Fonts requests are stubbed in tests (D3 from a cached copy of the pinned 7.9.0 file; the CDN tag carries an SRI hash).
- **Open:** Attack, Defence, Players, Match Explorer, Market Lens and Methodology are placeholders until M4–M6. Default season is the latest with at least 10 matches (2025-26), since 2026-27 has only 5.

## M4 Attack + Defence: done (2026-09-30)
- **Built:**
  - Attack page: shot map with player/situation/body-part/outcome filters (circle size grows with xG, goals filled), goals-minus-xG by player (with a penalties toggle), threat source mix, shot volume vs xG-per-shot scatter with league clubs and league-average lines, and a per-match xG trend with goals overlaid and a 10-match average.
  - Defence page: shots-conceded map, xGA trend with the league average, open-play vs set-piece vs penalty xGA, PPDA trend, deep completions allowed, and a clean-sheet strip.
  - Both pages start with a KPI row of baseline changes.
  - Every chart has a registry ⓘ tooltip and a table view.
- **Data additions:** `build.py` now ships all shots (both teams, 1.1 MB build), per-selection player rows, source mixes, rolling series and per-season league context.
- **ETL finding:** Understat's team statistics count opponent own goals as 1.0-xG shots, giving three different xG totals per season. Corrected shot-level league figures now equal Liverpool's shot sums exactly (new ETL test); see DATA_NOTES.md.
- **Verified (72 tests pass):** new `tests/test_build.py` checks the payload against independent pandas sums (shots, player rows vs the player-season table, source mixes, league context, rolling series). New Playwright tests check the following.
  - The map stats and every filter match pandas.
  - Pitch is exactly 105×68.
  - Attack circle positions equal (105X, 68Y) and defence positions equal the 180° rotation, compared circle by circle.
  - The finishing table matches pandas, including the non-penalty toggle.
  - The source mix matches pandas by source.
  - The scatter has 20 clubs (240 club-seasons for all seasons).
  - Bar counts equal matches, and the clean-sheet strip equals the record.
  - League reference lines are drawn (13 segments for all seasons).
  - Small-sample and empty states, and the registry tooltip on every chart title.
- **Screenshots reviewed** (desktop and 390px): fixed collapsed half-width cards (missing `span-6`), an unreadable in-pitch caption, clipped player names on mobile, a squashed single-group mix bar, an invisible legend swatch, and a "−0.0" label.
- **Design decisions:** circle size uses r = 3.5 + 12·√xG px so tiny shots stay visible at the 8 px marker minimum (size still increases monotonically with xG; the registry text says "grows with", not "proportional"). The pitch is drawn from 36 m (attack) / to 69 m (defence) to avoid empty space; hidden long shots are disclosed.
- **Open:** Players, Match Explorer, Market Lens and Methodology remain placeholders.

## M5 Players + Match Explorer: done (2026-09-30)
- **Players page:**
  - Role leaders (Finisher npG−npxG, Shot Threat npxG/90, Creator xA/90, Build-up xGBuildup/90, Involvement xGChain/90), ranked among players above the minimum-minutes filter.
  - Searchable, sortable squad table with per-90 values and a minimum-minutes filter (450 by default, 90 for selections under 10 matches).
  - Player profile with season-over-season trend charts (hollow points for seasons under 450 minutes) and a season table.
  - Two-player comparison for the selected season/era.
- **Match Explorer:**
  - Match picker (any match in the selection, plus step buttons).
  - Scoreboard with scorers (own goals credited to the other side).
  - xG race chart.
  - One full-pitch map of both teams' shots (Liverpool attack →, opposition rotated).
  - Market panel with de-vigged pre-match probabilities (proportional and Shin), the exact xG-simulated probabilities, the actual result and expected points, raw closing odds, the odds source and the Shin z.
  - Shot list.
- **Shareable state:** `player`, `vs` and `match` are kept in the URL (`replaceState`, no re-render), with validated fallbacks.
- **Data additions:** per-90 fields computed in Python (`build.py`); market detail per match (both de-vig methods, raw odds, margin, Shin z).
- **Precision fix found by the tests:** payload xG and probabilities were rounded to 4 decimals, and adding rounded shot xG in the browser gave a race-chart total (2.22) that disagreed with the scoreboard (2.21) for one match. xG and probabilities now ship at 6 decimals, and the race-chart end labels use the exact match total from Python.
- **Verified (82 tests):**
  - Squad table rows, filters, search and sorting equal an independent pandas calculation from rosters and shots.
  - Role-leader ranking equals pandas, including after changing the filter.
  - The profile's season table equals the player-season table, and comparison values equal pandas.
  - A shared URL restores profile and comparison.
  - All 461 matches are rendered one by one, and each scoreline, title, both teams' xG, goal markers, scorer lists, shot circles, market probabilities (independent de-vig) and simulated probabilities (independent convolution) reconcile with the data.
  - The picker steps, defaults to a season's latest match, and falls back correctly across seasons.
  - Pages that span seasons keep other season labels out of visible text (the profile is exempt and marked).
  - New payload tests cover the per-90 fields and market payload.
- **Screenshots reviewed** (desktop and 390px). Fixed: the probability and shot tables were clipped in narrow cards (now compact and widened), and the shot list mislabelled the team for own goals.
- **Open:** Market Lens and Methodology are still placeholders. The all-matches reconciliation test takes about 90 s.

## M6 Market Lens + Methodology: done (2026-09-30)
- **Market Lens page:** all six panels from the brief.
  - Actual vs xPts vs market-expected points per season (era-filterable).
  - Cumulative results vs market with shaded mispriced runs, plus a runs table.
  - Calibration: Brier and log loss for market vs xG simulation (plus an external model when present) next to a naive benchmark, and a reliability plot.
  - A per-match strip of market and simulated win probabilities against actual results.
  - Flat 1-unit stake: cumulative P&L, ROI by season and a "Retrospective analysis, not a strategy" badge.
  - A documented external-model hook.
- **Methodology page:** generated at build time from `template/methodology.md` plus generated blocks.
  - Generated blocks: the metric glossary from `metrics.json` (every metric once), live data-coverage counts, worked de-vig and xG-simulation examples computed by the real functions, and the takeaway thresholds read from settings.
  - Also included: sources and joins, baselines, the market-lens definitions, the known data quirks, and how accuracy is enforced.
- **Data additions:** per selection, `cal` (Brier, log loss, reliability, naive benchmark), `runs`, and cumulative market/xPts/P&L series.
- **External model hook (M2 code, now visible):** `data/external/model_probs.csv` is scored with the same maths. An end-to-end test writes a CSV containing the market's own probabilities for 20 matches and checks that the model's Brier equals the market's Brier on those matches (rebuilt page shows the row, then it disappears for seasons with no rows).
- **Verified (95 tests):**
  - The calibration table (own Brier and log-loss code and own de-vig), season table (points, xPts, market xPts, P&L, ROI for all 13 seasons), P&L and ROI, and mispriced runs (own windowing and merging) all equal independent recomputations.
  - Reliability counts sum to 3 per match, the strip has one mark per match, and the era filter restricts the seasons.
  - The Methodology worked examples equal an independent de-vig and a brute-force simulation of the same shots.
  - Glossary rows and text equal the registry.
  - Coverage numbers equal the data.
  - The markdown converter escapes raw HTML.
  - TOC clicks scroll without breaking hash routing.
  - Every page (including market and methodology) shows no other season label on screen.
- **Fixed after review:** a clipped runs table, overlapping ROI axis labels, result colours in the strip that clashed with the market series, a "+0.0" axis tick.
- **Inaccuracy caught in my own text:** the M1 docstring said Understat's team xG is lower than the shot sum in "~14%" of team-matches; the measured figure is 17% (158 of 922). The Methodology page now computes that share from the data instead of hard-coding it.
- **Open:** M7 polish and M8 ship remain. The Methodology page is long (the glossary is 89 rows); a collapsible glossary may help in M7.

## M7 Polish: done (2026-09-30)
- **Methodology is collapsible (requested):** every section is a `<details>` (only the first starts open), the seven glossary groups are collapsible too with metric counts, and there are "Expand all / Collapse all" buttons. The contents links open a collapsed section before scrolling, and never touch the hash route. Test-verified.
- **Resilience:**
  - The page shell paints before D3 arrives (D3 is `defer`red, with a loading message).
  - If D3 can't load, a clear message replaces a blank page.
  - A page that throws while drawing shows an error card, keeps the nav working and still logs the error (tested by deliberately corrupting a payload).
  - Added a favicon and theme-colour/Open Graph tags.
- **Empty states:**
  - A shot map with no matching shots explains itself.
  - Player search or minutes filters with no results say so, and role leaders say "No players above the filter".
  - "N/A" is shown instead of a divide-by-zero.
  - An era with no matches in a season gets an empty state (from M3).
- **Mobile pass:**
  - The current page stays visible in the horizontal nav (scrolls only if needed).
  - ⓘ has a 36 px invisible touch target, and every button, select, input and summary is at least 32 px tall (tested on all pages).
  - Tooltips work on touch (tap to show, stay after the finger lifts, tap elsewhere to dismiss; `hover: none` devices toggle ⓘ on tap).
  - Long per-match charts open on the latest matches with a scroll hint (only shown when they overflow).
  - New page → scroll to top and focus moved to `main` (no outline); changing season does not jump.
  - No horizontal overflow at 390, 768, 1024 and 1280 on any page.
- **Accessibility/contrast:** text tokens are tested for WCAG AA (4.5:1) against all three surfaces in both themes; two tokens failed and were adjusted (light "muted" text, dark accent).
- **Performance (measured, Chrome, local):** boot 245 ms, DOMContentLoaded 328 ms, JS heap ≈ 10 MB, `dist/index.html` 1.55 MB (377 KB gzipped, so GitHub Pages will serve ~0.4 MB). Slowest page renders: Defence (all seasons) 468 ms, Attack (all seasons) 386 ms (6-8k SVG circles); every other page under 60 ms. Budgets in the test are deliberately looser (boot 1.5 s, render 2.5 s). Charts redraw only when the width changes.
- **Visual QA from screenshots** (1440, 768, 390; light and dark; every page; heavy "all seasons" variants). Fixed:
  - a stray red focus outline around the page after navigation;
  - unreadable 7,700-shot overplot (circles scale down when more than 800 shots are shown and the page says so);
  - overlapping "beat/lagged" labels on 17 runs (labels only where they fit, plus a legend);
  - a clipped opponent name on the xG race chart;
  - mis-aligned shot-list columns;
  - a noisy scroll hint on charts that don't scroll.
- **Verified (111 tests):** all previous tests plus collapsible methodology, loading state, D3 failure, render failure, empty filters, scroll behaviour, mobile nav, touch targets, touch tooltips, tablet overflow, contrast and performance budgets.
- **Open:** M8 (GitHub Actions weekly build, deploy gated on tests; README as portfolio write-up with screenshots).

## M8 Ship: done (2026-09-30)
- **Workflow:** `.github/workflows/deploy.yml` runs on a weekly cron (Tuesday 06:00 UTC), manual dispatch and pushes to `main`: install → restore raw cache → **ETL → pipeline/metric/payload tests → build → browser tests → upload Pages artifact → deploy**. The deploy job `needs` the build job, so any failure (including a source that stops responding) leaves the last good site live. The raw-data cache is saved even when a later step fails, so the long first fetch (about 750 files, roughly 13 minutes) is not lost. Screenshots are uploaded as an artifact for inspection. YAML syntax validated locally; **the workflow itself has not run yet** (needs the first push and Pages enabled).
- **Robustness added for CI:** `etl._get` retries transient failures (connection errors, timeouts, 429/5xx) with exponential backoff, does not retry a 404, and raises after four attempts so the run (and the deploy) fails loudly; two new tests cover this and the one-request-per-second limiter.
- **Docs:** `README.md` written as a portfolio write-up (problem and how each reference-dashboard failure is guarded, pages with screenshots, sources, methodology highlights, data quirks found, the 113-test table, architecture, local run, deployment, external-model hook, limitations, attribution). Screenshots in `docs/screenshots/`. Also `requirements.txt`, `LICENSE` (MIT, a choice for the owner to confirm), `data/external/` with README and an example CSV.
- **Verified:** 113 tests pass locally (14 ETL, 34 metrics, 12 build, 53 frontend).
- **Open / not verifiable from here:** the first CI run on GitHub, Pages enablement (Settings → Pages → Source: GitHub Actions), Python 3.11 (developed on 3.10), and Understat's behaviour toward GitHub-hosted runner IPs.

### CI fix (2026-09-30)
First CI run: ETL and 51 of 53 browser tests passed on the runner; two touch tests failed because they read the locally cached D3 file (`data/raw/vendor/d3.min.js`, absent on a fresh runner). All network stubbing now goes through one helper that serves the cached copy when it exists and otherwise uses the real CDN (the page's SRI hash still applies). Verified by running the full 113-test suite with the cached file hidden.
