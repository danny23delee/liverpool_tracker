"""Processed data -> dashboard JSON -> inlined into template/index.html -> dist/index.html.

Every number the dashboard shows is computed here (via metrics.py) and only formatted in the browser,
so there is exactly one implementation of each calculation.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import math
import re as _re
from pathlib import Path

import numpy as np
import pandas as pd

import metrics as M
import style

ROOT = Path(__file__).parent
PROCESSED = ROOT / "data" / "processed"
TEMPLATE = ROOT / "template" / "index.html"
CREST = ROOT / "assets" / "crest.png"
CREST_MAX_HEIGHT = 192  # px; the sidebar shows it at about 46 x 54 px
DIST = ROOT / "dist" / "index.html"
MAX_BYTES = 5 * 1024 * 1024


# ------------------------------------------------------------------ JSON helpers
def clean(o):
    """Recursively make an object strictly JSON-safe: numpy -> python, NaN/inf -> None, Timestamp -> iso."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


def r4(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)


def r6(x):
    """Six decimals for values that are displayed and re-added in the browser (xG, probabilities), so the
    rounding of the payload can never change a displayed digit."""
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 6)


# ------------------------------------------------------------------ payload
def match_rows(e: pd.DataFrame) -> list[dict]:
    rows = []
    for r in e.itertuples():
        rows.append({
            "id": int(r.match_id), "d": r.fd_date.strftime("%Y-%m-%d"), "ko": r.kickoff_utc.isoformat(),
            "s": r.season, "era": r.era, "opp": r.opponent, "h": bool(r.is_home), "gf": int(r.gf), "ga": int(r.ga),
            "r": r.result, "pts": int(r.pts), "xg": r6(r.xg), "xga": r6(r.xga), "xpts": r6(r.xpts_sim),
            "xptm": r6(r.xpts_market), "sim": [r6(r.sim_w), r6(r.sim_d), r6(r.sim_l)],
            "mkt": [r6(r.mp_w), r6(r.mp_d), r6(r.mp_l)], "src": r.mkt_source, "odds": r6(r.odds_win),
            "ppda": r4(r.ppda_att / r.ppda_def) if r.ppda_def > 0 else None, "deep": int(r.deep), "deepa": int(r.deep_allowed),
            "sh": int(r.shots_for), "sha": int(r.shots_against),
            "mktp": {"proportional": [r6(r.mp_proportional_w), r6(r.mp_proportional_d), r6(r.mp_proportional_l)],
                     "shin": [r6(r.mp_shin_w), r6(r.mp_shin_d), r6(r.mp_shin_l)]},
            "o": [r4(r.mkt_h), r4(r.mkt_d), r4(r.mkt_a)], "ovr": r4(r.overround), "shin_z": r4(M.shin_z([r.mkt_h, r.mkt_d, r.mkt_a])),
        })
    return rows


RES_CODES = ["Goal", "SavedShot", "BlockedShot", "MissedShots", "ShotOnPost", "OwnGoal"]
SIT_CODES = ["OpenPlay", "FromCorner", "SetPiece", "DirectFreekick", "Penalty"]
TYP_CODES = ["RightFoot", "LeftFoot", "Head", "OtherBodyPart"]
SOURCE_OF = {"OpenPlay": "open", "FromCorner": "set", "SetPiece": "set", "DirectFreekick": "set", "Penalty": "pen"}


def shots_payload(e: pd.DataFrame, shots: pd.DataFrame) -> dict:
    """All shots in Liverpool matches, both teams, as compact rows (coordinates are the shooter's view)."""
    mi = {int(m): i for i, m in enumerate(e.match_id)}
    names = shots.drop_duplicates("player_id").set_index("player_id").player
    pids = sorted(shots.player_id.unique())
    pidx = {int(p): i for i, p in enumerate(pids)}
    rows = []
    for r in shots.sort_values(["match_id", "minute", "shot_id"]).itertuples():
        rows.append([mi[int(r.match_id)], int(r.minute), round(r.x, 3), round(r.y, 3), round(r.xg, 6),
                     RES_CODES.index(r.result), SIT_CODES.index(r.situation), TYP_CODES.index(r.shot_type),
                     pidx[int(r.player_id)], 1 if r.team == M.LIV else 0])
    return {"cols": ["mi", "min", "x", "y", "xg", "res", "sit", "typ", "pl", "fl"],
            "codes": {"res": RES_CODES, "sit": SIT_CODES, "typ": TYP_CODES},
            "players": [str(names[p]) for p in pids], "rows": rows}


