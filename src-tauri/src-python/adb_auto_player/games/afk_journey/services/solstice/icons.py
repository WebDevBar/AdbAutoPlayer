"""Hero icon library, decoded from the installed game's own assets.

These are the REAL in-game textures, not wiki uploads, and they beat the wiki
library on every measured surface: the locked-pick blind test went 52/54 -> 54/54
correct, median score 0.797 -> 0.973. Two heroes only work with game art at all:
the wiki's Reinier is a different picture (0.631 -> 0.975) and Eironn resolves via
his skin (0.648 -> 0.953).

## Container format (reverse-engineered 2026-07-26)

Files are named `*.png` but are NOT PNGs:

    bytes 0-2   "AST"
    byte  3,4   width  = b3 + b4*256
    byte  5,6   height = b5 + b6*256
    byte  7     13 (block-size code; every observed file is ASTC 6x6)
    bytes 8-11  uncompressed size, uint32 LE
    bytes 12+   LZ4 *block* (not frame) compressed ASTC data

Decode = LZ4-block decompress -> ASTC 6x6 -> flip vertically (Unity's origin is
bottom-left). Verified on every size variant present: 180x248 -> 20160,
508x716 -> 163200, 300x565 -> 76000, 280x168 -> 21056. All 1123 files decode
with zero failures.

**Never assume 180x248.** Read width/height from the header; forcing a 184x248
skin into 180 wide cost roughly 0.15 of match score.

## Naming

    spui_herohead_<ID>.png       base icon; <ID> is the GAME's own hero id
    spui_herohead_<ID>_s1.png    skin variant (any suffix after the id marks a skin)

IDs below 1000 are heroes; 1000+ are NPCs, mobs and bosses.

## Gamma

Decoded RGB renders darker than the game draws it. Applying
`library_config.gamma` (exponent 1/1.8) both looks right and measurably improves
matching against real ADB frames: median 0.9550 -> 0.9718, worst 0.9055 -> 0.9115.
It is applied at library-build time and never baked into the files on disk, so it
stays tunable.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

import cv2
import lz4.block
import numpy as np
import texture2ddecoder

from .config import SolsticeConfig

ASTC_BLOCK = 6
NPC_ID_FLOOR = 1000
FLATTEN_BG = 190.0  # must match how cells are flattened in vision.py

_ICON_NAME = re.compile(r"^spui_herohead_(\d+)(?:_(.+))?$")


def decode_ast(path: Path) -> np.ndarray:
    """Decode one AST container to a BGRA image, correctly oriented."""
    data = path.read_bytes()
    if data[:3] != b"AST":
        raise ValueError(f"not an AST container: {path}")
    width = data[3] + data[4] * 256
    height = data[5] + data[6] * 256
    raw_size = struct.unpack("<I", data[8:12])[0]
    raw = lz4.block.decompress(data[12:], uncompressed_size=raw_size)
    rgba = texture2ddecoder.decode_astc(raw, width, height, ASTC_BLOCK, ASTC_BLOCK)
    img = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    return cv2.flip(img, 0)  # Unity origin is bottom-left


def to_match_gray(bgra: np.ndarray, gamma: float) -> np.ndarray:
    """Gamma-correct, flatten onto the cell background, and convert to grayscale."""
    rgb = np.clip(bgra[:, :, :3].astype(np.float32) / 255.0, 0, 1)
    rgb = 255.0 * np.power(rgb, gamma)
    alpha = bgra[:, :, 3:4].astype(np.float32) / 255.0
    flat = rgb * alpha + FLATTEN_BG * (1 - alpha)
    return cv2.cvtColor(flat.astype(np.uint8), cv2.COLOR_BGR2GRAY)


@dataclass(frozen=True)
class IconEntry:
    """One matchable image.

    A skin carries its HERO's slug - identification never needs to name the skin,
    only the hero.
    """

    slug: str
    art_ref: str
    art_kind: str  # 'base' | 'skin'
    gray: np.ndarray


class IconLibrary:
    def __init__(self, entries: list[IconEntry]) -> None:
        self._entries = entries

    @classmethod
    def build(cls, cfg: SolsticeConfig, icon_dir: Path) -> IconLibrary:
        """Decode every hero icon under `icon_dir/hero`, gamma-corrected and grayscaled.

        Skips NPC ids and any id with no hero mapping (an unreleased hero, or an NPC the
        wiki files under Category:Heroes).
        """
        gamma = cfg.tunable_float("gamma")
        by_external = {
            hero.external_id: slug
            for slug, hero in cfg.heroes().items()
            if hero.external_id is not None
        }
        entries: list[IconEntry] = []
        for path in sorted((icon_dir / "hero").glob("spui_herohead_*.png")):
            match = _ICON_NAME.match(path.stem)
            if match is None:
                continue
            hero_id = int(match.group(1))
            if hero_id >= NPC_ID_FLOOR:
                continue
            slug = by_external.get(hero_id)
            if slug is None:
                continue
            entries.append(
                IconEntry(
                    slug=slug,
                    art_ref=path.stem,
                    art_kind="skin" if match.group(2) else "base",
                    gray=to_match_gray(decode_ast(path), gamma),
                )
            )
        return cls(entries)

    def entries(self) -> list[IconEntry]:
        return list(self._entries)

    def for_slugs(self, slugs: set[str]) -> list[IconEntry]:
        """The pool-constrained subset: 121 candidates down to <= 20 for a match."""
        return [e for e in self._entries if e.slug in slugs]
