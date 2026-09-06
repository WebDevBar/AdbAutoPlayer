# Fork notes - WebDevBar/AdbAutoPlayer

**Nothing here goes upstream. Not now, not later.** This is a private fork of
[AdbAutoPlayer/AdbAutoPlayer](https://github.com/AdbAutoPlayer/AdbAutoPlayer) (MIT) built for one
operator's purposes, and it is not a contribution path in either direction of intent:

- We do not open pull requests upstream.
- We would not expect them to be accepted if we did. The work here is specific to how we run the
  bot - a shared match pool with its own server, an odds model, an in-game overlay, a headless AVD
  on a home server - and none of that belongs in a general-purpose tool.
- Upstream owes us nothing and is not consulted about anything in this repo.

What we DO is pull upstream's work in, because it is good and we want it.

## The one-way flow

| We do | We do not |
|---|---|
| `git fetch upstream && git merge upstream/main` | Open a PR against `AdbAutoPlayer/AdbAutoPlayer` |
| Keep our work on `webdevbar` | Push any branch upstream |
| Take upstream's fixes and heroes | Ask upstream to take ours |

```bash
git remote add upstream https://github.com/AdbAutoPlayer/AdbAutoPlayer.git
git fetch upstream
git rev-list --left-right --count upstream/main...HEAD   # behind <tab> ahead
git merge upstream/main
```

## This is not a GitHub fork, and that is fine

The API reports `fork: false` with `parent: null`: the repo was created standalone and pushed
rather than made with the Fork button. So there is **no Sync-fork button** and no cross-repo
compare - `gh api repos/WebDevBar/AdbAutoPlayer/compare/AdbAutoPlayer:main...webdevbar` returns
404.

That costs two buttons in a web UI and nothing else. Every merge and every drift measurement runs
off the `upstream` remote above.

A real fork, `todor-wdb/adb-ap`, was created 2026-08-25 to get those buttons back. It was
**archived 2026-09-06**: no work ever happened there, it fell 388 commits behind, and moving
development to it would have meant re-pointing every dependabot branch, the fork release line, and
every clone and script. Its history stays readable, which matters because its `AFKJourney.toml`
holds vault-restored settings that differ from the live ones.

## Versioning

Upstream's version says which release our work sits on. It does not say which of OUR builds you
are running, and those differ by a lot within one upstream version. So the fork carries its own
release number in `src-tauri/src-python/adb_auto_player/wdb_version.py`, and everything else reads
it: tags are `wdb-<upstream>-<release>`, e.g. `wdb-12.12.0-35`.

Bump `WDB_RELEASE` when cutting a build. `build-rpm.sh` and the Linux bundle config follow it.
