"""Processed data -> dashboard JSON -> inlined into template/index.html -> dist/index.html.

Every number the dashboard shows is computed here (via metrics.py) and only formatted in the browser,
so there is exactly one implementation of each calculation.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import math
import os
import re as _re
from pathlib import Path

import numpy as np
import pandas as pd

import metrics as M
import style

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("TRACKER_DATA", ROOT / "data"))
PROCESSED = DATA / "processed"
TEMPLATE = ROOT / "template" / "index.html"
CREST = ROOT / "assets" / "crest.png"          # Liverpool's crest (kept at its original path)
CREST_MAX_HEIGHT = 192  # px; the sidebar shows it at about 46 x 54 px
CREST_MENU_HEIGHT = 64  # px; the thumbnail in the club switcher
DIST_DIR = Path(os.environ.get("TRACKER_DIST", ROOT / "dist"))
DIST = DIST_DIR / "index.html"                  # the default club (Liverpool) lives at the site root; the others in sub-folders
CLUBS = M.CLUBS
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


def shots_payload(e: pd.DataFrame, shots: pd.DataFrame, club: str = M.LIV) -> dict:
    """All shots in the club's matches, both teams, as compact rows (coordinates are the shooter's view)."""
    mi = {int(m): i for i, m in enumerate(e.match_id)}
    names = shots.drop_duplicates("player_id").set_index("player_id").player
    pids = sorted(shots.player_id.unique())
    pidx = {int(p): i for i, p in enumerate(pids)}
    rows = []
    for r in shots.sort_values(["match_id", "minute", "shot_id"]).itertuples():
        rows.append([mi[int(r.match_id)], int(r.minute), round(r.x, 3), round(r.y, 3), round(r.xg, 6),
                     RES_CODES.index(r.result), SIT_CODES.index(r.situation), TYP_CODES.index(r.shot_type),
                     pidx[int(r.player_id)], 1 if r.team == club else 0])
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


def player_rows(rosters: pd.DataFrame, shots: pd.DataFrame, ids: set, club: str = M.LIV) -> list[dict]:
    """The club's player totals over the given matches (rosters for minutes/assist stats, shots for npxG)."""
    r = rosters[(rosters.team == club) & rosters.match_id.isin(ids)]
    g = r.groupby(["player_id", "player"], as_index=False).agg(
        pos=("position", lambda x: x[x != "Sub"].mode().iat[0] if (x != "Sub").any() else "Sub"),
        apps=("minutes", lambda x: int((x > 0).sum())), min=("minutes", "sum"), g=("goals", "sum"), og=("own_goals", "sum"),
        ast=("assists", "sum"), xa=("xa", "sum"), kp=("key_passes", "sum"), chain=("xgchain", "sum"), build=("xgbuildup", "sum"))
    s = shots[(shots.team == club) & shots.match_id.isin(ids) & (shots.result != "OwnGoal")]
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


def mix_payload(sel: pd.DataFrame, shots: pd.DataFrame, season: str, club: str = M.LIV) -> dict:
    """xG / shots / goals by source (open play, set piece, penalty) for the club and against, per season
    when the selection spans all seasons, else one group for the selection."""
    s = shots[shots.result != "OwnGoal"]
    groups = [(k, g) for k, g in sel.groupby("season")] if season == "all" else [("Selection", sel)]
    res = {"for": [], "against": []}
    for label, g in groups:
        sub = s[s.match_id.isin(set(g.match_id))]
        for side, mask in (("for", sub.team == club), ("against", sub.team != club)):
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
                      shots: pd.DataFrame, rosters: pd.DataFrame, club: str = M.LIV) -> dict:
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
        "players": player_rows(rosters, shots, set(sel.match_id), club),
        "mix": mix_payload(sel, shots, season, club),
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


def localise(text: str, cc: dict) -> str:
    """The registry and methodology are written about Liverpool (the original club); this swaps in another club's name."""
    return text.replace("Liverpool FC", cc["full_name"]).replace("Liverpool", cc["name"])


