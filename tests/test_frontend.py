"""Frontend tests: Playwright against dist/index.html (run `python build.py` first).

Network is stubbed for determinism: D3 is served from the cached copy of the pinned cdnjs file and
Google Fonts requests get an empty stylesheet (the CSS has fallback stacks).
"""
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "index.html"
D3_LOCAL = ROOT / "data" / "raw" / "vendor" / "d3.min.js"
SHOTS = ROOT / "artifacts" / "screenshots"
PAGES = ["overview", "attack", "defence", "players", "match", "market", "methodology"]
SEASON_RE = re.compile(r"\b\d{4}-\d{2}\b")
BAD_TEXT = re.compile(r"NaN|undefined|Infinity|\bnull\b|\[object")

VISIBLE_TEXT_JS = """() => {
  // visible text of <main> and the page header, ignoring elements that deliberately cite other seasons
  const skip = el => el.closest('[data-baseline],[data-ref-season],script,style');
  const out = [];
  for (const root of [document.querySelector('#main'), document.querySelector('.titles')]) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const n = w.currentNode, p = n.parentElement;
      if (!p || skip(p)) continue;
      const cs = getComputedStyle(p);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      if (p.closest('details:not([open]) > :not(summary)')) continue;
      out.push(n.textContent);
    }
  }
  return out.join(' ');
}"""


def _data():
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', DIST.read_text(encoding="utf-8"), re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


@pytest.fixture(scope="session")
def data():
    if not DIST.exists():
        pytest.fail("run `python build.py` first")
    return _data()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:  # bundled chromium not installed (e.g. corporate proxy): use system Chrome
            b = p.chromium.launch(channel="chrome")
        yield b
        b.close()


def _stub_network(page):
    """Deterministic network: the pinned D3 build is served from the local cache when present (on a fresh CI
    runner it is fetched from the real CDN, where the page's SRI hash still protects it); fonts get an empty
    stylesheet because the CSS has fallback stacks."""
    if D3_LOCAL.exists():
        page.route("https://cdnjs.cloudflare.com/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=D3_LOCAL.read_bytes()))
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.route("https://fonts.gstatic.com/**", lambda r: r.fulfill(status=200, body=b""))


def _new_page(browser, width=1440, height=900, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}"))
    _stub_network(page)
    page.errors = errors
    return ctx, page


@pytest.fixture()
def page(browser):
    ctx, pg = _new_page(browser)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    yield pg
    ctx.close()


def go(page, route, season, era="all", extra=""):
    h = f"#/{route}?season={season}&era={era}{extra}"
    page.evaluate("""h => new Promise(r => { if (location.hash === h) return r();
        addEventListener('hashchange', () => setTimeout(r, 0), {once: true}); location.hash = h; })""", h)


def visible_text(page):
    return page.evaluate(VISIBLE_TEXT_JS)


def num(text):
    return int(text.strip().replace("−", "-").replace("+", "").replace(",", ""))


# ------------------------------------------------------------------ tests
def test_file_size_under_budget():
    assert DIST.stat().st_size < 5 * 1024 * 1024


def test_every_page_every_season_renders_without_console_errors(page, data):
    for route in PAGES:
        for season in ["all"] + data["seasons"]:
            go(page, route, season)
            assert page.text_content("#title"), (route, season)
            txt = page.inner_text("#main")
            assert not BAD_TEXT.search(txt), (route, season, BAD_TEXT.search(txt).group(0))
    assert page.errors == []


def test_no_other_season_labels_after_switching(page, data):
    for season in data["seasons"]:
        go(page, "overview", season)
        found = set(SEASON_RE.findall(visible_text(page)))
        assert found <= {season}, (season, found)


def test_record_values_match_build_json(page, data):
    for season in ["all"] + data["seasons"]:
        for era in ["all"] + [e["manager"].split()[-1].lower() for e in data["eras"]]:
            key_era = "all" if era == "all" else next(e["manager"] for e in data["eras"] if e["manager"].split()[-1].lower() == era)
            sel = data["selections"].get(f"{season}|{key_era}")
            go(page, "overview", season, era)
            if sel is None:
                assert page.locator("[data-testid=empty]").count() == 1, (season, era)
                continue
            r = sel["record"]
            for metric in ("wins", "draws", "losses", "points", "goals_for", "goals_against", "goal_diff", "clean_sheets"):
                assert num(page.inner_text(f"[data-metric={metric}]")) == r[metric], (season, era, metric)
            # expected values are shown to one decimal and never rendered as integers
            assert page.inner_text("[data-metric=xg]") == f"{r['xg']:,.1f}", (season, era)
            assert page.inner_text("[data-metric=xpts_market]") == f"{r['xpts_market']:,.1f}"
            assert r["wins"] + r["draws"] + r["losses"] == r["matches"] == sel["n"]


def test_small_sample_badge(page, data):
    small = [s for s in data["seasons"] if data["selections"][f"{s}|all"]["n"] < data["settings"]["min_sample"]]
    big = [s for s in data["seasons"] if data["selections"][f"{s}|all"]["n"] >= data["settings"]["min_sample"]]
    assert small and big, "need both kinds of season to test the badge"
    for s in small:
        go(page, "overview", s)
        assert page.locator("[data-testid=small-sample]").count() == 1, s
        assert not page.locator("[data-takeaway]:not([data-takeaway=form])").count(), "significance takeaways must be suppressed"
    for s in big:
        go(page, "overview", s)
        assert page.locator("[data-testid=small-sample]").count() == 0, s
    # an era slice below the threshold is also flagged
    go(page, "overview", "2015-16", "rodgers")
    assert page.locator("[data-testid=small-sample]").count() == 1


def test_baseline_panel_states_baseline_and_na(page, data):
    go(page, "overview", "2014-15")
    assert "first season" in page.inner_text("#main")
    assert "N/A" in page.inner_text("[data-row=xga_pm]") and "0%" not in page.inner_text("[data-row=xga_pm]")
    s = "2019-20"
    go(page, "overview", s)
    sel = data["selections"][f"{s}|all"]
    assert sel["baseline_label"] in page.inner_text("[data-baseline]")
    row = page.locator("[data-row=xga_pm] .delta")
    c = sel["rates"]["xga_pm"]["c"]
    assert c["kind"] == "pct"
    assert f"{c['value'] * 100:+.1f}%".replace("-", "−") in row.inner_text()
    # xGA up is bad -> coloured 'bad'; arrow follows the sign
    assert ("bad" if c["sentiment"] == "bad" else "good") in row.get_attribute("class")
    assert ("▲" if c["direction"] == "up" else "▼") in row.inner_text()


def test_info_tooltips_come_from_registry(page, data):
    go(page, "overview", "2024-25")
    buttons = page.locator("button.info")
    n = buttons.count()
    assert n >= 20
    seen = set()
    for i in range(n):
        b = buttons.nth(i)
        mid = b.get_attribute("data-info")
        if mid in seen:
            continue
        seen.add(mid)
        b.hover()
        tip = page.inner_text("#tip")
        assert data["registry"][mid]["description"] in tip, mid
        assert data["registry"][mid]["label"] in tip
    assert {"xg", "xga", "points", "xpts_sim", "xpts_market", "xgd_roll10"} <= seen


def test_routing_is_shareable_and_state_syncs(browser, data):
    ctx, pg = _new_page(browser)
    pg.goto(DIST.as_uri() + "#/overview?season=2022-23&era=klopp")
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    assert pg.input_value("#sel-season") == "2022-23" and pg.input_value("#sel-era") == "klopp"
    assert "2022-23" in pg.text_content("#title") and "Jürgen Klopp era" in pg.inner_text("#subtitle")
    pg.select_option("#sel-season", "2023-24")
    pg.wait_for_function("location.hash.includes('season=2023-24')")
    assert "era=klopp" in pg.evaluate("location.hash")
    pg.goto(DIST.as_uri() + "#/overview?season=1999-00&era=nonsense")  # invalid params fall back
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    assert pg.input_value("#sel-season") == data["default_season"] and pg.input_value("#sel-era") == "all"
    assert pg.errors == []
    ctx.close()


def test_titles_generated_from_selection(page, data):
    go(page, "overview", "2025-26")
    assert page.text_content("#title") == "Overview: 2025-26"
    assert "2025-26 season" in page.inner_text("#subtitle") and "38 matches" in page.inner_text("#subtitle")
    go(page, "overview", "2026-27")
    assert "5 of 38 matches played" in page.inner_text("#subtitle")
    go(page, "attack", "all")
    assert page.text_content("#title") == "Attack: all seasons"


def test_theme_toggle_and_dark_mode_render(browser):
    ctx, pg = _new_page(browser, scheme="dark")
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    bg_dark = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    pg.click("#theme-btn")
    assert pg.evaluate("document.documentElement.dataset.theme") == "light"
    assert pg.evaluate("getComputedStyle(document.body).backgroundColor") != bg_dark
    assert pg.errors == []
    ctx.close()


def test_no_horizontal_page_overflow_on_mobile(browser, data):
    ctx, pg = _new_page(browser, 390, 844)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    for route in PAGES:
        for season in (data["default_season"], "all", "2026-27"):
            go(pg, route, season)
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (route, season)
    assert pg.errors == []
    ctx.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (390, 844)])
