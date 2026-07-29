"""The odds model - pure, fixture-free, no device.

Every test here is a property the model must have for its number to be worth acting on,
not a snapshot of what it currently prints.
"""

import numpy as np
from adb_auto_player.games.afk_journey.services.solstice.odds import (
    CROSS_THEME_WEIGHT,
    USE_PLAYER_TERMS,
    Match,
    fit,
    predict,
)


def _match(left, right, left_won, theme_id=1, players=(None, None)):
    return Match(
        left=tuple(left),
        right=tuple(right),
        left_won=left_won,
        theme_id=theme_id,
        left_player=players[0],
        right_player=players[1],
    )


def _dominant(hero="star", n=40):
    """`hero`'s side always wins, and it plays each side equally often.

    Alternating sides matters: a fixture where the left always wins teaches the
    INTERCEPT that left wins, and the hero learns nothing - which is the model behaving
    correctly and the fixture testing nothing.
    """
    out = []
    for i in range(n):
        allies = [f"h{i % 7}", f"h{(i + 1) % 7}"]
        foes = [f"h{(i + 2) % 7}", f"h{(i + 3) % 7}", f"h{(i + 4) % 7}"]
        if i % 2 == 0:
            out.append(_match([hero, *allies], foes, left_won=True))
        else:
            out.append(_match(foes, [hero, *allies], left_won=False))
    return out


def test_a_hero_that_always_wins_gets_a_positive_strength():
    fitted = fit(_dominant())
    column = fitted.index_of("star")
    assert column is not None
    assert fitted.beta[column] > 0.0


def test_no_data_means_a_coin_flip_not_a_guess():
    """With nothing learned, the only honest answer is 50% - and a wide interval."""
    fitted = fit([])
    p = predict(fitted, ["a", "b", "c"], ["d", "e", "f"])
    assert abs(p.p_mid - 0.5) < 0.01
    assert p.known_heroes == 0


