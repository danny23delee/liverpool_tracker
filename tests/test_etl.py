"""ETL tests: run against data/processed/*.parquet built by `python etl.py`."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl  # noqa: E402


@pytest.fixture(scope="session")
def t():
    names = ["matches", "fixtures", "team_matches", "team_seasons", "shots", "rosters", "player_seasons"]
    missing = [n for n in names if not (etl.PROCESSED / f"{n}.parquet").exists()]
    if missing:
        pytest.fail(f"missing processed tables {missing}: run `python etl.py` first")
    return {n: pd.read_parquet(etl.PROCESSED / f"{n}.parquet") for n in names}


def test_season_match_counts(t):
    m, fx = t["matches"], t["fixtures"]
    counts = m.groupby("season").size()
    cur = etl.season_label(etl.current_season_start())
    for season, n in counts.items():
        if season != cur:
            assert n == 38, f"{season}: {n} matches"
    played_now = int(fx[(fx.season == cur) & fx.played].shape[0])
    assert counts[cur] == played_now
    # every scheduled season is present
    assert set(counts.index) == {etl.season_label(s) for s in etl.season_range()}


def test_completed_seasons_all_played(t):
    fx = t["fixtures"]
    cur = etl.season_label(etl.current_season_start())
    done = fx[fx.season != cur]
    assert done.played.all() and (done.groupby("season").size() == 38).all()


def test_each_match_joins_once_to_odds(t):
    m = t["matches"]
    assert m.fd_row_key.notna().all() and not m.fd_row_key.duplicated().any()
    assert not m.match_id.duplicated().any()
    # a usable closing price exists for every match, and the source is recorded
    assert m.mkt_source.notna().all()
    assert (m[["mkt_h", "mkt_d", "mkt_a"]] > 1).all().all()
    assert set(m.mkt_source) <= {"pinnacle_close", "market_avg_close", "betfair_exchange_close"}
    # date within +-1 day and score agrees with football-data (also enforced hard in the build)
    assert ((m.fd_date - m.kickoff_utc.dt.normalize()).abs() <= pd.Timedelta(days=1)).all()


def test_team_names_map_across_sources():
    us = set()
    for s in etl.season_range():
        us |= {x["title"] for x in etl.understat_league(s)["teams"].values()}
    fd = etl.load_football_data()
    fd_names = set(fd.home) | set(fd.away)  # already mapped through teams.json
    assert fd_names <= us, f"football-data names not mapped: {sorted(fd_names - us)}"
    raw_names = set(fd.HomeTeam) | set(fd.AwayTeam)
    unmapped = {n for n in raw_names if n not in us and n not in etl.FD_TO_CANON}
    assert not unmapped
    assert set(etl.FD_TO_CANON.values()) <= us


def test_shot_xg_sums_match_team_xg(t):
    """matches.xg is the shot-xG sum by construction (exact). Understat's own team-level figure
    (xg_reported) is a documented, one-sided inconsistency: it is never above the shot sum, and
    within +-0.02 in the large majority of team-matches. See DATA_NOTES.md."""
    m, s = t["matches"], t["shots"]
    sx = s.groupby(["match_id", "team"]).xg.sum()
    for r in m.itertuples():
        assert abs(sx.get((r.match_id, etl.LIV), 0.0) - r.xg) <= 1e-9, (r.match_id, "for")
        assert abs(sx.get((r.match_id, r.opponent), 0.0) - r.xga) <= 1e-9, (r.match_id, "against")
    d = pd.concat([m.xg - m.xg_reported, m.xga - m.xga_reported])
    assert d.min() >= -0.001, "shot sum fell below Understat team xG"
    assert (d.abs() <= 0.02).mean() >= 0.80
    by_season = m.groupby("season")[["xg", "xg_reported", "xga", "xga_reported"]].sum()
    assert (by_season.xg / by_season.xg_reported - 1).abs().max() < 0.03
    assert (by_season.xga / by_season.xga_reported - 1).abs().max() < 0.04


def test_goals_from_shots_plus_own_goals_equal_score(t):
    m, s = t["matches"], t["shots"]
    goals = s[s.result.isin(["Goal", "OwnGoal"])].groupby(["match_id", "scoring_team"]).size()
    for r in m.itertuples():
        assert goals.get((r.match_id, etl.LIV), 0) == r.gf, (r.match_id, "for")
        assert goals.get((r.match_id, r.opponent), 0) == r.ga, (r.match_id, "against")


def test_own_goal_xg_is_zero(t):
    s = t["shots"]
    assert (s[s.result == "OwnGoal"].xg == 0).all()


def test_player_goals_plus_own_goals_equal_team_goals_per_season(t):
    m, r = t["matches"], t["rosters"]
    for season, g in m.groupby("season"):
        ids = set(g.match_id)
        rr = r[r.match_id.isin(ids)]
        liv_goals = rr[rr.team == etl.LIV].goals.sum()
        opp_og = rr[rr.team != etl.LIV].own_goals.sum()  # opponents' own goals count for Liverpool
        assert liv_goals + opp_og == g.gf.sum(), season
        opp_goals = rr[rr.team != etl.LIV].goals.sum()
        liv_og = rr[rr.team == etl.LIV].own_goals.sum()
        assert opp_goals + liv_og == g.ga.sum(), season


def test_no_duplicate_ids(t):
    assert not t["shots"].shot_id.duplicated().any()
    assert not t["rosters"].roster_id.duplicated().any()
    assert not t["matches"].match_id.duplicated().any()
    assert not t["team_matches"].duplicated(["match_id", "team"]).any()
    assert not t["player_seasons"].duplicated(["season", "player_id"]).any()


def test_player_seasons_agree_with_understat_league_totals(t):
    """Independent cross-check: roster-derived totals vs Understat's own season table (single-club players)."""
    ps = t["player_seasons"].set_index(["season", "player_id"])
    checked = 0
    for s in etl.season_range():
        for p in etl.understat_league(s)["players"]:
            if p["team_title"] != etl.LIV:
                continue
            key = (etl.season_label(s), int(p["id"]))
            assert key in ps.index, key
            row = ps.loc[key]
            assert row.goals == int(p["goals"]) and row.minutes == int(p["time"]), key
            assert abs(row.xg - float(p["xG"])) < 0.05, key
            checked += 1
    assert checked > 200


def test_league_team_seasons_complete(t):
    ts = t["team_seasons"]
    cur = etl.season_label(etl.current_season_start())
    for season, g in ts.groupby("season"):
        assert len(g) == 20, season
        if season != cur:
            assert (g.matches == 38).all(), season
    assert (ts.shots > 0).all()
