"""Talking to the pooled match server.

THE GOVERNING RULE: sync must never cost a match.

Collection is the valuable thing; sync is an optimisation on top of it. A dead
endpoint, a timeout, an expired certificate, a rate limit, a 500 - none of them
are a reason to stop gathering data, and none may propagate into the collection
loop. Every consequence below follows from that.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "https://gameretro.net/adb"


def _builtin_key() -> str:
    """The fork key baked in at build time.

    build-rpm.sh writes `_forkkey.py`, which is gitignored - the key must never
    live in the repository. A development checkout has no such module and falls
    back to the environment, so sync is simply disabled unless a key is given.

    Not real authentication: it ships inside a binary handed to contributors, so
    it is extractable by anyone who looks. It stops drive-by traffic and nothing
    more. What protects the pool is that every row is attributable and one
    install can be revoked without touching anyone else.
    """
    try:
        from ._forkkey import FORK_API_KEY as baked  # type: ignore[import-not-found]

        return baked
    except ImportError:
        return os.environ.get("ADB_SYNC_KEY_BUILTIN", "")


FORK_API_KEY = _builtin_key()

BATCH_LIMIT = 500  # server rejects a larger batch with 422
MAX_CHUNKS_PER_CYCLE = 3
PULL_LIMIT = 500
# Pull re-requests an overlap: `seq` is assigned at INSERT, so a transaction can
# commit AFTER a client has read past its value and that row would be skipped
# forever. Re-delivery is free because pull upserts by natural_key.
PULL_OVERLAP = 50

# How much of an error response to keep. A 422 body names the exact field that failed
# validation, which is the difference between "sync server error 422" and an answer -
# a collaborator's log carried 75 of the former and diagnosed nothing.
HTTP_ERROR_BODY_LIMIT = 2048
AUTH_FAILURES_BEFORE_DISABLE = 3


def _detect_client_version() -> str:
    """Best-effort build identifier for the X-Client-Version header.

    Reported so the server can tell which build a contributor is running - a
    stale build submitting wrong data is the realistic failure here, not an
    attacker. Never fatal: an unknown version is worth less than a crash.
    """
    try:
        from importlib.metadata import version

        return version("adb-auto-player")
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class SyncConfig:
    base_url: str
    api_key: str
    enabled: bool
    timeout: float

    @classmethod
    def load(cls) -> SyncConfig:
        url = os.environ.get("ADB_SYNC_URL", DEFAULT_URL).rstrip("/")
        key_override = os.environ.get("ADB_SYNC_KEY")
        enabled = os.environ.get("ADB_SYNC_ENABLED", "true").lower() != "false"
        timeout = float(os.environ.get("ADB_SYNC_TIMEOUT", "15"))

        # A non-default URL REQUIRES an explicit key. Otherwise one mistyped env
        # var ships the baked fork key, the install UUID and everyone's match
        # data to an arbitrary host - the client would happily authenticate to
        # the wrong server.
        if url != DEFAULT_URL and not key_override:
            logging.warning(
                "[SC-36] ADB_SYNC_URL is not the default and ADB_SYNC_KEY is unset - "
                "sync disabled rather than send the built-in key to another host"
            )
            enabled = False

        return cls(url, key_override or FORK_API_KEY, enabled, timeout)


class SyncClient:
    """Push local matches to the pool and pull everyone else's back."""

    def __init__(
        self, store, config: SyncConfig | None = None, client_version: str | None = None
    ) -> None:
        self._store = store
        self._cfg = config or SyncConfig.load()
        self._client_version = client_version or _detect_client_version()
        self._auth_failures = 0
        self.enabled = self._cfg.enabled

    # -- transport ---------------------------------------------------------

    def _error_detail(self, exc: urllib.error.HTTPError) -> str:
        """A bounded, single-line, key-redacted view of an error response body.

        FastAPI's 422 body names the field that failed validation - `matches.0.left_odds`
        and the reason - and we were discarding it, so a blanket rejection looked
        identical to any other server error. One contributor pushed nothing for a day
        behind 75 undiagnosable 422s.

        Never raises: diagnostics must not be able to break collection.

        Args:
            exc: The HTTPError just caught.

        Returns:
            The body as one line, truncated, with the API key masked. Empty on any
            failure to read it.
        """
        try:
            raw = exc.read(HTTP_ERROR_BODY_LIMIT + 1)
        except Exception:
            return ""
        truncated = len(raw) > HTTP_ERROR_BODY_LIMIT
        text = " ".join(raw[:HTTP_ERROR_BODY_LIMIT].decode("utf-8", "replace").split())
        # A proxy or a misconfigured server can echo the request back. The log is
        # something people paste into chat.
        if self._cfg.api_key:
            text = text.replace(self._cfg.api_key, "[REDACTED]")
        return text + ("..." if truncated else "")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """Return the decoded response, or None on ANY failure.

        Never raises. That is the point: the caller is a collection loop.
        """
        # Built INSIDE the try. `instance_uuid()` opens SQLite and `json.dumps` can
        # raise on an unserialisable payload - both were outside it, so a function
        # documented as never raising could raise on a locked database.
        try:
            url = f"{self._cfg.base_url}{path}"
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("X-API-Key", self._cfg.api_key)
            req.add_header("X-Instance-Id", self._store.instance_uuid() or "")
            req.add_header("X-Client-Version", self._client_version)
            if data is not None:
                req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=self._cfg.timeout) as resp:
                self._auth_failures = 0  # any success clears the streak
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = self._error_detail(exc)
            if exc.code in (401, 403):
                self._auth_failures += 1
                logging.warning(
                    f"[SC-31] sync auth rejected ({exc.code}), "
                    f"failure {self._auth_failures}/{AUTH_FAILURES_BEFORE_DISABLE}"
                )
                # A threshold, not a hair trigger: a revoked instance will never
                # fix itself, but disabling on the FIRST 401 means a proxy hiccup
                # or a mid-deploy restart silently kills sync for a twelve-hour
                # overnight run.
                if self._auth_failures >= AUTH_FAILURES_BEFORE_DISABLE:
                    self.enabled = False
                    logging.warning("[SC-31] sync disabled for this run")
            elif exc.code == 429:
                logging.warning("[SC-32] sync rate limited - skipping this cycle")
            else:
                suffix = f": {detail}" if detail else ""
                logging.warning(f"[SC-33] sync server error {exc.code}{suffix}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logging.warning(f"[SC-30] sync unreachable: {exc}")
            return None
        except (ValueError, json.JSONDecodeError) as exc:
            logging.warning(f"[SC-34] sync response was not valid JSON: {exc}")
            return None

    # -- push --------------------------------------------------------------

    def push(self) -> tuple[int, int, int]:
        """Send the backlog in chunks. Returns (accepted, duplicate, rejected)."""
        if not self.enabled:
            return (0, 0, 0)

        accepted = duplicates = rejected = 0
        for _ in range(MAX_CHUNKS_PER_CYCLE):
            rows = self._store.pushable_matches(limit=BATCH_LIMIT)
            if not rows:
                break

            payload = {
                # 5: trios, no sides, no player names. The server accepts both for
                # one release, so an older client is not stranded mid-rollout.
                "schema_version": 5,
                "matches": [
                    {k: v for k, v in dict(r, index=i).items() if k != "local_id"}
                    for i, r in enumerate(rows)
                ],
            }
            body = self._request("POST", "/v1/matches", payload)
            if body is None:
                # A failed push is NOT a rejection. The rows are still good and
                # must stay unpushed - marking them sent on a timeout would drop
                # them from the pool forever.
                break

            for result in body.get("results", []):
                row = rows[result["index"]]
                status = result.get("status")
                if status in ("accepted", "duplicate"):
                    self._store.adopt_canonical(
                        row["local_id"],
                        result["natural_key"],
                        result.get("theme_slug"),
                        result.get("theme_resolved_by"),
                    )
                    accepted += status == "accepted"
                    duplicates += status == "duplicate"
                elif status == "rejected":
                    self._store.mark_push_rejected(
                        row["local_id"], result.get("reason", "rejected")
                    )
                    rejected += 1

            if len(rows) < BATCH_LIMIT:
                break

        if accepted or duplicates or rejected:
            logging.info(
                f"[SC-35] sync: pushed {accepted}, duplicate {duplicates}, "
                f"rejected {rejected}"
            )
        return (accepted, duplicates, rejected)

    # -- pull --------------------------------------------------------------

    def pull(self) -> int:
        """Fetch pooled matches and tombstones. Returns how many were newly stored.

        TWO cursors, moved independently. Marking a match superseded does not
        advance its `seq`, so supersessions ride their own server sequence; driving
        them from the match cursor would either re-read page one forever or, once
        the match cursor ran ahead, permanently miss retirements published behind
        it.
        """
        if not self.enabled:
            return 0

        since = max(0, self._store.pull_cursor() - PULL_OVERLAP)
        supersession_since = self._store.supersession_cursor()
        body = self._request(
            "GET",
            # The version is sent EXPLICITLY. It used to send none at all, which is
            # why an absent one has to mean 4 on the server - that default exists for
            # clients that predate this line, not for us.
            f"/v1/matches?since={since}&limit={PULL_LIMIT}"
            f"&supersession_since={supersession_since}"
            f"&schema_version=5",
        )
        if body is None:
            # Do NOT advance either cursor on failure - they are the only record
            # of what has been seen.
            return 0

        stored = 0
        highest = self._store.pull_cursor()
        for row in body.get("matches", []):
            if self._store.upsert_synced(row) is not None:
                stored += 1
            highest = max(highest, int(row.get("seq", 0)))
        self._store.set_pull_cursor(highest)

        retired = self._apply_supersessions(body, supersession_since)

        if stored or retired:
            logging.info(f"[SC-35] sync: pulled {stored} new, retired {retired}")
        return stored

    def _apply_supersessions(self, body: dict, since: int) -> int:
        """Consume the tombstone page and advance ITS cursor. Never raises.

        Args:
            body: The decoded pull response.
            since: The supersession cursor we sent, used as the floor so a server
                that omits or garbles its own cursor cannot rewind us.

        Returns:
            How many local rows were retired.
        """
        retired = 0
        for entry in body.get("superseded") or []:
            key = (entry or {}).get("natural_key")
            if not key:
                continue
            match_id = self._store.match_by_natural_key(key)
            if match_id is not None and self._store.retire_for_tombstone(match_id):
                retired += 1

        cursor = body.get("supersession_cursor")
        if cursor is not None:
            try:
                self._store.set_supersession_cursor(max(since, int(cursor)))
            except (TypeError, ValueError):
                logging.warning(
                    f"[SC-38] ignoring unusable supersession cursor {cursor!r}"
                )
        return retired

    def pull_themes(self) -> tuple[int, int]:
        """Adopt the pool's theme windows, then re-file matches they now cover.

        Themes rotate on a schedule the server is told about and a client is not. A
        client that does not know a window files its matches under the event default -
        which is not cosmetic, because the model conditions on theme and now discards
        other themes' matches outright rather than discounting them. A match filed under
        "unknown" is a match no model will ever use.

        That is not hypothetical: at the first rotation this install put 14 matches on
        "unknown", and so did every match pushed to the pool after it. Two seeds, one of
        them stale, is what caused it - so the windows are pooled like the matches are.

        Only NULL boundaries are filled. A boundary this install already recorded is
        never overwritten, because that would silently re-file matches already attributed
        to a theme.

        Returns:
            (boundaries filled, matches re-filed).
        """
        if not self.enabled:
            return (0, 0)
        payload = self._request("GET", "/v1/themes")
        if not payload:
            return (0, 0)
        filled = self._store.adopt_theme_windows(payload.get("themes", []))
        refiled = self._store.refile_default_themes()
        if filled or refiled:
            logging.info(
                f"[SC-37] theme windows: {filled} filled, {refiled} match(es) re-filed"
            )
        return (filled, refiled)

    def sync_now(self) -> None:
        """Manual trigger: push, then pull.

        Order matters - our own rows reach the pool before we read it back.

        Themes first: a match pushed before the window is known is filed under the
        default and stays that way on the server, so learning the boundaries has to
        happen before anything is sent.
        """
        self.pull_themes()
        if self.push()[0] >= 0:
            self.pull()
