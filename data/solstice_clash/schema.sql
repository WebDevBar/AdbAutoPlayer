-- AFK Journey / Solstice Clash database schema.
--
-- Applied by migrate.py, which is idempotent: it creates anything missing and
-- records the version in schema_version. Safe to run against an existing DB.
--
-- Two classes of data live here and they have DIFFERENT lifecycles:
--
--   WIKI-DERIVED  hero, hero_skin, hero_skill, solstice_roster
--                 Refreshed wholesale by build_hero_db.py on every run.
--
--   LOCALLY EARNED  hero.external_id, hero.game_icon, hero_alias, cell_registry,
--                   art_transform, library_config
--                   Measured/confirmed here and NOT reproducible from the wiki.
--                   build_hero_db.py must PRESERVE these across refreshes.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version(
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL,
  note        TEXT
);

-- ---------------------------------------------------------------- wiki-derived

CREATE TABLE IF NOT EXISTS hero(
  slug         TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  faction      TEXT,
  hero_class   TEXT,
  damage_type  TEXT,
  attack_range INTEGER,
  rarity       TEXT,
  title        TEXT,
  gender       TEXT,
  race         TEXT,
  -- icon sources. game_icon is PRIMARY (real in-game asset, see library_config.icon_priority)
  external_id  INTEGER,        -- the GAME's own hero id, from spui_herohead_<ID>.png
  game_icon    TEXT,           -- decoded from files/data/ui/icon/hero
  wiki_icon    TEXT,           -- Fandom File:Hero <Name>.png, fallback only
  icon_w       INTEGER,        -- verified per image; there are 8 distinct wiki sizes
  icon_h       INTEGER,
  last_seen    TEXT            -- set by build_hero_db.py; stale rows are reported, never deleted
);

CREATE TABLE IF NOT EXISTS hero_skin(
  id          INTEGER PRIMARY KEY,
  hero_slug   TEXT NOT NULL REFERENCES hero(slug),
  skin_name   TEXT NOT NULL,
  game_icon   TEXT,
  wiki_icon   TEXT,
  icon_w      INTEGER,
  icon_h      INTEGER,
  last_seen   TEXT,           -- stale rows reported, never deleted
  UNIQUE(hero_slug, skin_name)
);

CREATE TABLE IF NOT EXISTS hero_skill(
  id           INTEGER PRIMARY KEY,
  hero_slug    TEXT NOT NULL REFERENCES hero(slug),
  skill_type   TEXT,
  skill_name   TEXT,
  attack_range TEXT,
  cooldown     TEXT,
  energy       TEXT,
  summary      TEXT,
  detail       TEXT,
  UNIQUE(hero_slug, skill_name)
);

-- Event roster. A hero can appear in BOTH the wiki's usable and banned lists
-- (Zorya does) - usable must win, see build_hero_db.py.
CREATE TABLE IF NOT EXISTS solstice_roster(
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  hero_slug     TEXT,
  faction       TEXT,
  status        TEXT NOT NULL,      -- 'usable' | 'banned'
  adjustment    TEXT,               -- raw text, e.g. "HP -30% ATK +50%"
  hp_pct        INTEGER,
  atk_pct       INTEGER,
  phys_def_pct  INTEGER,
  magic_def_pct INTEGER,
  atk_spd_pct   INTEGER
);

-- ------------------------------------------------------------- locally earned

-- Alternate spellings: collab long names, "...New" suffixes, misspellings.
CREATE TABLE IF NOT EXISTS hero_alias(
  alias      TEXT PRIMARY KEY,
  hero_slug  TEXT NOT NULL REFERENCES hero(slug),
  source     TEXT
);

-- Screen regions, measured on raw 1080x1920 ADB frames (never rescaled screenshots).
CREATE TABLE IF NOT EXISTS cell_registry(
  id              INTEGER PRIMARY KEY,
  screen          TEXT NOT NULL,   -- 'prematch_locked_teams' | 'draft' | 'usable_heroes'
  cell_name       TEXT NOT NULL,
  cell_type       TEXT NOT NULL,   -- 'locked_pick' | 'draft_locked_pick' | 'draft_card'
  x0 INTEGER NOT NULL, y0 INTEGER NOT NULL,
  x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
  side            TEXT,            -- 'left' | 'right'
  slot            INTEGER,
  base_resolution TEXT NOT NULL DEFAULT '1080x1920',
  verified_at     TEXT,
  UNIQUE(screen, cell_name)
);

