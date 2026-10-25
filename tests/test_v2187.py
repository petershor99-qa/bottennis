"""
v2.187.0: партии по очереди быстрее, во весь экран — полоса сверху и отступ
снизу, игроки в ленте по строке, заставка запуска, скрипт цифр для порогов
радара (камбэки). v2.188.0 — картинка заставки, тема до app.js, трон из ракеток.
Запуск: pytest tests/test_v2187.py
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "scripts"))

import style_calibration as cal  # noqa: E402


def _m(a, b, winner, sets):
    return (a, b, winner, json.dumps([{"w": w, "l": lo} for w, lo in sets]))


def test_comeback_stats_old_and_new_definition():
    st = cal.comeback_stats([
        _m(1, 2, 1, [(5, 11), (11, 7), (11, 9)]),    # 1 отыгрался после проигранной первой
        _m(1, 2, 2, [(11, 8), (5, 11), (9, 11)]),    # 1 проиграл первую (в его перспективе 8:11) — и матч
        _m(2, 1, 2, [(9, 11), (8, 11), (11, 5), (11, 6), (11, 4)]),   # 2 — с 0:2
        _m(1, 2, None, [(11, 5), (5, 11)]),          # ничья: у 2 проиграна первая
    ])
    p1, p2 = st[1], st[2]
    assert (p1["matches"], p1["wins"], p1["lost_first"], p1["after_lost_first"], p1["from_0_2"]) == (4, 1, 2, 1, 0)
    assert (p2["wins"], p2["lost_first"], p2["after_lost_first"], p2["from_0_2"]) == (2, 2, 1, 1)


def test_script_prints_anonymized_table(tmp_path):
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE matches (challenger_id, challenged_id, winner_id, sets_data, status, completed_at)")
    for i in range(6):
        a, b, w, s = _m(1, 2, 1 if i % 2 else 2, [(5, 11), (11, 7), (11, 9)])
        con.execute("INSERT INTO matches VALUES (?,?,?,?, 'completed', ?)", (a, b, w, s, f"2026-10-0{i + 1}"))
    con.commit()
    con.close()
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "style_calibration.py"), "--db", str(db)],
                         capture_output=True, text=True, encoding="utf-8", check=True,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout
    assert "u1" in out and "u2" in out and "(3 из 3)" in out


def test_fullscreen_strip_and_bottom_padding():
    assert "html.is-fullscreen body::before" in CSS and "height: var(--top-safe)" in CSS
    assert "calc(92px + var(--bottom-safe, env(safe-area-inset-bottom)))" in CSS


def test_feed_players_two_rows_and_faster_replay():
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    assert "function feedPlayers" in home and "body.appendChild(feedPlayers(it))" in home
    match = (SRC / "20-match.js").read_text(encoding="utf-8")
    assert "var total = 2000, setMs = 350, gap = 60;" in match


def test_boot_splash_markup():
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="boot-state" role="status"' in html and "Клуб настольного тенниса" in html and "Загрузка…" in html
    assert ".boot-pic" in CSS and "#boot-state::before" not in CSS   # v2.188.0: картинка вместо мяча


# ── v2.188.0: картинка заставки и тёмная тема до app.js ──────────────────────

def test_splash_picture_light_and_preloaded():
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    pic = ROOT / "webapp" / "static" / "splash.webp"
    assert pic.stat().st_size < 40 * 1024
    assert 'class="boot-pic" src="static/splash.webp"' in html and 'rel="preload" href="static/splash.webp"' in html
    assert "tgWebAppThemeParams" in html and 'setAttribute("data-theme", "dark")' in html


async def test_splash_served_with_version_and_cached_forever(aiohttp_client=None):
    from bot.webapp.assets import IMMUTABLE, Assets
    from bot.webapp.server import STATIC_DIR

    assets = Assets(STATIC_DIR)
    v = assets.get("splash.webp").version
    html = assets.index_html().decode("utf-8")
    assert html.count(f'"static/splash.webp?v={v}"') == 2          # preload и сама картинка
    assert assets.get("splash.webp").body[:4] == b"RIFF" and IMMUTABLE.endswith("immutable")


# ── v2.188.0: трон из ракеток ────────────────────────────────────────────────

async def test_hall_of_fame_defenses_and_records(db):
    from datetime import datetime, timedelta, timezone

    from bot.db.models import ChampionReign, Match, MatchStatus
    from bot.webapp import api
    from tests.conftest import _player

    a, b = _player(1, "Алиса"), _player(2, "Боб")
    db.add_all([a, b])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now - timedelta(days=10)
    db.add(ChampionReign(player_id=a.id, started_at=start))

    def boss(winner, days_ago):
        return Match(challenger_id=b.id, challenged_id=a.id, status=MatchStatus.completed, winner_id=winner,
                     is_boss_fight=True, sets_data=[{"w": 11, "l": 7}], rating_change=10.0,
                     completed_at=now - timedelta(days=days_ago))
    db.add_all([boss(a.id, 8), boss(a.id, 3), boss(b.id, 20)])     # две защиты; давний боссфайт — не в правлении
    await db.commit()
    d = await api.hall_of_fame_payload(db)
    assert d["current"]["defenses"] == 2 and d["current"]["defenses_text"] == "2 защиты"
    labels = {r["label"]: r["value"] for r in d["records"]}
    assert labels["Дольше всех"] == "Алиса · 10 дней" and labels["Защит за правление"] == "Алиса · 2"


def test_throne_art_wiring():
    # v2.191.0: рисунок из ракеток заменён картинкой владельца (tests/test_v2191.py)
    club = (SRC / "14-club.js").read_text(encoding="utf-8")
    assert "function throneArt" not in club and "c.appendChild(throneCard(d.current, d.art, timeline))" in club
    assert ".throne-sitter" in CSS and "@keyframes throne-fan" not in CSS
