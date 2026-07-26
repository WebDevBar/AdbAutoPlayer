"""Geometry and tunables for Solstice Clash, loaded from heroes.sqlite.

No hardcoded cell rectangles, scale chains or thresholds live in this package. Every
number was measured on raw 1080x1920 ADB frames and is stored in the database, so
changing one means re-measuring and updating `cell_registry` / `library_config`.

AdbAutoPlayer forces the device to 1080x1920 (`games/afk_journey/base.py` sets
`base_resolution`; `game/_screenshot_mixin.py` calls `device.set_display_size()`),
so these coordinates are portable across devices.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cell:
    """A named rectangle on a screen, containing only unobstructed hero art.

    Bounds deliberately exclude the star crown above and the level plate below, so
    a crop is pure portrait with no frame or text bleed.
    """

    name: str
    cell_type: str
    x0: int
    y0: int
    x1: int
    y1: int
    side: str | None
    slot: int | None

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass(frozen=True)
class HeroRow:
    slug: str
    name: str
    faction: str | None
    external_id: int | None
    game_icon: str | None
    wiki_icon: str | None


class SolsticeConfig:
    """Read-only view of the geometry, tunables and hero rows."""

    def __init__(
        self,
        cells: dict[str, list[Cell]],
        tunables: dict[str, str],
        heroes: dict[str, HeroRow],
        aliases: dict[str, str],
    ) -> None:
        self._cells = cells
        self._tunables = tunables
        self._heroes = heroes
        self._aliases = aliases

    @classmethod
    def load(cls, db_path: Path) -> SolsticeConfig:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cells: dict[str, list[Cell]] = {}
            for row in con.execute(
                "SELECT cell_name,cell_type,x0,y0,x1,y1,side,slot FROM cell_registry "
                "ORDER BY cell_type, slot"
            ):
                cells.setdefault(row[1], []).append(Cell(*row))
            tunables = dict(con.execute("SELECT key,value FROM library_config"))
            heroes = {
                r[0]: HeroRow(*r)
                for r in con.execute(
                    "SELECT slug,name,faction,external_id,game_icon,wiki_icon FROM hero"
                )
            }
            aliases = dict(con.execute("SELECT alias,hero_slug FROM hero_alias"))
        finally:
            con.close()
        return cls(cells, tunables, heroes, aliases)

    def cells(self, cell_type: str) -> list[Cell]:
        return list(self._cells.get(cell_type, ()))

    def tunable(self, key: str) -> str:
        return self._tunables[key]

    def tunable_float(self, key: str) -> float:
        return float(self._tunables[key])

    def scale_chain(self, cell_type: str) -> tuple[float, ...]:
        """Scales to try, in order.

        Fix the SCALE and let matchTemplate find the offset; fixing the offset
        instead dropped one hero from 0.978 to 0.408.
        """
        key = "scale_draft_card" if cell_type == "draft_card" else "scale_chain"
        return tuple(float(x) for x in self._tunables[key].split(","))

    def heroes(self) -> dict[str, HeroRow]:
        return dict(self._heroes)

    def resolve_alias(self, name: str) -> str | None:
        """Slug for an alias, an exact name, or a case-insensitive name.

        Returns None if unknown. Aliases cover collab long names
        (`Lucy Heartfilia` -> `lucy`), `...New` suffixes and spelling variants.
        """
        if name in self._aliases:
            return self._aliases[name]
        lowered = name.lower()
        for slug, hero in self._heroes.items():
            if hero.name.lower() == lowered:
                return slug
        return None
