"""ETL tests: run against data/processed/*.parquet built by `python etl.py`."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl  # noqa: E402


def _slugs():
    """Clubs with processed data (all of config/clubs.json when the ETL has run for every club)."""
    return [c for c in etl.CLUBS if (etl.club_dir(c) / "matches.parquet").exists()] or list(etl.CLUBS)


@pytest.fixture(scope="session", params=_slugs())
def t(request):
    """Every ETL test below runs once per club: the club-specific tables plus the shared league tables."""
    slug = request.param
    own = ["matches", "fixtures", "shots", "rosters", "player_seasons"]
    shared = ["team_matches", "team_seasons"]
    missing = [n for n in own if not (etl.club_dir(slug) / f"{n}.parquet").exists()] + [n for n in shared if not (etl.PROCESSED / f"{n}.parquet").exists()]
    if missing:
        pytest.fail(f"missing processed tables {missing} for {slug}: run `python etl.py` first")
    out = {n: pd.read_parquet(etl.club_dir(slug) / f"{n}.parquet") for n in own}
    out.update({n: pd.read_parquet(etl.PROCESSED / f"{n}.parquet") for n in shared})
    out["slug"], out["club"] = slug, etl.CLUBS[slug]["canonical"]
    return out


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
        assert abs(sx.get((r.match_id, t["club"]), 0.0) - r.xg) <= 1e-9, (r.match_id, "for")
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
        assert goals.get((r.match_id, t["club"]), 0) == r.gf, (r.match_id, "for")
        assert goals.get((r.match_id, r.opponent), 0) == r.ga, (r.match_id, "against")


def test_own_goal_xg_is_zero(t):
    s = t["shots"]
    assert (s[s.result == "OwnGoal"].xg == 0).all()


def test_player_goals_plus_own_goals_equal_team_goals_per_season(t):
    m, r = t["matches"], t["rosters"]
    for season, g in m.groupby("season"):
        ids = set(g.match_id)
        rr = r[r.match_id.isin(ids)]
        liv_goals = rr[rr.team == t["club"]].goals.sum()
        opp_og = rr[rr.team != t["club"]].own_goals.sum()  # opponents' own goals count for Liverpool
        assert liv_goals + opp_og == g.gf.sum(), season
        opp_goals = rr[rr.team != t["club"]].goals.sum()
        liv_og = rr[rr.team == t["club"]].own_goals.sum()
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
            if p["team_title"] != t["club"]:
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


def test_team_season_shotlevel_matches_liverpool_shots(t):
    """Understat's team statistics include opponent own goals as 1.0-xG shots; the corrected
    shot-level figures must equal the sums of Liverpool's actual shots exactly."""
    ts, m = t["team_seasons"], t["matches"]
    liv = ts[ts.team == t["club"]].set_index("season")
    s = t["shots"]
    s = s[(s.team == t["club"]) & (s.result != "OwnGoal")]
    by = s.groupby("season").agg(n=("xg", "size"), xg=("xg", "sum"))
    for season in liv.index:
        assert liv.loc[season, "shots_shotlevel"] == by.loc[season, "n"], season
        assert abs(liv.loc[season, "xg_shotlevel"] - by.loc[season, "xg"]) < 1e-6, season
    assert (ts.og_for >= 0).all() and ts.og_for.max() <= 15


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise etl.requests.HTTPError(f"{self.status_code}", response=self)


