"""Style-of-play scoring tests: hand-computed fixtures first, then the real processed data."""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
import os
PROC = Path(os.environ.get("TRACKER_DATA", ROOT / "data")) / "processed"
sys.path.insert(0, str(ROOT))
import style  # noqa: E402

CFG = style.load_config()


def _league(n=20, seed=3):
    """A synthetic league: clubs c00.. with random values for every available KPI."""
    rng = np.random.default_rng(seed)
    clubs = [f"c{i:02d}" for i in range(n)]
    return pd.DataFrame({k: rng.normal(10, 2, n) for k, m in CFG["kpis"].items() if m["available"]}, index=clubs)


def _raw():
    p = PROC / "style_raw.parquet"
    if not p.exists():
        pytest.fail("run `python etl.py --no-fetch` first")
    return pd.read_parquet(p)


# ------------------------------------------------------------------ z-scores
def test_zscores_have_zero_mean_and_unit_population_sd():
    z = style.zscore(pd.Series(np.random.default_rng(1).normal(5, 3, 20)))
    assert abs(z.mean()) < 1e-9 and abs(z.std(ddof=0) - 1) < 1e-9
    z = style.zscore(pd.Series([1.0, 2, 3, 4, 5]))        # mean 3, population sd sqrt(2)
    r = math.sqrt(2)
    assert z.tolist() == pytest.approx([-2 / r, -1 / r, 0, 1 / r, 2 / r])
    assert style.zscore(pd.Series([4.0, 4, 4])).isna().all()       # no spread: undefined, never 0
    assert style.zscore(pd.Series([1.0, np.nan, 3.0])).isna().tolist() == [False, True, False]


def test_normal_cdf_values():
    assert style.phi(0) == 0.5
    assert style.phi(1.96) == pytest.approx(0.975, abs=5e-4)
    assert style.phi(-1) == pytest.approx(0.158655, abs=1e-6)


# ------------------------------------------------------------------ sign handling
def test_lowest_ppda_scores_highest_on_active():
    v = _league()
    v.loc["c05", "def_ppda"] = 2.0
    v.loc["c05", "def_actions_pm"] = 40.0
    res = style.score_axis(v, CFG["axes"]["style_def_press"], CFG)
    assert res["scores"].idxmax() == "c05" and res["ranks"]["c05"] == 1 and res["scores"]["c05"] > 90
    v = _league()
    v.loc["c07", "def_ppda"] = 30.0
    v.loc["c07", "def_actions_pm"] = 2.0
    res = style.score_axis(v, CFG["axes"]["style_def_press"], CFG)
    assert res["scores"].idxmin() == "c07" and res["ranks"]["c07"] == 20


def test_highest_in_box_conceded_scores_lowest_on_tight():
    v = _league()
    v.loc["c11", "def_inbox_shots_pm"] = 25.0
    v.loc["c11", "def_inbox_xga_pm"] = 25.0
    res = style.score_axis(v, CFG["axes"]["style_def_tight"], CFG)
    assert res["scores"].idxmin() == "c11" and res["ranks"]["c11"] == 20 and res["scores"]["c11"] < 10


def test_raising_each_kpi_moves_the_axis_the_way_the_config_sign_says():
    """sign +1: a higher KPI value raises the score; sign -1: a lower one does."""
    for aid, ax in CFG["axes"].items():
        if ax["status"] != "proxy":
            continue
        base = style.score_axis(_league(), ax, CFG)["scores"]["c03"]
        for k in ax["kpis"]:
            v = _league()
            v.loc["c03", k] = v[k].mean() + CFG["kpis"][k]["sign"] * 4 * v[k].std()
            assert style.score_axis(v, ax, CFG)["scores"]["c03"] > base, (aid, k)


# ------------------------------------------------------------------ null handling, range, ties
def test_axis_with_fewer_than_two_kpis_is_null_not_zero():
    v = _league()
    assert style.score_axis(v[["def_ppda"]], CFG["axes"]["style_def_press"], CFG) is None
    v2 = _league()
    v2["def_actions_pm"] = 7.0            # no spread: carries no information
    assert style.score_axis(v2, CFG["axes"]["style_def_press"], CFG) is None
    assert style.score_axis(_league(), CFG["axes"]["style_bu_length"], CFG) is None     # no KPIs at all
    v3 = _league()
    v3.loc["c00", "def_actions_pm"] = np.nan        # a club missing one of two KPIs has no score
    r3 = style.score_axis(v3, CFG["axes"]["style_def_press"], CFG)
    assert "c00" not in r3["scores"].index and len(r3["scores"]) == 19


