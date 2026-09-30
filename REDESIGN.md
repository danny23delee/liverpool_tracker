# Redesign pass: Liverpool theme, layout fixes, shot-map drawer

Read this whole file, then do the work on a branch: `git checkout -b redesign`. Do not merge to main until I approve.

**Scope: presentation only.** Do not change `metrics.json` values or definitions, `etl.py`, existing takeaway thresholds or wording, or the assertions in existing tests. If a test breaks because a selector or DOM structure changed, update the selector, never weaken the assertion. One additive exception is allowed: `metrics.py` may add structured fields to takeaway objects (see §2). Every existing test must pass unmodified apart from selectors.

**References (in `design/`):** `Overview.png`, `AttackCollapsed.png`, `AttackDrawerOpen.png` are the target layouts, exported from the mockups at 1920px wide. Follow them closely. The mockups use generated placeholder shot dots and approximate chart curves; the real charts keep using real data. Where this file and a mockup disagree, this file wins. If `design/` is missing, this file is self-contained.

**Before changing anything:** screenshot every page for two seasons (one whose Overview yields 4 takeaways, one that yields 6) at 1920×1080, 1440×900 and 390×844 into `artifacts/screenshots/before/`. Measure the current shot-map scale (see §3) and record it in `PROGRESS.md`.

---

## 1. Theme (dark + light, via existing CSS custom properties)

Retheme by changing token values; do not hard-code colours in components. Keep the light/dark toggle.

**Dark (default look, matches mockups)**

| Token | Value |
|---|---|
| page background | `#100507` |
| sidebar | `#160709` |
| card | `#1c0b0e` |
| tile (card inside card) | `#25100f` |
| border/line | `#3a1a1f` (subtle gridlines `#2e1519`, zero line `#6a3a40`) |
| text | `#f4eaec` |
| muted text | `#b8a5a9` |
| faint text | `#8f7c80` |
| brand red (fills, nav active, buttons; white text on it) | `#c8102e` |
| red for small text and Liverpool chart series | `#ff5468` |
| goal dot fill | `#ff3b52` with `#ffffff` 1.3px stroke |
| gold (highlights, set pieces) | `#f6eb61` |
| teal (secondary series, penalties, "above xG") | `#00b2a9` |
| good | `#3ecf8e` |
| bad | `#ff7a33` |

**Light:** derive a matching set. White cards on a warm off-white page (`#faf5f5`), border `#ecd9dc`, text `#1a0c0e`, muted `#6b5257`. Use darker variants for small text and thin lines (red text `#a30d25`, teal `#007f79`, good `#1f8a5b`, bad `#c2410c`), and keep gold for fills only, never text. Mockups exist for dark only.

**Rules**
- Brand red must never mean "worse". Good/bad colours come from each metric's `higher_is_better` in `metrics.json`, and always come with an arrow or +/− sign, never colour alone.
- Verify contrast: body and small text ≥ 4.5:1, text ≥ 24px ≥ 3:1, in both themes. Add a test that computes the contrast of every text/background token pair and fails under threshold.
- Goals vs no-goal shots must differ by fill (solid vs hollow), not hue alone.
- W/D/L result badges keep their letter and use green / grey / orange outlines.

**Typography (Google Fonts, with fallback stacks):** Barlow Condensed 600/700 for h1, card labels, big numbers and the wordmark. Barlow 400/500/600 for everything else.
- h1: 60px, uppercase, line-height 1.
- Card label: 18px, uppercase, letter-spacing .12em.
- Stat numbers: 54–64px condensed.
- Body: 16px. Notes and axis text: 13–14px.
- Small caps labels (select labels): 11–12px, letter-spacing .14em.

**Spacing:** page padding 32px 36px; gap between cards 20px; card radius 16, padding 26px 28px; tile radius 14; sidebar 232px; control height 44px minimum.

**Sidebar:** crest slot (see §5) beside "PERFORMANCE / TRACKER" (23px condensed, uppercase) with "LIVERPOOL · PREMIER LEAGUE" 10.5px beneath. Active nav item: red pill `#c8102e`, white text. Remove the old red accent bar.

---

## 2. Overview

