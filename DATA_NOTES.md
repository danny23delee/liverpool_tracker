# Data notes (verified in M0, 2026-09-30)

## Understat (EPL): access
- League/team/match pages **no longer embed JSON** (only `match_info` / `player` stubs). Data comes from XHR endpoints
  called by the page JS (found in `js/league.min.js`, `match.min.js`, `team.min.js`, `player.min.js`).
  Requests need `X-Requested-With: XMLHttpRequest`; the response is JSON (served as text/javascript).
- Endpoints (all GET; the adapter lives in `etl.py`):

  | Endpoint | Returns |
  |---|---|
  | `/getLeagueData/EPL/{year}` | `teams` (id → {id, title, history[]}), `players` (season totals), `dates` (all fixtures) |
  | `/getMatchData/{match_id}` | `shots` {h[], a[]}, `rosters` {h{}, a{}} (player-match stats), `tmpl` (HTML, ignored) |
  | `/getTeamData/{team}/{year}` | `dates`, `players`, `statistics` (situation / formation / gameState / timing / shotZone / attackSpeed / result) |
  | `/getPlayerData/{player_id}` | `matches` (every player-match, all seasons), `groups`, `shots` (career EPL shots) |

- `year` is the season **start year** (2025 = 2025/26). Understat's 2026 = 2026/27 is live (5 Liverpool results by 2026-09-30).
- All numbers arrive as **strings** (except team `history`), so ETL must cast them. Datetimes are UTC (Man Utd v Fulham 2024-08-16 19:00 = 20:00 BST).
- Coordinates: `X`, `Y` in 0–1 from the shooting team's perspective (X→1 = opponent goal). `h_a` on a shot is the side of the *shooter*.

### Schemas
- **team history row** (per team-match): `h_a, xG, xGA, npxG, npxGA, ppda{att,def}, ppda_allowed{att,def}, deep, deep_allowed, scored, missed, xpts, result (w/d/l), date, wins, draws, loses, pts, npxGD`.
  It has no match id or opponent, so it is joined to `dates` on (date, side). PPDA = att/def.
- **dates row**: `id, isResult, h{id,title,short_title}, a{...}, goals{h,a} (null if unplayed), xG{h,a}, datetime, forecast{w,d,l}`. The Understat forecast is not used as "the market".
- **shot**: `id, minute, result (Goal|SavedShot|BlockedShot|MissedShots|ShotOnPost|OwnGoal), X, Y, xG, player, h_a, player_id, situation (OpenPlay|FromCorner|SetPiece|DirectFreekick|Penalty), season, shotType (RightFoot|LeftFoot|Head|OtherBodyPart), match_id, h_team, a_team, h_goals, a_goals, date, player_assisted, lastAction`.
- **roster row**: `id, goals, own_goals, shots, xG, time, player_id, team_id, position, player, h_a, yellow_card, red_card, roster_in, roster_out, key_passes, assists, xA, xGChain, xGBuildup, positionOrder`.
- **league player row**: `id, player_name, games, time, goals, xG, assists, xA, shots, key_passes, yellow_cards, red_cards, position, team_title, npg, npxG, xGChain, xGBuildup`.
- **Own goals** appear as shots with result `OwnGoal`. Which side they are attributed to must be checked in M1 against final scores (the ETL test requires goals from shots + own goals for = score).

### Request budget
Match payloads contain both teams' shots and rosters: 38 × 13 ≈ 490 requests for Liverpool matches (~8 minutes at 1 req/s, cached forever). League-wide team aggregates (league-average lines) come from `getLeagueData` history: 1 request per season.

## football-data.co.uk (`/mmz4281/{yyzz}/E0.csv`)
- CSV, UTF-8 with BOM (`utf-8-sig`). `Date` is `dd/mm/yyyy` (2-digit years in older files). 2014/15 has a trailing blank row (381 rows).
- Result columns: `FTHG, FTAG, FTR`. Odds columns vary by season (Liverpool rows):

  | Season | Pinnacle open `PS*` | Pinnacle close `PSC*` | Avg/Max close `AvgC*`/`MaxC*` | Betfair Exchange |
  |---|---|---|---|---|
  | 2014/15–2018/19 | yes | yes | no | no |
  | 2019/20–2023/24 | yes | yes | yes | no |
  | 2024/25 | yes | yes | yes | yes (`BFE*`, `BFEC*`; also `BF*`, `BFC*`) |
  | 2025/26 | 17 of 38 null | 17 of 38 null | yes | `BFEC*` (3 null); `BFD*` replaces `BF*` |
  | 2026/27 (live) | column dropped | column dropped | yes | `BFEC*` (`BFDC*`) |

