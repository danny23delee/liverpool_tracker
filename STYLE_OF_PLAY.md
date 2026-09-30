# Feature: Style of play (build-up, attack, defence)

Read this whole file, then work on a new branch `style-of-play`. If the redesign branch has been merged to main, branch from main. If it hasn't, branch from `redesign`, and use its theme tokens and components (see `REDESIGN.md`). Do not merge until I approve.

**Goal:** show how Liverpool play, not just how well, across seasons and manager eras.
- **Attack page:** gets the **Build-up** and **Attack** phases.
- **Defence page:** gets the **Defence** phase.
- Everything honours the existing season and manager-era filters.

**Scope:** additive only. Do not change existing metric values, takeaways, or the assertions in existing tests. If a selector breaks, update the selector, never the assertion.

---

## 0. The methodology, and what we can honestly measure

The inspiration is the CIES Football Observatory's "Style of play of teams" method. It describes 3 phases × 3 tactical options, each scored 0–100 from 3 KPIs, plus composite indices. Their KPIs come from **Impect event data** (pressure locations, pass lengths, pass reception height, possession phases). That data is not freely available: Impect's open data covers only Bundesliga 2023/24, and the customer API needs paid credentials. Our sources are Understat (shot-level xG with coordinates, team match history including PPDA and deep completions, player xGChain/xGBuildup, and possibly attack-speed and shot-zone splits) and football-data.co.uk.

So this feature builds **proxy style scores**, clearly labelled, never presented as the CIES index. Most of the defence and attack axes can be proxied reasonably. **Build-up is the weak phase**, because Understat has no pass data. Every axis carries a status and a confidence in config, and the UI shows them. An axis that can't be computed from at least 2 KPIs shows "Needs pass-level data", never a made-up number.

Credit the CIES methodology in the Methodology page (name, what we borrowed, what we changed). Do not copy their text beyond short factual descriptions, and never call our output "CIES scores".

### The 9 axes (right-hand label = the 100 end of the scale)

| Phase | Axis (left ◄ ► right) | Composite index when all 3 axes exist |
|---|---|---|
| Defence | Low ◄► High · Passive ◄► Active · Lenient ◄► Tight | **Pressure index** |
| Build-up | Long ◄► Short · Vertical ◄► Horizontal · Simple ◄► Elaborate | **Control index** |
| Attack | Aerial ◄► Grounded · Rapid ◄► Placed · Scattered ◄► Grouped | **Occupation index** |

Each composite index is the mean of its phase's three axis scores. If an axis is unavailable, the index is shown only when ≥2 axes exist, and is labelled "partial". Do **not** build the "game dictating" index (it needs all three phases at adequate confidence).

**Style is not good or bad.** None of these components may use the good/bad colours or arrows. They use neutral styling (Liverpool red for Liverpool, grey for league context).

---

## 1. Step S0: recon (write the results to `DATA_NOTES.md`, section "Style of play feasibility")

Verify against the real data, don't trust this file's field names. For each axis, produce a table: candidate KPI, source field, verified available (yes/no), seasons covered, sign (+1 means a higher value pushes toward the right-hand label), confidence (high/medium/low). Drop or replace any candidate below that doesn't hold up.

**Candidates to check**

| Axis | Candidate KPIs (proxies) | Sign | Expected confidence |
|---|---|---|---|
| Low ◄► High | deep completions allowed per match; share of shots conceded from fast attacks (if attack-speed splits exist) | −, + | medium–low |
| Passive ◄► Active | PPDA (passes allowed per defensive action; lower = more active) | − | medium–high |
| Lenient ◄► Tight | shots conceded from the central rectangle of the own box per match; in-box shots conceded per match; xGA from in-box shots per match | −, −, − | medium–high |
| Long ◄► Short | none directly. Only if ≥2 honest proxies are found | — | unavailable |
| Vertical ◄► Horizontal | none directly. Only if ≥2 honest proxies are found | — | unavailable |
| Simple ◄► Elaborate | share of Slow (vs Fast) attacks in shots/xG; team xGBuildup as a share of xGChain | +, + | low |
| Aerial ◄► Grounded | share of shots that are headers; share of open-play shots whose last action was a cross | −, − | medium |
| Rapid ◄► Placed | share of shots and xG from fast attacks (attack-speed or fast-break situation) | − | medium–low |
| Scattered ◄► Grouped | share of open-play shots from the central lane; deep completions per shot; horizontal dispersion of open-play shots (std dev of y) | +, +, − | low |

