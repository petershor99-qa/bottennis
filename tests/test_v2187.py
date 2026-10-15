"""
v2.187.0: партии по очереди быстрее, во весь экран — полоса сверху и отступ
снизу, игроки в ленте по строке, заставка запуска, скрипт цифр для порогов
радара (камбэки).
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
    assert ".boot-ball" in CSS and "#boot-state::before" not in CSS
