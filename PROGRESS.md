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

### CI fix 2 (2026-09-30)
Second CI run: 52 of 53 browser tests passed; `test_tooltips_work_on_touch` failed on the Linux runner (tapping ⓘ left the tooltip closed) but passed locally. Cause in the page: the ⓘ click handler toggled based on "is a tooltip open", so any browser that also opens it during the same tap (via hover or focus) had the click close it again, and a touch pointer's `pointerleave` (fired when the finger lifts) could dismiss it too. The behaviour depended on each browser's event ordering, which I could only reproduce locally by replaying the events. Fix: a click within 350 ms of the tooltip opening from the same gesture keeps it open, the toggle only closes a tooltip that this same ⓘ opened earlier, and leaving via a touch pointer no longer dismisses. New test `test_info_tooltip_survives_event_orderings_seen_on_other_browsers` replays those orderings deterministically (verified to fail against the old handler); the touch test now runs with and without mobile emulation. 115 tests pass locally with the cached D3 file hidden. Whether this fully resolves the Linux failure can only be confirmed by the next CI run.

## Redesign pass (branch `redesign`, started 2026-09-30)
- **Before screenshots:** `artifacts/screenshots/before/` — every page for 2025-26 (Overview yields 4 takeaways) and 2023-24 (6 takeaways) at 1920×1080, 1440×900 and 390×844.
- **Shot-map scale before the redesign (1920×1080 viewport):** penalty area 568.89 px tall ÷ 40.32 m = **14.109 px/m** (same for the Attack and Defence maps; the old crop showed 73 m across ≈1030 px). The redesigned full-pitch map (109 m wide including goals) must render at the same scale, i.e. an SVG about 1538 px wide at 1920, and scale down proportionally below that.