def registry_for(cc: dict) -> dict:
    """The metric registry with the club's name in every string (the registry stays the single source of truth)."""
    def walk(o):
        if isinstance(o, str):
            return localise(o, cc)
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return {k: walk(v) for k, v in M.REGISTRY.items()}


def glossary_html(cc: dict = CLUBS["liverpool"]) -> str:
    seen, parts = set(), []
    REG = registry_for(cc)
    groups = [(t, [i for i in ids if i in REG]) for t, ids in GLOSSARY_GROUPS]
    scfg = style.load_config()
    groups.append(("Style of play (proxy scores, not good or bad)",
                   ["style_section", "style_phase_defence", "style_phase_buildup", "style_phase_attack"]
                   + [p["index_id"] for p in scfg["phases"].values()] + list(scfg["axes"]) + list(scfg["kpis"])))
    for _, ids in groups:
        seen.update(ids)
    rest = [i for i in REG if i not in seen]
    if rest:
        groups.append(("Other", rest))
    better = {True: "higher", False: "lower", None: "no judgement"}
    for title, ids in groups:
        rows = "".join(
            f'<tr data-metric-id="{esc(i)}"><td>{esc(REG[i]["label"])}</td><td>{esc(REG[i]["description"])}</td>'
            f'<td>{esc(REG[i]["unit"])}</td><td>{better[REG[i]["higher_is_better"]]}</td>'
            f'<td>{REG[i]["min_sample"]}</td><td>{esc(REG[i]["source"])}</td></tr>' for i in ids)
        parts.append(f'<details class="sub"><summary><h3>{esc(title)} <span class="cnt">({len(ids)})</span></h3></summary>'
                     '<div class="tbl-x"><table class="data glossary"><thead><tr><th>Metric</th><th>Definition</th><th>Unit</th>'
                     f'<th>Better when</th><th>Minimum sample</th><th>Source</th></tr></thead><tbody>{rows}</tbody></table></div></details>')
    return "".join(parts)


def sim_example_html(cc: dict = CLUBS["liverpool"]) -> str:
    liv, opp = [0.30, 0.50], [0.20]
    dl, do = M.goal_dist(liv), M.goal_dist(opp)
    w, d, l = M.outcome_probs(liv, opp)
    rows = "".join(f"<tr><td>{k}</td><td>{dl[k] * 100:.1f}%</td><td>{(do[k] * 100 if k < len(do) else 0):.1f}%</td></tr>" for k in range(len(dl)))
    return ('<div class="example" data-example="sim"><p><strong>Worked example.</strong> {esc(cc["name"])} have two shots (xG 0.30 and 0.50) and the opposition one (xG 0.20). '
            'The probability of each goal count is:</p><div class="tbl-x"><table class="data"><thead><tr><th>Goals</th><th>{esc(cc["name"])}</th><th>Opposition</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div><p>Combining the two gives P(win) = {w * 100:.1f}%, P(draw) = {d * 100:.1f}%, P(loss) = {l * 100:.1f}%, '
            f'so xPts = 3 × {w:.4f} + {d:.4f} = <strong>{M.xpts(w, d):.2f}</strong>.</p></div>')