def league_payload(ts: pd.DataFrame, tm: pd.DataFrame) -> dict:
    """League context per season: each club's shots per match / xG per shot (shot level, own-goal
    pseudo-shots removed) and league averages used as reference lines."""
    out = {}
    tm = tm.assign(ppda=tm.ppda_att / tm.ppda_def.where(tm.ppda_def > 0))
    for season, g in ts.groupby("season"):
        teams = [{"team": r.team, "shots_pm": r4(r.shots_shotlevel / r.matches), "xgps": r4(r.xg_shotlevel / r.shots_shotlevel),
                  "xg_pm": r4(r.xg_shotlevel / r.matches), "n": int(r.matches)} for r in g.itertuples()]
        t = tm[tm.season == season]
        top = g.sort_values("pts", ascending=False)
        # champion: only when every club has played a full 38 and the leader is strictly ahead on points (a tie leaves it unset)
        champion = top.team.iat[0] if (g.matches >= 38).all() and len(top) > 1 and top.pts.iat[0] > top.pts.iat[1] else None
        out[season] = {"champion": champion, "teams": teams, "avg": {
            "shots_pm": r4(g.shots_shotlevel.sum() / g.matches.sum()), "xgps": r4(g.xg_shotlevel.sum() / g.shots_shotlevel.sum()),
            "xg_pm": r4(g.xg_shotlevel.sum() / g.matches.sum()), "ppda": r4(t.ppda.mean()), "deep_pm": r4(t.deep.mean())}}
    return out


def player_rows(rosters: pd.DataFrame, shots: pd.DataFrame, ids: set) -> list[dict]:
    """Liverpool player totals over the given matches (rosters for minutes/assist stats, shots for npxG)."""
    r = rosters[(rosters.team == M.LIV) & rosters.match_id.isin(ids)]
    g = r.groupby(["player_id", "player"], as_index=False).agg(
        pos=("position", lambda x: x[x != "Sub"].mode().iat[0] if (x != "Sub").any() else "Sub"),
        apps=("minutes", lambda x: int((x > 0).sum())), min=("minutes", "sum"), g=("goals", "sum"), og=("own_goals", "sum"),
        ast=("assists", "sum"), xa=("xa", "sum"), kp=("key_passes", "sum"), chain=("xgchain", "sum"), build=("xgbuildup", "sum"))
    s = shots[(shots.team == M.LIV) & shots.match_id.isin(ids) & (shots.result != "OwnGoal")]
    a = s.groupby("player_id").agg(sh=("xg", "size"), xg=("xg", "sum"))
    n = s[s.situation != "Penalty"].groupby("player_id").agg(npxg=("xg", "sum"), npg=("result", lambda x: int((x == "Goal").sum())))
    g = g.merge(a, on="player_id", how="left").merge(n, on="player_id", how="left").fillna({"sh": 0, "xg": 0, "npxg": 0, "npg": 0})
    g = g[g["min"] > 0].sort_values("min", ascending=False)
    p90 = lambda v, mins: r4(v / mins * 90.0)  # minutes > 0 is guaranteed by the filter above
    return [{"id": int(x.player_id), "name": x.player, "pos": x.pos, "apps": int(x.apps), "min": int(x.min), "g": int(x.g),
             "npg": int(x.npg), "xg": r4(x.xg), "npxg": r4(x.npxg), "sh": int(x.sh), "ast": int(x.ast), "xa": r4(x.xa),
             "kp": int(x.kp), "chain": r4(x.chain), "build": r4(x.build), "fin": r4(x.npg - x.npxg),
             "xg90": p90(x.xg, x.min), "npxg90": p90(x.npxg, x.min), "xa90": p90(x.xa, x.min), "build90": p90(x.build, x.min),
             "chain90": p90(x.chain, x.min), "sh90": p90(x.sh, x.min), "kp90": p90(x.kp, x.min)} for x in g.itertuples()]


