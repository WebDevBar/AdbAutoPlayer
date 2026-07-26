"""AFK Journey Solstice Clash Mixin - Mode A, training and recording.

Spectates matches in a loop and records each outcome from the post-match summary, using
that OCR-confirmed ground truth to measure and tune identification on the draft and
prematch screens for Modes B and C.
"""

import logging
from abc import ABC
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

import cv2
import numpy as np

from adb_auto_player.decorators import register_command
from adb_auto_player.exceptions import GameTimeoutError
from adb_auto_player.games.afk_journey.base import AFKJourneyBase
from adb_auto_player.games.afk_journey.gui_category import AFKJCategory
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.models.decorators import GUIMetadata
from adb_auto_player.models.geometry import Point
from adb_auto_player.ocr import RapidOCRBackend

from ..services.solstice.config import SolsticeConfig
from ..services.solstice.icons import IconLibrary
from ..services.solstice.naming import resolve_hero_name
from ..services.solstice.store import AuditRow, HeroSlot, MatchRecord, MatchStore
from ..services.solstice.summary import SummaryHero, read_summary
from ..services.solstice.tuning import (
    confirmed_sides,
    learn_if_improved,
    train_from_frame,
)
# is where those functions are defined. Importing them here would leave this module
# unimportable at the end of Task 8 and fail its green-suite gate.

SOLSTICE_DB = Path("/mnt/docs/adbautoplayer/data/solstice_clash/heroes.sqlite")
SOLSTICE_ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")
TRAINING_ROOT = Path("/mnt/vault/solstice/training")

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