- **Consequence:** Pinnacle closing is unavailable for 17 Liverpool 2025/26 matches and all of 2026/27. Fallback chain: Pinnacle close → market-average close (`AvgC*`) → Betfair Exchange close (`BFEC*`), with `market_source` recorded per match. Betfair Exchange columns are kept where present.

## Join
Understat `dates` (id, UTC datetime, teams) ↔ football-data (Date, HomeTeam, AwayTeam mapped via `teams.json`) on (date ±1 day, home, away).
Checked in M0: Understat lists 38 Liverpool fixtures in each of the 13 seasons (2014/15–2026/27) and football-data has 38 Liverpool rows in every completed season, 5 in 2026/27 (matching Understat's 5 results).

## Team names
- Understat, 2014–2026 (36 teams): Arsenal, Aston Villa, Bournemouth, Brentford, Brighton, Burnley, Cardiff, Chelsea, Coventry, Crystal Palace, Everton, Fulham, Huddersfield, Hull, Ipswich, Leeds, Leicester, Liverpool, Luton, Manchester City, Manchester United, Middlesbrough, Newcastle United, Norwich, Nottingham Forest, Queens Park Rangers, Sheffield United, Southampton, Stoke, Sunderland, Swansea, Tottenham, Watford, West Bromwich Albion, West Ham, Wolverhampton Wanderers.
- football-data differs only for: Man City, Man United, Newcastle, Nott'm Forest, QPR, West Brom, Wolves. All are mapped in `config/teams.json`.

## Manager eras
Rodgers → Klopp (2015-10-08) and Iraola's appointment (2026-06-04, reported by LFC, Irish Times, ESPN) were checked against news sources. Klopp's exit and Slot's start/end dates are seed values, checked for consistency only: no match falls between eras.

## Politeness
`etl._get` enforces 1 request/second process-wide. Every response is cached under `data/raw/` and never re-fetched unless `refresh=True`; completed seasons are never refreshed.

## Known source inconsistency: team xG vs shot xG (found in M1)
Understat's team-level match xG (`dates.xG` = team `history.xG`, identical) is **lower than the sum of the same match's shot xG** in 158 of 922 Liverpool team-matches (both teams). It is never higher (min diff -5e-6), the gap is up to 0.88 xG, and it adds 0.4-2.5% to a season total. It is not a single shot type or a duplicate (ids are unique, and removing one shot explains only 47 of the 158 cases). The brief's ±0.02 test cannot hold on real data.
Decision: `matches.xg` / `xga` = **sum of shot xG** (own goals excluded, xG 0). Every shot map, xG race chart and the xG simulation use shots, so the numbers on screen reconcile. Understat's figure is kept as `xg_reported` / `xga_reported`. `team_seasons` (league-wide, no shot data for other clubs' matches) uses the reported figures; league reference lines are therefore ~1-2% lower than Liverpool's shot-sum values, which the Methodology page will state. The ETL test asserts the shot sum is never below the reported value, within ±0.02 in ≥80% of team-matches, and within 3-4% per season.

## Own goals (verified in M1)
An own goal is a shot with `result = OwnGoal`, xG 0, `h_a` = side of the player who scored it. It is credited to the opposition: goals for a team = `Goal` shots by that team + `OwnGoal` shots by the other team. `shots.scoring_team` encodes this. Player-season `goals` never include own goals; roster `own_goals` counts them for the scorer.

## Processed tables (`data/processed/*.parquet`)
`matches` (Liverpool played matches, 461 rows: results, xG, PPDA/deep, odds sets, chosen closing price `mkt_*` + `mkt_source`), `fixtures` (all 494 Liverpool fixtures incl. unplayed), `team_matches` (all clubs, 9,220), `team_seasons` (260 team-seasons incl. shots by situation), `shots` (12,084, both teams in Liverpool matches), `rosters` (13,154 player-matches), `player_seasons` (Liverpool, 335, from rosters; npxG/npG from non-penalty shots).
Market source actually used: Pinnacle close for 2014/15-2024/25 (all 38) and 21 of 38 in 2025/26; market-average close for 17 in 2025/26 and all 5 played in 2026/27. Betfair Exchange columns are stored (`bfe_open_*`, `bfe_close_*`, present 2024/25 onward) but not needed as a fallback so far.

