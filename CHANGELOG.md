# Changelog

## [Unreleased]

### Added

- **AFK Journey — Solstice Clash**: hero + event database built from the AFK Journey
  Fandom wiki API (`data/solstice_clash/`). 153 heroes, 732 skills, 173 skins across
  122 heroes, and the full event roster (95 usable + 24 banned) with per-hero stat
  adjustments parsed into integer columns. Regenerate with
  `python3 data/solstice_clash/build_hero_db.py`.
- **AFK Journey — Solstice Clash**: hero identification from wiki combat icons.
  `File:Hero <Name>.png` are the datamined in-game icons; the in-game 96px circular
  roster icon is a fixed crop (scale 0.68, offset 13,35, circular mask r=44).
  Verified 20/20 on hand-labelled icons and 8/8 against a 124-hero candidate set.
- **Docs**: `docs/solstice-clash/README.md` — project index covering the wiki API,
  screen geometry, database schema, failed approaches, and open items.

## [12.9.20] - 2026-07-03

### Bug Fixes

- **AFK Journey**:
  - **Guild Manager Scan**: Fixed RapidOCR silently returning no results due to an invalid `model_type` value (`small`) for the PP-OCRv4/PP-OCRv5 Chinese models; corrected to `mobile`.
  - **AFK Stages**: Fixed Season AFK Stages getting stuck on the "Are you sure you want to exit the game?" confirmation by reverting Battle Modes navigation to use the current overview instead of forcing navigation to the World view.
  - **Homestead**: Lowered the match threshold and enabled grayscale matching for Mine building card detection.