### Redesign pass: results (branch `redesign`, not merged)
- **Steps committed:** (1-2) theme + Overview; (3) Attack/Defence map stage, drawer and rows; (4) crest; (5) responsive pass and layout fixes on the other pages. Every commit had the tests green.
- **Theme:** dark and light token sets (dark values exactly as specified), Barlow Condensed / Barlow with fallback stacks, 60px upper-case h1 (scales down to 36px on phones, and to 46px for long titles such as Match Explorer's), 18px card labels, 54-64px numbers, 20px gaps, 16px card radius, 232px sidebar with red active pill. Brand red is a fill only; good/bad are teal-green and orange, always with an arrow or sign; small red text and Liverpool chart series use a separate `--red` token.
- **Overview:** dead space fixed by making cards fill (chart and last-5 list stretch, W/D/L bar and deltas pinned to the card bottom); takeaways are tiles with tag, arrow, 64px number, sentence and two comparison bars (columns 4/3/2/1 at 1400/900/600); vs-baseline is two side-by-side halves. `metrics.py` takeaways gained `tag`, `headline`, `direction`, `good`, `bars`; the sentences are byte-identical and a test checks the tile numbers against the sentence.
- **Shot map:** whole 105×68 pitch on a dark stage, same scale as before (**14.11 px/m at 1920, within 1%**; test-enforced), scales down proportionally with a constant 105:68 aspect; 2px lines; goals solid `#ff3b52` with a white 1.3px stroke, misses hollow; legend inside the stage. The old right-hand card and filter row are gone; a left-edge drawer (46×190 tab; 420px panel; bottom sheet under 768px) holds the stats, the filters, and the threat mix (Attack) or open-play vs set-piece xGA (Defence, from the mix data already computed). The mix appears once on Attack. Esc closes and returns focus to the tab; `aria-expanded`, real buttons.
- **Crest:** `assets/crest.png` (transparent background, checked) is resized with Pillow at build time (max 192px tall), inlined as a data URI, shown at about 46×54 in the sidebar only, `alt="Liverpool FC crest"`; without the file the page shows the 6×46 red bar with no errors. Pillow was added to `requirements.txt`. The footer disclaimer is unchanged.
- **Tests (136 pass; was 113; the full suite now takes about 4.5 minutes, mostly the every-page-every-season-both-themes layout checks):** contrast of every text/background token pair in both themes; dead space under 48px at 1920 and 1440 and under 32px at 390, every page × season × theme; takeaway grid for 0/1/4/5/6/8 tiles at five widths; map scale, 105:68 aspect at five widths, shots-shown equals plotted dots for several filter combinations on both pages, tab label count, drawer behaviour and sizing, single threat mix, legend position, bottom sheet, solid-vs-hollow fill; crest present/fallback. Existing tests kept their assertions: changes were selectors only (upper-case CSS text read via `text_content`, drawer opened before using its filters, the full pitch replacing the crop in the position test, threat-mix/xGA-mix looked up by `aria-label` instead of `section`).
- **After-screenshots:** `artifacts/screenshots/after/` (dark) and `after/light/`, every page for 2025-26 and 2023-24 at 1920, 1440 and 390; before/after composites in `artifacts/screenshots/compare/`.

**Deviations from the mockups / spec, and why**
- Light theme: three derived colours are darker than the spec's suggestions so text passes 4.5:1: good `#187a4e` (spec `#1f8a5b` gave 4.3:1), teal text `#00736e`, and gold `#e0b400` for chart fills (the spec's `#f6eb61` is invisible on white).
- The mockup's "Dark" button is the theme toggle showing the current theme; it is red, as in the mockup.
- With two takeaways the last tile spans three of four columns, as the spec's "last tile spans the remainder" rule dictates (lopsided but by design).
- The crest keeps its true aspect (about 0.58): it sits inside a 46×54 slot with `object-fit: contain`, so it renders roughly 31 px wide, not stretched to 46×54.
- Sidebar brand text is hidden under 600px so the top bar stays one row.
- Players, Match Explorer and Market Lens got layout changes beyond theme only where the dead-space rule flagged them: charts and lists now fill their card height, some card pairs became equal halves (Market: chart/runs; Match: race/market and map/shots), the runs and calibration tables stretch their rows, and the Match pitch sits on the same dark stage.
- `min-height` of Overview bars, tile sizes and the 48px/32px limits are as specified; the dead-space rule measures the bottom edge only, so I also measured empty bands *between* blocks: after the final fixes the largest such gap on any page, season and both widths is 38px.
- The Defence "open play vs set piece xGA" card no longer exists separately; it lives in the drawer with the same data.
- The crest and `design/` mockups were added to the repo by `git add -A`; remove `design/` before merging if you do not want the mockups public.

### CI fix 3 (2026-09-30, after the redesign merge)
The first CI run of the redesign failed two browser tests that pass on Windows: the runner has no Barlow (fonts are stubbed in tests, and can fail for real users) and falls back to DejaVu Sans, which is far wider.
- **Overflow (Market Lens, 390px):** the three stake tiles ("Stakes", "P&L", "ROI") sat in three columns at 44px, so "−22.4%" pushed the page 75px wider than the screen. Fixed by two columns and 38px numbers on phones, `min-width: 0` on tiles and wrapping on values.
- **Drawer sizing:** with wider text the drawer's content was 27px taller than the stage, so it scrolled internally, which is allowed by the design but the test demanded no scrolling. The test now applies the no-scroll/no-band rule only when the content fits in the stage (a capped panel scrolls by design).
- **Regression guard:** new test `test_no_overflow_with_wide_fallback_fonts` forces a DejaVu/Verdana stack and checks every page × several seasons × both themes at 390 and 768px for anything outside a scroll container reaching past the screen. I also ran the dead-space checks under the wide font at 1920, 1440 and 390: no card is over the limit.
- 138 tests pass locally with the cached D3 file hidden (as on CI).

