---
name: release
description: >
  Drives the full ship-a-feature workflow for the bottennis Telegram bot repo
  (F:\projects\bottenis): run tests+lint, open/merge the feature PR into develop,
  sync RELEASE_NOTES.md/README.md/TESTING.md/CLAUDE.md, open/merge the develop->main
  release PR, verify the deploy actually landed on the VPS over SSH, and record that
  in CLAUDE.md. Use this whenever work in this repo is ready to ship: the user says
  "запушь", "смёржи", "открой PR", "задеплой", "готово, давай в прод", "проверь что
  задеплоилось", or you yourself have just finished implementing a feature and the
  tests are green. Also use it just for the deploy-verification tail end when the
  user says something like "задеплоил" or "проверь прод" after a main merge. Trigger
  even on short/imperative phrasings like "мёржи" or "в прод" in this repo's context.
---

# Releasing a bottennis feature

This skill is the executable form of CLAUDE.md's own "Флоу: добавить новую фичу"
section. That section is the source of truth for *policy* (branch names, who merges
what); this skill is the source of truth for *mechanics* (exact commands, what order,
what not to forget). If the two ever disagree, CLAUDE.md wins — re-read its "⚙️
Репозиторий и рабочий процесс" section at the start of a run in case it changed since
this skill was written.

## Before anything: never let secrets touch this repo

`bottennis` is a **public** GitHub repo. The VPS host, IP, and SSH password must
never appear in any file this skill writes or edits — not in CLAUDE.md, not in a
commit message, not in a PR description, not in this skill file itself. When step 9
below needs them, pull them from your own memory (the `reference_vps_access.md`
memory note, or wherever your VPS-access reference lives for this project) at
execution time — never hardcode them here, never paste them into a repo file "for
documentation." This is the one mistake in this whole flow that's expensive to
undo (secret-scanning bots index public repos fast), so slow down at step 9
specifically and double-check any file you're about to commit doesn't quote them.

## Figure out where you're starting from

Don't assume you're at step 1. Run `git status` and `git log --oneline -5` first and
place yourself:
- Uncommitted changes, no feature branch yet → start at step 1.
- Already on a `feature/*` branch with committed work → skip to step 2.
- Feature PR already merged to `develop` → skip to step 4 (docs sync).
- Docs already synced, ready for prod → skip to step 5.
- User just said "задеплоил"/"проверь" after a main merge → jump straight to step 9.

## Step 1 — Tests and lint gate everything

```bash
py -3.13 -m pytest -q
ruff check .
```

Both must be clean before anything below happens. If either is red, stop and fix —
don't open a PR on red, don't tell the user it's ready. Note the passing test count
from pytest's `N passed` line; you'll need it for the docs sync.

## Step 2 — Feature branch, commit, push

If not already on one:
```bash
git checkout develop && git pull
git checkout -b feature/<short-kebab-description>
```
Then:
```bash
git add -A
git commit -m "feat: <what, in Russian, matching the project's existing commit style>

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push -u origin feature/<name>
```
Match the terse, factual commit-message tone already in `git log` — no marketing
language, state what changed and why in one or two lines.

## Step 3 — PR into develop, wait for CI, merge

```bash
gh pr create --base develop --head feature/<name> --title "<Russian title>" --body "$(cat <<'EOF'
## Что сделано
...

## Тесты
N тестов, CI зелёный.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
PR descriptions are in Russian — this project's established convention (see the
`feedback_pr_language` memory note if you have it loaded). Then:
```bash
gh pr checks <N> --watch
gh pr merge <N> --merge --delete-branch
git checkout develop && git pull
```
This repo's default is: **you merge feature→develop** without asking each time —
that's already the agreed convention. Only develop→main (step 6) needs a
judgment call about who clicks merge.

## Step 4 — Sync the docs

Four files, every release, no exceptions (this is the step that's easiest to
half-do and the one CLAUDE.md explicitly calls out as mandatory):

1. **RELEASE_NOTES.md** — new section at the *top* of the file:
   ```
   ## vX.Y.Z — YYYY-MM-DD

   ### <short feature title>

   - <what changed, why, what it touches — no emoji, dry and factual>
   - Добавлено N тестов. Всего тестов: <pytest count from step 1>.

   ---
   ```
   Pick the next version number by looking at the last entry already in the file.
   No emoji anywhere in this file — that's a hard project rule, not a style
   preference (see `feedback_release_notes_style` memory note if loaded).

2. **README.md** and **TESTING.md** — find every place the *old* total test count
   appears (grep for it) and replace with the new count. There are usually 4-5
   occurrences in README.md (intro paragraph, a code comment, a bullet, the repo
   tree diagram) and 1 in TESTING.md. Also skim README's feature bullet list — if
   the release added a new user-facing screen or mechanic, it probably deserves a
   one-line bullet there too, in the same terse style as its neighbors.

3. **CLAUDE.md** — this is the one that needs judgment, not just find-replace:
   - Update the "Текущий статус" block's version numbers and test count.
   - If the feature introduced a new non-obvious business rule, invariant, or
     "why we did it this way" decision — add a bullet to "Бизнес-правила" (или
     "Запланировано на будущее" если это заход на будущее). Follow the voice
     already there: state the decision, then *why*, in the same dense
     parenthetical style as the surrounding bullets. Don't just describe *what*
     the code does — that's derivable from reading it; the value of these
     bullets is capturing *why*, especially anything a future session would
     otherwise have to rediscover by reading tests or git blame.
   - If a file's responsibilities changed, touch its row in "Карта файлов" too.
   - Never write the VPS host/IP/password here — see the warning above.

Commit these four together as a small doc-only commit directly to `develop` (no
feature branch, no PR — this project treats doc sync as the "small fix" case that
goes straight to develop):
```bash
git add -A && git commit -m "docs: <version> release notes и синхронизация доков

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push origin develop
```

## Step 5 — Open the develop → main PR yourself, don't wait to be asked

Opening a PR is cheap and reversible (closing an unwanted one costs nothing), so
go ahead and open it right after the docs-sync commit lands — don't make the user
ask for it:
```bash
gh pr create --base main --head develop --title "Release: vX.Y.Z — <short summary>" --body "$(cat <<'EOF'
## Что в релизе
...

