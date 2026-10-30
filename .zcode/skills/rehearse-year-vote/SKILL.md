---
name: rehearse-year-vote
description: >
  Runs the end-to-end rehearsal of the annual voting ("Итоги года") on a
  temporary database with a fake clock and a recording Telegram session. Use
  before 21 December, after any change to bot/handlers/year_vote.py,
  bot/services/year_vote.py, bot/scheduler.py (year jobs) or bot/middleware.py,
  or when the user asks "прогони репетицию голосования".
---

# Rehearsal of the voting

```bash
PYTHONIOENCODING=utf-8 py -3.13 scripts/rehearse_year_vote.py
```

The script uses a temp SQLite file (like prod), the real Dispatcher and routers
from `main.py`, a fake clock and a Telegram session that records calls. It
touches neither the repo DB nor prod. It takes about 5 seconds.

Expected result: `=== Итого: 47 из 47 проверок пройдено ===`. Flags:
`ONLY=latency` (only timing of taps; write taps must take about 0.01 s, not
5 s) and `NO_USAGE=1` (without UsageMiddleware, for comparison).

If a check fails, decide first whether it is a product bug or a mistake in the
script (the script's expectations can be wrong). Report both honestly. After
fixing a product bug, add a regular test in `tests/`.
