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


# ------------------------------------------------------------------ M6: market payload and methodology generation
def test_market_payload_matches_independent_calculation(payload, tables):
    import numpy as np
    for key in ("2024-25|all", "all|all", "2019-20|all", "all|Arne Slot"):
        sel = payload["selections"][key]
        season, era = key.split("|")
        e = tables["enriched"]
        d = e[(e.season == season) if season != "all" else slice(None)]
        if era != "all":
            d = d[d.era == era]
        inv = 1 / d[["mkt_h", "mkt_d", "mkt_a"]].values
        prop = inv / inv.sum(axis=1, keepdims=True)
        P = np.where(d.is_home.values[:, None], prop, prop[:, ::-1])
        o = d.result.map({"W": 0, "D": 1, "L": 2}).values
        assert sel["cal"]["market"]["brier"] == pytest.approx(float(((P - np.eye(3)[o]) ** 2).sum(axis=1).mean()), abs=1e-9)
        assert sel["cal"]["market"]["log_loss"] == pytest.approx(float(-np.log(P[np.arange(len(o)), o]).mean()), abs=1e-9)
        assert sel["cal"]["market"]["n"] == len(d) == sel["cal"]["n"]
        assert sel["cum_market"][-1] == pytest.approx(float(d.pts.sum() - d.xpts_market.sum()), abs=2e-3)
        assert sel["cum_xpts"][-1] == pytest.approx(float(d.pts.sum() - d.xpts_sim.sum()), abs=2e-3)
        assert sel["cum_pnl"][-1] == pytest.approx(sel["record"]["pnl"], abs=2e-3)
        won = d.result == "W"
        assert sel["record"]["pnl"] == pytest.approx(float((d.odds_win[won] - 1).sum() - (~won).sum()), abs=1e-9)
        assert len(sel["cum_market"]) == len(sel["cum_pnl"]) == sel["n"]
        for r in sel["runs"]:
            assert 0 <= r["start_idx"] < r["end_idx"] < sel["n"] and r["n"] == r["end_idx"] - r["start_idx"] + 1
            assert abs(r["diff"]) >= 4.0 - 1e-9 or r["n"] > 10  # merged runs can be longer, single windows meet the threshold
        rel = sel["cal"]["market"]["reliability"]
        assert sum(b["n"] for b in rel) == 3 * len(d)
        assert all(0 <= b["observed"] <= 1 and 0 <= b["mean_p"] <= 1 for b in rel)


def test_calibration_reference_constants_in_registry_text():
    import math
    desc = {k: v["description"] for k, v in build.M.REGISTRY.items()}
    assert f"{2 / 3:.3f}" in desc["brier_market"] and f"{math.log(3):.3f}" in desc["logloss_market"]


def test_markdown_converter():
    html, toc = build.md_to_html("## Title {#my-id}\n\nSome **bold**, *italic*, `code` and [a link](https://x.org/a?b=1) with <b>tags</b>.\n\n- one\n- two\n\n{{thing}}\n\n### Sub\n\n```\nx < y\n```\n")
    assert '<h2 id="my-id">Title</h2>' in html and toc == [{"id": "my-id", "title": "Title"}]
    assert "<strong>bold</strong>" in html and "<em>italic</em>" in html and "<code>code</code>" in html
    assert '<a href="https://x.org/a?b=1" rel="noopener">a link</a>' in html
    assert "&lt;b&gt;tags&lt;/b&gt;" in html and "<b>" not in html  # raw HTML is escaped
    assert "<ul><li>one</li><li>two</li></ul>" in html and '<div data-gen="thing"></div>' in html
    assert "<h3 id=\"sub\">Sub</h3>" in html and "<pre><code>x &lt; y</code></pre>" in html


def test_methodology_payload_is_complete(payload, tables):
    meth = payload["methodology"]
    assert 'data-gen="' not in meth["html"] and "{{" not in meth["html"]
    ids = _re_findall(r'data-metric-id="([^"]+)"', meth["html"])
    assert sorted(ids) == sorted(build.M.REGISTRY)  # every registry metric appears exactly once
    assert [t["id"] for t in meth["toc"]][-1] == "accuracy" and len(meth["toc"]) >= 10
    # the worked de-vig example uses a real Pinnacle-priced match and its rows sum to 100%
    assert 'data-example="devig"' in meth["html"] and "100.00%" in meth["html"]


def _re_findall(pattern, text):
    import re
    return re.findall(pattern, text)


# ------------------------------------------------------------------ Redesign: crest inlining
def test_crest_is_resized_optimised_and_inlined(tmp_path):
    import base64
    import io

    from PIL import Image
    src = tmp_path / "crest.png"
    im = Image.new("RGBA", (340, 588), (0, 0, 0, 0))
    for x in range(100, 240):
        for y in range(100, 500):
            im.putpixel((x, y), (200, 16, 46, 255))
    im.save(src)
    html = build.crest_html(src)
    assert html.startswith('<img class="crest" src="data:image/png;base64,') and 'alt="Liverpool FC crest"' in html
    out = Image.open(io.BytesIO(base64.b64decode(html.split("base64,")[1].split('"')[0])))
    assert out.height == 192 and abs(out.width - round(340 * 192 / 588)) <= 1 and out.mode == "RGBA"  # aspect ratio kept
    assert out.getpixel((0, 0))[3] == 0                                                              # transparency kept
    assert len(html) < src.stat().st_size * 4 / 3 + 500                                              # smaller than the original
    assert build.crest_html(tmp_path / "missing.png") == '<span class="brand-mark" aria-hidden="true"></span>'


def test_supplied_crest_has_a_transparent_background():
    from PIL import Image
    if not build.CREST.exists():
        pytest.skip("no crest supplied")
    im = Image.open(build.CREST).convert("RGBA")
    corners = [im.getpixel(xy)[3] for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1))]
    assert corners == [0, 0, 0, 0], "the crest has an opaque background: tell the owner rather than cutting it out"