def devig_example_html(e: pd.DataFrame, cc: dict = CLUBS["liverpool"]) -> str:
    ex = e[e.mkt_source == "pinnacle_close"].iloc[-1]
    odds = [ex.mkt_h, ex.mkt_d, ex.mkt_a]
    raw = [1 / o for o in odds]
    prop, shin, z = M.devig_proportional(odds), M.devig_shin(odds), M.shin_z(odds)
    home, away = (cc["canonical"], ex.opponent) if ex.is_home else (ex.opponent, cc["canonical"])
    names = [f"{home} win", "Draw", f"{away} win"]
    rows = "".join(f"<tr><td>{esc(n)}</td><td>{o:.2f}</td><td>{r * 100:.2f}%</td><td>{p * 100:.2f}%</td><td>{sh * 100:.2f}%</td></tr>"
                   for n, o, r, p, sh in zip(names, odds, raw, prop, shin))
    return (f'<div class="example" data-example="devig"><p><strong>Worked example</strong>: {esc(home)} v {esc(away)}, {dmy(ex.fd_date, "%b")} '
            f'(Pinnacle closing odds). The implied probabilities add up to {sum(raw) * 100:.2f}%, a margin of {(sum(raw) - 1) * 100:.2f}%.</p>'
            '<div class="tbl-x"><table class="data" data-table="devig-example"><thead><tr><th>Outcome</th><th>Decimal odds</th><th>Implied (1 ÷ odds)</th><th>Proportional</th><th>Shin</th></tr></thead>'
            f'<tbody>{rows}</tbody><tfoot><tr><td>Sum</td><td></td><td>{sum(raw) * 100:.2f}%</td><td>{prop.sum() * 100:.2f}%</td><td>{shin.sum() * 100:.2f}%</td></tr></tfoot></table></div>'
            f'<p>Shin insider share z = {z:.4f}.</p></div>')


