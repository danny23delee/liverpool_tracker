"""Metric tests: hand-computed fixtures first, then cross-checks against the real processed data."""
import itertools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import metrics as M  # noqa: E402

import os

PROCESSED = Path(os.environ.get("TRACKER_DATA", Path(__file__).resolve().parents[1] / "data")) / "processed"


# ------------------------------------------------------------------ registry
def test_registry_is_well_formed():
    raw = __import__("json").loads((M.ROOT / "metrics.json").read_text(encoding="utf-8"))
    assert len({e["id"] for e in raw}) == len(raw), "duplicate metric ids"
    for e in raw:
        assert {"id", "label", "description", "unit", "higher_is_better", "format", "min_sample", "source"} <= set(e)
        assert e["unit"] in {"count", "rate", "pct", "per90", "prob", "odds", "score100"}, e["id"]
        assert e["higher_is_better"] in (True, False, None), e["id"]
        assert e["label"] and e["description"] and e["format"], e["id"]
        assert isinstance(e["min_sample"], int) and e["min_sample"] > 0
    assert set(M.RATE_SERIES) <= set(M.REGISTRY)


# ------------------------------------------------------------------ record fixture (hand computed)
def _fixture():
    # W 2-0, W 3-1, D 1-1, L 0-2, W 1-0
    return pd.DataFrame({
        "result": list("WWDLW"), "gf": [2, 3, 1, 0, 1], "ga": [0, 1, 1, 2, 0], "pts": [3, 3, 1, 0, 3],
        "xg": [1.5, 2.0, 1.0, 0.5, 1.2], "xga": [0.4, 1.1, 0.9, 1.8, 0.3], "npxg": [1.5, 2.0, 1.0, 0.5, 1.2],
        "xpts_sim": [2.4, 2.1, 1.2, 0.3, 2.0], "xpts_market": [2.0, 2.2, 1.5, 0.8, 2.1],
        "xpts_us": [2.4, 2.1, 1.2, 0.3, 2.0], "goals_shots": [2, 3, 1, 0, 1], "pnl": [0.5, 0.8, -1, -1, 0.6],
    })


def test_record_totals_are_consistent_integers():
    r = M.record(_fixture())
    assert (r["wins"], r["draws"], r["losses"]) == (3, 1, 1)
    assert r["wins"] + r["draws"] + r["losses"] == r["matches"] == 5
    assert r["points"] == 3 * r["wins"] + r["draws"] == 10
    assert (r["goals_for"], r["goals_against"], r["goal_diff"], r["clean_sheets"]) == (7, 4, 3, 2)
    for k in ("matches", "wins", "draws", "losses", "points", "goals_for", "goals_against", "goal_diff", "clean_sheets"):
        assert type(r[k]) is int, k
    assert r["xg"] == pytest.approx(6.2) and r["goals_minus_xg"] == pytest.approx(7 - 6.2)
    assert r["points_minus_xpts"] == pytest.approx(10 - 8.0)
    assert r["points_minus_market"] == pytest.approx(10 - 8.6)
    assert r["pnl"] == pytest.approx(-0.1)


# ------------------------------------------------------------------ change function
def test_change_null_baseline_or_zero_is_null():
    for base in (None, 0, 0.0, float("nan")):
        c = M.change(1.5, base, "xg_pm")
        assert c["kind"] is None and c["value"] is None and c["direction"] is None and c["sentiment"] is None
    assert M.change(None, 1.0, "xg_pm")["kind"] is None
    assert M.change(1.0, -2.0, "xg_pm")["kind"] is None  # % of a negative baseline is meaningless


def test_change_regression_13_3_vs_12_2_is_plus_9_percent():
    c = M.change(13.3, 12.2, "shots_pm")
    assert c["kind"] == "pct" and round(c["value"] * 100, 1) == 9.0
    assert c["direction"] == "up" and c["sentiment"] == "good"


def test_change_pct_and_prob_metrics_use_percentage_points():
    c = M.change(0.55, 0.50, "win_rate")
    assert c["kind"] == "pp" and c["value"] == pytest.approx(5.0)
    c = M.change(0.60, 0.65, "p_win_market")
    assert c["kind"] == "pp" and c["value"] == pytest.approx(-5.0) and c["sentiment"] == "bad"