## Style of play: S0 recon (branch `style-of-play`, 2026-09-30)
- Verified against the real data (details and the full axis matrix in DATA_NOTES.md, "Style of play feasibility"): the cached team pages carry `shotZone` and `attackSpeed` splits for all 260 club-seasons, consistent with the club's total shots in 100% of cases, so **no new requests and no new raw data are needed**. PPDA, deep completions and the league player xGChain/xGBuildup cover all seasons too.
- **Axis status:** Defence: Low◄►High (proxy, low), Passive◄►Active (proxy, medium), Lenient◄►Tight (proxy, medium) → full Pressure index. Build-up: Long◄►Short and Vertical◄►Horizontal unavailable, Simple◄►Elaborate proxy (low) → no Control index. Attack: Aerial◄►Grounded unavailable (needs league-wide shot type / last action), Rapid◄►Placed (proxy, medium), Scattered◄►Grouped (proxy, low) → partial Occupation index.
- **Dropped:** the central-rectangle KPI and any shot-coordinate or shot-type KPI, because league-wide they need every match of every club (about 4,560 requests, 76+ minutes, over the CI job limit). They stay in `config/style.json` as external-only KPIs that `data/external/style_kpis.csv` can fill.
- Stop rule passed (Defence 3 computable axes, Attack 2), so work continues.

### Style of play: S1 ETL (2026-09-30)
- `etl.build_style_raw` adds `data/processed/style_raw.parquet` (260 club-seasons × raw zone/speed/PPDA/deep/xGChain aggregates, 80 KB) from the already-cached team pages, league history and league player table. **0 new requests, 0 new raw data.**
- Tests (17 in `test_etl.py`, +3): 20 clubs per season and match counts equal the team-match table; zone and speed splits add up to total shots for and against in every club-season; league shots for = against; Liverpool's shots for and against equal the shot-level dataset exactly in all 13 seasons (own goals accounted for), and Understat's in-box counts agree with a geometric classification within 6%.

### Style of play: S2 scoring, config, registry (2026-09-30)
- `config/style.json` holds phases, axes, KPIs (sign, unit, source, confidence), transform, min KPIs, the central-rectangle assumption and the external CSV path. `style.py` holds the KPI formulas (keyed by KPI id), z-scores, re-standardisation, Φ or percentile transform, competition ranking, composite indices (partial flag, none below 2 axes), the optional external CSV hook and the dashboard payload.
- `metrics.json` gains 49 entries (9 axes, 3 indices, 17 KPIs, plus section entries): unit `score100`, `higher_is_better: null`, `status` (proxy or external) and `confidence`. The glossary gets a "Style of play" group.
- `tests/test_style.py` (hand-computed fixtures): z-score mean 0 and unit sd, sign handling for Active and Tight, single-KPI axis is null, scores in (0, 100) and a mean club at 50, tie rule, composite and partial flags, box and central-rectangle boundaries, Liverpool reconciliation, 20 clubs per season, external CSV override / enable / reject / absent.
- All non-browser tests pass (88). Open: payload wiring and UI (S3 onward).

