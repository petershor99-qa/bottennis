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
changes need a restart; static files (`webapp/static/*`) are served from disk.
Since v2.176.0 the page source lives in parts `webapp/src/app/*.js` — after
editing a part run `py -3.13 scripts/build_app_js.py` (it rewrites
`webapp/static/app.js`), then reload. For an automated pass (Playwright,
screenshot baselines) use the `app-check` skill.

## 2. Walk the screens

Built-in browser, mobile viewport (`resize_window` preset `mobile`), light
theme `…/preview#/…`, dark `…/preview?dark=1#/…`:

| Route | Screen |
|---|---|
| `#/` | Club home: first-run hint, podium, "Ты" with today line, match of the day, club feed, links |
| `#/rating` | Leaderboard |
| `#/player/1` | Own profile (tab "Я" active), `#/player/2` — someone else's |
| `#/player/1/matches`, `/achievements`, `/style` | Profile sections |
| `#/h2h/2` | "Ты против …" |
| `#/records`, `#/matches`, `#/activity/club`, `#/throne` | Club sections |
| `#/summary/day` (`week`, `month`, `quarter`, `year`) | Summaries from real digest snapshots; `year` also shows "Твой год" |
| `#/match/1` | Match screen with reactions (tap one — count and state change) |
| `#/when`, `#/when/me` | "Когда играют" grid (seed hours are random — real data is office hours) |
| `#/usage` | Admin "Использование" (preview seeds 400 usage events) |

Also worth checking by hand: an error screen (`#/match/99999` — icon and
"Повторить"), the new-achievement glow (put a short `seen_achievements_v1`
list into localStorage, open `#/player/1/achievements`), and the quiet
refresh on return (hide the page for over a minute via `document.hidden` +
`visibilitychange` — scroll stays, no skeleton). Photo requests answer 404
in the preview (no bot) — expected.

Screenshots of the emulated viewport show only the first screen — scroll to
see the rest. If screenshots fail, check by text instead: run in the page

```js
const routes=['#/','#/rating','#/player/2','#/player/2/matches','#/player/2/achievements','#/player/2/style','#/h2h/3','#/records','#/matches','#/activity/club','#/throne','#/summary/day','#/summary/year','#/match/1','#/when','#/when/me','#/usage','#/player/1'];
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
