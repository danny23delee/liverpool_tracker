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