def test_screenshots(browser, data, width, height):
    SHOTS.mkdir(parents=True, exist_ok=True)
    ctx, pg = _new_page(browser, width, height)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    for route in PAGES:
        go(pg, route, data["default_season"])
        pg.wait_for_timeout(50)
        pg.screenshot(path=str(SHOTS / f"{route}_{width}.png"), full_page=True)
    go(pg, "overview", "2026-27")
    pg.screenshot(path=str(SHOTS / f"overview_small-sample_{width}.png"), full_page=True)
    go(pg, "overview", "all")
    pg.screenshot(path=str(SHOTS / f"overview_all_{width}.png"), full_page=True)
    for route in ("attack", "market", "match"):  # the heaviest pages with every season
        go(pg, route, "all")
        pg.screenshot(path=str(SHOTS / f"{route}_all_{width}.png"), full_page=True)
    pg.evaluate("document.documentElement.dataset.theme = 'dark'")
    for route in PAGES:
        go(pg, route, data["default_season"])
        pg.screenshot(path=str(SHOTS / f"{route}_dark_{width}.png"), full_page=True)
    assert pg.errors == []
    ctx.close()


# ------------------------------------------------------------------ M4: Attack and Defence pages
def _shots(season, liverpool=True):
    import pandas as pd
    s = pd.read_parquet(ROOT / "data" / "processed" / "shots.parquet")
    s = s[(s.season == season) & (s.result != "OwnGoal")]
    return s[s.team == "Liverpool"] if liverpool else s[s.team != "Liverpool"]


def _circles(page, chart):
    return sorted((round(float(a), 3), round(float(b), 3)) for a, b in page.eval_on_selector_all(
        f"[data-chart={chart}] circle.shot", "els => els.map(e => [e.getAttribute('cx'), e.getAttribute('cy')])"))


def _table_rows(locator):
    return locator.evaluate_all("trs => trs.map(t => [...t.children].map(c => c.textContent))")


def test_attack_shot_map_stats_and_filters_match_data(page):
    go(page, "attack", "2024-25")
    liv = _shots("2024-25")

    def n():
        return int(page.inner_text("[data-stat=shots]").replace(",", ""))

    assert n() == len(liv)
    assert int(page.inner_text("[data-stat=goals]")) == int((liv.result == "Goal").sum())
    assert page.inner_text("[data-stat=xg]") == f"{liv.xg.sum():.1f}"
    assert page.inner_text("[data-stat=xgps]") == f"{liv.xg.sum() / len(liv):.3f}"
    # circle size grows with xG: the biggest circle is the biggest shot
    radii = page.eval_on_selector_all("[data-chart=attack] circle.shot", "els => els.map(e => +e.getAttribute('r'))")
    assert len(radii) <= len(liv) and max(radii) > 2.5 * min(radii)
    page.select_option("#f-player", label="Mohamed Salah")
    sal = liv[liv.player == "Mohamed Salah"]
    assert n() == len(sal) and page.inner_text("[data-stat=xg]") == f"{sal.xg.sum():.1f}"
    page.select_option("#f-sit", "Penalty")
    pen = sal[sal.situation == "Penalty"]
    assert n() == len(pen) and int(page.inner_text("[data-stat=goals]")) == int((pen.result == "Goal").sum())
    page.select_option("#f-player", "all")
    page.select_option("#f-sit", "all")
    page.select_option("#f-typ", "Head")
    assert n() == int((liv.shot_type == "Head").sum())
    page.select_option("#f-typ", "all")
    page.select_option("#f-res", "Goal")
    assert n() == int((liv.result == "Goal").sum())
    assert page.locator("[data-chart=attack] circle.shot").count() == n()


def test_pitch_proportions_and_transforms(page):
    go(page, "attack", "2025-26")
    pitch = page.eval_on_selector("[data-chart=attack] svg rect", "e => [+e.getAttribute('width'), +e.getAttribute('height')]")
    assert pitch == [105, 68]  # true 105 x 68 m proportions
    liv = _shots("2025-26")
    exp = sorted((round(105 * x, 3), round(68 * y, 3)) for x, y in zip(liv.x, liv.y) if 105 * x >= 36)
    assert _circles(page, "attack") == exp  # attack drawn as-is, towards the right goal
    go(page, "defence", "2025-26")
    opp = _shots("2025-26", liverpool=False)
    exp = sorted((round(105 * (1 - x), 3), round(68 * (1 - y), 3)) for x, y in zip(opp.x, opp.y) if 105 * (1 - x) <= 69)
    assert _circles(page, "defence") == exp  # opposition shots rotated: Liverpool defend the left goal
    assert int(page.inner_text("[data-stat=shots]")) == len(opp)


def test_finishing_chart_and_source_mix_match_data(page):
    go(page, "attack", "2024-25")
    liv = _shots("2024-25")
    card = page.locator("section[aria-label='Goals minus xG']")
    rows = _table_rows(card.locator("tbody tr"))
    g = liv.groupby("player").agg(sh=("xg", "size"), goals=("result", lambda x: int((x == "Goal").sum())), xg=("xg", "sum"))
    g = g[g.sh >= 5]
    assert {r[0] for r in rows} == set(g.index)
    for name, shots, goals, xg, diff in rows:
        r = g.loc[name]
        assert int(shots) == r.sh and int(goals) == r.goals and xg == f"{r.xg:.2f}", name
        assert diff.replace("−", "-") in (f"{r.goals - r.xg:+.2f}", "0.00")
    # excluding penalties switches to non-penalty goals and npxG
    card.get_by_role("button", name="Excluding penalties").click()
    rows = _table_rows(card.locator("tbody tr"))
    npl = liv[liv.situation != "Penalty"].groupby("player").agg(goals=("result", lambda x: int((x == "Goal").sum())), xg=("xg", "sum"))
    for name, shots, goals, xg, diff in rows:
        assert int(goals) == npl.loc[name].goals and xg == f"{npl.loc[name].xg:.2f}", name
    # source mix: xG by source equals pandas
    mix = _table_rows(page.locator("section[aria-label='Threat source mix']").locator("tbody tr"))
    src = liv.situation.map({"OpenPlay": "Open play", "FromCorner": "Set pieces", "SetPiece": "Set pieces",
                             "DirectFreekick": "Set pieces", "Penalty": "Penalties"})
    for _, name, xg, shots, goals in mix:
        x = liv[src == name]
        assert xg == f"{x.xg.sum():.1f}" and int(shots) == len(x) and int(goals) == int((x.result == "Goal").sum()), name
    assert sum(float(r[2]) for r in mix) == pytest.approx(liv.xg.sum(), abs=0.2)


