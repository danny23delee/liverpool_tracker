"""All derived metrics: registry access, baselines, change-vs-baseline, xG simulation, de-vig,
market probabilities, calibration, flat-stake P&L, mispriced runs and rule-based takeaways.

Nothing here does I/O except reading config and the optional external-model CSV. Inputs are the
processed tables from etl.py; build.py serialises the results for the dashboard.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
LIV = "Liverpool"

_DEFAULTS = {
    "devig_method": "proportional", "baseline_seasons": 2, "min_sample": 10, "form_matches": 5,
    "takeaway_thresholds": {"finishing_goals": 3.0, "market_points": 3.0, "xpts_points": 3.0,
                            "baseline_pct": 0.10, "form_strong_points": 10, "form_poor_points": 3,
                            "lowest_since_min_prior_seasons": 3},
    "mispriced": {"window": 10, "threshold_points": 4.0},
    "external_model_csv": "data/external/model_probs.csv",
}


def load_settings() -> dict:
    p = ROOT / "config" / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    out = {**_DEFAULTS, **s}
    out["takeaway_thresholds"] = {**_DEFAULTS["takeaway_thresholds"], **s.get("takeaway_thresholds", {})}
    out["mispriced"] = {**_DEFAULTS["mispriced"], **s.get("mispriced", {})}
    return out


SETTINGS = load_settings()
REGISTRY: dict[str, dict] = {m["id"]: m for m in json.loads((ROOT / "metrics.json").read_text(encoding="utf-8"))}
ERAS: list[dict] = json.loads((ROOT / "config" / "eras.json").read_text(encoding="utf-8"))


# ================================================================== change vs baseline
def _none_change() -> dict:
    return {"kind": None, "value": None, "direction": None, "sentiment": None}


def change(current, baseline, metric: str | dict) -> dict:
    """Change of `current` vs `baseline` for a registry metric.

    kind 'pct'  : fractional change (0.09 = +9%) for count/rate/per90 metrics; None if baseline is
                  null/0/negative (a % of zero or of a negative is meaningless).
    kind 'pp'   : percentage-point difference (in points, 3.2 = +3.2pp) for pct/prob metrics.
    kind 'abs'  : plain difference for `signed` metrics (goal difference, xG difference, ...) whose
                  values can cross zero, where a percentage would be impossible or misleading.
    Nulls give kind None ("N/A"). direction = sign of the change ('up'|'down'|'flat');
    sentiment = 'good'|'bad'|'neutral' from higher_is_better (xGA down -> good).
    """
    m = REGISTRY[metric] if isinstance(metric, str) else metric
    if current is None or baseline is None or _isnan(current) or _isnan(baseline):
        return _none_change()
    cur, base = float(current), float(baseline)
    unit = m["unit"]
    if unit in ("pct", "prob"):
        kind, value, shown = "pp", (cur - base) * 100.0, None
    elif m.get("signed"):
        kind, value = "abs", cur - base
    else:
        if base <= 0:
            return _none_change()
        kind, value = "pct", (cur - base) / base
    # direction from what the user would see: pct shown to 0.1%, pp to 0.1, abs to 0.01
    shown = round(value * 100, 1) if kind == "pct" else round(value, 1 if kind == "pp" else 2)
    direction = "flat" if shown == 0 else ("up" if value > 0 else "down")
    hib = m["higher_is_better"]
    if hib is None or direction == "flat":
        sentiment = "neutral"
    else:
        sentiment = "good" if (direction == "up") == bool(hib) else "bad"
    return {"kind": kind, "value": value, "direction": direction, "sentiment": sentiment}


def _isnan(x) -> bool:
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return False


# ================================================================== xG simulation (exact)
def goal_dist(xgs) -> np.ndarray:
    """Exact goal-count distribution: each shot is an independent Bernoulli(p = xG). Convolution
    of the per-shot [1-p, p] distributions (a Poisson-binomial), not Monte Carlo."""
    d = np.array([1.0])
    for p in xgs:
        p = min(max(float(p), 0.0), 1.0)
        n = np.zeros(len(d) + 1)
        n[:-1] += d * (1 - p)
        n[1:] += d * p
        d = n
    return d


def outcome_probs(xgs_for, xgs_against) -> tuple[float, float, float]:
    """(P(win), P(draw), P(loss)) for the 'for' side from the two exact goal distributions."""
    a, b = goal_dist(xgs_for), goal_dist(xgs_against)
    joint = np.outer(a, b)
    return float(np.tril(joint, -1).sum()), float(np.trace(joint)), float(np.triu(joint, 1).sum())


def xpts(p_win: float, p_draw: float) -> float:
    return 3.0 * p_win + p_draw


# ================================================================== de-vig
def devig_proportional(odds) -> np.ndarray:
    """Proportional (multiplicative) normalisation of inverse odds."""
    inv = _inv(odds)
    return inv / inv.sum()


def devig_shin(odds, tol: float = 1e-13) -> np.ndarray:
    """Shin (1993) method: solve for the insider-trading share z such that the implied true
    probabilities p_i = (sqrt(z^2 + 4(1-z) pi_i^2 / S) - z) / (2(1-z)) sum to 1 (pi_i = 1/odds_i,
    S = sum pi). Removes more of the margin from long shots than proportional does."""
    pi = _inv(odds)
    s = pi.sum()
    if s <= 1.0:  # no margin to remove
        return pi / s

    def total(z):
        return float(((np.sqrt(z * z + 4 * (1 - z) * pi ** 2 / s) - z) / (2 * (1 - z))).sum())

    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(200):
        mid = (lo + hi) / 2
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    z = (lo + hi) / 2
    p = (np.sqrt(z * z + 4 * (1 - z) * pi ** 2 / s) - z) / (2 * (1 - z))
    return p / p.sum()


def shin_z(odds) -> float:
    """The fitted insider share z (0 when the book has no margin)."""
    pi = _inv(odds)
    s = pi.sum()
    if s <= 1.0:
        return 0.0
    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(200):
        mid = (lo + hi) / 2
        t = float(((np.sqrt(mid * mid + 4 * (1 - mid) * pi ** 2 / s) - mid) / (2 * (1 - mid))).sum())
        lo, hi = (mid, hi) if t > 1.0 else (lo, mid)
    return (lo + hi) / 2


def _inv(odds) -> np.ndarray:
    o = np.asarray(odds, dtype=float)
    if not np.all(o > 1.0):
        raise ValueError(f"decimal odds must be > 1, got {odds}")
    return 1.0 / o


DEVIG = {"proportional": devig_proportional, "shin": devig_shin}


# ================================================================== enrich matches
_SET_PIECE = {"FromCorner", "SetPiece", "DirectFreekick"}


def assign_era(kickoff: pd.Series) -> pd.Series:
    d = kickoff.dt.tz_localize(None).dt.normalize() if kickoff.dt.tz is not None else kickoff.dt.normalize()
    out = pd.Series(["Unknown"] * len(d), index=d.index, dtype=object)
    for e in ERAS:
        lo = pd.Timestamp(e["from"])
        hi = pd.Timestamp(e["to"]) if e["to"] else pd.Timestamp.max
        out[(d >= lo) & (d <= hi)] = e["manager"]
    return out


def enrich_matches(matches: pd.DataFrame, shots: pd.DataFrame) -> pd.DataFrame:
    """Add shot-derived stats, exact xG-simulated probabilities/xPts, de-vigged market
    probabilities (both methods + the configured default), market xPts and flat-stake P&L."""
    m = matches.sort_values("kickoff_utc").reset_index(drop=True).copy()
    s = shots[shots.result != "OwnGoal"]  # own goals are not shots-with-a-probability
    groups = {k: g for k, g in s.groupby("match_id")}
    cols: dict[str, list] = {k: [] for k in (
        "shots_for", "shots_against", "goals_shots", "npxg", "npxga", "xg_openplay", "xga_openplay",
        "xga_setpiece", "sim_w", "sim_d", "sim_l")}
    empty = s.iloc[0:0]
    for mid, opp in zip(m.match_id, m.opponent):
        g = groups.get(mid, empty)
        lv, op = g[g.team == LIV], g[g.team == opp]
        cols["shots_for"].append(len(lv))
        cols["shots_against"].append(len(op))
        cols["goals_shots"].append(int((lv.result == "Goal").sum()))
        cols["npxg"].append(float(lv[lv.situation != "Penalty"].xg.sum()))
        cols["npxga"].append(float(op[op.situation != "Penalty"].xg.sum()))
        cols["xg_openplay"].append(float(lv[lv.situation == "OpenPlay"].xg.sum()))
        cols["xga_openplay"].append(float(op[op.situation == "OpenPlay"].xg.sum()))
        cols["xga_setpiece"].append(float(op[op.situation.isin(_SET_PIECE)].xg.sum()))
        w, d, l = outcome_probs(lv.xg.values, op.xg.values)
        cols["sim_w"].append(w), cols["sim_d"].append(d), cols["sim_l"].append(l)
    for k, v in cols.items():
        m[k] = v
    m["xpts_sim"] = 3 * m.sim_w + m.sim_d
    m["xg_per_shot"] = np.where(m.shots_for > 0, m.xg / m.shots_for.where(m.shots_for > 0), np.nan)
    m["clean_sheet"] = m.ga == 0

    # ---- market: home/draw/away odds -> Liverpool win/draw/loss, both de-vig methods
    for name, fn in DEVIG.items():
        probs = np.array([fn([h, d, a]) for h, d, a in zip(m.mkt_h, m.mkt_d, m.mkt_a)])
        home = m.is_home.values
        m[f"mp_{name}_w"] = np.where(home, probs[:, 0], probs[:, 2])
        m[f"mp_{name}_d"] = probs[:, 1]
        m[f"mp_{name}_l"] = np.where(home, probs[:, 2], probs[:, 0])
    dv = SETTINGS["devig_method"]
    for k in "wdl":
        m[f"mp_{k}"] = m[f"mp_{dv}_{k}"]
    m["xpts_market"] = 3 * m.mp_w + m.mp_d
    m["odds_win"] = np.where(m.is_home, m.mkt_h, m.mkt_a)
    m["overround"] = 1 / m.mkt_h + 1 / m.mkt_d + 1 / m.mkt_a - 1
    m["pnl"] = np.where(m.result == "W", m.odds_win - 1.0, -1.0)  # hypothetical flat 1-unit stake
    m["era"] = assign_era(m.kickoff_utc)
    return attach_external_model(m, load_external_model())


# ================================================================== external model hook
def load_external_model(path: str | Path | None = None) -> pd.DataFrame | None:
    """Optional external model probabilities. CSV columns: either `match_id` (Understat id) or
    `date` (yyyy-mm-dd, local match date) + `home_team` + `away_team` (canonical Understat names),
    plus `p_home`, `p_draw`, `p_away` (each in [0,1], summing to 1 within 1e-3). Returns None if
    the file does not exist."""
    p = Path(path) if path else ROOT / SETTINGS["external_model_csv"]
    if not p.exists():
        return None
    df = pd.read_csv(p)
    need = {"p_home", "p_draw", "p_away"}
    if not need <= set(df.columns) or not ({"match_id"} <= set(df.columns) or {"date", "home_team", "away_team"} <= set(df.columns)):
        raise ValueError("external model CSV needs match_id or date+home_team+away_team, and p_home,p_draw,p_away")
    pr = df[["p_home", "p_draw", "p_away"]].astype(float)
    if ((pr < 0) | (pr > 1)).any().any() or (pr.sum(axis=1) - 1).abs().max() > 1e-3:
        raise ValueError("external model probabilities must be in [0,1] and sum to 1 per row")
    return df


def attach_external_model(matches: pd.DataFrame, ext: pd.DataFrame | None) -> pd.DataFrame:
    """Adds mo_w/mo_d/mo_l (Liverpool orientation; NaN where the model has no row)."""
    m = matches.copy()
    for k in ("mo_w", "mo_d", "mo_l"):
        m[k] = np.nan
    if ext is None:
        return m
    if "match_id" in ext.columns:
        e = ext.set_index("match_id")
        key = m.match_id
    else:
        e = ext.assign(_k=list(zip(pd.to_datetime(ext.date).dt.normalize(), ext.home_team, ext.away_team))).set_index("_k")
        home = np.where(m.is_home, LIV, m.opponent)
        away = np.where(m.is_home, m.opponent, LIV)
        key = pd.Series(list(zip(m.fd_date.dt.normalize(), home, away)), index=m.index)
    if e.index.duplicated().any():
        raise ValueError("external model CSV has duplicate match keys")
    hit = key.isin(e.index)
    ph = key[hit].map(e.p_home).astype(float).values
    pd_ = key[hit].map(e.p_draw).astype(float).values
    pa = key[hit].map(e.p_away).astype(float).values
    home = m.loc[hit, "is_home"].values
    m.loc[hit, "mo_w"] = np.where(home, ph, pa)
    m.loc[hit, "mo_d"] = pd_
    m.loc[hit, "mo_l"] = np.where(home, pa, ph)
    return m


# ================================================================== calibration & staking
def outcome_index(result: pd.Series) -> np.ndarray:
    """W -> 0, D -> 1, L -> 2."""
    return result.map({"W": 0, "D": 1, "L": 2}).values.astype(int)


def brier(probs: np.ndarray, outcome: np.ndarray) -> float:
    """Multi-class Brier score: mean over matches of sum_k (p_k - o_k)^2."""
    probs = np.asarray(probs, float)
    onehot = np.eye(3)[np.asarray(outcome, int)]
    return float(((probs - onehot) ** 2).sum(axis=1).mean())


def log_loss(probs: np.ndarray, outcome: np.ndarray, eps: float = 1e-12) -> float:
    probs = np.asarray(probs, float)
    p = probs[np.arange(len(probs)), np.asarray(outcome, int)]
    return float(-np.log(np.clip(p, eps, 1.0)).mean())


def reliability(probs: np.ndarray, outcome: np.ndarray, bins: int = 10) -> list[dict]:
    """Reliability table pooling every (match, outcome) pair as a binary event: forecast
    probability vs observed frequency in equal-width bins."""
    probs = np.asarray(probs, float)
    onehot = np.eye(3)[np.asarray(outcome, int)]
    p, y = probs.ravel(), onehot.ravel()
    idx = np.minimum((p * bins).astype(int), bins - 1)
    out = []
    for b in range(bins):
        sel = idx == b
        if sel.any():
            out.append({"bin": b, "lo": b / bins, "hi": (b + 1) / bins, "n": int(sel.sum()),
                        "mean_p": float(p[sel].mean()), "observed": float(y[sel].mean())})
    return out


def calibration(m: pd.DataFrame) -> dict:
    """Brier/log-loss for market and xG-sim (and external model where present) on the matches given."""
    o = outcome_index(m.result)
    out = {"n": int(len(m))}
    for name, pre in (("market", "mp"), ("sim", "sim"), ("model", "mo")):
        cols = [f"{pre}_w", f"{pre}_d", f"{pre}_l"]
        if not set(cols) <= set(m.columns):
            continue
        if pre == "mo":
            ok = m[cols].notna().all(axis=1).values
            if not ok.any():
                continue
            P, oo = m.loc[ok, cols].values, o[ok]
        else:
            P, oo = m[cols].values, o
        out[name] = {"brier": brier(P, oo), "log_loss": log_loss(P, oo), "n": int(len(oo)),
                     "reliability": reliability(P, oo)}
    return out


def mispriced_runs(m: pd.DataFrame, window: int | None = None, threshold: float | None = None) -> list[dict]:
    """Stretches where results beat or lagged market expectation: every `window`-match window whose
    sum of (actual points - market-expected points) is >= threshold in size is flagged; overlapping
    windows of the same sign merge into one stretch."""
    window = window or SETTINGS["mispriced"]["window"]
    threshold = threshold or SETTINGS["mispriced"]["threshold_points"]
    m = m.sort_values("kickoff_utc").reset_index(drop=True)
    resid = (m.pts - m.xpts_market).values
    n = len(resid)
    runs = []
    for sign, name in ((1, "beat"), (-1, "lagged")):
        flagged = [(i - window + 1, i) for i in range(window - 1, n) if sign * resid[i - window + 1:i + 1].sum() >= threshold]
        merged: list[list[int]] = []
        for lo, hi in flagged:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1][1] = hi
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            seg = m.iloc[lo:hi + 1]
            runs.append({"direction": name, "start_idx": lo, "end_idx": hi, "n": int(len(seg)),
                         "from": seg.kickoff_utc.iloc[0], "to": seg.kickoff_utc.iloc[-1],
                         "points": int(seg.pts.sum()), "market_points": float(seg.xpts_market.sum()),
                         "diff": float(seg.pts.sum() - seg.xpts_market.sum())})
    return sorted(runs, key=lambda r: r["start_idx"])


# ================================================================== selection, baseline, summaries
def season_start(label: str) -> int:
    return int(label[:4])


def select(m: pd.DataFrame, season: str = "all", era: str = "all") -> pd.DataFrame:
    out = m
    if season != "all":
        out = out[out.season == season]
    if era != "all":
        out = out[out.era == era]
    return out


def baseline_for(m: pd.DataFrame, season: str, n: int | None = None) -> tuple[pd.DataFrame | None, str | None]:
    """Baseline = all matches in the previous `n` EPL seasons (default 2) before `season`; the seasons
    actually available are used, and the first season (2014-15) has none. Returns (matches, label)."""
    n = n or SETTINGS["baseline_seasons"]
    if season == "all":
        return None, None
    s0 = season_start(season)
    prev = m[(m.season_start >= s0 - n) & (m.season_start < s0)]
    if prev.empty:
        return None, None
    seasons = sorted(prev.season.unique())
    label = f"{seasons[0]} to {seasons[-1]}" if len(seasons) > 1 else seasons[0]
    return prev, f"{label} average per match"


RATE_SERIES = {
    "points_pm": lambda d: d.pts, "goals_pm": lambda d: d.gf, "goals_against_pm": lambda d: d.ga,
    "xg_pm": lambda d: d.xg, "xga_pm": lambda d: d.xga, "xgd_pm": lambda d: d.xg - d.xga,
    "npxg_pm": lambda d: d.npxg, "shots_pm": lambda d: d.shots_for, "shots_against_pm": lambda d: d.shots_against,
    "xg_per_shot": lambda d: d.xg_per_shot,
    "ppda": lambda d: d.ppda_att / d.ppda_def.where(d.ppda_def > 0),
    "deep_pm": lambda d: d.deep, "deep_allowed_pm": lambda d: d.deep_allowed,
    "xg_openplay_pm": lambda d: d.xg_openplay, "xga_openplay_pm": lambda d: d.xga_openplay,
    "xga_setpiece_pm": lambda d: d.xga_setpiece,
    "win_rate": lambda d: (d.result == "W").astype(float), "clean_sheet_rate": lambda d: (d.ga == 0).astype(float),
    "p_win_market": lambda d: d.mp_w, "p_win_sim": lambda d: d.sim_w, "overround": lambda d: d.overround,
}


def rate_value(d: pd.DataFrame, metric_id: str):
    """Mean of the per-match values of a rate/pct/prob metric (None when there is nothing to average)."""
    if d is None or d.empty:
        return None
    s = RATE_SERIES[metric_id](d).replace([np.inf, -np.inf], np.nan).dropna()
    return None if s.empty else float(s.mean())


def record(d: pd.DataFrame) -> dict:
    """Actuals (Python ints) and expected values (floats) for a set of matches."""
    n = len(d)
    w, dr, l = int((d.result == "W").sum()), int((d.result == "D").sum()), int((d.result == "L").sum())
    gf, ga = int(d.gf.sum()), int(d.ga.sum())
    xg, xga = float(d.xg.sum()), float(d.xga.sum())
    pts = int(d.pts.sum())
    xs, xm = float(d.xpts_sim.sum()), float(d.xpts_market.sum())
    gs = int(d.goals_shots.sum())
    return {
        "matches": n, "wins": w, "draws": dr, "losses": l, "points": pts, "goals_for": gf,
        "goals_against": ga, "goal_diff": gf - ga, "clean_sheets": int((d.ga == 0).sum()),
        "xg": xg, "xga": xga, "xgd": xg - xga, "npxg": float(d.npxg.sum()),
        "xpts_sim": xs, "xpts_market": xm, "xpts_understat": float(d.xpts_us.sum()),
        "goals_from_shots": gs, "goals_minus_xg": gs - xg,
        "points_minus_xpts": pts - xs, "points_minus_market": pts - xm,
        "pnl": float(d.pnl.sum()), "roi": float(d.pnl.sum() / n) if n else None,
    }


def rate_panel(sel: pd.DataFrame, base: pd.DataFrame | None) -> dict:
    """Per-match rate metrics for the selection vs the baseline, with change() applied."""
    out = {}
    for mid in RATE_SERIES:
        cur, b = rate_value(sel, mid), rate_value(base, mid)
        out[mid] = {"value": cur, "baseline": b, "change": change(cur, b, mid)}
    return out


def is_small_sample(n: int) -> bool:
    return n < SETTINGS["min_sample"]


# ================================================================== takeaways
def season_table(m: pd.DataFrame) -> pd.DataFrame:
    """Per-season per-match xG/xGA (all eras) for 'lowest/highest since' comparisons."""
    g = m.groupby("season")
    return pd.DataFrame({"n": g.size(), "xg_pm": g.xg.mean(), "xga_pm": g.xga.mean()})


def _signed(v: float, dp: int = 1, suffix: str = "") -> str:
    """'+6.8' / '−6.8' (true minus sign) for the big number on a takeaway tile."""
    return f"{'+' if v >= 0 else '−'}{abs(v):.{dp}f}{suffix}"


def _tk(id_, text, sentiment, *, tag, headline, direction, bars, **facts):
    """A takeaway. `text` and `facts` are the sentence and the numbers it cites; the tile fields
    (`tag`, `headline`, `direction`, `good`, `bars`) present the same numbers for the dashboard and
    are computed from the same values as the sentence, never separately. `good` follows the metric's
    higher_is_better (it is the sentiment the sentence was already given)."""
    return {"id": id_, "text": text, "sentiment": sentiment, "facts": facts, "tag": tag, "headline": headline,
            "direction": direction, "good": sentiment == "positive", "bars": bars}


def takeaways(sel: pd.DataFrame, base: pd.DataFrame | None = None, base_label: str | None = None,
              seasons: pd.DataFrame | None = None, season: str | None = None) -> list[dict]:
    """Rule-based takeaways. Each cites only numbers computed from the data (returned in `facts`,
    rounded exactly as shown in `text`). Rules needing significance are suppressed below the
    min_sample threshold. If no rule fires, the list is empty."""
    T = SETTINGS["takeaway_thresholds"]
    out: list[dict] = []
    n = len(sel)
    if n == 0:
        return out
    sel = sel.sort_values("kickoff_utc")

    # -- form (a plain description of the latest matches: allowed on small samples)
    k = SETTINGS["form_matches"]
    if n >= k:
        last = sel.tail(k)
        pts = int(last.pts.sum())
        w, d, l = (int((last.result == r).sum()) for r in "WDL")
        rec = f"{w}W {d}D {l}L"
        form_bars = [{"label": f"Last {k}", "value": pts}, {"label": "Available", "value": 3 * k}]
        if pts >= T["form_strong_points"]:
            out.append(_tk("form", f"Strong form: {pts} points from the last {k} matches ({rec})", "positive",
                           tag="RECENT FORM", headline=f"{pts} pts", direction="up", bars=form_bars,
                           points_last=pts, n=k, wins=w, draws=d, losses=l))
        elif pts <= T["form_poor_points"]:
            out.append(_tk("form", f"Poor form: {pts} points from the last {k} matches ({rec})", "negative",
                           tag="RECENT FORM", headline=f"{pts} pts", direction="down", bars=form_bars,
                           points_last=pts, n=k, wins=w, draws=d, losses=l))

    if is_small_sample(n):
        return out

    r = record(sel)
    # -- finishing vs xG
    diff = r["goals_minus_xg"]
    if abs(diff) >= T["finishing_goals"]:
        above = diff > 0
        out.append(_tk("finishing", f"Finishing {abs(diff):.1f} goals {'above' if above else 'below'} xG "
                       f"({r['goals_from_shots']} goals from {r['xg']:.1f} xG)",
                       "positive" if above else "negative",
                       tag="GOALS VS XG", headline=_signed(round(diff, 1)), direction="up" if above else "down",
                       bars=[{"label": "Goals", "value": r["goals_from_shots"]}, {"label": "xG", "value": round(r["xg"], 1)}],
                       goals=r["goals_from_shots"], xg=round(r["xg"], 1), diff=round(abs(diff), 1)))
    # -- results vs market
    dm = r["points_minus_market"]
    if abs(dm) >= T["market_points"]:
        ahead = dm > 0
        out.append(_tk("market", f"Results {'ahead of' if ahead else 'trailing'} the market by {abs(dm):.1f} points "
                       f"({r['points']} points vs {r['xpts_market']:.1f} market-expected)",
                       "positive" if ahead else "negative",
                       tag="POINTS VS MARKET", headline=_signed(round(dm, 1)), direction="up" if ahead else "down",
                       bars=[{"label": "Points", "value": r["points"]}, {"label": "Market", "value": round(r["xpts_market"], 1)}],
                       points=r["points"], market_points=round(r["xpts_market"], 1), diff=round(abs(dm), 1)))
    # -- results vs xG-based points
    dx = r["points_minus_xpts"]
    if abs(dx) >= T["xpts_points"]:
        above = dx > 0
        out.append(_tk("xpts", f"Points {abs(dx):.1f} {'above' if above else 'below'} xG-based expectation "
                       f"({r['points']} vs {r['xpts_sim']:.1f} xPts)",
                       "positive" if above else "negative",
                       tag="POINTS VS XPTS", headline=_signed(round(dx, 1)), direction="up" if above else "down",
                       bars=[{"label": "Points", "value": r["points"]}, {"label": "xPts", "value": round(r["xpts_sim"], 1)}],
                       points=r["points"], xpts=round(r["xpts_sim"], 1), diff=round(abs(dx), 1)))
    # -- vs baseline (xG for / against per match)
    if base is not None and not base.empty and not is_small_sample(len(base)):
        for mid, nice in (("xga_pm", "xGA"), ("xg_pm", "xG")):
            cur, b = rate_value(sel, mid), rate_value(base, mid)
            ch = change(cur, b, mid)
            if ch["kind"] == "pct" and abs(ch["value"]) >= T["baseline_pct"]:
                up = ch["value"] > 0
                out.append(_tk(f"baseline_{mid}", f"{nice} per match {cur:.2f}, {abs(ch['value']) * 100:.0f}% "
                               f"{'higher' if up else 'lower'} than the {base_label} of {b:.2f}",
                               "positive" if ch["sentiment"] == "good" else "negative",
                               tag=f"{nice} PER MATCH", headline=_signed(round(ch["value"] * 100), 0, "%"), direction="up" if up else "down",
                               bars=[{"label": "Selection", "value": round(cur, 2)}, {"label": "Baseline", "value": round(b, 2)}],
                               value=round(cur, 2), baseline=round(b, 2), pct=round(abs(ch["value"]) * 100)))
    # -- best/worst since (full single-season selections only)
    if seasons is not None and season and season != "all":
        prior = seasons[seasons.index < season]
        prior = prior[prior.n >= SETTINGS["min_sample"]]
        if len(prior) >= T["lowest_since_min_prior_seasons"] and season in seasons.index:
            # Only claims worth making: best-since (low xGA / high xG, a genuine run) and
            # worst-on-record (nothing as bad in the data). A "lowest xG since 2016" would be true
            # but noise, so the other combinations are not generated.
            for mid, nice, phrase in (("xga_pm", "xGA", "lowest"), ("xga_pm", "xGA", "highest"),
                                      ("xg_pm", "xG", "highest"), ("xg_pm", "xG", "lowest")):
                good = (phrase == "lowest") == (mid == "xga_pm")  # low xGA good, high xG good
                cur = float(seasons.loc[season, mid])
                as_extreme = (lambda v: v <= cur) if phrase == "lowest" else (lambda v: v >= cur)
                beaten = prior[prior[mid].map(as_extreme)]
                if as_extreme(prior[mid].iloc[-1]):  # previous season already as extreme: not a "since"
                    continue
                since = beaten.index[-1] if len(beaten) else None
                if not good and since is not None:
                    continue
                scope = f"since {since}" if since else f"in the data (from {prior.index[0]})"
                # the comparison is the season it is being measured against: the last one as extreme, or
                # (for a record) the most extreme earlier season
                if since:
                    other, other_label = float(prior.loc[since, mid]), since
                else:
                    other = float(prior[mid].max() if phrase == "highest" else prior[mid].min())
                    other_label = "Previous worst" if not good else "Previous best"
                out.append(_tk(f"extreme_{mid}_{phrase}",
                               f"{nice} per match {cur:.2f} is the {phrase} of any season {scope}",
                               "positive" if good else "negative",
                               tag=f"{nice} PER MATCH", headline=f"{cur:.2f}", direction="down" if phrase == "lowest" else "up",
                               bars=[{"label": "Selection", "value": round(cur, 2)}, {"label": other_label, "value": round(other, 2)}],
                               value=round(cur, 2), since=since))
    return out
