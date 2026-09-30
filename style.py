"""Style-of-play proxy scores (3 phases x 3 tactical axes) for every club, relative to each season's league.

Inspired by the CIES Football Observatory's "Style of play of teams" framework (3 phases, 3 tactical options
each, scored 0-100, plus composite indices). CIES scores come from Impect event data (pressure locations,
pass lengths, reception heights), which is not freely available; this module builds *proxy* scores from
Understat team data and never presents them as CIES scores. Config (axes, KPI lists and signs, transform,
status, confidence) lives in config/style.json; the KPI formulas below are keyed by KPI id.

Scoring, per season and per axis, over the clubs of that season's league:
  1. each KPI -> z = (value - league mean) / population sd
  2. axis raw = mean of sign * z over the KPIs available for the club (an axis needs >= min_kpis, else null)
  3. re-standardise the raw values across the league (averaging shrinks the spread)
  4. score = 100 * Phi(z) (normal CDF; "percentile_rank" is the alternative transform), 50 = league average
  5. rank by z, 1 = furthest toward the right-hand label (ties share the best rank, see config tie_rule)
  6. composite index = mean of the available axis scores of the phase (>= 2 axes; "partial" if fewer than all)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config" / "style.json"
EPS = 1e-12


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ================================================================== KPI formulas (from the style_raw table)
def _speed_total(d: pd.DataFrame, side: str, what: str = "shots") -> pd.Series:
    return sum(d[f"speed_{k}_{what}_{side}"] for k in ("fast", "normal", "standard", "slow"))


def _inbox(d: pd.DataFrame, side: str, what: str = "shots") -> pd.Series:
    return d[f"zone_six_{what}_{side}"] + d[f"zone_pen_{what}_{side}"]


KPI_FORMULAS = {
    "def_deep_allowed_pm": lambda d: d.deep_allowed / d.matches,
    "def_fast_conceded_share": lambda d: d.speed_fast_shots_against / _speed_total(d, "against"),
    "def_ppda": lambda d: d.ppda_att / d.ppda_def,
    "def_actions_pm": lambda d: d.ppda_def / d.matches,
    "def_inbox_shots_pm": lambda d: _inbox(d, "against") / d.matches,
    "def_inbox_xga_pm": lambda d: _inbox(d, "against", "xg") / d.matches,
    "bu_slow_share_shots": lambda d: d.speed_slow_shots_for / _speed_total(d, "for"),
    "bu_buildup_share": lambda d: d.xgbuildup / d.xgchain,
    "att_fast_share_shots": lambda d: d.speed_fast_shots_for / _speed_total(d, "for"),
    "att_fast_share_xg": lambda d: d.speed_fast_xg_for / _speed_total(d, "for", "xg"),
    "att_inbox_share": lambda d: _inbox(d, "for") / (_inbox(d, "for") + d.zone_out_shots_for),   # own-goal pseudo-shots excluded
    "att_deep_per_shot": lambda d: d.deep / (_inbox(d, "for") + d.zone_out_shots_for),
}


def kpi_table(raw: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Long table (season, club, kpi_id, value, origin='proxy') for every computable KPI of the config."""
    cfg = cfg or load_config()
    rows = []
    for kid, meta in cfg["kpis"].items():
        if not meta["available"] or kid not in KPI_FORMULAS:
            continue
        v = KPI_FORMULAS[kid](raw).replace([np.inf, -np.inf], np.nan)
        rows.append(pd.DataFrame({"season": raw.season.values, "club": raw.club.values, "kpi_id": kid, "value": v.values, "origin": "proxy"}))
    return pd.concat(rows, ignore_index=True)


# ================================================================== external KPI hook
def load_external(path: str | Path | None, cfg: dict | None = None, raw: pd.DataFrame | None = None) -> pd.DataFrame | None:
    """Optional CSV `season,club,kpi_id,value` (season like 2024-25, club = Understat name, kpi_id from the
    config) that replaces or adds KPI values, e.g. real pass-level data. Returns None if the file is absent;
    raises ValueError for anything malformed."""
    cfg = cfg or load_config()
    p = Path(path) if path else ROOT / cfg["external_csv"]
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if list(df.columns) != ["season", "club", "kpi_id", "value"]:
        raise ValueError("style_kpis.csv needs exactly the columns season,club,kpi_id,value")
    unknown = sorted(set(df.kpi_id) - set(cfg["kpis"]))
    if unknown:
        raise ValueError(f"style_kpis.csv has unknown kpi_id(s): {unknown}")
    df["value"] = pd.to_numeric(df["value"], errors="raise")
    if not np.isfinite(df["value"]).all():
        raise ValueError("style_kpis.csv values must be finite numbers")
    if df.duplicated(["season", "club", "kpi_id"]).any():
        raise ValueError("style_kpis.csv has duplicate season/club/kpi_id rows")
    if raw is not None:
        bad = df[~df.set_index(["season", "club"]).index.isin(raw.set_index(["season", "club"]).index)]
        if len(bad):
            raise ValueError(f"style_kpis.csv has season/club pairs not in the data, e.g. {bad.iloc[0].season} / {bad.iloc[0].club}")
    return df.assign(origin="external")