def mix_payload(sel: pd.DataFrame, shots: pd.DataFrame, season: str) -> dict:
    """xG / shots / goals by source (open play, set piece, penalty) for Liverpool and against, per season
    when the selection spans all seasons, else one group for the selection."""
    s = shots[shots.result != "OwnGoal"]
    groups = [(k, g) for k, g in sel.groupby("season")] if season == "all" else [("Selection", sel)]
    res = {"for": [], "against": []}
    for label, g in groups:
        sub = s[s.match_id.isin(set(g.match_id))]
        for side, mask in (("for", sub.team == M.LIV), ("against", sub.team != M.LIV)):
            d = sub[mask]
            src = d.situation.map(SOURCE_OF)
            row = {"label": label, "n": int(len(g))}
            for k in ("open", "set", "pen"):
                x = d[src == k]
                row[k] = {"xg": r4(x.xg.sum()), "shots": int(len(x)), "goals": int((x.result == "Goal").sum())}
            res[side].append(row)
    return res


def lfc_points(sel: pd.DataFrame, season: str) -> list[dict]:
    groups = [(k, g) for k, g in sel.groupby("season")] if season == "all" else [(season, sel)]
    return [{"label": k, "n": int(len(g)), "shots_pm": r4(g.shots_for.mean()), "xgps": r4(g.xg.sum() / g.shots_for.sum()),
             "xg_pm": r4(g.xg.mean())} for k, g in groups]


def selection_payload(e: pd.DataFrame, sel: pd.DataFrame, season: str, era: str, seasons_tbl: pd.DataFrame,
                      shots: pd.DataFrame, rosters: pd.DataFrame) -> dict:
    base, base_label = M.baseline_for(e, season)
    rec = M.record(sel)
    xgd = (sel.xg - sel.xga).reset_index(drop=True)
    roll = xgd.rolling(10, min_periods=10).mean()
    season_all = e[e.season == season] if season != "all" else e
    out = {
        "n": int(len(sel)), "idx": [int(i) for i in sel.index], "record": rec,
        "rates": {mid: {"v": v["value"], "b": v["baseline"], "c": v["change"]} for mid, v in M.rate_panel(sel, base).items()},
        "baseline_label": base_label,
        "baseline_seasons": sorted(base.season.unique().tolist()) if base is not None else [],
        "small": M.is_small_sample(len(sel)),
        "takeaways": M.takeaways(sel, base, base_label, seasons_tbl,
                                 season if (era == "all" and season != "all") else None),
        "roll": [r4(v) for v in roll],
        **{f"roll_{k}": [r4(v) for v in col.reset_index(drop=True).rolling(10, min_periods=10).mean()]
           for k, col in (("xg", sel.xg), ("xga", sel.xga), ("ppda", sel.ppda_att / sel.ppda_def.where(sel.ppda_def > 0)),
                          ("deepa", sel.deep_allowed))},
        "players": player_rows(rosters, shots, set(sel.match_id)),
        "mix": mix_payload(sel, shots, season),
        "lfc_points": lfc_points(sel, season),
        **market_payload(sel),
        "last5": [int(i) for i in sel.sort_values("kickoff_utc").tail(M.SETTINGS["form_matches"]).index],
        "season_played": int(len(season_all)),
    }
    return out


def market_payload(sel: pd.DataFrame) -> dict:
    """Calibration, mispriced runs and cumulative series for the Market Lens page."""
    cal = M.calibration(sel)
    o = M.outcome_index(sel.result)
    freq = np.bincount(o, minlength=3) / len(o)
    naive = np.tile(freq, (len(o), 1))
    cal["naive"] = {"brier": M.brier(naive, o), "log_loss": M.log_loss(naive, o), "rates": [float(x) for x in freq]}
    runs = [{**r, "from": r["from"].strftime("%Y-%m-%d"), "to": r["to"].strftime("%Y-%m-%d")} for r in M.mispriced_runs(sel)]
    return {
        "cal": cal, "runs": runs,
        "cum_market": [r4(v) for v in (sel.pts - sel.xpts_market).cumsum()],
        "cum_xpts": [r4(v) for v in (sel.pts - sel.xpts_sim).cumsum()],
        "cum_pnl": [r4(v) for v in sel.pnl.cumsum()],
    }