def test_change_lower_is_better_colouring():
    down = M.change(1.0, 1.2, "xga_pm")  # xGA down -> good
    assert down["direction"] == "down" and down["sentiment"] == "good"
    up = M.change(1.4, 1.2, "xga_pm")
    assert up["direction"] == "up" and up["sentiment"] == "bad"
    assert M.change(1.0, 1.2, "xg_pm")["sentiment"] == "bad"  # xG down -> bad
    assert M.change(12.0, 12.2, "ppda")["sentiment"] == "good"  # PPDA down -> good
    assert M.change(1.0, 1.0, "xga_pm")["direction"] == "flat"
    assert M.change(5, 4, "draws")["sentiment"] == "neutral"  # higher_is_better null


def test_change_signed_metrics_never_show_impossible_percentages():
    c = M.change(-2, 10, "goal_diff")
    assert c["kind"] == "abs" and c["value"] == -12 and c["sentiment"] == "bad"
    # the reference dashboard's "-117%" cannot arise: unsigned metrics are bounded below by -100%
    rng = np.random.default_rng(0)
    for mid in (i for i, e in M.REGISTRY.items() if e["unit"] in ("count", "rate", "per90") and not e.get("signed")):
        for cur, base in rng.uniform(0, 50, size=(20, 2)):
            c = M.change(cur, base, mid)
            assert c["kind"] == "pct" and c["value"] >= -1.0


# ------------------------------------------------------------------ de-vig
BOOKS = [(2.0, 3.5, 4.0), (1.5, 4.5, 7.0), (1.25, 6.5, 13.0), (3.1, 3.3, 2.4), (10.0, 5.5, 1.3)]


@pytest.mark.parametrize("odds", BOOKS)
@pytest.mark.parametrize("fn", [M.devig_proportional, M.devig_shin])
def test_devig_probabilities_valid(fn, odds):
    p = fn(odds)
    assert ((p > 0) & (p < 1)).all()
    assert abs(p.sum() - 1) < 1e-9


def test_devig_proportional_hand_computed():
    p = M.devig_proportional((2.0, 3.5, 4.0))
    s = 0.5 + 1 / 3.5 + 0.25
    assert p == pytest.approx([0.5 / s, (1 / 3.5) / s, 0.25 / s], abs=1e-12)
    assert p == pytest.approx([0.482759, 0.275862, 0.241379], abs=1e-6)


def test_devig_shin_and_proportional_agree_on_low_margin_books():
    low = (2.02, 3.62, 4.1)  # ~1.6% margin
    assert abs(sum(1 / o for o in low) - 1) < 0.02
    assert np.abs(M.devig_shin(low) - M.devig_proportional(low)).max() < 0.002
    assert np.abs(M.devig_shin((2.0, 3.5, 4.0)) - M.devig_proportional((2.0, 3.5, 4.0))).max() < 0.01


def test_devig_shin_moves_mass_to_favourite_and_handles_no_margin():
    prop, shin = M.devig_proportional((1.25, 6.5, 13.0)), M.devig_shin((1.25, 6.5, 13.0))
    assert shin[0] > prop[0] and shin[2] < prop[2]  # favourite-longshot correction
    assert 0 < M.shin_z((1.25, 6.5, 13.0)) < 1
    fair = M.devig_shin((2.0, 4.0, 4.0))  # exactly fair book
    assert fair == pytest.approx([0.5, 0.25, 0.25])
    with pytest.raises(ValueError):
        M.devig_proportional((1.0, 3.0, 4.0))


# ------------------------------------------------------------------ xG simulation
def _brute(xgs_a, xgs_b):
    """Enumerate every hit/miss combination of every shot."""
    w = d = l = 0.0
    shots = [(p, 0) for p in xgs_a] + [(p, 1) for p in xgs_b]
    for hits in itertools.product([0, 1], repeat=len(shots)):
        prob, g = 1.0, [0, 0]
        for (p, side), h in zip(shots, hits):
            prob *= p if h else 1 - p
            g[side] += h
        if g[0] > g[1]:
            w += prob
        elif g[0] == g[1]:
            d += prob
        else:
            l += prob
    return w, d, l


