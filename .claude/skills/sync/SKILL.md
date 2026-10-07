---
name: sync
description: >
  Brings the local bottennis repo up to date and explains what changed. Use when
  the user says "сделай гит пул", "git pull", "синхронизируй", "подтяни изменения",
  "что нового", or starts a session on a different computer (home/work). Pulls
  develop, installs missing dev dependencies, runs tests, then summarizes what
  parallel sessions added, with roadmap status.
---

# Sync: pull and summarize

The repo lives on the **local disk** of each computer (not on Google Drive).
Find it with `git rev-parse --show-toplevel`; if the cwd is not the repo, use
the working directory that contains `.git` and has the remote
`petershor99-qa/bottennis`.

1. `git status -sb` — if there are uncommitted changes or unpushed commits, say
   so before pulling (never discard them). If the branch is not `develop` or
   a `feature/*`, tell the user.
2. `git fetch` then `git pull --ff-only`. If it cannot fast-forward, stop and
   explain; do not force.
3. Show what arrived: `git log --oneline <old>..HEAD` and the top entries of
   `RELEASE_NOTES.md`. Group by version, in plain Russian, short.
4. Run `py -3.13 -m pytest -q`. If imports fail (new dependency), run
   `py -3.13 -m pip install -r requirements-dev.txt` and retry. Report the
   test count and result honestly.
5. Finish with the roadmap status (table in `CLAUDE.md`, section
   «Дорожная карта») in one or two lines: what is done, what is next and its
   command.

The user is a junior QA: prefer a short table over paragraphs. Remind at the
end of a work session: commit and push, so the other computer sees the changes.
