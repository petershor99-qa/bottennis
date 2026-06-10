"""
Тесты хендлеров (поведение флоу, а не только чистая логика).

Мокаем Telegram-объекты (Message/CallbackQuery/Bot), но используем
настоящие FSM (MemoryStorage) и in-memory SQLite — чтобы проверять
реальный путь пользователя: вызов → ввод счёта → отмена.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bot.db.models import Base, Match, MatchStatus, Player
from bot.handlers.challenge import do_cancel_match, send_challenge
from bot.handlers.match_result import confirm_result, handle_direct_score, process_set_score
from bot.handlers.profile import _nearest_achievement_progress
from bot.services.achievements import get_achievements
from bot.states.states import MatchResultStates
from bot.utils import MSK_OFFSET, compute_alltime_streak, get_rec_signal, msk_day_start

# ── Фикстуры и хелперы ──────────────────────────────────────────────────────────

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


def _state(user_id: int = 1, chat_id: int = 1) -> FSMContext:
    """Настоящий FSMContext на MemoryStorage."""
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=MemoryStorage(), key=key)


def _message(user_id: int, text: str) -> AsyncMock:
    m = AsyncMock()
    m.from_user = SimpleNamespace(id=user_id, username="u", full_name="U")
    m.text = text
    m.chat = SimpleNamespace(id=user_id)
    # message.answer(...) возвращает объект с .message_id
    m.answer = AsyncMock(return_value=SimpleNamespace(message_id=999))
    return m


def _callback(user_id: int, data: str) -> AsyncMock:
    cb = AsyncMock()
    cb.from_user = SimpleNamespace(id=user_id)
    cb.data = data
    cb.message = AsyncMock()
    cb.message.chat = SimpleNamespace(id=user_id)
    cb.message.message_id = 555
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


async def _accepted_match(db, challenger: Player, challenged: Player) -> Match:
    m = Match(
        challenger_id=challenger.id, challenged_id=challenged.id,
        status=MatchStatus.accepted, accepted_at=datetime(2026, 6, 1, 12, 0, 0),
    )
    db.add(m)
    await db.flush()
    return m


# ── handle_direct_score (прямой ввод счёта) ─────────────────────────────────────

async def test_direct_score_no_active_match_is_ignored(db):
    """Нет активного матча → хендлер молча выходит, FSM не запускается."""
    p1 = _player(1, "Alice")
    db.add(p1)
    await db.flush()

    msg, st = _message(1, "11:7"), _state(1)
    await handle_direct_score(msg, db, st)

    assert await st.get_state() is None
    msg.answer.assert_not_called()


async def test_direct_score_one_active_match_starts_input(db):
    """Один активный матч → FSM стартует и счёт обрабатывается."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _accepted_match(db, p1, p2)

    msg, st = _message(1, "11:7"), _state(1)
    await handle_direct_score(msg, db, st)

    assert await st.get_state() == MatchResultStates.entering_set_score.state
    data = await st.get_data()
    assert data["sets_data"] == [{"reporter": 11, "opponent": 7}]
    msg.answer.assert_called()  # показал прогресс