def test_convolution_matches_brute_force_on_three_shot_fixture():
    a, b = [0.3, 0.5], [0.2]
    assert M.outcome_probs(a, b) == pytest.approx(_brute(a, b), abs=1e-12)
    assert M.outcome_probs([0.1, 0.76, 0.05], []) == pytest.approx(_brute([0.1, 0.76, 0.05], []), abs=1e-12)
    # hand check: P(0 goals) for [0.3, 0.5] = 0.7 * 0.5
    assert M.goal_dist([0.3, 0.5])[0] == pytest.approx(0.35)
    assert M.goal_dist([0.3, 0.5]) == pytest.approx([0.35, 0.3 * 0.5 + 0.7 * 0.5, 0.15])
    w, d, l = M.outcome_probs(a, b)
    assert w + d + l == pytest.approx(1.0)
    assert M.xpts(w, d) == pytest.approx(3 * w + d)
    assert M.outcome_probs([], []) == (0.0, 1.0, 0.0)  # no shots: 0-0 draw


def test_convolution_matches_brute_force_on_random_small_matches():
    rng = np.random.default_rng(7)
    for _ in range(10):
        a, b = rng.uniform(0, 1, rng.integers(0, 4)), rng.uniform(0, 1, rng.integers(0, 4))
        assert M.outcome_probs(a, b) == pytest.approx(_brute(a, b), abs=1e-12)


# ------------------------------------------------------------------ calibration, staking, mispriced runs
def test_brier_and_log_loss_hand_values():
    outcome = np.array([0, 1, 2])
    assert M.brier(np.eye(3), outcome) == 0.0
    assert M.brier(np.full((3, 3), 1 / 3), outcome) == pytest.approx(2 / 3)
    assert M.log_loss(np.full((3, 3), 1 / 3), outcome) == pytest.approx(math.log(3))
    assert M.brier(np.array([[0.5, 0.3, 0.2]]), np.array([0])) == pytest.approx(0.25 + 0.09 + 0.04)
    rel = M.reliability(np.array([[0.5, 0.3, 0.2], [0.1, 0.2, 0.7]]), np.array([0, 2]))
    assert sum(r["n"] for r in rel) == 6
    assert all(0 <= r["observed"] <= 1 for r in rel)


def test_mispriced_runs_fixture():
    pts = [3, 3, 3, 1, 1, 1, 0, 0, 0, 1, 1]
    df = pd.DataFrame({"pts": pts, "xpts_market": 1.5,
                       "kickoff_utc": pd.date_range("2024-01-01", periods=len(pts), freq="7D")})
    runs = M.mispriced_runs(df, window=3, threshold=4.0)
    assert [(r["direction"], r["start_idx"], r["end_idx"]) for r in runs] == [("beat", 0, 2), ("lagged", 6, 8)]
    assert runs[0]["diff"] == pytest.approx(4.5) and runs[1]["diff"] == pytest.approx(-4.5)
    assert runs[0]["points"] == 9 and runs[0]["market_points"] == pytest.approx(4.5)


def test_external_model_hook(tmp_path):
    assert M.load_external_model(tmp_path / "missing.csv") is None
    good = tmp_path / "m.csv"
    good.write_text("match_id,p_home,p_draw,p_away\n1,0.5,0.3,0.2\n2,0.2,0.3,0.5\n")
    m = pd.DataFrame({"match_id": [1, 2, 3], "is_home": [True, False, True]})
    out = M.attach_external_model(m, M.load_external_model(good))
    assert out.loc[0, ["mo_w", "mo_d", "mo_l"]].tolist() == [0.5, 0.3, 0.2]
    assert out.loc[1, ["mo_w", "mo_d", "mo_l"]].tolist() == [0.5, 0.3, 0.2]  # away: Liverpool = p_away
    assert out.loc[2, ["mo_w", "mo_d", "mo_l"]].isna().all()
    bad = tmp_path / "bad.csv"
    bad.write_text("match_id,p_home,p_draw,p_away\n1,0.5,0.5,0.5\n")
    with pytest.raises(ValueError):
        M.load_external_model(bad)