**Central rectangle of the own box:** define it in `config/style.json` as a documented assumption, e.g. shooter-perspective x ≥ 0.843 (16.5m of 105m) and |y − 0.5| ≤ 0.13, and make it configurable. Write the definition on the Methodology page.

**Data volume:** all league-wide scoring needs all 20 clubs per season. Find the cheapest verified route: league-page team data (one request per season) and team-page splits (one per club-season) are preferred over fetching every match's shots (about 380 requests per season). If per-match league-wide shots are required, fetch once, cache in `data/raw/`, respect the 1 request per second limit, and never re-fetch completed seasons. Only aggregated style values go into the dashboard payload, never league-wide shot lists. Keep the file under 5 MB. CI must not depend on live Understat access: commit whatever processed snapshot the workflow needs (same fallback as the existing deploy).

**Stop and report after S0** if fewer than 2 axes per phase in Defence and Attack are computable. Otherwise continue.

---

## 2. Scoring (implement in a new `style.py`; config in `config/style.json`)

For each season, using **per-match rates** so partial seasons work:
1. For each KPI, compute each club's value, then the league mean and population standard deviation across the clubs in that season. Get `z = (value − mean) / sd`.
2. Axis raw = mean of `sign × z` over the KPIs available for that club-season. An axis needs ≥ 2 KPIs; otherwise it is `null`.
3. Re-standardise the axis raw across the league to unit variance (averaging shrinks the spread).
4. **Score = 100 × Φ(axis z)** (normal CDF), so 50 = league average and 100 = the right-hand end. Also compute the **rank** among the clubs (1 = furthest toward the right-hand label).
5. Composite index = mean of the available axis scores (see §0).

Keep the transform (Φ vs percentile rank), sign, KPI list, status and confidence all in config, not in code. `null` propagates as N/A and never becomes 0. Scores are relative to that season's league, so cross-season movement means *relative* style change. Say so on the Methodology page and in the tooltip.

**Small samples:** if a season has fewer than 10 matches, show the existing "small sample" badge.

**External data hook (optional, build it but don't require it):** if `data/external/style_kpis.csv` exists with columns `season,club,kpi_id,value`, use those values for the matching KPI ids (an entry in config can point at the external KPI id), and label the axis "External data" instead of "Proxy". This lets real pass-level data (e.g. from a paid provider) replace weak proxies later without code changes. Include a documented sample row in the README and handle absence silently.

**Registry:** add each axis score, index, and KPI to `metrics.json`: `id, label, description, unit ("score100"), higher_is_better: null, format, min_sample, source, status (proxy|external), confidence`. The frontend reads labels, tooltips and badges from there.

---

## 3. UI

Use the current theme tokens, typography and spacing (Liverpool red for the Liverpool marker, grey for other clubs, muted text for labels). Reuse existing card, badge and tooltip components.

**Axis row** (one per axis, the same component everywhere)
- Left label and right label at the ends of a 0–100 track, a tick at 50 labelled "league avg".
- **Strip plot:** all clubs that season as small grey dots on the track (hover shows the club name and score). Liverpool is a large red dot with the score and rank ("Short 71 · 4th of 20").
- **Trend:** a small sparkline to the right of the track showing Liverpool's score across all seasons (or the selected era's seasons), with a dot on the selected season and manager-era background bands. Gaps stay gaps.
- Badge: "Proxy" / "External data", and a confidence pip (high/medium/low) with a tooltip.
- Expandable "How this is measured": lists the KPIs with Liverpool's value, the league average and the rank (sign and unit from config).
- **Unavailable axis:** greyed row reading "Needs pass-level data", with a short tooltip explaining why, and no track.
- Keyboard accessible (expanders are real buttons), 44px minimum targets, and a text/table alternative ("View as a table") like the other charts.