def test_scatter_league_context(page):
    go(page, "attack", "2025-26")
    assert page.locator("[data-chart=scatter] circle").count() == 20  # 19 other clubs + Liverpool
    liv = _shots("2025-26")
    rows = _table_rows(page.locator("section[aria-label='Shot volume vs shot quality'] tbody tr"))
    lfc = next(r for r in rows if r[0] == "Liverpool")
    assert lfc[1] == f"{len(liv) / 38:.1f}" and lfc[2] == f"{liv.xg.sum() / len(liv):.3f}"
    assert len(rows) == 20
    go(page, "attack", "all")
    assert page.locator("[data-chart=scatter] circle").count() == 20 * 13  # every club-season, Liverpool 13 highlighted


def test_defence_charts_and_clean_sheets(page, data):
    for season in ("2024-25", "2019-20"):
        go(page, "defence", season)
        sel = data["selections"][f"{season}|all"]
        assert page.locator("[data-chart=xga-trend] rect.bar").count() == sel["n"]
        assert page.locator("[data-strip=clean-sheets] i").count() == sel["n"]
        assert page.locator("[data-strip=clean-sheets] i.cs").count() == sel["record"]["clean_sheets"]
        assert num(page.inner_text("[data-metric=clean_sheets]")) == sel["record"]["clean_sheets"]
        assert page.locator("[data-chart=xga-trend] line[stroke-dasharray]").count() == 1  # league average line
        mix = page.locator("section[aria-label='Open play vs set piece xGA'] tbody tr").evaluate_all("trs => trs.map(t => t.children[2].textContent)")
        assert sum(float(x) for x in mix) == pytest.approx(sel["record"]["xga"], abs=0.2)
    go(page, "defence", "all")
    assert page.locator("[data-chart=xga-trend] line[stroke-dasharray]").count() == 13  # per-season league averages
    assert page.locator("section[aria-label='Open play vs set piece xGA'] .mixrow").count() == 13


def test_attack_defence_small_sample_and_empty_states(page):
    go(page, "attack", "2026-27")
    assert page.locator("[data-testid=small-sample]").count() == 1
    go(page, "defence", "2026-27")
    assert page.locator("[data-strip=clean-sheets] i").count() == 5
    go(page, "attack", "2025-26", "iraola")
    assert page.locator("[data-testid=empty]").count() == 1


def test_attack_defence_titles_have_registry_tooltips_and_table_views(page, data):
    for route in ("attack", "defence"):
        go(page, route, "2024-25")
        titles = page.locator("section.card > h2 > button.info")
        assert titles.count() >= 4, route
        for i in range(titles.count()):
            mid = titles.nth(i).get_attribute("data-info")
            titles.nth(i).hover()
            assert data["registry"][mid]["description"] in page.inner_text("#tip"), mid
        assert page.locator("details.tbl").count() >= 4, route


# ------------------------------------------------------------------ M5: Players and Match Explorer
def _players(season):
    """Independent per-player totals for a season from the processed rosters and shots."""
    import pandas as pd
    r = pd.read_parquet(ROOT / "data" / "processed" / "rosters.parquet")
    r = r[(r.season == season) & (r.team == "Liverpool")]
    g = r.groupby("player").agg(pid=("player_id", "first"), apps=("minutes", lambda x: int((x > 0).sum())), minutes=("minutes", "sum"),
                                goals=("goals", "sum"), ast=("assists", "sum"), xa=("xa", "sum"), kp=("key_passes", "sum"),
                                chain=("xgchain", "sum"), build=("xgbuildup", "sum"))
    s = _shots(season)
    a = s.groupby("player").agg(sh=("xg", "size"), xg=("xg", "sum"))
    n = s[s.situation != "Penalty"].groupby("player").agg(npxg=("xg", "sum"), npg=("result", lambda x: int((x == "Goal").sum())))
    g = g.join(a).join(n).fillna(0)
    g = g[g.minutes > 0].copy()
    for k, v in (("npxg90", "npxg"), ("xa90", "xa"), ("build90", "build"), ("chain90", "chain"), ("sh90", "sh"), ("kp90", "kp"), ("xg90", "xg")):
        g[k] = g[v] / g.minutes * 90
    g["fin"] = g.npg - g.npxg
    return g


def _flt(text):
    return float(text.replace("−", "-").replace(",", "").replace("+", ""))


SQUAD_KEYS = ["name", "pos", "apps", "minutes", "goals", "npg", "ast", "xg", "npxg", "xa", "fin", "npxg90", "xa90", "build90", "chain90", "sh90", "kp90", "xg90"]


def test_squad_table_matches_data_and_filters(page):
    go(page, "players", "2024-25")
    g = _players("2024-25")
    big = g[g.minutes >= 450]
    rows = _table_rows(page.locator("table[data-table=squad] tbody tr"))
    assert len(rows) == len(big) and {r[0] for r in rows} == set(big.index)
    for r in rows:
        p = big.loc[r[0]]
        d = dict(zip(SQUAD_KEYS, r))
        assert int(d["apps"]) == p.apps and _flt(d["minutes"]) == p.minutes and int(d["goals"]) == p.goals and int(d["npg"]) == p.npg and int(d["ast"]) == p.ast, r[0]
        for k in ("xg", "npxg", "xa", "fin"):
            assert abs(_flt(d[k]) - p[k]) < 0.06, (r[0], k)
        for k in ("npxg90", "xa90", "build90", "chain90", "sh90", "kp90", "xg90"):
            assert abs(_flt(d[k]) - p[k]) < 0.006, (r[0], k)
    assert page.inner_text("[data-testid=player-count]") == f"Showing {len(big)} of {len(g)} players"
    # minimum-minutes filter and search
    page.select_option("#p-min", "1500")
    assert page.locator("table[data-table=squad] tbody tr").count() == int((g.minutes >= 1500).sum())
    page.select_option("#p-min", "0")
    assert page.locator("table[data-table=squad] tbody tr").count() == len(g)
    page.fill("#p-search", "salah")
    assert page.locator("table[data-table=squad] tbody tr").count() == 1
    assert page.inner_text("table[data-table=squad] tbody tr td") == "Mohamed Salah"
    page.fill("#p-search", "zzzz")
    assert page.locator("table[data-table=squad] tbody tr[data-pid]").count() == 0
    assert page.locator("[data-testid=no-players]").count() == 1 and "No players match" in page.inner_text("[data-testid=no-players]")
    assert "No players above the filter" in page.inner_text("[data-role=Creator]")
    page.fill("#p-search", "")
    # sorting: default minutes descending, click flips it
    page.select_option("#p-min", "450")
    mins = [_flt(r[3]) for r in _table_rows(page.locator("table[data-table=squad] tbody tr"))]
    assert mins == sorted(mins, reverse=True)
    page.locator("table[data-table=squad] th").nth(3).locator("button.sortbtn").click()
    mins = [_flt(r[3]) for r in _table_rows(page.locator("table[data-table=squad] tbody tr"))]
    assert mins == sorted(mins)


def test_role_leaders_match_data(page):
    go(page, "players", "2024-25")
    big = _players("2024-25")
    big = big[big.minutes >= 450]
    for title, col in (("Finisher", "fin"), ("Shot Threat", "npxg90"), ("Creator", "xa90"), ("Build-up", "build90"), ("Involvement", "chain90")):
        exp = list(big.sort_values([col, "minutes"], ascending=False).index[:3])
        got = page.locator(f"[data-role='{title}'] li .nm").all_inner_texts()
        assert got == exp, (title, got, exp)
    # the leaders respond to the minimum-minutes filter
    page.select_option("#p-min", "1500")
    hi = big[big.minutes >= 1500]
    assert page.locator("[data-role='Creator'] li .nm").all_inner_texts() == list(hi.sort_values(["xa90", "minutes"], ascending=False).index[:3])
    assert f"{len(hi)} players" in page.inner_text("[data-testid=roles-note]")


