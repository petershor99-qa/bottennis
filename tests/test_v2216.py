"""
v2.216.0: «Итоги» периода — карточка-корона доминирующего игрока, заголовок-
приговор («Октябрь: топ-3, но в минус»), «Характер периода» и ход мест в топе
(▲▼ против прошлого периода). Механики переиспользуют «Твой месяц» (match_mood)
и сводки бота; изменения generic — действуют на неделю, месяц, квартал и год.
Запуск: pytest tests/test_v2216.py
"""
from datetime import datetime, timedelta

import pytest

import bot.webapp.api_period as api_period
from tests.test_come_back import _club, _m

NOW = datetime(2026, 10, 15, 9, 0)             # четверг 15.10.2026, 12:00 МСК
MONDAY = datetime(2026, 10, 11, 21, 0)         # 12.10 00:00 МСК


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(api_period, "_now", lambda: NOW)


async def test_dominant_player_collapses_into_crown(db):
    """5 побед Алисы из 5 матчей недели — плитки «Героев» сворачиваются в
    карточку-корону с её титулами, чужие и парные плитки остаются в сетке."""
    a, b, c = await _club(db)
    t = MONDAY + timedelta(hours=10)
    db.add_all([
        _m(a, b, a.id, [(11, 3), (11, 4)], t, rc=15.0),                                  # разгром
        _m(a, b, a.id, [(9, 11), (9, 11), (11, 9)], t + timedelta(hours=1), rc=12.0),    # камбэк
        _m(a, c, a.id, [(11, 2)], t + timedelta(hours=2), rc=11.0),                      # разгром
        _m(a, c, a.id, [(12, 10), (11, 9)], t + timedelta(hours=3), rc=10.0),            # дьюсы
        _m(a, b, a.id, [(11, 4), (11, 5)], t + timedelta(hours=4), rc=10.0),
    ])
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    assert d["dominant"], "Алиса выиграла 5 из 5 — без короны стена плиток о ней"
    assert d["dominant"]["player"]["name"] == "Алиса"
    assert d["dominant"]["title"] == "Неделя Алиса"
    assert d["dominant"]["games"] == "5 матчей" and d["dominant"]["value"] == "+58.0"
    joined = " | ".join(d["dominant"]["lines"])
    assert "Больше всех побед — 5" in joined and "Нагибатель недели" in joined
    assert all(not (len(h["players"]) == 1 and h["players"][0]["id"] == a.id) for h in d["heroes"])
    assert any(h["key"] == "derby" for h in d["heroes"]), "парная плитка не сворачивается"


async def test_no_dominant_when_spread(db):
    """Титулы размазаны (максимум 2 плитки у одного игрока) — короны нет.
    rc разные, чтобы «Лучший рост» и «Отрицательный рост» достались разным."""
    a, b, c = await _club(db)
    t = MONDAY + timedelta(hours=10)
    db.add_all([
        _m(a, b, a.id, [(11, 8)], t, rc=10.0),
        _m(b, c, b.id, [(11, 8)], t + timedelta(hours=1), rc=30.0),
        _m(c, a, c.id, [(11, 8)], t + timedelta(hours=2), rc=5.0),
    ])
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    assert d["dominant"] is None
    assert len(d["heroes"]) >= 4, "плиток достаточно, просто нет доминанта"


async def test_verdict_tone_and_wording(db):
    """Приговор: место и дельта. Топ-3 в минус — «но», топ вне тройки в плюс —
    «зато»... нет, связка та же схема «#N, но в плюс». Тон по дельте."""
    a, b, c = await _club(db)
    t = MONDAY + timedelta(hours=10)
    db.add_all([
        _m(a, b, b.id, [(11, 5)], t, rc=8.0),
        _m(a, c, c.id, [(11, 5)], t + timedelta(hours=1), rc=8.0),
        _m(a, b, a.id, [(11, 5)], t + timedelta(hours=2), rc=8.0),
        _m(a, c, c.id, [(11, 5)], t + timedelta(hours=3), rc=8.0),
    ])
    await db.commit()
    d = await api_period.period_payload(db, a, "month", "2026-10-15")
    # Алиса: 1 победа, 3 поражения, дельта −16, место по рейтингу #1 (топ-3) → «но в минус»
    assert d["you"]["verdict"]["text"] == "Октябрь: топ-1, но в минус −16.0 (1:3)"
    assert d["you"]["verdict"]["tone"] == "tone-loss"
    assert all("Место" not in line and "Рейтинг" not in line for line in d["you"]["lines"])


async def test_year_has_you_card_with_verdict(db):
    a, b, _ = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5)], MONDAY + timedelta(hours=10), rc=12.0))
    await db.commit()
    d = await api_period.period_payload(db, a, "year", None)
    assert d["you"]["title"] == "Твой год"
    assert d["you"]["verdict"]["text"] == "Год: топ-1, и в плюс +12.0 (1:0)"
    assert d["you"]["verdict"]["tone"] == "tone-win"


async def test_mood_line_counts(db):
    a, b, c = await _club(db)
    t = MONDAY + timedelta(hours=10)
    db.add_all([
        _m(a, b, a.id, [(11, 3), (11, 4)], t, rc=10.0),                                     # разгром
        _m(a, b, a.id, [(9, 11), (9, 11), (11, 9)], t + timedelta(hours=1), rc=10.0),       # камбэк
        _m(b, c, b.id, [(12, 10), (13, 11)], t + timedelta(hours=2), rc=10.0),              # дьюсы (2 партии на дьюсе)
    ])
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    assert "1 камбэк" in d["mood"] and "1 разгром" in d["mood"] and "1 дьюс" in d["mood"]


async def test_standings_move_vs_previous_week(db):
    a, b, _ = await _club(db)
    db.add_all([
        _m(a, b, b.id, [(11, 5)], MONDAY - timedelta(days=7) + timedelta(hours=10), rc=10.0),
        _m(a, b, a.id, [(11, 5)], MONDAY + timedelta(hours=10), rc=10.0),
    ])
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    moves = {r["name"]: r["move"] for r in d["standings"]}
    assert moves["Алиса"]["label"] == "▲1" and moves["Алиса"]["tone"] == "tone-win"
    assert moves["Боб"]["label"] == "▼1" and moves["Боб"]["tone"] == "tone-loss"


async def test_no_move_badges_without_previous_matches(db):
    a, b, _ = await _club(db)
    db.add(_m(a, b, a.id, [(11, 5)], MONDAY + timedelta(hours=10), rc=10.0))
    await db.commit()
    d = await api_period.period_payload(db, a, "week", None)
    assert all(r["move"] is None for r in d["standings"]), "первый период — значков хода нет"
