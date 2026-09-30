## What this dashboard is

This dashboard follows Liverpool's Premier League performance since 2014/15 and puts three things side by side: **what happened** (results), **what the chances said should have happened** (expected goals), and **what the betting market expected before kick-off** (closing odds). The gaps between those three are the interesting part: a team can win without creating much, lose while dominating, or beat a market that had priced it lower.

Two rules run through everything:

- **Actual and expected are never mixed.** Goals, points and wins, draws and losses are whole numbers from the results. xG, xGA and xPts are always labelled "expected" and shown to one decimal. Where a page shows a gap between the two, it says which is which.
- **Every number is computed once, in Python, and only formatted in the browser.** A large automated test suite checks the pipeline and the page against independent calculations. See [How accuracy is enforced](#accuracy).

## Data sources

{{coverage}}

**Politeness and reproducibility.** Every raw response is cached, requests are limited to one per second, completed seasons are never fetched again, and the whole site is rebuilt from the cache. There are no runtime calls from your browser to any data source.

**Attribution.** Shot-level expected goals, player and team match data: [Understat](https://understat.com). Results and betting odds: [football-data.co.uk](https://www.football-data.co.uk). Charts: [D3](https://d3js.org). This is an independent portfolio project, not affiliated with or endorsed by Liverpool FC, the Premier League, Understat or football-data.co.uk. FBref advanced statistics are not used (they are no longer available), and there are no defensive event statistics such as tackles or interceptions: the defensive picture comes from xGA, shots conceded, PPDA and deep completions allowed.

## How the two sources are joined

Understat and football-data name clubs differently (for example "Manchester United" and "Man United"), so a mapping table translates every name. Each Liverpool match is then matched on **home team, away team and date within one day** (Understat times are UTC, football-data dates are local). The build **fails outright** if any match joins zero times or more than once, or if the two sources disagree on the score. There is no silent fallback and no warning that can be ignored.

## Metric definitions

Every label, tooltip and number format on the site comes from one registry file, `metrics.json`, and this table is generated from it, so the definitions here cannot drift from what the charts show. "Better when" drives the colour of change arrows: for example, lower xGA is good and is coloured green.

{{registry_glossary}}

## Baselines and change

The vs-baseline panels compare the selection with a **baseline: the mean of per-match rates over the previous two Premier League seasons**. The panel always names the baseline it used. Rules that keep the arithmetic honest:

- The first season in the data (2014/15) has no baseline, and there is no baseline for a multi-season selection. Both show **N/A**, never "0%". Season 2015/16 has only one earlier season, so it uses that one and says so.
- For counts, rates and per-90 values the change is a percentage: `(current − baseline) ÷ baseline`. It is N/A if the baseline is missing, zero or negative.
- For percentages and probabilities the change is shown in **percentage points**, never a percentage of a percentage.
- Metrics that can be negative (goal difference, xG difference, "minus xG" gaps) show the **plain difference** instead of a percentage. A percentage of a negative or near-zero baseline produces impossible figures such as "−117%".
- The **arrow follows the sign** of the change. The **colour follows whether higher is better**: xGA falling is a green down-arrow.
- If a selection has fewer than 10 matches (or a player fewer than 450 minutes) a **small sample** badge appears and takeaways that would need statistical significance are suppressed.

## Expected goals and the exact simulation

Expected goals (xG) come from Understat's shot-level model: each shot gets the probability that it becomes a goal, based on its location, the type of chance and how it was created. The site never re-estimates xG; it uses Understat's value for every shot and adds them up. Own goals are not shots and carry no xG.

**xG-simulated result probabilities.** To turn the chances in a match into win, draw and loss probabilities, each shot is treated as an independent coin flip that lands with probability equal to its xG (penalties are ordinary shots with their own xG). Each team's goal count is then a Poisson-binomial distribution, and it is computed **exactly by convolution**, not by Monte Carlo sampling: start with the distribution `[1]` and, for each shot with probability `p`, combine `[1 − p, p]` into it. The two teams' distributions are multiplied together to give the probability of every scoreline, and summed to `P(win)`, `P(draw)` and `P(loss)`. Simulated **xPts = 3 × P(win) + P(draw)**.

{{sim_example}}

The simulation uses the shots that were actually taken, so it describes how good the chances were, after the fact. It is **not a pre-match forecast**, and that is why the calibration section treats it as a benchmark rather than a rival model. As a cross-check, season totals of simulated xPts sit within 0.7 points of Understat's own xPts in every season (the test allows 2.0).

## Turning odds into probabilities (de-vig)

Betting odds contain a margin (the "vig" or overround), so the implied probabilities `1 ÷ odds` add up to more than 100%. The market lens removes it with the **closing price** (the most informed price before kick-off). Two methods are implemented and both are shown on the match page:

- **Proportional** (the default): divide each implied probability by their sum, `p_i = (1 ÷ o_i) ÷ Σ(1 ÷ o_j)`. Simple, transparent, and spreads the margin evenly.
- **Shin (1993)**: assumes a share `z` of bettors are insiders and solves for the `z` that makes the implied probabilities sum to exactly 1. It takes proportionally more margin out of long shots than favourites, correcting the favourite-longshot bias. On low-margin books the two agree closely.

{{devig_example}}

**Price used.** Pinnacle's closing odds are the primary price. Where they are missing (as they are for part of 2025/26 and all of 2026/27 in the source), the market-average closing price is used instead, and the match page states which price it used. **Market-expected points = 3 × P(win) + P(draw)**, using the de-vigged closing probabilities.

## Market lens: how the comparisons work

- **Points: actual vs xPts vs market.** For each season: actual points, points expected from the chances (xG simulation) and points expected by the market. Differences are labelled "minus" gaps and coloured by sign.
- **Calibration.** *Brier score* is the mean over matches of the sum of squared errors of the three outcome probabilities (lower is better; always guessing one third scores 0.667). *Log loss* is the mean of `−ln(probability given to what actually happened)` (lower is better; one third scores 1.099). The *reliability plot* pools every (match, outcome) pair and compares forecast probability with how often the outcome occurred. A naive benchmark that always predicts the selection's own win, draw and loss rates is shown for scale. Because the xG simulation sees the match's shots, expect it to score better than the market: that is what "results follow chances" looks like, not a claim that it beats the market.
- **Mispriced runs.** Every window of 10 consecutive matches whose actual points differ from market-expected points by at least 4.0 is flagged; overlapping windows of the same sign are merged into one run ("beat" or "lagged" the market).
- **Flat 1-unit stake.** A hypothetical 1-unit bet on a Liverpool win in every match at the closing odds: profit is `odds − 1` on a win and `−1` otherwise, and ROI is profit divided by the number of stakes. **This is retrospective analysis of a public price series, not a strategy and not betting advice.** A season is only about 38 bets, so ROI swings widely on luck.

## Plugging in your own model

{{external_hook}}

## Takeaways

The takeaway sentences are generated by fixed rules with explicit thresholds; nothing is written by hand and every number in a sentence is computed from the data and re-checked by tests. If no rule fires, nothing is shown, and rules that need a meaningful sample switch off for small samples.

{{takeaway_rules}}

## Known limitations and data quirks

- **Three different xG totals exist for a season in Understat's own data**, and the site says which it uses. Team match totals, the sum of match shots and team season statistics can differ: team season statistics count opponent own goals as 1.0-xG "shots" (removed here), and Understat's team match xG is lower than the sum of that match's shots in {{xg_gap_share}} of team-matches, by up to {{xg_gap_max}} xG. This site uses the **sum of shots** everywhere so that every map, chart and total reconciles. League-wide reference lines for other clubs come from team-level figures with the own-goal artefact removed.
- **Own goals** are recorded on the side of the player who scored them and credited to the opposition; goals in the record include them, player goals never do.
- **xG is a model.** Understat's model does not know about goalkeepers, defenders' positions or player quality beyond the shot itself, so a finisher who beats xG for years is not necessarily lucky.
- **PPDA and deep completions** are Understat definitions. PPDA is a ratio of the opponent's passes to Liverpool's defensive actions in the attacking 40% of the pitch, so it varies widely from match to match; trend lines use a rolling 10-match average.
- **The market** is one price series. When Pinnacle's price is missing, a different bookmaker composite is used, and its margin is typically larger.
- **Small samples.** A single season is about 38 matches. The site flags samples under 10 matches and players under 450 minutes rather than hiding them.

## How accuracy is enforced {#accuracy}

The build and the deployed site are guarded by automated tests that must pass before anything is published; a failing run leaves the last good version live.

- **Pipeline tests**: every completed season has exactly 38 matches; every match joins once to odds and the scores agree; every club name maps; no duplicate ids; goals from shots plus own goals equal the final score in every match; player goals plus own goals equal team goals in every season; roster totals equal Understat's own season table.
- **Maths tests**: hand-computed fixtures; the exact xG convolution equals brute-force enumeration; de-vigged probabilities lie in (0, 1) and sum to 1; Shin and proportional agree on low-margin books; the change function returns N/A for missing baselines, points for probabilities, and the right colour for lower-is-better metrics; a regression test locks in that 13.3 against 12.2 is +9.0%.
- **Page tests** (a real browser): every page renders for every season with no console errors; after switching season no other season label appears on screen; displayed records equal the build data; every match page is checked against an independent recomputation of its xG, market probabilities and simulated probabilities; shot positions are compared circle by circle against the source coordinates.
