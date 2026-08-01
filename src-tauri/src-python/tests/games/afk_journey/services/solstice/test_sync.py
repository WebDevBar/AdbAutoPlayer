"""Pooled sync: config, push, pull, and the rule that failure never costs a match."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
from adb_auto_player.games.afk_journey.services.solstice.sync import (
    DEFAULT_URL,
    SyncClient,
    SyncConfig,
)

REPO = Path(__file__).resolve().parents[7]
MIGRATE = REPO / "data" / "solstice_clash" / "migrate.py"
SHIPPED_DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A migrated COPY of the shipped database - never migrate the real one.

    A copy rather than an empty database because match_hero.hero_slug is a
    foreign key into `hero`; a schema-only database would reject every seeded
    slug.
    """
    target = tmp_path / "heroes.sqlite"
    shutil.copy(SHIPPED_DB, target)
    subprocess.run(
        [sys.executable, str(MIGRATE), str(target)], check=True, capture_output=True
    )
    return target


@pytest.fixture
def store(db):
    return MatchStore(db)


class FakeServer:
    """Stands in for the API. Records what it was sent."""

    def __init__(self):
        self.batches, self.pulls, self.calls = [], 0, []
        self.fail_push = self.fail_pull = False
        self.reject_reason = None
        self.rows = []
        self.themes = []

    def __call__(self, method, path, body=None):
        if path.endswith("/themes"):
            # Distinguished by PATH, not method: both this and the match pull are GETs,
            # and recording them under one name hides the order they happen in - which is
            # the thing the sync test exists to pin.
            self.calls.append("themes")
            return {"themes": self.themes}
        if method == "POST":
            self.calls.append("push")
            self.batches.append(body["matches"])
            if self.fail_push:
                return None
            results = []
            for m in body["matches"]:
                if self.reject_reason:
                    results.append({"index": m["index"], "status": "rejected",
                                    "reason": self.reject_reason})
                else:
                    results.append({
                        "index": m["index"], "status": "accepted",
                        "natural_key": f"sha256:server{m['index']:04d}",
                        "theme_slug": "converging-paths",
                        "theme_resolved_by": "window",
                    })
            return {"results": results}
        self.calls.append("pull")
        self.pulls += 1
        if self.fail_pull:
            return None
        return {"matches": self.rows, "next_cursor": 0}


@pytest.fixture
def client(store):
    cfg = SyncConfig(DEFAULT_URL, "k", True, 5.0)
    c = SyncClient(store, cfg, client_version="test")
    c._server = FakeServer()
    c._request = c._server
    return c


# --- configuration ---------------------------------------------------------

def test_defaults_to_the_production_url(monkeypatch):
    monkeypatch.delenv("ADB_SYNC_URL", raising=False)
    assert SyncConfig.load().base_url == DEFAULT_URL


def test_non_default_url_without_an_explicit_key_disables_sync(monkeypatch, caplog):
    """Otherwise one mistyped env var ships the baked key to any host."""
    monkeypatch.setenv("ADB_SYNC_URL", "https://elsewhere.example")
    monkeypatch.delenv("ADB_SYNC_KEY", raising=False)
    with caplog.at_level("WARNING"):
        cfg = SyncConfig.load()
    assert cfg.enabled is False
    assert "[SC-36]" in caplog.text


def test_non_default_url_with_an_explicit_key_is_allowed(monkeypatch):
    monkeypatch.setenv("ADB_SYNC_URL", "https://staging.example")
    monkeypatch.setenv("ADB_SYNC_KEY", "staging-key")
    assert SyncConfig.load().enabled is True


def test_sync_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ADB_SYNC_ENABLED", "false")
    assert SyncConfig.load().enabled is False


def test_timeout_defaults_to_fifteen_seconds(monkeypatch):
    monkeypatch.delenv("ADB_SYNC_TIMEOUT", raising=False)
    assert SyncConfig.load().timeout == 15


# --- push ------------------------------------------------------------------

def _seed_pushable(store, n=1):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        HeroSlot,
        MatchRecord,
    )

    ids = []
    for i in range(n):
        captured_at = f"2026-07-25T{i:02d}:10:00+00:00"
        # The event/theme have to be RESOLVED, not left NULL: the push gate is
        # `comps_key IS NOT NULL`, and `comps_key` is computed from the event
        # slug plus the two trios, so an unattributed row is never pushable.
        event_id, theme_id, resolved_by = store.resolve_theme(captured_at)
        mid = store.record_match(
            MatchRecord(source="spectate_summary",
                        captured_at=captured_at,
                        theme="Converging Paths", outcome="left",
                        outcome_source="observed",
                        event_id=event_id, theme_id=theme_id,
                        theme_resolved_by=resolved_by)
        )
        store.record_heroes(mid, [
            HeroSlot(side="left", slot=j, hero_slug=s, art_ref=None,
                     status="identified")
            for j, s in enumerate(("aliceth", "alna", "alsa"))
        ] + [
            HeroSlot(side="right", slot=j, hero_slug=s, art_ref=None,
                     status="identified")
            for j, s in enumerate(("antandra", "arden", "atalanta"))
        ])
        store.finalise_identity(mid)
        ids.append(mid)
    return ids