# ------------------------------------------------------------------ baseline
def test_baseline_seasons():
    m = pd.DataFrame({"season": [f"{s}-{(s + 1) % 100:02d}" for s in range(2014, 2020)] * 2})
    m["season_start"] = m.season.str[:4].astype(int)
    assert M.baseline_for(m, "2014-15") == (None, None)  # first season: none
    assert M.baseline_for(m, "all") == (None, None)
    b, label = M.baseline_for(m, "2015-16")
    assert set(b.season) == {"2014-15"} and "2014-15" in label
    b, label = M.baseline_for(m, "2018-19")
    assert set(b.season) == {"2016-17", "2017-18"} and "2016-17 to 2017-18" in label
    b, _ = M.baseline_for(m, "2018-19", n=3)
    assert set(b.season) == {"2015-16", "2016-17", "2017-18"}


# ------------------------------------------------------------------ real data
def _slugs():
    return [c for c in M.CLUBS if (PROCESSED / c / "matches.parquet").exists()] or list(M.CLUBS)


@pytest.fixture(scope="module", params=_slugs())
def enriched(request):
    """Every real-data test below runs once per club."""
    slug = request.param
    if not (PROCESSED / slug / "matches.parquet").exists():
        pytest.fail(f"run `python etl.py` first (no data for {slug})")
    e = M.enrich_matches(pd.read_parquet(PROCESSED / slug / "matches.parquet"), pd.read_parquet(PROCESSED / slug / "shots.parquet"),
                         M.CLUBS[slug]["canonical"], M.load_eras(slug))
    e.attrs["slug"] = slug
    return e


def test_real_season_xpts_within_2_of_understat(enriched):
    g = enriched.groupby("season")[["xpts_sim", "xpts_us"]].sum()
    assert (g.xpts_sim - g.xpts_us).abs().max() <= 2.0, g


def test_real_record_invariants(enriched):
    for season, d in enriched.groupby("season"):
        r = M.record(d)
        assert r["wins"] + r["draws"] + r["losses"] == r["matches"]
        assert r["points"] == 3 * r["wins"] + r["draws"]
        assert r["points"] == int(d.pts.sum()) and r["goals_for"] == int(d.gf.sum())
        assert r["goals_from_shots"] <= r["goals_for"]
    p = enriched[["mp_w", "mp_d", "mp_l", "mp_proportional_w", "mp_shin_w"]]
    assert ((p > 0) & (p < 1)).all().all()                       # de-vigged market probabilities are strictly inside (0, 1)
    q = enriched[["sim_w", "sim_d", "sim_l"]]
    assert ((q >= 0) & (q <= 1)).all().all()                     # an exact xG simulation is 0 when a side had no shots (a dominant side cannot lose)
    assert (enriched[["sim_w", "sim_d", "sim_l"]].sum(axis=1) - 1).abs().max() < 1e-9
    assert (enriched[["mp_w", "mp_d", "mp_l"]].sum(axis=1) - 1).abs().max() < 1e-9
    assert (enriched.xpts_market - (3 * enriched.mp_w + enriched.mp_d)).abs().max() < 1e-12
    eras = {e["manager"] for e in M.load_eras(enriched.attrs["slug"])}
    unknown = enriched[~enriched.era.isin(eras)]
    assert unknown.empty, f"{len(unknown)} matches outside every era (first: {unknown.kickoff_utc.min()}): add the new manager to config/eras/{enriched.attrs['slug']}.json"


def test_real_pnl_matches_definition(enriched):
    won = enriched.result == "W"
    assert np.allclose(enriched.pnl[won], enriched.odds_win[won] - 1)
    assert (enriched.pnl[~won] == -1).all()


# ------------------------------------------------------------------ takeaways
def _selections(e):
    yield "all", "all", e
    for season, d in e.groupby("season"):
        yield season, "all", d
    for era, d in e.groupby("era"):
        yield "all", era, d


