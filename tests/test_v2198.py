"""
v2.198.0: матчи после рассылки итогов дня (после 21:30) — блоком «🌙 После прошлых
итогов» в следующей рассылке; штамп «Камбэк» только с 0:2, после 0:1 — «Отыгрался».
Запуск: pytest tests/test_v2198.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

import bot.scheduler as scheduler
from bot.db.models import DigestSnapshot
from bot.utils import msk_day_start
from tests.conftest import _completed, _player

ROOT = Path(__file__).resolve().parent.parent


async def _club(db_factory, *, snapshot_ago_h: float | None, late: bool, today: bool):
    day_start = msk_day_start()
    async with db_factory() as s:
        a, b = _player(1001, "Алиса"), _player(2002, "Боб")
        s.add_all([a, b])
        await s.flush()
        if snapshot_ago_h is not None:   # вчерашняя рассылка в 21:30 МСК = за 2,5 ч до полуночи
            s.add(DigestSnapshot(kind="day", text="вчера", created_at=day_start - timedelta(hours=snapshot_ago_h)))
        s.add(_completed(a, b, b.id, 9.0, day_start - timedelta(hours=3)))          # до рассылки — уже был в итогах
        if late:
            s.add(_completed(a, b, a.id, 11.0, day_start - timedelta(hours=1, minutes=53)))   # 22:07
        if today:
            # не раньше полуночи по МСК: прогон, попавший на смену суток, уводил матч во «вчера»
            s.add(_completed(b, a, b.id, 7.0, max(day_start, datetime.now(timezone.utc).replace(tzinfo=None)
                                                   - timedelta(minutes=5))))
        await s.commit()


def _texts(bot):
    return [c.args[1] for c in bot.send_message.call_args_list]


async def test_late_match_goes_to_next_summary(db_factory, monkeypatch):
    monkeypatch.setattr(scheduler, "async_session", db_factory)
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    await _club(db_factory, snapshot_ago_h=2.5, late=True, today=True)
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_summary(bot)
    text = _texts(bot)[0]
    assert text.startswith("📅 <b>Итоги дня")
    block = text.split("🌙 <b>После прошлых итогов</b>\n", 1)[1].split("\n\n", 1)[0]
    assert block.count("\n") == 0 and "<b>Алиса</b> vs Боб" in block     # только поздний, не тот, что до рассылки
    assert text.index("После прошлых итогов") < text.index("Топ дня")


async def test_late_match_alone_when_nobody_played_today(db_factory, monkeypatch):
    monkeypatch.setattr(scheduler, "async_session", db_factory)
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    await _club(db_factory, snapshot_ago_h=2.5, late=True, today=False)
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_summary(bot)
    texts = _texts(bot)
    assert len(texts) == 2                                    # обоим игрокам
    assert all(t.startswith("🌙 <b>После прошлых итогов</b>") and "<b>Алиса</b> vs Боб" in t for t in texts)
    assert all("Итоги дня" not in t for t in texts)
    async with db_factory() as s:
        kinds = [x.kind for x in (await s.execute(select(DigestSnapshot))).scalars().all()]
    assert kinds == ["day", "day_late"]
    bot2 = SimpleNamespace(send_message=AsyncMock())          # повторно — уже сообщили
    await scheduler.send_daily_summary(bot2)
    bot2.send_message.assert_not_called()


async def test_no_block_without_late_matches_or_previous_summary(db_factory, monkeypatch):
    monkeypatch.setattr(scheduler, "async_session", db_factory)
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    await _club(db_factory, snapshot_ago_h=None, late=True, today=True)     # рассылок ещё не было
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_summary(bot)
    assert "После прошлых итогов" not in _texts(bot)[0]


def test_stamp_levels_in_page():
    match = (ROOT / "webapp" / "src" / "app" / "20-match.js").read_text(encoding="utf-8")
    assert 'rally: "Отыгрался"' in match and 'if (lostTwo) return "comeback";' in match
    css = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".mood-comeback .mood-stamp { color: var(--gold);" in css and ".mood-rally .mood-stamp" in css