# ---------------------------------------------------------------- methodology page (generated)
GLOSSARY_GROUPS = [
    ("Results (actual, whole numbers)", ["matches", "wins", "draws", "losses", "points", "goals_for", "goals_against", "goal_diff", "clean_sheets"]),
    ("Expected values", ["xg", "xga", "xgd", "npxg", "xpts_sim", "xpts_market", "goals_minus_xg", "points_minus_xpts", "points_minus_market"]),
    ("Per-match rates", ["points_pm", "goals_pm", "goals_against_pm", "xg_pm", "xga_pm", "xgd_pm", "xgd_roll10", "npxg_pm", "shots_pm", "shots_against_pm",
                         "xg_per_shot", "xg_per_shot_pooled", "ppda", "deep_pm", "deep_allowed_pm", "xg_openplay_pm", "xga_openplay_pm", "xga_setpiece_pm"]),
    ("Percentages and probabilities", ["win_rate", "clean_sheet_rate", "p_win_market", "p_win_sim", "overround", "odds_win"]),
    ("Market lens", ["brier_market", "brier_sim", "logloss_market", "logloss_sim", "brier_model", "logloss_model", "pnl", "roi", "season_points", "cum_market",
                     "mispriced_runs", "calibration", "reliability", "match_strip", "pnl_cum", "roi_season", "model_hook"]),
    ("Players", ["apps", "minutes", "player_goals", "npg", "assists", "xg_player", "npxg_player", "xa", "npg_minus_npxg", "xg_p90", "npxg_p90", "xa_p90",
                 "xgbuildup_p90", "xgchain_p90", "shots_p90", "key_passes_p90", "squad_table", "role_leaders", "player_trends", "player_compare"]),
    ("Charts and maps", ["shot_map", "shots_conceded_map", "vol_vs_quality", "xg_mix", "xga_mix", "xg_trend", "xga_trend", "xg_race", "match_shots", "match_probs"]),
]


def dmy(ts, month_fmt: str = "%b") -> str:
    """'5 Jan 2026' without the platform-specific %-d directive."""
    return f"{ts.day} {ts.strftime(month_fmt + ' %Y')}"


def esc(t) -> str:
    return _html.escape(str(t), quote=True)


def glossary_html() -> str:
    seen, parts = set(), []
    groups = [(t, [i for i in ids if i in M.REGISTRY]) for t, ids in GLOSSARY_GROUPS]
    scfg = style.load_config()
    groups.append(("Style of play (proxy scores, not good or bad)",
                   ["style_section", "style_phase_defence", "style_phase_buildup", "style_phase_attack"]
                   + [p["index_id"] for p in scfg["phases"].values()] + list(scfg["axes"]) + list(scfg["kpis"])))
    for _, ids in groups:
        seen.update(ids)
    rest = [i for i in M.REGISTRY if i not in seen]
    if rest:
        groups.append(("Other", rest))
    better = {True: "higher", False: "lower", None: "no judgement"}
    for title, ids in groups:
        rows = "".join(
            f'<tr data-metric-id="{esc(i)}"><td>{esc(M.REGISTRY[i]["label"])}</td><td>{esc(M.REGISTRY[i]["description"])}</td>'
            f'<td>{esc(M.REGISTRY[i]["unit"])}</td><td>{better[M.REGISTRY[i]["higher_is_better"]]}</td>'
            f'<td>{M.REGISTRY[i]["min_sample"]}</td><td>{esc(M.REGISTRY[i]["source"])}</td></tr>' for i in ids)
        parts.append(f'<details class="sub"><summary><h3>{esc(title)} <span class="cnt">({len(ids)})</span></h3></summary>'
                     '<div class="tbl-x"><table class="data glossary"><thead><tr><th>Metric</th><th>Definition</th><th>Unit</th>'
                     f'<th>Better when</th><th>Minimum sample</th><th>Source</th></tr></thead><tbody>{rows}</tbody></table></div></details>')
    return "".join(parts)