def test_push_selects_only_local_keyed_unpushed_rows(store, client):
    before = len(store.pushable_matches())
    _seed_pushable(store, 2)
    assert len(store.pushable_matches()) == before + 2


def test_push_payload_omits_natural_key_and_theme_slug(store, client):
    _seed_pushable(store, 1)
    client.push()
    row = client._server.batches[0][0]
    assert "natural_key" not in row
    assert "theme_slug" not in row
    assert row["theme_ocr"] == "Converging Paths"


def test_adoption_rewrites_the_key_then_marks_pushed(store, client):
    """The client must take the SERVER's identity, or it later pulls its own
    match back under a key matching nothing locally and stores it twice."""
    (mid,) = _seed_pushable(store, 1)
    client.push()
    row = _row(store, mid)
    assert row["natural_key"].startswith("sha256:server")
    assert row["pushed_at"] is not None


def test_a_rejected_row_stops_retrying_and_records_why(store, client):
    """Counted on the specific row, not a global total: the shipped database
    copy already holds the backfilled matches, so push() touches those too."""
    (mid,) = _seed_pushable(store, 1)
    client._server.reject_reason = "hero_slug 'xyz' unknown"
    client.push()

    row = _row(store, mid)
    assert row["push_rejected_reason"] == "hero_slug 'xyz' unknown"
    assert mid not in {r["local_id"] for r in store.pushable_matches()}


def test_a_transient_failure_leaves_rows_pushable(store, client):
    _seed_pushable(store, 1)
    client._server.fail_push = True
    before = len(store.pushable_matches())
    client.push()
    assert len(store.pushable_matches()) == before


def test_push_does_nothing_when_disabled(store, client):
    _seed_pushable(store, 1)
    client.enabled = False
    client.push()
    assert client._server.batches == []


# --- pull ------------------------------------------------------------------

def test_pull_sends_an_overlapped_cursor(store, client):
    store.set_pull_cursor(1234)
    client.pull()
    assert client._server.pulls == 1


def test_a_failed_pull_does_not_advance_the_cursor(store, client):
    store.set_pull_cursor(10)
    client._server.fail_pull = True
    client.pull()
    assert store.pull_cursor() == 10


def test_pull_inserts_with_origin_synced(store, client):
    client._server.rows = [{
        "seq": 5, "natural_key": "sha256:remote1", "source": "spectate_summary",
        "captured_at": "2026-07-25T05:00:00+00:00", "outcome": "left",
        "theme_slug": "converging-paths", "theme_resolved_by": "window",
        "contributor_uuid": "other-install", "remote_received_at": "2026-07-25T06:00:00Z",
        "heroes": [{"side": "left", "slot": 0, "hero_slug": "aliceth"}],
    }]
    client.pull()
    assert _row_by_key(store, "sha256:remote1")["origin"] == "synced"


def test_pull_of_an_already_known_match_is_a_no_op(store, client):
    client._server.rows = [{
        "seq": 5, "natural_key": "sha256:remote2", "source": "spectate_summary",
        "captured_at": "2026-07-25T05:00:00+00:00", "outcome": "left",
        "theme_slug": "converging-paths", "theme_resolved_by": "window",
        "heroes": [],
    }]
    assert client.pull() == 1
    assert client.pull() == 0


def test_synced_rows_are_never_pushed_back(store, client):
    """A client that echoes pulled rows makes everyone re-upload everyone else's
    data forever."""
    client._server.rows = [{
        "seq": 9, "natural_key": "sha256:remote3", "source": "spectate_summary",
        "captured_at": "2026-07-25T05:00:00+00:00", "outcome": "left",
        "theme_slug": "converging-paths", "theme_resolved_by": "window",
        "heroes": [],
    }]
    client.pull()
    assert all(r["local_id"] for r in store.pushable_matches())
    keys = {r for r in _all_keys(store, origin="synced")}
    pushable_keys = {r["local_id"] for r in store.pushable_matches()}
    assert not (keys & pushable_keys)


# --- manual trigger --------------------------------------------------------

def test_manual_sync_learns_the_themes_then_pushes_then_pulls(store, client):
    """Order matters twice over.

    Our rows reach the pool before we read it back - and the theme windows are learned
    before either, because a match pushed without them is filed under the event default
    on the SERVER too and stays that way. That is not hypothetical: the first rotation
    put every match pushed after it onto "unknown".
    """
    _seed_pushable(store, 1)
    client.sync_now()
    calls = [c for c in client._server.calls if c in ("themes", "push", "pull")]
    assert calls[:3] == ["themes", "push", "pull"]


# --- helpers ---------------------------------------------------------------

def _row(store, match_id):
    import sqlite3

    con = sqlite3.connect(_path(store))
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM match WHERE id=?", (match_id,)).fetchone()


def _row_by_key(store, key):
    import sqlite3

    con = sqlite3.connect(_path(store))
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM match WHERE natural_key=?", (key,)).fetchone()


def _all_keys(store, origin):
    import sqlite3

    con = sqlite3.connect(_path(store))
    return [r[0] for r in con.execute(
        "SELECT id FROM match WHERE origin=?", (origin,))]


def _path(store):
    for attr in ("_db_path", "_db", "db_path", "path"):
        if hasattr(store, attr):
            return str(getattr(store, attr))
    raise AssertionError("cannot locate the store's database path")
