"""Supreme Arena Mixin."""

import logging

from adb_auto_player.decorators import register_command, register_custom_routine_choice
from adb_auto_player.exceptions import GameTimeoutError
from adb_auto_player.games.afk_journey.base import AFKJourneyBase
from adb_auto_player.games.afk_journey.gui_category import AFKJCategory
from adb_auto_player.games.afk_journey.settings import OpponentPosition
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
    CONTROL_TAP,
    SA_TAP_POINTS,
)
from adb_auto_player.models.decorators import GUIMetadata
from adb_auto_player.models.geometry import Point
from adb_auto_player.ocr import RapidOCRBackend

# The guard re-reads after every refresh. Bounded so a screen that never changes
# cannot loop forever; the real exit is the control turning into the X.
_MAX_FRIENDLY_FIRE_ROUNDS = 12


class SupremeArenaMixin(AFKJourneyBase):
    """Supreme Arena Mixin."""

    @register_command(
        name="SupremeArena",
        gui=GUIMetadata(
            label="Supreme Arena",
            category=AFKJCategory.GAME_MODES,
            tooltip="Participate in daily Supreme Arena battles automatically",
        ),
    )
    @register_custom_routine_choice(label="Supreme Arena")
    def run_supreme_arena(self) -> None:
        """Use Supreme Arena attempts."""
        self.start_up(device_streaming=False)
        # Set by the friendly-fire guard when the run must not continue.
        self._ff_stop_run = False

        try:
            self._enter_supreme_arena()
        except GameTimeoutError:
            return

        for _ in range(self.settings.supreme_arena.attempts):
            if self._ff_stop_run:
                break
            if self.game_find_template_match("arena/no_attempts.png"):
                logging.info("No more Supreme Arena challenges available.")
                break

            if not self._sa_choose_opponent():
                break
            if not self._sa_battle():
                break

        logging.info("Supreme Arena finished.")

    ############################## Helper Functions ##############################

    def _enter_supreme_arena(self) -> None:
        """Enter Supreme Arena."""
        logging.info("Entering Supreme Arena...")
        self.navigate_to_battle_modes_screen()
        try:
            mode = self._find_in_battle_modes(
                "battle_modes/supreme_arena.png",
                "Failed to find Supreme Arena.",
            )
            self.tap(mode)
            self.sleep_navigation()
        except GameTimeoutError as fail:
            logging.error(f"{fail} {self.LANG_ERROR}")
            raise

    def _sa_choose_opponent(self) -> bool:
        """Challenge and choose the weakest (leftmost) Supreme Arena opponent.

        Returns:
            bool: True if opponent chosen and challenge confirmed, False otherwise.
        """
        try:
            logging.debug("Tapping Challenge or Continue to enter opponent selection.")
            # Loop to dismiss any intermediate screens (reward popups, daily rewards,
            # battle-modes list, etc.) before reaching Challenge / Continue.
            btn = None
            for _ in range(5):
                btn = self.wait_for_any_template(
                    templates=[
                        "supreme_arena/challenge.png",
                        "arena/continue.png",
                        "tap_to_close.png",
                        "supreme_arena/daily_rewards.png",
                        "battle_modes/supreme_arena.png",
                    ],
                    timeout=self.min_timeout,
                    timeout_message="Failed to find Challenge or Continue button.",
                )
                if btn.template in (
                    "supreme_arena/challenge.png",
                    "arena/continue.png",
                ):
                    break
                logging.debug(f"Dismissing intermediate screen: {btn.template}")
                self.tap(btn)
                self.sleep_navigation()
            else:
                raise GameTimeoutError(
                    "Failed to reach Challenge button after dismissing popups."
                )
            self.sleep_navigation()
            self.tap(btn)
            self.sleep_navigation()

            logging.debug("Waiting for Select Opponent screen.")
            result = None
            for _ in range(5):
                result = self.wait_for_any_template(
                    templates=[
                        "supreme_arena/select_opponent.png",
                        "supreme_arena/no_attempts_popup.png",
                        "tap_to_close.png",
                        "supreme_arena/daily_rewards.png",
                    ],
                    timeout=self.min_timeout,
                    timeout_message="Failed to find Select Opponent screen.",
                )
                if result.template in (
                    "supreme_arena/select_opponent.png",
                    "supreme_arena/no_attempts_popup.png",
                ):
                    break
                logging.debug(
                    "Dismissing intermediate screen after tapping Challenge: "
                    f"{result.template}"
                )
                self.tap(result)
                self.sleep_navigation()
            else:
                raise GameTimeoutError(
                    "Failed to reach Select Opponent screen after dismissing popups."
                )

            if "no_attempts_popup" in result.template:
                logging.info(
                    "All free Supreme Arena attempts used. Declining purchase."
                )
                self.tap(Point(485, 1250))  # Tap X to cancel purchase
                return False

            if self._sa_friendly_fire_enabled():
                if not self._sa_choose_opponent_guarded():
                    return False
            else:
                position = self.settings.supreme_arena.opponent_position
                opponent_x = {
                    OpponentPosition.Left: 165,
                    OpponentPosition.Middle: 540,
                    OpponentPosition.Right: 915,
                }[position]
                logging.debug(f"Tapping {position} opponent card.")
                self.tap(Point(opponent_x, 950))

            logging.debug("Waiting for Challenge! button on opponent detail screen.")
            challenge = self.wait_for_template(
                template="supreme_arena/challenge_detail.png",
                timeout=self.min_timeout,
                timeout_message="Failed to find Challenge! button.",
            )
            self.tap(challenge)
            return True
        except GameTimeoutError as fail:
            logging.error(fail)
            return False

    ######################## Prevent Friendly Fire ########################

    def _sa_friendly_fire_enabled(self) -> bool:
        """Whether the guard is on for Supreme Arena."""
        # `is True`, not truthiness - see the Arena equivalent.
        return (
            getattr(self.settings.supreme_arena, "prevent_friendly_fire", False) is True
        )

    def _sa_halt(self) -> bool:
        """Stop the whole run and return False. Every failure exit goes through here."""
        self._ff_stop_run = True
        return False

    def _sa_position(self) -> OpponentPosition:
        """The user's configured card preference, which the guard respects."""
        return self.settings.supreme_arena.opponent_position

    def _sa_ocr(self) -> RapidOCRBackend:
        """One OCR backend per run, built lazily - there is no base-class attribute."""
        backend = getattr(self, "_sa_ocr_cache", None)
        if backend is None:
            backend = RapidOCRBackend()
            self._sa_ocr_cache = backend
        return backend

    def _tap_sa_card(self, index: int) -> None:
        """Tap opponent card `index` at its existing fixed point."""
        self.tap(SA_TAP_POINTS[index])

    def _sa_give_up(self) -> bool:
        """Forfeit the challenge: tap the X, match the dialog tick, tap it."""
        self.tap(CONTROL_TAP[FFMode.SUPREME_ARENA])
        self.sleep_navigation()
        tick = find_give_up_tick(self.get_screenshot(), self.template_dir)
        if tick is None:
            logging.error("[FF-43] give-up dialog did not appear - stopping")
            return self._sa_halt()
        self.tap(tick)
        self.sleep_navigation()
        logging.info("[FF-44] gave up the challenge - every opponent was a friend")
        return self._sa_halt()

    def _sa_refresh(self) -> bool:
        """Tap Refresh and confirm the cards actually redrew."""
        before = self.get_screenshot()
        self.tap(CONTROL_TAP[FFMode.SUPREME_ARENA])
        self.sleep_navigation()
        if not screen_changed(before, self.get_screenshot()):
            logging.error(
                "[FF-47] the screen did not change after a refresh - stopping "
                "rather than assuming exhaustion, which would forfeit an attempt"
            )
            return False
        return True

    def _sa_choose_opponent_guarded(self) -> bool:
        """Pick an opponent that is neither a Friend nor a Guild Member."""
        retried_unknown = False
        excluded: set[int] = set()
        for _ in range(_MAX_FRIENDLY_FIRE_ROUNDS):
            self.handle_popup_messages()
            decision = ff_evaluate(
                self.get_screenshot(),
                FFMode.SUPREME_ARENA,
                self._sa_position(),
                self._sa_ocr(),
                self.template_dir,
                frozenset(excluded),
            )
            if decision.action is Action.TAKE:
                self.sleep_navigation()
                if confirms_take(
                    self.get_screenshot(),
                    FFMode.SUPREME_ARENA,
                    self._sa_position(),
                    self._sa_ocr(),
                    decision.card,
                ):
                    self._tap_sa_card(decision.card)
                    return True
                excluded.add(decision.card)
                continue
            if decision.action is Action.STOP:
                if "neither Refresh nor X" in decision.reason and not retried_unknown:
                    retried_unknown = True
                    self.sleep_navigation()
                    continue
                logging.warning(f"[FF-41] stopping: {decision.reason}")
                return self._sa_halt()
            if decision.action is Action.GIVE_UP:
                return self._sa_give_up()
            if not self._sa_refresh():
                return self._sa_halt()
        logging.warning("[FF-42] no non-friendly opponent after the round cap")
        return self._sa_halt()

    ############################## Helper Functions ##############################

    def _sa_battle(self) -> bool:
        """Execute the Supreme Arena battle: Next, Next, Battle, wait for end.

        Returns:
            bool: True if battle completed, False otherwise.
        """
        try:
            logging.debug("Tapping Next (1/2).")
            next1 = self.wait_for_template(
                template="next.png",
                timeout=self.min_timeout,
                timeout_message="Failed to find Next button (1/2).",
            )
            self.tap(next1)
            self.sleep_navigation()

            logging.debug("Tapping Next (2/2).")
            next2 = self.wait_for_template(
                template="next.png",
                timeout=self.min_timeout,
                timeout_message="Failed to find Next button (2/2).",
            )
            self.tap(next2)

            logging.debug("Starting battle.")
            battle = self.wait_for_template(
                template="arena/battle.png",
                timeout=self.min_timeout,
                timeout_message="Failed to find Battle button.",
            )
            self.sleep_navigation()
            self.tap(battle)

            logging.debug("Waiting for battle to complete.")
            # Try to skip if available
            try:
                skip = self.wait_for_template(
                    template="arena/skip.png",
                    timeout=self.min_timeout,
                    timeout_message="No skip button found.",
                )
                self.tap(skip)
            except GameTimeoutError:
                pass

            self.handle_popup_messages()
            done = self.wait_for_any_template(
                templates=["arena/done.png", "next.png", "navigation/confirm.png"],
                timeout=self.BATTLE_TIMEOUT,
                timeout_message="Battle did not complete in time.",
            )
            self.sleep_navigation()
            self.tap(done)
            self.sleep_navigation()
            # Dismiss any post-battle reward level popup (may appear with a short delay)
            try:
                tap_close = self.wait_for_any_template(
                    templates=["tap_to_close.png"],
                    timeout=self.fast_timeout,
                    timeout_message="No post-battle popup found.",
                )
                logging.debug("Dismissing post-battle reward popup.")
                self.tap(tap_close)
                self.sleep_navigation()
            except GameTimeoutError:
                pass
            return True
        except GameTimeoutError as fail:
            logging.error(fail)
            return False
