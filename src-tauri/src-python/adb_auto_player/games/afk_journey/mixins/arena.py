"""Arena Mixin."""

import logging

from adb_auto_player.decorators import register_command, register_custom_routine_choice
from adb_auto_player.exceptions import GameTimeoutError
from adb_auto_player.games.afk_journey.base import AFKJourneyBase
from adb_auto_player.games.afk_journey.gui_category import AFKJCategory
from adb_auto_player.models.decorators import GUIMetadata
from adb_auto_player.games.afk_journey.services.friendly_fire import (
    Action,
    Mode as FFMode,
    confirms_take,
    evaluate as ff_evaluate,
    screen_changed,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.control import (
    find_give_up_tick,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    CARD_X_RANGES,
    CONTROL_TAP,
)
from adb_auto_player.games.afk_journey.settings import OpponentPosition
from adb_auto_player.models.geometry import Point
from adb_auto_player.models.image_manipulation import CropRegions
from adb_auto_player.ocr import RapidOCRBackend

# The guard re-reads after every refresh. Bounded so a screen that never changes
# cannot loop forever; the real exit is the control turning into the X.
_MAX_FRIENDLY_FIRE_ROUNDS = 12
_SCREEN_WIDTH = 1080


class ArenaMixin(AFKJourneyBase):
    """Arena Mixin."""

    @register_command(
        name="Arena",
        gui=GUIMetadata(
            label="Arena",
            category=AFKJCategory.GAME_MODES,
            tooltip="Participate in daily Arena battles automatically",
        ),
    )
    @register_custom_routine_choice(label="Arena")
    def run_arena(self) -> None:
        """Use Arena attempts."""
        self.start_up(device_streaming=False)
        # Set by the friendly-fire guard when it decides the run must not continue.
        # run_arena has TWO loops, so a bare False from _choose_opponent only breaks
        # the first - the second would then claim a free attempt and fight on.
        self._ff_stop_run = False

        try:
            self._enter_arena()
        except GameTimeoutError:
            return

        for _ in range(5):
            if self._ff_stop_run:
                break
            if self.game_find_template_match("arena/no_attempts.png"):
                logging.debug("Free attempts exhausted before 5 attempts.")
                break

            if not self._choose_opponent():
                break
            if not self._battle():
                break

        for _ in range(2):
            if self._ff_stop_run:
                break
            if not self._claim_free_attempt():
                break

            if not self._choose_opponent():
                break
            if not self._battle():
                break

        logging.info("Arena finished.")

    ############################## Helper Functions ##############################

    def _enter_arena(self) -> None:
        """Enter Arena."""
        logging.info("Entering Arena...")
        self.navigate_to_battle_modes_screen()
        try:
            arena_mode = self._find_in_battle_modes(
                "arena/label.png",
                "Failed to find Arena.",
            )
            self.tap(arena_mode)
            self.sleep_navigation()
        except GameTimeoutError as fail:
            logging.error(f"{fail} {self.LANG_ERROR}")
            raise

        logging.debug("Checking for weekly arena notices.")
        all(self._confirm_notices() for _ in range(2))

    def _confirm_notices(self) -> bool:
        """Close out weekly reward and weekly notice popups.

        Returns:
            bool: True if notices were closed, False otherwise.
        """
        try:
            _ = self.wait_for_any_template(
                templates=["arena/weekly_rewards.png", "arena/weekly_notice.png"],
                timeout=self.min_timeout,
                timeout_message="No notices found.",
            )
            self.tap(Point(380, 1890))
            self.sleep_navigation()

            return True
        except GameTimeoutError as fail:
            logging.debug(fail)
            pass

        return False

    def _choose_opponent(self) -> bool:
        """Choose Arena opponent.

        Returns:
            bool: True if opponent chosen, False otherwise.
        """
        try:
            logging.debug("Start arena challenge.")
            btn = self.wait_for_any_template(
                templates=["arena/challenge.png", "arena/continue.png"],
                timeout=self.min_timeout,
                timeout_message="Failed to start Arena runs.",
            )
            self.sleep_navigation()
            self.tap(btn)

            logging.debug("Choosing opponent.")
            self.handle_popup_messages()  # Clear any potential popups
            if self._friendly_fire_enabled():
                return self._choose_opponent_guarded()
            opponent = self.wait_for_template(
                template="arena/opponent.png",
                crop_regions=CropRegions(right=0.6),  # Target weakest opponent.
                timeout=self.min_timeout,
                timeout_message="Failed to find Arena opponent.",
            )
            self.tap(opponent)
            return True
        except GameTimeoutError as fail:
            logging.error(fail)
            return False

    ######################## Prevent Friendly Fire ########################

    def _friendly_fire_enabled(self) -> bool:
        """Whether the guard is on for this mode."""
        arena = getattr(self.settings, "arena", None)
        # `is True`, not truthiness: the setting is a bool, and anything else -
        # a mock, a stub, a partially-built settings object - must NOT silently
        # switch on a guard that changes which opponent gets attacked.
        return getattr(arena, "prevent_friendly_fire", False) is True

    def _ff_halt(self) -> bool:
        """Stop the whole RUN, not just this selection, and return False.

        Every failure exit goes through here. `run_arena` has TWO loops, so a bare
        `return False` only breaks the first - the second then claims a free attempt
        and fights on, which is how an unsafe give-up or a failed refresh would have
        been followed by more battles.
        """
        self._ff_stop_run = True
        return False

    def _ff_ocr(self) -> RapidOCRBackend:
        """One OCR backend per run, built lazily.

        There is no `ocr_backend` attribute on the base class - every mode that needs
        OCR constructs its own, as `solstice_clash.py` does.
        """
        backend = getattr(self, "_ff_ocr_cache", None)
        if backend is None:
            backend = RapidOCRBackend()
            self._ff_ocr_cache = backend
        return backend

    def _tap_arena_card(self, index: int) -> bool:
        """Tap opponent card `index`, located within its own x-range.

        The unguarded path matches this same template inside CropRegions(right=0.6) -
        the left 40% - which is exactly why it can only ever find card 1.
        """
        x0, x1 = CARD_X_RANGES[FFMode.ARENA][index]
        crop = CropRegions(
            left=x0 / _SCREEN_WIDTH, right=(_SCREEN_WIDTH - x1) / _SCREEN_WIDTH
        )
        match = self.game_find_template_match(
            template="arena/opponent.png", crop_regions=crop
        )
        if match is None:
            logging.error(f"[FF-30] could not locate card {index + 1}")
            return self._ff_halt()
        self.tap(match)
        return True

    def _ff_give_up(self) -> bool:
        """Forfeit the challenge, with both matches required before any tap.

        The X is already the control - that is what a GIVE_UP decision means - so tap
        it FIRST to raise the dialog, then match the tick and tap that.
        """
        self.tap(CONTROL_TAP[FFMode.ARENA])
        self.sleep_navigation()
        tick = find_give_up_tick(self.get_screenshot())
        if tick is None:
            logging.error("[FF-33] give-up dialog did not appear - stopping")
            return self._ff_halt()
        self.tap(tick)
        self.sleep_navigation()
        logging.info("[FF-34] gave up the challenge - every opponent was a friend")
        return self._ff_halt()

    def _choose_opponent_guarded(self) -> bool:
        """Pick an opponent that is neither a Friend nor a Guild Member.

        Waits for the selection screen before reading it: the unguarded path gets that
        readiness guarantee from its `wait_for_template`, and without an explicit wait
        the first evaluation can land on a half-drawn frame and read it as clear.
        """
        try:
            self.wait_for_template(
                template="arena/opponent.png",
                timeout=self.min_timeout,
                timeout_message="Arena opponent screen did not appear.",
            )
        except GameTimeoutError as fail:
            logging.error(f"[FF-35] {fail}")
            return self._ff_halt()

        retried_unknown = False
        excluded: set[int] = set()
        for _ in range(_MAX_FRIENDLY_FIRE_ROUNDS):
            self.handle_popup_messages()
            decision = ff_evaluate(
                self.get_screenshot(),
                FFMode.ARENA,
                OpponentPosition.Left,  # Arena has no position setting
                self._ff_ocr(),
                frozenset(excluded),
            )
            if decision.action is Action.TAKE:
                # Confirming read before committing to a battle. Free - no timer -
                # and it is what catches a transient false negative.
                self.sleep_navigation()
                if confirms_take(
                    self.get_screenshot(),
                    FFMode.ARENA,
                    OpponentPosition.Left,
                    self._ff_ocr(),
                    decision.card,
                ):
                    return self._tap_arena_card(decision.card)
                # Remember the rejection for the rest of this attempt, or the next
                # iteration starts fresh and can take the very card the second read
                # called friendly.
                excluded.add(decision.card)
                continue
            if decision.action is Action.STOP:
                if "neither Refresh nor X" in decision.reason and not retried_unknown:
                    retried_unknown = True
                    self.sleep_navigation()
                    continue
                logging.warning(f"[FF-31] stopping: {decision.reason}")
                return self._ff_halt()
            if decision.action is Action.GIVE_UP:
                return self._ff_give_up()

            if not self._ff_refresh():
                return self._ff_halt()
        logging.warning("[FF-32] no non-friendly opponent after the round cap")
        return self._ff_halt()

    def _ff_refresh(self) -> bool:
        """Tap Refresh and confirm the cards actually redrew.

        A stalled refresh and an exhausted one look identical from a single frame,
        and acting on the guess taps the control that forfeits an attempt.

        Returns:
            True if the screen redrew and is ready to read again.
        """
        before = self.get_screenshot()
        self.tap(CONTROL_TAP[FFMode.ARENA])
        self.sleep_navigation()
        if not screen_changed(before, self.get_screenshot()):
            logging.error(
                "[FF-37] the screen did not change after a refresh - stopping "
                "rather than assuming exhaustion, which would forfeit an attempt"
            )
            return False
        try:
            self.wait_for_template(
                template="arena/opponent.png",
                timeout=self.min_timeout,
                timeout_message="Arena cards did not redraw after a refresh.",
            )
        except GameTimeoutError as fail:
            logging.error(f"[FF-36] {fail}")
            return False
        return True

    ############################## Helper Functions ##############################

    def _battle(self) -> bool:
        """Battle Arena opponent.

        Returns:
            bool: True if battle completed, False otherwise.
        """
        try:
            logging.debug("Initiate battle.")
            start = self.wait_for_template(
                template="arena/battle.png",
                timeout=self.min_timeout,
                timeout_message="Failed to start Arena battle.",
            )
            self.sleep_navigation()
            self.tap(start)

            logging.debug("Skip battle.")
            skip = self.wait_for_template(
                template="arena/skip.png",
                timeout=self.min_timeout,
                timeout_message="Failed to skip Arena battle.",
            )
            self.tap(skip)

            logging.debug("Battle complete.")
            self.handle_popup_messages()  # Clear any potential popups
            confirm = self.wait_for_any_template(
                templates=["arena/done.png", "next.png", "navigation/confirm.png"],
                timeout=self.min_timeout,
                timeout_message="Failed to confirm Arena battle completion.",
            )
            self.sleep_navigation()
            self.tap(confirm)
            self.sleep_navigation()
            return True
        except GameTimeoutError as fail:
            logging.error(fail)
            return False

    def _claim_free_attempt(self) -> bool:
        """Claim free Arena attempts.

        Returns:
            bool: True if free attempt claimed, False not available.
        """
        logging.debug("Claiming free attempts.")
        if not self._try_wait_and_tap(
            "arena/buy.png",
            timeout_message="Failed looking for free attempts.",
        ):
            return False

        try:
            _ = self.wait_for_template(
                template="arena/buy_free.png",
                timeout=self.min_timeout,
                timeout_message="No more free attempts.",
            )
            logging.debug("Free attempt found.")
        except GameTimeoutError as fail:
            logging.info(fail)
            cancel = self.game_find_template_match("arena/cancel_purchase.png")
            (
                self.tap(cancel)
                if cancel
                else self.tap(Point(550, 1790))  # Cancel fallback
            )

            return False

        logging.debug("Purchasing free attempt.")
        self._click_confirm_on_popup()

        return True
