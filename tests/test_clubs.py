"""Multi-club configuration: clubs, eras and themes. No data needed: these guard the config the whole site is built from."""
import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build  # noqa: E402
import metrics as M  # noqa: E402

CLUBS = list(M.CLUBS)
BIG_FIVE = {"liverpool", "arsenal", "chelsea", "manchester-united", "manchester-city"}


def test_the_five_clubs_are_configured_and_the_default_sits_at_the_site_root():
    assert set(CLUBS) == BIG_FIVE and M.DEFAULT_CLUB == "liverpool"
    paths = [c["path"] for c in M.CLUBS.values()]
    assert len(set(paths)) == 5 and M.CLUBS["liverpool"]["path"] == ""
    assert all(c["path"] == f"{c['slug']}/" for c in M.CLUBS.values() if c["slug"] != "liverpool")
    assert len({c["canonical"] for c in M.CLUBS.values()}) == 5


@pytest.mark.parametrize("slug", CLUBS)
def test_eras_are_well_formed(slug):
    eras = M.load_eras(slug)
    assert eras[0]["from"] <= "2014-08-01", "the first era must cover the first season"
    assert eras[-1]["to"] is None, "the last era is the current manager (open-ended)"
    for a, b in zip(eras, eras[1:]):
        assert a["to"] is not None and a["from"] <= a["to"] < b["from"], (a["manager"], b["manager"])   # ordered, inclusive, no overlap
    names = [e["manager"] for e in eras]
    assert len(names) == len(set(names)), "a manager (or stint) appears twice: give the stint its own name and slug"
    slugs = [e.get("slug") or "".join(c for c in unicodedata.normalize("NFD", e["manager"].split()[-1]) if not unicodedata.combining(c)).lower() for e in eras]
    assert len(slugs) == len(set(slugs)), f"era URL slugs collide: {slugs}"


def test_every_date_before_the_first_era_ends_has_an_era():
    """Matches falling between two eras would be labelled 'Unknown': the gaps must hold no Premier League weekend."""
    import pandas as pd
    for slug in CLUBS:
        eras = M.load_eras(slug)
        for a, b in zip(eras, eras[1:]):
            gap = (pd.Timestamp(b["from"]) - pd.Timestamp(a["to"])).days - 1
            assert gap <= 12, (slug, a["manager"], b["manager"], gap)


@pytest.mark.parametrize("slug", [c for c in CLUBS if c != "liverpool"])
def test_theme_text_and_brand_colours_are_readable_in_both_themes(slug):
    cc = M.CLUBS[slug]
    tk = build.theme_tokens(cc)
    assert build.contrast(cc["theme"]["brand"], "#ffffff") >= 4.5, "white text on the sidebar / masthead"
    assert build.contrast(cc["record_ink"], "#f6efe7") >= 4.5, "record card heading and points on the cream card"
    for mode in ("light", "dark"):
        t = tk[mode]
        surface = "#ffffff" if mode == "light" else t["surface"]
        for bg in (surface, t["bg"], t["surface-2"]):
            for name in ("ink", "ink-2", "muted", "neutral", "red"):
                assert build.contrast(t[name], bg) >= 4.5, (slug, mode, name, bg)


def test_dark_theme_defines_every_token_the_light_theme_overrides():
    for slug in CLUBS:
        tk = build.theme_tokens(M.CLUBS[slug])
        if tk:
            assert set(tk["light"]) - {"pitch-line", "shot-fill", "shot-stroke"} <= set(tk["dark"]) | {"surface"}, slug


def test_theme_css_has_the_three_blocks_and_none_for_the_default_club():
    assert build.theme_css(M.CLUBS["liverpool"]) == ""
    css = build.theme_css(M.CLUBS["chelsea"])
    assert css.count("--accent: #034694") == 3 and "prefers-color-scheme: dark" in css and '[data-theme="dark"]' in css
    assert '--lettering: "STAMFORD BRIDGE"' in css and "--seats: url(" in css


def test_clubs_look_different():
    brands = {c["slug"]: (c.get("theme") or {}).get("brand", "#c8102e") for c in M.CLUBS.values()}
    assert brands["chelsea"] != brands["arsenal"] != brands["manchester-city"] and brands["manchester-united"] != brands["arsenal"]
    assert len(set(brands.values())) == 5


def test_monogram_badges_are_valid_svg_with_the_clubs_initials():
    import xml.dom.minidom as dom
    for slug, cc in M.CLUBS.items():
        svg = build.monogram_svg(cc)
        dom.parseString(svg)
        assert f">{cc['initials']}</text>" in svg


def test_switcher_links_are_relative_and_reach_every_club():
    root = build.club_switcher(M.CLUBS["liverpool"])
    assert [c["href"] for c in root] == ["index.html", "arsenal/index.html", "chelsea/index.html", "manchester-united/index.html", "manchester-city/index.html"]
    sub = build.club_switcher(M.CLUBS["chelsea"])
    assert [c["href"] for c in sub] == ["../index.html", "../arsenal/index.html", "../chelsea/index.html", "../manchester-united/index.html", "../manchester-city/index.html"]
    assert [c["slug"] for c in sub if c["current"]] == ["chelsea"] and all(c["badge"].startswith("data:image/") for c in sub)


def test_text_localisation_swaps_the_club_name_everywhere_it_is_generated():
    cc = M.CLUBS["manchester-united"]
    reg = build.registry_for(cc)
    assert "Liverpool" not in json.dumps(reg) and "Manchester United" in reg["shot_map"]["description"]
    assert build.localise("Liverpool FC and Liverpool's xG", cc) == "Manchester United FC and Manchester United's xG"


def test_every_tracked_club_is_named_the_way_understat_names_it():
    """The canonical names must exist in the team-name mapping universe (Understat titles); the football-data spellings map onto them."""
    mapping = json.loads((ROOT / "config" / "teams.json").read_text(encoding="utf-8"))["football_data_to_canonical"]
    assert mapping["Man City"] == "Manchester City" and mapping["Man United"] == "Manchester United"
    for c in M.CLUBS.values():
        assert re.fullmatch(r"[A-Za-z ]+", c["canonical"])