def coverage_html(e: pd.DataFrame, shots: pd.DataFrame, cc: dict = CLUBS["liverpool"]) -> str:
    src = e.mkt_source.value_counts().to_dict()
    label = {"pinnacle_close": "Pinnacle closing odds", "market_avg_close": "market-average closing odds", "betfair_exchange_close": "Betfair Exchange closing odds"}
    items = [
        f"<li><strong>Understat</strong> (Premier League, 2014/15 onwards): shot-level data with x/y coordinates, situation, shot type and xG for every shot; team match xG, xGA, PPDA and deep completions; player match statistics (minutes, goals, xA, key passes, xGChain, xGBuildup).</li>",
        f"<li><strong>football-data.co.uk</strong>: results and betting odds (Pinnacle open and close, market average and maximum, Betfair Exchange where present).</li>",
        f"<li><strong>Coverage</strong>: {len(e)} {cc['name']} matches across {e.season.nunique()} seasons ({e.season.iloc[0]} to {e.season.iloc[-1]}), {len(shots):,} shots including the opposition's, "
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


def methodology_payload(e: pd.DataFrame, shots: pd.DataFrame, cc: dict = CLUBS["liverpool"]) -> dict:
    src = localise((ROOT / "template" / "methodology.md").read_text(encoding="utf-8"), cc)
    d = pd.concat([e.xg - e.xg_reported, e.xga - e.xga_reported])
    rect = style.load_config()["central_rectangle"]
    inline = {"xg_gap_share": f"{(d > 0.02).mean() * 100:.0f}%", "xg_gap_max": f"{d.max():.1f}", "rect_x": f"{rect['x_min']}", "rect_y": f"{rect['y_half_width']}"}
    for k, v in inline.items():
        src = src.replace("{{" + k + "}}", v)
    html, toc = md_to_html(src)
    gen = {"coverage": coverage_html(e, shots, cc), "registry_glossary": glossary_html(cc), "sim_example": sim_example_html(cc),
           "devig_example": devig_example_html(e, cc), "takeaway_rules": takeaway_rules_html(), "external_hook": external_hook_html(), "style_mapping": style_mapping_html()}
    for k, v in gen.items():
        token = f'<div data-gen="{k}"></div>'
        assert token in html, f"placeholder {k} missing from methodology.md"
        html = html.replace(token, v)
    assert 'data-gen="' not in html, "unresolved placeholder in methodology.md"
    return {"html": html, "toc": toc}


def style_payload(style_raw: pd.DataFrame | None, club: str = M.LIV) -> dict | None:
    """Style-of-play scores for the dashboard (aggregates only). Reads the optional external KPI CSV silently."""
    if style_raw is None:
        style_raw = pd.read_parquet(PROCESSED / "style_raw.parquet")
    cfg = style.load_config()
    ext = style.load_external(None, cfg, style_raw)
    own = style_raw[style_raw.club == club].set_index("season").matches.to_dict()
    return style.payload(style.compute(style_raw, cfg, ext), cfg, club=club, matches={k: int(v) for k, v in own.items()})


# ------------------------------------------------------------------ club identity: theme, crest, switcher
def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{round(min(max(c, 0), 1) * 255):02x}" for c in (r, g, b))


def luminance(h: str) -> float:
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def retint(h: str, hue: float) -> str:
    """The same lightness and saturation as `h` at another hue (the Liverpool greys are red-tinted; other clubs get their own tint)."""
    import colorsys
    hh, l, sat = colorsys.rgb_to_hls(*_rgb(h))
    return _hex(*colorsys.hls_to_rgb(hue / 360.0, l, sat))


def _nudge(h: str, against: list[str], need: float, direction: int) -> str:
    """Move `h` lighter (direction +1) or darker (-1), keeping hue and saturation, until it has `need`:1 against every colour in `against`."""
    import colorsys
    hh, l, sat = colorsys.rgb_to_hls(*_rgb(h))
    for _ in range(100):
        if all(contrast(h, a) >= need for a in against):
            break
        l = min(max(l + direction * 0.01, 0), 1)
        h = _hex(*colorsys.hls_to_rgb(hh, l, sat))
    return h


# The Liverpool neutrals (the template default), re-tinted per club. (token, light value, dark value); None = not tinted.
_NEUTRALS = [("bg", "#faf5f5", "#100507"), ("sidebar", None, "#160709"), ("surface-2", "#f7f0f0", "#25100f"),
             ("ink", "#1a0c0e", "#f4eaec"), ("ink-2", "#6b5257", "#b8a5a9"), ("muted", "#7d6469", "#8f7c80"), ("neutral", "#6b5257", "#b8a5a9"),
             ("line", "#ecd9dc", "#3a1a1f"), ("line-strong", "#dcc3c7", "#4b2a30"), ("chart-grid", "#f0e3e5", "#2e1519"), ("zero", "#b99aa0", "#6a3a40"),
             ("stage", "#f3e9ea", "#130608")]
_SURFACE = {"light": "#ffffff", "dark": "#1c0b0e"}


def theme_tokens(cc: dict) -> dict:
    """{'light': {token: value}, 'dark': {...}, 'brand': {...}} for a club, or empty for the default club (its tokens live in the template).
    Text tokens are nudged until they keep 4.5:1 on the page surfaces, so a hue change can never cost readability."""
    t = cc.get("theme")
    if not t:
        return {}
    out = {}
    for mode in ("light", "dark"):
        tok = {}
        for name, lo, dk in _NEUTRALS:
            v = lo if mode == "light" else dk
            if v is not None:
                tok[name] = retint(v, t["hue"])
        surface = _SURFACE[mode] if mode == "light" else retint("#1c0b0e", t["hue"])
        if mode == "dark":
            tok["surface"] = surface
            tok["drawer"] = "rgba(%d,%d,%d,.95)" % tuple(round(c * 255) for c in _rgb(retint("#160709", t["hue"])))
        surfaces = [surface, tok["bg"], tok["surface-2"]]
        direction = -1 if mode == "light" else 1
        for name in ("ink-2", "muted", "neutral"):
            tok[name] = _nudge(tok[name], surfaces, 4.5, direction)
        c = t[mode]
        mark = _nudge(c["mark"], surfaces, 4.5, direction)
        tok.update({"accent": c["accent"], "red": mark, "goal": c["goal"], "mix-open": mark, "bar-sel": mark, "bar-cmp": c["bar_cmp"],
                    "s1": c["s1"], "s2": c["s2"], "s3": c["s3"], "mix-set": c["s2"], "mix-pen": c["s1"], "gold": c["s2"],
                    "accent-soft": "rgba(%d,%d,%d,%s)" % (*(round(x * 255) for x in _rgb(mark)), ".09" if mode == "light" else ".22")})
        ink = _rgb(tok["ink"])
        rgba = lambda a: "rgba(%d,%d,%d,%s)" % (*(round(x * 255) for x in ink), a)
        if mode == "light":
            tok.update({"pitch-line": rgba(".24"), "shot-fill": rgba(".06"), "shot-stroke": rgba(".6")})
        out[mode] = tok
    out["brand"] = {"brand-deep": t["brand_deep"], "brand-deeper": t["brand_deeper"], "record-ink": cc["record_ink"], "record-num": cc["record_ink"],
                    "record-soft": "rgba(%d,%d,%d,.10)" % tuple(round(x * 255) for x in _rgb(cc["record_ink"]))}
    return out


def seats_uri(fill: str) -> str:
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='24' height='44'><g fill='" + fill + "'><rect x='4' y='1' width='16' height='9' rx='3'/>"
           "<rect x='2' y='11' width='20' height='9' rx='3'/><rect x='16' y='23' width='16' height='9' rx='3'/><rect x='-8' y='23' width='16' height='9' rx='3'/>"
           "<rect x='14' y='33' width='20' height='9' rx='3'/><rect x='-10' y='33' width='20' height='9' rx='3'/></g></svg>")
    from urllib.parse import quote
    return 'url("data:image/svg+xml,' + quote(svg, safe="/:='") + '")'