def test_scores_in_open_interval_and_league_mean_scores_50():
    v = _league()
    for ax in ("style_def_line", "style_def_tight", "style_att_tempo"):
        res = style.score_axis(v, CFG["axes"][ax], CFG)
        assert ((res["scores"] > 0) & (res["scores"] < 100)).all()
        assert sorted(res["ranks"]) == list(range(1, 21))
        assert abs(res["z"].mean()) < 1e-9 and abs(res["z"].std(ddof=0) - 1) < 1e-9
    n = 21            # symmetric league: the middle club is exactly at the mean on every KPI
    sym = pd.DataFrame({"def_ppda": np.linspace(-5, 5, n) + 10, "def_actions_pm": np.linspace(-3, 3, n) - 4 + 20},
                       index=[f"s{i}" for i in range(n)])
    res = style.score_axis(sym, CFG["axes"]["style_def_press"], CFG)
    assert abs(res["scores"]["s10"] - 50) < 0.5


def test_identical_values_share_a_rank_and_the_next_rank_is_skipped():
    z = pd.Series([1.0, 0.5, 0.5, 0.5, -1.0], index=list("abcde"))
    assert style.competition_rank(z).to_dict() == {"a": 1, "b": 2, "c": 2, "d": 2, "e": 5}
    assert style.competition_rank(pd.Series([0.3, 0.3 + 1e-14, 0.1])).tolist() == [1, 1, 3]


def test_percentile_rank_transform_is_available_and_monotone():
    cfg = {**CFG, "transform": "percentile_rank"}
    res = style.score_axis(_league(), CFG["axes"]["style_def_tight"], cfg)
    assert res["scores"].max() == pytest.approx(97.5) and res["scores"].min() == pytest.approx(2.5)
    assert list(res["scores"].sort_values().index) == list(res["z"].sort_values().index)


# ------------------------------------------------------------------ composite index
def test_composite_index_is_the_mean_of_available_axes():
    frame = pd.DataFrame({"a": [60.0, 40.0, 80.0, np.nan], "b": [80.0, np.nan, 20.0, np.nan], "c": [70.0, 50.0, np.nan, 90.0]},
                         index=list("wxyz"))
    idx = style.composite(frame, 3)
    assert idx.loc["w", "score"] == 70 and idx.loc["w", "n_axes"] == 3 and not idx.loc["w", "partial"]
    assert idx.loc["x", "score"] == 45 and idx.loc["x", "partial"]          # mean of 40 and 50
    assert idx.loc["y", "score"] == 50 and idx.loc["y", "partial"]          # mean of 80 and 20
    assert pd.isna(idx.loc["z", "score"]) and idx.loc["z", "n_axes"] == 1 and not idx.loc["z", "partial"]   # one axis: no index


def test_indices_follow_axis_availability_on_real_data():
    res = style.compute(_raw(), CFG)
    for season, r in res.items():
        p, c, o = (r["indices"][i] for i in ("style_idx_pressure", "style_idx_control", "style_idx_occupation"))
        assert p.n_axes.eq(3).all() and not p.partial.any()
        assert o.n_axes.eq(2).all() and o.partial.all()                       # Aerial is unavailable
        assert c.score.isna().all() and c.n_axes.eq(1).all() and not c.partial.any()   # one axis: no index
        manual = pd.DataFrame({a: r["axes"][a]["scores"] for a in ("style_att_tempo", "style_att_grouping")}).mean(axis=1)
        assert (o.score - manual).abs().max() < 1e-9


# ------------------------------------------------------------------ shot-location classifiers
def test_central_rectangle_boundaries():
    c = CFG["central_rectangle"]
    e = 1e-6
    assert style.in_central_rectangle(c["x_min"], 0.5, CFG) and not style.in_central_rectangle(c["x_min"] - e, 0.5, CFG)
    hi = 0.5 + c["y_half_width"]
    lo = 0.5 - c["y_half_width"]
    assert style.in_central_rectangle(0.95, hi, CFG) and not style.in_central_rectangle(0.95, hi + e, CFG)
    assert style.in_central_rectangle(0.95, lo, CFG) and not style.in_central_rectangle(0.95, lo - e, CFG)
    assert style.in_central_rectangle(1.0, 0.5, CFG) and not style.in_central_rectangle(0.5, 0.5, CFG)