def merge_external(values: pd.DataFrame, ext: pd.DataFrame | None) -> pd.DataFrame:
    if ext is None or ext.empty:
        return values
    key = ["season", "club", "kpi_id"]
    kept = values.merge(ext[key], on=key, how="left", indicator=True).query("_merge == 'left_only'").drop(columns="_merge")
    return pd.concat([kept, ext[key + ["value", "origin"]]], ignore_index=True)


# ================================================================== scoring
def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def zscore(s: pd.Series) -> pd.Series:
    """z = (x - mean) / population sd over the non-null values; NaN stays NaN. Undefined (all equal) -> all NaN."""
    m, sd = s.mean(), s.std(ddof=0)
    if not np.isfinite(sd) or sd < EPS:
        return pd.Series(np.nan, index=s.index)
    return (s - m) / sd


def competition_rank(z: pd.Series) -> pd.Series:
    """1 = highest z. Values equal to 1e-12 share the best rank of the group and the next rank is skipped."""
    r = z.round(12).rank(method="min", ascending=False)
    return r.astype(int)


def score_axis(season_values: pd.DataFrame, axis: dict, cfg: dict) -> dict | None:
    """season_values: clubs (index) x KPI ids (columns). Returns scores / ranks / z per club, or None if the axis
    cannot be computed from at least min_kpis KPIs."""
    min_kpis, min_clubs = cfg["min_kpis"], cfg.get("min_clubs_external", 10)
    usable = []
    for kid in axis["kpis"] + axis.get("external_kpis", []):
        if kid not in season_values.columns:
            continue
        col = season_values[kid]
        need = 2 if cfg["kpis"][kid]["available"] else min_clubs
        if col.notna().sum() >= need and col.std(ddof=0) > EPS:
            usable.append(kid)
    if len(usable) < min_kpis:
        return None
    z = pd.DataFrame({k: cfg["kpis"][k]["sign"] * zscore(season_values[k]) for k in usable})
    raw = z.mean(axis=1, skipna=True)
    raw[z.notna().sum(axis=1) < min_kpis] = np.nan        # a club needs >= min_kpis of the axis KPIs
    valid = raw.dropna()
    if len(valid) < 2:
        return None
    sd = valid.std(ddof=0)
    z2 = (valid - valid.mean()) / sd if sd > EPS else valid * 0.0      # re-standardise to unit variance
    if cfg["transform"] == "percentile_rank":
        score = 100.0 * (z2.rank(method="average") - 0.5) / len(z2)
    else:
        score = z2.map(lambda v: 100.0 * phi(v))
    return {"scores": score, "ranks": competition_rank(z2), "z": z2, "kpis": usable, "kpi_z": z}


def composite(axis_scores: pd.DataFrame, n_axes_total: int, min_axes: int = 2) -> pd.DataFrame:
    """Index = mean of the available axis scores; `partial` when fewer than all axes; none (NaN) below min_axes."""
    n = axis_scores.notna().sum(axis=1)
    idx = axis_scores.mean(axis=1, skipna=True).where(n >= min_axes)
    return pd.DataFrame({"score": idx, "n_axes": n, "partial": (n < n_axes_total) & (n >= min_axes)})


def compute(raw: pd.DataFrame, cfg: dict | None = None, external: pd.DataFrame | None = None) -> dict:
    """Scores for every season in `raw` (the style_raw table). Returns
    {season: {"n_clubs": int, "axes": {axis_id: result | None}, "origin": {axis_id: 'proxy'|'external'},
              "indices": {index_id: DataFrame(score, n_axes, partial) indexed by club}, "values": wide KPI table}}."""
    cfg = cfg or load_config()
    values = merge_external(kpi_table(raw, cfg), external)
    out = {}
    for season, g in values.groupby("season"):
        wide = g.pivot(index="club", columns="kpi_id", values="value")
        origin = g.pivot(index="club", columns="kpi_id", values="origin")
        axes, origins = {}, {}
        for aid, axis in cfg["axes"].items():
            res = score_axis(wide, axis, cfg)
            axes[aid] = res
            used_external = res is not None and any((origin[k] == "external").any() for k in res["kpis"] if k in origin.columns)
            origins[aid] = "external" if used_external else "proxy"
        indices = {}
        for ph in cfg["phases"].values():
            cols = {a: axes[a]["scores"] for a in ph["axes"] if axes[a] is not None}
            frame = pd.DataFrame(cols, index=wide.index) if cols else pd.DataFrame(index=wide.index, columns=ph["axes"], dtype=float)
            indices[ph["index_id"]] = composite(frame, len(ph["axes"]))
        out[season] = {"n_clubs": int(len(wide)), "axes": axes, "origin": origins, "indices": indices, "values": wide}
    return out


