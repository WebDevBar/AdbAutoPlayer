"""Shared fixtures for the Solstice Clash service tests.

Every test here reads a COMMITTED PNG - nothing opens ADB - so the suite runs while the
device is in use.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

# This file sits at src-tauri/src-python/tests/games/afk_journey/services/solstice/
# parents[5] = src-python, parents[6] = src-tauri, parents[7] = repo root.
REPO = Path(__file__).resolve().parents[7]
SRC = Path(__file__).resolve().parents[5] / "adb_auto_player"
DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"
DATA = Path(__file__).parent / "data"
ANCHORS = (
    SRC / "games" / "afk_journey" / "templates" / "event" / "solstice_clash" / "anchors"
)


@pytest.fixture(scope="session")
def db_path() -> Path:
    assert DB.exists(), f"database missing: {DB}"
    return DB


@pytest.fixture(scope="session")
def frames() -> dict[str, Path]:
    found = {p.stem: p for p in DATA.glob("*.png")}
    assert found, f"no fixture frames in {DATA}"
    return found


@pytest.fixture(scope="session")
def anchor_dir() -> Path:
    return ANCHORS


def _read_frame(path: Path) -> np.ndarray:
    """Read a fixture frame, failing loudly if it is missing or unreadable.

    cv2.imread returns None rather than raising, which would otherwise surface as a
    confusing error deep inside a matcher.
    """
    img = cv2.imread(str(path))
    assert img is not None, f"could not read frame: {path}"
    return img


def _slot_of(cell) -> int:
    """Cell.slot is Optional[int]; every registered cell we iterate has one."""
    assert cell.slot is not None, f"cell {cell.name} has no slot"
    return cell.slot


@pytest.fixture(scope="session")
def read_frame():
    """Checked cv2.imread.

    The test directories are not packages, so shared helpers arrive as fixtures
    rather than imports.
    """
    return _read_frame


@pytest.fixture(scope="session")
def slot_of():
    return _slot_of
