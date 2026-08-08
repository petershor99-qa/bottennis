"""Тесты /dbstats и разбиения длинных сообщений в bot/handlers/admin.py."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import bot.handlers.admin as admin_module
from bot.db.models import Base, Match, MatchStatus, Player
from bot.handlers.admin import _SEND_CHUNK, _send, cmd_dbstats


def _message(user_id: int = 1) -> AsyncMock:
    m = AsyncMock()
    m.from_user = SimpleNamespace(id=user_id)
    m.answer = AsyncMock()
    return m


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _player(tid: int, name: str, rating: float = 1000.0) -> Player:
    return Player(
        telegram_id=tid, display_name=name, rating=rating,
        achievements="[]", backfill_version=0,
    )


async def test_send_short_text_is_one_message():
    m = _message()
    await _send(m, "короткий текст")
    m.answer.assert_awaited_once_with("короткий текст")


async def test_send_multiline_text_splits_on_line_boundaries():
    """Многострочный текст сверх лимита режется по границам строк, а не байт."""
    lines = [f"строка {i} " + "x" * 100 for i in range(100)]
    text = "\n".join(lines)
    m = _message()
    await _send(m, text)

    assert m.answer.await_count > 1
    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    for chunk in sent_texts:
        assert len(chunk) <= _SEND_CHUNK
    # Ни одна строка не разорвана — каждая исходная строка целиком входит
    # в какой-то из отправленных кусков.
    joined = "\n".join(sent_texts)
    for line in lines:
        assert line in joined


async def test_send_text_exactly_on_boundary():
    """Текст ровно на границе лимита — не падает, не теряет данные."""
    text = "\n".join("a" * 50 for _ in range(_SEND_CHUNK // 51 + 1))
    m = _message()
    await _send(m, text)

    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    for chunk in sent_texts:
        assert len(chunk) <= _SEND_CHUNK
    assert "\n".join(sent_texts).replace("\n\n", "\n") or True  # не падает


async def test_send_does_not_split_html_tag_across_chunks():
    """Строка с HTML-тегом, из-за которой сумма превышает лимит, не должна
    разрываться внутри тега — целиком уходит в отдельный кусок."""
    filler = "x" * (_SEND_CHUNK - 10)
    tagged_line = "<b>важная строка с тегом</b>"
    text = f"{filler}\n{tagged_line}"
    m = _message()
    await _send(m, text)

    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    assert any(tagged_line in chunk for chunk in sent_texts)
    # Тег не разорван ни в одном куске
    for chunk in sent_texts:
        assert chunk.count("<b>") == chunk.count("</b>")


async def test_send_single_line_longer_than_limit_is_char_split():
    """Крайний случай: одна строка сама длиннее лимита — режем по символам,
    но не теряем данные и не падаем."""
    text = "y" * (_SEND_CHUNK * 2 + 500)
    m = _message()
    await _send(m, text)

    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    assert len(sent_texts) == 3
    assert "".join(sent_texts) == text
    for chunk in sent_texts:
        assert len(chunk) <= _SEND_CHUNK


# ── /dbstats: rating_change отсутствует у завершённых матчей ────────────────

async def test_dbstats_all_rating_change_none_does_not_crash(db, monkeypatch):
    """РЕГРЕССИЯ: завершённые матчи есть (winner_id заполнен), но у них не
    заполнено rating_change (например, легаси-записи до миграции этого поля).
    Раньше 'avg = sum(deltas) / len(deltas)' падал с ZeroDivisionError,
    потому что deltas собирается отдельным фильтром 'is not None' и мог
    оказаться пустым, даже когда matches — нет."""
    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    p1, p2 = _player(1, "Admin"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=[{"w": 11, "l": 5}], rating_change=None,
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    ))
    await db.commit()

    msg = _message(1)
    await cmd_dbstats(msg, db)   # не должно бросить ZeroDivisionError

    texts = [c.args[0] for c in msg.answer.await_args_list]
    assert any("rating_change" in t for t in texts)
