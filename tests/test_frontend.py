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