-- Per-art, per-cell-type transform. The three cell types have different aspect
-- ratios, so they need different recipes.
CREATE TABLE IF NOT EXISTS art_transform(
  id          INTEGER PRIMARY KEY,
  art_ref     TEXT NOT NULL,       -- hero name or skin name
  art_kind    TEXT NOT NULL,       -- 'base' | 'skin'
  hero_slug   TEXT NOT NULL REFERENCES hero(slug),
  cell_type   TEXT NOT NULL,
  scale       REAL NOT NULL,
  off_x       INTEGER NOT NULL,
  off_y       INTEGER NOT NULL,
  score       REAL,
  source      TEXT,                -- capture the calibration came from
  verified_at TEXT,
  UNIQUE(art_ref, art_kind, cell_type)
);

-- Tunables measured against real frames. Change these, not the code.
CREATE TABLE IF NOT EXISTS library_config(
  key   TEXT PRIMARY KEY,
  value TEXT,
  note  TEXT
);

CREATE INDEX IF NOT EXISTS idx_hero_faction   ON hero(faction);
CREATE INDEX IF NOT EXISTS idx_hero_external  ON hero(external_id);
CREATE INDEX IF NOT EXISTS idx_skill_hero     ON hero_skill(hero_slug);
CREATE INDEX IF NOT EXISTS idx_roster_status  ON solstice_roster(status);
CREATE INDEX IF NOT EXISTS idx_cell_type      ON cell_registry(cell_type);
CREATE INDEX IF NOT EXISTS idx_transform_cell ON art_transform(cell_type);

-- ------------------------------------------------- match data (locally earned)
-- build_hero_db.py must NEVER touch these tables.

CREATE TABLE IF NOT EXISTS match(
  id             INTEGER PRIMARY KEY,
  -- NULLABLE on purpose. A match observed mid-draft has no stable key yet (unknown
  -- heroes, no outcome). Rows with a NULL key are local-only and excluded from any
  -- future sync; the key is set once enough stable facts exist. SQLite permits many
  -- NULLs in a UNIQUE column, which is exactly what we want.
  natural_key    TEXT UNIQUE,
  source         TEXT NOT NULL,          -- 'compete' | 'spectate'
  captured_at    TEXT NOT NULL,
  -- The RAW OCR read, kept for provenance. It is NOT the source of truth: theme_id
  -- below is, because a name resolved from the capture time survives a misread.
  theme          TEXT,
  event_id       INTEGER REFERENCES event(id) ON DELETE SET NULL,
  theme_id       INTEGER REFERENCES theme(id) ON DELETE SET NULL,
  balance_epoch  TEXT,                   -- hash of roster adjustments at capture time
  left_player    TEXT, left_rating INTEGER, left_rank INTEGER,
  right_player   TEXT, right_rating INTEGER, right_rank INTEGER,
  outcome        TEXT,                   -- 'left' | 'right' | 'draw' | NULL
  outcome_source TEXT
);

-- ---------------------------------------------------------------------------
-- Events and themes
--
-- Written so this mode can be lifted wholesale for the NEXT event of this shape,
-- not just Solstice Clash. An event is the container; a theme is a dated variation
-- within it (different map, different roster balance). Events without variations
-- still get exactly one theme row, flagged is_default, so every match resolves the
-- same way and no caller needs a special case.
--
-- Themes carry DATE WINDOWS because that is what makes the assignment trustworthy.
-- The theme name is read by OCR off the event screen; one bad read used to be
-- enough to file a match under the wrong balance patch, and match data is only
-- comparable WITHIN a theme. Resolving by captured_at instead means a match lands
-- in the right window even if the name was misread or never read at all.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event(
  id          INTEGER PRIMARY KEY,
  slug        TEXT NOT NULL UNIQUE,     -- 'solstice-clash'
  name        TEXT NOT NULL,            -- 'Solstice Clash'
  game        TEXT NOT NULL DEFAULT 'afk-journey',
  notes       TEXT
);