def sim_example_html() -> str:
    liv, opp = [0.30, 0.50], [0.20]
    dl, do = M.goal_dist(liv), M.goal_dist(opp)
    w, d, l = M.outcome_probs(liv, opp)
    rows = "".join(f"<tr><td>{k}</td><td>{dl[k] * 100:.1f}%</td><td>{(do[k] * 100 if k < len(do) else 0):.1f}%</td></tr>" for k in range(len(dl)))
    return ('<div class="example" data-example="sim"><p><strong>Worked example.</strong> Liverpool have two shots (xG 0.30 and 0.50) and the opposition one (xG 0.20). '
            'The probability of each goal count is:</p><div class="tbl-x"><table class="data"><thead><tr><th>Goals</th><th>Liverpool</th><th>Opposition</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div><p>Combining the two gives P(win) = {w * 100:.1f}%, P(draw) = {d * 100:.1f}%, P(loss) = {l * 100:.1f}%, '
            f'so xPts = 3 × {w:.4f} + {d:.4f} = <strong>{M.xpts(w, d):.2f}</strong>.</p></div>')


def devig_example_html(e: pd.DataFrame) -> str:
    ex = e[e.mkt_source == "pinnacle_close"].iloc[-1]
    odds = [ex.mkt_h, ex.mkt_d, ex.mkt_a]
    raw = [1 / o for o in odds]
    prop, shin, z = M.devig_proportional(odds), M.devig_shin(odds), M.shin_z(odds)
    home, away = (M.LIV, ex.opponent) if ex.is_home else (ex.opponent, M.LIV)
    names = [f"{home} win", "Draw", f"{away} win"]
    rows = "".join(f"<tr><td>{esc(n)}</td><td>{o:.2f}</td><td>{r * 100:.2f}%</td><td>{p * 100:.2f}%</td><td>{sh * 100:.2f}%</td></tr>"
                   for n, o, r, p, sh in zip(names, odds, raw, prop, shin))
    return (f'<div class="example" data-example="devig"><p><strong>Worked example</strong>: {esc(home)} v {esc(away)}, {dmy(ex.fd_date, "%b")} '
            f'(Pinnacle closing odds). The implied probabilities add up to {sum(raw) * 100:.2f}%, a margin of {(sum(raw) - 1) * 100:.2f}%.</p>'
            '<div class="tbl-x"><table class="data" data-table="devig-example"><thead><tr><th>Outcome</th><th>Decimal odds</th><th>Implied (1 ÷ odds)</th><th>Proportional</th><th>Shin</th></tr></thead>'
            f'<tbody>{rows}</tbody><tfoot><tr><td>Sum</td><td></td><td>{sum(raw) * 100:.2f}%</td><td>{prop.sum() * 100:.2f}%</td><td>{shin.sum() * 100:.2f}%</td></tr></tfoot></table></div>'
            f'<p>Shin insider share z = {z:.4f}.</p></div>')


def coverage_html(e: pd.DataFrame, shots: pd.DataFrame) -> str:
    src = e.mkt_source.value_counts().to_dict()
    label = {"pinnacle_close": "Pinnacle closing odds", "market_avg_close": "market-average closing odds", "betfair_exchange_close": "Betfair Exchange closing odds"}
    items = [
        f"<li><strong>Understat</strong> (Premier League, 2014/15 onwards): shot-level data with x/y coordinates, situation, shot type and xG for every shot; team match xG, xGA, PPDA and deep completions; player match statistics (minutes, goals, xA, key passes, xGChain, xGBuildup).</li>",
        f"<li><strong>football-data.co.uk</strong>: results and betting odds (Pinnacle open and close, market average and maximum, Betfair Exchange where present).</li>",
        f"<li><strong>Coverage</strong>: {len(e)} Liverpool matches across {e.season.nunique()} seasons ({e.season.iloc[0]} to {e.season.iloc[-1]}), {len(shots):,} shots including the opposition's, "
        f"data through {dmy(e.fd_date.max(), '%B')}. The current season is partial and is flagged wherever it has fewer than {M.SETTINGS['min_sample']} matches.</li>",
        "<li><strong>Price used for each match</strong>: " + "; ".join(f"{label.get(k, k)} for {v} matches" for k, v in src.items()) + ".</li>",
    ]
    return '<ul data-gen-list="coverage">' + "".join(items) + "</ul>"


