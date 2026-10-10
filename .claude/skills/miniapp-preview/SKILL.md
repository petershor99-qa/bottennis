---
name: miniapp-preview
description: >
  Visual check of the bottennis Mini App before release: starts the real web
  part (bot/webapp) on a temporary SQLite with seeded data and a stubbed
  Telegram, then walks every screen in light and dark themes and checks the
  console for errors. Use after any change to webapp/static/*, bot/webapp/* or
  the services they read (leaderboard, stats, achievements, club records), and
  when the user asks "проверь мини апп", "покажи как выглядит", "превью".
---

# Mini App preview

Touches neither the repo DB nor prod: the DB lives in a temp folder, Telegram
is a stub with a correctly signed `initData`. No player photos (no bot) —
letters on colored circles are expected.

## 1. Start the server (background)

```bash
PYTHONIOENCODING=utf-8 py -3.13 scripts/preview_miniapp.py
```

Wait for the line `ready http://127.0.0.1:8770/preview`. Port busy — pass
another one as the first argument (`… preview_miniapp.py 8771`). Python
changes need a restart; static files (`webapp/static/*`) are served from disk,
a page reload is enough.

## 2. Walk the screens

Built-in browser, mobile viewport (`resize_window` preset `mobile`), light
theme `…/preview#/…`, dark `…/preview?dark=1#/…`:

| Route | Screen |
|---|---|
| `#/` | Club home: podium, "Ты", match of the day, records |
| `#/rating` | Leaderboard |
| `#/player/1` | Own profile (tab "Я" active), `#/player/2` — someone else's |
| `#/player/1/matches`, `/achievements`, `/style` | Profile sections |
| `#/h2h/2` | "Ты против …" |
| `#/records`, `#/matches`, `#/activity/club`, `#/throne` | Club sections |

Screenshots of the emulated viewport show only the first screen — scroll to
see the rest. If screenshots fail, check by text instead: run in the page

```js
const routes=['#/','#/rating','#/player/2','#/player/2/matches','#/player/2/achievements','#/player/2/style','#/h2h/3','#/records','#/matches','#/activity/club','#/throne','#/player/1'];
const errs=[];window.addEventListener('error',e=>errs.push(e.message));const out=[];
for(const r of routes){location.hash=r;await new Promise(res=>setTimeout(res,700));
out.push(r+' ['+[...document.querySelectorAll('.tab.is-active .tab-label')].map(x=>x.textContent)+'] '+document.getElementById('app').innerText.replace(/\s+/g,' ').slice(0,120));}
out.join('\n')+'\nERR:'+errs.join('|')
```

Every route must show content (not "Загрузка…" or an error) and `ERR:` must be
empty. Also read `read_console_messages` with `onlyErrors` (a 404 for
favicon is fine).

## 3. Finish

- Reset the viewport (`resize_window` preset `desktop`) and stop the server
  (kill the process listening on the port).
- Report honestly what was seen with your own eyes and what was checked only
  by text. Data in the preview is invented — say so if the user asks how it
  looks on real data.
