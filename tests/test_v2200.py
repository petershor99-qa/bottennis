"""
v2.200.0: подиум на главной — советская тумба (картинка владельца), праздник
закрытой цели недели и «недель подряд» с текущей закрытой неделей.
Запуск: pytest tests/test_v2200.py
"""
import os
from datetime import timedelta
from pathlib import Path

import bot.webapp.api_return as ret
from bot.webapp.assets import IMMUTABLE, Assets
from tests.test_come_back import MONDAY, _clock, _club, _m  # noqa: F401 — фикстура часов

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webapp" / "static"
SRC = ROOT / "webapp" / "src" / "app"


def test_podium_art_is_small_transparent_webp():
    data = (STATIC / "podium.webp").read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert b"ALPH" in data or b"VP8L" in data           # прозрачный фон
    assert len(data) < 40 * 1024


def test_app_js_gets_versioned_podium_url():
    assets = Assets(STATIC)
    v = assets.get("podium.webp").version
    assert f'"static/podium.webp?v={v}"'.encode() in assets.get("app.js").body
    assert IMMUTABLE.endswith("immutable")


def test_new_podium_art_changes_app_js_version(tmp_path: Path):
    for name, text in {"app.js": 'var p = "static/podium.webp";', "icons.svg": "<svg/>"}.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "podium.webp").write_bytes(b"RIFF0000WEBPa")
    assets = Assets(tmp_path)
    first = assets.get("app.js").version
    (tmp_path / "podium.webp").write_bytes(b"RIFF0000WEBPb")
    st = (tmp_path / "podium.webp").stat()
    os.utime(tmp_path / "podium.webp", (st.st_atime, st.st_mtime + 10))
    assert assets.get("app.js").version != first


def test_podium_columns_match_stand_steps():
    js = (SRC / "10-home.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert 'var PODIUM_ART = "static/podium.webp";' in js and '"podium-step"' in js
    assert "grid-template-columns: 567fr 606fr 567fr" in css
    for ratio in ("606 / 456", "567 / 328", "567 / 284"):
        assert f"aspect-ratio: {ratio}" in css
    assert "podium-summary" in (SRC / "15-summary.js").read_text(encoding="utf-8")   # «Итоги» — прежний подиум


def test_goals_celebration_wiring():
    js = (SRC / "23-return.js").read_text(encoding="utf-8")
    assert 'var GOALS_SEEN_KEY = "goals_seen_v1";' in js
    assert "celebrateGoals(wrap, g);" in js and "ballBurst(" in js and "confetti(foot)" in js
    assert 'li.setAttribute("data-goal", goal.key);' in js


async def test_closed_current_week_counts_in_streak(db, monkeypatch):
    a, b, c = await _club(db)
    done_weeks = {MONDAY, MONDAY - timedelta(weeks=1)}

    def fake(history, week, pid, start, names, chosen=None):
        ok = start in done_weeks
        return [{"key": "x", "title": "x", "target": 1, "progress": int(ok), "done": ok}]

    monkeypatch.setattr(ret, "_goals_for_week", fake)
    db.add(_m(a, b, a.id, [(11, 5)], MONDAY - timedelta(weeks=5)))
    await db.commit()
    g = await ret.goals_payload(db, a)
    assert g["all_done"] and g["streak"] == 2 and g["week"] == "2026-10-12"


def test_forward_navigation_starts_at_top():
    js = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert "currentNav.restore ? (scrollPos[id] || 0) : 0" in js   # v2.201.0 — по записи истории
    assert js.count("enterScreen(route, quiet);") == 2


def test_record_holders_in_even_grid():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "grid-template-columns: repeat(auto-fit, minmax(76px, 1fr))" in css