# ================================================================== dashboard payload
def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def payload(result: dict, cfg: dict, club: str = "Liverpool", matches: dict[str, int] | None = None) -> dict:
    """Everything the page needs, with only aggregated values (no league-wide shot lists): per season and axis the
    scores and ranks of all clubs (for the strip plot), Liverpool's KPI table, the indices, and Liverpool's
    series across seasons (for the trend sparklines)."""
    seasons = sorted(result)
    per_season, series = {}, {a: {} for a in cfg["axes"]} | {p["index_id"]: {} for p in cfg["phases"].values()}
    prev_idx: dict[str, float | None] = {}
    for season in seasons:
        r = result[season]
        axes = {}
        for aid, axis in cfg["axes"].items():
            res = r["axes"][aid]
            if res is None:
                axes[aid] = {"status": "unavailable"}
                series[aid][season] = None
                continue
            liv = res["scores"].get(club)
            kpis = []
            for k in res["kpis"]:
                col = r["values"][k]
                sign = cfg["kpis"][k]["sign"]
                rank = competition_rank((sign * col).dropna()) if col.notna().any() else None
                kpis.append({"id": k, "liv": None if pd.isna(col.get(club)) else round(float(col[club]), 6), "mean": round(float(col.mean()), 6),
                             "rank": None if rank is None or club not in rank.index else int(rank[club]), "n": int(col.notna().sum())})
            clubs = sorted(r["values"].index)           # strip-plot arrays are aligned with this list (null = no score)
            axes[aid] = {"status": "computed", "origin": r["origin"][aid], "n": int(res["scores"].notna().sum()), "kpis": kpis,
                         "scores": [None if c not in res["scores"].index else round(float(res["scores"][c]), 2) for c in clubs],
                         "ranks": [None if c not in res["ranks"].index else int(res["ranks"][c]) for c in clubs],
                         "liv": None if liv is None or pd.isna(liv) else {"score": round(float(liv), 2), "rank": int(res["ranks"][club])}}
            series[aid][season] = None if liv is None or pd.isna(liv) else round(float(liv), 2)
        indices = {}
        for ph in cfg["phases"].values():
            iid = ph["index_id"]
            row = r["indices"][iid].loc[club] if club in r["indices"][iid].index else None
            score = None if row is None or pd.isna(row["score"]) else round(float(row["score"]), 2)
            prev = prev_idx.get(iid)
            # the change is the difference of the two displayed (whole-number) scores, so the page never shows 72 -> 67 as "+6"
            indices[iid] = {"score": score, "n_axes": None if row is None else int(row["n_axes"]), "partial": bool(row["partial"]) if row is not None else False,
                            "change": None if score is None or prev is None else math.floor(score + 0.5) - math.floor(prev + 0.5)}
            series[iid][season] = score
            prev_idx[iid] = score
        per_season[season] = {"n_clubs": r["n_clubs"], "matches": (matches or {}).get(season), "clubs": sorted(r["values"].index),
                              "axes": axes, "indices": indices}
    structure = {"transform": cfg["transform"], "club": club,
                 "phases": {k: {"label": p["label"], "index_id": p["index_id"], "axes": p["axes"]} for k, p in cfg["phases"].items()},
                 "axes": {a: {"phase": x["phase"], "label": x["label"], "left": x["left"], "right": x["right"], "status": x["status"],
                              "confidence": x["confidence"], "kpis": x["kpis"], "external_kpis": x["external_kpis"],
                              "unavailable_reason": x.get("unavailable_reason")} for a, x in cfg["axes"].items()},
                 "kpis": {k: {"sign": m["sign"], "available": m["available"]} for k, m in cfg["kpis"].items()}}
    return {"cfg": structure, "seasons": per_season, "series": series}


# ================================================================== shot-location classifiers
def box_zone(x: float, y: float) -> str:
    """'six' (six-yard box), 'pen' (penalty area outside the six-yard box) or 'out' for a shot at (x, y) in
    Understat's 0-1 shooter-perspective coordinates on a 105 x 68 m pitch (x -> 1 is the goal attacked)."""
    xm, ym = x * 105.0, y * 68.0
    if xm >= 105.0 - 5.5 and 24.84 <= ym <= 43.16:
        return "six"
    if xm >= 105.0 - 16.5 and 13.84 <= ym <= 54.16:
        return "pen"
    return "out"


def in_central_rectangle(x: float, y: float, cfg: dict | None = None) -> bool:
    """The configurable central rectangle in front of goal (config central_rectangle)."""
    c = (cfg or load_config())["central_rectangle"]
    return x >= c["x_min"] and abs(y - 0.5) <= c["y_half_width"] + EPS


def zone_counts(shots: pd.DataFrame) -> dict:
    """Shot counts by box zone; they always sum to len(shots)."""
    z = [box_zone(x, y) for x, y in zip(shots.x, shots.y)]
    return {k: z.count(k) for k in ("six", "pen", "out")}