def test_fetch_retries_transient_failures_and_respects_rate_limit(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(etl.time, "sleep", lambda s: sleeps.append(s))
    seq = iter([etl.requests.ConnectionError("boom"), _Resp(503), _Resp(200)])

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(etl._session, "get", fake_get)
    assert etl._get("http://x").status_code == 200
    assert len(calls) == 3
    assert sleeps.count(etl.BACKOFF) == 1 and etl.BACKOFF * 2 in sleeps  # exponential backoff between attempts


def test_fetch_gives_up_and_raises_so_the_deploy_is_skipped(monkeypatch):
    monkeypatch.setattr(etl.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(etl._session, "get", lambda url, headers=None, timeout=None: (calls.append(1), _Resp(503))[1])
    with pytest.raises(etl.requests.HTTPError):
        etl._get("http://x")
    assert len(calls) == etl.RETRIES
    calls.clear()
    monkeypatch.setattr(etl._session, "get", lambda url, headers=None, timeout=None: (calls.append(1), _Resp(404))[1])
    with pytest.raises(etl.requests.HTTPError):  # a 404 is not transient: no retries
        etl._get("http://x")
    assert len(calls) == 1


# ------------------------------------------------------------------ style of play: raw inputs (S1)
@pytest.fixture(scope="session")
def style_raw():
    p = etl.PROCESSED / "style_raw.parquet"
    if not p.exists():
        pytest.fail("run `python etl.py --no-fetch` first")
    return pd.read_parquet(p)


def test_style_raw_has_twenty_clubs_per_season_and_matches_team_matches(style_raw, t):
    per = style_raw.groupby("season").club.nunique()
    assert set(per.index) == {etl.season_label(s) for s in etl.season_range()} and (per == 20).all()
    assert not style_raw.duplicated(["season", "club"]).any()
    played = t["team_matches"].groupby(["season", "team"]).size()
    for r in style_raw.itertuples():
        assert r.matches == played[(r.season, r.club)], (r.season, r.club)
    cur = etl.season_label(etl.current_season_start())
    assert (style_raw[style_raw.season != cur].matches == 38).all()


def test_style_raw_zone_and_speed_splits_add_up_to_total_shots(style_raw):
    zone_for = style_raw[[f"zone_{k}_shots_for" for k in ("six", "pen", "out", "og")]].sum(axis=1)
    zone_ag = style_raw[[f"zone_{k}_shots_against" for k in ("six", "pen", "out", "og")]].sum(axis=1)
    speed_for = style_raw[[f"speed_{k}_shots_for" for k in ("fast", "normal", "standard", "slow")]].sum(axis=1)
    speed_ag = style_raw[[f"speed_{k}_shots_against" for k in ("fast", "normal", "standard", "slow")]].sum(axis=1)
    assert (zone_for == style_raw.situation_shots_for).all() and (zone_ag == style_raw.situation_shots_against).all()
    assert (speed_for == style_raw.situation_shots_for).all() and (speed_ag == style_raw.situation_shots_against).all()
    # in-box is a subset of all shots; own goals are at most a handful
    inbox = style_raw.zone_six_shots_against + style_raw.zone_pen_shots_against
    assert (inbox <= zone_ag).all() and (inbox > 0).all() and style_raw.zone_og_shots_for.max() <= 12
    # league symmetry: every shot is for one club and against another, per season
    g = style_raw.groupby("season")[["situation_shots_for", "situation_shots_against"]].sum()
    assert (g.situation_shots_for == g.situation_shots_against).all()
    assert (style_raw.xgbuildup <= style_raw.xgchain + 1e-9).all() and style_raw.xgchain.notna().all()


def test_style_raw_reconciles_with_liverpool_shot_dataset(style_raw, t):
    """Liverpool's conceded / created shot counts from the team page equal the existing shot-level dataset exactly,
    and the zone split agrees with a geometric box classification to within 6%."""
    s = t["shots"]
    liv = style_raw[style_raw.club == t["club"]].set_index("season")
    for season, r in liv.iterrows():
        d = s[s.season == season]
        opp_shots = int(((d.team != t["club"]) & (d.result != "OwnGoal")).sum())
        own_goals_by_liv = int(((d.team == t["club"]) & (d.result == "OwnGoal")).sum())
        assert r.situation_shots_against == opp_shots + own_goals_by_liv, season          # against = their shots + our own goals
        liv_shots = int(((d.team == t["club"]) & (d.result != "OwnGoal")).sum())
        opp_own_goals = int(((d.team != t["club"]) & (d.result == "OwnGoal")).sum())
        assert r.situation_shots_for == liv_shots + opp_own_goals, season                 # for = our shots + their own goals
        assert r.zone_og_shots_against == own_goals_by_liv and r.zone_og_shots_for == opp_own_goals, season
        opp = d[(d.team != t["club"]) & (d.result != "OwnGoal")]
        x, y = opp.x * 105, opp.y * 68
        six = (x >= 105 - 5.5) & (y >= 24.84) & (y <= 43.16)
        pen = (x >= 105 - 16.5) & (y >= 13.84) & (y <= 54.16) & ~six
        inbox_geo, inbox_us = int((six | pen).sum()), int(r.zone_six_shots_against + r.zone_pen_shots_against)
        assert abs(inbox_geo - inbox_us) <= 0.06 * inbox_us + 2, (season, inbox_geo, inbox_us)
        assert inbox_us <= r.situation_shots_against