## External model hook (built in M2, scored in M6)
Drop a CSV at `data/external/model_probs.csv` (path in `config/settings.json`). Columns: `match_id` (Understat id) **or** `date` (yyyy-mm-dd, local match date) + `home_team` + `away_team` (canonical Understat names), plus `p_home`, `p_draw`, `p_away` (each in [0,1], row sums to 1 ± 1e-3). Rows that fail validation raise; matches with no row are simply not scored. `metrics.attach_external_model` maps probabilities to Liverpool orientation (`mo_w/d/l`), and `metrics.calibration` reports Brier/log loss for it next to market and xG-sim.

## Three different xG totals in Understat (found in M4)
For Liverpool 2023/24 the same season has three xG totals: 94.79 (team match history, `xg_reported`), 97.11 (sum of the match payload shots, used as `xg`) and 103.11 (getTeamData `statistics.situation`).
- The 6.0 gap between the last two is exactly the number of opponent own goals: the team statistics treat each own goal as a 1.0-xG "goal" shot for the beneficiary (and count it against the culprit). Own goals for a team = team goals − goals by the team's own players (team payload `players`). `team_seasons.shots_shotlevel` / `xg_shotlevel` remove them; the ETL test proves they equal Liverpool's summed shots exactly for all 13 seasons.
- League averages of "against" measures equal league averages of "for" measures (every shot is for one club and against another), so league xGA reference lines use the corrected "for" figures.
- The gap between the first two (team history vs shots) remains as documented in M1.

## Shot map conventions (M4)
Understat X/Y are 0-1 from the shooter's view, X→1 = the goal being attacked. Attack map: (105·X, 68·Y) on a 105×68 m pitch, attacking left to right, drawn from x = 36 m. Defence map: opposition shots are rotated 180° ((105·(1−X), 68·(1−Y))) so Liverpool always attack left to right and defend the left goal, drawn up to x = 69 m. Shots outside the drawn area (long shots) are not plotted but are counted in the totals, and the page says how many.


## Style of play feasibility (S0, 2026-09-30)

Goal: proxy "style of play" scores (3 phases × 3 tactical axes) for every club, relative to that season's league. The CIES Football Observatory method uses Impect event data (pressure locations, pass lengths, reception heights, possession phases), which is not freely available, so everything here is a **proxy**, never the CIES index.

### What was verified in the real data
- **Team page splits exist for all 260 club-seasons** (20 clubs × 13 seasons, 2014/15 to 2026/27) in the already-cached `getTeamData` payloads, so **no new requests are needed** (style adds 0 requests and 0 MB of raw data). Fields of `statistics`:
  - `shotZone`: `shotSixYardBox`, `shotPenaltyArea`, `shotOboxTotal`, `ownGoals`, each with `shots / goals / xG` for the club and `against` for opponents. Sums of the four buckets equal the club's total shots in 100% of club-seasons, for and against.
  - `attackSpeed`: `Fast`, `Normal`, `Standard`, `Slow`, same layout; sums equal total shots in 100% of club-seasons. (Own-goal pseudo-shots are included in the totals, in an unknown bucket: at most 1 shot in ~600, negligible for shares.)
  - `situation`, `gameState`, `timing`, `formation`, `result`: present but not used for style.
- **Team match history** (`getLeagueData`): `ppda{att,def}`, `deep`, `deep_allowed` per match, all seasons.
- **League player table** (`getLeagueData`): `xGChain`, `xGBuildup` per player, all seasons; summed per club (12 of 562 players in 2024 list two clubs and are skipped for the club sums).
- **Zone definitions differ slightly from a geometric box.** Liverpool 2024/25 against: Understat penalty area 214 / six-yard 31 / outside 141; from coordinates (penalty area 16.5 × 40.32 m, six-yard 5.5 × 18.32 m) 204 / 30 / 152 of 386 opposition shots. Totals reconcile exactly; zone counts agree within about 5%. Understat's own zone counts are used, because they are available for every club.
- **Not available league-wide:** shot type (headers), shot last action (crosses) and shot coordinates exist only in match or player payloads. League-wide they would need every match of every club (about 4,560 requests, ~76 minutes at 1 request/second, more than the CI job limit), so the **central rectangle of the own box** (defined in `config/style.json`: shooter-perspective x ≥ 0.843 and |y − 0.5| ≤ 0.13), the header share, the cross share, the central-lane share and the shot-dispersion KPIs are **not computed**. They are declared in the config as `external-only`, so real values can be supplied through `data/external/style_kpis.csv` without code changes.