См. RELEASE_NOTES.md за подробностями.

## Тесты
N тестов, CI зелёный.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Step 6 — Wait for CI

```bash
gh pr checks <N> --watch
```

## Step 7 — Merging main is the user's call, not yours, by default

This is the one action in the whole flow with real, hard-to-undo consequences
(it ships to real players immediately via autodeploy) — that's why it's the
established convention that **the user clicks merge on develop→main**, even
though you drive everything else. Tell them the PR is green and ready, then
stop and wait. Only merge it yourself if they've explicitly said to ship/deploy
this round (e.g. "делай всё", "и задеплой", "смёржи и в прод") — in that case,
merge it the same way as step 3:
```bash
gh pr merge <N> --merge --delete-branch
```
Either way, before you do anything with it, check whether it's already been
merged out from under you — the user merging main PRs on their own, between
your turns, without telling you first, happens routinely in this project:
```bash
gh pr view <N> --json state,mergedAt
```
If `state` is already `MERGED`, don't try to merge again — that's your cue to
jump straight to step 8 and verify the deploy.

## Step 8 — Confirm main actually deployed

`main` merges trigger an automatic deploy job, but "the workflow said success"
and "the bot is actually running the new code" are different claims — always
verify the second one, don't take the green checkmark's word for it:
```bash
git fetch origin
git log origin/main -1 --oneline
```

## Step 9 — Verify on the VPS over SSH

Pull the VPS host/user/password from your memory for this project (never from a
repo file — see the warning at the top). Use `plink`, not `ssh` — this machine's
setup needs PuTTY's tools, and `-batch` alone isn't enough to skip the host-key
prompt on first connect, so pass the pinned host key explicitly:

```bash
plink -batch -hostkey "<host key fingerprint from memory>" root@<host from memory> -pw <password from memory> "cd /opt/bottennis && git log -1 --oneline && systemctl is-active bottennis && systemctl show bottennis --property=ActiveEnterTimestamp"
```

Confirm: the commit hash matches the merge commit from step 7/8, the service is
`active`, and `ActiveEnterTimestamp` is recent (close to when the merge happened —
that's your signal the restart actually fired, not just that the service happens
to still be running from before). Then check for anything the restart broke:

```bash
plink -batch -hostkey "<same fingerprint>" root@<host from memory> -pw <password from memory> "journalctl -u bottennis --since '5 minutes ago' --no-pager | tail -30"
```

Look for tracebacks or repeated restart-crash-restart loops, not just "does it
print anything." A few `Update id=... is handled` lines with normal durations
means it's healthy.

If SSH itself is unreachable or stalls (this has happened before, intermittently,
for reasons never fully root-caused — see CLAUDE.md's git-history if curious),
don't spin retrying it for more than a couple of tries; tell the user SSH access
is currently flaky and the deploy is unconfirmed rather than silently giving up
or claiming success you didn't actually check.

## Step 10 — Record the confirmed deploy

Update CLAUDE.md's "Текущий статус" line to say prod is confirmed on the new
version with today's date, commit and push directly to `develop`:
```bash
git add -A && git commit -m "docs: подтверждён деплой vX.Y.Z в прод

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push origin develop
```

Report back to the user with the version, what shipped, and that the deploy is
confirmed — not just "PR merged."