def test_player_profile_and_comparison(page, data):
    import pandas as pd
    go(page, "players", "2024-25")
    page.locator("table[data-table=squad] tbody tr", has_text="Mohamed Salah").first.click()
    assert "player=1250" in page.evaluate("location.hash")
    assert page.text_content("[data-profile] h3") == "Mohamed Salah"
    ps = pd.read_parquet(ROOT / "data" / "processed" / "player_seasons.parquet")
    sal = ps[(ps.player_id == 1250) & (ps.minutes > 0)].sort_values("season")
    prof = page.locator("section[aria-label='Player profile']")
    rows = _table_rows(prof.locator("details.tbl tbody tr"))
    assert [r[0] for r in rows] == list(sal.season)
    for r, (_, p) in zip(rows, sal.iterrows()):
        assert int(r[3]) == p.minutes and int(r[4]) == p.goals and int(r[5]) == p.npg and int(r[6]) == p.assists, r[0]
    assert prof.locator("[data-mini=npxg_p90] circle.pt").count() == len(sal)
    hollow = prof.locator("[data-mini=npxg_p90] circle.pt").evaluate_all("cs => cs.filter(c => c.style.fill.includes('surface')).length")
    assert hollow == int((sal.minutes < 450).sum())
    # comparison
    page.select_option("#cmp-a", "1250")
    page.select_option("#cmp-b", label="Virgil van Dijk")
    g = _players("2024-25")
    for name, tag in (("Mohamed Salah", "a"), ("Virgil van Dijk", "b")):
        assert abs(_flt(page.inner_text(f"[data-cmp='npxg_p90:{tag}'] b")) - g.loc[name, "npxg90"]) < 0.006
        assert _flt(page.inner_text(f"[data-cmp='minutes:{tag}'] b")) == g.loc[name, "minutes"]
        assert abs(_flt(page.inner_text(f"[data-cmp='npg_minus_npxg:{tag}'] b")) - g.loc[name, "fin"]) < 0.06
    assert f"vs={g.loc['Virgil van Dijk', 'pid']}" in page.evaluate("location.hash")


def test_players_url_is_shareable(browser):
    ctx, pg = _new_page(browser)
    pg.goto(DIST.as_uri() + "#/players?season=2024-25&era=all&player=8260&vs=1250")
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    assert pg.input_value("#cmp-b") == "1250" and pg.inner_text("[data-profile] h3") != ""
    assert pg.errors == []
    ctx.close()


def test_players_small_sample_selection_defaults_lower_minimum(page):
    go(page, "players", "2026-27")
    assert page.locator("[data-testid=small-sample]").count() == 1
    assert page.input_value("#p-min") == "90" and page.locator("table[data-table=squad] tbody tr").count() > 5
    go(page, "players", "2015-16", "rodgers")
    assert page.locator("table[data-table=squad] tbody tr").count() > 0


def test_every_match_explorer_page_reconciles_with_data(page, data):
    import pandas as pd
    import metrics as MT
    m = pd.read_parquet(ROOT / "data" / "processed" / "matches.parquet").set_index("match_id")
    shots = pd.read_parquet(ROOT / "data" / "processed" / "shots.parquet")
    shots_by = {k: g for k, g in shots.groupby("match_id")}
    for row in data["matches"]:
        mid = row["id"]
        go(page, "match", "all", "all", f"&match={mid}")
        r = m.loc[mid]
        sh = shots_by[mid]
        hg, ag = (r.gf, r.ga) if r.is_home else (r.ga, r.gf)
        assert page.inner_text("[data-testid=scoreline]").split("\n")[0] == f"{hg}–{ag}", mid
        home, away = ("Liverpool", r.opponent) if r.is_home else (r.opponent, "Liverpool")
        assert page.text_content("#title") == f"Match Explorer: {home} {hg}–{ag} {away}", mid
        # xG of both teams = sum of that team's shots
        lx, ox = sh[sh.team == "Liverpool"].xg.sum(), sh[sh.team != "Liverpool"].xg.sum()
        assert page.text_content("[data-race-final=lfc]").split()[0] == f"{lx:.2f}", mid
        assert page.text_content("[data-race-final=opp]").split()[0] == f"{ox:.2f}", mid
        # goals: markers and scorer lists equal the score (own goals credited to the other side)
        assert page.locator("circle.goal-mark").count() == r.gf + r.ga, mid
        assert page.locator("[data-goal=lfc]").count() == r.gf and page.locator("[data-goal=opp]").count() == r.ga, mid
        assert page.locator("[data-chart=match-pitch] circle.shot").count() == int((sh.result != "OwnGoal").sum()), mid
        # market panel: independent de-vig and exact simulation
        odds = [r.mkt_h, r.mkt_d, r.mkt_a]
        prop = MT.devig_proportional(odds)
        exp = list(prop[:3]) if r.is_home else list(prop[::-1])
        cells = _table_rows(page.locator("table[data-table=probs] tbody tr"))
        assert all(abs(_flt(cells[i][1].rstrip("%")) - exp[i] * 100) <= 0.06 for i in range(3)), mid
        s = sh[sh.result != "OwnGoal"]
        w, d_, l = MT.outcome_probs(s[s.team == "Liverpool"].xg.values, s[s.team != "Liverpool"].xg.values)
        assert all(abs(_flt(cells[i][3].rstrip("%")) - v * 100) <= 0.06 for i, v in enumerate((w, d_, l))), mid
        assert page.inner_text("[data-testid=actual-result]") == {"W": "Win", "D": "Draw", "L": "Loss"}[r.result]
    assert page.errors == []


def test_match_picker_steps_and_urls(page, data):
    go(page, "match", "2024-25")
    ids = [m["id"] for m in data["matches"] if m["s"] == "2024-25"]
    assert f"match={ids[-1]}" in page.evaluate("location.hash")  # defaults to the latest match in the selection
    page.click("[data-testid=prev-match]")
    assert f"match={ids[-2]}" in page.evaluate("location.hash")
    page.click("[data-testid=next-match]")
    assert f"match={ids[-1]}" in page.evaluate("location.hash")
    assert page.locator("[data-testid=next-match]").is_disabled()
    page.select_option("#m-pick", str(next(i for i, m in enumerate(data["matches"]) if m["id"] == ids[0])))
    assert page.locator("[data-testid=prev-match]").is_disabled() and f"match={ids[0]}" in page.evaluate("location.hash")
    assert page.locator("#m-pick option").count() == 38
    go(page, "match", "2023-24")  # a match from another season falls back to that season's latest match
    ids2 = [m["id"] for m in data["matches"] if m["s"] == "2023-24"]
    assert f"match={ids2[-1]}" in page.evaluate("location.hash")


def test_match_explorer_no_other_season_labels(page, data):
    for season in ("2019-20", "2024-25"):
        go(page, "match", season)
        assert set(SEASON_RE.findall(visible_text(page))) <= {season}
        go(page, "players", season)
        assert set(SEASON_RE.findall(visible_text(page))) <= {season}