async def test_direct_score_multiple_active_matches_prompts(db):
    """РЕГРЕССИЯ: 2+ активных матча → подсказка, без краша и без FSM."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)
    await _accepted_match(db, p1, p3)

    msg, st = _message(1, "11:7"), _state(1)
    await handle_direct_score(msg, db, st)

    assert await st.get_state() is None
    msg.answer.assert_called_once()
    assert "несколько активных" in msg.answer.call_args.args[0]


# ── process_set_score (валидация ввода) ─────────────────────────────────────────

async def _prep_input_state(match_id: int) -> FSMContext:
    st = _state(1)
    await st.set_state(MatchResultStates.entering_set_score)
    await st.update_data(sets_data=[], match_id=match_id, reporter_player_id=1)
    return st


async def test_process_set_score_valid_single(db):
    st = await _prep_input_state(1)
    msg = _message(1, "11:7")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == [{"reporter": 11, "opponent": 7}]


async def test_process_set_score_invalid_is_rejected(db):
    """Некорректный счёт 15:7 → ошибка, партия не добавлена."""
    st = await _prep_input_state(1)
    msg = _message(1, "15:7")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == []
    msg.answer.assert_called()


async def test_process_set_score_batch(db):
    """Пакетный ввод '11:7 9:11' → две партии."""
    st = await _prep_input_state(1)
    msg = _message(1, "11:7 9:11")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == [
        {"reporter": 11, "opponent": 7},
        {"reporter": 9, "opponent": 11},
    ]


async def test_process_set_score_dash_separator(db):
    """Дефис как разделитель: '11-7' = '11:7'."""
    st = await _prep_input_state(1)
    msg = _message(1, "11-7")
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == [{"reporter": 11, "opponent": 7}]


# ── send_challenge (создание матча) ─────────────────────────────────────────────

async def test_send_challenge_creates_active_match(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    r = await db.execute(
        Match.__table__.select().where(Match.status == MatchStatus.accepted)
    )
    rows = r.fetchall()
    assert len(rows) == 1
    bot.send_message.assert_called()      # соперник уведомлён
    cb.message.edit_text.assert_called()  # инициатор видит «матч начат»


async def test_send_challenge_blocks_duplicate(db):
    """Нельзя вызвать игрока, с которым уже есть активный матч."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _accepted_match(db, p1, p2)
    await db.commit()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    cb.answer.assert_called()  # показал alert про активный матч
    assert any("активный матч" in str(c.args) for c in cb.answer.call_args_list)


# ── do_cancel_match (отмена + уведомление + ачивка) ─────────────────────────────

