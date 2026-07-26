# Changelog

## [Unreleased]

### Added

- **AFK Journey - Solstice Clash Phase 1**: the vision and storage layer that turns a
  1080x1920 screenshot into identified heroes and database rows.
  - `config.py` - cell geometry, scale chains and accept thresholds read from
    `heroes.sqlite`; no hardcoded geometry or tunables in the package.
  - `icons.py` - decodes the game's own hero art. Files named `*.png` are an `AST`
    header wrapping LZ4-block-compressed ASTC 6x6; dimensions come from the header and
    the decode needs a vertical flip (Unity origin is bottom-left). Gamma 1/1.8 applied
    at library-build time.
  - `vision.py` - template-anchored screen classification, cell extraction, hero
    identification with the `score >= 0.70 AND margin >= 0.10` rule, ban-glyph detection,
    and pool capture with a two-tier candidate strategy.
  - `store.py` + schema v2 - `match`, `match_hero`, `match_pool`, `match_odds`.
  - Measured: 18/18 draft cells and 6/6 locked picks correct, all above their score
    floors; ban detection finds exactly the two banned slots.
  - Adds `lz4` and `texture2ddecoder`; registers the `network` pytest marker.

## [12.9.20] - 2026-07-03

### Bug Fixes

- **AFK Journey**:
  - **Guild Manager Scan**: Fixed RapidOCR silently returning no results due to an invalid `model_type` value (`small`) for the PP-OCRv4/PP-OCRv5 Chinese models; corrected to `mobile`.
  - **AFK Stages**: Fixed Season AFK Stages getting stuck on the "Are you sure you want to exit the game?" confirmation by reverting Battle Modes navigation to use the current overview instead of forcing navigation to the World view.
  - **Homestead**: Lowered the match threshold and enabled grayscale matching for Mine building card detection.
