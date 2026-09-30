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


def _new_page(browser, width=1440, height=900, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}"))
    if D3_LOCAL.exists():
        page.route("https://cdnjs.cloudflare.com/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=D3_LOCAL.read_bytes()))
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.route("https://fonts.gstatic.com/**", lambda r: r.fulfill(status=200, body=b""))
    page.errors = errors
    return ctx, page


@pytest.fixture()
def page(browser):
    ctx, pg = _new_page(browser)
    pg.goto(DIST.as_uri())
    pg.wait_for_function("window.__tracker && window.__tracker.ready")
    yield pg
    ctx.close()


def go(page, route, season, era="all"):
    h = f"#/{route}?season={season}&era={era}"
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
            assert page.inner_text("#title"), (route, season)
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
    assert "2022-23" in pg.inner_text("#title") and "Jürgen Klopp era" in pg.inner_text("#subtitle")
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
    assert page.inner_text("#title") == "Overview: 2025-26"
    assert "2025-26 season" in page.inner_text("#subtitle") and "38 matches" in page.inner_text("#subtitle")
    go(page, "overview", "2026-27")
    assert "5 of 38 matches played" in page.inner_text("#subtitle")
    go(page, "attack", "all")
    assert page.inner_text("#title") == "Attack: all seasons"


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


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
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
    pg.evaluate("document.documentElement.dataset.theme = 'dark'")
    go(pg, "overview", data["default_season"])
    pg.screenshot(path=str(SHOTS / f"overview_dark_{width}.png"), full_page=True)
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
