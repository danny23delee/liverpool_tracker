# Liverpool FC Performance Tracker: Build Brief

You are building a public portfolio project: an interactive dashboard of Liverpool FC's Premier League team and player performance across multiple seasons. It includes a "market lens" that compares betting-market expectations with xG-based performance and actual results.

The owner is a data analyst (Python, SQL, pandas, strong on exchange/betting mechanics). He wants working output, minimal architecture, and numbers that are **provably correct**. Accuracy is the headline feature. A reference dashboard this is inspired by had fractional "goals", impossible percentage changes ("-117%"), inverted good/bad colouring, stale season labels and canned narrative text that contradicted results. Every one of those is a test case below.

---

## 1. Hard requirements

- **Deliverable:** one self-contained `dist/index.html` (HTML + CSS + JS + data inlined as JSON) deployed to GitHub Pages. It needs no backend and no runtime API calls.
- **Frontend:** vanilla JS + D3 v7 (pinned version, from cdnjs). No framework and no npm build chain. Google Fonts are allowed, with fallback stacks.
- **Pipeline:** Python 3.11+, pandas, requests, pytest, Playwright (python). Keep the module count low. Prefer a few substantial files over a package tree:
  ```
  etl.py            # fetch + cache + clean + join -> data/processed/*.parquet
  metrics.py        # all derived metrics, baselines, xG sims, market probs
  build.py          # processed data -> dashboard JSON -> inline into template -> dist/index.html
  metrics.json      # metric registry (see §4)
  config/eras.json  # manager eras by date
  config/teams.json # team-name mapping across sources
  template/index.html
  tests/            # test_etl.py, test_metrics.py, test_frontend.py, fixtures/
  data/raw/         # cached raw responses (gitignored)
  data/processed/   # parquet (gitignored)
  dist/             # build output
  DATA_NOTES.md PROGRESS.md README.md
  ```