def takeaway_rules_html() -> str:
    T = M.SETTINGS["takeaway_thresholds"]
    k = M.SETTINGS["form_matches"]
    rows = [
        ("Form", f"points from the last {k} matches are {T['form_strong_points']} or more (strong) or {T['form_poor_points']} or fewer (poor). Between those, nothing is said. A positive claim needs at least 7 points.", "any sample"),
        ("Finishing", f"goals scored by players minus xG is at least {T['finishing_goals']} goals in either direction.", f"{M.SETTINGS['min_sample']}+ matches"),
        ("Results vs market", f"actual points differ from market-expected points by at least {T['market_points']}.", f"{M.SETTINGS['min_sample']}+ matches"),
        ("Points vs xPts", f"actual points differ from xG-simulated points by at least {T['xpts_points']}.", f"{M.SETTINGS['min_sample']}+ matches"),
        ("Versus baseline", f"xG or xGA per match differs from the baseline by at least {T['baseline_pct'] * 100:.0f}%.", f"{M.SETTINGS['min_sample']}+ matches and a baseline"),
        ("Best since / worst on record", f"a season's xGA per match is the lowest (or xG the highest) since an earlier season, or its xGA is the highest in the data.", f"{T['lowest_since_min_prior_seasons']}+ earlier seasons"),
    ]
    body = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in rows)
    return f'<div class="tbl-x"><table class="data" data-table="takeaway-rules"><thead><tr><th>Rule</th><th>Fires when</th><th>Needs</th></tr></thead><tbody>{body}</tbody></table></div>'


def external_hook_html() -> str:
    p = M.SETTINGS["external_model_csv"]
    return ('<p>To score an external model next to the market and the xG simulation, save a CSV at '
            f'<code>{esc(p)}</code> and rebuild. Columns: <code>match_id</code> (the Understat id) <em>or</em> <code>date</code> (yyyy-mm-dd, the local match date) with '
            '<code>home_team</code> and <code>away_team</code> (Understat club names), plus <code>p_home</code>, <code>p_draw</code> and <code>p_away</code>. Each probability must be in [0, 1] '
            'and each row must sum to 1 (within 0.001); anything else stops the build. Matches without a row are simply not scored. The Market Lens page then adds the model to the '
            'calibration table and reliability plot.</p>')


