"""Where the two signals, the control and the decision meet."""

import logging

import numpy as np

from adb_auto_player.games.afk_journey.settings import OpponentPosition
from adb_auto_player.models import ConfidenceValue

from .collect import archive
from .control import classify_control
from .detect import card_has_badge_text, cards_with_badges
from .geometry import CARD_X_RANGES, OCR_CONFIDENCE_FLOOR, OCR_Y_RANGE, Mode
from .select import Decision, decide, preference_order

_SCREEN_CHANGE_THRESHOLD = 1.0


def _ocr_flags(frame: np.ndarray, mode: Mode, cards, ocr_backend) -> set[int]:
    """Which of `cards` carry badge text.

    Each card is OCR'd from its OWN crop, so the results need no coordinate mapping
    back to screen space - which is where an earlier design went wrong.
    """
    flagged: set[int] = set()
    y0, y1 = OCR_Y_RANGE[mode]
    for index in cards:
        x0, x1 = CARD_X_RANGES[mode][index]
        try:
            blocks = ocr_backend.detect_text_blocks(
                frame[y0:y1, x0:x1], ConfidenceValue(OCR_CONFIDENCE_FLOOR)
            )
        except Exception as exc:
            logging.warning(f"[FF-22] OCR failed on card {index + 1}: {exc}")
            continue
        if card_has_badge_text(blocks):
            flagged.add(index)
    return flagged


def evaluate(
    frame: np.ndarray,
    mode: Mode,
    position: OpponentPosition,
    ocr_backend,
    templates,
    excluded: frozenset[int] = frozenset(),
) -> Decision:
    """Read one select-opponent frame and decide what to do about it.

    Args:
        frame: BGR screenshot at 1080x1920, as `get_screenshot()` returns it.
        mode: which screen this is.
        position: the user's configured Opponent Position (ignored for Arena).
        ocr_backend: anything exposing `detect_text_blocks`.
        templates: the game's template directory (`Game.template_dir`).
        excluded: cards already rejected by a confirming read this attempt.

    Returns:
        The action to take, with a reason for the log.
    """
    order = preference_order(mode, position)
    colour = cards_with_badges(frame, mode, frozenset(order))
    ocr = _ocr_flags(frame, mode, order, ocr_backend)
    control = classify_control(frame, mode, templates)
    decision = decide(order, colour, ocr, control, excluded)

    disagreement = colour ^ ocr
    outcome = "-".join(
        [
            decision.action.value,
            f"colour{sorted(c + 1 for c in colour)}",
            f"ocr{sorted(c + 1 for c in ocr)}",
            control,
        ]
    ).replace(" ", "")
    archive(frame, mode, outcome + ("-DISAGREE" if disagreement else ""))

    logging.info(
        f"[FF-01] {mode.value}: colour flagged {sorted(c + 1 for c in colour)}, "
        f"OCR flagged {sorted(c + 1 for c in ocr)}, control={control} "
        f"-> {decision.action.value} ({decision.reason})"
    )
    return decision


def confirms_take(
    frame: np.ndarray,
    mode: Mode,
    position: OpponentPosition,
    ocr_backend,
    card: int,
) -> bool:
    """Second read: is `card` STILL clear on a fresh frame?

    The mode has no timer, so a confirming read is free and the cost of being wrong
    is not. A card seen clear once and flagged once is treated as flagged - the
    optimistic read would defeat the whole feature.

    Args:
        frame: a SECOND screenshot, taken after the one that produced the decision.
        mode: which screen.
        position: the configured preference.
        ocr_backend: anything exposing `detect_text_blocks`.
        card: the card the first read chose.

    Returns:
        True only if both reads agree the card is clear.
    """
    order = preference_order(mode, position)
    colour = cards_with_badges(frame, mode, frozenset(order))
    ocr = _ocr_flags(frame, mode, [card], ocr_backend)
    if card in (colour | ocr):
        logging.warning(
            f"[FF-02] second read disagrees on card {card + 1} - treating as flagged"
        )
        archive(frame, mode, f"DISAGREE-card{card + 1}")
        return False
    return True


def screen_changed(before: np.ndarray, after: np.ndarray) -> bool:
    """Whether a refresh actually redrew the cards.

    A stalled refresh and an exhausted one look identical from a single frame, and
    acting on the guess taps a control that forfeits an attempt.

    Args:
        before: frame taken before tapping Refresh.
        after: frame taken after.

    Returns:
        True if the screen changed.
    """
    if before.shape != after.shape:
        return True
    difference = np.mean(np.abs(before.astype(np.int16) - after.astype(np.int16)))
    return bool(difference > _SCREEN_CHANGE_THRESHOLD)
