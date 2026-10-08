"""Club switcher and per-club themes in a real browser, across every club's built page.

Run `python build.py` first (it writes dist/index.html and dist/<club>/index.html).
"""
import os
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import metrics as M  # noqa: E402

DIST_DIR = Path(os.environ.get("TRACKER_DIST", ROOT / "dist"))
D3_LOCAL = ROOT / "data" / "raw" / "vendor" / "d3.min.js"
PAGES = ["overview", "attack", "defence", "players", "match", "market", "methodology"]
BUILT = [s for s, c in M.CLUBS.items() if (DIST_DIR / c["path"] / "index.html").exists()]


def _url(slug, hash_=""):
    return (DIST_DIR / M.CLUBS[slug]["path"] / "index.html").as_uri() + hash_


@pytest.fixture(scope="module")
def browser():
    if not BUILT:
        pytest.fail("run `python build.py` first")
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _page(browser, width=1440, height=900, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("console", lambda m: pg.errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if D3_LOCAL.exists():
        pg.route("https://cdnjs.cloudflare.com/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=D3_LOCAL.read_bytes()))
    pg.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    pg.route("https://fonts.gstatic.com/**", lambda r: r.fulfill(status=200, body=b""))
    return ctx, pg


def _open(pg, slug, hash_=""):
    pg.goto(_url(slug, hash_))
    pg.wait_for_function("window.__tracker && window.__tracker.ready")


def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


@pytest.mark.parametrize("slug", BUILT)
def test_each_club_page_is_branded_and_themed(browser, slug):
    cc = M.CLUBS[slug]
    ctx, pg = _page(browser)
    _open(pg, slug)
    brand = (cc.get("theme") or {}).get("brand", "#c8102e")
    assert pg.evaluate("getComputedStyle(document.querySelector('.nav')).backgroundColor") == _rgb(brand)
    assert pg.evaluate("getComputedStyle(document.querySelector('.topbar')).backgroundColor") == _rgb(brand)
    assert pg.evaluate("getComputedStyle(document.querySelector('.nav'), '::before').content").strip('"') == cc["lettering"]
    assert cc["name"] in pg.title() and cc["name"].lower() in pg.inner_text(".brand-text").lower()
    assert pg.get_attribute("#club-btn", "aria-label") == f"Switch club (now {cc['name']})"
    # nothing about another tracked club is baked into the chrome
    chrome = pg.inner_text(".nav") + " " + pg.inner_text("footer") + " " + pg.title()
    for other in M.CLUBS.values():
        if other["slug"] != slug:
            assert other["name"] not in chrome, (slug, other["name"])
    assert pg.errors == []
    ctx.close()


def test_club_colours_differ_between_pages(browser):
    seen = {}
    for slug in BUILT:
        ctx, pg = _page(browser)
        _open(pg, slug)
        seen[slug] = pg.evaluate("getComputedStyle(document.querySelector('.nav')).backgroundColor")
        ctx.close()
    assert len(set(seen.values())) == len(BUILT)


@pytest.mark.parametrize("slug", BUILT)
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_page_renders_cleanly_in_the_clubs_theme(browser, slug, scheme):
    ctx, pg = _page(browser, scheme=scheme)
    _open(pg, slug)
    seasons = pg.evaluate("[...document.querySelectorAll('#sel-season option')].map(o => o.value)")
    for page in PAGES:
        for season in (seasons[0], seasons[1]):
            pg.evaluate("h => location.hash = h", f"#/{page}?season={season}&era=all")
            pg.wait_for_timeout(120)
            assert pg.text_content("#title")
            txt = pg.inner_text("#main")
            assert not re.search(r"NaN|undefined|Infinity|\[object", txt), (slug, page, season)
    assert pg.errors == []
    ctx.close()


def test_switcher_lists_every_club_and_carries_page_and_season(browser):
    ctx, pg = _page(browser)
    _open(pg, "liverpool", "#/defence?season=2024-25&era=all")
    assert pg.get_attribute("#club-btn", "aria-expanded") == "false" and pg.is_hidden("#club-menu")
    pg.click("#club-btn")
    assert pg.get_attribute("#club-btn", "aria-expanded") == "true" and pg.is_visible("#club-menu")
    assert pg.locator(".club-item").count() == len(M.CLUBS)
    assert pg.locator(".club-item[aria-current=true]").get_attribute("data-club") == "liverpool"
    for slug, cc in M.CLUBS.items():
        if slug in BUILT:
            assert cc["name"] in pg.inner_text(f".club-item[data-club={slug}]")
    if "arsenal" in BUILT:
        pg.click(".club-item[data-club=arsenal]")
        pg.wait_for_function("window.__tracker && window.__tracker.ready && document.title.includes('Arsenal')")
        assert pg.url.endswith("arsenal/index.html#/defence?season=2024-25&era=all")
        assert "Arsenal" in pg.text_content(".brand-text") and pg.text_content("#title") == "Defence: 2024-25"
        # and back again through the same menu
        pg.click("#club-btn")
        pg.click(".club-item[data-club=liverpool]")
        pg.wait_for_function("document.title.includes('Liverpool')")
        assert pg.url.endswith("dist/index.html#/defence?season=2024-25&era=all") or pg.url.split("#")[0].endswith("index.html")
    assert pg.errors == []
    ctx.close()


@pytest.mark.parametrize("slug", BUILT)
def test_every_club_can_be_reached_from_every_club(browser, slug):
    ctx, pg = _page(browser)
    for target in BUILT:
        pg.goto("about:blank")   # a fresh load each time (a hash-only change would keep the previous menu state)
        _open(pg, slug, "#/overview?season=2024-25&era=all")
        pg.click("#club-btn")
        pg.click(f".club-item[data-club={target}]")
        pg.wait_for_function(f"document.title.includes({M.CLUBS[target]['name']!r})")
        pg.wait_for_function("window.__tracker && window.__tracker.ready")
        assert M.CLUBS[target]["name"] in pg.text_content(".brand-text")
    ctx.close()


def test_switcher_is_keyboard_operable_and_closes_on_escape_and_outside_click(browser):
    ctx, pg = _page(browser)
    _open(pg, "liverpool")
    pg.focus("#club-btn")
    pg.keyboard.press("Enter")
    assert pg.is_visible("#club-menu") and pg.evaluate("document.activeElement.dataset.club") == "liverpool"   # focus lands on the current club
    pg.keyboard.press("ArrowDown")
    assert pg.evaluate("document.activeElement.dataset.club") == list(M.CLUBS)[1]
    pg.keyboard.press("End")
    assert pg.evaluate("document.activeElement.dataset.club") == list(M.CLUBS)[-1]
    pg.keyboard.press("Escape")
    assert pg.is_hidden("#club-menu") and pg.evaluate("document.activeElement.id") == "club-btn"
    pg.click("#club-btn")
    pg.mouse.click(900, 600)
    assert pg.is_hidden("#club-menu")
    assert pg.get_attribute("#club-btn", "aria-haspopup") == "true" and pg.get_attribute("#club-menu", "role") == "menu"
    ctx.close()


@pytest.mark.parametrize("slug", BUILT)
def test_menu_fits_the_phone_screen_and_targets_are_large_enough(browser, slug):
    ctx, pg = _page(browser, 390, 844, "dark")
    _open(pg, slug)
    pg.click("#club-btn")
    box = pg.evaluate("(() => { const r = document.querySelector('#club-menu').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom]; })()")
    assert box[0] >= 0 and box[1] <= 390 and box[2] >= 0 and box[3] <= 844
    for h in pg.evaluate("[...document.querySelectorAll('.club-item')].map(a => a.getBoundingClientRect().height)"):
        assert h >= 44
    b = pg.evaluate("(() => { const r = document.querySelector('#club-btn').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert b[0] >= 44 and b[1] >= 44
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()


@pytest.mark.parametrize("slug", BUILT)
def test_screenshots_of_each_club(browser, slug):
    out = ROOT / "artifacts" / "screenshots" / "clubs"
    out.mkdir(parents=True, exist_ok=True)
    for scheme in ("light", "dark"):
        for w, h in ((1440, 900), (390, 844)):
            ctx, pg = _page(browser, w, h, scheme)
            _open(pg, slug, "#/overview?season=2024-25&era=all")
            pg.screenshot(path=str(out / f"{slug}-{scheme}-{w}.png"))
            ctx.close()