# ------------------------------------------------------------------ M6: Market Lens and Methodology
def _lens(season, era=None):
    """Independent market-lens inputs for a season from the processed tables (own de-vig, own scoring)."""
    import numpy as np
    import pandas as pd
    import metrics as MT
    m = pd.read_parquet(ROOT / "data" / "processed" / "matches.parquet")
    sh = pd.read_parquet(ROOT / "data" / "processed" / "shots.parquet")
    m = m[m.season == season].sort_values("kickoff_utc").reset_index(drop=True)
    inv = 1 / m[["mkt_h", "mkt_d", "mkt_a"]].values
    prop = inv / inv.sum(axis=1, keepdims=True)                      # proportional de-vig, written out here
    pm = np.where(m.is_home.values[:, None], prop, prop[:, ::-1])    # Liverpool W, D, L
    sh = sh[(sh.result != "OwnGoal") & sh.match_id.isin(m.match_id)]
    sim = []
    for r in m.itertuples():
        g = sh[sh.match_id == r.match_id]
        sim.append(MT.outcome_probs(g[g.team == "Liverpool"].xg.values, g[g.team != "Liverpool"].xg.values))
    o = m.result.map({"W": 0, "D": 1, "L": 2}).values
    win_odds = np.where(m.is_home, m.mkt_h, m.mkt_a)
    return m, pm, np.array(sim), o, win_odds


def _score(P, o):
    import numpy as np
    onehot = np.eye(3)[o]
    return float(((P - onehot) ** 2).sum(axis=1).mean()), float(-np.log(P[np.arange(len(o)), o]).mean())


def test_market_lens_calibration_matches_independent_scores(page):
    go(page, "market", "2024-25")
    m, pm, sim, o, _ = _lens("2024-25")
    rows = {r[0]: r for r in _table_rows(page.locator("table[data-table=calibration] tbody tr"))}
    keys = list(rows)
    b, ll = _score(pm, o)
    assert rows[keys[0]][1] == f"{b:.3f}" and rows[keys[0]][2] == f"{ll:.3f}" and int(rows[keys[0]][3]) == len(m)
    b, ll = _score(sim, o)
    assert rows[keys[1]][1] == f"{b:.3f}" and rows[keys[1]][2] == f"{ll:.3f}"
    import numpy as np
    freq = np.bincount(o, minlength=3) / len(o)
    b, ll = _score(np.tile(freq, (len(o), 1)), o)
    assert rows[keys[2]][1] == f"{b:.3f}" and rows[keys[2]][2] == f"{ll:.3f}"
    # reliability: each forecast contributes 3 outcomes per match, split across bins
    tbl = _table_rows(page.locator("section[aria-label='Reliability plot'] tbody tr"))
    for name in {r[0] for r in tbl}:
        assert sum(int(r[4]) for r in tbl if r[0] == name) == 3 * len(m), name
    assert page.locator("[data-testid=no-model]").count() == 1
    assert page.locator("[data-chart=reliability] path.rel-pt[data-series=market]").count() > 3


def test_market_lens_season_table_and_points(page, data):
    import pandas as pd
    go(page, "market", "2025-26")
    rows = _table_rows(page.locator("section[aria-label='Points: actual vs expected'] tbody tr"))
    assert [r[0] for r in rows] == data["seasons"]
    for r in rows:
        m, pm, sim, o, win_odds = _lens(r[0])
        assert int(r[1]) == len(m) and int(r[2]) == int(m.pts.sum())
        assert r[3] == f"{(3 * sim[:, 0] + sim[:, 1]).sum():.1f}", r[0]
        assert r[4] == f"{(3 * pm[:, 0] + pm[:, 1]).sum():.1f}", r[0]
        pnl = float(sum(w - 1 if oo == 0 else -1 for w, oo in zip(win_odds, o)))
        assert _flt(r[7]) == pytest.approx(pnl, abs=0.006) and _flt(r[8].rstrip("%")) == pytest.approx(pnl / len(m) * 100, abs=0.06), r[0]
    # seasons under the sample threshold are not charted, and the selected season is shaded
    assert page.locator("[data-chart=season-points] rect.sbar").count() == 3 * (len(data["seasons"]) - 1)
    for tag, expect in (("points", "Actual points"), ("xpts_sim", "xPts"), ("xpts_market", "Market")):
        assert page.locator(f"[data-chart=season-points] rect.sbar[data-series={tag}]").count() == len(data["seasons"]) - 1
    # manager-era filter restricts the seasons
    go(page, "market", "all", "klopp")
    rows = _table_rows(page.locator("section[aria-label='Points: actual vs expected'] tbody tr"))
    assert [r[0] for r in rows] == data["seasons"][1:10]


def test_market_lens_staking_matches_independent_pnl(page):
    go(page, "market", "2019-20")
    m, pm, sim, o, win_odds = _lens("2019-20")
    pnl = float(sum(w - 1 if oo == 0 else -1 for w, oo in zip(win_odds, o)))
    assert _flt(page.inner_text("[data-metric=pnl]")) == pytest.approx(pnl, abs=0.006)
    assert _flt(page.inner_text("[data-metric=roi]").rstrip("%")) == pytest.approx(pnl / len(m) * 100, abs=0.06)
    assert int(page.inner_text("[data-metric=stakes]")) == len(m)
    assert page.locator("[data-testid=retro-badge]").count() == 1
    assert "not a strategy" in page.inner_text("[data-testid=retro-badge]")
    assert "not betting advice" in page.inner_text("footer")
    assert page.locator("[data-chart=roi] rect.roi-bar").count() == 12  # completed seasons with the current one omitted


def _own_runs(pts, xm, window=10, thr=4.0):
    r = pts - xm
    out = []
    for sign, name in ((1, "beat"), (-1, "lagged")):
        flagged = [(i - window + 1, i) for i in range(window - 1, len(r)) if sign * r[i - window + 1:i + 1].sum() >= thr]
        merged = []
        for lo, hi in flagged:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1][1] = hi
            else:
                merged.append([lo, hi])
        out += [(lo, hi, name) for lo, hi in merged]
    return sorted(out)


def test_market_lens_mispriced_runs_match_definition(page):
    for season in ("2019-20", "2025-26", "2014-15"):
        go(page, "market", season)
        m, pm, sim, o, _ = _lens(season)
        xm = 3 * pm[:, 0] + pm[:, 1]
        exp = _own_runs(m.pts.values.astype(float), xm)
        rows = _table_rows(page.locator("table[data-table=runs] tbody tr"))
        assert len(rows) == len(exp), season
        for r, (lo, hi, name) in zip(rows, exp):
            assert int(r[3]) == hi - lo + 1 and int(r[4]) == int(m.pts.values[lo:hi + 1].sum()), (season, lo)
            assert _flt(r[6]) == pytest.approx(m.pts.values[lo:hi + 1].sum() - xm[lo:hi + 1].sum(), abs=0.06)
            assert ("Beat" if name == "beat" else "Lagged") in r[0]
        assert page.locator("[data-chart=cum-market] rect.run-band").count() == len(exp)
    go(page, "market", "2026-27")
    assert page.locator("[data-testid=small-sample]").count() == 1 and "at least 10" in page.inner_text("section[aria-label='Mispriced runs']")


def test_market_lens_strip_and_no_other_season_labels(page, data):
    go(page, "market", "2024-25")
    assert page.locator("[data-chart=strip] rect.res-strip").count() == 38
    assert page.locator("[data-chart=strip] circle.p-mkt").count() == 38 and page.locator("[data-chart=strip] path.p-sim").count() == 38
    wins = page.locator("[data-chart=strip] rect.res-strip[data-r=W]").count()
    assert wins == data["selections"]["2024-25|all"]["record"]["wins"]
    rows = _table_rows(page.locator("section[aria-label='Every match: market vs xG simulation'] tbody tr"))
    assert len(rows) == 38
    for route in ("market", "methodology", "attack", "defence", "players", "match"):
        go(page, route, "2019-20")
        assert set(SEASON_RE.findall(visible_text(page))) <= {"2019-20"}, route


