#!/bin/sh
# PreToolUse hook (Bash) для ZCode — gates `git commit` on `pytest -q` + `ruff check .`,
# so a red test suite or a lint failure never lands in a commit. Fires only on
# commands that actually contain "git commit"; every other bash call (git
# status, git diff, ls, ...) passes straight through with no subprocess
# spawned — the outer `case` below is a cheap substring filter on the raw JSON.
#
# ZCode wiring: .zcode/config.json → hooks.events.PreToolUse (needs
# hooks.enabled: true). Committed to the repo, so it travels with every
# `git pull`. A running session may need a restart to pick up hooks config.
#
# Deny signal: exit code 2 + reason on stderr (documented ZCode contract:
# 0 passes, 2 blocks). Stdout stays empty so the strict hook-output JSON
# schema can never reject us. The tool_input.command parse fails open: if
# the input shape ever changes, `cmd` is empty and the commit is allowed.

input=$(cat)

case "$input" in
  *"git commit"*)
    cmd=$(printf '%s' "$input" | py -3.13 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)
    case "$cmd" in
      *"git commit"*)
        # Tests run in the actual repo: cwd if it is a git repo, else the
        # standard D: clone (sessions live in the G: Drive folder — no code
        # there by design).
        repo=$(git rev-parse --show-toplevel 2>/dev/null)
        [ -z "$repo" ] && repo=/d/claude/bottennis
        [ -d "$repo" ] || exit 0
        cd "$repo" || exit 0
        if ! py -3.13 -m pytest -q >/tmp/zcode-precommit-pytest.log 2>&1; then
          printf 'pytest -q упал — коммит заблокирован. Смотри /tmp/zcode-precommit-pytest.log, почини и закоммить заново.' >&2
          exit 2
        fi
        if ! py -3.13 -m ruff check . >/tmp/zcode-precommit-ruff.log 2>&1; then
          printf 'ruff check упал — коммит заблокирован. Смотри /tmp/zcode-precommit-ruff.log, почини и закоммить заново.' >&2
          exit 2
        fi
        ;;
    esac
    ;;
esac
exit 0
