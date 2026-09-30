"""ETL: fetch + cache + clean + join -> data/processed/*.parquet.

M0: only the source adapters (fetch + cache) exist so far.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

try:  # use the OS trust store (corporate TLS inspection); verification stays ON
    import truststore
    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
UA = "Mozilla/5.0 (liverpool-tracker portfolio project; polite, cached, <=1 req/s)"
MIN_INTERVAL = 1.0  # seconds between requests to any source

_last_request = 0.0
_session = requests.Session()
_session.headers["User-Agent"] = UA


def _get(url: str, headers: dict | None = None) -> requests.Response:
    """Rate-limited GET (at most 1 request/second, process-wide)."""
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    resp = _session.get(url, headers=headers, timeout=60)
    _last_request = time.monotonic()
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------- Understat adapter
# Understat pages no longer embed JSON; the page JS calls XHR endpoints that
# require the X-Requested-With header. All Understat access lives here.
UNDERSTAT = "https://understat.com"


def _understat_json(path: str, cache_name: str, refresh: bool = False):
    cache = RAW / "understat" / cache_name
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    r = _get(f"{UNDERSTAT}/{path}", {"X-Requested-With": "XMLHttpRequest",
                                     "Referer": f"{UNDERSTAT}/"})
    data = r.json()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def understat_league(season_start: int, refresh: bool = False) -> dict:
    """{'teams','players','dates'} for an EPL season (season_start = 2024 for 2024/25)."""
    return _understat_json(f"getLeagueData/EPL/{season_start}", f"league_EPL_{season_start}.json", refresh)


def understat_match(match_id: int | str, refresh: bool = False) -> dict:
    """{'rosters','shots','tmpl'} for one match."""
    return _understat_json(f"getMatchData/{match_id}", f"match_{match_id}.json", refresh)


def understat_team(team: str, season_start: int, refresh: bool = False) -> dict:
    return _understat_json(f"getTeamData/{team}/{season_start}", f"team_{team}_{season_start}.json", refresh)


def understat_player(player_id: int | str, refresh: bool = False) -> dict:
    """{'groups','matches','shots','player'?}: per-player season groups + matches + shots."""
    return _understat_json(f"getPlayerData/{player_id}", f"player_{player_id}.json", refresh)


# ---------------------------------------------------------------- football-data adapter
def football_data_csv(season_start: int, refresh: bool = False) -> Path:
    code = f"{season_start % 100:02d}{(season_start + 1) % 100:02d}"
    cache = RAW / "football_data" / f"E0_{code}.csv"
    if cache.exists() and not refresh:
        return cache
    r = _get(f"https://www.football-data.co.uk/mmz4281/{code}/E0.csv")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return cache