def test_box_zone_boundaries_and_zone_counts_sum_to_total():
    six_x, pen_x = (105 - 5.5) / 105, (105 - 16.5) / 105
    assert style.box_zone(six_x, 0.5) == "six" and style.box_zone(six_x - 1e-6, 0.5) == "pen"
    assert style.box_zone(pen_x, 0.5) == "pen" and style.box_zone(pen_x - 1e-6, 0.5) == "out"
    assert style.box_zone(0.99, 24.85 / 68) == "six" and style.box_zone(0.99, 24.83 / 68) == "pen"
    assert style.box_zone(0.92, 13.85 / 68) == "pen" and style.box_zone(0.92, 13.83 / 68) == "out"
    assert style.box_zone(0.92, 54.15 / 68) == "pen" and style.box_zone(0.92, 54.17 / 68) == "out"
    shots = pd.read_parquet(PROC / "liverpool" / "shots.parquet")
    for season, g in shots[shots.result != "OwnGoal"].groupby("season"):
        assert sum(style.zone_counts(g).values()) == len(g), season


# ------------------------------------------------------------------ real data
def test_liverpool_conceded_counts_reconcile_with_shot_dataset():
    raw, cfg = _raw(), CFG
    shots = pd.read_parquet(PROC / "liverpool" / "shots.parquet")
    kp = style.kpi_table(raw, cfg)
    for season, r in raw[raw.club == "Liverpool"].set_index("season").iterrows():
        d = shots[(shots.season == season) & (shots.team != "Liverpool") & (shots.result != "OwnGoal")]
        own = int(((shots.season == season) & (shots.team == "Liverpool") & (shots.result == "OwnGoal")).sum())
        assert r.situation_shots_against == len(d) + own, season
        inbox = int(r.zone_six_shots_against + r.zone_pen_shots_against)
        geo = sum(style.box_zone(x, y) != "out" for x, y in zip(d.x, d.y))
        assert abs(geo - inbox) <= 0.06 * inbox + 2 and inbox <= r.situation_shots_against, season
        v = kp[(kp.season == season) & (kp.club == "Liverpool")].set_index("kpi_id").value
        assert v["def_inbox_shots_pm"] * r.matches == pytest.approx(inbox)
        assert v["def_inbox_xga_pm"] * r.matches <= d.xg.sum() + 1.0, season


def test_every_season_has_twenty_clubs_and_partial_season_uses_matches_played():
    raw = _raw()
    res = style.compute(raw, CFG)
    assert len(res) == raw.season.nunique()
    for season, r in res.items():
        assert r["n_clubs"] == 20
        for aid, ax in CFG["axes"].items():
            if ax["status"] == "proxy":
                assert r["axes"][aid] is not None and len(r["axes"][aid]["scores"]) == 20 and r["axes"][aid]["scores"].notna().all(), (season, aid)
            else:
                assert r["axes"][aid] is None, (season, aid)
    last = raw.season.max()
    full = raw[raw.season != last]
    assert (full.matches == 38).all() and (raw[raw.season == last].matches < 38).all()
    k = style.kpi_table(raw, CFG)
    cur = raw[raw.season == last].set_index("club")
    a = k[(k.season == last) & (k.kpi_id == "def_actions_pm")].set_index("club").value
    assert ((a * cur.matches) - cur.ppda_def).abs().max() < 1e-9


def test_unavailable_axes_carry_no_numbers_in_the_payload():
    pl = style.payload(style.compute(_raw(), CFG), CFG)
    for aid in ("style_bu_length", "style_bu_direction", "style_att_aerial"):
        assert all(s["axes"][aid] == {"status": "unavailable"} for s in pl["seasons"].values())
        assert all(v is None for v in pl["series"][aid].values())
    assert all(s["indices"]["style_idx_control"]["score"] is None for s in pl["seasons"].values())


# ------------------------------------------------------------------ external hook
def test_absent_external_file_is_silent(tmp_path):
    assert style.load_external(tmp_path / "nope.csv", CFG) is None
    res = style.compute(_raw(), CFG, external=None)
    assert set(res["2024-25"]["origin"].values()) == {"proxy"}


def test_external_csv_overrides_a_kpi_and_flips_the_badge(tmp_path):
    raw = _raw()
    clubs = sorted(raw[raw.season == "2024-25"].club)
    base = style.compute(raw, CFG)["2024-25"]["axes"]["style_def_press"]
    csv = tmp_path / "style_kpis.csv"
    pd.DataFrame({"season": "2024-25", "club": clubs, "kpi_id": "def_ppda",
                  "value": [100.0 if c == "Liverpool" else 5.0 + i * 0.1 for i, c in enumerate(clubs)]}).to_csv(csv, index=False)
    res = style.compute(raw, CFG, external=style.load_external(csv, CFG, raw))
    r = res["2024-25"]
    assert r["origin"]["style_def_press"] == "external" and r["origin"]["style_def_tight"] == "proxy"
    assert r["axes"]["style_def_press"]["scores"]["Liverpool"] < base["scores"]["Liverpool"] - 20
    assert res["2023-24"]["origin"]["style_def_press"] == "proxy"
    assert style.payload(res, CFG)["seasons"]["2024-25"]["axes"]["style_def_press"]["origin"] == "external"


