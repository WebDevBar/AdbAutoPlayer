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

BATCH_LIMIT = 500      # server rejects a larger batch with 422
MAX_CHUNKS_PER_CYCLE = 3
PULL_LIMIT = 500
# Pull re-requests an overlap: `seq` is assigned at INSERT, so a transaction can
# commit AFTER a client has read past its value and that row would be skipped
# forever. Re-delivery is free because pull upserts by natural_key.
PULL_OVERLAP = 50
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
    except Exception:  # noqa: BLE001
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

    def __init__(self, store, config: SyncConfig | None = None,
                 client_version: str | None = None) -> None:
        self._store = store
        self._cfg = config or SyncConfig.load()
        self._client_version = client_version or _detect_client_version()
        self._auth_failures = 0
        self.enabled = self._cfg.enabled

    # -- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """Return the decoded response, or None on ANY failure.

        Never raises. That is the point: the caller is a collection loop.
        """
        url = f"{self._cfg.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self._cfg.api_key)
        req.add_header("X-Instance-Id", self._store.instance_uuid() or "")
        req.add_header("X-Client-Version", self._client_version)
        if data is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout) as resp:
                self._auth_failures = 0   # any success clears the streak
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
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
                logging.warning(f"[SC-33] sync server error {exc.code}")
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
                "schema_version": 4,
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
        """Fetch pooled matches. Returns how many were newly stored."""
        if not self.enabled:
            return 0

        since = max(0, self._store.pull_cursor() - PULL_OVERLAP)
        body = self._request("GET", f"/v1/matches?since={since}&limit={PULL_LIMIT}")
        if body is None:
            # Do NOT advance the cursor on failure - it is the only record of
            # what has been seen.
            return 0

        stored = 0
        highest = self._store.pull_cursor()
        for row in body.get("matches", []):
            if self._store.upsert_synced(row) is not None:
                stored += 1
            highest = max(highest, int(row.get("seq", 0)))
        self._store.set_pull_cursor(highest)

        if stored:
            logging.info(f"[SC-35] sync: pulled {stored} new")
        return stored

    def sync_now(self) -> None:
        """Manual trigger: push, then pull.

        Order matters - our own rows reach the pool before we read it back.
        """
        if self.push()[0] >= 0:
            self.pull()
