"""Build a SYNTHETIC raw-data cache in the exact shape of the cached Understat and football-data responses.

Why: the real sources cannot always be reached (CI works, a locked-down sandbox does not), and the multi-club code
(ETL, builder, themes, switcher) needs something to run on. Everything here is random but internally consistent
(shot xG sums, goals, rosters, odds), so it exercises the same invariants the real data must satisfy. It is
NOT real football data and is never used for the published site.

    TRACKER_DATA=/path/to/tree TRACKER_FIRST_SEASON=2023 python tests/fixtures/make_synthetic.py
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TRACKER_DATA", str(ROOT / "data"))
import etl  # noqa: E402
import metrics as M  # noqa: E402

TEAMS = ["Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Burnley", "Chelsea", "Crystal Palace", "Everton", "Fulham",
         "Leeds", "Liverpool", "Manchester City", "Manchester United", "Newcastle United", "Nottingham Forest", "Sunderland", "Tottenham",
         "West Ham", "Wolverhampton Wanderers"]
TO_FD = {v: k for k, v in etl.FD_TO_CANON.items()}
SITS = ["OpenPlay", "FromCorner", "SetPiece", "DirectFreekick", "Penalty"]
POS = ["GK", "DR", "DC", "DC", "DL", "DMC", "MC", "AMR", "AML", "AMC", "FW"]


def rounds(n_teams: int = 20):
    """Double round-robin (circle method): list of 38 rounds of (home, away) index pairs."""
    idx = list(range(n_teams))
    first = []
    for r in range(n_teams - 1):
        pairs = [(idx[i], idx[n_teams - 1 - i]) for i in range(n_teams // 2)]
        first.append([(a, b) if (r + i) % 2 == 0 else (b, a) for i, (a, b) in enumerate(pairs)])
        idx = [idx[0]] + [idx[-1]] + idx[1:-1]
    return first + [[(b, a) for a, b in rd] for rd in first]


def make_season(s: int, rng: random.Random, today: dt.date) -> None:
    raw = etl.RAW
    strength = {t: rng.uniform(0.7, 1.5) for t in TEAMS}
    start = dt.datetime(s, 8, 16, 15, 0)
    tid = {t: str(100 + i) for i, t in enumerate(TEAMS)}
    squads = {t: [(1000 * (i + 1) + k, f"{t.split()[0]} Player{k:02d}") for k in range(20)] for i, t in enumerate(TEAMS)}
    matches, mid = [], s * 1000
    for r, rd in enumerate(rounds()):
        for j, (a, b) in enumerate(rd):
            mid += 1
            when = start + dt.timedelta(days=7 * r + (j % 3), hours=(j % 4) * 2)
            matches.append(dict(id=mid, h=TEAMS[a], a=TEAMS[b], when=when, played=when.date() < today))
    played = [m for m in matches if m["played"]]
    tracked = {c["canonical"] for c in etl.CLUBS.values()}
    hist = {t: [] for t in TEAMS}
    stats = {t: {"situation": {}, "shotZone": {}, "attackSpeed": {}, "players": {}} for t in TEAMS}
    own_goals_for = {t: 0 for t in TEAMS}
    pgoals = {t: {p[0]: [p[1], 0] for p in squads[t]} for t in TEAMS}
    ptot: dict = {}   # (team, player id) -> [minutes, xG]

    def bump(d, key, shots, xg, goals, against):
        e = d.setdefault(key, {"shots": 0, "goals": 0, "xG": 0.0, "against": {"shots": 0, "goals": 0, "xG": 0.0}})
        tgt = e["against"] if against else e
        tgt["shots"] += shots; tgt["goals"] += goals; tgt["xG"] += xg

    for m in played:
        shots = {"h": [], "a": []}
        rosters = {"h": {}, "a": {}}
        goals = {"h": 0, "a": 0}
        for side, other in (("h", "a"), ("a", "h")):
            team, opp = m[side], m[other]
            lam = 11 * strength[team] / strength[opp] ** 0.6 * (1.1 if side == "h" else 0.92)
            n = max(1, round(rng.gauss(lam, 3)))
            squad = squads[team]
            xi = squad[:11]
            roster = {}
            for k, (pid, name) in enumerate(squad[:14]):
                mins = 90 if k < 11 else (rng.choice([0, 0, 25]) if k < 14 else 0)
                roster[pid] = dict(id=str(m["id"] * 100 + k + (50 if side == "a" else 0)), player_id=str(pid), player=name,
                                   position=POS[k] if k < 11 else "Sub", time=str(mins), goals=0, own_goals=0, shots=0, xG=0.0,
                                   assists=0, xA=0.0, key_passes=0, xGChain=0.0, xGBuildup=0.0, yellow_card=0, red_card=0)
            for _ in range(n):
                sit = rng.choices(SITS, [0.62, 0.17, 0.1, 0.03, 0.08 * 0.4])[0]
                xg = 0.76 if sit == "Penalty" else min(0.95, max(0.01, rng.betavariate(1.1, 9) * (1.4 if sit != "OpenPlay" else 1.0)))
                pid, name = rng.choice(xi[5:] + xi[7:] * 2)
                x = rng.uniform(0.7, 0.97) if sit != "Penalty" else 0.885
                y = rng.uniform(0.3, 0.7) if sit != "Penalty" else 0.5
                res = "Goal" if rng.random() < xg else rng.choice(["SavedShot", "BlockedShot", "MissedShots", "MissedShots", "ShotOnPost"])
                shots[side].append(dict(id=str(m["id"] * 100 + len(shots["h"]) + len(shots["a"])), minute=str(rng.randint(1, 94)), result=res,
                                        X=f"{x:.3f}", Y=f"{y:.3f}", xG=f"{xg:.6f}", player=name, h_a=side, player_id=str(pid), situation=sit,
                                        shotType=rng.choice(["RightFoot", "LeftFoot", "Head", "RightFoot"]), player_assisted=None, lastAction="Pass"))
                r_ = roster[pid]; r_["shots"] += 1; r_["xG"] += xg; r_["goals"] += res == "Goal"
                goals[side] += res == "Goal"
                if res == "Goal" and rng.random() < 0.7:
                    ap = rng.choice(xi[4:])[0]
                    if ap != pid:
                        roster[ap]["assists"] += 1; roster[ap]["xA"] += xg * 0.8
                for pid2 in rng.sample([p for p, _ in xi], 4):
                    roster[pid2]["xGChain"] += xg * rng.uniform(0.3, 1); roster[pid2]["xGBuildup"] += xg * rng.uniform(0, 0.4)
            if rng.random() < 0.01:   # an own goal by this side's player: benefits the other side, recorded with xG 0
                pid, name = rng.choice(xi)
                shots[side].append(dict(id=str(m["id"] * 100 + 98 + (side == "a")), minute=str(rng.randint(1, 94)), result="OwnGoal", X="0.05", Y="0.5", xG="0.000000",
                                        player=name, h_a=side, player_id=str(pid), situation="OpenPlay", shotType="OtherBodyPart", player_assisted=None, lastAction="None"))
                roster[pid]["own_goals"] += 1
                goals[other] += 1
            rosters[side] = {r["id"]: {**r, "time": r["time"], "goals": str(r["goals"]), "own_goals": str(r["own_goals"]), "shots": str(r["shots"]),
                                       "xG": f"{r['xG']:.6f}", "assists": str(r["assists"]), "xA": f"{r['xA']:.6f}", "key_passes": "1",
                                       "xGChain": f"{r['xGChain']:.6f}", "xGBuildup": f"{r['xGBuildup']:.6f}", "yellow_card": "0", "red_card": "0"}
                              for r in roster.values()}
            for pid, r in roster.items():
                pgoals[team][pid][1] += r["goals"]
                tot = ptot.setdefault((team, pid), [0, 0.0])
                tot[0] += int(r["time"]); tot[1] += r["xG"]
        m["shots"], m["rosters"], m["goals"] = shots, rosters, goals
        if m["h"] in tracked or m["a"] in tracked:
            (raw / "understat").mkdir(parents=True, exist_ok=True)
            (raw / "understat" / f"match_{m['id']}.json").write_text(json.dumps({"rosters": rosters, "shots": shots, "tmpl": {}}), encoding="utf-8")
        for side, other in (("h", "a"), ("a", "h")):
            team, opp = m[side], m[other]
            real = [x for x in shots[side] if x["result"] != "OwnGoal"]
            xg_for = sum(float(x["xG"]) for x in real)
            xg_ag = sum(float(x["xG"]) for x in shots[other] if x["result"] != "OwnGoal")
            gf, ga = goals[side], goals[other]
            w, d, l = M.outcome_probs([float(x["xG"]) for x in real], [float(x["xG"]) for x in shots[other] if x["result"] != "OwnGoal"])
            hist[team].append(dict(h_a=side, xG=xg_for, xGA=xg_ag, npxG=xg_for * 0.92, npxGA=xg_ag * 0.92,
                                   ppda={"att": rng.randint(250, 450), "def": rng.randint(20, 45)}, ppda_allowed={"att": rng.randint(250, 450), "def": rng.randint(20, 45)},
                                   deep=rng.randint(3, 14), deep_allowed=rng.randint(3, 14), scored=gf, missed=ga, xpts=3 * w + d,
                                   result="w" if gf > ga else ("d" if gf == ga else "l"), date=m["when"].strftime("%Y-%m-%d %H:%M:%S"),
                                   pts=3 if gf > ga else (1 if gf == ga else 0)))
            own_goals_for[team] += sum(1 for x in shots[other] if x["result"] == "OwnGoal")
            st = stats[team]
            for a_, shot_list, flip in ((False, shots[side], False), (True, shots[other], True)):
                sl = [x for x in shot_list if x["result"] != "OwnGoal"]
                for x in sl:
                    xgv, g_ = float(x["xG"]), int(x["result"] == "Goal")
                    bump(st["situation"], x["situation"], 1, xgv, g_, a_)
                    px, py = float(x["X"]) * 105, float(x["Y"]) * 68
                    zone = "shotSixYardBox" if px >= 99.5 and 24.84 <= py <= 43.16 else ("shotPenaltyArea" if px >= 88.5 and 13.84 <= py <= 54.16 else "shotOboxTotal")
                    bump(st["shotZone"], zone, 1, xgv, g_, a_)
                    bump(st["attackSpeed"], rng.choice(["Fast", "Normal", "Standard", "Slow"]), 1, xgv, g_, a_)
                ogs = sum(1 for x in shot_list if x["result"] == "OwnGoal")   # own goals count for the opponent as 1.0-xG goal "shots"
                if ogs:
                    tgt_against = not a_
                    # an own goal by `other`'s player benefits `team` (a_ False list is team's own shots; a_ True list is other's)
                    if a_:
                        bump(st["situation"], "OpenPlay", ogs, ogs * 1.0, ogs, False)
                        bump(st["shotZone"], "ownGoals", ogs, ogs * 1.0, ogs, False)
                        bump(st["attackSpeed"], "Normal", ogs, ogs * 1.0, ogs, False)
                    else:
                        bump(st["situation"], "OpenPlay", ogs, ogs * 1.0, ogs, True)
                        bump(st["shotZone"], "ownGoals", ogs, ogs * 1.0, ogs, True)
                        bump(st["attackSpeed"], "Normal", ogs, ogs * 1.0, ogs, True)
    complete = all(m["played"] for m in matches)
    league = {"teams": {tid[t]: {"id": tid[t], "title": t, "history": hist[t]} for t in TEAMS},
              "players": [{"id": str(pid), "player_name": nm, "team_title": t, "xGChain": "1.0", "xGBuildup": "0.4", "goals": str(g),
                           "time": str(ptot.get((t, pid), [0, 0.0])[0]), "xG": f"{ptot.get((t, pid), [0, 0.0])[1]:.6f}"}
                          for t in TEAMS for pid, (nm, g) in pgoals[t].items() if ptot.get((t, pid), [0])[0] > 0],
              "dates": [{"id": str(m["id"]), "isResult": m["played"], "h": {"id": tid[m["h"]], "title": m["h"]}, "a": {"id": tid[m["a"]], "title": m["a"]},
                         "datetime": m["when"].strftime("%Y-%m-%d %H:%M:%S")} for m in matches]}
    (raw / "understat").mkdir(parents=True, exist_ok=True)
    (raw / "understat" / f"league_EPL_{s}.json").write_text(json.dumps(league), encoding="utf-8")
    for t in TEAMS:
        for k in ("shotSixYardBox", "shotPenaltyArea", "shotOboxTotal", "ownGoals"):
            stats[t]["shotZone"].setdefault(k, {"shots": 0, "goals": 0, "xG": 0.0, "against": {"shots": 0, "goals": 0, "xG": 0.0}})
        players = [{"player_name": nm, "goals": str(g)} for pid, (nm, g) in pgoals[t].items()]
        payload = {"statistics": {k: stats[t][k] for k in ("situation", "shotZone", "attackSpeed")}, "players": players}
        (raw / "understat" / f"team_{t.replace(' ', '_')}_{s}.json").write_text(json.dumps(payload), encoding="utf-8")
    # football-data CSV with Pinnacle closing odds (margin ~3%)
    rows = ["Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA"]
    for m in played:
        sh, sa = strength[m["h"]] * 1.15, strength[m["a"]]
        ph, pa = sh / (sh + sa) * 0.75, sa / (sh + sa) * 0.75
        pd_ = 1 - ph - pa
        marg = 1.03
        o = [round(1 / (p * marg), 2) for p in (ph, pd_, pa)]
        gh, ga = m["goals"]["h"], m["goals"]["a"]
        rows.append(",".join([m["when"].strftime("%d/%m/%Y"), TO_FD.get(m["h"], m["h"]), TO_FD.get(m["a"], m["a"]), str(gh), str(ga),
                              "H" if gh > ga else ("D" if gh == ga else "A"), *map(str, o), *map(str, o)]))
    (raw / "football_data").mkdir(parents=True, exist_ok=True)
    (raw / "football_data" / f"E0_{s % 100:02d}{(s + 1) % 100:02d}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    rng = random.Random(2026)
    for s in etl.season_range():
        make_season(s, rng, dt.date.today())
        print("synthetic season", s)


if __name__ == "__main__":
    main()
