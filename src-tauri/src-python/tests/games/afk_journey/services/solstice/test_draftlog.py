"""Live draft logging - pure tests, no device, no GUI."""

from adb_auto_player.games.afk_journey.services.solstice.draftlog import (
    PickRead,
    better,
    format_final,
    format_pick,
    newly_locked,
)


def _read(slot, side, slug, score=0.9, margin=0.2, cell_type="draft_locked_pick"):
    return PickRead(
        slot=slot,
        side=side,
        cell_type=cell_type,
        slug=slug,
        name=slug.title() if slug else None,
        score=score,
        margin=margin,
    )


def test_only_new_picks_are_logged():
    seen: dict[int, str] = {}
    first = newly_locked(seen, [_read(1, "left", "lorsan")])
    assert [r.slug for r in first] == ["lorsan"]
    assert newly_locked(seen, [_read(1, "left", "lorsan")]) == []


def test_a_mid_draft_join_reports_everything_in_draft_order():
    """The normal case: we arrive with four picks already on screen."""
    seen: dict[int, str] = {}
    reads = [
        _read(4, "left", "thoran"),
        _read(1, "left", "lorsan"),
        _read(3, "right", "lily-may"),
        _read(2, "right", "smokey"),
    ]
    assert [r.slot for r in newly_locked(seen, reads)] == [1, 2, 3, 4]


def test_unidentified_cells_are_never_logged_as_picks():
    """`unknown` means sit this one out - it must never appear as a hero."""
    seen: dict[int, str] = {}
    assert newly_locked(seen, [_read(1, "left", None, score=0.4, margin=0.02)]) == []
    assert seen == {}


def test_a_corrected_read_is_logged_again():
    """A silent correction would leave the log disagreeing with the model's input."""
    seen: dict[int, str] = {}
    newly_locked(seen, [_read(1, "left", "lorsan")])
    again = newly_locked(seen, [_read(1, "left", "thoran")])
    assert [r.slug for r in again] == ["thoran"]


def test_an_identified_read_beats_an_unidentified_one_whatever_the_score():
    """A read that failed the accept rule is not weaker evidence, it is none."""
    rejected = _read(1, "left", None, score=0.99, margin=0.01)
    accepted = _read(1, "left", "lorsan", score=0.71, margin=0.11)
    assert better(rejected, accepted) is accepted
    assert better(accepted, rejected) is accepted


def test_between_two_identified_reads_the_stronger_wins():
    weak = _read(1, "left", "lorsan", score=0.72, margin=0.11)
    strong = _read(1, "left", "thoran", score=0.95, margin=0.30)
    assert better(weak, strong) is strong
    assert better(strong, weak) is strong


def test_better_handles_a_missing_geometry():
    only = _read(1, "left", "lorsan")
    assert better(None, only) is only
    assert better(only, None) is only
    assert better(None, None) is None


def test_the_pick_line_carries_its_evidence():
    line = format_pick(_read(2, "right", "lily-may", score=0.842, margin=0.213))
    assert line.startswith("Red (right) picked: Lily-May")
    assert "0.842/0.213" in line


def test_the_final_line_groups_by_side_in_draft_order():
    line = format_final(
        [
            _read(5, "left", "eironn"),
            _read(1, "left", "lorsan"),
            _read(2, "right", "smokey"),
            _read(4, "left", "thoran"),
            _read(6, "right", "reinier"),
            _read(3, "right", "lily-may"),
        ]
    )
    assert "Blue (left): Lorsan, Thoran, Eironn" in line
    assert "Red (right): Smokey, Lily-May, Reinier" in line