def test_external_model_hook_end_to_end(browser, tmp_path):
    """A CSV of model probabilities is scored next to the market: same probabilities, same score, on the subset it covers."""
    import numpy as np
    import pandas as pd
    import build
    import metrics as MT
    t = build.load_tables()
    e0 = MT.enrich_matches(t["matches"], t["shots"])
    sub = e0[e0.season == "2024-25"].iloc[:20]
    probs = np.array([MT.devig_proportional([h, d, a]) for h, d, a in zip(sub.mkt_h, sub.mkt_d, sub.mkt_a)])  # home, draw, away
    csv = tmp_path / "model.csv"
    pd.DataFrame({"match_id": sub.match_id.values, "p_home": probs[:, 0], "p_draw": probs[:, 1], "p_away": probs[:, 2]}).to_csv(csv, index=False)
    e = MT.attach_external_model(e0, MT.load_external_model(csv))
    data = build.dashboard(e, t["fixtures"], t["shots"], t["rosters"], t["team_seasons"], t["team_matches"])
    cal = data["selections"]["2024-25|all"]["cal"]
    assert cal["model"]["n"] == 20
    o = MT.outcome_index(sub.result)
    assert cal["model"]["brier"] == pytest.approx(MT.brier(sub[["mp_w", "mp_d", "mp_l"]].values, o), abs=1e-9)  # same probabilities, same score
    assert "model" not in data["selections"]["2023-24|all"]["cal"]
    out = tmp_path / "index.html"
    out.write_text(build.render(data), encoding="utf-8")
    ctx, pg = _new_page(browser)
    pg.goto(out.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    go(pg, "market", "2024-25")
    rows = _table_rows(pg.locator("table[data-table=calibration] tbody tr"))
    assert len(rows) == 4 and rows[2][0] == "External model" and int(rows[2][3]) == 20
    assert rows[2][1] == f"{cal['model']['brier']:.3f}"
    assert pg.locator("[data-testid=no-model]").count() == 0 and "scored on 20 of 38" in pg.inner_text("[data-testid=hook-status]")
    assert pg.locator("[data-chart=reliability] path.rel-pt[data-series=model]").count() > 0
    go(pg, "market", "2023-24")
    assert pg.locator("[data-testid=no-model]").count() == 1
    assert pg.errors == []
    ctx.close()


def test_methodology_generated_from_registry_and_data(page, data):
    import pandas as pd
    import metrics as MT
    go(page, "methodology", "2025-26")
    page.click("[data-testid=expand-all]")
    reg = data["registry"]
    rows = page.locator("table.glossary tbody tr")
    assert rows.count() == len(reg)
    seen = rows.evaluate_all("trs => trs.map(t => [t.dataset.metricId, t.children[0].textContent, t.children[1].textContent])")
    assert {r[0] for r in seen} == set(reg)
    for mid, label, desc in seen:
        assert label == reg[mid]["label"] and desc == reg[mid]["description"], mid
    assert page.locator("[data-gen]").count() == 0  # every placeholder was resolved
    txt = page.text_content("[data-testid=methodology]")  # DOM text: the headings are upper-cased by CSS
    for heading in ("Data sources", "Baselines and change", "Expected goals and the exact simulation", "Turning odds into probabilities", "Known limitations", "How accuracy is enforced"):
        assert heading in txt, heading
    # coverage numbers come from the data
    m = pd.read_parquet(ROOT / "data" / "processed" / "matches.parquet")
    assert f"{len(m)} Liverpool matches across {m.season.nunique()} seasons" in txt
    src = m.mkt_source.value_counts()
    assert f"Pinnacle closing odds for {src['pinnacle_close']} matches" in txt
    # thresholds shown equal the settings used by the takeaway rules
    rules = _table_rows(page.locator("table[data-table=takeaway-rules] tbody tr"))
    T = MT.SETTINGS["takeaway_thresholds"]
    assert f"{T['finishing_goals']} goals" in rules[1][1] and f"{T['market_points']}" in rules[2][1] and f"{T['xpts_points']}" in rules[3][1]
    assert f"{T['baseline_pct'] * 100:.0f}%" in rules[4][1] and f"{T['form_strong_points']} or more" in rules[0][1]
    # registry text agrees with the configured mispriced-run parameters
    W = MT.SETTINGS["mispriced"]
    assert f"{W['window']} consecutive" in reg["mispriced_runs"]["description"] and f"{W['threshold_points']:.0f} points" in reg["mispriced_runs"]["description"]


def test_methodology_worked_examples_are_correct(page):
    import numpy as np
    import metrics as MT
    go(page, "methodology", "2025-26")
    page.click("[data-testid=expand-all]")
    rows = _table_rows(page.locator("table[data-table=devig-example] tbody tr"))
    odds = [float(r[1]) for r in rows]
    prop, shin, z = MT.devig_proportional(odds), MT.devig_shin(odds), MT.shin_z(odds)
    for r, p, s in zip(rows, prop, shin):
        assert r[3] == f"{p * 100:.2f}%" and r[4] == f"{s * 100:.2f}%" and r[2] == f"{100 / float(r[1]):.2f}%"
    assert f"z = {z:.4f}" in page.inner_text("[data-example=devig]")
    sim = page.inner_text("[data-example=sim]")
    # brute force of the worked example (Liverpool shots 0.30 and 0.50 against one shot of 0.20)
    import itertools
    w = d = 0.0
    for a1, a2, b1 in itertools.product([0, 1], repeat=3):
        pr = (0.3 if a1 else 0.7) * (0.5 if a2 else 0.5) * (0.2 if b1 else 0.8)
        gl, go_ = a1 + a2, b1
        w += pr * (gl > go_)
        d += pr * (gl == go_)
    assert f"P(win) = {w * 100:.1f}%" in sim and f"P(draw) = {d * 100:.1f}%" in sim and f"{3 * w + d:.2f}" in sim


def test_methodology_navigation_does_not_break_routing(page):
    go(page, "methodology", "2024-25")
    page.click("[data-toc='accuracy']")
    page.wait_for_function("document.getElementById('accuracy').getBoundingClientRect().top < 700", timeout=8000)  # smooth scroll finished
    assert page.evaluate("location.hash").startswith("#/methodology?season=2024-25")
    assert page.evaluate("document.getElementById('accuracy').getBoundingClientRect().top") > -5
    page.click("a[data-jump='#accuracy']")
    assert page.evaluate("location.hash").startswith("#/methodology")
    assert page.errors == []


def test_methodology_is_collapsible(page, data):
    go(page, "methodology", "2025-26")
    secs = page.locator("details.sec")
    n = secs.count()
    assert n == 12
    # only the first section starts open; every heading is visible as a summary
    assert [secs.nth(i).get_attribute("open") is not None for i in range(n)] == [True] + [False] * (n - 1)
    assert page.locator("details.sec > summary h2").all_text_contents()[0] == "What this dashboard is"
    hidden = page.locator("[data-testid=methodology]").inner_text()
    assert "Two rules run through everything" in hidden and "Politeness and reproducibility" not in hidden  # collapsed text is not rendered
    # clicking a summary toggles it
    page.locator("details.sec > summary", has_text="Baselines and change").click()
    assert "arrow follows the sign" in page.locator("[data-testid=methodology]").inner_text()
    page.locator("details.sec > summary", has_text="Baselines and change").click()
    assert "arrow follows the sign" not in page.locator("[data-testid=methodology]").inner_text()
    # expand / collapse all (sections and glossary groups)
    page.click("[data-testid=expand-all]")
    assert page.locator("details[open]").count() == page.locator("details").count() and page.locator("details.sub").count() == 7
    assert "Brier score is the mean" in page.locator("[data-testid=methodology]").inner_text()
    page.click("[data-testid=collapse-all]")
    assert page.locator("details[open]").count() == 0
    # the contents jump opens a collapsed section and scrolls to it; hash routing is untouched
    page.click("[data-toc='baselines-and-change']")
    page.wait_for_function("document.querySelector('details.sec:has(#baselines-and-change)').open")
    page.wait_for_function("document.getElementById('baselines-and-change').getBoundingClientRect().top < 500", timeout=8000)
    assert page.evaluate("location.hash").startswith("#/methodology?season=2025-26")
    # the glossary groups have counts that add up to the registry
    page.click("[data-testid=expand-all]")
    counts = page.locator("details.sub summary .cnt").all_inner_texts()
    assert sum(int(c.strip("()")) for c in counts) == len(data["registry"])
    assert page.errors == []


# ------------------------------------------------------------------ M7: polish, resilience, performance
def _css_tokens():
    import re
    css = (ROOT / "template" / "index.html").read_text(encoding="utf-8")

    def block(start):
        i = css.index(start)
        return css[i:css.index("}", i)]

    def toks(b):
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", b))
    return {"light": toks(block(":root {\n  color-scheme: light")), "dark": toks(block(':root[data-theme="dark"] {'))}


def _lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_text_colours_meet_wcag_aa_in_both_themes():
    """Every text token against every background token, both themes, 4.5:1 (so also comfortably above the
    3:1 needed for text of 24px and up). The brand red is a fill (white text on it is tested below); small
    text and series in Liverpool red use the separate `red` token."""
    for theme, t in _css_tokens().items():
        for fg in ("ink", "ink-2", "muted", "neutral", "red", "good", "bad", "teal-text"):
            for bg in ("bg", "sidebar", "surface", "surface-2"):
                assert _contrast(t[fg], t[bg]) >= 4.5, (theme, fg, bg, round(_contrast(t[fg], t[bg]), 2))
        assert _contrast(t["accent-ink"], t["accent"]) >= 4.5, theme  # text on the red buttons, nav pill and pressed toggles
        assert t["accent"].lower() == "#c8102e"
        # the brand red must never be the "bad" colour, and bad/good are never the brand red
        assert t["bad"].lower() != t["accent"].lower() and t["good"].lower() != t["accent"].lower()
        # a goal is told apart from a miss by fill (solid vs hollow), and the goal marker is legible on the stage
        assert _contrast(t["goal"], t["stage"]) >= 3.0, theme


def test_dark_theme_defines_every_token_of_the_light_theme():
    t = _css_tokens()
    assert set(t["light"]) <= set(t["dark"]) | {"good-bg", "bad-bg"}, set(t["light"]) - set(t["dark"])


def test_page_shell_shows_loading_state_before_scripts_run(browser):
    ctx = browser.new_context(java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(DIST.as_uri())
    assert "Loading the dashboard" in pg.inner_text("#view") and "needs JavaScript" in pg.text_content("noscript")
    assert pg.inner_text("h1") != "" and pg.locator("footer").count() == 1
    ctx.close()


def test_page_degrades_gracefully_when_d3_cannot_load(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("https://cdnjs.cloudflare.com/**", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    pg.goto(DIST.as_uri())
    pg.wait_for_selector("[data-testid=d3-missing]")
    assert "charts library could not be loaded" in pg.inner_text("#view") and errors == []
    ctx.close()


def test_render_failure_shows_message_and_keeps_navigation(browser, data, tmp_path):
    import build
    broken = json.loads(json.dumps(data))
    del broken["selections"]["2025-26|all"]["cal"]  # a page that needs this will throw while drawing
    out = tmp_path / "broken.html"
    out.write_text(build.render(broken), encoding="utf-8")
    ctx, pg = _new_page(browser)
    pg.goto(out.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    go(pg, "market", "2025-26")
    assert pg.locator("[data-testid=render-error]").count() == 1 and pg.locator("nav.nav a.item").count() == 7
    go(pg, "overview", "2025-26")  # the rest of the site still works
    assert pg.locator("[data-metric=wins]").count() == 1 and pg.locator("[data-testid=render-error]").count() == 0
    assert any("pageerror" in e or "console.error" in e for e in pg.errors)  # the failure is still reported, not swallowed
    ctx.close()


def test_empty_filter_results_are_explained(page):
    go(page, "attack", "2025-26")
    page.select_option("#f-player", label="Virgil van Dijk")
    page.select_option("#f-sit", "Penalty")
    assert page.inner_text("[data-stat=shots]") == "0" and page.locator("[data-chart=attack] .empty-note").count() == 1
    assert page.inner_text("[data-stat=xgps]") == "N/A"  # never a divide-by-zero figure
    page.select_option("#f-sit", "all")
    assert page.locator("[data-chart=attack] .empty-note").count() == 0
    go(page, "defence", "2025-26")
    page.select_option("#f-opp", label="Arsenal")
    page.select_option("#f-res", "ShotOnPost")
    page.select_option("#f-typ", "OtherBodyPart")
    assert page.inner_text("[data-stat=shots]") == "0" and page.locator("[data-chart=defence] .empty-note").count() == 1


def test_changing_page_scrolls_to_top_and_keeps_focus_management(page):
    go(page, "players", "2024-25")
    page.evaluate("window.scrollTo(0, 1200)")
    assert page.evaluate("scrollY") > 500
    page.click("nav.nav a.item:has-text('Attack')")
    page.wait_for_function("document.querySelector('#title').textContent.startsWith('Attack')")
    assert page.evaluate("scrollY") == 0
    # switching season on the same page must not jump to the top
    page.evaluate("window.scrollTo(0, 600)")
    page.select_option("#sel-season", "2023-24")
    page.wait_for_function("document.querySelector('#title').textContent.includes('2023-24')")
    assert page.evaluate("scrollY") > 300


def test_mobile_nav_keeps_current_page_visible(browser):
    ctx, pg = _new_page(browser, 390, 844)
    pg.goto(DIST.as_uri() + "#/methodology?season=2025-26&era=all")
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    box = pg.evaluate("""() => { const n = document.querySelector('.nav').getBoundingClientRect(), c = document.querySelector('.nav a[aria-current]').getBoundingClientRect();
        return [n.left, n.right, c.left, c.right, document.querySelector('.nav').scrollLeft]; }""")
    assert box[4] > 0 and box[2] >= box[0] - 1 and box[3] <= box[1] + 1
    ctx.close()


def test_touch_targets_are_large_enough_on_mobile(browser, data):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    _stub_network(pg)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    small = []
    for route in PAGES:
        go(pg, route, data["default_season"])
        for tag, box in pg.evaluate("""() => [...document.querySelectorAll('a.item, select, input, button:not(.info):not(.sortbtn):not(.pl), summary, .seg button')]
            .filter(e => e.offsetParent !== null && !e.closest('details:not([open]) > :not(summary)'))
            .map(e => { const r = e.getBoundingClientRect(); return [e.tagName + '.' + e.className + ' ' + (e.textContent || '').trim().slice(0, 20), [r.width, r.height]]; })"""):
            if box[1] < 32:
                small.append((route, tag, [round(x) for x in box]))
        # the tiny ⓘ glyph has a larger invisible hit area: points 13px either side still hit the button
        ok = pg.evaluate("""() => { const b = document.querySelector('button.info'); if (!b) return true; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            return [[-13, 0], [13, 0], [0, -13], [0, 13]].every(([dx, dy]) => document.elementFromPoint(cx + dx, cy + dy) === b); }""")
        assert ok, route
    assert small == [], small
    ctx.close()


@pytest.mark.parametrize("is_mobile", [True, False], ids=["mobile-no-hover", "touch-with-hover-media"])
def test_tooltips_work_on_touch(browser, is_mobile):
    """Tap opens, a later tap closes, tapping elsewhere dismisses. Run with and without mobile emulation:
    without it the browser still matches (hover: hover) and fires mouseenter/focus during the tap, which
    must not make the click that follows toggle the tooltip straight back off (this failed on CI)."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=is_mobile)
    pg = ctx.new_page()
    _stub_network(pg)
    pg.goto(DIST.as_uri() + "#/attack?season=2024-25&era=all")
    pg.wait_for_function("window.__tracker && window.__tracker.ready")

    def tip_on():
        return "on" in (pg.get_attribute("#tip", "class") or "").split()

    pg.locator("[data-chart=attack] circle.shot").first.scroll_into_view_if_needed()
    pg.tap("[data-chart=attack] circle.shot >> nth=5", force=True)
    pg.wait_for_timeout(150)
    assert tip_on() and "xG" in pg.inner_text("#tip")  # stays after the finger lifts
    pg.tap("h1")
    pg.wait_for_timeout(100)
    assert not tip_on()
    pg.tap("button.info >> nth=0")
    pg.wait_for_timeout(150)
    assert tip_on() and "Source:" in pg.inner_text("#tip")
    pg.wait_for_timeout(500)
    pg.tap("button.info >> nth=0")  # a deliberate second tap closes it
    pg.wait_for_timeout(100)
    assert not tip_on()
    ctx.close()


@pytest.mark.parametrize("width,height", [(768, 1024), (1024, 768), (1280, 720)])
def test_no_horizontal_overflow_at_tablet_and_laptop_widths(browser, data, width, height):
    ctx, pg = _new_page(browser, width, height)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    for route in PAGES:
        for season in (data["default_season"], "all"):
            go(pg, route, season)
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (route, season, width)
    assert pg.errors == []
    ctx.close()


def test_performance_budgets(browser, data):
    """Local budgets (generous for CI): boot, and the slowest realistic renders. Numbers are recorded in PROGRESS.md."""
    ctx, pg = _new_page(browser)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    assert pg.evaluate("window.__tracker.boot") < 1500
    worst = {}
    for route in PAGES:
        for season in ("all", data["default_season"]):
            go(pg, route, season)
            worst[(route, season)] = pg.evaluate("window.__tracker.lastRender")
    assert max(worst.values()) < 2500, worst
    assert pg.evaluate("performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd") < 3000
    print("render ms:", {f"{k[0]}/{k[1]}": v for k, v in worst.items()})
    ctx.close()


def test_info_tooltip_survives_event_orderings_seen_on_other_browsers(page):
    """Browsers differ in which events fire around a tap (mouseenter, focus, pointerleave). The tooltip must
    end up open after a tap however they interleave, and a mouse leaving must still close it."""
    go(page, "attack", "2024-25")
    on = lambda: "on" in (page.get_attribute("#tip", "class") or "").split()
    page.evaluate("""() => { const b = document.querySelector('button.info');
        b.dispatchEvent(new MouseEvent('mouseenter'));            // opened by hover during the tap...
        b.click(); }""")  # ...then the click arrives in the same gesture
    assert on(), "a click straight after a hover-open must not close the tooltip"
    page.evaluate("document.querySelector('button.info').dispatchEvent(new PointerEvent('pointerleave', {pointerType: 'touch'}))")
    assert on(), "a lifted finger is not a dismissal"
    page.evaluate("document.querySelector('button.info').dispatchEvent(new PointerEvent('pointerleave', {pointerType: 'mouse'}))")
    assert not on(), "a mouse leaving closes it"
    page.wait_for_timeout(400)
    page.evaluate("document.querySelector('button.info').click()")   # a deliberate click opens ...
    assert on()
    page.wait_for_timeout(400)
    page.evaluate("document.querySelector('button.info').click()")   # ... and the next one closes it
    assert not on()
    # another ⓘ's tooltip is replaced, not toggled off
    page.evaluate("document.querySelectorAll('button.info')[0].click()")
    page.wait_for_timeout(400)
    page.evaluate("document.querySelectorAll('button.info')[1].click()")
    assert on() and page.inner_text("#tip") != ""


# ------------------------------------------------------------------ Redesign: layout rules
DEADSPACE_JS = """() => [...document.querySelectorAll('.card')].filter(c => c.offsetParent !== null).map(c => {
    const kids = [...c.children].filter(k => getComputedStyle(k).display !== 'none' && getComputedStyle(k).position !== 'absolute' && k.getBoundingClientRect().height > 0);
    const last = kids[kids.length - 1], cb = c.getBoundingClientRect().bottom;
    const label = c.getAttribute('aria-label') || (c.querySelector('h2') || {}).textContent || c.className;
    return [label, last ? cb - last.getBoundingClientRect().bottom : 0];
}).filter(([, gap]) => gap !== null)"""
THEMES = ("light", "dark")


@pytest.mark.parametrize("width,height,limit", [(1920, 1080, 48), (1440, 900, 48), (390, 844, 32)])
def test_no_card_has_dead_space_at_its_bottom(browser, data, width, height, limit):
    """Empty space at the bottom of a card (card bottom minus the bottom of its last child, padding included)
    stays under the limit on every page, season and theme."""
    ctx, pg = _new_page(browser, width, height)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    bad = []
    for theme in THEMES:
        pg.evaluate("t => { document.documentElement.dataset.theme = t }", theme)
        for route in PAGES:
            for season in ["all"] + data["seasons"]:
                go(pg, route, season)
                if route == "methodology" and season != "all":
                    continue  # the page does not depend on the season
                for label, gap in pg.evaluate(DEADSPACE_JS):
                    if gap >= limit:
                        bad.append((width, theme, route, season, label.strip()[:40], round(gap)))
    assert bad == [], bad[:20]
    assert pg.errors == []
    ctx.close()


@pytest.mark.parametrize("width", [1920, 1440, 1024, 800, 390])
def test_takeaway_grid_has_no_orphan_gaps(browser, width):
    """0-8 tiles: rows are full, and an incomplete last row's tile spans the remaining columns."""
    ctx, pg = _new_page(browser, width, 900)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    base = {"tag": "GOALS VS XG", "headline": "-6.8", "direction": "down", "good": False, "sentiment": "negative", "text": "Finishing 6.8 goals below xG (61 goals from 67.8 xG)",
            "bars": [{"label": "Goals", "value": 61}, {"label": "xG", "value": 67.8}]}
    cols = 4 if width >= 1400 else 3 if width >= 900 else 2 if width >= 600 else 1
    for n in (0, 1, 4, 5, 6, 8):
        pg.evaluate("(list) => window.__tracker.mountTakeaways(list)", [dict(base, id=f"t{i}") for i in range(n)])
        pg.wait_for_timeout(30)
        if n == 0:
            assert pg.locator("[data-testid=take-grid]").count() == 0 and pg.locator(".card").count() == 0  # nothing fired: no card
            continue
        info = pg.evaluate("""() => { const g = document.querySelector('[data-testid=take-grid]'), gr = g.getBoundingClientRect();
            return {grid: [gr.left, gr.right], cols: getComputedStyle(g).gridTemplateColumns.split(' ').length,
                    tiles: [...g.children].map(t => { const r = t.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom]; })}; }""")
        assert info["cols"] == cols, (width, n, info["cols"])
        rows = {}
        for left, right, top, bottom in info["tiles"]:
            rows.setdefault(round(top), []).append((left, right))
        assert len(rows) == -(-n // cols), (width, n)  # ceil(n / cols) rows: no empty rows or stray wraps
        for top, tiles in rows.items():
            tiles.sort()
            assert abs(tiles[0][0] - info["grid"][0]) < 1.5 and abs(tiles[-1][1] - info["grid"][1]) < 1.5, (width, n, top, "row does not span the grid")
            for a, b in zip(tiles, tiles[1:]):
                assert 0 < b[0] - a[1] < 40, (width, n, "gap between tiles")  # only the 20px gutter, no hole
    assert pg.errors == []
    ctx.close()
