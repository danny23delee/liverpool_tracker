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
        })
    return rows


def selection_payload(e: pd.DataFrame, sel: pd.DataFrame, season: str, era: str, seasons_tbl: pd.DataFrame) -> dict:
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
        "last5": [int(i) for i in sel.sort_values("kickoff_utc").tail(M.SETTINGS["form_matches"]).index],
        "season_played": int(len(season_all)),
    }
    return out


def dashboard(e: pd.DataFrame, fixtures: pd.DataFrame) -> dict:
    seasons = sorted(e.season.unique())
    eras = [x["manager"] for x in M.ERAS if (e.era == x["manager"]).any()]
    st = M.season_table(e)
    selections = {}
    for season in ["all"] + seasons:
        for era in ["all"] + eras:
            sel = M.select(e, season, era)
            if len(sel):
                selections[f"{season}|{era}"] = selection_payload(e, sel, season, era, st)
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
        "selections": selections,
    }


def load_enriched() -> tuple[pd.DataFrame, pd.DataFrame]:
    m = pd.read_parquet(PROCESSED / "matches.parquet")
    s = pd.read_parquet(PROCESSED / "shots.parquet")
    fx = pd.read_parquet(PROCESSED / "fixtures.parquet")
    return M.enrich_matches(m, s), fx


# ------------------------------------------------------------------ render
def render(data: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    blob = json.dumps(clean(data), allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    blob = blob.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    assert "__DATA_JSON__" in html
    return html.replace("__DATA_JSON__", blob)


def build() -> Path:
    e, fx = load_enriched()
    data = dashboard(e, fx)
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
