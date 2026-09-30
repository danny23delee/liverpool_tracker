"""Processed data -> dashboard JSON -> inlined into template/index.html -> dist/index.html.

Every number the dashboard shows is computed here (via metrics.py) and only formatted in the browser,
so there is exactly one implementation of each calculation.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import metrics as M

ROOT = Path(__file__).parent
PROCESSED = ROOT / "data" / "processed"
TEMPLATE = ROOT / "template" / "index.html"
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


# ------------------------------------------------------------------ payload
def match_rows(e: pd.DataFrame) -> list[dict]:
    rows = []
    for r in e.itertuples():
        rows.append({
            "id": int(r.match_id), "d": r.fd_date.strftime("%Y-%m-%d"), "ko": r.kickoff_utc.isoformat(),
            "s": r.season, "era": r.era, "opp": r.opponent, "h": bool(r.is_home), "gf": int(r.gf), "ga": int(r.ga),
            "r": r.result, "pts": int(r.pts), "xg": r4(r.xg), "xga": r4(r.xga), "xpts": r4(r.xpts_sim),
            "xptm": r4(r.xpts_market), "sim": [r4(r.sim_w), r4(r.sim_d), r4(r.sim_l)],
            "mkt": [r4(r.mp_w), r4(r.mp_d), r4(r.mp_l)], "src": r.mkt_source, "odds": r4(r.odds_win),
            "ppda": r4(r.ppda_att / r.ppda_def) if r.ppda_def > 0 else None, "deep": int(r.deep), "deepa": int(r.deep_allowed),
            "sh": int(r.shots_for), "sha": int(r.shots_against),
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
        rows.append([mi[int(r.match_id)], int(r.minute), round(r.x, 3), round(r.y, 3), round(r.xg, 4),
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
        out[season] = {"teams": teams, "avg": {
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
    return [{"id": int(x.player_id), "name": x.player, "pos": x.pos, "apps": int(x.apps), "min": int(x.min), "g": int(x.g),
             "npg": int(x.npg), "xg": r4(x.xg), "npxg": r4(x.npxg), "sh": int(x.sh), "ast": int(x.ast), "xa": r4(x.xa),
             "kp": int(x.kp), "chain": r4(x.chain), "build": r4(x.build)} for x in g.itertuples()]


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
        "last5": [int(i) for i in sel.sort_values("kickoff_utc").tail(M.SETTINGS["form_matches"]).index],
        "season_played": int(len(season_all)),
    }
    return out


def dashboard(e: pd.DataFrame, fixtures: pd.DataFrame, shots: pd.DataFrame, rosters: pd.DataFrame,
              ts: pd.DataFrame, tm: pd.DataFrame) -> dict:
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
        "settings": {"min_sample": M.SETTINGS["min_sample"], "baseline_seasons": M.SETTINGS["baseline_seasons"],
                     "devig_method": M.SETTINGS["devig_method"], "form_matches": M.SETTINGS["form_matches"]},
        "registry": M.REGISTRY,
        "matches": match_rows(e),
        "shots": shots_payload(e, shots),
        "league": league_payload(ts, tm),
        "selections": selections,
    }


def load_tables() -> dict:
    t = {n: pd.read_parquet(PROCESSED / f"{n}.parquet") for n in
         ("matches", "fixtures", "shots", "rosters", "team_seasons", "team_matches")}
    t["enriched"] = M.enrich_matches(t["matches"], t["shots"])
    return t


# ------------------------------------------------------------------ render
def render(data: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
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
