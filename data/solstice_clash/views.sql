-- Derived views over the Solstice Clash tables.
--
-- SEPARATE FILE, and executed by BOTH `migrate.py` and the client at startup, because a
-- shipped build never runs migrate.py and `solstice_db_path()` returns an existing user
-- database untouched. A view defined only in schema.sql would therefore exist on the
-- machine that ran the migration and nowhere else.
--
-- Every statement here must be DROP-then-CREATE and safe to run on every start: that is
-- what makes re-running it free, and what lets a definition change reach an install that
-- already has the old one. Views hold no data, so dropping one costs nothing.
-- ---------------------------------------------------------------- derived views

-- Hero-vs-hero record, one row per unordered pair per theme.
--
-- A VIEW rather than a maintained table, deliberately. Every number here is an aggregate
-- of `match` x `match_hero`, and those rows are the permanent archive - so the tallies are
-- reconstructable forever and nothing is gained by keeping a second copy. What a second
-- copy WOULD add is the ability to silently disagree with the first: matches arrive late
-- from the pool, get re-filed when a theme window is corrected (14 of them already have),
-- get hand-relabelled from a saved crop, and get deleted. Every one of those silently
-- corrupts an incremented counter, and the corruption is undetectable because there is
-- nothing left to compare against. A view cannot drift. At ~450 matches and 9 lookups per
-- fight this is sub-millisecond; if that ever stops being true, materialise it with a
-- full rebuild as the ONLY write path - never increments.
--
-- Canonical ordering: hero_a < hero_b always, so the key for a pair is reproducible
-- without a lookup and "A vs B" and "B vs A" are the same row, one the negation of the
-- other. Guaranteed by MIN/MAX here rather than by application discipline.
--
-- The corpus rule is deliberately IDENTICAL to the odds model's (`matches_for_fit` plus
-- `load_matches`): decisive outcome, hero_slug not null, exactly three a side. If this
-- view admitted a match the model rejects, the two would disagree about the same fight
-- forever and no one would know which was right.
--
--   SELECT a_wins, b_wins, tally, observations FROM hero_matchup
--    WHERE theme_id=? AND hero_a=? AND hero_b=?      -- one row, canonical key
--
-- `tally` is a_wins - b_wins, computed rather than stored, so it can never disagree with
-- the counts that produced it. Keep BOTH: a tally of +1 from one match and from nine at
-- 5-4 mean entirely different things, and any sane weighting damps by `observations`.--
-- SCHEMA v6: `outcome` and `side` are gone, replaced by `winning_trio` and
-- `match_hero.trio`. The translation was direct, because this view was ALREADY
-- orientation-free in spirit - it asked whether the side holding the lexicographically
-- smaller hero won. `winning_trio = 1` IS "the smaller composition won", so what the
-- view computed by hand now falls out of the schema.

DROP VIEW IF EXISTS hero_matchup;

CREATE VIEW hero_matchup AS
WITH complete AS (
  SELECT m.id, m.event_id, m.theme_id, m.winning_trio
    FROM match m
   WHERE m.canonical_state = 'canonical'
     AND (SELECT COUNT(*) FROM match_hero h
           WHERE h.match_id = m.id AND h.trio = 1 AND h.hero_slug IS NOT NULL) = 3
     AND (SELECT COUNT(*) FROM match_hero h
           WHERE h.match_id = m.id AND h.trio = 2 AND h.hero_slug IS NOT NULL) = 3
)
SELECT
  c.event_id,
  c.theme_id,
  MIN(l.hero_slug, r.hero_slug) AS hero_a,
  MAX(l.hero_slug, r.hero_slug) AS hero_b,
  -- A draw has no winning_trio, so it counts as an observation and neither a win nor
  -- a loss - the same treatment the old `outcome = 'draw'` branch gave it.
  SUM(c.winning_trio IS NOT NULL
      AND (c.winning_trio = 1) =  (l.hero_slug < r.hero_slug))              AS a_wins,
  SUM(c.winning_trio IS NOT NULL
      AND (c.winning_trio = 1) <> (l.hero_slug < r.hero_slug))              AS b_wins,
  -- `tally` is a_wins - b_wins, computed rather than stored, so it can never
  -- disagree with the counts that produced it.
  SUM(CASE WHEN c.winning_trio IS NULL THEN 0
           WHEN (c.winning_trio = 1) = (l.hero_slug < r.hero_slug) THEN 1
           ELSE -1 END)                                                      AS tally,
  SUM(c.winning_trio IS NULL)                                              AS draws,
  SUM(c.winning_trio IS NOT NULL)                                          AS observations
FROM complete c
JOIN match_hero l ON l.match_id = c.id AND l.trio = 1 AND l.hero_slug IS NOT NULL
JOIN match_hero r ON r.match_id = c.id AND r.trio = 2 AND r.hero_slug IS NOT NULL
-- A mirror pick puts the same hero on both sides. "X vs X" is not a matchup.
WHERE l.hero_slug <> r.hero_slug
GROUP BY c.event_id, c.theme_id, hero_a, hero_b;
