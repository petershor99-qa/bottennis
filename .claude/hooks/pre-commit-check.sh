#!/bin/sh
# PreToolUse hook (Bash) — gates `git commit` on `pytest -q` + `ruff check .`,
# so a red test suite or a lint failure never lands in a commit. Fires only on
# commands that actually contain "git commit"; every other bash call (git
# status, git diff, plink, ls, ...) passes straight through with no
# subprocess spawned — the outer `case` below is a cheap substring filter on
# the raw JSON so we only pay for parsing it and running the checks when the
# command might plausibly be a commit.
#
# Committed to the repo — .claude/settings.json wires this in — so it travels
# with every `git pull`. No per-machine install step, but an ALREADY-RUNNING
# Claude Code session may need a restart (or "/hooks" once) to pick up a
# brand-new hooks config that didn't exist when the session started.

input=$(cat)

case "$input" in
  *"git commit"*)
    cmd=$(printf '%s' "$input" | py -3.13 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)
    case "$cmd" in
      *"git commit"*)
        if ! py -3.13 -m pytest -q >/tmp/claude-precommit-pytest.log 2>&1; then
          printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"pytest -q упал — коммит заблокирован. Смотри /tmp/claude-precommit-pytest.log, почини и закоммить заново."}}'
          exit 0
        fi
        if ! ruff check . >/tmp/claude-precommit-ruff.log 2>&1; then
          printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"ruff check упал — коммит заблокирован. Смотри /tmp/claude-precommit-ruff.log, почини и закоммить заново."}}'
          exit 0
        fi
        ;;
    esac
    ;;
esac
exit 0
