"""Обход Mini App на тупики, ошибки и крайние случаи (v2.208.0).

Для каждого сочетания «вариант данных × зритель» поднимает превью
(`scripts/preview_miniapp.py`) и в настоящем браузере:
- проходит все экраны, до которых можно дойти нажатием (`data-route`), плюс
  список заведомо странных адресов (несуществующий игрок, будущий месяц и т.п.);
- на каждом экране проверяет: ошибки JS и неудачные запросы, «undefined»/«NaN» в
  тексте, горизонтальную прокрутку, пустой экран, есть ли выход (нижнее меню,
  кнопка, ссылка), что кнопка «Назад» Telegram, если она видна, что-то делает;
- нажимает каждую кнопку экрана (кроме отправляющих что-то наружу) и смотрит, не
  упало ли что-то и закрывается ли открывшееся окно;
- роняет запросы к API и проверяет, что экран ошибки не оставляет без выхода.

Запуск (нужен playwright):
  CRAWL_CHANNEL=msedge PYTHONIOENCODING=utf-8 py -3.13 scripts/crawl_miniapp.py
  CRAWL_ONLY=empty:1001 — один вариант; CRAWL_OUT=файл.json — сохранить находки.
"""
import json
import os
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNEL = os.environ.get("CRAWL_CHANNEL") or None

# (вариант данных, зритель): default — клуб из шести, tiny — трое и один матч, empty — без матчей;
# 1001 — игрок с историей, 1099 — «Новичок» (в tiny и empty), 7777 — не зарегистрирован
COMBOS = [("default", 1001), ("tiny", 1001), ("tiny", 1099), ("empty", 1001), ("empty", 1099), ("default", 7777)]
# Заведомо странные адреса: несуществующее, будущее, самого с собой, мусор
ODD_ROUTES = [
    "player/99999", "player/1/bogus", "match/99999", "pair/1/1", "pair/1/99999", "h2h/99999", "h2h/1",
    "month/209912", "month/190001", "month/abc", "summary/week/20991231", "summary/week/19000101",
    "summary/year/20300101", "summary/bogus", "foo", "usage",
]
SKIP_BUTTONS = ("Поделиться", "Вызвать", "Фото", "фото", "Сменить цель")   # уходят наружу или пишут в базу

READY_JS = """() => { const a = document.getElementById('app');
  return !!a && !document.getElementById('boot-state') && !a.querySelector('.sk') && a.innerText.trim().length > 3; }"""

SCAN_JS = """() => {
  const app = document.getElementById('app');
  const vis = (e) => { const b = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const tabs = [...document.querySelectorAll('#tabbar .tab')].filter(vis).length;
  const routes = [...new Set([...document.querySelectorAll('#app [data-route]')].map(e => e.getAttribute('data-route')))];
  const buttons = [...document.querySelectorAll('#app button')].filter(vis).map(b => b.textContent.trim());
  const err = document.querySelector('#app .state-error .state-text');
  const bad = [...document.querySelectorAll('#app *')].filter(e => e.children.length === 0 &&
    /undefined|NaN|\\bnull\\b|\\[object|Infinity/.test(e.textContent)).map(e => e.textContent.trim().slice(0, 40));
  const W = document.documentElement.clientWidth;
  const bk = window.Telegram && Telegram.WebApp && Telegram.WebApp.BackButton;
  return { text: app.innerText.trim().slice(0, 120), tabs, routes, buttons, error: err ? err.textContent : '',
    bad: bad.slice(0, 3), hscroll: document.documentElement.scrollWidth > W + 1,
    back: !!(bk && bk.visible), hash: location.hash };
}"""

findings = []


def note(kind, where, detail=""):
    key = (kind, where, detail)
    if key not in {(f["kind"], f["where"], f["detail"]) for f in findings}:
        findings.append({"kind": kind, "where": where, "detail": detail})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_preview(variant):
    port = free_port()
    env = dict(os.environ, PREVIEW_VARIANT=variant, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "preview_miniapp.py"), str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env)
    deadline = time.time() + 90
    for line in proc.stdout:
        if line.startswith("ready"):
            return proc, f"http://127.0.0.1:{port}"
        if time.time() > deadline:
            break
    proc.kill()
    raise RuntimeError("превью не поднялось")


def open_route(page, base, viewer, route):
    page.goto(f"{base}/preview?u={viewer}#/{route}")
    try:
        page.wait_for_function(READY_JS, timeout=12000)
    except Exception:  # noqa: BLE001
        return False
    page.wait_for_timeout(500)
    return True


back_routes = set()     # экраны, где видна «Назад» Telegram — проверяются отдельно, на чистой странице


def check_back_button(ctx, base, viewer, tag):
    """Экран открыт сразу (по ссылке из бота): в истории одна запись. Если «Назад»
    видна, нажатие должно вести на главную, а не стоять на месте."""
    for route in sorted(back_routes):
        page = ctx.new_page()
        if open_route(page, base, viewer, route):
            before = page.evaluate("location.hash")
            try:
                page.evaluate("Telegram.WebApp.BackButton.cb && Telegram.WebApp.BackButton.cb()")
                page.wait_for_timeout(500)
                stuck = page.evaluate("location.hash") == before
            except Exception:  # noqa: BLE001 — страница ушла (history.back из первой записи)
                stuck = True
            if stuck:
                note("«Назад» видна, но при открытии по ссылке ничего не делает", f"{tag} #/{route}",
                     "в истории нет предыдущего экрана")
        page.close()
    back_routes.clear()


