"""AFK Journey Solstice Clash Mixin - Mode A, training and recording.

Spectates matches in a loop and records each outcome from the post-match summary, using
that OCR-confirmed ground truth to measure and tune identification on the draft and
prematch screens for Modes B and C.
"""

import itertools
import logging
import os as os_module
from pathlib import Path
from abc import ABC
from datetime import UTC, datetime
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
from ..services.solstice.draftlog import (
    PickRead,
    better,
    format_merged,
    format_pick,
    MAX_PICKS_PER_SIDE,
    merge_screens,
    newly_locked,
)
from ..services.solstice.details_screen import (
    SOLSTICE_CLASH_TITLE,
    details_signals,
    is_details_screen,
    load_replay_template,
)
from ..services.solstice.icons import IconLibrary
from ..services.solstice.matchkey import is_complete, natural_key
from ..services.solstice.naming import resolve_hero_name_strict
from ..services.solstice.odds import (
    MIN_LOCKED_FOR_ODDS,
    band_evidence,
    fit as fit_odds,
    format_odds,
    gate_reason,
    load_matches,
    predict as predict_odds,
)
from ..services.solstice import overlay
from ..services.solstice.paths import (
    draft_frame_dir,
    solstice_db_path,
    solstice_icon_dir,
)
from ..services.solstice.pools import read_pools, read_spectators
from ..services.solstice.ratings import read_ratings
from ..services.solstice.screens import (
    is_draft_screen,
    is_locked_screen,
    load_templates,
)
from ..services.solstice.store import (
    AuditRow,
    HeroSlot,
    MatchRecord,
    MatchStore,
    OddsSample,
)
from ..services.solstice.summary import CELL_TYPE, SummaryHero, read_summary
from ..services.solstice.vision import extract_cell, identify_with_pool
from ..services.solstice.sync import SyncClient
from ..services.solstice.tuning import (
    confirmed_sides,
    learn_if_improved,
    train_from_frame,
)
# is where those functions are defined. Importing them here would leave this module
# unimportable at the end of Task 8 and fail its green-suite gate.

# Resolved at call time, not import time: the first call seeds the user copy
# from the bundled one, and doing that during module import would run it on
# every command in the app rather than only when Solstice Clash is used.

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

# --- Mode B: passive collection while the user plays ------------------------
# Every one of these is read from the MODULE at use, never captured into a
# default argument, so a test can patch it.
POLL_SECONDS = 2.0
# Mode C watches a draft that lasts ~25s and resolves six picks in it, so 2s would miss
# picks outright and report them late in a batch. 0.4s is the smallest interval that
# still leaves the six-cell read (measured 1.49s against the full library on a real
# draft frame) room to run; the loop logs its measured cost so this stays evidence-led.
DRAFT_POLL_SECONDS = 0.4
# Measured on the committed fixtures with the bundled icon library:
#
#   spectate_draft.png     draft_pick 3/6 (best 0.98)   draft_locked_pick 1/6 (0.80)
#   spectate_prematch.png  prematch_pick 6/6 (0.99)     locked_pick 2/6 (0.80)
#   prematch_locked.png    locked_pick 6/6 (0.99)       prematch_pick 0/6 (0.88)
#
# So the geometries are not rivals for one screen - each belongs to a different screen,
# and reading the wrong one produces confident nonsense rather than nothing. Mode C
# spectates, so it uses the spectate geometries.
DRAFT_CELL_TYPES = ("draft_pick",)
LOCKED_CELL_TYPES = ("prematch_pick",)
# The locked screen animates in. Wait, then re-confirm we are still on it, and only
# then read - a read during the animation is what left the last slot unknown.
# 2s. A run failed its second confirmation while the locked screen was, by observation,
# still plainly up - so the miss was a transition or an animation frame, not brevity.
# Two seconds is long enough for the cards to arrive without lingering.
LOCKED_SETTLE_SECONDS = 2.0
# One frame can lie: it can catch a fade, a popup, or a stream artefact. A few looks,
# half a second apart, distinguish "not the locked screen" from "one bad frame" - which
# is the same lesson the draft-end detection already learned the hard way.
LOCKED_CONFIRM_LOOKS = 4
# Read the spectator count once this many picks are in: it costs ~305ms and grows
# through the draft, so late is both cheaper and more accurate.
SPECTATOR_READ_AFTER_PICKS = 4
LOCKED_LOOK_INTERVAL = 0.5
# Two confirmations in total: the detection that ends the draft watch, then ONE look
# this long afterwards. Kept short deliberately - the locked screen itself may not last
# long, so a longer settle risks missing it entirely. Raise toward 2s only if reads keep
# landing mid-animation.
# Diagnostic burst on the locked screen, enabled by ADB_SOLSTICE_LOCK_FRAMES.
LOCK_BURST_FRAMES = 8
LOCK_BURST_INTERVAL = 0.5
# Two budgets, because they bound different things. The first is how long to WAIT for a
# draft that may already be over - we can join mid-match. The second starts when the
# draft is actually SEEN and bounds the draft itself.
#
# One budget for both was the bug: waiting ate most of it, so it expired mid-draft and
# the watch left with 4 of 6 picks and no [SC-55] to explain why.
DRAFT_WATCH_TIMEOUT = 60.0
DRAFT_MAX_SECONDS = 180.0
# ~5 minutes. A heartbeat, not an on-stop summary: the GUI stop button is
# SIGTERM (__main__.py:352) and Python does not run finally blocks on SIGTERM,
# so anything deferred to exit never runs in the only way a user actually stops
# this mode.
HEARTBEAT_POLLS = 150
# ~30 seconds. A dead ADB connection makes get_screenshot raise on EVERY poll,
# and "log it and continue" would spin silently for hours while the user plays a
# whole evening believing they are collecting. This mode's characteristic
# failure is silence, so it must be loud about the one failure it cannot see
# past.
MAX_CONSECUTIVE_SCREENSHOT_FAILURES = 15
# ~1 minute of a screen showing one detection signal but not the other. The
# signals are ANDed, so a game update breaking either one stops collection
# silently; the redundancy is only real if something notices.
MAX_SIGNAL_DISAGREEMENT = 30

# Enumerated log codes for this mode. Every failure path gets its OWN code: a
# shared code means a log line tells you something went wrong but not WHICH
# thing, which is the difference between reading the answer and grepping the
# whole file for it.
#
#   SC-30  sync call failed (shared with Mode A)
#   SC-35  sync status / summary (shared with Mode A)
#   SC-40  match recorded                              info
#   SC-41  already recorded - the natural_key backstop  debug   benign
#   SC-42  wrong screen resolution, refused to run      raises
#   SC-43  periodic heartbeat                           info
#   SC-45  device connection lost, collection stopped   raises
#   SC-46  detection signals disagree, possible rot     warning once
#   SC-47  read_summary RAISED on a confirmed screen    warning
#   SC-48  read completed but was INCOMPLETE            debug
#
# SC-47 and SC-48 are deliberately distinct: one means the parser broke, the
# other means the screen was mid-animation and the next poll will be fine. They
# were one code until an audit caught that a log could not tell them apart.