async def test_cancel_match_declines_and_notifies(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    cb, bot = _callback(1, f"cancel_yes_{m.id}"), AsyncMock()
    await do_cancel_match(cb, db, bot)

    assert m.status == MatchStatus.declined
    bot.send_message.assert_called()           # соперник уведомлён
    cb.message.edit_text.assert_called()
    # «Дух Анкориджа» — обоим участникам
    assert "anchorage_spirit" in get_achievements(p1)
    assert "anchorage_spirit" in get_achievements(p2)


async def test_cancel_completed_match_is_blocked(db):
    """Завершённый матч нельзя отменить — рейтинг уже начислен."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=[{"w": 11, "l": 7}], rating_change=10.0,
        completed_at=datetime(2026, 6, 1, 12, 0, 0),
    )
    db.add(m)
    await db.commit()

    cb, bot = _callback(1, f"cancel_yes_{m.id}"), AsyncMock()
    await do_cancel_match(cb, db, bot)

    assert m.status == MatchStatus.completed   # статус не затёрт
    bot.send_message.assert_not_called()


# ── confirm_result: пороги новичок/ветеран не сдвинуты текущим матчем ──────────

async def _confirming_state(match_id: int, reporter_id: int, sets: list[dict]) -> FSMContext:
    st = _state(1)
    await st.set_state(MatchResultStates.confirming)
    await st.update_data(
        match_id=match_id, reporter_player_id=reporter_id,
        sets_data=sets, is_draw=False,
    )
    return st


async def test_confirm_result_current_match_excluded_from_counts(db):
    """РЕГРЕССИЯ v2.55.0: CAS-guard переводит матч в completed ДО подсчётов,
    из-за чего текущий матч попадал в кол-во завершённых:
      - проигравший с 14 прошлыми матчами считался ветераном (пол 900 вместо 1000)
      - первая встреча соперников получала repeat-штраф ×0.95 вместо ×1.0
    """
    p1 = _player(1, "Winner", rating=1000.0)
    p2 = _player(2, "Loser", rating=1001.0)
    p3 = _player(3, "Filler")
    db.add_all([p1, p2, p3])
    await db.flush()

    # У проигравшего ровно 14 завершённых матчей → он ещё новичок (пол 1000)
    for i in range(14):
        db.add(Match(
            challenger_id=p2.id, challenged_id=p3.id,
            status=MatchStatus.completed, winner_id=p3.id,
            sets_data=[{"w": 11, "l": 5}], rating_change=5.0,
            completed_at=datetime(2026, 5, 1 + i, 12, 0, 0),
        ))
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    st = await _confirming_state(m.id, p1.id, [{"reporter": 11, "opponent": 0}])
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    # Дельта: base≈16.0 × mult 1.4 × short 0.75 = 16.8 → ×1.2 новичок ×1.0 repeat = 20.2
    # (с багом repeat был бы ×0.95 → 19.2)
    assert p1.rating == 1020.2
    # Пол новичка 1000 (с багом — ветеранский 900 → рейтинг упал бы до 980.8)
    assert p2.rating == 1000.0
    assert m.status == MatchStatus.completed
    assert m.winner_id == p1.id


# ── Граница дня по МСК ──────────────────────────────────────────────────────────

def test_msk_day_start_is_msk_midnight():
    """msk_day_start() — полночь по МСК, выраженная в naive-UTC."""
    start = msk_day_start()
    msk = start + MSK_OFFSET
    assert (msk.hour, msk.minute, msk.second) == (0, 0, 0)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert start <= now < start + timedelta(days=1)


# ── get_rec_signal — рекомендация соперника ──────────────────────────────────────

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _h2h(winner_id, days_ago):
    """Мок матча для get_rec_signal: только winner_id + completed_at."""
    return SimpleNamespace(winner_id=winner_id, completed_at=_NOW - timedelta(days=days_ago))
_VID, _OID = 1, 2
_V_RATING, _O_RATING = 1000.0, 1000.0


def test_rec_signal_no_history():
    assert get_rec_signal(_V_RATING, _VID, _O_RATING, _OID, [], _NOW) == "ещё не встречались"


def test_rec_signal_loss_streak_2():
    h2h = [_h2h(_OID, 0), _h2h(_OID, 1)]
    assert get_rec_signal(_V_RATING, _VID, _O_RATING, _OID, h2h, _NOW) == "серия поражений — 2 подряд"


def test_rec_signal_loss_streak_3():
    h2h = [_h2h(_OID, 0), _h2h(_OID, 1), _h2h(_OID, 2)]
    assert get_rec_signal(_V_RATING, _VID, _O_RATING, _OID, h2h, _NOW) == "серия поражений — 3 подряд"


def test_rec_signal_single_loss():
    """Последний матч проигран, но не серия (до этого была победа)."""
    h2h = [_h2h(_OID, 0), _h2h(_VID, 1)]
    assert get_rec_signal(_V_RATING, _VID, _O_RATING, _OID, h2h, _NOW) == "ты проиграл последний матч"


def test_rec_signal_days_since():
    """Последний матч выигран 5 дней назад — показываем паузу."""
    h2h = [_h2h(_VID, 5)]
    result = get_rec_signal(_V_RATING, _VID, _O_RATING, _OID, h2h, _NOW)
    assert result == "не играли 5 дней"


def test_rec_signal_stronger_opponent():
    """Нет давней паузы, но соперник на 40 pts сильнее."""
    h2h = [_h2h(_VID, 1)]
    result = get_rec_signal(_V_RATING, _VID, _V_RATING + 40, _OID, h2h, _NOW)
    assert result == "он сильнее на +40"


def test_rec_signal_no_signal():
    """Последний матч выигран вчера, соперник близок по рейтингу — нет сигнала."""
    h2h = [_h2h(_VID, 1)]
    assert get_rec_signal(_V_RATING, _VID, _V_RATING + 10, _OID, h2h, _NOW) == ""


def test_rec_signal_draw_breaks_streak():
    """Ничья прерывает серию поражений — одиночный флаг не показывается."""
    h2h = [_h2h(None, 0), _h2h(_OID, 1)]  # последний — ничья, до этого проигрыш
    result = get_rec_signal(_V_RATING, _VID, _V_RATING + 10, _OID, h2h, _NOW)
    # ничья не проигрыш → не "ты проиграл", не серия; пауза 0 дней → нет сигнала
    assert result == ""


# ── compute_alltime_streak ────────────────────────────────────────────────────────

def _match_result(winner_id):
    return SimpleNamespace(winner_id=winner_id)


def test_alltime_streak_basic():
    """W W L W W W → лучшая серия 3."""
    ms = [_match_result(_VID), _match_result(_VID), _match_result(_OID),
          _match_result(_VID), _match_result(_VID), _match_result(_VID)]
    assert compute_alltime_streak(ms, _VID) == 3


def test_alltime_streak_all_wins():
    ms = [_match_result(_VID)] * 5
    assert compute_alltime_streak(ms, _VID) == 5


def test_alltime_streak_no_wins():
    ms = [_match_result(_OID)] * 3
    assert compute_alltime_streak(ms, _VID) == 0


# ── _nearest_achievement_progress ────────────────────────────────────────────────

def _p_ach(achievements: list[str], rating: float = 1000.0):
    """Минимальный мок Player для _nearest_achievement_progress."""
    return SimpleNamespace(achievements=str(achievements).replace("'", '"'), rating=rating)


def _stats(wins=0, draws=0, losses=0, streak=0, beaten=0):
    return {
        "wins": wins, "draws": draws, "losses": losses,
        "streak": streak, "beaten_opponents_count": beaten,
    }


def test_ach_progress_no_matches():
    """Нет матчей → None."""
    p = _p_ach([])
    assert _nearest_achievement_progress(p, _stats(), total_players=3) is None


def test_ach_progress_streak_hat_trick():
    """Серия 2 побед, hat_trick не заработан → показывает hat_trick 2/3."""
    # rating_1200 уже «заработан» в earned, чтобы оно не перебивало hat_trick (ratio 2/3)
    p = _p_ach(["rating_1200"])
    result = _nearest_achievement_progress(p, _stats(wins=2, streak=2), total_players=3)
    assert result is not None
    assert "2/3" in result
    assert "Хет-трик" in result


def test_ach_progress_skips_earned():
    """hat_trick уже заработан → показывает следующую по прогрессу."""
    p = _p_ach(["press_start", "first_blood", "hat_trick", "rating_1200"])
    result = _nearest_achievement_progress(p, _stats(wins=2, streak=2), total_players=3)
    # hat_trick пропущен, im_on_fire (2/5) или fifty (2/50) — берётся лучший по ratio
    assert result is not None
    assert "Я горяч нахуй" in result  # im_on_fire: 2/5 = 0.4 > fifty: 2/50 = 0.04


def test_ach_progress_all_earned_returns_none():
    """Все счётные ачивки заработаны → None."""
    all_ids = [
        "hat_trick", "im_on_fire", "god_mode",
        "fifty", "veteran", "legend",
        "diplomat", "collector", "rating_1200",
    ]
    p = _p_ach(all_ids, rating=1300.0)
    s = _stats(wins=200, draws=5, losses=10, streak=10, beaten=4)
    result = _nearest_achievement_progress(p, s, total_players=5)
    assert result is None


def test_ach_progress_collector():
    """beaten_opponents_count=2 из 3 → показывает collector 2/3."""
    # rating_1200 уже «заработан», чтобы collector (2/3=0.67) выиграл
    p = _p_ach(["rating_1200"])
    s = _stats(wins=10, losses=5, beaten=2)
    result = _nearest_achievement_progress(p, s, total_players=4)
    assert result is not None
    assert "2/3" in result
    assert "Со всеми" in result
