# Tangent: better tooling for agentic file editing and development

**Status:** open question, raised 2026-07-26. Not blocking Phase 1.

## Why this came up

My editing workflow is: write a Python heredoc that does `s.replace(old, new)` with
`assert s.count(old) == 1` before each replacement. That exists because the built-in
line-based edit tool silently converts straight quotes to curly quotes in code files
(`~/Dev/.claude/rules/edit-tool-code-files.md`), which corrupts PHP/JS/Python.

The Python-replace workaround has its own failure modes, and three of them bit in one session:

1. **Heredoc nesting.** A replacement containing `PY` / `EOF` or nested triple-quoted strings
   terminates the heredoc early. Cost a broken command and a half-applied-looking state.
2. **Silent no-ops.** A replacement whose `old` does not match does nothing. Without the
   `assert`, it passes quietly - this actually happened (`hero_skin.wiki_icon` kept 45 stale
   rows because one replacement silently missed).
3. **No structural awareness.** "Add a field to this dataclass" or "rename this function"
   has to be expressed as exact whitespace-sensitive text.

## What is installed here

`rg`, `jq`, `bat`, `gh`, `tree`, `sqlite3`, `git`, `uv`, `pytest`, `codex`.

**Not** installed: `sd`, `ast-grep`, `comby`, `difftastic`, `delta`, `semgrep`, `watchexec`,
`entr`, `yq`, `miller`, `scc`, `hyperfine`, `shellcheck`.

⚠️ **`/usr/bin/sg` on this machine is the setgid binary, NOT ast-grep.** ast-grep also installs
as `sg` on some systems. Always invoke `ast-grep` by its full name here, and never assume `sg`
is the structural tool - running the wrong one could change process credentials.

## Candidates worth evaluating

| Tool | What it does better than what I use now |
|---|---|
| **ast-grep** | Structural search/rewrite over the AST via tree-sitter, 20+ languages. Expresses "rename this function", "add this field", "wrap these calls" without whitespace-exact text. Non-interactive (`ast-grep run -p ... -r ... -U`). Most-recommended for agents in current write-ups. |
| **git apply / patch** | Unified diffs are the format I already reason in, apply atomically, and **fail loudly** on context mismatch instead of no-opping. Needs no new dependency - `git` is already here. |
| **comby** | Structural match/rewrite without a per-language parser; also handles JSON/Markdown. Caveat: upstream warns it is deprecated and does not support OCaml 5 - verify before relying on it. |
| **sd** | Simpler, saner regex replace than `sed` (no escaping maze). Still line/regex-level, so it does not fix the structural gap. |
| **difftastic** | Syntax-aware diff - shows what actually changed structurally rather than line noise. Useful for verifying my own edits. |
| **watchexec / entr** | Re-run tests on file change; useful during a TDD loop. |
| **shellcheck** | Would have caught the `pkill -f grab.py` self-kill and the unprivileged-glob-under-sudo bug, both of which cost real time this session. |

## The most promising change, independent of any install

**Use `git apply` with a unified diff instead of Python string replacement.** It needs nothing
new installed, it is atomic, and critically it **errors on context mismatch** rather than
silently doing nothing - which directly kills failure mode #2 above. Heredoc nesting (#1) also
goes away if the diff is written to a file with the Write tool rather than embedded in a shell
heredoc.

That leaves only #3 (structural edits), which is what `ast-grep` would solve.

## Open questions to settle before changing the workflow

- Does `git apply` behave acceptably on files with mixed indentation / CRLF in this repo?
- Is `ast-grep` packaged for Fedora, or does it need cargo/npm? What does it install as here,
  given the `sg` collision?
- Is comby's deprecation warning a real blocker or cosmetic?
- Would a `--check` dry-run pass (e.g. `git apply --check`) be a good default before every edit?

## Next step

Nothing here blocks Phase 1. When picked up: install one tool at a time, verify on a scratch
copy, and only then use it on repo files. Do not batch-install - the point is to reduce edit
risk, not add unverified tools to the critical path.

---

## Codex research (2026-07-26)

### Recommended editing hierarchy

1. **Unified diff + `git apply --check` then `git apply`** - the default for non-interactive
   source edits. Quote-safe, heredoc-independent when the patch is written to a file, reviewable
   with `git diff`, and **fails exactly on context mismatch**. Failure mode: stale context, and a
   too-loose context can apply in a nearby wrong place - so always `--check` first, then inspect
   `git diff`, then run tests.
2. **LibCST for structural Python edits** - "rename this function", "add a field to this
   dataclass". Preserves formatting and comments, unlike stdlib `ast`.
3. **ast-grep** for structural search/rewrite across languages (tree-sitter). Not semantic - it
   does not understand imports or types, and patterns can miss equivalent code written
   differently.
4. **Python `.replace()`** only for tiny exact single-site edits, always with
   `assert s.count(old) == 1`.
5. **`sd`** as a better `sed`; still regex, not structural. Not for refactors.
6. **`comby`** - good idea, weaker practical choice now; upstream looks stale and Linux install
   is awkward versus ast-grep.

### Verified empirically here

```
git apply --check on mismatched context  ->  "error: patch does not apply"  (loud failure)
python s.replace() on the same mismatch  ->  silent no-op, file unchanged
```

That is exactly the failure that left 45 stale `hero_skin.wiki_icon` rows this session.

### Tools worth installing

| Group | Tool | Why | Install |
|---|---|---|---|
| editing | `ast-grep` | structural rewrite, 20+ languages | `cargo install ast-grep --locked` |
| editing | `libcst` | source-preserving Python codemods | `uv add --dev libcst` |
| editing | `sd` | sane regex replace (no escaping maze) | `cargo install sd` |
| search | `fd-find` | faster, cleaner `find` | `sudo dnf install fd-find` |
| testing | `pytest-xdist` | `pytest -n auto` parallelism | `uv add --dev pytest-xdist` |
| testing | `pytest-testmon` | run only tests affected by changed code | `uv add --dev pytest-testmon` |
| testing | `pytest-randomly` | catches order coupling | `uv add --dev pytest-randomly` |
| testing | `watchexec` | scriptable watch runner for TDD loops | `sudo dnf install watchexec` |
| diffing | `difftastic` | syntax-aware diff | `sudo dnf install difftastic` |
| diffing | `git-delta` | better pager for ordinary diffs | `sudo dnf install git-delta` |
| quality | `basedpyright` / `mypy` | static type checking | `uv add --dev basedpyright` |
| quality | `shellcheck` | would have caught this session's `pkill -f` self-kill and the unprivileged-glob-under-sudo bug | `sudo dnf install ShellCheck` |

`ruff` and `pytest-cov` are already project dependencies.

### Useful pytest selection commands (uv projects)

```bash
uv run pytest path/to/test.py::test_name     # single test
uv run pytest -k "name and not slow"         # by expression
uv run pytest -m "not network"               # by marker
uv run pytest -n auto                        # parallel (needs pytest-xdist)
uv run pytest --testmon                      # only what changed touched
uv run pytest --cov=src --cov-report=term-missing
```

## Recommendation

Adopt **unified diffs via `git apply`** as the default editing method immediately - it needs
nothing installed and removes the two failure modes that actually cost time this session.

Treat installs as a separate, deliberate step: one tool at a time, verified on a scratch copy
first. `ast-grep` and `shellcheck` look like the highest value; `libcst` if Python codemods
become frequent. Installing software needs its own go-ahead.
