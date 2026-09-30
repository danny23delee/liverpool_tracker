"""ETL: fetch + cache + clean + join -> data/processed/*.parquet.

Run `python etl.py` (fetch, cache, build) or `python etl.py --no-fetch` (rebuild from cache).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
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


# ---------------------------------------------------------------- config / constants
PROCESSED = ROOT / "data" / "processed"
FIRST_SEASON = 2014
LIV_ID = "87"
LIV = "Liverpool"
TEAMS = json.loads((ROOT / "config" / "teams.json").read_text(encoding="utf-8"))
FD_TO_CANON = TEAMS["football_data_to_canonical"]


def season_label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def current_season_start(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def season_range() -> range:
    return range(FIRST_SEASON, current_season_start() + 1)


def _is_complete(league: dict) -> bool:
    return all(m["isResult"] for m in league["dates"])


# ---------------------------------------------------------------- fetch orchestration
def fetch_all() -> None:
    """Fetch (and cache) everything the build needs. Completed seasons are never re-fetched."""
    for s in season_range():
        cache = RAW / "understat" / f"league_EPL_{s}.json"
        refresh = False
        if cache.exists():
            refresh = not _is_complete(json.loads(cache.read_text(encoding="utf-8")))
        league = understat_league(s, refresh=refresh)
        complete = _is_complete(league)
        for m in league["dates"]:
            if LIV_ID in (m["h"]["id"], m["a"]["id"]) and m["isResult"]:
                understat_match(m["id"])  # a played match never changes: cached forever
        for t in league["teams"].values():  # team payloads: shots for/against by situation
            name = t["title"].replace(" ", "_")
            cache = RAW / "understat" / f"team_{name}_{s}.json"
            understat_team(name, s, refresh=(not complete and cache.exists()))
        football_data_csv(s, refresh=not complete)
        print(f"fetched {season_label(s)} (complete={complete})", flush=True)


# ---------------------------------------------------------------- build
def _f(x):
    return None if x is None else float(x)


def build_team_matches(leagues: dict[int, dict]) -> pd.DataFrame:
    """One row per team per played match (all 20 teams), from Understat team history + fixtures."""
    rows = []
    for s, lg in leagues.items():
        fixtures = {}
        for m in lg["dates"]:
            if m["isResult"]:
                fixtures[(m["h"]["id"], m["datetime"])] = (m, "h")
                fixtures[(m["a"]["id"], m["datetime"])] = (m, "a")
        for tid, t in lg["teams"].items():
            for h in t["history"]:
                m, side = fixtures[(tid, h["date"])]
                assert side == h["h_a"], (tid, h["date"])
                opp = m["a" if side == "h" else "h"]
                rows.append(dict(
                    match_id=int(m["id"]), season=season_label(s), season_start=s,
                    kickoff_utc=pd.Timestamp(h["date"]), team=t["title"], team_id=int(tid),
                    opponent=opp["title"], opponent_id=int(opp["id"]), side=side,
                    gf=int(h["scored"]), ga=int(h["missed"]),
                    xg=h["xG"], xga=h["xGA"], npxg=h["npxG"], npxga=h["npxGA"],
                    ppda_att=h["ppda"]["att"], ppda_def=h["ppda"]["def"],
                    ppda_allowed_att=h["ppda_allowed"]["att"], ppda_allowed_def=h["ppda_allowed"]["def"],
                    deep=h["deep"], deep_allowed=h["deep_allowed"],
                    xpts_us=h["xpts"], pts=h["pts"], result=h["result"].upper(),
                ))
    df = pd.DataFrame(rows).sort_values(["kickoff_utc", "match_id", "side"]).reset_index(drop=True)
    return df


def build_team_seasons(leagues: dict[int, dict], team_matches: pd.DataFrame) -> pd.DataFrame:
    """League-wide season aggregates: history sums + shots for/against by situation (getTeamData)."""
    g = team_matches.groupby(["season", "season_start", "team"], as_index=False).agg(
        matches=("match_id", "count"), gf=("gf", "sum"), ga=("ga", "sum"),
        xg=("xg", "sum"), xga=("xga", "sum"), npxg=("npxg", "sum"), npxga=("npxga", "sum"),
        ppda_att=("ppda_att", "sum"), ppda_def=("ppda_def", "sum"),
        ppda_allowed_att=("ppda_allowed_att", "sum"), ppda_allowed_def=("ppda_allowed_def", "sum"),
        deep=("deep", "sum"), deep_allowed=("deep_allowed", "sum"),
        xpts_us=("xpts_us", "sum"), pts=("pts", "sum"))
    extra = []
    for s, lg in leagues.items():
        for t in lg["teams"].values():
            f = RAW / "understat" / f"team_{t['title'].replace(' ', '_')}_{s}.json"
            payload = json.loads(f.read_text(encoding="utf-8"))
            stats = payload["statistics"]["situation"]
            row = dict(season_start=s, team=t["title"])
            # Understat's team statistics count opponent own goals as 1.0-xG "goal shots". Own goals
            # for = team goals - goals by the team's own players (own goals excluded from player goals).
            row["og_for"] = int(t_goals(lg, t["title"]) - sum(int(p["goals"]) for p in payload["players"]))
            for sit, v in stats.items():
                row[f"shots_{sit}"] = v["shots"]
                row[f"xg_{sit}"] = v["xG"]
                row[f"shots_against_{sit}"] = v["against"]["shots"]
                row[f"xga_{sit}"] = v["against"]["xG"]
            extra.append(row)
    ex = pd.DataFrame(extra).fillna(0)
    out = g.merge(ex, on=["season_start", "team"], how="left")
    sit_cols = [c for c in ex.columns if c.startswith("shots_") and not c.startswith("shots_against_")]
    out["shots"] = out[sit_cols].sum(axis=1)
    out["shots_against"] = out[[c for c in ex.columns if c.startswith("shots_against_")]].sum(axis=1)
    # shot-level 'for' figures with own-goal pseudo-shots (1.0 xG each) removed
    out["shots_shotlevel"] = out["shots"] - out["og_for"]
    out["xg_shotlevel"] = out[[c for c in ex.columns if c.startswith("xg_")]].sum(axis=1) - out["og_for"]
    return out


def t_goals(lg: dict, title: str) -> int:
    """Goals scored (incl. own goals) by a team over the played matches of a league payload."""
    tid = next(k for k, v in lg["teams"].items() if v["title"] == title)
    return sum(int(h["scored"]) for h in lg["teams"][tid]["history"])


def build_shots_rosters(leagues: dict[int, dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    shots, rosters = [], []
    for s, lg in leagues.items():
        for m in lg["dates"]:
            if not (m["isResult"] and LIV_ID in (m["h"]["id"], m["a"]["id"])):
                continue
            md = understat_match(m["id"])
            names = {"h": m["h"]["title"], "a": m["a"]["title"]}
            for side in "ha":
                for sh in md["shots"][side]:
                    team = names[sh["h_a"]]
                    scoring = team if sh["result"] != "OwnGoal" else names["a" if sh["h_a"] == "h" else "h"]
                    shots.append(dict(
                        shot_id=int(sh["id"]), match_id=int(m["id"]), season=season_label(s),
                        minute=int(sh["minute"]), result=sh["result"], x=float(sh["X"]), y=float(sh["Y"]),
                        xg=float(sh["xG"]), player=sh["player"], player_id=int(sh["player_id"]),
                        side=sh["h_a"], team=team, scoring_team=scoring, situation=sh["situation"],
                        shot_type=sh["shotType"], assisted_by=sh["player_assisted"],
                        last_action=sh["lastAction"]))
                for r in md["rosters"][side].values():
                    rosters.append(dict(
                        roster_id=int(r["id"]), match_id=int(m["id"]), season=season_label(s),
                        player_id=int(r["player_id"]), player=r["player"], team=names[side], side=side,
                        position=r["position"], minutes=int(r["time"]), goals=int(r["goals"]),
                        own_goals=int(r["own_goals"]), shots=int(r["shots"]), xg=float(r["xG"]),
                        assists=int(r["assists"]), xa=float(r["xA"]), key_passes=int(r["key_passes"]),
                        xgchain=float(r["xGChain"]), xgbuildup=float(r["xGBuildup"]),
                        yellow=int(r["yellow_card"]), red=int(r["red_card"])))
    return pd.DataFrame(shots), pd.DataFrame(rosters)


def build_player_seasons(rosters: pd.DataFrame, shots: pd.DataFrame) -> pd.DataFrame:
    """Liverpool player-season totals from player-match rosters (+ non-penalty split from shots)."""
    r = rosters[rosters.team == LIV]
    ps = r.groupby(["season", "player_id", "player"], as_index=False).agg(
        position=("position", lambda s: s[s != "Sub"].mode().iat[0] if (s != "Sub").any() else "Sub"),
        apps=("minutes", lambda s: int((s > 0).sum())), minutes=("minutes", "sum"),
        goals=("goals", "sum"), own_goals=("own_goals", "sum"), shots=("shots", "sum"),
        xg=("xg", "sum"), assists=("assists", "sum"), xa=("xa", "sum"), key_passes=("key_passes", "sum"),
        xgchain=("xgchain", "sum"), xgbuildup=("xgbuildup", "sum"))
    s = shots[(shots.team == LIV) & (shots.situation != "Penalty") & (shots.result != "OwnGoal")]
    np_ = s.groupby(["season", "player_id"], as_index=False).agg(
        npxg=("xg", "sum"), npg=("result", lambda x: int((x == "Goal").sum())))
    ps = ps.merge(np_, on=["season", "player_id"], how="left")
    ps[["npxg", "npg"]] = ps[["npxg", "npg"]].fillna(0)
    return ps


# ---- odds join
ODDS_SETS = {  # prefix -> (H, D, A) column names in football-data
    "ps_open": ("PSH", "PSD", "PSA"), "ps_close": ("PSCH", "PSCD", "PSCA"),
    "avg_close": ("AvgCH", "AvgCD", "AvgCA"), "max_close": ("MaxCH", "MaxCD", "MaxCA"),
    "bfe_open": ("BFEH", "BFED", "BFEA"), "bfe_close": ("BFECH", "BFECD", "BFECA"),
}
CLOSE_PRIORITY = [("ps_close", "pinnacle_close"), ("avg_close", "market_avg_close"),
                  ("bfe_close", "betfair_exchange_close")]


def load_football_data() -> pd.DataFrame:
    frames = []
    for s in season_range():
        df = pd.read_csv(football_data_csv(s), encoding="utf-8-sig")
        df = df.dropna(subset=["HomeTeam", "AwayTeam"]).copy()
        df["season_start"] = s
        df["fd_date"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed")
        df["home"] = df["HomeTeam"].map(lambda t: FD_TO_CANON.get(t, t))
        df["away"] = df["AwayTeam"].map(lambda t: FD_TO_CANON.get(t, t))
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def build_matches(team_matches: pd.DataFrame, shots: pd.DataFrame) -> pd.DataFrame:
    """Liverpool matches joined exactly once to football-data results and odds.

    xg/xga are the sums of the match's shot xG (what every shot map, xG race and the xG
    simulation use). Understat's own team-level figure is kept as xg_reported/xga_reported:
    it is lower than the shot sum in ~14% of team-matches (see DATA_NOTES.md).
    """
    fd = load_football_data()
    liv = team_matches[team_matches.team == LIV].copy()
    liv["home"] = liv.apply(lambda r: LIV if r.side == "h" else r.opponent, axis=1)
    liv["away"] = liv.apply(lambda r: r.opponent if r.side == "h" else LIV, axis=1)
    liv["date"] = liv.kickoff_utc.dt.normalize()
    fd_by_pair = {k: g for k, g in fd.groupby(["home", "away"])}
    out = []
    for _, r in liv.iterrows():
        cand = fd_by_pair.get((r.home, r.away))
        hit = cand[(cand.fd_date - r.date).abs() <= pd.Timedelta(days=1)] if cand is not None else None
        if hit is None or len(hit) != 1:
            raise ValueError(f"odds join failed for match {r.match_id} {r.home}-{r.away} {r.date.date()}: "
                             f"{0 if hit is None else len(hit)} candidates")
        f = hit.iloc[0]
        gh, ga = (r.gf, r.ga) if r.side == "h" else (r.ga, r.gf)
        if (int(f.FTHG), int(f.FTAG)) != (gh, ga):
            raise ValueError(f"score mismatch match {r.match_id}: understat {gh}-{ga} vs football-data {f.FTHG}-{f.FTAG}")
        row = r.to_dict()
        for pre, cols in ODDS_SETS.items():
            for k, c in zip("hda", cols):
                row[f"{pre}_{k}"] = pd.to_numeric(f.get(c), errors="coerce")
        row["fd_date"] = f.fd_date
        row["fd_row_key"] = f"{f.season_start}:{f.name}"
        # primary market price = first complete (H, D, A all > 1) set in priority order
        row["mkt_source"], row["mkt_h"], row["mkt_d"], row["mkt_a"] = None, None, None, None
        for pre, label in CLOSE_PRIORITY:
            trio = [row[f"{pre}_{k}"] for k in "hda"]
            if all(pd.notna(v) and v > 1 for v in trio):
                row["mkt_source"], (row["mkt_h"], row["mkt_d"], row["mkt_a"]) = label, trio
                break
        out.append(row)
    m = pd.DataFrame(out)
    sx = shots.groupby(["match_id", "team"]).xg.sum()
    m["xg_reported"], m["xga_reported"] = m["xg"], m["xga"]
    m["xg"] = [sx.get((r.match_id, LIV), 0.0) for r in m.itertuples()]
    m["xga"] = [sx.get((r.match_id, r.opponent), 0.0) for r in m.itertuples()]
    if m.fd_row_key.duplicated().any():
        raise ValueError("a football-data row joined to more than one match")
    m = m.rename(columns={"team": "club"}).drop(columns=["team_id", "opponent_id", "date"])
    m["is_home"] = m.side == "h"
    m["played"] = True
    return m.sort_values("kickoff_utc").reset_index(drop=True)


def build() -> dict[str, pd.DataFrame]:
    leagues = {s: understat_league(s) for s in season_range()}
    tm = build_team_matches(leagues)
    ts = build_team_seasons(leagues, tm)
    shots, rosters = build_shots_rosters(leagues)
    matches = build_matches(tm, shots)
    ps = build_player_seasons(rosters, shots)
    fixtures = pd.DataFrame([
        dict(match_id=int(m["id"]), season=season_label(s), kickoff_utc=pd.Timestamp(m["datetime"]),
             home=m["h"]["title"], away=m["a"]["title"], played=bool(m["isResult"]))
        for s, lg in leagues.items() for m in lg["dates"] if LIV_ID in (m["h"]["id"], m["a"]["id"])])
    tables = dict(matches=matches, fixtures=fixtures, team_matches=tm, team_seasons=ts,
                  shots=shots, rosters=rosters, player_seasons=ps)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(PROCESSED / f"{name}.parquet", index=False)
        print(f"{name}: {len(df)} rows", flush=True)
    return tables


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="rebuild from cache only")
    args = ap.parse_args()
    if not args.no_fetch:
        fetch_all()
    build()