# User-tested: short presses can fail to open the popup, over-long ones do no harm. The
# cost is asymmetric, so bias long rather than tuning for the minimum that worked once.
LONGPRESS_SECONDS = 3.0
LONGPRESS_ATTEMPTS = 3
# Wait before grabbing the draft frame so more pick slots have filled.
TRAINING_LATE_DELAY = 8.0
# The prematch screen appears once the draft countdown expires. The draft ran ~25s
# in the observed match, so this covers a full draft plus the transition.
PREMATCH_WAIT_TIMEOUT = 90.0
# Reaching the event page can involve a scene load, so this is deliberately longer than
# the 10s default that comes from settings.
EVENT_SCREEN_TIMEOUT = 30.0
# The chat widget overlaps the locked hero cards. Measured on device 2026-07-26: the
# bubble defaults to (1012, 970) and the emoji to (1012, 1075), both inside the card band.
# Dragging the bubble to y620 clears both. The duration is load-bearing - see the helper.
CHAT_DRAG_X = 1012
CHAT_DRAG_FROM_Y = 970
CHAT_DRAG_TO_Y = 620
CHAT_DRAG_SECONDS = 2.0


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
        self._collect_forever()

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
        # Confirm we are on the Solstice Clash event page, then tap Fortune Picks by
        # COORDINATE. The button sits in a fixed position on this page, and template
        # matching it proved unreliable on the live screen - it scored 0.376 against a
        # template cut from an earlier capture of the same page. Arrival is what needs
        # verifying; the button's location does not.
        self.wait_for_template(
            template="event/solstice_clash/event_screen",
            timeout=EVENT_SCREEN_TIMEOUT,
            timeout_message="did not reach the Solstice Clash event page",
        )
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
        text = self._ocr.extract_text(crop).strip()
        return text or None

    def _run_one_match(self) -> bool:
        """Spectate one match and record it. Returns True if a match was recorded."""
        # _open_spectate reads the theme while it is ON the event screen and returns it.
        # The theme cannot be read before that call (we are on the overworld) and cannot
        # be read after it (the summary screen does not show it).
        opened, theme = self._open_spectate()
        if not opened:
            return False

        # Optional training material. If we entered mid-match there is no draft left to
        # capture - that is normal, not an error, and must never block recording.
        # The draft screen is up NOW (or we entered mid-match and it is not).
        # Shift the chat widget out of the card band before anything is read. By default
        # the chat bubble sits at y970 and the emoji at y1075, both INSIDE the locked-card
        # band (y1005-1085), and the emoji overlaps the last red card. Measured cost of
        # the overlay on that cell: about 0.10-0.14 of match score.
        self._move_chat_out_of_the_way()

        draft_frame = self._capture_training_frame(
            "event/solstice_clash/draft_anchor", late=True, wait_timeout=0.0
        )
        # The prematch screen only appears AFTER the draft ends, so it must be WAITED
        # for, not probed. Probing immediately after the draft capture would return None
        # on exactly the matches where training data is available.
        prematch_frame = self._capture_training_frame(
            "event/solstice_clash/prematch_anchor",
            late=False,
            wait_timeout=PREMATCH_WAIT_TIMEOUT if draft_frame is not None else 0.0,
        )

        self.wait_for_template(
            template="event/solstice_clash/result_back",
            delay=RESULT_POLL_DELAY,
            timeout=MATCH_TIMEOUT,
            timeout_message="no result screen - abandoning this match",
        )
        chart = self.wait_for_template(template="event/solstice_clash/result_chart")
        self.tap(chart)
        sleep(2)

        self._record_summary(draft_frame, prematch_frame, theme)

        back = self.wait_for_template(template="event/solstice_clash/summary_back")
        self.tap(back)
        sleep(1)
        green_back = self.wait_for_template(template="event/solstice_clash/result_back")
        self.tap(green_back)
        sleep(3)
        return True

    def _collect_forever(
        self, max_restarts: int = 3, max_matches: int | None = None
    ) -> None:
        """Loop until the restart budget is exhausted.

        Recovery has to be bounded or an unattended run spends the night retrying. The
        counter resets on every recorded match, so one bad match cannot accumulate toward
        the limit across an otherwise healthy night. Three CONSECUTIVE failures means
        something structural changed - a moved button, the event ending, a wedged device -
        and continuing would produce only noise.
        """
        consecutive_failures = 0
        recorded = 0
        while consecutive_failures < max_restarts:
            if max_matches is not None and recorded >= max_matches:
                logging.info(f"recorded {recorded} match(es), stopping as requested")
                return
            try:
                if self._run_one_match():
                    consecutive_failures = 0
                    recorded += 1
                    continue
                consecutive_failures += 1
                logging.warning(
                    f"no match recorded ({consecutive_failures}/{max_restarts})"
                )
            except Exception as exc:  # noqa: BLE001 - one bad match must not end the run
                consecutive_failures += 1
                logging.warning(
                    f"match failed ({consecutive_failures}/{max_restarts}): {exc}"
                )
            self.navigate_to_world()

        raise GameTimeoutError(
            f"stopping: {max_restarts} consecutive cycles recorded no match"
        )

    def _record_summary(self, draft_frame, prematch_frame, theme: str | None) -> None:
        """Read the summary, record the match, and audit every identification.

        `theme` is read during navigation (the summary screen does NOT show it) and
        passed in, so a match cannot be recorded against the wrong balance epoch.
        """
        frame = self.get_screenshot()
        read = read_summary(frame, self._solstice_cfg, self._solstice_library, self._ocr)

        match_id = self._store.record_match(
            MatchRecord(
                source="spectate_summary",
                captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
                theme=theme,
                outcome=read.winner,
                outcome_source="observed",
                blue_player=read.blue_player,
                red_player=read.red_player,
            )
        )

        slots: list[HeroSlot] = []
        for hero in read.heroes:
            confirmed = self._confirm_by_longpress(hero)
            slots.append(
                HeroSlot(
                    side=hero.side,
                    slot=hero.slot,
                    hero_slug=confirmed or hero.slug,
                    art_ref=hero.art_ref,
                    status="identified" if (confirmed or hero.slug) else "unknown",
                    score=hero.score,
                    margin=hero.margin,
                    cell_type="summary_hero",
                    stat_sword=hero.stats.sword,
                    stat_heart=hero.stats.heart,
                    stat_shield=hero.stats.shield,
                    identified_by="longpress_ocr" if confirmed else "image",
                )
            )
            audit_id = self._store.record_audit(
                AuditRow(
                    screen_slug="solstice_summary",
                    side=hero.side,
                    slot=hero.slot,
                    image_slug=hero.slug,
                    image_art_ref=hero.art_ref,
                    image_score=hero.score,
                    image_margin=hero.margin,
                    ocr_slug=confirmed,
                    frame_path=None if confirmed == hero.slug else self._archive(frame),
                    match_id=match_id,
                )
            )
            cell = next(
                c
                for c in self._solstice_cfg.cells("summary_hero")
                if c.side == hero.side and c.slot == hero.slot
            )
            if learn_if_improved(
                store=self._store,
                cfg=self._solstice_cfg,
                library=self._solstice_library,
                gray=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                centre=((cell.x0 + cell.x1) // 2, (cell.y0 + cell.y1) // 2),
                screen_slug="solstice_summary",
                image_slug=hero.slug,
                confirmed_slug=confirmed,
                art_ref=hero.art_ref or (confirmed or ""),
                current_score=hero.score,
                current_margin=hero.margin,
                audit_id=audit_id,
            ):
                logging.info(f"tuned {confirmed} on the summary screen")
        self._store.record_heroes(match_id, slots)

        # Only long-press-OCR-confirmed identities may seed this - see confirmed_sides().
        confirmed_by_side = confirmed_sides(slots)

        for frame_img, screen_slug, cell_type in (
            (draft_frame, "spectate_draft_picks", "draft_pick"),
            (prematch_frame, "spectate_prematch", "prematch_pick"),
        ):
            if frame_img is None:
                continue  # entered mid-match: normal, never an error
            result = train_from_frame(
                store=self._store, cfg=self._solstice_cfg,
                library=self._solstice_library, frame=frame_img,
                screen_slug=screen_slug, cell_type=cell_type,
                confirmed_by_side=confirmed_by_side,
                frame_path=self._archive(frame_img, kind=screen_slug),
                match_id=match_id,
            )
            # deduced/set_consistent are MEASUREMENTS, never confirmation - see
            # train_from_frame's docstring. Logged only, never used to learn a transform.
            logging.info(
                f"{screen_slug}: recorded {result.written} rows, "
                f"{result.deduced} deduced by elimination, "
                f"{result.set_consistent} set-consistent"
            )

    # --- lazily built, because IconLibrary decoding takes seconds and the GUI imports
    # --- this module at startup.
    @property
    def _solstice_cfg(self) -> SolsticeConfig:
        if getattr(self, "_cfg_cache", None) is None:
            self._cfg_cache = SolsticeConfig.load(SOLSTICE_DB)
        return self._cfg_cache

    @property
    def _solstice_library(self) -> IconLibrary:
        if getattr(self, "_lib_cache", None) is None:
            self._lib_cache = IconLibrary.build(self._solstice_cfg, SOLSTICE_ICON_DIR)
        return self._lib_cache

    @property
    def _ocr(self) -> RapidOCRBackend:
        if getattr(self, "_ocr_cache", None) is None:
            self._ocr_cache = RapidOCRBackend()
        return self._ocr_cache

    @property
    def _store(self) -> MatchStore:
        if getattr(self, "_store_cache", None) is None:
            self._store_cache = MatchStore(SOLSTICE_DB)
        return self._store_cache

    def _archive(self, frame: np.ndarray, kind: str = "frame") -> str:
        """Save a frame to the vault and return its path.

        Never /tmp - that is a 16GB tmpfs this project has already filled once. Never
        rmtree the directory either: training frames accumulate across runs by design.
        """
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        directory = TRAINING_ROOT / day
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%H%M%S_%f")
        path = directory / f"{kind}_{stamp}.png"
        cv2.imwrite(str(path), frame)
        return str(path)

    def _move_chat_out_of_the_way(self) -> None:
        """Drag the chat bubble upward so it and the emoji clear the hero cards.

        The drag MUST be slow. Measured on device: a 600ms swipe is treated as a fling and
        moves the widget only about 40px, while a 2000ms drag moves it fully. Dragging the
        bubble carries the emoji with it.

        Best effort - a failure here costs a little match score, never a match, so it must
        never raise into the loop.
        """
        try:
            self.swipe_up(
                x=CHAT_DRAG_X,
                sy=CHAT_DRAG_FROM_Y,
                ey=CHAT_DRAG_TO_Y,
                duration=CHAT_DRAG_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - cosmetic step, never fatal
            logging.debug(f"could not move the chat widget: {exc}")

    def _capture_training_frame(
        self, anchor: str, late: bool, wait_timeout: float = 0.0
    ) -> np.ndarray | None:
        """Grab one draft or prematch frame.

        Returns None when the screen never appears, which is NORMAL - we may have entered
        mid-match. This is optional training material and must never prevent the outcome
        from being recorded.

        `wait_timeout=0` probes for a screen that should be up right now. A positive value
        WAITS for one that has not happened yet, which is the prematch case: it only
        exists after the draft ends.

        `late=True` delays before capturing so more pick slots have filled - the training
        targets are the pick slots, so an early frame with an empty strip is worthless.
        """
        if wait_timeout <= 0:
            if self.game_find_template_match(template=anchor) is None:
                return None
        else:
            try:
                self.wait_for_template(
                    template=anchor,
                    delay=1.0,
                    timeout=wait_timeout,
                    timeout_message=f"{anchor} never appeared - skipping training capture",
                )
            except GameTimeoutError:
                # Not an error: the match may have been entered late, or the transition
                # may have been missed. Recording the outcome does not depend on this.
                return None

        if late:
            sleep(TRAINING_LATE_DELAY)
        frame = self.get_screenshot()
        return frame

    def _confirm_by_longpress(self, hero: SummaryHero) -> str | None:
        """Long-press a summary card and OCR the hero name from the popup.

        Returns the confirmed slug, or None if no popup could be read.
        """
        cell = next(
            c
            for c in self._solstice_cfg.cells("summary_hero")
            if c.side == hero.side and c.slot == hero.slot
        )
        point = Point((cell.x0 + cell.x1) // 2, (cell.y0 + cell.y1) // 2)

        for _ in range(LONGPRESS_ATTEMPTS):
            slug: str | None = None
            try:
                self.hold(point, duration=LONGPRESS_SECONDS)
                sleep(1.0)
                frame = self.get_screenshot()
                # The popup renders downward from blue cards and upward from red ones, so
                # its position is not fixed - it is detected by CONTENT, not geometry.
                blocks = self._ocr.detect_text_blocks(frame, ConfidenceValue(0.5))
                slug = resolve_hero_name([b.text for b in blocks], self._solstice_cfg)
            except Exception as exc:  # noqa: BLE001 - one unreadable card must not end the match
                logging.warning(f"long-press confirm failed: {exc}")
            finally:
                # Dismiss on EVERY path, including failure - even if hold or
                # get_screenshot raised above. A popup left open covers the screen, so
                # the next long-press and the navigation that follows would act on the
                # wrong UI state - and that failure would look like a matching problem.
                self.tap(Point(540, 1750))
                sleep(0.5)
            if slug is not None:
                return slug
        return None