### Style of play: S3 and S4 axis rows, Defence, Attack (2026-09-30)
- Dashboard payload gains `style` (aggregates only: per season the 20 clubs' axis scores and ranks, Liverpool KPI tables, indices, per-axis series). Axis-row component: strip plot, Liverpool dot with score and rank, tick at 50, sparkline with manager-era bands and gaps, Proxy/External badge, confidence pip, KPI table (expander on Attack, beside each row on Defence), unavailable state "Needs pass-level data", two table alternatives. Neutral styling only.
- Defence: one full-width Defence style card after the map. Attack: Build-up and Attack cards side by side (equal height, stacked under 1100px) between the shot map and the lower row.
- New frontend tests (in test_frontend.py): sections per page, every score/rank/index/change equals the build JSON, badge and pip on every axis, tick at 50 and dot at the score, no good/bad colours or arrows in both themes, season/era updates, keyboard expanders, small-sample badge, table alternatives, screenshots in `artifacts/screenshots/style/`.
- Existing tests: two selectors/counts updated (touch-target scan excludes the tiny tooltip buttons, which have 44px hit areas; glossary sub-section count 7 to 8). No assertion on a value changed. Full suite 174 passed.

### Style of play: S5 Methodology, README, QA (2026-09-30)
- Methodology gains a "Style of play (proxy scores)" section: CIES credit (borrowed vs changed), formula, tie rule, relative-to-season-league explanation, central-rectangle assumption, confidence, unavailable axes, and a proxy mapping table generated from `config/style.json`. README section and external CSV documentation added (the external-model README content overwritten in S2 is restored).
- Tests: methodology section test; section-count assertion 12 to 13 (a new section is added). Screenshots for both themes at 1920, 1440 and 390 in `artifacts/screenshots/style/` were viewed; a mobile overflow in the Defence card and the Defence table layout were fixed.
- Size: payload `style` 50 KB, `style_raw.parquet` 80 KB (not shipped), dist 1.72 MB. Full suite 175 passed. Not merged: awaiting approval.
## Liverpool identity layer (2026-09-30, branch `claude/happy-pasteur-qhu6a2`)
Presentation on top of the redesign; no metric values, definitions or existing assertions changed.
- **Sidebar:** solid brand red with white text and a white active pill; faded "ANFIELD" lettering and half-pitch markings (pure CSS pseudo-elements, decorative, hidden under 860px where the nav becomes a top bar). The crest sits on a white plate so it stays visible on red.
- **Masthead:** the page header is a brand-red panel with a Kop-seat pattern (inline SVG background) and a white/red scarf stripe with fringe beneath it. Selects, theme button and the small-sample badge are restyled for the red ground.
- **Record card:** cream "away kit" surface (local token overrides, so every child follows), red points value. When Liverpool finished strictly top of a completed season (all managers selected) it gets a gold double-line trim and a "League champions" chip.
- **Data:** `build.league_payload` now adds `champion` per season: the strict points leader once every club has played 38, otherwise `null` (a tie is never called). Derived from `team_seasons`, no new source.
- **Tests added:** two unit tests for the champion rule (run and pass); three browser tests (trim only on champion seasons and never on "all seasons"; sidebar and masthead colours and the ANFIELD layer; cream record numbers meet 4.5:1 in both themes).
- **Not verified here:** the sandbox cannot reach Understat or football-data, so the real data build and the full existing suite were **not** run. The new browser tests pass against a synthetic single-page payload rendered from the real template (no console errors, no horizontal overflow at 390 px, screenshots checked in dark and light). Please run `python etl.py && python build.py && pytest` before merging; the layout tests that measure dead space and overflow are the ones most likely to notice the taller masthead (`margin-bottom` is now 56 px to make room for the scarf).

### Attack page: Build-up card removed (2026-10-01)
- Build-up has too little data to show (no pass data), so the Attack page now has only the Attack style card, full width under the shot map with each KPI table beside its row, the same layout and width as the Defence card. The expander mode and its CSS are gone. The build-up axes stay in the config, registry and Methodology table. Tests updated accordingly (180 passed).

## Multi-club: Liverpool, Arsenal, Chelsea, Manchester United, Manchester City (2026-10-08, branch `claude/determined-gates-b9u7yl`)
- **Built:** `config/clubs.json` + `config/eras/<club>.json`; ETL/metrics/build take a club; per-club tables in `data/processed/<slug>/`; one self-contained page per club (`dist/index.html` = Liverpool, `dist/<slug>/index.html`); per-club light/dark themes generated in `build.py` (neutrals re-tinted, text tokens nudged to 4.5:1), stadium lettering, original monogram badges (drop `assets/crests/<slug>.png` for official crests); crest is a button opening a keyboard-accessible switcher that carries page and season. CI: ETL and data tests once for all clubs, full browser suite per club (matrix), switcher job, deploy only if all pass.
- **Verified here (synthetic data only):** the sandbox cannot reach Understat, football-data or cdnjs, so `tests/fixtures/make_synthetic.py` generates a raw cache in the real response shapes (4 seasons, 20 teams). On it: ETL, builds for all five clubs, `test_clubs.py`, the per-club ETL/metrics/build tests (apart from assertions sized for 13 real seasons) and screenshots of each club in both themes.
- **NOT verified:** any real-data run; the full browser suite on every club (a Liverpool and Chelsea run on synthetic data was still in progress at commit time); a baseline comparison against the pre-change code was started to separate synthetic-data failures from regressions.
- **Open:** manager eras are from memory and unverified (see DATA_NOTES.md, esp. 2026 changes at Chelsea, Man United, Man City); first CI run fetches ~2,000 more files (~45 min).