def _check(tk, sel):
    """Recompute every cited number for one takeaway from the data."""
    f, t = tk["facts"], tk["text"]
    sel = sel.sort_values("kickoff_utc")
    r = M.record(sel)
    if tk["id"] == "form":
        last = sel.tail(f["n"])
        assert f["points_last"] == int(last.pts.sum())
        assert (f["wins"], f["draws"], f["losses"]) == tuple(int((last.result == x).sum()) for x in "WDL")
        assert f"{f['points_last']} points from the last {f['n']}" in t
        if tk["sentiment"] == "positive":
            assert f["points_last"] >= 7, "positive form claimed with < 7 points from last five"
        else:
            assert f["points_last"] <= 4 and tk["sentiment"] == "negative"
    elif tk["id"] == "finishing":
        d = r["goals_minus_xg"]
        assert f["goals"] == r["goals_from_shots"] and f["xg"] == round(r["xg"], 1) and f["diff"] == round(abs(d), 1)
        assert tk["sentiment"] == ("positive" if d > 0 else "negative")
        assert (("above" in t) == (d > 0)) and f"{abs(d):.1f}" in t and f"{r['xg']:.1f}" in t
    elif tk["id"] == "market":
        d = r["points_minus_market"]
        assert f["points"] == r["points"] and f["market_points"] == round(r["xpts_market"], 1)
        assert f["diff"] == round(abs(d), 1) and tk["sentiment"] == ("positive" if d > 0 else "negative")
        assert ("ahead of" in t) == (d > 0) and f"{abs(d):.1f}" in t
    elif tk["id"] == "xpts":
        d = r["points_minus_xpts"]
        assert f["points"] == r["points"] and f["xpts"] == round(r["xpts_sim"], 1) and f["diff"] == round(abs(d), 1)
        assert tk["sentiment"] == ("positive" if d > 0 else "negative") and (("above" in t) == (d > 0))
    else:
        raise AssertionError(tk["id"])


def test_takeaways_consistent_with_data(enriched):
    st = M.season_table(enriched)
    fired = set()
    for season, era, sel in _selections(enriched):
        base, label = M.baseline_for(enriched, season)
        for tk in M.takeaways(sel, base, label, st, season if era == "all" and season != "all" else None):
            fired.add(tk["id"])
            if tk["id"].startswith("baseline_"):
                mid = tk["id"][len("baseline_"):]
                cur, b = M.rate_value(sel, mid), M.rate_value(base, mid)
                ch = M.change(cur, b, mid)
                assert tk["facts"] == {"value": round(cur, 2), "baseline": round(b, 2), "pct": round(abs(ch["value"]) * 100)}
                assert f"{cur:.2f}" in tk["text"] and f"{b:.2f}" in tk["text"] and label in tk["text"]
                assert tk["sentiment"] == ("positive" if ch["sentiment"] == "good" else "negative")
            elif tk["id"].startswith("extreme_"):
                mid = "xga_pm" if "xga" in tk["id"] else "xg_pm"
                cur = st.loc[season, mid]
                assert tk["facts"]["value"] == round(cur, 2) and f"{cur:.2f}" in tk["text"]
                lower = tk["id"].endswith("lowest")
                prior = st[st.index < season]
                since = tk["facts"]["since"]
                ok = (lambda v: v <= cur) if lower else (lambda v: v >= cur)
                if since is None:
                    assert not prior[mid].map(ok).any()
                else:  # every season after `since` is strictly less extreme; `since` itself is not
                    assert ok(prior.loc[since, mid])
                    assert not prior[prior.index > since][mid].map(ok).any()
            else:
                _check(tk, sel)
    assert {"form", "finishing", "market", "xpts"} <= fired  # rules actually exercised on real data


def test_form_statement_never_positive_below_seven_points(enriched):
    """Slide through every season match by match; forcing the last five to 6 points must not yield positive form."""
    for season, d in enriched.groupby("season"):
        d = d.sort_values("kickoff_utc")
        for n in range(5, len(d) + 1):
            sel = d.iloc[:n]
            forms = [t for t in M.takeaways(sel) if t["id"] == "form"]
            pts = int(sel.tail(5).pts.sum())
            if pts < 7:
                assert not any(t["sentiment"] == "positive" for t in forms), (season, n, pts)
            if pts >= 10:
                assert forms and forms[0]["sentiment"] == "positive"
            if 3 < pts < 10:
                assert not forms, "no filler between the thresholds"


def test_small_sample_suppresses_significance_takeaways(enriched):
    d = enriched[enriched.season == "2019-20"].sort_values("kickoff_utc").iloc[:9]
    assert M.is_small_sample(len(d)) and not M.is_small_sample(10)
    assert {t["id"] for t in M.takeaways(d)} <= {"form"}
    assert M.takeaways(d.iloc[:0]) == []