def theme_css(cc: dict) -> str:
    """The <style> text that re-themes the template for a club: token overrides in the same three blocks the template uses
    (light, automatic dark, forced dark), plus the club-constant identity variables. Empty for the default club."""
    tk = theme_tokens(cc)
    if not tk:
        return ""
    decl = lambda d: " ".join(f"--{k}: {v};" for k, v in d.items())
    lettering = cc["lettering"]
    size = min(1.0, 8 / len(lettering))
    const = {**tk["brand"], "seats": seats_uri(cc["theme"]["pattern"]), "lettering": f'"{lettering}"',
             "lettering-size": f"min({190 * size:.0f}px, {16 * size:.1f}vh)"}
    return (f":root {{ {decl(tk['light'])} {decl(const)} }}\n"
            f"@media (prefers-color-scheme: dark) {{ :root:not([data-theme=\"light\"]) {{ {decl(tk['dark'])} }} }}\n"
            f":root[data-theme=\"dark\"] {{ {decl(tk['dark'])} }}\n")


def monogram_svg(cc: dict) -> str:
    """An original badge (initials on a shield in the club colours) for clubs without a supplied crest image."""
    t = cc.get("theme") or {}
    fill, deep = t.get("brand", "#c8102e"), t.get("brand_deep", "#a30d25")
    ini = cc["initials"]
    size = 22 if len(ini) <= 3 else 15
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 60 70' width='60' height='70'><path d='M4 4h52v34c0 15-12 25-26 30C16 63 4 53 4 38z' fill='" + fill + "' stroke='#fff' stroke-width='3'/>"
            "<path d='M10 10h40v28c0 11-9 19-20 24C19 57 10 49 10 38z' fill='none' stroke='" + deep + "' stroke-width='2'/>"
            f"<text x='30' y='42' text-anchor='middle' font-family='Arial Narrow,Arial,sans-serif' font-weight='700' font-size='{size}' fill='#fff'>{esc(ini)}</text></svg>")


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def crest_path(cc: dict) -> Path:
    """The club's crest image: the configured path, else a file in assets/ or assets/crests/ named after the club
    (the slug or one of `crest_aliases`, any letter case, .png), else the configured path (which may not exist: the
    generated monogram is used then)."""
    want = {cc["slug"].lower(), *(a.lower() for a in cc.get("crest_aliases", []))}
    configured = ROOT / cc["crest"]
    if configured.exists():
        return configured
    for folder in (ROOT / "assets" / "crests", ROOT / "assets"):
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() == ".png" and f.stem.lower() in want:
                    return f
    return configured


