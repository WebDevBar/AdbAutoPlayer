"""Old hero spellings must keep resolving, or saved profiles break.

Three names were corrected against the wiki's playable hero list on 2026-09-06.
`GeneralSettings.excluded_heroes` is `list[HeroesEnum]`, so a profile holding an old
spelling would fail validation outright rather than degrade - the user loses every
exclusion, not just the renamed one.
"""

import pytest

from adb_auto_player.games.afk_journey.heroes import HeroesEnum


@pytest.mark.parametrize(
    ("old", "expected"),
    [
        ("Isabelle", HeroesEnum.Isabella),
        ("Smokey", HeroesEnum.Smokey_and_Meerky),
        ("Sylphyra", HeroesEnum.Sylphira),
    ],
)
def test_old_spelling_still_resolves(old, expected):
    assert HeroesEnum(old) is expected


@pytest.mark.parametrize(
    "current", ["Isabella", "Smokey & Meerky", "Sylphira"]
)
def test_current_spelling_resolves(current):
    assert HeroesEnum(current).value == current


def test_an_unknown_name_still_raises():
    """The alias must not turn every typo into a silent pass."""
    with pytest.raises(ValueError):
        HeroesEnum("NotAHero")


def test_every_member_round_trips():
    for member in HeroesEnum:
        assert HeroesEnum(member.value) is member