CREATE TABLE IF NOT EXISTS theme(
  id          INTEGER PRIMARY KEY,
  event_id    INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  slug        TEXT NOT NULL,            -- 'converging-paths'
  name        TEXT NOT NULL,            -- 'Converging Paths' as shown in game
  -- Inclusive start, EXCLUSIVE end, both ISO-8601 UTC. NULL end = still open.
  starts_at   TEXT,
  ends_at     TEXT,
  -- Exactly one per event may be the fallback for matches whose capture time
  -- falls outside every dated window, and the only theme for events with no
  -- variations at all.
  is_default  INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
  notes       TEXT,
  UNIQUE(event_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_theme_event   ON theme(event_id);
CREATE INDEX IF NOT EXISTS idx_theme_window  ON theme(starts_at, ends_at);

CREATE TABLE IF NOT EXISTS match_hero(
  id            INTEGER PRIMARY KEY,
  match_id      INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
  side          TEXT NOT NULL,           -- 'left' | 'right'
  slot          INTEGER NOT NULL,
  hero_slug     TEXT REFERENCES hero(slug),
  art_ref       TEXT,
  status        TEXT NOT NULL,           -- 'identified' | 'unknown'
  score         REAL,
  margin        REAL,
  -- Provenance. Without it a bad pool read and a legitimate out-of-pool recovery are
  -- indistinguishable forever, and a failed identification cannot be relabelled by hand.
  cell_type       TEXT,
  cell_name       TEXT,
  candidate_scope TEXT,                  -- 'pool' | 'full_library'
  pool_miss       INTEGER,               -- 1 = pool tier failed, full library used
  runner_up_slug  TEXT,
  runner_up_score REAL,
  crop_path       TEXT,                  -- saved cell crop, for relabelling
  frame_path      TEXT,                  -- source frame, for re-measuring geometry
  stat_sword      INTEGER,               -- summary column 1, sword icon
  stat_heart      INTEGER,               -- summary column 2, heart icon
  stat_shield     INTEGER,               -- summary column 3, shield icon
  power           INTEGER,               -- from the long-press popup only
  identified_by   TEXT,                  -- 'image' | 'longpress_ocr'
  UNIQUE(match_id, side, slot)
);

-- The 20 heroes on offer and which were banned. "Available but not picked" is a real
-- signal, and a pick absent from the pool is a detected error. Computing this and
-- discarding it would be the most expensive omission to retrofit.
CREATE TABLE IF NOT EXISTS match_pool(
  id            INTEGER PRIMARY KEY,
  match_id      INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
  slot          INTEGER NOT NULL,        -- 1..20, row-major in the 5x4 grid
  hero_slug     TEXT REFERENCES hero(slug),
  art_ref       TEXT,
  status        TEXT NOT NULL,           -- 'identified' | 'unknown' | 'banned'
  banned        INTEGER NOT NULL DEFAULT 0,
  score         REAL,
  margin        REAL,
  runner_up_slug  TEXT,
  runner_up_score REAL,
  crop_path       TEXT,
  frame_path      TEXT,
  UNIQUE(match_id, slot)
);

CREATE TABLE IF NOT EXISTS match_odds(
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
  sampled_at  TEXT NOT NULL,
  left_pool   INTEGER, right_pool INTEGER,
  left_odds   REAL,    right_odds REAL,
  spectators  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_match_hero_match ON match_hero(match_id);
CREATE INDEX IF NOT EXISTS idx_match_pool_match ON match_pool(match_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_match ON match_odds(match_id);
CREATE INDEX IF NOT EXISTS idx_match_outcome    ON match(outcome);

-- ---------------------------------------------------------------------------
-- Schema v3: screen registry, identification audit trail, learned transforms.
-- ---------------------------------------------------------------------------

-- Named screens. cell_registry.cell_type stays as-is; this table adds the
-- screen-level crop defaults that a per-hero transform can override.
CREATE TABLE IF NOT EXISTS screen(
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  description     TEXT NOT NULL,
  base_resolution TEXT NOT NULL,
  crop_half_w     INTEGER,
  crop_top        INTEGER,
  crop_bottom     INTEGER
);

-- One row per identified cell, agreements included. Agreements are what make the
-- false-positive RATE computable: recording only misfires gives a numerator with no
-- denominator.
--
-- match_id is ON DELETE SET NULL, NOT CASCADE. hero_screen_transform.audit_id is
-- NOT NULL, so cascading a match delete into its audit rows makes SQLite abort the
-- delete with FOREIGN KEY constraint failed as soon as any transform has been learned
-- from that match. Audit rows are evidence about identification, not about the match,
-- so they outlive it.
CREATE TABLE IF NOT EXISTS identification_audit(
  id            INTEGER PRIMARY KEY,
  match_id      INTEGER REFERENCES match(id) ON DELETE SET NULL,
  screen_id     INTEGER NOT NULL REFERENCES screen(id),
  side          TEXT NOT NULL,
  slot          INTEGER NOT NULL,
  image_slug    TEXT,
  image_art_ref TEXT,
  image_score   REAL NOT NULL,
  image_margin  REAL NOT NULL,
  ocr_slug      TEXT,
  agreed        INTEGER NOT NULL,
  frame_path    TEXT,
  created_at    TEXT NOT NULL,
  CHECK(agreed IN (0, 1)),
  -- 'agreed' must MEAN what it says. Without this a row could claim agreed=1 while the
  -- two channels disagree, and the trigger below would accept it as confirmation.
  CHECK(agreed = 0 OR (image_slug IS NOT NULL
                       AND ocr_slug IS NOT NULL
                       AND image_slug = ocr_slug))
);

CREATE TABLE IF NOT EXISTS hero_screen_transform(
  id            INTEGER PRIMARY KEY,
  screen_id     INTEGER NOT NULL REFERENCES screen(id),
  hero_slug     TEXT NOT NULL REFERENCES hero(slug),
  art_ref       TEXT NOT NULL,
  scale         REAL NOT NULL,
  -- NULL means "use the screen default". Measured: the optimum crop differs per hero.
  crop_half_w   INTEGER,
  crop_top      INTEGER,
  crop_bottom   INTEGER,
  score         REAL NOT NULL,
  margin        REAL NOT NULL,
  confirmed_by  TEXT NOT NULL CHECK(confirmed_by IN ('longpress_ocr')),
  audit_id      INTEGER NOT NULL REFERENCES identification_audit(id),
  verified_at   TEXT NOT NULL,
  UNIQUE(screen_id, hero_slug, art_ref)
);

-- NOT NULL only proves an audit row exists. It cannot prove the row agrees, names this
-- hero, or came from this screen, and SQLite CHECK cannot reference another table.
CREATE TRIGGER IF NOT EXISTS hero_screen_transform_confirm_insert
BEFORE INSERT ON hero_screen_transform
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'transform requires an agreeing OCR-confirmed audit row for the same hero and screen')
  WHERE NOT EXISTS (
    SELECT 1 FROM identification_audit a
    WHERE a.id = NEW.audit_id AND a.agreed = 1
      AND a.ocr_slug = NEW.hero_slug AND a.screen_id = NEW.screen_id
  );
END;

-- Re-tuning is an UPDATE. A BEFORE INSERT trigger alone would hold on the first write
-- and leak on every subsequent one.
CREATE TRIGGER IF NOT EXISTS hero_screen_transform_confirm_update
BEFORE UPDATE ON hero_screen_transform
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'transform update requires an agreeing OCR-confirmed audit row for the same hero and screen')
  WHERE NOT EXISTS (
    SELECT 1 FROM identification_audit a
    WHERE a.id = NEW.audit_id AND a.agreed = 1
      AND a.ocr_slug = NEW.hero_slug AND a.screen_id = NEW.screen_id
  );
END;
