"""Geometry and tunables come from the database, never from hardcoded constants.

The numbers asserted here were MEASURED on raw 1080x1920 ADB frames. If one fails,
the database changed - re-measure, do not edit the expectation to match.

Re-measured 2026-09-06 for Savannah Cup, which is a different event on the same
engine. The draft grid went from Solstice Clash's 5x4 to 5x3, and the locked-pick row
moved up the screen. These expectations were updated FROM new captures, which is the
case the rule above allows; they were not relaxed to make a failure go away.
"""

import sqlite3
from itertools import pairwise

from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig


def test_loads_all_three_cell_types(db_path):
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    assert len(cfg.cells("locked_pick")) == 6
    assert len(cfg.cells("draft_locked_pick")) == 6
    assert len(cfg.cells("draft_card")) == 20


def test_locked_pick_cells_have_expected_geometry(db_path):
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    cells = sorted(cfg.cells("locked_pick"), key=lambda c: c.slot or 0)
    assert [c.x0 for c in cells] == [62, 206, 349, 635, 779, 922]
    assert all((c.y0, c.y1) == (1495, 1580) for c in cells)
    assert [c.side for c in cells] == ["left"] * 3 + ["right"] * 3
    assert all((c.width, c.height) == (100, 85) for c in cells)


def test_draft_card_grid_is_an_exact_lattice(db_path):
    """Every column shares one x-range, every row one y-range, with uniform pitch."""
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    cells = sorted(cfg.cells("draft_card"), key=lambda c: c.slot or 0)
    cols: dict[int, set] = {}
    rows: dict[int, set] = {}
    for c in cells:
        assert c.slot is not None, f"draft_card cell {c.name} has no slot"
        r, col = divmod(c.slot - 1, 5)
        cols.setdefault(col, set()).add((c.x0, c.x1))
        rows.setdefault(r, set()).add((c.y0, c.y1))
    assert all(len(v) == 1 for v in cols.values()), f"columns not aligned: {cols}"
    assert all(len(v) == 1 for v in rows.values()), f"rows not aligned: {rows}"
    xs = sorted({c.x0 for c in cells})
    ys = sorted({c.y0 for c in cells})
    assert [b - a for a, b in pairwise(xs)] == [160, 160, 160, 160]
    assert [b - a for a, b in pairwise(ys)] == [235, 235, 235]


def test_cell_types_have_the_three_distinct_aspects(db_path):
    """Three different aspect ratios is WHY each cell type needs its own transform."""
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    sizes = {
        t: (cfg.cells(t)[0].width, cfg.cells(t)[0].height)
        for t in ("locked_pick", "draft_locked_pick", "draft_card")
    }
    assert sizes == {
        "locked_pick": (100, 85),
        "draft_locked_pick": (100, 74),
        "draft_card": (110, 120),
    }


def test_tunables_present(db_path):
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    assert cfg.tunable_float("accept_score") == 0.70
    assert cfg.tunable_float("accept_margin") == 0.10
    assert cfg.tunable("icon_priority") == "game,wiki"
    assert cfg.scale_chain("locked_pick") == (1.01, 0.95, 1.08)
    assert cfg.scale_chain("draft_card") == (1.19, 1.10, 1.30)


def test_scale_chain_is_per_cell_type(cfg):
    assert cfg.scale_chain("draft_card") != cfg.scale_chain("summary_hero")
    # an unregistered cell type falls back rather than raising
    assert cfg.scale_chain("no_such_cell_type") == cfg.scale_chain("locked_pick")


def test_hero_rows_and_aliases(db_path):
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    heroes = cfg.heroes()
    assert heroes["sonja"].external_id == 66
    assert heroes["sonja"].game_icon == "spui_herohead_66.png"
    assert cfg.resolve_alias("Lucy Heartfilia") == "lucy"
    assert cfg.resolve_alias("Natsu Dragneel") == "natsu"
    assert cfg.resolve_alias("Sonja") == "sonja"
    assert cfg.resolve_alias("nobody at all") is None


def test_every_usable_roster_hero_has_a_game_icon(db_path):
    """The library cannot identify a hero it has no art for."""
    cfg = SolsticeConfig.load(db_path, event_slug="solstice-clash")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    usable = [
        r[0]
        for r in con.execute(
            "SELECT h.slug FROM solstice_roster r JOIN hero h ON h.name = r.name "
            "WHERE r.status='usable'"
        )
    ]
    con.close()
    assert len(usable) == 95
    heroes = cfg.heroes()
    missing = [s for s in usable if not heroes[s].game_icon]
    assert not missing, f"usable heroes without a game icon: {missing}"


def test_savannah_cup_has_its_own_geometry(savannah_cfg):
    """Geometry is per event, and the two must not be interchangeable.

    Loading without an event slug would hand Solstice frames the newest event's
    coordinates. That failed silently - identification scores collapsed rather than
    anything raising - so the separation is asserted directly.
    """
    cells = sorted(savannah_cfg.cells("draft_card"), key=lambda c: c.slot or 0)
    assert len(cells) == 15, "Savannah Cup draws 5x3; POOL_SIZE in store.py must agree"
    assert all((c.width, c.height) == (146, 197) for c in cells)
    xs = sorted({c.x0 for c in cells})
    ys = sorted({c.y0 for c in cells})
    assert [b - a for a, b in pairwise(xs)] == [155, 155, 155, 155]
    assert [b - a for a, b in pairwise(ys)] == [220, 220]

    locked = sorted(savannah_cfg.cells("locked_pick"), key=lambda c: c.slot or 0)
    assert [c.x0 for c in locked] == [70, 205, 340, 610, 745, 880]
    # y0=1000, not the portrait's true top near 975: a chat ticker crosses the row at
    # y~967 and would clip the cell. Ducking under it costs only the star band.
    assert all((c.y0, c.y1) == (1000, 1125) for c in locked)


def test_the_two_events_do_not_share_geometry(cfg, savannah_cfg):
    """The regression this whole split exists to prevent."""
    assert len(cfg.cells("draft_card")) == 20
    assert len(savannah_cfg.cells("draft_card")) == 15
