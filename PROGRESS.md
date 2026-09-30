# Progress

## M0 Recon: done (2026-09-30)
- **Built:** `etl.py` source adapters (cache + 1 req/s), `config/teams.json`, `config/eras.json`, `DATA_NOTES.md`, venv, `.gitignore`.
- **Fetched:** Understat league payloads 2014–2026, one match, one player, one team; football-data CSVs 2014–2026 (in `data/raw/`, gitignored).
- **Verified:** Understat serves JSON via XHR, not embedded; 38 Liverpool fixtures in all 13 seasons; 7 team-name mismatches mapped; Iraola appointment date confirmed.
- **Scope changes:** Pinnacle closing odds are missing for 17 Liverpool 2025/26 matches and all of 2026/27, so M1 uses a fallback chain (Pinnacle → market average → Betfair Exchange) with a per-match `market_source`. Average/Max closing columns don't exist before 2019/20, but Pinnacle covers those seasons.
- **Environment:** only Python 3.10 is installed locally (brief says 3.11+). Code avoids 3.11-only features; CI will use 3.11. Corporate TLS inspection is handled with `truststore` (certificate verification stays on).
- **Open:** Klopp exit and Slot start/end dates are seed values. M0 has no tests defined.
