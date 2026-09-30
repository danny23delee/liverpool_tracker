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