def style_mapping_html() -> str:
    """The proxy mapping table, generated from config/style.json (and the registry labels), so it cannot drift."""
    cfg = style.load_config()
    rows = []
    for ph in cfg["phases"].values():
        for aid in ph["axes"]:
            a = cfg["axes"][aid]
            ks = [f'{esc(M.REGISTRY[k]["label"])} ({"higher" if cfg["kpis"][k]["sign"] > 0 else "lower"} pushes toward {esc(a["right"])})' for k in a["kpis"]]
            ext = [esc(M.REGISTRY[k]["label"]) for k in a["external_kpis"]]
            kp = "<br>".join(ks) if ks else "none"
            if ext:
                kp += "<br><em>Not computed (needs shot-level data for every club): " + "; ".join(ext) + "</em>"
            status = "Proxy" if a["status"] == "proxy" else "Unavailable: needs pass-level data"
            rows.append(f'<tr data-axis="{aid}"><td>{esc(ph["label"])}</td><td>{esc(a["left"])} ◄► {esc(a["right"])}</td><td>{kp}</td><td>{status}</td><td>{a["confidence"]}</td></tr>')
    return ('<div class="tbl-x"><table class="data" data-table="style-mapping"><thead><tr><th>Phase</th><th>Axis</th><th>KPIs (Understat team data)</th><th>Status</th><th>Confidence</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _inline(t: str) -> str:
    t = esc(t)
    t = _re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = _re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", t)
    t = _re.sub(r"\[([^\]]+)\]\((#[\w-]+)\)", r'<a href="\2" data-jump="\2">\1</a>', t)
    t = _re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', t)
    return t


def md_to_html(md: str) -> tuple[str, list[dict]]:
    """A deliberately small markdown converter: ## / ### headings ({#id} optional), paragraphs, lists,
    fenced code, **bold**, *italic*, `code`, links, and {{name}} block placeholders. Every ## section is
    wrapped in a collapsible <details class="sec"> whose <summary> holds the heading; only the first
    section starts open."""
    out, toc, i, lines = [], [], 0, md.splitlines()
    para: list[str] = []
    in_section = False

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            flush(); i += 1; continue
        m = _re.match(r"(#{2,3}) (.+?)(?: \{#([\w-]+)\})?$", ln)
        if m:
            flush()
            title, hid = m.group(2), m.group(3) or _re.sub(r"[^a-z0-9]+", "-", m.group(2).lower()).strip("-")
            level = len(m.group(1))
            if level == 2:
                if in_section:
                    out.append("</details>")
                out.append(f'<details class="sec"{" open" if not toc else ""}><summary><h2 id="{hid}">{_inline(title)}</h2></summary>')
                in_section = True
                toc.append({"id": hid, "title": title})
            else:
                out.append(f'<h{level} id="{hid}">{_inline(title)}</h{level}>')
            i += 1; continue
        if ln.strip().startswith("```"):
            flush(); j = i + 1; buf = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                buf.append(lines[j]); j += 1
            out.append("<pre><code>" + esc("\n".join(buf)) + "</code></pre>"); i = j + 1; continue
        m = _re.fullmatch(r"\{\{(\w+)\}\}", ln.strip())
        if m:
            flush(); out.append(f'<div data-gen="{m.group(1)}"></div>'); i += 1; continue
        if ln.lstrip().startswith("- "):
            flush(); items = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(lines[i].lstrip()[2:]); i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>"); continue
        para.append(ln.strip()); i += 1
    flush()
    if in_section:
        out.append("</details>")
    return "\n".join(out), toc


def methodology_payload(e: pd.DataFrame, shots: pd.DataFrame) -> dict:
    src = (ROOT / "template" / "methodology.md").read_text(encoding="utf-8")
    d = pd.concat([e.xg - e.xg_reported, e.xga - e.xga_reported])
    rect = style.load_config()["central_rectangle"]
    inline = {"xg_gap_share": f"{(d > 0.02).mean() * 100:.0f}%", "xg_gap_max": f"{d.max():.1f}", "rect_x": f"{rect['x_min']}", "rect_y": f"{rect['y_half_width']}"}
    for k, v in inline.items():
        src = src.replace("{{" + k + "}}", v)
    html, toc = md_to_html(src)
    gen = {"coverage": coverage_html(e, shots), "registry_glossary": glossary_html(), "sim_example": sim_example_html(),
           "devig_example": devig_example_html(e), "takeaway_rules": takeaway_rules_html(), "external_hook": external_hook_html(), "style_mapping": style_mapping_html()}
    for k, v in gen.items():
        token = f'<div data-gen="{k}"></div>'
        assert token in html, f"placeholder {k} missing from methodology.md"
        html = html.replace(token, v)
    assert 'data-gen="' not in html, "unresolved placeholder in methodology.md"
    return {"html": html, "toc": toc}


def style_payload(style_raw: pd.DataFrame | None) -> dict | None:
    """Style-of-play scores for the dashboard (aggregates only). Reads the optional external KPI CSV silently."""
    if style_raw is None:
        style_raw = pd.read_parquet(PROCESSED / "style_raw.parquet")
    cfg = style.load_config()
    ext = style.load_external(None, cfg, style_raw)
    liv = style_raw[style_raw.club == "Liverpool"].set_index("season").matches.to_dict()
    return style.payload(style.compute(style_raw, cfg, ext), cfg, matches={k: int(v) for k, v in liv.items()})


def dashboard(e: pd.DataFrame, fixtures: pd.DataFrame, shots: pd.DataFrame, rosters: pd.DataFrame,
              ts: pd.DataFrame, tm: pd.DataFrame, style_raw: pd.DataFrame | None = None) -> dict:
    seasons = sorted(e.season.unique())
    eras = [x["manager"] for x in M.ERAS if (e.era == x["manager"]).any()]
    st = M.season_table(e)
    selections = {}
    for season in ["all"] + seasons:
        for era in ["all"] + eras:
            sel = M.select(e, season, era)
            if len(sel):
                selections[f"{season}|{era}"] = selection_payload(e, sel, season, era, st, shots, rosters)
    counted = e.groupby("season").size()
    default = next(s for s in reversed(seasons) if counted[s] >= M.SETTINGS["min_sample"])
    scheduled = fixtures.groupby("season").size().to_dict()
    return {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "data_through": e.fd_date.max().strftime("%Y-%m-%d"),
        "seasons": seasons, "default_season": default, "scheduled": scheduled,
        "eras": [x for x in M.ERAS if x["manager"] in eras],
        "settings": {"mispriced": M.SETTINGS["mispriced"], "min_sample": M.SETTINGS["min_sample"], "baseline_seasons": M.SETTINGS["baseline_seasons"],
                     "devig_method": M.SETTINGS["devig_method"], "form_matches": M.SETTINGS["form_matches"]},
        "registry": M.REGISTRY,
        "matches": match_rows(e),
        "methodology": methodology_payload(e, shots),
        "shots": shots_payload(e, shots),
        "league": league_payload(ts, tm),
        "style": style_payload(style_raw),
        "selections": selections,
    }


def load_tables() -> dict:
    t = {n: pd.read_parquet(PROCESSED / f"{n}.parquet") for n in
         ("matches", "fixtures", "shots", "rosters", "team_seasons", "team_matches")}
    t["enriched"] = M.enrich_matches(t["matches"], t["shots"])
    return t


# ------------------------------------------------------------------ render
def crest_html(path: Path = CREST) -> str:
    """The sidebar crest: the supplied PNG resized (Pillow, aspect ratio kept, at most 192 px tall), optimised and
    inlined as a base64 data URI so the deploy stays one file. If the file is missing, a red bar. The image is
    used in this one place only (never as favicon, background or share image)."""
    fallback = '<span class="brand-mark" aria-hidden="true"></span>'
    if not Path(path).exists():
        return fallback
    import base64
    import io

    from PIL import Image

    im = Image.open(path).convert("RGBA")
    corners = [im.getpixel(xy)[3] for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1))]
    if min(corners) > 0:
        print(f"WARNING: {path} has an opaque background (corner alpha {corners}); it is used as supplied, please check it")
    if im.height > CREST_MAX_HEIGHT:
        im = im.resize((round(im.width * CREST_MAX_HEIGHT / im.height), CREST_MAX_HEIGHT), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img class="crest" src="{uri}" alt="Liverpool FC crest" width="46" height="54">'


def render(data: dict, crest_path: Path = CREST) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "__CREST__" in html
    html = html.replace("__CREST__", crest_html(crest_path))
    blob = json.dumps(clean(data), allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    blob = blob.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    assert "__DATA_JSON__" in html
    return html.replace("__DATA_JSON__", blob)


def build() -> Path:
    t = load_tables()
    data = dashboard(t["enriched"], t["fixtures"], t["shots"], t["rosters"], t["team_seasons"], t["team_matches"])
    html = render(data)
    DIST.parent.mkdir(parents=True, exist_ok=True)
    DIST.write_text(html, encoding="utf-8")
    size = DIST.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"dist/index.html is {size / 1e6:.2f} MB, over the 5 MB budget")
    print(f"wrote {DIST} ({size / 1024:.0f} KB, {len(data['selections'])} selections)")
    return DIST


if __name__ == "__main__":
    build()