# Mode A's bounded wait for the details screen after tapping the chart button.
DETAILS_POLL_DELAY = 0.5
DETAILS_TIMEOUT = 15.0

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
            label="WDB: Sync Solstice Clash Data",
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
        store = MatchStore(solstice_db_path())
        sync = SyncClient(store)
        if not sync.enabled:
            logging.warning("[SC-36] sync is disabled - nothing to do")
            return
        # Themes first: a match pushed before the window is known is filed under the
        # default on the SERVER too, and stays that way.
        sync.pull_themes()
        pushed, duplicate, rejected = sync.push()
        pulled = sync.pull()
        logging.info(
            f"[SC-35] sync: pushed {pushed}, duplicate {duplicate}, "
            f"rejected {rejected}, pulled {pulled}"
        )

    @register_command(
        name="SolsticeClashInstallOverlay",
        gui=GUIMetadata(
            label="WDB: Install Odds Overlay",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Install the in-game odds bubble and grant it permission",
        ),
    )
    def install_odds_overlay(self) -> None:
        """Install and grant, without starting a collection run.

        A collection run does this itself. This exists so that a thing installed on
        someone's device is visible where they can see it and remove it, rather than
        being a side effect of something else.
        """
        self.start_up(device_streaming=False)
        self._overlay_prepare(install_if_absent=True)
        if not getattr(self, "_overlay_ok", False):
            logging.warning("[SC-82] the odds overlay could not be installed")

    @register_command(
        name="SolsticeClashUninstallOverlay",
        gui=GUIMetadata(
            label="WDB: Uninstall Odds Overlay",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Remove the in-game odds bubble from the device",
        ),
    )
    def uninstall_odds_overlay(self) -> None:
        """Remove it. Nobody should need an adb incantation to undo what a mode did."""
        self.start_up(device_streaming=False)
        try:
            # Hide and stop before removing. Android kills the process on uninstall and
            # the window manager reaps its windows with it, so a visible overlay cannot
            # be stranded - but taking it down first means the removal never depends on
            # that, and the screen is clean a moment earlier either way.
            overlay.safe_shell(self._device.d, overlay.clear_command())
            overlay.safe_shell(self._device.d, overlay.stop_command())
            self._device.d.d.uninstall(overlay.PACKAGE)
            logging.info("[SC-82] odds overlay removed")
        except Exception as exc:  # noqa: BLE001
            logging.warning(f"[SC-82] could not remove the overlay: {exc}")

    @register_command(
        name="SolsticeClashCollect",
        gui=GUIMetadata(
            label="WDB: Collect Solstice Clash Data",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Spectate Solstice Clash matches and record outcomes for analysis",
        ),
    )
    def collect_solstice_clash(self, max_matches: int | None = None) -> None:
        """Spectate matches in a loop, logging the draft and recording each one.

        One mode, not two. Watching a draft to log its picks and watching one to record
        its result are the same act - the bot navigates, spectates and records either
        way - so a separate "log the picks" mode was two names for one thing, and the
        odds display this feeds has no reason to be optional either.

        Screencap rather than the H264 stream: streaming degrades OCR of stylised text
        and OCR is this mode's ground truth for the summary. The draft watch turns the
        stream on for itself and off again, where speed matters and OCR does not.
        """
        self.start_up(device_streaming=False)
        # Before anything is recorded: a match stored under the wrong theme is a match no
        # model will use, and the window is knowable only from the pool.
        self._sync_theme_windows()
        self.navigate_to_world()
        self._overlay_prepare()
        # ADB_SOLSTICE_MAX_MATCHES bounds a run without touching the CLI, which does not
        # expose this argument. Testing a change otherwise meant starting an unbounded
        # run, watching minutes of silence between drafts, and killing it by hand.
        if max_matches is None:
            bound = os_module.environ.get("ADB_SOLSTICE_MAX_MATCHES")
            max_matches = int(bound) if bound and bound.isdigit() else None
        logging.info(
            "[SC-50] Solstice Clash: spectating, logging picks, recording"
            + (f" - stopping after {max_matches} match(es)" if max_matches else "")
        )
        self._collect_forever(max_matches=max_matches)

    @register_command(
        name="SolsticeClashCollectCompete",
        gui=GUIMetadata(
            label="WDB: Collect While Playing (Compete)",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip=(
                "Watch for post-match details screens while YOU play and record "
                "them. Never taps, swipes or navigates."
            ),
        ),
    )
    def collect_while_playing(self, max_polls: int | None = None) -> None:
        """Record every Solstice Clash details screen the user opens.

        NEVER touches the device. The user is in a live ranked match; a stray tap
        could lose it. get_screenshot() is the only device call made.

        Deliberately does NOT call start_up(): that runs _set_device_resolution()
        and can call start_game(), i.e. resize the display or launch the app
        under a running match. The resolution is checked and refused instead.

        `max_polls` exists for the tests; production passes None and runs until
        stopped.
        """
        replay = load_replay_template(self.template_dir)
        # Same reason as the spectate mode: a match recorded under the wrong theme is a
        # match no model will use, and this mode records real ranked matches.
        self._sync_theme_windows()
        sync = SyncClient(self._store)
        logging.info(
            f"[SC-35] sync {'enabled' if sync.enabled else 'disabled'}; "
            f"watching for details screens - play normally, this never taps"
        )

        # armed == "ready to record". Set False after a record and back to True
        # only when the screen goes away, so one viewing yields one row however
        # long it stays up. natural_key is the second layer, for a REOPENED
        # screen, which this one cannot see.
        armed = True
        recorded = skipped = 0
        screenshot_failures = 0
        disagreements = 0
        warned_disagreement = False
        checked_resolution = False

        for poll in itertools.count():
            if max_polls is not None and poll >= max_polls:
                break
            if poll:
                time_module.sleep(POLL_SECONDS)

            try:
                frame = self.get_screenshot()
            except Exception as exc:
                screenshot_failures += 1
                logging.debug(f"[SC-45] screenshot failed: {exc}")
                if screenshot_failures >= MAX_CONSECUTIVE_SCREENSHOT_FAILURES:
                    raise GameActionFailedError(
                        f"[SC-45] {screenshot_failures} consecutive screenshot "
                        f"failures - the device connection is gone. Collection "
                        f"has stopped; nothing was being recorded."
                    ) from exc
                continue
            screenshot_failures = 0

            # On the FIRST frame we actually get, not via a separate screenshot:
            # a dedicated capture would consume a poll's worth of screen and
            # shift every test's frame queue by one.
            if not checked_resolution:
                checked_resolution = True
                if frame.shape[:2] != (1920, 1080):
                    raise GameActionFailedError(
                        f"[SC-42] expected a 1080x1920 screen, got "
                        f"{frame.shape[1]}x{frame.shape[0]} - this mode cannot "
                        f"resize the display because you may be in a live match"
                    )

            if poll and poll % HEARTBEAT_POLLS == 0:
                logging.info(
                    f"[SC-43] {poll} polls, {recorded} recorded, {skipped} skipped"
                )

            signals = details_signals(
                frame, replay, self._ocr, header_title=SOLSTICE_CLASH_TITLE
            )

            # One core signal without the other is the shape of a game update
            # breaking detection. The header is excluded: header=False with both
            # others true just means another mode's details screen, which is the
            # guard working, not rot.
            if signals.template != signals.labels:
                disagreements += 1
                if disagreements >= MAX_SIGNAL_DISAGREEMENT and not warned_disagreement:
                    warned_disagreement = True
                    logging.warning(
                        f"[SC-46] the Replay template and the Ally/Enemy labels "
                        f"have disagreed for {disagreements} polls "
                        f"(template={signals.template}, labels={signals.labels}). "
                        f"One detection signal may have broken in a game update "
                        f"- collection may be silently degraded."
                    )
            else:
                disagreements = 0

            if not signals.confirmed:
                armed = True  # screen gone - the next one is a new match
                continue
            if not armed:
                continue

            if self._record_compete_summary(frame):
                recorded += 1
                armed = False
                # Push right after the record, NOT on exit - see HEARTBEAT_POLLS
                # on why nothing may be deferred to exit. A record only happens
                # between matches, so this is never network noise during play,
                # and the wrapper means a dead endpoint cannot cost a match.
                try:
                    if sync.enabled:
                        sync.push()
                        sync.pull()
                except Exception as exc:
                    logging.warning(f"[SC-30] sync failed, continuing: {exc}")
            else:
                skipped += 1
                # Stay ARMED: the screen is still up and the next poll gets a
                # cleaner read of the same match.

    def _wait_for_details_screen(self) -> np.ndarray:
        """Block until the details screen is up, and return THAT frame.

        No header title is required: Mode A navigated to Solstice Clash itself,
        so it already knows which event it is in. Mode B passes one because it
        watches whatever the user happens to be playing.
        """
        replay = load_replay_template(self.template_dir)

        def _details_ready() -> np.ndarray:
            frame = self.get_screenshot()
            # _ocr is a PROPERTY - self._ocr() would raise TypeError.
            if is_details_screen(frame, replay, self._ocr):
                return frame
            # MUST raise, not return False: _execute_or_timeout retries only on
            # _UndesiredResultError and treats ANY returned value - including
            # False - as success, which would make this wait a silent no-op.
            raise _UndesiredResultError()

        return self._execute_or_timeout(
            _details_ready,
            delay=DETAILS_POLL_DELAY,
            timeout=DETAILS_TIMEOUT,
            timeout_message=(
                "[SC-44] details screen never appeared after tapping the chart button"
            ),
        )

    def _record_compete_summary(self, frame: np.ndarray) -> bool:
        """Record one details screen the user played. Returns False if skipped.

        Deliberately leaner than _record_summary: no identification_audit rows
        and no training. Audit rows are confirmation evidence for cell tuning and
        this mode cannot long-press to confirm anything, and there is no draft or
        prematch frame to train from - the mode never saw those screens.
        """
        try:
            read = read_summary(
                frame, self._solstice_cfg, self._solstice_library, self._ocr
            )
        except Exception as exc:
            logging.warning(
                f"[SC-47] read_summary raised on a confirmed details "
                f"screen, skipping this poll: {exc!r}"
            )
            return False

        left = [h.slug for h in read.heroes if h.side == "left" and h.slug]
        right = [h.slug for h in read.heroes if h.side == "right" and h.slug]

        # is_complete, NOT len(read.heroes): read_summary returns one SummaryHero
        # per configured cell ALWAYS, with slug=None on a failed identification,
        # so a length check passes on a half-rendered screen. It also rejects a
        # winner that is not left/right, covering the unresolved-banner case.
        if not is_complete(left, right, read.winner or ""):
            logging.debug(
                f"[SC-48] incomplete read: {len(left)}+{len(right)} of 3+3 heroes "
                f"identified, winner={read.winner!r} - staying armed for a "
                f"cleaner read"
            )
            return False

        captured_at = datetime.now(UTC).isoformat(timespec="seconds")
        key = natural_key(read.winner, left, right, captured_at)
        if self._store.match_by_natural_key(key) is not None:
            logging.debug(
                f"[SC-41] already recorded ({key[:19]}...), skipping - this "
                f"is the natural_key backstop, not a failure"
            )
            return False

        # Theme by DATE. The details screen never shows it, so unlike Mode A
        # there is no screen read to pass in and nothing to misread.
        event_id, theme_id, theme_resolved_by = self._store.resolve_theme(
            captured_at, None
        )

        match_id = self._store.record_match(
            MatchRecord(
                source="compete_summary",
                captured_at=captured_at,
                theme=None,
                event_id=event_id,
                theme_id=theme_id,
                theme_resolved_by=theme_resolved_by,
                outcome=read.winner,
                outcome_source="observed",
                left_player=read.left_player,
                right_player=read.right_player,
            )
        )
        self._store.record_heroes(
            match_id,
            [
                HeroSlot(
                    side=h.side,
                    slot=h.slot,
                    hero_slug=h.slug,
                    art_ref=h.art_ref,
                    status="identified" if h.slug else "unknown",
                    score=h.score,
                    margin=h.margin,
                    cell_type=CELL_TYPE,
                    identified_by="image",
                    stat_sword=h.stats.sword,
                    stat_heart=h.stats.heart,
                    stat_shield=h.stats.shield,
                )
                for h in read.heroes
            ],
        )
        self._store.set_natural_key(match_id, key)

        logging.info(
            f"[SC-40] recorded match {match_id}: {read.winner} won, "
            f"theme_id={theme_id} ({theme_resolved_by})"
        )
        return True

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
        # Poll the draft rather than grabbing one frame at the end of it. The chat drag
        # happens inside, the moment the draft is confirmed, and the last draft frame
        # comes back so everything downstream is unchanged.
        self._left_on_locked_screen = False
        draft_frame = self._watch_draft()

        # No wait for the "Last chance!" banner: it flashes in the final seconds of the
        # DRAFT, so by the time the picks are locked it has gone - waiting for it burned
        # up to 90s and then returned None, skipping the locked read entirely. The draft
        # watch already exits ON the locked screen, and the next marker anyone cares
        # about is the result screen.
        prematch_frame = None

        # The locked-picks screen, read from its own geometry. It is the last look at
        # the six before the battle, and the only one where nothing can still change.
        confirmed = False
        # Gated on HOW the watch exited, not on whether it returned a frame. It returns
        # one either way, so this used to announce the locked screen immediately after
        # SC-62 reported that it never appeared - and then read six cells off a draft
        # frame as though they were locked.
        if draft_frame is not None and self._left_on_locked_screen:
            logging.info("[SC-58] locked picks screen")

            burst_dir = os_module.environ.get("ADB_SOLSTICE_LOCK_FRAMES")
            if burst_dir:
                # Frames to disk, nothing else. Matching each one cost a full read per
                # frame and told us nothing the real read does not - the pixels are what
                # we want to look at. Diagnostics must also never cost a match: this
                # block has already raised twice, and each time the collect loop counted
                # a FAILED match and navigated back.
                try:
                    target = Path(burst_dir)
                    target.mkdir(parents=True, exist_ok=True)
                    for shot in range(LOCK_BURST_FRAMES):
                        cv2.imwrite(
                            str(target / f"lock_{shot:02d}.png"), self.get_screenshot()
                        )
                        time_module.sleep(LOCK_BURST_INTERVAL)
                    logging.info(f"[SC-65] saved {LOCK_BURST_FRAMES} frames to {target}")
                except Exception as exc:  # noqa: BLE001 - never fatal
                    logging.warning(f"[SC-67] lock-frame capture failed: {exc}")

            # CONFIRMATION TWO. The first was the detection that ended the draft
            # watch; this is one look 1.5s later, which is what proves the screen has
            # settled rather than merely appeared. The screen animates in, and a read
            # taken mid-animation is what left the last slot unknown while a frame
            # captured by hand moments later read 6/6.
            #
            # If it does not confirm, the locked read is abandoned for this match. A
            # guess from an unsettled screen is worse than no reading at all.
            time_module.sleep(LOCKED_SETTLE_SECONDS)
            settled_frame = None
            for look in range(LOCKED_CONFIRM_LOOKS):
                try:
                    candidate = self.get_screenshot()
                except Exception as exc:  # noqa: BLE001
                    logging.debug(f"[SC-45] settle screenshot failed: {exc}")
                    time_module.sleep(LOCKED_LOOK_INTERVAL)
                    continue
                if is_locked_screen(candidate, self._screens):
                    settled_frame = candidate
                    break
                logging.debug(f"[SC-68] look {look + 1} was not the locked screen")
                time_module.sleep(LOCKED_LOOK_INTERVAL)

            if settled_frame is None:
                # Fall back to the frame that ENDED the draft watch. It already
                # confirmed as the locked screen, so the risk is that it is early in the
                # animation - which costs a hero or two - where discarding it costs the
                # sixth pick and the final odds outright. A live run lost exactly that.
                fallback = getattr(self, "_first_locked_frame", None)
                if fallback is not None:
                    logging.warning(
                        f"[SC-68] locked screen did not re-confirm after "
                        f"{LOCKED_SETTLE_SECONDS:.1f}s - reading the frame that ended "
                        f"the draft instead, which did confirm"
                    )
                    prematch_frame = fallback
                    confirmed = True
                else:
                    logging.warning(
                        f"[SC-68] locked screen did not confirm after "
                        f"{LOCKED_SETTLE_SECONDS:.1f}s and no earlier frame was kept - "
                        f"not reading it. The draft's own picks still stand."
                    )
                    self._last_draft_reads = []
                    confirmed = False
            else:
                prematch_frame = settled_frame
                confirmed = True

        if confirmed:
            # Which side is still short, from what the draft managed to read. That
            # side is read FIRST, and within it the last slot first: those are the
            # picks that landed latest and are the only ones still missing.
            draft_by_side: dict[str, int] = {"left": 0, "right": 0}
            for read in getattr(self, "_last_draft_reads", []):
                if read.identified and read.side in draft_by_side:
                    draft_by_side[read.side] += 1
            incomplete = tuple(
                side
                for side, found in sorted(draft_by_side.items(), key=lambda kv: kv[1])
                if found < MAX_PICKS_PER_SIDE
            )
            locked = self._read_draft_picks(
                prematch_frame,
                None,
                cell_types=LOCKED_CELL_TYPES,
                sides_first=incomplete,
                reverse_slots=True,
            )
            # Merge on side and position, never on the raw slot: the draft numbers 1-6
            # across both teams and the locked screen numbers 1-3 per side, so keying on
            # slot mixes the sides. A live run printed four heroes on Blue and two on
            # Red because of exactly that.
            draft_reads = getattr(self, "_last_draft_reads", [])
            # What the LOCKED screen itself read, before any merging. Without this the
            # merged line is unattributable: five names that all came from the draft
            # look identical to five the locked screen read, and a duplicate hero
            # collapsing into a `?` looks like a cell that could not be read.
            detail = " ".join(
                f"{r.side[0]}{r.slot}:{r.slug or '-'}({r.score:.3f})"
                for r in sorted(locked, key=lambda r: (r.side, r.slot))
            )
            logging.info(f"[SC-69] locked read: {detail}")
            logging.info(
                f"[SC-71] read order was {' '.join(f'{r.side[0]}{r.slot}' for r in locked)}"
                f" - the missing side's last slot first"
            )
            for side in ("left", "right"):
                slugs = [r.slug for r in locked if r.side == side and r.identified]
                if len(slugs) != len(set(slugs)):
                    logging.warning(
                        f"[SC-70] the same hero was read twice on {side} - one of those "
                        f"cells is wrong, and the dedupe turns it into a `?`"
                    )

            merged = merge_screens(draft_reads, locked)
            filled = sum(1 for r in merged if r.identified)
            from_locked = {r.slug for r in locked if r.identified}
            from_draft = sum(
                1 for r in merged if r.identified and r.slug not in from_locked
            )
            logging.info(format_merged(merged))

            # The prediction as it stood with the final six, BEFORE the fight. Recorded
            # against the match so it can be scored later: a match called at 80% and
            # lost is the only evidence that shows where the logic is wrong, and it
            # cannot be reconstructed afterwards because the model moves as data arrives.
            # The FINAL odds, with all six known. Shown as well as recorded: the block
            # during the draft is a running estimate on a partial comp, and this is the
            # one a person would actually act on - it was being stored and never
            # displayed, which meant the best number never reached the screen.
            self._pending_prediction = self._final_prediction(merged)
            self._log_final_odds(merged)
            # Name the slots that stayed unknown, with their scores. Without this a `?`
            # is unattributable after the fact: covered by the chat widget, a card that
            # had not finished animating in, and a geometry drift all look identical in
            # the log.
            unread = [r for r in locked if not r.identified]
            if unread:
                detail = " ".join(
                    f"{r.side[0]}{r.slot}:{r.score:.3f}/{r.margin:.3f}"
                    for r in sorted(unread, key=lambda r: (r.side, r.slot))
                )
                logging.warning(f"[SC-64] locked screen could not read: {detail}")
            if from_draft:
                logging.info(
                    f"[SC-59] {filled}/6 identified, {from_draft} recovered from the "
                    f"draft screen"
                )
            self._last_draft_reads = []

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
        # The locked screen is still up when this wait begins - it IS the last-chance
        # betting countdown - so the fight has not started yet and the number is still
        # worth showing. The fight starts when that screen goes away, which is the only
        # honest signal for "betting is closed": seen, then gone.
        locked_seen = False

        def _match_end() -> TemplateMatchResult | None:
            """Return a result-screen match, None for a confirmed draw, else poll on."""
            nonlocal overworld_seen, locked_seen
            frame = self.get_screenshot()

            # Hiding earlier - when this wait began - took the bubble down while betting
            # was still open, which is the one moment it exists for.
            try:
                screens = self._screens
                if is_locked_screen(frame, screens):
                    locked_seen = True
                elif locked_seen:
                    locked_seen = False
                    self._overlay_hide()
            except Exception as exc:  # noqa: BLE001 - a display is never worth a match
                logging.debug(f"[SC-81] could not track the locked screen: {exc}")

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

        try:
            found = self._execute_or_timeout(
                _match_end,
                delay=RESULT_POLL_DELAY,
                timeout=MATCH_TIMEOUT,
                timeout_message=(
                    "[SC-03] match did not end in time: no result screen appeared and "
                    "we never settled on the overworld"
                ),
            )
        except GameTimeoutError:
            # Say WHAT was on screen, not just that nothing matched. [SC-03] costs the
            # outcome, burns a retry, and cascades: recovery taps back repeatedly and
            # the framework ends up restarting the game. Chasing it without evidence
            # means guessing which of three detectors drifted - the two result templates
            # or the overworld check - which is exactly how the draft anchor wasted a
            # day. Frames go to ADB_SOLSTICE_FAIL_FRAMES when set.
            self._report_match_end_failure()
            raise
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
        # Replaces sleep(2)-and-hope with a bounded wait. If the tap missed or
        # the transition was slow, read_summary used to parse whatever was on
        # screen. Scoped honestly: this would NOT have caught the live-battle
        # read-as-a-draw bug, which happened in match-end detection before this
        # point. It prevents the adjacent failure - recording garbage parsed
        # from a screen that is not the details screen.
        frame = self._wait_for_details_screen()

        self._record_summary(draft_frame, prematch_frame, theme, frame=frame)
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
        logging.info(f"[SC-35] sync {'enabled' if sync.enabled else 'disabled'}")
        try:
            sync.pull()
        except Exception as exc:
            logging.warning(f"[SC-30] initial pull failed: {exc}")
        while consecutive_failures < max_restarts:
            self._match_recorded_this_cycle = False
            self._draw_this_cycle = False
            if max_matches is not None and recorded >= max_matches:
                logging.info(f"recorded {recorded} match(es), stopping as requested")
                self._overlay_stop()
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
            except Exception as exc:
                # A match that was already RECORDED does not count as a failure, however
                # the cycle ended. _run_one_match writes the match before navigating back,
                # so an exception in that back-navigation used to burn a failure against a
                # perfectly good match - three unlucky exits could end the night despite
                # three matches collected.
                if self._match_recorded_this_cycle:
                    recorded += 1
                    consecutive_failures = 0
                    logging.warning(
                        f"[SC-21] recorded, but the cycle ended badly: {exc}"
                    )
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
            except Exception as exc:
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
                    logging.debug(
                        "[SC-26] already on the overworld - skipping recovery"
                    )
                else:
                    self.navigate_to_world()
            except Exception as exc:
                consecutive_failures += 1
                logging.warning(
                    f"[SC-23] recovery failed ({consecutive_failures}/{max_restarts}): {exc}"
                )

        raise GameTimeoutError(
            f"stopping: {max_restarts} consecutive cycles recorded no match"
        )

    def _record_summary(
        self,
        draft_frame,
        prematch_frame,
        theme: str | None,
        frame: np.ndarray | None = None,
    ) -> None:
        """Read the summary, record the match, and audit every identification.

        `theme` is read during navigation (the summary screen does NOT show it) and
        passed in, so a match cannot be recorded against the wrong balance epoch.

        `frame` is the capture already CONFIRMED to be the details screen.
        Re-capturing here would reintroduce the race the confirmation exists to
        close, because the screen can be dismissed between the check and the
        second capture. Defaulted so every existing caller and test is unchanged.
        """
        if frame is None:
            frame = self.get_screenshot()
        read = read_summary(
            frame, self._solstice_cfg, self._solstice_library, self._ocr
        )

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
                left_rating=getattr(self, "_draft_ratings", (None, None))[0],
                right_rating=getattr(self, "_draft_ratings", (None, None))[1],
            )
        )
        self._claim_draft_frame(match_id)
        # The last market reading before the picks locked, stored against the match.
        # `record_odds` and the table it writes to have existed since the schema was
        # written and were never used - the pools were visible on screen the whole time.
        sample = getattr(self, "_pool_read", None)
        if sample is not None and match_id:
            try:
                self._store.record_odds(
                    match_id,
                    OddsSample(
                        sampled_at=datetime.now(UTC)
                        .isoformat(timespec="seconds")
                        .replace("+00:00", "Z"),
                        left_pool=sample.left_pool,
                        right_pool=sample.right_pool,
                        left_odds=sample.left_odds,
                        right_odds=sample.right_odds,
                        spectators=getattr(self, "_spectators", None),
                    ),
                )
                crowd = sample.crowd_probability
                logging.info(
                    f"[SC-78] market recorded: {crowd * 100:.0f}% left"
                    f" from {(sample.left_pool or 0) + (sample.right_pool or 0)} staked"
                    if crowd is not None
                    else "[SC-78] market recorded"
                )
            except Exception as exc:  # noqa: BLE001 - never worth a match
                logging.warning(f"[SC-78] market could not be recorded: {exc}")
        self._pool_read = None
        self._spectators = None

        pending = getattr(self, "_pending_prediction", None)
        if pending is not None and match_id:
            p_left, source, locked = pending
            self._store.record_prediction(
                match_id,
                p_left,
                source,
                locked,
                datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            logging.info(
                f"[SC-75] recorded prediction {p_left * 100:.0f}% left ({source})"
            )
        self._pending_prediction = None
        self._draft_ratings = (None, None)
        self._first_locked_frame = None

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
                    frame_path=None,
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
        left_slugs = [
            s_.hero_slug for s_ in slots if s_.side == "left" and s_.hero_slug
        ]
        right_slugs = [
            s_.hero_slug for s_ in slots if s_.side == "right" and s_.hero_slug
        ]
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
                store=self._store,
                cfg=self._solstice_cfg,
                library=self._solstice_library,
                frame=frame_img,
                screen_slug=screen_slug,
                cell_type=cell_type,
                confirmed_by_side=confirmed_by_side,
                frame_path="",
                match_id=match_id,
            )
            # deduced/set_consistent are MEASUREMENTS, never confirmation - see
            # train_from_frame's docstring. Logged only, never used to learn a transform.
            logging.info(
                f"{screen_slug}: recorded {result.written} rows, "
                f"{result.deduced} deduced by elimination, "
                f"{result.set_consistent} set-consistent"
            )

    def _watch_draft(self) -> np.ndarray | None:
        """Poll the draft, logging each pick as it locks. Returns the last draft frame.

        Replaces the single late capture for Mode C, and returns a frame the existing
        training path can still use, so nothing downstream changes.

        Bounded by the draft's own length: it ends when the draft screen goes away, and
        gives up after DRAFT_WATCH_TIMEOUT so a mis-detected screen cannot park the
        collection loop here forever.
        """
        # The H264 stream, for the draft only: screencap costs ~3-4s per read against
        # 1.5s for the matching itself, so the last picks land after our final look.
        #
        # Guarded, because the failure mode is destructive rather than slow. If no frame
        # arrives, the framework falls back to chunked `screenrecord --time-limit=1` -
        # the exact mode this fork's Waydroid patch removes, because it churns the
        # encoder until it stops producing frames. On 2026-07-27 that froze the
        # gamescope viewer, with a second client (the installed app) holding the only
        # recorder. So we give the stream 3 seconds, which is INSIDE the framework's own
        # 4-second fallback timer, and stop it ourselves if nothing comes.
        self._start_device_streaming(True)
        stream = getattr(self, "_stream", None)
        if stream is not None:
            streaming = False
            for _ in range(30):
                if stream.get_latest_frame() is not None:
                    streaming = True
                    break
                time_module.sleep(0.1)
            if not streaming:
                logging.warning(
                    "[SC-60] no stream frame in 3s - stopping before the chunked "
                    "fallback can churn the encoder; reading with screencap instead"
                )
                self.stop_stream()
            else:
                logging.info("[SC-61] device stream up - reads should be ~2s faster")

        screens = self._screens
        seen: dict[int, str] = {}
        settled: dict[int, PickRead] = {}
        last_frame = None
        last_reads: list[PickRead] = []
        slowest = 0.0
        deadline = time_module.monotonic() + DRAFT_WATCH_TIMEOUT
        saw_draft = False
        last_draft_frame = None

        while time_module.monotonic() < deadline:
            started = time_module.perf_counter()
            try:
                frame = self.get_screenshot()
            except Exception as exc:  # noqa: BLE001 - a lost frame is not fatal here
                logging.debug(f"[SC-45] screenshot failed during the draft: {exc}")
                time_module.sleep(DRAFT_POLL_SECONDS)
                continue

            # Two signals, not one label. The old "Current Theme:" anchor is a small
            # text crop that an overlay or animation hides for a frame, and one such
            # frame read as "draft over" - ending the watch ~10s early and capping every
            # run at four of six picks.
            on_draft = is_draft_screen(frame, screens)
            if on_draft:
                last_draft_frame = frame
            if not saw_draft and on_draft:
                logging.info("[SC-54] draft screen")
                self._overlay_hide()

            if not on_draft:
                # The ONLY normal exit is the next screen arriving. Absence of the draft
                # is not an ending: between the last pick and the locked screen there
                # are several frames that are neither, and leaving during that gap is
                # how run 7 lost the sixth pick. Nothing re-enters a draft on its own,
                # so waiting through the gap can only help.
                if saw_draft and is_locked_screen(frame, screens):
                    logging.info("[SC-55] draft over - locked screen is up")
                    # The ONLY exit that means the locked screen is actually up. The
                    # timeout path returns a frame too, so a caller that infers "locked
                    # screen" from a non-None return logs it after SC-62 has just said
                    # the opposite - and then reads six cells off a draft frame.
                    self._left_on_locked_screen = True
                    # Kept as the fallback for the read: this frame HAS confirmed as the
                    # locked screen, so if the settle re-check later disagrees, reading
                    # it beats reading nothing.
                    self._first_locked_frame = frame
                    self._save_draft_frame(last_draft_frame)
                    break
                time_module.sleep(DRAFT_POLL_SECONDS)
                continue

            if not saw_draft:
                # The clock restarts here: everything before this was waiting.
                deadline = time_module.monotonic() + DRAFT_MAX_SECONDS
                # Now that the draft is CONFIRMED on screen, clear the chat widget off
                # the cards. By default the bubble sits at y970 and the emoji at y1075,
                # both inside the card band, and the emoji overlaps the last red card -
                # measured at 0.10-0.14 of match score on that cell.
                self._move_chat_out_of_the_way()
                # Re-read after the drag: the frame in hand was captured before it.
                try:
                    frame = self.get_screenshot()
                except Exception as exc:  # noqa: BLE001
                    logging.debug(f"[SC-45] screenshot after the chat drag: {exc}")

                # Fit ONCE per draft, here. The fit takes ~2s over a few hundred
                # matches and nothing it learns changes while the picks land, so doing
                # it per pick would spend the draft re-deriving the same numbers.
                fitted, theme_matches = self._fit_odds()

                # The ladder ratings, read HERE because this is where they are on screen
                # while betting is open. Codex's estimate: a 100-150 point gap plausibly
                # moves win probability 5-15 points, which at this sample size is worth
                # more than all 93 hero strengths combined - 277 matches over 93 heroes
                # is 3 matches per parameter.
                try:
                    left_rating, right_rating = read_ratings(frame, self._ocr, "draft")
                    if left_rating and right_rating:
                        logging.info(
                            f"[SC-74] ratings {left_rating} vs {right_rating} "
                            f"(gap {left_rating - right_rating:+d})"
                        )
                    self._draft_ratings = (left_rating, right_rating)
                except Exception as exc:  # noqa: BLE001 - never worth a match
                    logging.debug(f"[SC-74] ratings unreadable: {exc}")
                    self._draft_ratings = (None, None)
            saw_draft = True
            last_frame = frame
            # No pool read. Narrowing six identifications by first identifying twenty
            # cards is a losing trade: measured live it cost 7 of the ~10 seconds the
            # draft was on screen and returned 4/20 anyway, leaving ONE read of the
            # picks - which landed before any had appeared, so the draft logged nothing
            # while the geometry was fine all along. Six cells against the full library
            # measure 1.49s on a real draft frame, which fits the window several times
            # over.
            fresh = self._read_draft_picks(frame, None, skip_slots=set(settled))
            newly = newly_locked(seen, fresh)
            if newly:
                # Sample the betting market when a pick LANDS, not every poll: the read
                # costs ~436ms against a ~20s draft where the six-cell hero read already
                # takes ~2s. The pools move on picks anyway, and the value that matters
                # is the last one before lock - so each success replaces the previous
                # and an occluded frame simply leaves the earlier reading standing.
                try:
                    sample = read_pools(frame, self._ocr)
                    if sample.crowd_probability is not None:
                        self._pool_read = sample
                        logging.info(
                            f"[SC-77] crowd {sample.crowd_probability * 100:.0f}% left"
                            f" (pools {sample.left_pool}/{sample.right_pool})"
                        )
                    # The spectator count, once, late in the draft. It reads only on
                    # THIS screen - on the locked screen it is greyed out and OCR finds
                    # nothing - and it grows as the draft runs, so a late reading is the
                    # honest one. It matters because it, not the pool size, says how
                    # many people are behind the split: under about 50 the pools are a
                    # handful of bettors.
                    if len(settled) >= SPECTATOR_READ_AFTER_PICKS:
                        # Re-read on every later pick, not once: the count is still
                        # climbing while the draft runs, and the number that matters is
                        # the one at lock - same reasoning as the pools, which are also
                        # replaced rather than averaged.
                        count = read_spectators(frame, self._ocr)
                        if count is not None:
                            self._spectators = count
                            logging.info(f"[SC-80] {count} spectators")
                except Exception as exc:  # noqa: BLE001 - never worth a match
                    logging.debug(f"[SC-77] pool read failed: {exc}")
            for pick in newly:
                logging.info(format_pick(pick))
                settled[pick.slot] = pick

            # Odds after every pick from the fourth onward - the point at which enough
            # of the draft exists for the number to mean anything, and while betting is
            # still open. Prediction itself is microseconds; the fit already happened.
            if newly and len(settled) >= MIN_LOCKED_FOR_ODDS:
                self._log_odds(fitted, settled, theme_matches)
            # Everything the draft knows: the settled picks, plus this poll's cells that
            # have still not resolved, so [SC-57] can print their scores.
            last_reads = list(settled.values()) + [r for r in fresh if not r.identified]

            slowest = max(slowest, time_module.perf_counter() - started)
            time_module.sleep(DRAFT_POLL_SECONDS)

        # Back to screencap before the summary: Mode A reads that screen with OCR and
        # the stream degrades stylised text, which is why the collect mode asks for
        # screencap in the first place.
        #
        # stop_stream(), NOT _start_device_streaming(False). The latter stops the stream
        # but leaves `self._stream` set, and get_screenshot returns the stream's LAST
        # FRAME whenever that attribute exists - so every screenshot for the rest of the
        # match was the frozen final draft frame. The result screen could never appear,
        # Details never opened, and every match burned the full four-minute timeout
        # before [SC-03]. Introduced tonight along with draft streaming.
        self.stop_stream()

        if not saw_draft:
            # Loud, because silence here reads exactly like "no draft was on screen".
            logging.warning(
                f"[SC-56] no draft screen in {DRAFT_WATCH_TIMEOUT:.0f}s - nothing was "
                f"logged. Either this match was joined after the draft, or the draft "
                f"anchor no longer matches."
            )
            return last_frame

        if not seen:
            # Detection worked and identification did not - a different failure, and one
            # the scores can diagnose, so print them rather than an empty log.
            worst = ", ".join(
                f"slot {r.slot} {r.score:.3f}/{r.margin:.3f}"
                for r in sorted(last_reads, key=lambda r: r.slot)
            )
            logging.warning(f"[SC-57] draft seen but no pick identified: {worst}")

        # Kept for the locked screen: a hero read during the draft is still evidence
        # about that slot, and the locked screen missing it does not unmake it.
        self._last_draft_reads = last_reads
        if saw_draft and time_module.monotonic() >= deadline:
            # Say WHY we left. Silence here is what made the last run unreadable.
            logging.warning(
                f"[SC-62] gave up after {DRAFT_MAX_SECONDS:.0f}s with the draft still "
                f"on screen - the locked screen never appeared"
            )
        logging.info(
            f"[SC-53] draft over: {len(seen)}/6 picks read, slowest read "
            f"{slowest * 1000:.0f}ms against a {DRAFT_POLL_SECONDS * 1000:.0f}ms "
            f"interval"
        )
        return last_frame

    def _report_match_end_failure(self) -> None:
        """Score every match-end detector against the current screen, and keep a frame.

        Diagnostics must never make the failure worse, so the whole thing is wrapped:
        this runs at the moment a match has already been lost.
        """
        try:
            frame = self.get_screenshot()
            scores = []
            for name in (
                "event/solstice_clash/result_chart",
                "event/solstice_clash/result_back",
                "event/solstice_clash/draft_anchor",
                "event/solstice_clash/prematch_anchor",
            ):
                hit = self.game_find_template_match(template=name, screenshot=frame)
                scores.append(f"{name.rsplit('/', 1)[1]}={'HIT' if hit else 'miss'}")
            overworld = self._is_in_overview(screenshot=frame)
            logging.warning(
                f"[SC-79] at timeout: {' '.join(scores)} overworld="
                f"{'yes' if overworld else 'no'}"
            )

            where = os_module.environ.get("ADB_SOLSTICE_FAIL_FRAMES")
            if where:
                target = Path(where)
                target.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(UTC).strftime("%H%M%S")
                cv2.imwrite(str(target / f"sc03_{stamp}.png"), frame)
                logging.warning(f"[SC-79] frame saved to {target}")
        except Exception as exc:  # noqa: BLE001 - the match is already lost
            logging.debug(f"[SC-79] could not report the failure: {exc}")

    def _claim_draft_frame(self, match_id: int | None) -> None:
        """Rename this match's saved frame to carry its match id.

        Without this the frame is `draft-pending-20260729-014412.png` and the only way
        back to its match is a timestamp join - the kind that silently pairs the wrong
        rows and is discovered months later. The id is not known until the row is
        written, which is why the file is named twice rather than once.
        """
        pending = getattr(self, "_pending_frame", None)
        self._pending_frame = None
        if pending is None or not match_id:
            return
        try:
            if pending.exists():
                pending.rename(pending.with_name(f"draft-{match_id}.png"))
        except OSError as exc:
            logging.debug(f"[SC-83] could not name the frame for match {match_id}: {exc}")

    def _save_draft_frame(self, frame) -> None:
        """Keep this draft's screenshot, if the user asked for that.

        OFF by default, and the reason is other people: this is shared with
        collaborators, and silently filling somebody's disk at 1-2 MB a match is not a
        default anyone consented to.

        Why keep them at all: every idea about reading something new off the draft -
        hero levels, star tiers, pick order, which heroes went unpicked - is currently
        answerable only by collecting for weeks. With the frames kept, the same question
        is answered against matches already recorded. The one time this was skipped, the
        crowd-trajectory question became permanently unanswerable.

        Never fatal. A failed write costs a screenshot, not a match.
        """
        try:
            if frame is None:
                return
            wdb = getattr(self.settings, "wdb_modes", None)
            if wdb is None or not wdb.save_draft_frames:
                return
            target = draft_frame_dir(wdb.draft_frame_dir)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            path = target / f"draft-pending-{stamp}.png"
            cv2.imwrite(str(path), frame)
            # Named `pending` until the match is recorded and can claim it. A frame that
            # keeps this name is one whose match was never stored - a failed read, or a
            # run killed mid-match - and it is meant to stand out as unusable.
            self._pending_frame = path
            logging.debug(f"[SC-83] draft frame saved to {path}")
        except Exception as exc:  # noqa: BLE001 - a screenshot is never worth a match
            logging.debug(f"[SC-83] could not save the draft frame: {exc}")

    def _sync_theme_windows(self) -> None:
        """Adopt the pool's theme boundaries, and re-file anything they now cover.

        Themes rotate on a schedule the server knows and a client does not, and a client
        that does not know a window files its matches under the event default - which the
        model then discards entirely, because cross-theme matches count for nothing. That
        is not hypothetical: the first rotation put 14 matches on "unknown" here and
        every match pushed to the pool after it.

        Never fatal. No network, no sync key, a server that is down - all of them mean
        the run proceeds on whatever boundaries this install already had.
        """
        try:
            sync = SyncClient(self._store)
            if sync.enabled:
                sync.pull_themes()
        except Exception as exc:  # noqa: BLE001 - never worth a run
            logging.debug(f"[SC-37] could not sync theme windows: {exc}")

    def _overlay_prepare(self, install_if_absent: bool = False) -> None:
        """Get the overlay ready, ONCE, before the collection loop.

        A collection run does NOT install it. Absence is a decision: there is an explicit
        command to install it and another to remove it, and a run that quietly put back
        something the user deleted would be the tool overriding them. An install that IS
        present but out of date is upgraded, because that choice was already made.

        Nothing here happens on the draft path either. A draft is ~20 seconds and the
        first pick reads already compete with the model fit and the ratings OCR; an
        install there would cost pick reads and push the first number past the moment a
        bet is possible, which is the one thing this feature exists to prevent.

        Args:
            install_if_absent: True only for the explicit install command.
        """
        self._overlay_ok = False
        try:
            apk = overlay.apk_path()
            if apk is None:
                logging.debug("[SC-82] no overlay APK bundled - running without it")
                return
            device = self._device.d
            installed = overlay.parse_version(
                overlay.safe_shell(device, overlay.version_command()) or ""
            )
            if installed is None and not install_if_absent:
                logging.debug(
                    "[SC-82] odds overlay is not installed - "
                    "run 'WDB: Install Odds Overlay' if you want it"
                )
                return
            if overlay.needs_install(installed, overlay.OVERLAY_VERSION):
                logging.info(f"[SC-82] installing the odds overlay ({apk.name})")
                self._device.d.d.sync.push(str(apk), overlay.STAGED_APK)
                # Waydroid's dex2oat hangs - installd killed it after 570 SECONDS on a 4KB
                # dex, wedging every install. Skipping compilation installs in ~25s. The
                # property does not survive a reboot, so it is set on every install rather
                # than once by hand: the mode has to work on a machine nobody prepared.
                overlay.safe_shell(device, overlay.dexopt_skip_command())
                result = overlay.safe_shell(device, overlay.install_command()) or ""
                if "Success" not in result:
                    logging.info(f"[SC-82] overlay install failed, continuing: {result}")
                    return
            # Unconditionally: an uninstall/install cycle loses the grant, and re-granting
            # one already in place costs nothing.
            overlay.safe_shell(device, overlay.grant_command())
            overlay.safe_shell(device, overlay.snooze_notice_command())
            self._overlay_ok = True
            logging.info("[SC-82] odds overlay ready")
        except Exception as exc:  # noqa: BLE001 - a display is never worth a run
            logging.info(f"[SC-82] overlay unavailable, continuing without it: {exc}")

    def _overlay_show(self, prediction, gate: str | None) -> None:
        """Paint the current call, or hide the bubble when there is no favourite."""
        if not getattr(self, "_overlay_ok", False):
            return
        mode, percent = overlay.bubble_for(prediction, gate)
        overlay.safe_shell(self._device.d, overlay.update_command(mode, percent))

    def _overlay_hide(self) -> None:
        """Hide it.

        Also called when a draft is confirmed, as a retry: a hide that failed at the end
        of the last match would otherwise leave its number on screen through the next
        draft, presenting a stale call as a current one.
        """
        if not getattr(self, "_overlay_ok", False):
            return
        overlay.safe_shell(self._device.d, overlay.clear_command())

    def _overlay_stop(self) -> None:
        """The overlay does not outlive the run that started it."""
        if not getattr(self, "_overlay_ok", False):
            return
        overlay.safe_shell(self._device.d, overlay.stop_command())

    def _log_final_odds(self, merged) -> None:
        """Print the odds block for the completed six-hero draft."""
        try:
            fitted = getattr(self, "_odds_fit", None)
            if fitted is None:
                return
            left = [r.slug for r in merged if r.side == "left" and r.slug]
            right = [r.slug for r in merged if r.side == "right" and r.slug]
            ratings = getattr(self, "_draft_ratings", (None, None))
            has_ratings = ratings[0] is not None and ratings[1] is not None
            market = getattr(self, "_pool_read", None)
            prediction = predict_odds(
                fitted,
                left,
                right,
                ratings[0],
                ratings[1],
                getattr(self, "_band_evidence", None),
                market.crowd_probability if market else None,
                getattr(self, "_spectators", None),
                ((market.left_pool or 0) + (market.right_pool or 0)) if market else None,
            )
            # Both of these used to be invented: MIN_LOCKED_FOR_ODDS for a count we
            # actually have, and a literal 0 for the theme total. With ratings present
            # the gate returns early and neither was ever read, so the lie only surfaced
            # on a match whose ratings OCR failed - which then reported "0 matches for
            # this theme" against 292 collected.
            gate = gate_reason(
                fitted,
                len(left) + len(right),
                getattr(self, "_theme_matches", 0),
                has_ratings,
            )
            logging.info("[SC-76] FINAL - all picks locked")
            for line in format_odds(prediction, len(left) + len(right), gate):
                logging.info(line)
            # Shown, not hidden. Betting stays open through the last-chance countdown on
            # the locked screen - MATCH_TIMEOUT is measured from the prematch screen for
            # exactly that reason - so the complete six-hero call arrives while a bet is
            # still possible.
            self._overlay_show(prediction, gate)
        except Exception as exc:  # noqa: BLE001 - never worth a match
            logging.debug(f"[SC-76] final odds could not be shown: {exc}")

    def _final_prediction(self, merged) -> tuple[float, str, int] | None:
        """(p_left, source, picks known) for the locked six, or None if not predictable."""
        try:
            fitted = getattr(self, "_odds_fit", None)
            if fitted is None:
                return None
            left = [r.slug for r in merged if r.side == "left" and r.slug]
            right = [r.slug for r in merged if r.side == "right" and r.slug]
            ratings = getattr(self, "_draft_ratings", (None, None))
            market = getattr(self, "_pool_read", None)
            prediction = predict_odds(
                fitted,
                left,
                right,
                ratings[0],
                ratings[1],
                getattr(self, "_band_evidence", None),
                market.crowd_probability if market else None,
                getattr(self, "_spectators", None),
                ((market.left_pool or 0) + (market.right_pool or 0)) if market else None,
            )
            return prediction.p_mid, prediction.source_code, len(left) + len(right)
        except Exception as exc:  # noqa: BLE001 - never worth a match
            logging.debug(f"[SC-75] final prediction failed: {exc}")
            return None

    def _fit_odds(self):
        """Fit the model on everything collected. Returns (fit, matches for this theme).

        Never fatal: a model that will not fit costs the odds display, not the match.
        """
        try:
            matches = load_matches(self._store.matches_for_fit())
            # The theme in force RIGHT NOW, resolved by date exactly as a recorded match
            # is - not an attribute someone remembered to set. Reading a stale or unset
            # one made every match look cross-theme and the gate said "0 for this theme"
            # while the database held hundreds.
            now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            _event, theme_id, _how = self._store.resolve_theme(now)
            fitted = fit_odds(matches, theme_id=theme_id)
            same_theme = sum(1 for m in matches if m.theme_id == theme_id)
            # The gate counts the THEME. Themes change the roster and the battlefield -
            # a hero banned in one theme has no strength to carry into the next - so
            # matches from a sibling theme do not establish that this theme is understood.
            # `same_event` is still computed and logged, because the contrast between the
            # two numbers is what shows how much a rotation costs.
            same_event = sum(1 for m in matches if m.event_id == _event)
            # Rank evidence pools across themes but never across events: rank points
            # reset on every theme change and the ladder resets between events, so a gap
            # from another event is a different scale entirely.
            self._band_evidence = band_evidence(matches, event_id=_event)
            rated = sum(seen for seen, _ in self._band_evidence.values())
            # Every number here used to carry a different scope, printed side by side as
            # though they matched: the headline counted rows the fit DISCARDS, the hero
            # count included heroes only ever seen in discarded matches, and the ratings
            # count was per-event and silently excluded zero-gap matches. Say what each
            # one measures, and lead with the one that decides whether the model works.
            logging.info(
                f"[SC-72] odds model: fitted on {same_theme} matches from this theme "
                f"({len(fitted.appearances)} heroes seen in them); "
                f"{len(matches)} stored, {same_event} this event, "
                f"{rated} rated with a non-zero gap this event"
            )
            self._odds_fit = fitted
            self._theme_matches = same_theme
            return fitted, same_theme
        except Exception as exc:  # noqa: BLE001 - odds are never worth a match
            logging.warning(f"[SC-73] odds model could not be fitted: {exc}")
            self._band_evidence = {}
            return None, 0

    def _log_odds(self, fitted, settled: dict, theme_matches: int) -> None:
        """Print the odds block for the picks locked so far."""
        try:
            left = [p.slug for p in settled.values() if p.side == "left" and p.slug]
            right = [p.slug for p in settled.values() if p.side == "right" and p.slug]
            ratings = getattr(self, "_draft_ratings", (None, None))
            has_ratings = ratings[0] is not None and ratings[1] is not None
            gate = gate_reason(fitted, len(settled), theme_matches, has_ratings)
            market = getattr(self, "_pool_read", None)
            prediction = (
                predict_odds(
                    fitted,
                    left,
                    right,
                    ratings[0],
                    ratings[1],
                    getattr(self, "_band_evidence", None),
                    market.crowd_probability if market else None,
                    getattr(self, "_spectators", None),
                    ((market.left_pool or 0) + (market.right_pool or 0))
                    if market
                    else None,
                )
                if fitted is not None
                else None
            )
            if prediction is None:
                return
            for line in format_odds(prediction, len(settled), gate):
                logging.info(line)
            self._overlay_show(prediction, gate)
        except Exception as exc:  # noqa: BLE001
            logging.warning(f"[SC-73] odds could not be computed: {exc}")

    def _read_draft_picks(
        self,
        frame,
        pool: set[str] | None,
        cell_types: tuple[str, ...] = DRAFT_CELL_TYPES,
        skip_slots: set[int] | None = None,
        sides_first: tuple[str, ...] = (),
        reverse_slots: bool = False,
    ) -> list[PickRead]:
        """Read the six pick cells, taking the stronger answer per slot.

        Two geometries are registered for the same positions and only one can be right;
        reading both and recording which answered turns that into a measurement instead
        of a choice made in advance. See services/solstice/draftlog.py.
        """
        cfg = self._solstice_cfg
        library = self._solstice_library
        heroes = cfg.heroes()
        # Keyed by SIDE AND SLOT. Slot alone silently halved every locked read: the
        # draft numbers its cells 1-6 across both teams, but the locked screen numbers
        # them 1-3 within each side, so left-1 and right-1 collided and one was
        # discarded. Three cells came back instead of six, which is why the sixth pick
        # never appeared no matter how long we waited for the screen to settle.
        best: dict[tuple[str, int], PickRead] = {}

        def read_order(cell):
            """Unread work first, and the LAST pick before the earlier ones.

            The locked screen is read after the draft, so the slots still missing are
            the ones that locked latest - and those are the ones at risk of being lost
            if the screen changes mid-read. Everything already known can wait.
            """
            side_rank = (
                sides_first.index(cell.side)
                if cell.side in sides_first
                else len(sides_first)
            )
            slot = cell.slot or 0
            return (side_rank, -slot if reverse_slots else slot)

        for cell_type in cell_types:
            for cell in sorted(cfg.cells(cell_type), key=read_order):
                if cell.slot is None or cell.side is None:
                    continue
                # A locked pick does not change, so re-reading it every poll spends the
                # draft's short window re-deriving answers already in hand. By the
                # fourth pick this is two cells instead of six, so the reads get FASTER
                # as the draft fills rather than staying flat.
                if skip_slots and cell.slot in skip_slots and len(cell_types) == 1:
                    continue
                identification = identify_with_pool(
                    extract_cell(frame, cell), cell_type, library, cfg, pool
                )
                hero = heroes.get(identification.slug) if identification.slug else None
                read = PickRead(
                    slot=cell.slot,
                    side=cell.side,
                    cell_type=cell_type,
                    slug=identification.slug,
                    name=hero.name if hero else identification.slug,
                    score=identification.score,
                    margin=identification.margin,
                )
                key = (cell.side, cell.slot)
                best[key] = better(best.get(key), read)

        return list(best.values())

    @property
    def _screens(self):
        """Screen templates, loaded once. IO.load_image caches globally anyway."""
        if getattr(self, "_screens_cache", None) is None:
            self._screens_cache = load_templates(self.template_dir)
        return self._screens_cache

    # --- lazily built, because IconLibrary decoding takes seconds and the GUI imports
    # --- this module at startup.
    @property
    def _solstice_cfg(self) -> SolsticeConfig:
        if getattr(self, "_cfg_cache", None) is None:
            self._cfg_cache = SolsticeConfig.load(solstice_db_path())
        return self._cfg_cache

    @property
    def _solstice_library(self) -> IconLibrary:
        if getattr(self, "_lib_cache", None) is None:
            icon_dir = solstice_icon_dir()
            if icon_dir is None:
                raise GameActionFailedError(
                    "[SC-49] bundled hero art not found - without it every hero "
                    "reads as unknown and nothing recorded is usable. Set "
                    "ADB_SOLSTICE_ICON_DIR, or reinstall: the icons ship in "
                    "data/solstice_clash/icons/."
                )
            self._lib_cache = IconLibrary.build(self._solstice_cfg, icon_dir)
        return self._lib_cache

    @property
    def _ocr(self) -> RapidOCRBackend:
        if getattr(self, "_ocr_cache", None) is None:
            self._ocr_cache = RapidOCRBackend()
        return self._ocr_cache

    @property
    def _store(self) -> MatchStore:
        if getattr(self, "_store_cache", None) is None:
            self._store_cache = MatchStore(solstice_db_path())
        return self._store_cache

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
        except Exception as exc:
            logging.debug(f"could not move the chat widget: {exc}")

    def _wait_for_locked_screen(self, timeout: float):
        """A frame of the locked-picks screen, or None if it never appears.

        Replaces waiting for the "Last chance!" banner, which flashes in the final
        seconds of the DRAFT: by the time the picks are locked it has gone, so the wait
        ran its full timeout and returned nothing. Both modes now key on the screen they
        actually want - the VS art plus an All In tile - which is the same signal Mode C
        ends its draft watch on.
        """
        deadline = time_module.monotonic() + timeout
        while time_module.monotonic() < deadline:
            try:
                frame = self.get_screenshot()
            except Exception as exc:  # noqa: BLE001 - a lost frame is not fatal
                logging.debug(f"[SC-45] screenshot while waiting to lock: {exc}")
                time_module.sleep(DRAFT_POLL_SECONDS)
                continue
            if is_locked_screen(frame, self._screens):
                return frame
            time_module.sleep(DRAFT_POLL_SECONDS)
        logging.debug("[SC-07] the locked screen never appeared - no training capture")
        return None

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
                    b.text.strip() for b in blocks if b.text.strip() not in baseline
                ]
                slug = resolve_hero_name_strict(fresh, self._solstice_cfg)
            except Exception as exc:
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