Fix the dead space. Diagnose the cause first (likely grid rows stretching a card to match a taller neighbour). Layout, top to bottom:

1. **Record card | Expected card** (flex 1.35 : 1, stretch). Record: 8 stats in a 4×2 grid, plus a proportional W/D/L bar (real values: wins green, draws grey, losses orange) with labels beneath ("17 wins · 9 draws · 12 defeats · 38 played"). Expected: 4 stats in a 2×2 grid, dashed divider, then the three "minus" deltas (points minus xPts, points minus market expectation, goals minus xG). Use `justify-content: space-between` so both cards fill their height.
2. **Rolling 10-match xG difference | Last 5 results** (flex 1.6 : 1). Content unchanged. Restyle for the theme (red line and 16% red area fill, dashed zero line, latest-value marker and label).
3. **Takeaways: full-width card containing a tile grid.** Grid columns: 4 at ≥1400px, 3 at 900–1399, 2 at 600–899, 1 below. When the last row is incomplete, the last tile spans the remaining columns. Must look right with 0–8 takeaways. With 0, hide the card.
   - Each tile: small uppercase tag (metric name), an arrow in the good/bad colour, a big condensed number (64px, good/bad colour), the takeaway sentence at 16px, and two horizontal comparison bars (selection in `#ff5468`, comparison in `#7a5a60`, values at the right), e.g. actual points vs market-expected, or this season's xGA vs baseline.
   - **Additive `metrics.py` change:** each takeaway object gains `tag`, `headline` (the big number, formatted), `direction`, `good` (bool from `higher_is_better`) and `bars` (`[{label, value}, {label, value}]`). The sentence text stays byte-identical. Every number comes from the same computation the sentence uses. Add a test that the headline and bar values match the sentence's numbers.
4. **Vs baseline: full-width card, table split into two side-by-side halves** (first ceil(n/2) rows left, the rest right, each with Per match / Selection / Baseline / Change columns). Below 1100px it collapses to one column. Keep the baseline explanation line above it.
5. Footer unchanged.

## 3. Attack and Defence: shot map

**Scale must not change.** Measure the current pixels-per-metre (e.g. rendered width of the penalty area ÷ 40.32 m) at a 1920px viewport and record it. After the redesign it must match within 1%.

**Change what the map shows:** the whole pitch (105 × 68 m) instead of the current crop. At that scale the pitch is about 1,470 × 952 px, plus goals. Keep attack left to right on the Attack page. On Defence, keep the existing orientation logic, and the opponents' shots still transform to the same view as now. Use a `viewBox` with `width: 100%` so the map scales down proportionally below 1920px, with a constant 105:68 aspect and no distortion.

**Card:** full width, and it wraps tightly around the map. Structure, in order: card header ("SHOT MAP" + info + badge), the stage (dark `#130608` background, 1px border, radius 14, height = map height, pitch centred), then the caption and "View the 200 largest shots as a table".
- Remove the old right-hand "For the shots shown" column and the filter row above the map.
- Pitch lines `rgba(255,255,255,.22)` 2px. Non-goal shots: fill `rgba(255,255,255,.07)`, stroke `rgba(255,255,255,.62)`. Goal shots draw on top.
- **Legend:** inside the stage, bottom-left corner, always visible (Goal / No goal / size scale labelled "xG (expected)").

**Drawer (left side of the stage, default collapsed):**
- Collapsed: a 46px-wide, 190px-tall red tab at the stage's left edge, radius `0 12px 12px 0`, with a chevron and vertical text "FILTERS · {n} SHOTS" that updates with the filtering.
- Open: a 420px panel overlaying the stage's left edge, background `rgba(22,8,10,.95)`, 1px border, radius `0 16px 16px 0`, sized to its content (no empty space, internal scroll if taller than the stage). The tab moves to the panel's right edge, shrinks to 64px tall and reads "Hide".
- Panel contents, top to bottom: title "FOR THE SHOTS SHOWN"; 3-column stats grid (Shots shown, Goals (actual), xG (expected), xG per shot, Goals minus xG); divider; filters in a 2×2 grid (Attack: Player, Situation, Body part, Outcome. Defence: Opponent, Situation, Body part, Outcome); divider; **Attack:** "THREAT SOURCE MIX" bar (open play `#ff5468`, set pieces `#f6eb61`, penalties `#00b2a9`) with a text legend and percentages. **Defence:** the same treatment for open-play vs set-piece xGA from data already available; if a split is not currently computed, compute it in `metrics.py` as an additive metric with a registry entry, definition and test.
- Behaviour: `aria-expanded` on the tab, real `<button>`, keyboard operable, `Esc` closes and returns focus to the tab, focus moves into the panel on open. Filters and stats apply live.
- **Threat mix must not also appear elsewhere on the Attack page.**
- Below 768px the drawer becomes a bottom sheet (same contents, slides up, tab becomes a full-width handle above the map).

