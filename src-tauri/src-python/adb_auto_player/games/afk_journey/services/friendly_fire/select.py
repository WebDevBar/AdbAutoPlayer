"""What to do about the cards, as a pure function of what was seen."""

from dataclasses import dataclass
from enum import StrEnum

from adb_auto_player.games.afk_journey.settings import OpponentPosition

from .geometry import Mode


class Action(StrEnum):
    """What the caller should do next."""

    TAKE = "take"
    REFRESH = "refresh"
    GIVE_UP = "give_up"
    STOP = "stop"


@dataclass(frozen=True)
class Decision:
    """An action, the card it applies to, and why - the reason goes in the log."""

    action: Action
    card: int | None
    reason: str


# Arena has no position setting, so its order is fixed.
_ARENA_ORDER: tuple[int, ...] = (0, 1)
_SA_ORDERS: dict[OpponentPosition, tuple[int, ...]] = {
    OpponentPosition.Left: (0, 1),
    OpponentPosition.Middle: (1, 0),
    # Right offers card 3 FIRST because the user asked for it, but never as a
    # fallback: card 3 is routinely out of the power bracket, and falling back onto
    # it would lose the battle in order to avoid a friend.
    OpponentPosition.Right: (2, 0, 1),
}


def preference_order(mode: Mode, position: OpponentPosition) -> tuple[int, ...]:
    """The cards to evaluate, in the order they should be preferred.

    Args:
        mode: which screen.
        position: the user's configured choice, ignored for Arena.

    Returns:
        Zero-based card indices, most preferred first.
    """
    if mode is Mode.ARENA:
        return _ARENA_ORDER
    return _SA_ORDERS[position]


def decide(
    order: tuple[int, ...],
    flagged_colour: set[int],
    flagged_ocr: set[int],
    control: str,
    excluded: frozenset[int] = frozenset(),
) -> Decision:
    """Choose an action.

    Args:
        order: cards to consider, most preferred first.
        flagged_colour: cards flagged by the colour arm.
        flagged_ocr: cards flagged by the OCR arm.
        control: "refresh", "give_up" or "unknown".
        excluded: cards a CONFIRMING read has already rejected. These stay rejected
            for the rest of the attempt - without that memory the loop re-evaluates
            from scratch and can take a card the second read called friendly.

    Returns:
        The action to take, with a reason for the log.
    """
    flagged = flagged_colour | flagged_ocr | excluded
    for card in order:
        if card not in flagged:
            return Decision(Action.TAKE, card, f"card {card + 1} is clear")

    if control == "refresh":
        return Decision(Action.REFRESH, None, "every evaluated card is flagged")

    if control != "give_up":
        return Decision(Action.STOP, None, "the control matched neither Refresh nor X")

    # Forfeiting costs a daily attempt, so one signal is not enough to justify it.
    # A single detector agreeing with itself across every refresh is exactly what a
    # persistent false positive looks like. An excluded card was rejected by a
    # DISAGREEMENT, which is one-signal evidence by definition, so it can never
    # satisfy this precondition either.
    single_signal = [c for c in order if c not in (flagged_colour & flagged_ocr)]
    if single_signal:
        return Decision(
            Action.STOP,
            None,
            f"card(s) {[c + 1 for c in single_signal]} flagged by one signal only - "
            f"refusing to forfeit an attempt on a single detector",
        )
    return Decision(Action.GIVE_UP, None, "every card flagged by both signals")