**Composite index:** a large number (54px condensed) with the index name, "partial" if applicable, and a change vs the previous season in neutral styling (points, e.g. "+6 pts"). No good/bad arrows.

**Attack page:** add a full-width section **after the shot map card and before the existing lower row**, containing two equal-height cards side by side: **Build-up** (Control index + 3 axis rows) and **Attack** (Occupation index + 3 axis rows). Below 1100px they stack. Keep the page free of dead space under the existing rule (empty space at the bottom of any card < 48px).

**Defence page:** add one full-width card **Defence** (Pressure index + 3 axis rows laid out with the KPI table visible beside each row where width allows). Reflow the remaining cards so no empty band appears.

**Methodology page:** add a section covering the borrowed framework and credit, the proxy mapping table (from S0, with confidence), the scoring formula, the central-rectangle assumption, why scores are relative to each season's league, and which axes are unavailable and why.

Nothing on Overview changes.

---

## 4. Tests

**Unit (`test_style.py`, hand-computed fixtures)**
- z-scores within a league-season average 0 (±1e-9) with unit population sd.
- Sign handling: a synthetic club with the lowest PPDA scores highest on "Active"; with the highest central-box shots conceded it scores lowest on "Tight".
- An axis with only one available KPI is `null`; `null` never renders as 0.
- Scores lie in (0, 100); a club at the league mean scores 50 (±0.5); ranks are 1..20 with no ties broken arbitrarily across identical values (document the tie rule).
- Composite index equals the mean of the available axis scores; partial flag set correctly; no index when <2 axes.
- Central-rectangle classifier: points just inside and just outside each boundary; zone counts sum to total shots.
- Reconciliation: Liverpool's shot-derived conceded counts equal the existing Liverpool shots dataset for every season, and in-box ≤ total.
- Every completed season has exactly 20 clubs with style values per available axis; the partial current season is computed on matches played.
- External hook: a sample CSV overrides the matching KPI and flips the badge to "External data"; absence causes no error.

**Frontend (Playwright, both themes, 1920 and 1440 wide, plus 390)**
- Attack page shows Build-up and Attack sections; Defence page shows the Defence section; Overview unchanged.
- Every rendered axis score matches the build JSON; unavailable axes show the "Needs pass-level data" state with no track.
- Proxy badge and confidence pip appear on every non-external axis; the league-average tick is at 50.
- **No good/bad colours or arrows** are used anywhere in the style components (assert on computed colours and the absence of delta arrows).
- Changing season or era updates the markers, the sparkline dot and the strip plot; no visible text shows a stale season label.
- Dead-space rule passes on Attack and Defence; zero console errors; keyboard: expanders toggle with Enter/Space.
- Contrast passes for all new text/background pairs in both themes.
- Screenshots at 1920×1080, 1440×900 and 390×844 into `artifacts/screenshots/style/`. **Look at them yourself** and fix overlap, clipping and gaps before reporting.

---

## 5. Process

Commit after each step with tests green; add a short entry to `PROGRESS.md` each time.
1. **S0:** recon and feasibility table, then report the axis status matrix to me.
2. **S1:** ETL additions and cached league-wide data, with their tests.
3. **S2:** `style.py`, config, registry entries, unit tests.
4. **S3:** axis-row component and the Defence section.
5. **S4:** Attack page Build-up and Attack sections.
6. **S5:** Methodology page, README update, responsive pass, final QA.

**Report back with:** the final axis status matrix (computable / proxy / unavailable, with confidence), total new data size, anything that deviates from this spec and why, and before/after screenshots. Do not merge until I approve.
