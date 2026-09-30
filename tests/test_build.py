"""Build-payload tests: the numbers build.py hands to the dashboard, checked against independent pandas sums."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


@pytest.fixture(scope="module")
def tables():
    if not (build.PROCESSED / "matches.parquet").exists():
        pytest.fail("run `python etl.py` first")
    return build.load_tables()


@pytest.fixture(scope="module")
def payload(tables):
    t = tables
    return build.dashboard(t["enriched"], t["fixtures"], t["shots"], t["rosters"], t["team_seasons"], t["team_matches"])


def test_payload_is_strict_json(payload):
    blob = json.dumps(build.clean(payload), allow_nan=False)
    assert "NaN" not in blob and "Infinity" not in blob
    assert len(blob) < 4 * 1024 * 1024


def test_shots_payload_matches_table(payload, tables):
    sh = payload["shots"]
    assert len(sh["rows"]) == len(tables["shots"])
    n_matches = len(payload["matches"])
    for r in sh["rows"]:
        assert 0 <= r[0] < n_matches
        assert 0 <= r[2] <= 1 and 0 <= r[3] <= 1 and 0 <= r[4] <= 1
    # each match's shot xG (own goals are xG 0) equals the match xG in the payload
    by = pd.DataFrame(sh["rows"], columns=sh["cols"])
    lfc = by[by.fl == 1].groupby("mi").xg.sum()
    for i, m in enumerate(payload["matches"]):
        assert abs(lfc.get(i, 0.0) - m["xg"]) < 0.005, i


def test_player_rows_match_player_seasons(payload, tables):
    ps = pd.read_parquet(build.PROCESSED / "player_seasons.parquet")
    for season in ("2019-20", "2024-25", "2025-26"):
        rows = {p["id"]: p for p in payload["selections"][f"{season}|all"]["players"]}
        ref = ps[ps.season == season].set_index("player_id")
        ref = ref[ref.minutes > 0]
        assert set(rows) == set(ref.index), season
        for pid, r in ref.iterrows():
            p = rows[pid]
            assert p["g"] == r.goals and p["min"] == r.minutes and p["ast"] == r.assists, (season, pid)
            assert p["npg"] == r.npg and abs(p["xg"] - r.xg) < 1e-3 and abs(p["npxg"] - r.npxg) < 1e-3, (season, pid)


def test_source_mix_equals_shot_sums(payload, tables):
    e = tables["enriched"]
    for key, sel in payload["selections"].items():
        season, era = key.split("|")
        d = e[(e.season == season) if season != "all" else slice(None)]
        if era != "all":
            d = d[d.era == era]
        for side, col in (("for", "xg"), ("against", "xga")):
            total = sum(v["xg"] for g in sel["mix"][side] for v in (g["open"], g["set"], g["pen"]))
            assert abs(total - d[col].sum()) < 0.01 * max(1, len(sel["mix"][side])), (key, side)
        goals = sum(v["goals"] for g in sel["mix"]["for"] for v in (g["open"], g["set"], g["pen"]))
        assert goals == int(d.goals_shots.sum()), key
        assert sum(g["n"] for g in sel["mix"]["for"]) == len(d)


def test_league_context_agrees_with_liverpool_matches(payload, tables):
    e = tables["enriched"]
    for season, g in e.groupby("season"):
        lg = payload["league"][season]
        assert len(lg["teams"]) == 20
        liv = next(t for t in lg["teams"] if t["team"] == "Liverpool")
        assert abs(liv["shots_pm"] - g.shots_for.mean()) < 1e-3, season
        assert abs(liv["xgps"] - g.xg.sum() / g.shots_for.sum()) < 1e-3, season
        # league average xG per shot must be a plausible pooled figure
        assert 0.08 < lg["avg"]["xgps"] < 0.13 and 8 < lg["avg"]["shots_pm"] < 15
    # league xG per match average is symmetric: mean xG for = mean xG against
    tm = tables["team_matches"]
    assert abs(tm.groupby("season").xg.mean().sub(tm.groupby("season").xga.mean()).abs().max()) < 1e-6


def test_lfc_points_and_rolls(payload):
    sel = payload["selections"]["2025-26|all"]
    assert len(sel["lfc_points"]) == 1 and sel["lfc_points"][0]["n"] == 38
    for k in ("roll_xg", "roll_xga", "roll_ppda", "roll_deepa", "roll"):
        assert len(sel[k]) == sel["n"]
        assert all(v is None for v in sel[k][:9]) and all(v is not None for v in sel[k][9:])
    assert len(payload["selections"]["all|all"]["lfc_points"]) == 13


def test_player_per90_fields_are_consistent(payload):
    n = 0
    for key, sel in payload["selections"].items():
        for p in sel["players"]:
            assert p["min"] > 0
            for k, v in (("npxg90", "npxg"), ("xa90", "xa"), ("build90", "build"), ("chain90", "chain"), ("sh90", "sh"), ("kp90", "kp"), ("xg90", "xg")):
                assert abs(p[k] - p[v] / p["min"] * 90) < 1e-4 + 5.1e-5 * 90 / p["min"], (key, p["name"], k)  # inputs are rounded to 4 dp
            assert abs(p["fin"] - (p["npg"] - p["npxg"])) < 1e-3
            assert p["npg"] <= p["g"] and p["npxg"] <= p["xg"] + 1e-6
            n += 1
    assert n > 900


def test_match_market_payload_matches_metrics(payload, tables):
    import metrics as MT
    e = tables["enriched"]
    for m, r in zip(payload["matches"], e.itertuples()):
        odds = [r.mkt_h, r.mkt_d, r.mkt_a]
        home = r.is_home
        for name, fn in (("proportional", MT.devig_proportional), ("shin", MT.devig_shin)):
            p = fn(odds)
            exp = list(p) if home else list(p[::-1])
            assert all(abs(a - b) < 2e-6 for a, b in zip(m["mktp"][name], exp)), (m["id"], name)
            assert abs(sum(m["mktp"][name]) - 1) < 1e-5
        assert abs(sum(m["sim"]) - 1) < 1e-5 and abs(m["ovr"] - (sum(1 / o for o in odds) - 1)) < 1e-4
        assert m["o"] == [round(o, 4) for o in odds]