def check_screen(page, where, scan):
    if scan["bad"]:
        note("в тексте мусор", where, str(scan["bad"]))
    if scan["hscroll"]:
        note("горизонтальная прокрутка", where)
    has_exit = scan["tabs"] > 0 or scan["routes"] or [b for b in scan["buttons"] if b != "Повторить"]
    if not has_exit:
        note("ТУПИК: нет ни меню, ни кнопок, ни ссылок", where, scan["text"])
    if scan["error"] and scan["tabs"] == 0:
        note("экран ошибки без нижнего меню", where, scan["error"])
    if scan["back"]:
        back_routes.add(scan["hash"].lstrip("#/"))


def run_combo(p, variant, viewer):
    proc, base = start_preview(variant)
    tag = f"{variant}/u{viewer}"
    browser = p.chromium.launch(channel=CHANNEL)
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(f"JS: {e}"))
    page.on("console", lambda m: errs.append(f"console: {m.text}") if m.type == "error" and "404" not in m.text else None)
    page.on("response", lambda r: errs.append(f"{r.status} {r.url.split('/api/')[-1]}")
            if r.status >= 400 and "/api/photo/" not in r.url else None)
    expected_errors = viewer == 7777

    def flush(where):
        odd = any(where.endswith(f"#/{r}") or f"#/{r} " in where for r in ODD_ROUTES)
        for e in errs:
            if expected_errors and ("401" in e or "403" in e):
                continue
            if odd and (e.startswith(("404", "400")) or "status of 404" in e or "status of 400" in e):
                continue                      # заведомо странный адрес — 404 ожидаем
            note("ошибка в консоли/сети", f"{tag} {where}", e[:140])
        errs.clear()

    # 1. обход по ссылкам, начиная с главной, плюс странные адреса
    queue, seen = [""], set()
    routes_extra = list(ODD_ROUTES)
    while queue or routes_extra:
        route = queue.pop(0) if queue else routes_extra.pop(0)
        if route in seen:
            continue
        seen.add(route)
        where = f"{tag} #/{route}"
        if not open_route(page, base, viewer, route):
            note("экран не нарисовался за 12 с", where)
            flush(where)
            continue
        scan = page.evaluate(SCAN_JS)
        check_screen(page, where, scan)
        flush(where)
        for r in scan["routes"]:
            if r not in seen and len(seen) < 70:
                queue.append(r)
        # 2. каждая кнопка экрана (по одной, на свежей загрузке)
        names = [b for b in scan["buttons"] if b and b != "Повторить" and not any(s in b for s in SKIP_BUTTONS)][:10]
        for i, name in enumerate(names):
            if not open_route(page, base, viewer, route):
                break
            btns = page.locator("#app button:visible")
            idx = None
            for k in range(btns.count()):
                if btns.nth(k).inner_text().strip() == name:
                    idx = k
                    break
            if idx is None:
                continue
            try:
                btns.nth(idx).click(timeout=3000)
            except Exception as e:  # noqa: BLE001
                note("кнопка не нажимается", where, f"«{name}»: {str(e)[:80]}")
                continue
            page.wait_for_timeout(500)
            sheet = page.locator(".sheet-wrap")
            if sheet.count():
                sheet.first.click(position={"x": 5, "y": 5}, timeout=2000)
                page.wait_for_timeout(300)
                if page.locator(".sheet-wrap").count():
                    note("окно не закрывается по нажатию мимо", where, f"после «{name}»")
            after = page.evaluate(SCAN_JS)
            check_screen(page, f"{where} после «{name}»", after)
            flush(f"{where} после «{name}»")

    check_back_button(ctx, base, viewer, tag)

    # 3. сбой сети: экран ошибки должен давать повторить, и повтор должен работать
    # без сохранённого снимка главной: новый контекст с пустой памятью, запросы сразу падают
    ctx2 = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx2.new_page()
    page.route("**/api/home*", lambda r: r.abort())
    page.route("**/api/leaderboard*", lambda r: r.abort())
    page.goto(f"{base}/preview?u={viewer}#/")
    page.wait_for_timeout(4000)
    scan = page.evaluate(SCAN_JS)
    if not expected_errors:
        if not scan["error"]:
            note("при сбое сети нет сообщения об ошибке", f"{tag} главная", scan["text"])
        elif "Повторить" not in scan["buttons"]:
            note("при сбое сети нет кнопки «Повторить»", f"{tag} главная", scan["error"])
        else:
            page.unroute("**/api/home*")
            page.unroute("**/api/leaderboard*")
            page.get_by_text("Повторить").first.click()
            page.wait_for_timeout(1500)
            if page.evaluate(SCAN_JS)["error"]:
                note("«Повторить» не помогает после восстановления сети", f"{tag} главная")
    errs.clear()
    browser.close()
    proc.kill()
    print(f"  {tag}: экранов {len(seen)}", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    only = os.environ.get("CRAWL_ONLY")
    combos = [c for c in COMBOS if not only or f"{c[0]}:{c[1]}" == only]
    with sync_playwright() as p:
        for variant, viewer in combos:
            run_combo(p, variant, viewer)
    print(f"\nНаходок: {len(findings)}")
    for f in findings:
        print(f"[{f['kind']}] {f['where']}  {f['detail']}")
    out = os.environ.get("CRAWL_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(findings, fh, ensure_ascii=False, indent=1)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
