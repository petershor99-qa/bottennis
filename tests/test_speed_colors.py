"""
Пакет 4, релиз 2 (v2.180.0): облегчённый шрифт, предзагрузка, без favicon,
мгновенный старт из снимка, время и неудачные запуски в «Использовании»,
цвет показателей стиля относительно клуба, «пружинка» реакций.
Браузерная часть (бюджет загрузки, снимок, цвета на странице) —
tests/e2e/test_boot_budget_e2e.py. Запуск: pytest tests/test_speed_colors.py
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fontTools.ttLib import TTFont
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

import bot.webapp.style_tiers as style_tiers
import bot.webapp.usage as usage
from bot.db.models import AppBoot, Base
from bot.webapp.api_insights import boot_summary
from bot.webapp.server import STATIC_DIR, USAGE_KEY, create_app
from tests.conftest import _completed, _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


# ── Шрифт, предзагрузка, favicon ──────────────────────────────────────────────

def test_subset_font_keeps_every_needed_glyph():
    from scripts.subset_font import SOURCE, TARGET, css_unicode_range

    src, sub = TTFont(SOURCE).getBestCmap(), TTFont(TARGET).getBestCmap()
    needed = {cp for cp in css_unicode_range() if cp in src}
    assert needed and needed <= set(sub), sorted(needed - set(sub))[:10]
    assert TARGET.stat().st_size < SOURCE.stat().st_size * 0.8


def test_css_uses_subset_and_source_is_not_served():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert 'url("fonts/Raleway-Subset.woff2")' in css and "Raleway-Variable" not in css
    assert not (STATIC_DIR / "fonts" / "Raleway-Variable.woff2").exists()


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        s.add(_player(ADMIN, "Alice"))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        c.auth = {"Authorization": "tma " + _init_data(ADMIN)}
        c.factory = db_factory
        yield c


async def test_index_preloads_versioned_app_and_font(client):
    html = await (await client.get("/")).text()
    preload = re.search(r'<link rel="preload" href="static/app\.js\?v=([0-9a-f]{12})" as="script">', html)
    boot = re.search(r's\.src = "static/app\.js\?v=([0-9a-f]{12})"', html)
    assert preload and boot and preload.group(1) == boot.group(1)   # один адрес — один запрос
    assert 'href="static/fonts/Raleway-Subset.woff2" as="font"' in html
    assert '<link rel="icon" href="data:,">' in html


# ── Время и неудачные запуски ─────────────────────────────────────────────────

async def test_boot_summary_tail_slow_share_and_failures(db):
    db.add_all([AppBoot(ms=ms, platform="android") for ms in (500, 800, 1000, 1200, 4000)]
               + [AppBoot(ms=0, platform="android", failed=True), AppBoot(ms=900, platform="ios")])
    await db.commit()
    rows = {r["label"]: r for r in await boot_summary(db)}
    android = rows["android"]
    assert android["value"] == "1,0 с"                        # медиана без неудачных
    assert android["note"] == "5 запусков · 90% — до 1,2 с · дольше 3 с — 20% · не запустилось — 1"
    assert rows["ios"]["note"] == "1 запуск · 90% — до 0,9 с"


async def test_boot_summary_only_failures(db):
    db.add(AppBoot(ms=0, platform="tdesktop", failed=True))
    await db.commit()
    rows = await boot_summary(db)
    assert rows[0]["value"] == "—" and rows[0]["note"] == "0 запусков · не запустилось — 1"


async def test_boot_fail_is_stored_once_per_cooldown(client, monkeypatch):
    usage._last_failure.clear()
    body = {"stage": "app_js_pending", "platform": "android", "version": "8.0"}
    for _ in range(2):
        await client.post("/api/boot_fail", json=body, headers=client.auth)
    await client.app[USAGE_KEY].drain()
    async with client.factory() as s:
        rows = (await s.execute(select(AppBoot.ms, AppBoot.platform, AppBoot.failed))).all()
    assert rows == [(0, "android", True)]
    usage._last_failure.clear()


async def test_migration_adds_failed_to_app_boots(monkeypatch, tmp_path):
    import bot.db.database as database

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE app_boots DROP COLUMN failed"))
    monkeypatch.setattr(database, "engine", engine)
    await database._migrate_db()
    async with engine.begin() as conn:
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(app_boots)"))).all()]
    await engine.dispose()
    assert "failed" in cols


# ── Цвет показателей ──────────────────────────────────────────────────────────

def _s(**kw) -> dict:
    base = {"wins": 20, "draws": 0, "losses": 10, "deuce_total": 20, "first_set_wins": 20, "dominance_matches": 20}
    base.update(kw)
    return base


REF = {"Винрейт": [(1, 70.0), (2, 50.0), (3, 48.0), (4, 30.0)],
       "Камбэки": [(1, 2.0), (2, 20.0), (3, 22.0), (4, 25.0)],
       "Клатч": [(1, 50.0), (2, 50.0)]}


@pytest.mark.parametrize("player_id, value, expected", [
    (1, 70.0, "gold"),        # лучший с отрывом
    (2, 50.0, "white"),       # около медианы (49)
    (4, 30.0, "red"),         # ниже медианы больше чем на 8
])
def test_tier_by_club(player_id, value, expected):
    assert style_tiers.tier("Винрейт", value, _s(), player_id, REF)[0] == expected


def test_tier_green_without_being_best_and_tie_is_not_gold():
    ref = {"Винрейт": [(1, 70.0), (2, 70.0), (3, 48.0), (4, 40.0)]}
    assert style_tiers.tier("Винрейт", 70.0, _s(), 1, ref) == ("green", "Выше среднего по клубу на 11")


def test_comebacks_never_red():
    assert style_tiers.tier("Камбэки", 2.0, _s(), 1, REF)[0] == "white"


def test_small_sample_is_gray_with_reason():
    t, hint = style_tiers.tier("Клатч", 100.0, _s(deuce_total=3), 1, REF)
    assert (t, hint) == ("gray", "Мало данных: 3 из 5 дьюсов")
    assert style_tiers.tier("Винрейт", 100.0, _s(wins=4, losses=1), 1, REF) == ("gray", "Мало данных: 5 из 10 матчей")


def test_nobody_to_compare_is_white():
    assert style_tiers.tier("Клатч", 90.0, _s(), 1, REF) == ("white", "Пока не с кем сравнить")


async def _club(db, n_players=4, matches=12):
    players = [_player(1000 + i, f"P{i}") for i in range(n_players)]
    db.add_all(players)
    await db.flush()
    for k in range(matches):
        a, b = players[k % n_players], players[(k + 1) % n_players]
        db.add(_completed(a, b, a.id, 5.0, NOW - timedelta(hours=k + 1)))
    await db.commit()
    return players


async def test_club_reference_filters_by_sample_and_caches(db, monkeypatch):
    await _club(db)
    style_tiers._reference.clear()
    ref = await style_tiers.club_reference(db)
    ids = {pid for pid, _ in ref["Винрейт"]}
    assert ids == set()                                   # по 6 матчей — меньше 10 для винрейта
    assert {pid for pid, _ in ref["Камбэки"]} == set()    # побед меньше 10
    calls = []
    monkeypatch.setattr(style_tiers, "get_career_matches", lambda *a, **k: calls.append(1))
    assert await style_tiers.club_reference(db) is ref    # без нового матча — из кэша
    assert calls == []


async def test_profile_and_style_payloads_carry_tiers(db):
    from bot.webapp import api

    players = await _club(db, matches=40)
    style_tiers._reference.clear()
    payload = await api.player_payload(db, players[0], players[0])
    stats = payload["card"]["stats"]
    assert all(s["tier"] in {"gray", "white", "green", "gold", "red"} and "tier_hint" in s for s in stats)
    radar = await api.radar_payload(db, players[0])
    assert all("tier" in a and "tier_hint" in a for a in radar["axes"])


# ── Страница ──────────────────────────────────────────────────────────────────

def test_snapshot_start_is_bound_to_player_and_aged():
    js = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert 'var SNAP_KEY = "snap_home_v1";' in js and "SNAP_MAX_AGE_MS" in js
    assert "snap.u === uid" in js                       # чужой снимок не показывается
    assert 'snapshotNote("Обновляется…")' in js and "Нет связи — данные на " in js
    assert 'if (route === "") saveHomeSnapshot(data);' in js


def test_reaction_pop_and_tier_classes_on_page():
    home = (SRC / "10-home.js").read_text(encoding="utf-8")
    profile = (SRC / "12-profile.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "reactionPop(bar, r, next)" in home and ".reaction.pop" in css
    assert '" tier tier-" + s.tier' in profile and ".tier-gold" in css
    assert "prefers-reduced-motion" in css.split("v2.180.0: «пружинка»")[1]
