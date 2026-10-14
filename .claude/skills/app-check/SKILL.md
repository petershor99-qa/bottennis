---
name: app-check
description: >
  Full automated check of the bottennis Mini App before a release: unit tests,
  JS logic tests, app.js build check, then the real browser (Playwright) walks
  every screen in light/dark and phone/desktop sizes, runs the key actions
  (reaction, history filter, month story, swipe, share) and compares five
  screens with screenshot baselines. Use after any change to webapp/src/app/*,
  webapp/static/*, bot/webapp/*, and when the user says "проверь приложение",
  "прогони e2e", "скриншот-тесты", "всё ли ок в мини аппе".
---

# Mini App check

Everything runs on the preview server (temp SQLite, fictional players) — the
repo DB and prod are never touched.

## 1. Fast checks

```bash
py -3.13 scripts/build_app_js.py --check
node --check webapp/static/app.js
node --test tests/js/*.test.js
py -3.13 -m pytest -q
```

`build_app_js.py --check` fails → a part in `webapp/src/app/` was edited but
`app.js` was not rebuilt: run `py -3.13 scripts/build_app_js.py`.

## 2. Browser tests (Playwright)

```bash
RUN_E2E=1 E2E_CHANNEL=msedge py -3.13 -m pytest -q tests/e2e
```

- `E2E_CHANNEL=msedge` uses the installed Edge (on this Windows machine
  `playwright install chromium` cannot write to AppData). With a downloaded
  Chromium just omit it. Install once: `py -3.13 -m pip install -r requirements-e2e.txt`.
- `test_app_e2e.py` — every screen (no JS errors, no failed requests except
  photos, no error state, no horizontal scroll) ×3 sizes/themes, plus actions.
  The same file runs in CI (job `e2e`, advisory).
- `test_visual.py` — five screens that do not depend on "today" (scores,
  records, match/96, h2h/3, throne), light and dark, pixel diff ≤1% with dates
  masked. Local only (fonts differ between systems). Baselines:
  `tests/e2e/baselines/<platform>/`. A failure writes `<name>.actual.png` next
  to the baseline — open both and decide:
  - unintended change → fix the code;
  - intended redesign → `UPDATE_BASELINES=1 RUN_E2E=1 E2E_CHANNEL=msedge py -3.13 -m pytest -q tests/e2e/test_visual.py`,
    look at the new PNGs, commit them with the change.

## 3. Screenshots for the user or README

Headless Edge, reduced motion (finished numbers, no count-up):

```bash
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu --hide-scrollbars \
  --force-prefers-reduced-motion --user-data-dir="<scratch>/prof" --window-size=500,1000 \
  --force-device-scale-factor=2 --virtual-time-budget=9000 --screenshot="<scratch>/x.png" \
  "http://127.0.0.1:8770/preview?dark=1#/player/1"
```

Width below ~500 is rendered wider than requested by headless Edge — use 500.
README screenshots must stay anonymized: preview players only, the bot menu is
an HTML mockup with a fictional name.

## 4. Report honestly

Say what ran and what did not (e.g. "visual tests skipped: baselines created
this run"). Known harmless artifacts: count-up numbers caught mid-animation in
the browser pane; a hidden pane has zero width (tap-zone tests there are
meaningless — use Playwright).