def test_no_rule_fires_gives_empty_list(enriched):
    d = enriched[enriched.season == "2026-27"].sort_values("kickoff_utc")  # 5 matches, mixed form
    if 3 < int(d.tail(5).pts.sum()) < 10:
        assert M.takeaways(d) == []


def test_takeaway_tile_fields_match_the_sentence(enriched):
    """The tile fields (tag, headline, direction, good, bars) present the same numbers as the sentence."""
    import re
    st = M.season_table(enriched)
    checked, ids = 0, set()
    for season, era, sel in _selections(enriched):
        base, label = M.baseline_for(enriched, season)
        for tk in M.takeaways(sel, base, label, st, season if era == "all" and season != "all" else None):
            checked += 1
            i, f, text = tk["id"], tk["facts"], tk["text"]
            ids.add(i.split("_")[0])
            assert {"tag", "headline", "direction", "good", "bars"} <= set(tk)
            assert tk["good"] == (tk["sentiment"] == "positive") and tk["direction"] in ("up", "down")
            assert len(tk["bars"]) == 2 and all(isinstance(b["value"], (int, float)) and b["label"] for b in tk["bars"])
            nums = set(re.findall(r"\d+(?:\.\d+)?", text))
            head = re.sub(r"[^\d.]", "", tk["headline"])  # magnitude of the big number
            signed = tk["headline"][0] in "+−"
            up = tk["direction"] == "up"
            v = [b["value"] for b in tk["bars"]]
            if i == "form":
                assert head == str(f["points_last"]) and head in nums and v == [f["points_last"], 3 * f["n"]]
                assert up == ("Strong" in text)
            elif i in ("finishing", "market", "xpts"):
                assert signed and (tk["headline"][0] == "+") == up and head == f"{f['diff']:.1f}" and head in nums
                word = {"finishing": "above", "market": "ahead of", "xpts": "above"}[i]
                assert (word in text) == up
                keys = {"finishing": ("goals", "xg"), "market": ("points", "market_points"), "xpts": ("points", "xpts")}[i]
                assert v == [f[keys[0]], f[keys[1]]]
                assert str(f[keys[0]]) in nums and f"{f[keys[1]]:.1f}" in nums
            elif i.startswith("baseline_"):
                assert tk["headline"].endswith("%") and signed and head == str(f["pct"]) and head in nums
                assert (tk["headline"][0] == "+") == up == ("higher" in text)
                assert v == [f["value"], f["baseline"]] and f"{f['value']:.2f}" in nums and f"{f['baseline']:.2f}" in nums
            else:  # extreme_*: headline is the value; the comparison is read from the season table
                assert not signed and head == f"{f['value']:.2f}" and head in nums and v[0] == f["value"]
                mid = "xga_pm" if "xga" in i else "xg_pm"
                prior = st[st.index < season]
                exp = float(st.loc[f["since"], mid]) if f["since"] else float(prior[mid].max() if i.endswith("highest") else prior[mid].min())
                assert v[1] == round(exp, 2) and up == i.endswith("highest")
    assert checked > 50 and {"form", "finishing", "market", "xpts", "baseline", "extreme"} <= ids


def test_rate_panel_null_for_first_season_and_no_zero_percent(enriched):
    sel = M.select(enriched, "2014-15")
    base, _ = M.baseline_for(enriched, "2014-15")
    panel = M.rate_panel(sel, base)
    assert all(v["change"]["kind"] is None and v["baseline"] is None for v in panel.values())
    sel = M.select(enriched, "2019-20")
    base, _ = M.baseline_for(enriched, "2019-20")
    panel = M.rate_panel(sel, base)
    assert panel["xga_pm"]["change"]["kind"] == "pct"
    cur, b = panel["xga_pm"]["value"], panel["xga_pm"]["baseline"]
    assert panel["xga_pm"]["change"]["value"] == pytest.approx((cur - b) / b)
    # no metric shows a change against a zero/None baseline
    for k, v in panel.items():
        if v["baseline"] in (None, 0):
            assert v["change"]["kind"] is None, k