def test_external_kpis_can_enable_an_unavailable_axis(tmp_path):
    raw = _raw()
    clubs = sorted(raw[raw.season == "2024-25"].club)
    rows = [{"season": "2024-25", "club": c, "kpi_id": k, "value": (i * 7 % 13) / 100 + (0.2 if k == "att_header_share" else 0.1) + (i % 3) * 0.01 * (k == "att_cross_share")}
            for k in ("att_header_share", "att_cross_share") for i, c in enumerate(clubs)]
    csv = tmp_path / "style_kpis.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)
    res = style.compute(raw, CFG, external=style.load_external(csv, CFG, raw))
    aerial = res["2024-25"]["axes"]["style_att_aerial"]
    assert aerial is not None and res["2024-25"]["origin"]["style_att_aerial"] == "external" and len(aerial["scores"]) == 20
    occ = res["2024-25"]["indices"]["style_idx_occupation"]
    assert occ.n_axes.eq(3).all() and not occ.partial.any()
    assert res["2023-24"]["axes"]["style_att_aerial"] is None


def test_malformed_external_files_are_rejected(tmp_path):
    good = {"season": ["2024-25"], "club": ["Liverpool"], "kpi_id": ["def_ppda"], "value": [9.0]}

    def w(df):
        p = tmp_path / "x.csv"
        df.to_csv(p, index=False)
        return p

    with pytest.raises(ValueError, match="columns"):
        style.load_external(w(pd.DataFrame({**good, "extra": [1]})), CFG)
    with pytest.raises(ValueError, match="unknown"):
        style.load_external(w(pd.DataFrame({**good, "kpi_id": ["made_up"]})), CFG)
    with pytest.raises(ValueError, match="duplicate"):
        style.load_external(w(pd.concat([pd.DataFrame(good)] * 2)), CFG)
    with pytest.raises(ValueError, match="not in the data"):
        style.load_external(w(pd.DataFrame({**good, "club": ["Nowhere FC"]})), CFG, _raw())
    with pytest.raises(ValueError):
        style.load_external(w(pd.DataFrame({**good, "value": [float("inf")]})), CFG)


# ------------------------------------------------------------------ config and registry
def test_config_is_consistent():
    assert CFG["transform"] in ("normal_cdf", "percentile_rank") and CFG["min_kpis"] >= 2
    for ph in CFG["phases"].values():
        assert len(ph["axes"]) == 3 and all(a in CFG["axes"] for a in ph["axes"])
    used = set()
    for aid, ax in CFG["axes"].items():
        assert ax["status"] in ("proxy", "unavailable") and ax["confidence"] in ("high", "medium", "low", "none")
        assert ax["status"] == "unavailable" or len(ax["kpis"]) >= CFG["min_kpis"], aid
        assert ax["status"] == "proxy" or ax.get("unavailable_reason")
        for k in ax["kpis"] + ax["external_kpis"]:
            assert k in CFG["kpis"]
            used.add(k)
        assert all(CFG["kpis"][k]["available"] for k in ax["kpis"])
    assert used == set(CFG["kpis"])
    for k, meta in CFG["kpis"].items():
        assert meta["sign"] in (-1, 1)
        assert (k in style.KPI_FORMULAS) == meta["available"], k


def test_registry_entries_exist_for_every_axis_index_and_kpi():
    reg = {m["id"]: m for m in json.loads((ROOT / "metrics.json").read_text(encoding="utf-8"))}
    scored = set(CFG["axes"]) | {p["index_id"] for p in CFG["phases"].values()}
    assert scored | set(CFG["kpis"]) <= set(reg)
    for i in scored | set(CFG["kpis"]):
        m = reg[i]
        assert m["higher_is_better"] is None and m["status"] in ("proxy", "external"), i
        assert m["confidence"] in ("high", "medium", "low", "none"), i
        assert {"label", "description", "unit", "format", "min_sample", "source"} <= set(m), i
    for i in scored:
        assert reg[i]["unit"] == "score100", i
