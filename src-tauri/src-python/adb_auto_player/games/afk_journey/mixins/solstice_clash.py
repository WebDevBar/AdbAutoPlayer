"""AFK Journey Solstice Clash Mixin - Mode A, training and recording.

Spectates matches in a loop and records each outcome from the post-match summary, using
that OCR-confirmed ground truth to measure and tune identification on the draft and
prematch screens for Modes B and C.
"""

import logging
from abc import ABC
from datetime import UTC, datetime
from pathlib import Path
import time as time_module
from time import sleep

import cv2
import numpy as np

from adb_auto_player.decorators import register_command
from adb_auto_player.exceptions import GameActionFailedError, GameTimeoutError
from adb_auto_player.game._template_mixin import _UndesiredResultError
from adb_auto_player.models.template_matching import TemplateMatchResult
from adb_auto_player.games.afk_journey.base import AFKJourneyBase
from adb_auto_player.games.afk_journey.gui_category import AFKJCategory
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.models.decorators import GUIMetadata
from adb_auto_player.models.geometry import Point
from adb_auto_player.ocr import RapidOCRBackend

from ..services.solstice.config import SolsticeConfig
from ..services.solstice.icons import IconLibrary
from ..services.solstice.matchkey import is_complete, natural_key
from ..services.solstice.naming import resolve_hero_name_strict
from ..services.solstice.store import AuditRow, HeroSlot, MatchRecord, MatchStore
from ..services.solstice.summary import SummaryHero, read_summary
from ..services.solstice.sync import SyncClient
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
# Measured from the moment the prematch screen is seen, so it covers the last-chance
# countdown plus the fight. A match runs about a minute at 2x speed and ults stretch it,
# so 4 minutes is a comfortable ceiling without being the 10 minutes we started with.
MATCH_TIMEOUT = 240.0
# Poll every 5 seconds for either the result screen or the overworld. Both WAIT for input
# once reached, so the only cost of a longer interval is idle time on a screen that is
# already finished - measured at up to a full interval. 5s keeps that barely visible while
# still being about 48 checks per match rather than the 90 the original 2s interval used.
RESULT_POLL_DELAY = 5.0
# Real draws happen, but not many in a row. A long streak means the result screen
# is being read as the overworld - and since a draw resets the failure counter,
# nothing else in the loop can ever notice.
MAX_CONSECUTIVE_DRAWS = 4

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
# Master switch for long-press OCR confirmation. OFF: the popup proved unreliable on
# device and each attempt costs a full-frame OCR. Turn back on once popup opening is
# solved; everything downstream already handles its absence.
LONGPRESS_VERIFICATION = False
# Empty middle of the details overlay. Verified on device: tapping here closes an open
# hero popup and returns the screen to its exact prior state (delta 0.07).
#
# The overlay only has two clickable controls, the back arrow on the left and Replay on
# the right; the green button visible through it belongs to the screen underneath and is
# not reachable. Deliberately NOT y1830 - that sits close enough to the bottom edge to
# risk the Android gesture area - and NOT the top gap at y170, which does nothing at all.
POPUP_DISMISS_AT = Point(540, 1700)
# Wait before grabbing the draft frame so more pick slots have filled.
# Delay before grabbing the draft frame, so more pick slots have filled. Kept small: the
# long-press confirmation it was feeding is shelved, so a late frame buys little today.
# Raise it again if cross-screen training is turned back on.
TRAINING_LATE_DELAY = 1.0
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
        name="SolsticeClashSync",
        gui=GUIMetadata(
            label="Sync Solstice Clash Data",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Upload collected matches to the shared pool and fetch everyone else's",
        ),
    )
    def sync_solstice_clash(self) -> None:
        """Push then pull, without starting a collection run.

        Push first so this install's own matches are in the pool before it reads
        the pool back. Needs no device: it only touches the local database and
        the network.
        """
        store = MatchStore(SOLSTICE_DB)
        sync = SyncClient(store)
        if not sync.enabled:
            logging.warning("[SC-36] sync is disabled - nothing to do")
            return
        pushed, duplicate, rejected = sync.push()
        pulled = sync.pull()
        logging.info(
            f"[SC-35] sync: pushed {pushed}, duplicate {duplicate}, "
            f"rejected {rejected}, pulled {pulled}"
        )

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
        # Refuse to tap anything unless we are demonstrably on the overworld.
        # _tap_till_template_disappears SILENTLY SUCCEEDS when its template is never
        # found (its while loop simply never runs), so mid-battle this whole chain
        # no-ops without complaint and the failure only surfaces 30s later as SC-01.
        # Worse, the recovery that follows presses Back - which is what made the game
        # ask "Exit battle?" during a live match on 2026-07-27.
        # Raise rather than recover here: _collect_forever already charges this to the
        # failure budget AND runs navigate_to_world() in its own protected block, so
        # recovering inline would duplicate that and hide a repeating bad state from
        # the 3-strike counter.
        if not self._is_in_overview():
            raise GameActionFailedError(
                "[SC-25] not on the overworld - refusing to start the menu chain "
                "(a battle or another screen may still be up)"
            )
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
            timeout_message="[SC-01] did not reach the Solstice Clash event page",
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
            timeout_message="[SC-02] Royal City Show dialog did not appear",
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

        # The clock starts HERE - once the prematch screen has been SEEN. There is no need
        # to wait for it to clear: seeing it is enough to know the fight is about to begin,
        # and waiting for it to disappear was a minute of dead time per match.
        # Wait for the result screen. A DRAW never produces one - the game drops back
        # to the overworld with no result and no summary - so a draw has to be inferred
        # from ABSENCE, which makes it the default answer whenever result detection has
        # an off day. That is exactly what went wrong on 2026-07-27: a bare full-frame
        # match for the small green `homestead_enter` icon out-scored a result screen,
        # every match was read as a draw, and because a draw resets the failure counter
        # the run never stopped - it just tapped blindly through live battles instead
        # (the game asked "Exit battle?").
        #
        # So: only RESULT-SCREEN templates decide "the match ended", and the overworld
        # is confirmed with the framework's own _is_in_overview(), which crops to the
        # top-right corner and keys on the time-of-day dial. Measured on real frames:
        # 0.99 on the overworld against 0.51-0.57 on the draft, betting and menu
        # screens, versus the old icon's 0.99-vs-0.60. Do not hand-roll this again.
        # Polled through the framework's own _execute_or_timeout - the primitive every
        # wait_* helper is built on. No wait_* helper fits directly: the result templates
        # need the FULL frame while _is_in_overview deliberately crops to the top-right
        # corner, and none of them can express "seen twice in a row". Hand-rolling the
        # poll instead would re-lose monotonic timing, which matters on an overnight run
        # where an NTP step would otherwise stretch or shorten this timeout silently.
        overworld_seen = 0

        def _match_end() -> TemplateMatchResult | None:
            """Return a result-screen match, None for a confirmed draw, else poll on."""
            nonlocal overworld_seen
            frame = self.get_screenshot()
            hit = self.find_any_template(
                [
                    # result_chart FIRST: it is the DETAILS button tapped next, so its
                    # presence is what the following step depends on anyway. result_back
                    # is a large flat green button - the low-texture kind that misses on
                    # a variant screen.
                    "event/solstice_clash/result_chart",
                    "event/solstice_clash/result_back",
                ],
                screenshot=frame,
            )
            if hit is not None:
                return hit
            if self._is_in_overview(screenshot=frame):
                # Confirm twice. A draw parks the game on the overworld until something
                # taps, so a real draw always survives a re-check; a mid-transition
                # frame does not. Nothing re-enters a battle by itself, so this can
                # only filter transients - it can never miss a genuine draw.
                overworld_seen += 1
                if overworld_seen >= 2:
                    return None
            else:
                overworld_seen = 0
            raise _UndesiredResultError()

        found = self._execute_or_timeout(
            _match_end,
            delay=RESULT_POLL_DELAY,
            timeout=MATCH_TIMEOUT,
            timeout_message=(
                "[SC-03] match did not end in time: no result screen appeared and we "
                "never settled on the overworld"
            ),
        )
        if found is None:
            logging.info(
                "[SC-10] no result screen and the overworld confirmed twice - draw, "
                "nothing to record"
            )
            self._draw_this_cycle = True
            return True
        logging.debug(
            f"[SC-09] match-end anchor: {found.template} "
            f"confidence={found.confidence} box={found.box}"
        )
        chart = self.wait_for_template(
            template="event/solstice_clash/result_chart",
            timeout_message=(
                "[SC-04] result screen reached but the DETAILS (chart) button never "
                "appeared"
            ),
        )
        self.tap(chart)
        sleep(2)

        self._record_summary(draft_frame, prematch_frame, theme)
        # Durably recorded. Anything that fails after this point is a navigation problem,
        # not a lost match, and must not count against the failure budget.
        self._match_recorded_this_cycle = True

        back = self.wait_for_template(
            template="event/solstice_clash/summary_back",
            timeout_message="[SC-05] summary recorded but its Back button never appeared",
        )
        self.tap(back)
        sleep(1)
        green_back = self.wait_for_template(
            template="event/solstice_clash/result_back",
            timeout_message="[SC-06] left the summary but the result Back never appeared",
        )
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
        consecutive_draws = 0
        recorded = 0

        # Seed the local pool before collecting. Wrapped like every other
        # sync call: a dead endpoint must never stop a collection run.
        sync = SyncClient(self._store)
        logging.info(
            f"[SC-35] sync {'enabled' if sync.enabled else 'disabled'}"
        )
        try:
            sync.pull()
        except Exception as exc:  # noqa: BLE001
            logging.warning(f"[SC-30] initial pull failed: {exc}")
        while consecutive_failures < max_restarts:
            self._match_recorded_this_cycle = False
            self._draw_this_cycle = False
            if max_matches is not None and recorded >= max_matches:
                logging.info(f"recorded {recorded} match(es), stopping as requested")
                return
            try:
                if self._run_one_match():
                    consecutive_failures = 0
                    # Count only cycles that actually WROTE a match. A draw returns True
                    # (the loop behaved correctly and must not be penalised) but records
                    # nothing, so counting it would let max_matches be satisfied by a run
                    # that collected no data.
                    if self._match_recorded_this_cycle:
                        recorded += 1
                        consecutive_draws = 0
                    elif self._draw_this_cycle:
                        # A draw is legitimate, but an ENDLESS run of them is not - that
                        # is what a mis-detection looks like from the loop's point of
                        # view, and because a draw resets consecutive_failures it can
                        # never trip the 3-strike stop on its own.
                        consecutive_draws += 1
                    # NO `continue` here. Skipping to the next iteration also skipped the
                    # recovery navigate_to_world() below, which is what left the game
                    # parked on the result screen and made the NEXT cycle fail in
                    # navigation instead of here.
                else:
                    consecutive_failures += 1
                    logging.warning(
                        f"[SC-20] no match recorded "
                        f"({consecutive_failures}/{max_restarts})"
                    )
            except Exception as exc:  # noqa: BLE001 - one bad match must not end the run
                # A match that was already RECORDED does not count as a failure, however
                # the cycle ended. _run_one_match writes the match before navigating back,
                # so an exception in that back-navigation used to burn a failure against a
                # perfectly good match - three unlucky exits could end the night despite
                # three matches collected.
                if self._match_recorded_this_cycle:
                    recorded += 1
                    consecutive_failures = 0
                    logging.warning(f"[SC-21] recorded, but the cycle ended badly: {exc}")
                else:
                    consecutive_failures += 1
                    logging.warning(
                        f"[SC-22] match failed ({consecutive_failures}/{max_restarts}): {exc}"
                    )

            # Push at the BOTTOM of the loop body - after the accounting above and
            # after the recovery below - so every path reaches it: a clean cycle,
            # a recorded-then-navigation-failed cycle ([SC-21]), and a failed
            # cycle that still has an older backlog. Hooking the normal return of
            # _run_one_match would skip exactly the matches most worth pushing.
            try:
                if sync.enabled:
                    sync.push()
                    sync.pull()
            except Exception as exc:  # noqa: BLE001 - sync must never cost a match
                logging.warning(f"[SC-30] sync failed, continuing: {exc}")

            # Checked out here, NOT inside the try above: a raise in that block is
            # caught by its own except and downgraded to a warning, which is precisely
            # the swallowing this guard exists to prevent.
            if consecutive_draws >= MAX_CONSECUTIVE_DRAWS:
                raise GameTimeoutError(
                    f"[SC-24] {consecutive_draws} draws in a row with nothing recorded - "
                    "the result screen is almost certainly being mis-read as the "
                    "overworld. Refusing to keep spectating blind."
                )

            # Recovery runs INSIDE protection. It is invoked in the state most likely to
            # make it raise, and an unguarded failure here would end the run on the first
            # bad cycle instead of the third.
            try:
                # Only navigate if we are NOT already where recovery would take us.
                # navigate_to_world() presses Back when nothing matches, and pressing
                # Back inside a live battle is what made the game ask "Exit battle?".
                # After a correctly-detected draw we are already on the overworld, so
                # this is a no-op; after an INCORRECT one it is the difference between
                # waiting harmlessly and tapping through someone's match.
                if self._is_in_overview():
                    logging.debug("[SC-26] already on the overworld - skipping recovery")
                else:
                    self.navigate_to_world()
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                logging.warning(
                    f"[SC-23] recovery failed ({consecutive_failures}/{max_restarts}): {exc}"
                )

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

        captured_at = datetime.now(UTC).isoformat(timespec="seconds")
        # Resolve by DATE first, OCR name only as a fallback. `theme` here is the
        # raw screen read and is kept for provenance, but it is not what the data
        # is filed under - a misread name must not be able to file a match against
        # the wrong balance patch, because matches are only comparable within a
        # theme.
        event_id, theme_id, theme_resolved_by = self._store.resolve_theme(
            captured_at, theme
        )

        match_id = self._store.record_match(
            MatchRecord(
                source="spectate_summary",
                captured_at=captured_at,
                theme=theme,
                event_id=event_id,
                theme_id=theme_id,
                theme_resolved_by=theme_resolved_by,
                outcome=read.winner,
                outcome_source="observed",
                left_player=read.left_player,
                right_player=read.right_player,
            )
        )

        slots: list[HeroSlot] = []
        for hero in read.heroes:
            # SHELVED. Long-press OCR is left in place but switched off: on device the
            # popup did not open reliably enough, and each attempt costs a full-frame OCR,
            # so a single match could take minutes and still come back unconfirmed.
            # Identification therefore rests on image matching alone for now, which scored
            # 0.81-0.95 across 54 heroes in nine captured matches.
            #
            # The consequence is recorded honestly rather than hidden: identified_by is
            # "image" for every hero, confirmed_sides stays empty, and because transform
            # learning is gated on longpress_ocr confirmation it simply never fires. No
            # unconfirmed data can be promoted to ground truth by accident.
            confirmed = (
                self._confirm_by_longpress(hero) if LONGPRESS_VERIFICATION else None
            )
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

        # The key can only be computed HERE, not at insert: the match row is
        # written before the summary is read, so the heroes and the outcome -
        # everything the key is made of - are not known until now.
        #
        # Incomplete matches deliberately keep natural_key NULL and are never
        # pushed. A half-read match with a key could claim identity over the good
        # version of the same match, because the first submission wins.
        left_slugs = [s_.hero_slug for s_ in slots
                      if s_.side == "left" and s_.hero_slug]
        right_slugs = [s_.hero_slug for s_ in slots
                       if s_.side == "right" and s_.hero_slug]
        if read.winner and is_complete(left_slugs, right_slugs, read.winner):
            self._store.set_natural_key(
                match_id,
                natural_key(read.winner, left_slugs, right_slugs, captured_at),
            )

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
                    timeout_message=f"[SC-07] {anchor} never appeared - skipping training capture",
                )
            except GameTimeoutError:
                # Not an error: the match may have been entered late, or the transition
                # may have been missed. Recording the outcome does not depend on this.
                return None

        if late:
            sleep(TRAINING_LATE_DELAY)
        frame = self.get_screenshot()
        return frame

    def _dismiss_popup(self) -> None:
        """Tap an empty area to close any open hero popup.

        POPUP_DISMISS_AT is deliberately not near a button. The previous value (540, 1750)
        sat on the green Back button, so dismissing could have left the summary entirely.
        """
        self.tap(POPUP_DISMISS_AT)
        sleep(0.5)

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

        # Baseline the screen with nothing open, so the diff below is meaningful.
        self._dismiss_popup()
        baseline = {
            b.text.strip()
            for b in self._ocr.detect_text_blocks(
                self.get_screenshot(), ConfidenceValue(0.5)
            )
        }

        for _ in range(LONGPRESS_ATTEMPTS):
            slug: str | None = None
            try:
                # Close anything already open BEFORE pressing. A popup left over from the
                # previous card would still be on screen and would be read as this card's
                # answer - a false positive that looks exactly like a correct one.
                self._dismiss_popup()
                self.hold(point, duration=LONGPRESS_SECONDS)
                sleep(1.0)
                frame = self.get_screenshot()
                # BASELINE DIFF. Everything already on the details screen before pressing
                # - background labels, stat numbers, player names, avatar fragments - is
                # by definition not popup text. Only what is NEW can be the popup, so the
                # hero name is looked for there and nowhere else.
                #
                # This is what makes strict matching possible, and it removes both failure
                # modes seen on device: a player name being read as a hero ("Silver Bull"
                # resolving to the hero "Silven"), and a background word colliding with a
                # second hero to trigger the ambiguity guard, which made Berial and Harak
                # return None despite being plainly on screen.
                blocks = self._ocr.detect_text_blocks(frame, ConfidenceValue(0.5))
                fresh = [
                    b.text.strip()
                    for b in blocks
                    if b.text.strip() not in baseline
                ]
                slug = resolve_hero_name_strict(fresh, self._solstice_cfg)
            except Exception as exc:  # noqa: BLE001 - one unreadable card must not end the match
                logging.warning(f"[SC-08] long-press confirm failed: {exc}")
            finally:
                # Dismiss on EVERY path, including failure - even if hold or
                # get_screenshot raised above. A popup left open covers the screen, so
                # the next long-press and the navigation that follows would act on the
                # wrong UI state - and that failure would look like a matching problem.
                self._dismiss_popup()
            if slug is not None:
                return slug
        return None