**Lower rows:**
- **Attack:** Goals minus xG and Shot volume vs shot quality side by side at equal height (chart heights set so neither leaves an empty band), then the xG per match trend as a full-width card (not mocked; same styling, keep the existing content). Goals minus xG: teal for above xG, orange for below, +/− signs on every value label.
- **Defence:** reflow the remaining cards so no empty band appears.

## 4. Other pages

Players, Match Explorer, Market Lens, Methodology: theme only (tokens, fonts, spacing). Then check each for dead space under the same rule below, and fix only what the rule flags.

## 5. Crest

I have the crest as `assets/crest.png`. No conversion to SVG is needed. Do this:
- Check the PNG has a transparent background; if it has an opaque white or black background, tell me rather than trying to cut it out.
- Resize with Pillow (in the build step, not by editing my file) to max 192px tall, keep aspect ratio, optimise, and inline it into the single HTML as a base64 data URI so the deploy stays a single file.
- Display at about 46px wide × 54px tall in the sidebar, `alt="Liverpool FC crest"`, no other placement. Never in the favicon, page background or OG image, and no other club imagery.
- If `assets/crest.png` is missing, fall back to a 6px × 46px red bar (`#c8102e`), with no errors.
- This overrides the original brief's "no crests" rule for this one image only. Player photos and any other licensed imagery stay banned. The footer's "not affiliated with or endorsed by Liverpool FC or the Premier League" line must stay.

---

## 6. Tests (all must pass; existing ones untouched except selectors)

Playwright, at 1920×1080 and 1440×900, every page × every season × both themes unless noted:
- **Dead space:** for every card on every page, empty vertical space at the bottom of the card (card bottom minus the bottom of its last child) < 48px. At 390×844 apply < 32px.
- **Takeaways:** for 0, 1, 4, 5, 6 and 8 tiles (inject test data), the grid has no orphan gaps; an incomplete last row's tile spans the remainder.
- **Map scale:** pixels-per-metre within 1% of the pre-redesign measurement at 1920px. The pitch aspect is 105:68 ±0.5% at every viewport width.
- **Map data:** "Shots shown" equals the number of plotted shot dots after filtering, for at least three filter combinations, and the tab label count matches.
- **Drawer:** starts collapsed; opens and closes on click and `Esc`; `aria-expanded` toggles; focus moves in and returns; the open panel's height equals its content height (no empty band); at 390px it is a bottom sheet.
- **No duplicates:** threat source mix appears exactly once on the Attack page (in the drawer).
- **Contrast:** every text/background token pair passes the thresholds in §1 for both themes.
- **Crest:** renders at the sidebar size when the file exists; the fallback bar shows and the page has zero console errors when it doesn't.
- Zero console errors everywhere.

## 7. Process

Commit after each step, with the tests green:
1. Tokens, fonts and theme (both themes).
2. Overview layout and the takeaway structure (`metrics.py` additive change with its test).
3. Attack and Defence map card, drawer and lower rows.
4. Crest and sidebar.
5. Responsive pass (1440, 768 and 390) and final QA.

When done: after-screenshots into `artifacts/screenshots/after/`. **Look at them yourself** and fix overlap, clipping, illegible text and remaining gaps before reporting. Give me a before/after of Overview, Attack and Defence at 1920 and 390, a list of anything that deviates from the mockups and why, and any card still over the empty-space limit. Do not merge to main until I approve.