def test_unknown_heroes_contribute_nothing_rather_than_a_guess():
    fitted = fit(_dominant())
    known = predict(fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"])
    unknown = predict(fitted, ["star", "h0", "nobody"], ["h2", "h3", "h4"])
    assert unknown.known_heroes == known.known_heroes - 1
    # The unknown hero moves nothing on its own account.
    assert unknown.p_mid != known.p_mid or unknown.known_heroes < known.known_heroes


def test_the_side_with_the_winning_hero_is_favoured():
    fitted = fit(_dominant())
    p = predict(fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"])
    assert p.p_mid > 0.5 and p.favours == "left"
    mirrored = predict(fitted, ["h2", "h3", "h4"], ["star", "h0", "h1"])
    assert mirrored.p_mid < 0.5 and mirrored.favours == "right"


def test_swapping_sides_mirrors_the_probability():
    """A model that is not symmetric under a side swap is reading position, not heroes."""
    fitted = fit(_dominant())
    left = predict(fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"])
    right = predict(fitted, ["h2", "h3", "h4"], ["star", "h0", "h1"])
    assert abs((1.0 - left.p_mid) - right.p_mid) < 1e-9


def test_regularisation_keeps_a_single_observation_modest():
    """One match must not produce certainty. Unpenalised, this diverges."""
    fitted = fit([_match(["one", "two", "three"], ["x", "y", "z"], True)])
    p = predict(fitted, ["one", "two", "three"], ["x", "y", "z"])
    assert p.p_mid < 0.90, f"one match should not yield {p.p_mid:.2f}"
    assert p.weakest_evidence == 1


def test_more_evidence_narrows_the_interval():
    thin = predict(fit(_dominant(n=6)), ["star", "h0", "h1"], ["h2", "h3", "h4"])
    thick = predict(fit(_dominant(n=60)), ["star", "h0", "h1"], ["h2", "h3", "h4"])
    assert thick.standard_error < thin.standard_error
    assert (thick.p_high - thick.p_low) < (thin.p_high - thin.p_low)


def test_another_theme_counts_for_nothing():
    """Themes change the roster and the battlefield, so a match from another theme is
    evidence about the same heroes under different rules.

    Established the hard way on 2026-07-29: Aurora was pickable in a live Flourishing
    Wilds match while our Converging Paths roster had her banned, and the in-game Themes
    screen distinguishes standard from special terrain. Discounted to 0.35 first, then
    dropped to zero when the borrowed evidence proved worse than the thin data it was
    meant to cushion."""
    same = fit(_dominant(), theme_id=1)
    other = fit([_match(m.left, m.right, m.left_won, theme_id=2) for m in _dominant()],
                theme_id=1)
    column = same.index_of("star")
    assert column is not None
    # Learned nothing at all from the sibling theme: the strength stays at its prior.
    assert abs(other.beta[column]) < 1e-6
    assert CROSS_THEME_WEIGHT == 0.0


def test_a_thin_theme_is_gated_rather_than_padded():
    """The consequence of discarding: right after a rotation there is nothing to predict
    from, and the gate must say so instead of the model borrowing last theme's answers."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        MIN_MATCHES_FOR_ODDS,
        gate_reason,
    )

    fitted = fit([_match(m.left, m.right, m.left_won, theme_id=2) for m in _dominant()],
                 theme_id=1)
    reason = gate_reason(fitted, 6, 0, has_ratings=False)
    assert reason is not None
    assert str(MIN_MATCHES_FOR_ODDS) in reason


def test_the_interval_brackets_the_estimate():
    fitted = fit(_dominant())
    p = predict(fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"])
    assert p.p_low < p.p_mid < p.p_high
    assert 0.0 < p.p_low and p.p_high < 1.0


def test_the_fit_converges_rather_than_wandering():
    """The gradient at the solution must be ~zero, or the answer is wherever it stopped."""
    matches = _dominant(n=30)
    fitted = fit(matches)
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        SIGMA_BETA,
        SIGMA_PHI,
        SIGMA_THETA,
        design,
    )

    x, y, w, heroes, players = design(matches, None)
    penalty = np.full(x.shape[1], 1.0 / SIGMA_THETA**2)
    penalty[0] = 1.0 / SIGMA_BETA**2
    penalty[1 + len(heroes) :] = 1.0 / SIGMA_PHI**2
    p = 1.0 / (1.0 + np.exp(-(x @ fitted.beta)))
    gradient = x.T @ (w * (p - y)) + penalty * fitted.beta
    assert np.max(np.abs(gradient)) < 1e-6


def test_player_terms_are_off_until_players_actually_repeat():
    """Measured, not assumed: on the first 245 collected matches there were 162
    distinct players - nearly two per match - and including them moved out-of-sample
    logloss by less than 0.0001 while adding 162 parameters. A term that fits one match
    and predicts nothing is overfitting with extra steps.
    """
    matches = [
        _match([f"a{i}", f"b{i}", f"c{i}"], [f"d{i}", f"e{i}", f"f{i}"], True,
               players=("ace", "other"))
        for i in range(30)
    ]
    fitted = fit(matches)
    assert fitted.players == (), "player terms should be off"
    assert not USE_PLAYER_TERMS


def test_the_player_machinery_still_works_when_switched_on():
    """Kept alive deliberately - the question is worth revisiting once players repeat."""
    import adb_auto_player.games.afk_journey.services.solstice.odds as odds_module

    matches = [
        _match([f"a{i}", f"b{i}", f"c{i}"], [f"d{i}", f"e{i}", f"f{i}"], True,
               players=("ace", "other"))
        for i in range(30)
    ]
    odds_module.USE_PLAYER_TERMS = True
    try:
        fitted = fit(matches)
        assert "ace" in fitted.players
        column = 1 + len(fitted.heroes) + fitted.players.index("ace")
        assert fitted.beta[column] > 0.0
    finally:
        odds_module.USE_PLAYER_TERMS = False


def test_the_rating_step_ignores_small_gaps_and_is_flat_beyond_the_threshold():
    """Measured over 95 rated matches, replacing the stated 9-band table.

    Two claims, and the second is the surprising one: gaps under 100 points are worth
    nothing (0-50 favourites won 47%, which is noise), and past 100 more gap is NOT more
    edge - every proportional variant scored at or below this flat step and none was as
    reliable."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        blended_nudge,
    )

    assert blended_nudge(0, {}) == 0.0
    assert blended_nudge(100, {}) == 0.0622
    # Flat: a 400-point gap earns no more than a 150-point one.
    assert blended_nudge(100, None) == blended_nudge(100, None)


def test_a_band_moves_toward_what_was_actually_observed():
    """Shrinkage, not replacement: ten results nudge, sixty largely decide."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        blended_nudge,
    )

    prior = blended_nudge(150, {})
    thin = blended_nudge(150, {150: (10, 5)})     # 50% - the band means nothing
    thick = blended_nudge(150, {150: (60, 30)})
    assert thin < prior, "evidence against the prior must pull it down"
    assert thick < thin, "more evidence pulls further"


def test_rank_evidence_pools_across_themes_but_never_across_events():
    """Ratings reset between events, and rank points reset on every theme change - so a
    gap from another event is a different scale, while another theme is the same one."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import band_evidence

    same_event = [
        Match(("a",), ("b",), True, theme_id=t, left_rating=4300, right_rating=4100,
              event_id=1)
        for t in (3, 4, 5)
    ]
    other_event = [
        Match(("a",), ("b",), True, theme_id=3, left_rating=4300, right_rating=4100,
              event_id=2)
    ]
    pooled = band_evidence(same_event + other_event, event_id=1)
    # Band 100: the step has two bands, 0 and 100, so a 200-point gap lands in 100.
    assert pooled[100] == (3, 3), "three themes of one event must pool"


def test_a_gap_of_zero_is_not_evidence_about_anything():
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        band_evidence,
        rating_offset,
    )

    assert rating_offset(4100, 4100) == 0.0
    assert band_evidence(
        [Match(("a",), ("b",), True, left_rating=4100, right_rating=4100, event_id=1)],
        event_id=1,
    ) == {}


def test_no_rating_gap_alone_reaches_certainty():
    """A 900-point gap is heavily favoured, never proof."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        MAX_RATING_PROBABILITY,
    )

    p = predict(fit([]), ["a", "b", "c"], ["d", "e", "f"], 5000, 4100)
    assert p.p_mid <= MAX_RATING_PROBABILITY + 1e-9


# --- combining rating, crowd and heroes ------------------------------------

def test_a_thin_market_nudges_and_does_not_decide():
    """The case that prompted the weighting: 21 spectators screamed 92% and lost."""
    fitted = fit([])
    thin = predict(fitted, ["a", "b", "c"], ["d", "e", "f"],
                   4240, 4335, None, 0.92, 21, 128_492)
    assert 0.45 < thin.p_mid < 0.60, f"a 21-spectator market moved it to {thin.p_mid:.2f}"


def test_the_crowd_no_longer_moves_the_number_however_many_are_watching():
    """W_CROWD is 0 since 2026-07-29. Measured over 54 scored predictions: the crowd is
    flat across its own confidence and a noisy echo of the rating gap, and at weight 0.70
    it supplied nearly all the displayed spread - which is why confident calls kept
    landing the wrong way.

    The reading is still taken and still recorded; it is only barred from the number, so
    this stays a weight to revisit rather than a deletion."""
    fitted = fit([])
    thin = predict(fitted, ["a"], ["b"], 4300, 4300, None, 0.75, 21, 200_000)
    thick = predict(fitted, ["a"], ["b"], 4300, 4300, None, 0.75, 221, 200_000)
    assert thick.p_mid == thin.p_mid
    assert abs(thick.p_mid - 0.5) < 1e-9   # equal ratings, no heroes: nothing to say


def test_signals_that_disagree_pull_toward_even():
    """Log-odds addition, not averaging: disagreement cancels rather than compounds.

    Demonstrated on the two signals that still carry weight - a rating gap and a hero
    model - since the crowd no longer contributes to the sum."""
    fitted = fit(_dominant(n=40))
    # Rating favours left; "star" is the dominant hero and is also on the left.
    agree = predict(fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"], 4600, 4200)
    # Same rating edge, but the dominant hero is now on the RIGHT.
    disagree = predict(fitted, ["h2", "h3", "h4"], ["star", "h0", "h1"], 4600, 4200)
    assert agree.p_mid > disagree.p_mid
    assert agree.p_mid > 0.5


def test_heroes_count_more_as_they_are_seen_more():
    """A hero seen twice must not carry the weight of one seen fifty times."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import hero_evidence

    fitted = fit(_dominant(n=40))
    known = hero_evidence(fitted, ["star", "h0", "h1"])
    unknown = hero_evidence(fitted, ["nobody", "nobody2", "nobody3"])
    assert known > 0.6
    assert unknown == 0.0


def test_crowd_reliability_matches_the_observed_bands():
    """20 spectators is nothing, 100 about half, 200+ full."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        crowd_reliability,
    )

    assert crowd_reliability(20, 100_000) < 0.15
    assert 0.3 < crowd_reliability(100, 200_000) < 0.6
    assert crowd_reliability(220, 500_000) > 0.85


def test_a_missing_spectator_count_caps_trust_at_half():
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        crowd_reliability,
    )

    assert crowd_reliability(None, 1_000_000) <= 0.5


def test_the_block_names_the_signals_that_built_the_number():
    """The header used to say "from the rating gap" on a number the crowd had already
    moved by twenty points. A person reading it has to know the crowd is in there -
    otherwise they treat it as independent confirmation of what the screen shows."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import format_odds

    fitted = fit(_dominant(n=40))
    p = predict(
        fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"],
        left_rating=4400, right_rating=4100,
        crowd=0.30, spectators=250, total_pool=500_000,
    )
    # The crowd is present in the inputs and absent from the label, because W_CROWD is 0
    # and the label describes what actually moved the number rather than what was read.
    assert p.signals == ("rating", "heroes")
    header = format_odds(p, 6, None)[2]
    assert "rating + heroes" in header
    assert "crowd" not in header


def test_a_signal_that_did_not_move_the_number_is_not_named():
    """Equal ratings contribute nothing, and a comp of unseen heroes contributes
    nothing. Naming either would describe a number that does not exist."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import format_odds

    fitted = fit(_dominant(n=40))
    p = predict(
        fitted, ["nobody1", "nobody2", "nobody3"], ["nobody4", "nobody5", "nobody6"],
        left_rating=4400, right_rating=4400,
        crowd=0.30, spectators=250, total_pool=500_000,
    )
    # Equal ratings contribute nothing, unseen heroes contribute nothing, and the crowd
    # is switched off - so there is nothing honest to name.
    assert p.signals == ()
    assert "nothing measurable" in format_odds(p, 6, None)[2]


def test_a_thin_market_is_not_named_either():
    """Twelve spectators earned a weight near zero even when the crowd counted, and it
    counts for nothing at all now. Kept because `crowd_reliability` still exists and is
    the thing that would gate the crowd's return."""
    fitted = fit(_dominant(n=40))
    p = predict(
        fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"],
        left_rating=4400, right_rating=4400,
        crowd=0.90, spectators=12, total_pool=3_000,
    )
    assert "crowd" not in p.signals


def test_the_stored_source_records_the_composition():
    """Scored predictions get split by composition later; rating-only and
    rating-plus-crowd are different models and must not pool their calibration."""
    fitted = fit(_dominant(n=40))
    p = predict(
        fitted, ["star", "h0", "h1"], ["h2", "h3", "h4"],
        left_rating=4400, right_rating=4100,
        crowd=0.30, spectators=250, total_pool=500_000,
    )
    assert p.source_code == "r+h"
    # The server column is 16 characters and silently truncates past it.
    assert len(p.source_code) <= 16


def test_the_gate_reports_the_real_theme_count_not_a_placeholder():
    """The final block passed a literal 0 for the theme total, so a match whose ratings
    OCR failed reported "0 matches for this theme" against 292 collected. The gate is the
    thing that decides whether a number is shown at all - it must be told the truth."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import (
        MIN_MATCHES_FOR_ODDS,
        gate_reason,
    )

    fitted = fit(_dominant(n=40))
    # No ratings, so the gate cannot short-circuit and must judge on collected matches.
    assert gate_reason(fitted, 6, MIN_MATCHES_FOR_ODDS + 10, has_ratings=False) is None
    thin = gate_reason(fitted, 6, 0, has_ratings=False)
    assert thin is not None and "0 matches for this theme" in thin