def crest_uri(path: Path, height: int) -> str:
    """The supplied crest PNG resized to at most `height` px tall (aspect kept, optimised), as a data URI."""
    import io

    from PIL import Image

    im = Image.open(path).convert("RGBA")
    corners = [im.getpixel(xy)[3] for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1))]
    if min(corners) > 0:
        print(f"WARNING: {path} has an opaque background (corner alpha {corners}); it is used as supplied, please check it")
    if im.height > height:
        im = im.resize((round(im.width * height / im.height), height), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + _b64(buf.getvalue())


def badge_uri(cc: dict, path: Path | None = None, height: int = CREST_MENU_HEIGHT) -> tuple[str, bool]:
    """(data URI, is_supplied_image): the club's crest image if the file exists, else its generated monogram."""
    path = crest_path(cc) if path is None else Path(path)
    if path.exists():
        return crest_uri(path, height), True
    return "data:image/svg+xml;base64," + _b64(monogram_svg(cc).encode("utf-8")), False


def favicon_uri(cc: dict) -> str:
    from urllib.parse import quote
    fill = (cc.get("theme") or {}).get("brand", "#c8102e")
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='{fill}'/>"
           "<rect x='7' y='7' width='5' height='18' rx='1' fill='white'/><rect x='17' y='7' width='5' height='18' rx='1' fill='white'/></svg>")
    return "data:image/svg+xml," + quote(svg, safe="/:='")


def club_switcher(current: dict) -> list[dict]:
    """The clubs as the page's switcher lists them. `href` is relative to the current page (the default club sits at the site root)."""
    up = "" if not current["path"] else "../"
    return [{"slug": c["slug"], "name": c["name"], "full_name": c["full_name"], "initials": c["initials"], "href": up + c["path"] + "index.html",
             "badge": badge_uri(c)[0], "brand": (c.get("theme") or {}).get("brand", "#c8102e"), "current": c["slug"] == current["slug"]}
            for c in CLUBS.values()]


# ------------------------------------------------------------------ dashboard
def dashboard(e: pd.DataFrame, fixtures: pd.DataFrame, shots: pd.DataFrame, rosters: pd.DataFrame,
              ts: pd.DataFrame, tm: pd.DataFrame, style_raw: pd.DataFrame | None = None, club: str = "liverpool") -> dict:
    cc = CLUBS[club]
    name = cc["canonical"]
    eras_cfg = M.load_eras(club)
    seasons = sorted(e.season.unique())
    eras = [x["manager"] for x in eras_cfg if (e.era == x["manager"]).any()]
    st = M.season_table(e)
    selections = {}
    for season in ["all"] + seasons:
        for era in ["all"] + eras:
            sel = M.select(e, season, era)
            if len(sel):
                selections[f"{season}|{era}"] = selection_payload(e, sel, season, era, st, shots, rosters, name)
    counted = e.groupby("season").size()
    default = next(s for s in reversed(seasons) if counted[s] >= M.SETTINGS["min_sample"])
    scheduled = fixtures.groupby("season").size().to_dict()
    return {
        "club": {k: cc[k] for k in ("slug", "name", "full_name", "canonical", "initials", "path", "lettering")},
        "clubs": club_switcher(cc),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "data_through": e.fd_date.max().strftime("%Y-%m-%d"),
        "seasons": seasons, "default_season": default, "scheduled": scheduled,
        "eras": [x for x in eras_cfg if x["manager"] in eras],
        "settings": {"mispriced": M.SETTINGS["mispriced"], "min_sample": M.SETTINGS["min_sample"], "baseline_seasons": M.SETTINGS["baseline_seasons"],
                     "devig_method": M.SETTINGS["devig_method"], "form_matches": M.SETTINGS["form_matches"]},
        "registry": registry_for(cc),
        "matches": match_rows(e),
        "methodology": methodology_payload(e, shots, cc),
        "shots": shots_payload(e, shots, name),
        "league": league_payload(ts, tm),
        "style": style_payload(style_raw, name),
        "selections": selections,
    }