### Axis status matrix
Sign: +1 means a higher KPI value pushes toward the right-hand label. "Computable" needs at least 2 verified KPIs.

| Phase | Axis (left ◄► right) | KPI | Source field | Verified | Seasons | Sign | Axis status | Confidence |
|---|---|---|---|---|---|---|---|---|
| Defence | Low ◄► High | deep completions allowed per match | history `deep_allowed` | yes | all 13 | −1 | **proxy** | low |
| | | share of shots conceded from fast attacks | `attackSpeed.Fast.against.shots` ÷ total | yes | all 13 | +1 | | |
| Defence | Passive ◄► Active | PPDA (passes allowed per defensive action) | history `ppda.att ÷ ppda.def` | yes | all 13 | −1 | **proxy** | medium |
| | | defensive actions in the opposition 40% per match | history `ppda.def` | yes | all 13 | +1 | | (the two share a source and are correlated) |
| Defence | Lenient ◄► Tight | in-box shots conceded per match | `shotZone` penalty area + six-yard `.against.shots` | yes | all 13 | −1 | **proxy** | medium |
| | | xGA from in-box shots per match | same zones `.against.xG` | yes | all 13 | −1 | | |
| | | shots conceded from the central rectangle | shot coordinates, league-wide | **no** (see above) | none | −1 | dropped | |
| Build-up | Long ◄► Short | none | | no honest proxy | | | **unavailable** | |
| Build-up | Vertical ◄► Horizontal | none | | no honest proxy | | | **unavailable** | |
| Build-up | Simple ◄► Elaborate | share of shots from slow attacks | `attackSpeed.Slow.shots` ÷ total | yes | all 13 | +1 | **proxy** | low |
| | | xGBuildup as a share of xGChain (club totals) | league players `xGBuildup ÷ xGChain` | yes | all 13 | +1 | | |
| Attack | Aerial ◄► Grounded | share of shots that are headers; share of open-play shots from a cross | shot type / last action, league-wide | **no** | none | −1, −1 | **unavailable** (external-only KPIs) | |
| Attack | Rapid ◄► Placed | share of shots from fast attacks | `attackSpeed.Fast.shots` ÷ total | yes | all 13 | −1 | **proxy** | medium |
| | | share of xG from fast attacks | `attackSpeed.Fast.xG` ÷ total xG | yes | all 13 | −1 | | |
| Attack | Scattered ◄► Grouped | share of shots taken inside the box | `shotZone` penalty area + six-yard ÷ non-own-goal shots | yes | all 13 | +1 | **proxy** | low |
| | | deep completions per shot | history `deep` ÷ shots | yes | all 13 | +1 | | |
| | | central-lane share; y dispersion of open-play shots | shot coordinates, league-wide | **no** | none | +1, −1 | dropped (external-only) | |

### Composite indices
- **Pressure index** (Defence): all three axes computable.
- **Occupation index** (Attack): 2 of 3 axes (Aerial missing), shown as **partial**.
- **Control index** (Build-up): only 1 of 3 axes computable, below the 2-axis minimum, so **no index**; the card says "Needs pass-level data".

Result of the S0 stop rule: Defence has 3 computable axes and Attack has 2, so the feature continues.

### Deviations from STYLE_OF_PLAY.md
- The Passive ◄► Active axis is given two KPIs (PPDA and defensive actions per match) instead of one, to respect the "at least 2 KPIs" rule. They are correlated (PPDA is the opponent's passes divided by the actions), so the confidence is capped at medium.
- The Lenient ◄► Tight axis uses two in-box KPIs; the central-rectangle KPI is dropped (needs league-wide coordinates).
- The Grouped axis replaces the two coordinate-based KPIs with in-box shot share.