- **No club crests, player photos or other licensed imagery.** Use initials avatars and your own visual identity. Liverpool red (#C8102E) as an accent colour is fine.
- **Attribution footer** naming each data source. Be polite to sources: cache every raw response, send at most 1 request per second, and never re-fetch completed seasons.

---

## 2. Data sources

Do **not** assume field names or access methods from memory. Verify them in M0 and record the real schemas in `DATA_NOTES.md`.

| Source | Use | Notes |
|---|---|---|
| **Understat** (EPL, 2014/15 → current) | Team match history (xG, xGA, npxG, xPts, PPDA, deep completions), match shots with x/y coordinates, situation, shot type and result, player-season and player-match stats (minutes, goals, xG, xA, key passes, xGChain, xGBuildup) | Confirm how data is currently served (embedded JSON in page scripts vs XHR endpoints) and adapt. Put all access in one adapter section of `etl.py`. |
| **football-data.co.uk** (`E0.csv` per season) | Results and odds: opening/closing Pinnacle, market average and max, and Betfair Exchange columns where present | Primary market price = Pinnacle closing. Fallback = market-average closing. Record which was used per match. Check for and keep Betfair Exchange columns if present. |

FBref advanced stats are gone (Opta pulled them in Jan 2026), so do not use FBref. Leave out defensive event stats (interceptions, tackles, recoveries). Use the sources above only. The defensive picture comes from xGA, shots conceded, PPDA and deep completions allowed.

**Joins:** Understat ↔ football-data on (date ±1 day, home team, away team) via `config/teams.json`. Every Liverpool match must join exactly once. Unmatched rows are a hard failure, not a warning.

**Scope:** Liverpool EPL matches, 2014/15 → current season. Opponent data is needed only where a view uses it (shots conceded, match explorer). League-wide team season aggregates are needed for league-average reference lines.

---

## 3. Pages

Use hash routing with shareable state, e.g. `#/attack?season=2025-26&era=all`. A global season selector and a manager-era filter drive every page. Every page title and subtitle must be generated from the current selection.

1. **Overview:** a record card with actual W/D/L, points and goals (integers) shown next to xPts and xG (clearly labelled as expected). A rolling 10-match xG difference line. The last five results. Rule-based takeaways (§5). A "vs baseline" panel.
2. **Attack:** shot map (filters: player, situation, body part, outcome; circle size = xG). Goals minus xG by player. Shot volume vs xG/shot scatter with league teams as context. Threat source mix (open play / set piece / penalty). Per-match xG trend.
3. **Defence:** shots-conceded map. xGA trend. Open-play vs set-piece xGA. PPDA trend. Deep completions allowed. Clean sheets.
4. **Players:** a searchable squad table (per-90 values, minimum-minutes filter), player profile with season-over-season trends, and a two-player comparison. Role leaders based only on available data: Finisher (npG − npxG), Shot Threat (npxG/90), Creator (xA/90), Build-up (xGBuildup/90), Involvement (xGChain/90).
5. **Match Explorer:** pick any match to see the xG race chart (cumulative xG over minutes), both teams' shot maps, the de-vigged pre-match market probabilities and the xG-simulated outcome probabilities.
6. **Market Lens** (the differentiator):
   - Per match: market-implied Liverpool win/draw/loss probabilities (closing price, overround removed), xG-simulated probabilities, and the actual result.
   - Per season: actual points vs xPts (xG-simulated) vs market-expected points.
   - Calibration: Brier score and log loss of market vs xG-sim for Liverpool matches, plus a reliability plot.
   - "Mispriced" runs: stretches where results beat or lagged market expectation.
   - A hypothetical flat 1-unit stake on Liverpool at closing odds, showing cumulative P&L and ROI by season. Label it as retrospective analysis, not a strategy.
   - Leave a documented hook for plugging in an external model's probabilities later (CSV with match key + H/D/A probs). If present, it is scored alongside market and xG-sim.
7. **Methodology:** generated from `metrics.json` and the notes. Covers sources, definitions, baseline logic, the de-vig method and the xG simulation. Recruiters read this page, so write it well.

Every metric shows an ⓘ tooltip whose text comes from its registry definition.

---

## 4. Metric registry & calculation rules

`metrics.json` is the single source of truth. Each entry has: `id, label, description, unit (count|rate|pct|per90|prob|odds), higher_is_better (true|false|null), format, min_sample, source`. The frontend reads labels, formats, tooltips and colouring from it. Do not hard-code any of these anywhere else.

Rules:
- **Actuals vs expected are never mixed.** Goals, points and W/D/L are integers from results. xG, xGA and xPts are always labelled "expected".
- **Change vs baseline:**
  - `count`/`rate`/`per90`: % change = (current − baseline) / baseline. It is `null` if the baseline is null or 0.
  - `pct`/`prob`: show percentage-point difference, never % of a %.
  - `null` renders as "N/A". It never renders as "0%".
  - Arrow direction = sign of the change. Colour = good/bad from `higher_is_better` (xGA down → green).
- **Baseline:** the mean of per-match rates over the previous 2 EPL seasons (configurable). The first season has no baseline. The UI always states what the baseline is.
- **Small samples:** if the selection has fewer than `min_sample` matches (default 10), show a visible "small sample" badge and suppress takeaways that need significance.
- **xG simulation:** treat each shot as an independent Bernoulli trial with p = xG. Compute each team's goal distribution by convolution (exact, not Monte Carlo). Derive P(W/D/L) and xPts. Exclude own goals. Handle penalties as ordinary shots with their own xG.
- **De-vig:** proportional normalisation by default. Also implement the Shin method and show both on the Methodology page. The market lens uses the configured default.
- **Market-expected points** = 3·P(win) + 1·P(draw) using de-vigged closing probabilities.

---

## 5. Takeaways

Generate these in `metrics.py` from explicit rules, each with a threshold and the numbers it cites. Examples: "Finishing 4.2 goals above xG", "xGA per match lowest of any season since 2019/20", "Results trailing the market by 3.1 points". Every number in the text must be computed. Sentiment has to agree with the data: never claim positive form if points from the last five < 7. If no rule fires, show nothing rather than filler.

---

## 6. Tests (must all pass before any milestone is marked done)

**ETL (`test_etl.py`)**
- Every completed Liverpool EPL season has exactly 38 matches. The current season count equals matches played to date.
- Each Liverpool match joins exactly once to odds. Every team name in both sources maps via `teams.json`.
- For each match: sum of shot xG equals team match xG (±0.02), and goals from shots + own goals for equals final score.
- Sum of player goals + own goals for equals team goals per season.
- No duplicate match or shot IDs.

**Metrics (`test_metrics.py`)** with small hand-computed fixtures
- W + D + L = matches. Points = 3W + D. All goal and point actuals are integers.
- The change function returns: null for a null or zero baseline, pp for pct metrics, and correct good/bad colour for a lower-is-better metric (xGA down → good).
- The % change for 13.3 vs a 12.2 baseline is +9.0% (regression test for the reference dashboard's bug).
- De-vigged probabilities are each in (0, 1) and sum to 1 (±1e-9). Shin and proportional agree within tolerance on low-margin books.
- The xG convolution matches brute-force enumeration on a 3-shot fixture. Season xPts from the simulation are within ±2.0 of Understat's xPts for every season (cross-check).
- Takeaway rules: form statements are consistent with the last-five points. Each cited number matches the data.

**Frontend (`test_frontend.py`, Playwright on `dist/index.html`)**
- Every page × every season renders with zero console errors.
- After switching season, no visible text contains a season label other than the selected one (the baseline label excepted).
- Displayed record values match the build JSON.
- Seasons below the sample threshold show the small-sample badge.
- Take screenshots of every page at 1440×900 and 390×844 into `artifacts/screenshots/`. **Look at them yourself** and fix overlap, clipping, unreadable labels and empty panels before marking the milestone done.

---

## 7. Design

Use the reference layout as inspiration only (a left nav, a card grid, a season selector top right) and give this project its own identity. Build light and dark themes from CSS custom properties. Make it responsive down to 390px wide. Wide charts scroll inside their own container. Pitch drawings use correct proportions (105×68) and attack left→right consistently. Understat coordinates are 0–1 normalised from the shooting team's perspective, so transform opponent shots for the defence maps. Keep the page fast: the whole file should stay under 5 MB.

---

## 8. Milestones

Work in order. After each milestone: run all tests, commit, and add a short entry to `PROGRESS.md` (what was built, what's verified, open issues). Do not start the next milestone with failing tests. If a data reality forces a scope change, write it in `PROGRESS.md` and pick the most sensible option rather than stopping.

- **M0 Recon:** fetch one season from each source into `data/raw/`. Document the real schemas, access methods, team-name lists and odds columns in `DATA_NOTES.md`. Draft `teams.json` and `eras.json`.
- **M1 ETL:** all seasons, cached, cleaned and joined to parquet. ETL tests pass.
- **M2 Metrics:** registry, baselines, xG simulation, de-vig, market points, takeaways. Metric tests pass.
- **M3 Shell + Overview:** template, routing, filters, themes, tooltips, build inlining, Overview page. First Playwright pass with screenshots reviewed.
- **M4 Attack + Defence pages.**
- **M5 Players + Match Explorer.**
- **M6 Market Lens + Methodology.**
- **M7 Polish:** a mobile pass, empty states, loading and performance work, final visual QA from screenshots.
- **M8 Ship:** GitHub Actions workflow on a weekly cron (Tuesday 06:00 UTC) plus manual dispatch. It runs ETL → tests → build → deploy to Pages. **If tests fail, it doesn't deploy**, so the last good build stays live. Write the README as a portfolio write-up: the problem, sources, methodology highlights, screenshots, and how accuracy is enforced.

---

## 9. Config seeds

`config/eras.json`: date-keyed and inclusive. Verify the exact dates in M0.
```json
[
  {"manager": "Brendan Rodgers", "from": "2014-08-01", "to": "2015-10-04"},
  {"manager": "Jürgen Klopp",    "from": "2015-10-08", "to": "2024-05-31"},
  {"manager": "Arne Slot",       "from": "2024-06-01", "to": "2026-05-30"},
  {"manager": "Andoni Iraola",   "from": "2026-06-04", "to": null}
]
```
