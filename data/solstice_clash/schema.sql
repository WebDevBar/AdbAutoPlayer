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
  side            TEXT,            -- 'blue' | 'red'
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
