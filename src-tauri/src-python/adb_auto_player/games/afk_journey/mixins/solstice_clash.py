"""AFK Journey Solstice Clash Mixin - Mode A, training and recording.

Spectates matches in a loop and records each outcome from the post-match summary, using
that OCR-confirmed ground truth to measure and tune identification on the draft and
prematch screens for Modes B and C.
"""

import logging
from abc import ABC
from time import sleep

from adb_auto_player.decorators import register_command
from adb_auto_player.games.afk_journey.base import AFKJourneyBase
from adb_auto_player.games.afk_journey.gui_category import AFKJCategory
from adb_auto_player.models.decorators import GUIMetadata
from adb_auto_player.models.geometry import Point
from adb_auto_player.ocr import RapidOCRBackend

# Measured on device 2026-07-26. The far branch (teleport plus auto-path) took ~12s, so
# 30s gives roughly 2.5x headroom on the only far position we have sampled.
NPC_DIALOG_TIMEOUT = 30.0
# Combat runs for minutes. wait_for_template defaults to template_timeout (10s), which
# would raise GameTimeoutError on every normal match, so this is always passed explicitly.
MATCH_TIMEOUT = 600.0
RESULT_POLL_DELAY = 2.0

# The "Current Theme: <name>" plate on the event screen, measured on s3.png (1080x1920
# native capture). Cropped tight to the theme name line only - "Current Theme" (the
# label above) and "Rotates in <n>" (below) are excluded so OCR isn't asked to split
# multiple lines. Verified against s3.png: RapidOCR reads "Converging Paths" cleanly
# from this exact region; Tesseract on the same crop garbled it ("Converoino Pathe"),
# so RapidOCR is used here rather than the TesseractBackend default seen elsewhere.
_THEME_NAME_REGION = (1195, 1280, 10, 540)  # y0, y1, x0, x1


class SolsticeClashMixin(AFKJourneyBase, ABC):
    """Solstice Clash data collection."""

    @register_command(
        name="SolsticeClashCollect",
        gui=GUIMetadata(
            label="Collect Solstice Clash Data",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Spectate Solstice Clash matches and record outcomes for analysis",
        ),
    )
    def collect_solstice_clash(self) -> None:
        """Spectate matches in a loop, recording each one."""
        # Screencap, not the H264 stream: streaming degrades OCR of stylised text and OCR
        # is this mode's ground truth.
        self.start_up(device_streaming=False)
        self.navigate_to_world()
        logging.info("Solstice Clash collection starting")

    def _open_spectate(self) -> tuple[bool, str | None]:
        """Navigate from the overworld to a live spectated match.

        Returns:
            (opened, theme). `theme` is read from the event screen on the way through -
            the only screen in the whole flow that shows it - and is None if unreadable.
        """
        # _navigate_menu_chain taps each template until it DISAPPEARS
        # (_tap_till_template_disappears, up to 3 attempts, then GameActionFailedError).
        # So every entry must be a tappable control that goes away when tapped. The last
        # entry is therefore the Solstice Clash CARD in the events list - NOT the event
        # screen's title, which stays on screen after arrival and would fail the chain.
        self._navigate_menu_chain(
            [
                "navigation/hamburger_menu",
                "dailies/hamburger/events",
                "event/solstice_clash/events_card",
            ]
        )
        # Arrival is confirmed by WAITING for a title that persists, never by tapping it.
        self.wait_for_template(template="event/solstice_clash/event_screen")
        self.wait_for_template(template="event/solstice_clash/fortune_picks")
        # Read the theme HERE, while the event screen is up. It shows "Current Theme:
        # <name>" and "Rotates in <n>"; no later screen in this flow shows either.
        theme = self._read_current_theme()
        self.tap(Point(121, 1606))  # Fortune Picks

        # Three branches converge here: adjacent to the NPC (immediate), a short walk
        # (~4s, NO popup at all), or far away (teleport popup, ~12s with auto-path).
        # The walk branch is why a fixed sleep is wrong - nothing signals it is happening.
        self.handle_popup_messages()
        result = self.wait_for_template(
            template="event/solstice_clash/spectate_live",
            delay=1.0,
            timeout=NPC_DIALOG_TIMEOUT,
            timeout_message="Royal City Show dialog did not appear",
        )
        self.tap(result)
        sleep(3)
        return True, theme

    def _read_current_theme(self) -> str | None:
        """OCR the theme name from the "Current Theme:" plate on the event screen.

        Returns:
            The theme name (e.g. "Converging Paths"), or None if OCR found nothing.
        """
        screenshot = self.get_screenshot()
        y0, y1, x0, x1 = _THEME_NAME_REGION
        crop = screenshot[y0:y1, x0:x1]
        text = RapidOCRBackend().extract_text(crop).strip()
        return text or None