def load_tables(club: str = "liverpool") -> dict:
    d = PROCESSED / club
    t = {n: pd.read_parquet(d / f"{n}.parquet") for n in ("matches", "fixtures", "shots", "rosters")}
    t.update({n: pd.read_parquet(PROCESSED / f"{n}.parquet") for n in ("team_seasons", "team_matches")})
    t["enriched"] = M.enrich_matches(t["matches"], t["shots"], CLUBS[club]["canonical"], M.load_eras(club))
    return t


# ------------------------------------------------------------------ render
def crest_html(path: Path = CREST, cc: dict | None = None) -> str:
    """The sidebar crest: the club's crest PNG resized (Pillow, aspect ratio kept, at most 192 px tall), optimised and
    inlined as a base64 data URI so the deploy stays one file. If the file is missing, the default club gets its red bar
    and the others their generated monogram badge. It is a button that opens the club switcher (wired in the template)."""
    cc = cc or CLUBS[M.DEFAULT_CLUB]
    if not Path(path).exists():
        if cc["slug"] == M.DEFAULT_CLUB:
            return '<span class="brand-mark" aria-hidden="true"></span>'
        uri, _ = badge_uri(cc, path)
        return f'<img class="crest mono" src="{uri}" alt="{esc(cc["name"])} badge" width="46" height="54">'
    return f'<img class="crest" src="{crest_uri(Path(path), CREST_MAX_HEIGHT)}" alt="{esc(cc["full_name"])} crest" width="46" height="54">'


def render(data: dict, crest_path: Path | None = None) -> str:
    cc = CLUBS[data["club"]["slug"]]
    html = TEMPLATE.read_text(encoding="utf-8")
    for tok in ("__CREST__", "__CLUB_THEME__", "__DATA_JSON__"):
        assert tok in html, tok
    name, full = esc(cc["name"]), esc(cc["full_name"])
    html = html.replace("__CREST__", crest_html(crest_path if crest_path is not None else crest_path_of(cc), cc))
    html = html.replace("__CLUB_THEME__", theme_css(cc))
    html = html.replace("__CLUB_NAME__", name).replace("__CLUB_FULL__", full)
    html = html.replace("__FAVICON__", favicon_uri(cc)).replace("__THEME_COLOR__", theme_tokens(cc).get("dark", {}).get("bg", "#100507"))
    blob = json.dumps(clean(data), allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    blob = blob.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return html.replace("__DATA_JSON__", blob)


def crest_path_of(cc: dict) -> Path:
    return crest_path(cc)


def out_path(cc: dict) -> Path:
    return DIST_DIR / cc["path"] / "index.html"


def build(slugs: list[str] | None = None) -> list[Path]:
    """Build dist/index.html (Liverpool) and dist/<slug>/index.html for every club with processed data."""
    done = []
    for slug in (slugs or list(CLUBS)):
        if not (PROCESSED / slug / "matches.parquet").exists():
            raise SystemExit(f"no processed data for {slug}: run `python etl.py` first")
        cc = CLUBS[slug]
        t = load_tables(slug)
        data = dashboard(t["enriched"], t["fixtures"], t["shots"], t["rosters"], t["team_seasons"], t["team_matches"], club=slug)
        html = render(data)
        out = out_path(cc)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        size = out.stat().st_size
        if size > MAX_BYTES:
            raise SystemExit(f"{out} is {size / 1e6:.2f} MB, over the 5 MB budget")
        print(f"wrote {out} ({size / 1024:.0f} KB, {len(data['selections'])} selections)")
        done.append(out)
    return done


if __name__ == "__main__":
    import sys
    build(sys.argv[1:] or None)
