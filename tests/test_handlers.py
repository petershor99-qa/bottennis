"""
Тесты хендлеров (поведение флоу, а не только чистая логика).

Мокаем Telegram-объекты (Message/CallbackQuery/Bot), но используем
настоящие FSM (MemoryStorage) и in-memory SQLite — чтобы проверять
реальный путь пользователя: вызов → ввод счёта → отмена.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from bot.db.models import Base, Match, MatchStatus, Player
from bot.handlers.challenge import do_cancel_match, send_challenge, show_players_for_challenge
from bot.handlers.match_result import (
    _send_h2h_milestone_egg,
    _send_quick_rematch_egg,
    _send_time_based_eggs,
    _send_winner_eggs,
    confirm_result,
    fsm_reset_notice,
    handle_direct_score,
    process_set_score,
    start_report,
)
from bot.handlers.profile import (
    _compute_player_stats,
    _nearest_achievement_progress,
    _rank_gap_line,
    _render_stats_lines,
    _throne_distance_line,
)
from bot.services.achievements import get_achievements
from bot.states.states import MatchResultStates
from bot.utils import (
    MSK_OFFSET,
    compute_alltime_streak,
    compute_ranks,
    env_int,
    format_rank,
    get_match_counts,
    get_rec_signal,
    msk_day_start,
    pluralize_sets,
)

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


async def test_send_challenge_screen_has_no_report_button(db):
    """Экран начала матча не предлагает кнопку «Внести результат» — счёт
    вносится прямым вводом в чат (кнопка осталась только на «Мои матчи»,
    где нужна для выбора среди нескольких активных матчей)."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    opponent_kb = bot.send_message.call_args_list[0].kwargs["reply_markup"]
    initiator_kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    for kb in (opponent_kb, initiator_kb):
        buttons = [b.text for row in kb.inline_keyboard for b in row]
        assert not any("report_" in (b.callback_data or "") for row in kb.inline_keyboard for b in row)
        assert "Внести результат" not in buttons

    initiator_text = cb.message.edit_text.call_args.args[0]
    assert "напиши счёт сюда" in initiator_text


async def test_send_challenge_blocks_when_challenger_busy_with_same_opponent(db):
    """Нельзя вызвать игрока, с которым уже есть активный матч (частный
    случай общего правила «только один активный матч одновременно»)."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    await _accepted_match(db, p1, p2)
    await db.commit()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    cb.message.edit_text.assert_called()
    text = cb.message.edit_text.call_args[0][0]
    assert "активный матч" in text
    assert "Bob" in text
    # матч не создан повторно — остался ровно один активный
    r = await db.execute(
        Match.__table__.select().where(Match.status == MatchStatus.accepted)
    )
    assert len(r.fetchall()) == 1


async def test_send_challenge_blocks_when_challenger_busy_with_different_opponent(db):
    """Нельзя вызвать нового соперника, если уже есть активный матч с кем-то другим."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)  # Alice уже занята матчем с Bob
    await db.commit()

    cb, bot = _callback(1, f"challenge_{p3.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    cb.message.edit_text.assert_called()
    text = cb.message.edit_text.call_args[0][0]
    assert "активный матч" in text
    assert "Bob" in text  # блокировка про ТЕКУЩИЙ активный матч, не про Cara
    bot.send_message.assert_not_called()  # Cara не должна получить уведомление


async def test_send_challenge_blocks_when_opponent_busy(db):
    """Нельзя вызвать соперника, который уже занят матчем с кем-то третьим."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p2, p3)  # Bob уже занят матчем с Cara
    await db.commit()

    cb, bot = _callback(1, f"challenge_{p2.id}"), AsyncMock()
    await send_challenge(cb, db, bot)

    cb.answer.assert_called()
    assert any("занят" in str(c.args) for c in cb.answer.call_args_list)
    bot.send_message.assert_not_called()


# ── show_players_for_challenge (экран «Кого вызвать») ───────────────────────────

async def test_challenge_screen_shows_busy_match_when_viewer_busy(db):
    """Если зритель уже занят активным матчем — вместо списка соперников
    показываем его текущий матч, а не список игроков."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)  # Alice занята матчем с Bob
    await db.commit()

    cb = _callback(1, "menu_play")
    await show_players_for_challenge(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "активный матч" in text
    assert "Bob" in text
    assert "Cara" not in text  # список соперников не показан вообще


async def test_challenge_screen_hides_globally_busy_players(db):
    """Игрок, занятый матчем с кем-то ТРЕТЬИМ (не зрителем), не показывается
    в списке для вызова — раньше фильтровались только свои же соперники."""
    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p2, p3)  # Bob занят матчем с Cara, Alice свободна
    await db.commit()

    cb = _callback(1, "menu_play")
    await show_players_for_challenge(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Bob" in t for t in buttons)
    assert not any("Cara" in t for t in buttons)


async def test_challenge_screen_shows_free_players(db):
    """Свободный игрок нормально показывается в списке для вызова."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.commit()

    cb = _callback(1, "menu_play")
    await show_players_for_challenge(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Bob" in t for t in buttons)


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


# ── Ранги: единый источник правды (только среди игравших) ─────────────────────

def test_compute_ranks_excludes_zero_match_players():
    players = [
        SimpleNamespace(id=1, rating=1100.0),
        SimpleNamespace(id=2, rating=1000.0),
        SimpleNamespace(id=3, rating=1200.0),  # 0 матчей — вне рейтинга
    ]
    ranks = compute_ranks(players, {1: 5, 2: 3})  # у игрока 3 матчей нет
    assert ranks == {1: 1, 2: 2}
    assert 3 not in ranks  # самый высокий рейтинг, но без матчей — не ранжируется


def test_compute_ranks_orders_by_rating_desc():
    players = [SimpleNamespace(id=1, rating=950.0), SimpleNamespace(id=2, rating=1050.0)]
    assert compute_ranks(players, {1: 1, 2: 1}) == {2: 1, 1: 2}


def test_format_rank_ranked_and_unranked():
    ranks = {1: 1, 2: 2}
    assert format_rank(ranks, 1) == "#1 из 2"
    assert format_rank(ranks, 2) == "#2 из 2"
    assert format_rank(ranks, 99) == "вне рейтинга"  # нет матчей


async def test_get_match_counts_ignores_non_completed(db):
    a, b, c = _player(1, "A"), _player(2, "B"), _player(3, "C")
    db.add_all([a, b, c])
    await db.commit()
    # завершённый матч a vs b — считается обоим
    db.add(Match(
        challenger_id=a.id, challenged_id=b.id,
        status=MatchStatus.completed, winner_id=a.id,
    ))
    # активный матч a vs c — НЕ считается
    db.add(Match(challenger_id=a.id, challenged_id=c.id, status=MatchStatus.accepted))
    await db.commit()

    counts = await get_match_counts(db)
    assert counts == {a.id: 1, b.id: 1}  # accepted не в счёт; c без завершённых отсутствует


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


def test_ach_progress_rating_ratio_from_baseline():
    """Прогресс рейтинга считается от 1000: игрок с 1000.0 не получает цель «Рейтинг 1200»
    при наличии любой другой цели с положительным прогрессом."""
    p = _p_ach([])
    # fifty: 10/50 = 0.2 > rating_1200: (1000-1000)/200 = 0.0
    s = _stats(wins=5, losses=5)
    result = _nearest_achievement_progress(p, s, total_players=2)
    assert result is not None
    assert "Рейтинг 1200" not in result


def test_ach_progress_rating_high_wins():
    """Рейтинг 1150 → ratio (1150-1000)/200 = 0.75 — рейтинговая цель побеждает."""
    p = _p_ach([], rating=1150.0)
    s = _stats(wins=5, losses=5)
    result = _nearest_achievement_progress(p, s, total_players=2)
    assert result is not None
    assert "Рейтинг 1200" in result
    assert "1150/1200" in result


# ── _render_stats_lines (группировка экрана «Статистика») ───────────────────────

def _full_stats(**overrides) -> dict:
    """Полный набор ключей _compute_player_stats с нейтральными дефолтами."""
    base = {
        "recent_7": [], "streak": 0, "loss_streak": 0,
        "best_opp": None, "nemesis": None, "top_opp": None,
        "avg_delta": None, "best_win": None,
        "total_earned": 0.0, "total_lost": 0.0,
        "best_streak": 0, "total_sets_played": 0,
        "first_set_conv": None, "fav_format": None,
        "best_day": None, "best_day_count": 0,
        "boss_fights_played": 0, "boss_fights_won": 0,
        "trend_30d": None, "trend_30d_matches": 0,
        "deuce_total": 0, "deuce_won": 0,
    }
    base.update(overrides)
    return base


def test_render_stats_lines_empty_when_nothing_notable():
    p = SimpleNamespace(id=1, rating=1000.0, peak_rating=None)
    assert _render_stats_lines(p, _full_stats()) == []


def test_render_stats_lines_groups_separated_by_single_blank_line():
    """Форма/серии, соперники, рейтинг/рекорды и разное — разделены ровно одной
    пустой строкой между непустыми группами (как «Рекорды клуба», v2.73.0)."""
    p = SimpleNamespace(id=1, rating=1050.0, peak_rating=1080.0)
    s = _full_stats(
        streak=3,
        best_opp={"name": "Bob", "wins": 4},
        avg_delta=2.5,
        total_sets_played=42,
    )
    lines = _render_stats_lines(p, s)

    assert "" in lines
    blank_indices = [i for i, ln in enumerate(lines) if ln == ""]
    # Между четырьмя непустыми группами — ровно 3 разделителя, никаких подряд идущих пустых строк
    assert len(blank_indices) == 3
    assert lines[0] != "" and lines[-1] != ""
    for i in blank_indices:
        assert lines[i - 1] != "" and lines[i + 1] != ""


def test_render_stats_lines_peak_rating_only_when_above_current():
    p_above = SimpleNamespace(id=1, rating=1000.0, peak_rating=1100.0)
    lines_above = _render_stats_lines(p_above, _full_stats())
    assert any("Пик рейтинга" in ln for ln in lines_above)

    p_equal = SimpleNamespace(id=1, rating=1100.0, peak_rating=1100.0)
    lines_equal = _render_stats_lines(p_equal, _full_stats())
    assert not any("Пик рейтинга" in ln for ln in lines_equal)


def test_render_stats_lines_order_form_then_opponents_then_rating_then_misc():
    """Порядок групп фиксирован: форма/серии → соперники → рейтинг/рекорды → разное."""
    p = SimpleNamespace(id=1, rating=1000.0, peak_rating=None)
    s = _full_stats(
        streak=2,
        nemesis={"name": "Cara", "losses": 3},
        best_win=15.0,
        fav_format=(3, 10),
    )
    lines = _render_stats_lines(p, s)
    idx_streak = next(i for i, ln in enumerate(lines) if "Серия" in ln)
    idx_nemesis = next(i for i, ln in enumerate(lines) if "Кошмар" in ln)
    idx_best_win = next(i for i, ln in enumerate(lines) if "Лучший матч" in ln)
    idx_format = next(i for i, ln in enumerate(lines) if "Любимый формат" in ln)
    assert idx_streak < idx_nemesis < idx_best_win < idx_format


def test_render_stats_lines_shows_trend_30d():
    p = SimpleNamespace(id=1, rating=1000.0, peak_rating=None)
    s = _full_stats(trend_30d=12.4, trend_30d_matches=3)
    lines = _render_stats_lines(p, s)
    assert any("За 30 дней" in ln and "+12.4" in ln for ln in lines)


def test_render_stats_lines_no_trend_line_when_no_recent_matches():
    p = SimpleNamespace(id=1, rating=1000.0, peak_rating=None)
    lines = _render_stats_lines(p, _full_stats())
    assert not any("За 30 дней" in ln for ln in lines)


def test_render_stats_lines_shows_deuce_stats():
    p = SimpleNamespace(id=1, rating=1000.0, peak_rating=None)
    s = _full_stats(deuce_total=5, deuce_won=3)
    lines = _render_stats_lines(p, s)
    assert any("Партий на дьюсе" in ln and "5" in ln and "3" in ln for ln in lines)


def test_render_stats_lines_no_deuce_line_when_zero():
    p = SimpleNamespace(id=1, rating=1000.0, peak_rating=None)
    lines = _render_stats_lines(p, _full_stats())
    assert not any("дьюсе" in ln for ln in lines)


# ── _compute_player_stats: тренд за 30 дней / статистика по дьюсу ───────────────

async def test_compute_stats_trend_30d_sums_recent_deltas(db):
    """_compute_player_stats читает m.challenger/m.challenged (relationship,
    не просто id) — матчи должны быть реально закоммичены, не голые объекты."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(_completed(p1, p2, p1.id, 10.0, now - timedelta(days=5)))
    db.add(_completed(p2, p1, p2.id, 4.0, now - timedelta(days=2)))  # p1 проиграл: -4.0
    await db.commit()

    all_r = await db.execute(
        select(Match).where(Match.status == MatchStatus.completed)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    s = _compute_player_stats(p1, all_r.scalars().all())
    assert s["trend_30d"] == 6.0
    assert s["trend_30d_matches"] == 2


async def test_compute_stats_trend_30d_excludes_old_matches(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(_completed(p1, p2, p1.id, 10.0, now - timedelta(days=45)))  # за пределами 30 дней
    await db.commit()

    all_r = await db.execute(
        select(Match).where(Match.status == MatchStatus.completed)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    s = _compute_player_stats(p1, all_r.scalars().all())
    assert s["trend_30d"] is None
    assert s["trend_30d_matches"] == 0


async def test_compute_stats_deuce_counts_total_and_won(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        # sets_data в перспективе challenger (p1): "w"=очки p1, "l"=очки p2
        sets_data=[{"w": 12, "l": 10}, {"w": 10, "l": 12}, {"w": 11, "l": 7}],
        completed_at=now,
    ))
    await db.commit()

    all_r = await db.execute(
        select(Match).where(Match.status == MatchStatus.completed)
        .options(selectinload(Match.challenger), selectinload(Match.challenged))
    )
    s = _compute_player_stats(p1, all_r.scalars().all())
    # партия 1 (12:10) — на дьюсе, p1 выиграл; партия 2 (10:12) — на дьюсе, p1 проиграл
    assert s["deuce_total"] == 2
    assert s["deuce_won"] == 1


# ── _rank_gap_line ────────────────────────────────────────────────────────────

def test_rank_gap_line_shows_positive_gap():
    me = SimpleNamespace(id=1, display_name="Me", rating=1000.0)
    above = SimpleNamespace(id=2, display_name="Above", rating=1050.0)
    result = _rank_gap_line(me, [me, above], ranks={1: 2, 2: 1})
    assert result is not None
    assert "Above" in result
    assert "50.0" in result


def test_rank_gap_line_none_for_rank_1():
    me = SimpleNamespace(id=1, display_name="Me", rating=1000.0)
    assert _rank_gap_line(me, [me], ranks={1: 1}) is None


def test_rank_gap_line_none_when_gap_not_positive():
    """Пиннинг чемпиона: игрок рангом выше может иметь более низкий сырой
    рейтинг — строку не показываем, чтобы не путать «отрицательным разрывом»."""
    me = SimpleNamespace(id=1, display_name="Me", rating=1300.0)
    champion = SimpleNamespace(id=2, display_name="Champion", rating=1200.0)
    result = _rank_gap_line(me, [me, champion], ranks={1: 2, 2: 1})
    assert result is None


# ── _throne_distance_line (чистая функция — champion/challenger уже на руках) ───

def test_throne_distance_none_when_no_champion():
    p1 = _player(1, "Alice")
    assert _throne_distance_line(p1, None, None, total_matches=20) is None


def test_throne_distance_none_for_champion_himself():
    champion = _player(1, "Champion", rating=1200.0)
    champion.id = 1
    assert _throne_distance_line(champion, champion, None, total_matches=20) is None


def test_throne_distance_challenger_gets_call_to_action():
    champion = _player(1, "Champion", rating=1000.0)
    champion.id = 1
    contender = _player(2, "Contender", rating=1100.0)
    contender.id = 2

    result = _throne_distance_line(contender, champion, contender, total_matches=15)
    assert result is not None
    assert "вызови чемпиона" in result


def test_throne_distance_gap_when_below_champion():
    champion = _player(1, "Champion", rating=1200.0)
    champion.id = 1
    weaker = _player(2, "Weaker", rating=1000.0)
    weaker.id = 2

    result = _throne_distance_line(weaker, champion, None, total_matches=5)
    assert result is not None
    assert "До трона" in result
    assert "200.0" in result


def test_throne_distance_exact_tie_is_not_shown_as_arrived():
    """РЕГРЕССИЯ: при <= рейтинг==чемпионскому давал «−0.0 pts», хотя
    get_challenger() требует СТРОГО больше — тай ещё не даёт претендентства."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.id = 1
    tied = _player(2, "Tied", rating=1000.0)
    tied.id = 2

    result = _throne_distance_line(tied, champion, None, total_matches=20)
    assert result is not None
    assert "−0.0" not in result
    assert "сравнялся" in result


def test_throne_distance_needs_more_matches_when_above_champion():
    """Обогнал чемпиона по очкам, но не набрал порог матчей — претендента ВООБЩЕ нет."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.id = 1
    newcomer = _player(2, "Newcomer", rating=1100.0)
    newcomer.id = 2

    result = _throne_distance_line(newcomer, champion, None, total_matches=5)
    assert result is not None
    assert "не хватает матчей" in result


def test_throne_distance_shows_who_is_ahead():
    """Обогнал чемпиона и набрал порог, но претендентское место занято третьим."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.id = 1
    me = _player(2, "Me", rating=1100.0)
    me.id = 2
    ahead = _player(3, "Ahead", rating=1150.0)
    ahead.id = 3

    result = _throne_distance_line(me, champion, ahead, total_matches=20)
    assert result is not None
    assert "Ahead" in result
    assert "50.0" in result


# ── env_int / pluralize_sets ─────────────────────────────────────────────────────

def test_env_int_missing(monkeypatch):
    monkeypatch.delenv("X_TEST_INT", raising=False)
    assert env_int("X_TEST_INT") == 0


def test_env_int_empty(monkeypatch):
    """ADMIN_ID= (пустое значение) не должен ронять бот."""
    monkeypatch.setenv("X_TEST_INT", "")
    assert env_int("X_TEST_INT") == 0


def test_env_int_garbage(monkeypatch):
    monkeypatch.setenv("X_TEST_INT", "123  # комментарий")
    assert env_int("X_TEST_INT") == 0


def test_env_int_valid(monkeypatch):
    monkeypatch.setenv("X_TEST_INT", " 42 ")
    assert env_int("X_TEST_INT") == 42


def test_pluralize_sets():
    assert pluralize_sets(1) == "1 партия"
    assert pluralize_sets(2) == "2 партии"
    assert pluralize_sets(5) == "5 партий"
    assert pluralize_sets(11) == "11 партий"
    assert pluralize_sets(21) == "21 партия"
    assert pluralize_sets(22) == "22 партии"


# ── Скрытие игроков с 0 матчей / график по игроку ───────────────────────────────

def _completed(challenger, challenged, winner_id, rc, when):
    return Match(
        challenger_id=challenger.id, challenged_id=challenged.id,
        status=MatchStatus.completed, winner_id=winner_id,
        sets_data=[{"w": 11, "l": 5}], rating_change=rc, completed_at=when,
    )


async def test_leaderboard_hides_zero_match_players(db):
    """Игрок без сыгранных матчей не показывается в рейтинге."""
    from bot.handlers.leaderboard import show_leaderboard

    p1, p2 = _player(1, "Alice", 1010.0), _player(2, "Bob", 990.0)
    ghost = _player(3, "Ghost", 1000.0)  # 0 матчей
    db.add_all([p1, p2, ghost])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "menu_leaderboard")
    await show_leaderboard(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Alice" in text and "Bob" in text
    assert "Ghost" not in text


async def test_leaderboard_empty_when_no_completed_matches(db):
    """Если ни у кого нет сыгранных матчей — показываем заглушку, не пустую таблицу."""
    from bot.handlers.leaderboard import show_leaderboard

    db.add_all([_player(1, "Alice"), _player(2, "Bob")])
    await db.commit()

    cb = _callback(1, "menu_leaderboard")
    await show_leaderboard(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Пока нет сыгранных матчей" in text


async def test_player_chart_invalid_id(db):
    from bot.handlers.history import show_player_rating_chart

    cb, bot = _callback(1, "player_chart_abc"), AsyncMock()
    await show_player_rating_chart(cb, db, bot)
    cb.answer.assert_awaited()
    bot.send_photo.assert_not_called()


async def test_player_chart_not_found(db):
    from bot.handlers.history import show_player_rating_chart

    cb, bot = _callback(1, "player_chart_999"), AsyncMock()
    await show_player_rating_chart(cb, db, bot)
    cb.answer.assert_awaited_with("Игрок не найден.", show_alert=True)
    bot.send_photo.assert_not_called()


async def test_player_chart_too_few_matches(db):
    from bot.handlers.history import show_player_rating_chart

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    cb, bot = _callback(9, f"player_chart_{p1.id}"), AsyncMock()
    await show_player_rating_chart(cb, db, bot)
    bot.send_photo.assert_not_called()  # нужно ≥2 матча


async def test_player_chart_sends_photo(db):
    from bot.handlers.history import show_player_rating_chart

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    db.add(_completed(p1, p2, p1.id, 8.0, datetime(2026, 6, 2, 12, 0, 0)))
    await db.commit()

    cb, bot = _callback(9, f"player_chart_{p1.id}"), AsyncMock()
    await show_player_rating_chart(cb, db, bot)
    bot.send_photo.assert_awaited()


# ── Мемная фраза под прогнозом ──────────────────────────────────────────────────

def test_match_phrase_buckets():
    from bot.utils import (
        EVEN_PHRASES,
        FAVORITE_PHRASES,
        UNDERDOG_PHRASES,
        match_phrase,
    )
    assert match_phrase(70, 0) in FAVORITE_PHRASES
    assert match_phrase(66, 0) in FAVORITE_PHRASES
    assert match_phrase(65, 0) in EVEN_PHRASES      # граница 65 → равны
    assert match_phrase(50, 0) in EVEN_PHRASES
    assert match_phrase(35, 0) in EVEN_PHRASES      # граница 35 → равны
    assert match_phrase(34, 0) in UNDERDOG_PHRASES
    assert match_phrase(5, 0) in UNDERDOG_PHRASES


def test_match_phrase_deterministic():
    from bot.utils import EVEN_PHRASES, match_phrase
    # Стабильна для одного match_id — не «прыгает» при перерисовке экрана
    assert match_phrase(50, 7) == match_phrase(50, 7)
    # Индекс по match_id
    n = len(EVEN_PHRASES)
    assert match_phrase(50, 0) == EVEN_PHRASES[0]
    assert match_phrase(50, n + 1) == EVEN_PHRASES[1]


# ── Дерби клуба ─────────────────────────────────────────────────────────────────

async def test_club_records_shows_derby(db):
    from bot.handlers.leaderboard import show_club_records

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    for i in range(3):  # самая играющая пара — Alice vs Bob
        db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1 + i, 12, 0, 0)))
    db.add(_completed(p1, p3, p1.id, 10.0, datetime(2026, 6, 9, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Дерби клуба" in text
    assert "Alice" in text and "Bob" in text


async def test_my_matches_fresh_active_valid_html(db):
    """РЕГРЕССИЯ: активный матч моложе часа давал '< 1ч' — сырой '<' ломал
    HTML-парсинг Telegram ('Unsupported start tag'), edit_text падал, спиннер
    на кнопке висел ~15 сек ('query is too old'). Теперь рендерится 'до 1ч'."""
    from bot.handlers.history import show_my_matches

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.accepted, accepted_at=now,
    ))
    await db.commit()

    cb = _callback(1, "menu_matches")
    await show_my_matches(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "до 1ч" in text
    assert "< 1" not in text  # сырой '<' не должен попасть в HTML-сообщение


async def test_dominance_matrix_escapes_player_name(db):
    """РЕГРЕССИЯ: имя игрока со спецсимволами (< > &) шло в <pre> без
    экранирования — Telegram ронял edit_text ошибкой парсинга HTML,
    матрица переставала открываться у всех игроков."""
    from bot.handlers.leaderboard import show_dominance_matrix

    p1, p2 = _player(1, "<Jerry>", 1010.0), _player(2, "Bob", 990.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "dominance_matrix")
    await show_dominance_matrix(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "<Jer" not in text  # обрезанная метка не должна пролезть сырым тегом
    assert "&lt;" in text


async def test_dbstats_escapes_player_name(db, monkeypatch):
    """РЕГРЕССИЯ: имя игрока со спецсимволами в /dbstats шло в HTML без
    экранирования (топ рейтингов и топ/боттом начислений)."""
    from bot.handlers import admin as admin_module
    from bot.handlers.admin import cmd_dbstats

    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)

    p1, p2 = _player(1, "Tom & <Jerry>", 1010.0), _player(2, "Bob", 990.0)
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    msg = _message(1, "/dbstats")
    await cmd_dbstats(msg, db)

    all_text = "".join(
        call.args[0] for call in msg.answer.call_args_list if call.args
    )
    assert "<Jerry>" not in all_text
    assert "&lt;" in all_text or "Jerry" in all_text


# ── «Сегодня» — личный мини-итог ────────────────────────────────────────────────

async def test_today_shows_personal_summary(db):
    """Экран «Сегодня» показывает зрителю его личный счёт и дельту за день."""
    from bot.handlers.leaderboard import show_today_stats

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(_completed(p1, p2, p1.id, 10.0, now))   # Alice +10
    db.add(_completed(p1, p2, p1.id, 8.0, now))    # Alice +8
    db.add(_completed(p1, p2, p2.id, 12.0, now))   # Alice −12
    db.add(_completed(p2, p3, p2.id, 20.0, now))   # Alice не участвует — в её дельту не идёт
    await db.commit()

    cb = _callback(1, "menu_today")  # смотрит Alice
    await show_today_stats(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Ты сегодня:" in text
    assert "2–1" in text            # 2 победы, 1 поражение
    assert "+6.0 pts" in text       # 10 + 8 − 12 (чужой матч Bob–Cara не учитывается)


async def test_today_personal_not_played(db):
    """Зритель сегодня не играл — личная строка это отражает, без падений."""
    from bot.handlers.leaderboard import show_today_stats

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(_completed(p2, p3, p2.id, 10.0, now))   # Alice не играла
    await db.commit()

    cb = _callback(1, "menu_today")  # смотрит Alice
    await show_today_stats(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "ещё не играл" in text


# ── Рекорды клуба: новые рекорды ────────────────────────────────────────────────

async def test_club_records_shows_peak_rating(db):
    """Высший рейтинг в истории — по peak_rating среди игравших."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice", 1050.0), _player(2, "Bob", 980.0)
    p1.peak_rating = 1075.0
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Высший рейтинг в истории" in text
    assert "1075.0" in text


async def test_club_records_shows_nagibator(db):
    """Нагибатор клуба — самое одностороннее противостояние (≥3 побед, есть перевес)."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    for i in range(4):  # Alice над Bob 4–0
        db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1 + i, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Нагибатор клуба" in text
    assert "4–0" in text


async def test_club_records_shows_current_streak(db):
    """В ударе сейчас — текущая активная серия побед (от последнего матча назад)."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p2.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))  # Bob
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 2, 12, 0, 0)))  # Alice
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 3, 12, 0, 0)))  # Alice
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 4, 12, 0, 0)))  # Alice
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "В ударе сейчас" in text  # текущая серия Alice = 3


async def test_club_records_shows_most_draws(db):
    """Больше всего ничьих — от 3 ничьих у игрока."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    for i in range(3):
        db.add(_completed(p1, p2, None, 0.0, datetime(2026, 6, 1 + i, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Больше всего ничьих" in text
    assert "3 ничьих" in text


async def test_no_most_draws_below_threshold(db):
    """2 ничьи — рекорд ещё не показывается (порог 3)."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    for i in range(2):
        db.add(_completed(p1, p2, None, 0.0, datetime(2026, 6, 1 + i, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Больше всего ничьих" not in text


async def test_club_records_shows_hottest_day(db):
    """Самый жаркий день клуба — сумма матчей всех игроков за день (от 3)."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    hot_day = datetime(2026, 6, 5, 10, 0, 0)
    db.add(_completed(p1, p2, p1.id, 10.0, hot_day))
    db.add(_completed(p1, p3, p1.id, 10.0, hot_day + timedelta(hours=1)))
    db.add(_completed(p2, p3, p2.id, 10.0, hot_day + timedelta(hours=2)))
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 6, 10, 0, 0)))  # другой день
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Самый жаркий день клуба" in text
    assert "05.06.26" in text
    assert "3 матча" in text


async def test_club_records_shows_fastest_match(db):
    """Самый быстрый матч — по accepted_at → completed_at."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.completed, winner_id=p1.id,
        sets_data=[{"w": 11, "l": 5}], rating_change=10.0,
        accepted_at=datetime(2026, 6, 1, 12, 0, 0),
        completed_at=datetime(2026, 6, 1, 12, 4, 0),  # 4 минуты
    )
    db.add(m)
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Самый быстрый матч" in text
    assert "4 мин" in text


async def test_fastest_match_skipped_without_accepted_at(db):
    """Матчи без accepted_at (старые записи) не участвуют в рекорде — не падаем."""
    from bot.handlers.leaderboard import show_club_records

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 6, 1, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "club_records")
    await show_club_records(cb, db)  # не должно упасть

    text = cb.message.edit_text.call_args[0][0]
    assert "Самый быстрый матч" not in text


# ── Скрытые ачивки: незаработанные не раскрывают имя/условие ───────────────────

async def test_hidden_achievement_masked_when_locked(db):
    """Незаработанная скрытая ачивка показывается как '🔒 ???', без имени и условия."""
    from bot.handlers.profile import show_my_achievements

    p1 = _player(1, "Alice")
    db.add(p1)
    await db.commit()

    cb = _callback(1, "my_achievements")
    await show_my_achievements(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "🔒 ???" in text
    # ни одна скрытая ачивка не раскрывает имя, пока не заработана
    assert "Король ночи" not in text
    assert "Вынес терминатора" not in text
    assert "Такова жись" not in text


async def test_hidden_achievement_revealed_when_earned(db):
    """Заработанная скрытая ачивка показывается полностью, как обычная."""
    from bot.handlers.profile import show_my_achievements

    p1 = _player(1, "Alice")
    p1.achievements = '["night_king"]'
    db.add(p1)
    await db.commit()

    cb = _callback(1, "my_achievements")
    await show_my_achievements(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Король ночи" in text
    assert "✅" in text


async def test_non_hidden_locked_achievement_still_shown(db):
    """Обычная (не скрытая) незаработанная ачивка по-прежнему показывает имя и условие."""
    from bot.handlers.profile import show_my_achievements

    p1 = _player(1, "Alice")
    db.add(p1)
    await db.commit()

    cb = _callback(1, "my_achievements")
    await show_my_achievements(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Стукнул полтинник" in text  # обычная счётная ачивка, не скрытая


async def test_fatality_no_sweat_dominator_masked_when_locked(db):
    """Три ситуативных ачивки (fatality/no_sweat/dominator) скрыты, пока не заработаны —
    того же характера, что уже скрытые Феникс/Терминатор/Такова жись."""
    from bot.handlers.profile import show_my_achievements

    p1 = _player(1, "Alice")
    db.add(p1)
    await db.commit()

    cb = _callback(1, "my_achievements")
    await show_my_achievements(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Фаталити" not in text
    assert "Даже не вспотел" not in text
    assert "То что мертво" not in text


async def test_fatality_no_sweat_dominator_revealed_when_earned(db):
    """Заработанные fatality/no_sweat/dominator показываются полностью."""
    from bot.handlers.profile import show_my_achievements

    p1 = _player(1, "Alice")
    p1.achievements = '["fatality", "no_sweat", "dominator"]'
    db.add(p1)
    await db.commit()

    cb = _callback(1, "my_achievements")
    await show_my_achievements(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "Фаталити" in text
    assert "Даже не вспотел" in text
    assert "То что мертво" in text


def test_fatality_dominator_not_in_achievement_progress_whitelist():
    """Скрытие fatality/no_sweat/dominator не задело белый список счётных целей —
    ни один из них не может быть предложен как ближайшая цель прогресса."""
    p = _p_ach([])
    result = _nearest_achievement_progress(p, _stats(wins=2, streak=2), total_players=3)
    assert result is None or "Фаталити" not in result
    assert result is None or "Даже не вспотел" not in result
    assert result is None or "То что мертво" not in result


# ── Кнопка «Вызвать» скрывается, если занят зритель или соперник ────────────────
# (профиль, H2H, «С кем сыграть?») — согласовано с send_challenge/менюшкой вызова.

async def test_player_profile_hides_challenge_when_viewer_busy(db):
    from bot.handlers.profile import show_player_profile

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)  # Alice (зритель) занята матчем с Bob
    await db.commit()

    cb = _callback(1, f"player_profile_{p3.id}")
    await show_player_profile(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Вызвать" in t for t in buttons)


async def test_player_profile_hides_challenge_when_target_busy(db):
    from bot.handlers.profile import show_player_profile

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p2, p3)  # Bob (владелец профиля) занят матчем с Cara
    await db.commit()

    cb = _callback(1, f"player_profile_{p2.id}")  # Alice смотрит профиль Bob
    await show_player_profile(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Вызвать" in t for t in buttons)


async def test_player_profile_shows_challenge_when_both_free(db):
    from bot.handlers.profile import show_player_profile

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.commit()

    cb = _callback(1, f"player_profile_{p2.id}")
    await show_player_profile(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Вызвать" in t for t in buttons)


async def test_h2h_hides_challenge_when_viewer_busy(db):
    from bot.handlers.history import show_h2h

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)  # Alice занята матчем с Bob
    await db.commit()

    cb = _callback(1, f"h2h_{p3.id}_0")
    await show_h2h(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Вызвать" in t for t in buttons)


async def test_my_matches_no_challenge_buttons_when_viewer_busy(db):
    """Если зритель занят, кнопки «Вызвать X» не показываются вообще —
    раньше показывались для всех, кто индивидуально свободен."""
    from bot.handlers.history import show_my_matches

    p1, p2, p3 = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Cara")
    db.add_all([p1, p2, p3])
    await db.flush()
    await _accepted_match(db, p1, p2)  # Alice занята матчем с Bob, Cara свободна
    await db.commit()

    cb = _callback(1, "menu_matches")
    await show_my_matches(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert not any(t.startswith("Вызвать") for t in buttons)
    assert "Заверши свой активный матч" in text
    assert "Cara" in text  # инфо-строка про свободного соперника остаётся


async def test_my_matches_shows_challenge_buttons_when_viewer_free(db):
    from bot.handlers.history import show_my_matches

    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.commit()

    cb = _callback(1, "menu_matches")
    await show_my_matches(cb, db)

    kb = cb.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert any(t.startswith("Вызвать") for t in buttons)


# ── favor_icon / единый вид сложности соперника ──────────────────────────────

def test_favor_icon_thresholds():
    from bot.utils import favor_icon

    assert favor_icon(50) == "💪 "    # соперник сильно сильнее
    assert favor_icon(-50) == "😊 "   # соперник сильно слабее
    assert favor_icon(0) == "⚡ "     # примерно равны
    assert favor_icon(35.1) == "💪 "
    assert favor_icon(-35.1) == "😊 "


async def test_my_matches_shows_favor_icon_matching_players_list_kb(db):
    """«С кем сыграть?» раньше показывал только сырые pts — теперь та же
    иконка 💪/😊/⚡, что и на экране «Кого вызвать?» (players_list_kb)."""
    from bot.handlers.history import show_my_matches

    p1 = _player(1, "Alice", rating=1000.0)
    p2 = _player(2, "Bob", rating=1100.0)  # сильнее на 100 -> 💪
    db.add_all([p1, p2])
    await db.commit()

    cb = _callback(1, "menu_matches")
    await show_my_matches(cb, db)

    text = cb.message.edit_text.call_args[0][0]
    assert "💪" in text


async def test_help_lists_icon_legend():
    from bot.handlers.start import cmd_help

    msg = AsyncMock()
    msg.answer = AsyncMock()
    await cmd_help(msg)

    text = msg.answer.call_args[0][0]
    assert "💪" in text and "❄️" in text and "🔥" in text and "👑" in text


# ── Пик рейтинга обновляется при ничьей (баг-фикс) ──────────────────────────────

async def test_peak_rating_updated_on_draw(db):
    """РЕГРЕССИЯ: при ничьей андердог растёт, но peak_rating не обновлялся."""
    p1 = _player(1, "Underdog", rating=1000.0)
    p2 = _player(2, "Favourite", rating=1200.0)
    p1.peak_rating = 1000.0
    p2.peak_rating = 1200.0
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    # Ничья 1:1 — андердог-challenger получает плюс к рейтингу
    st = _state(1)
    await st.set_state(MatchResultStates.confirming)
    await st.update_data(
        match_id=m.id, reporter_player_id=p1.id,
        sets_data=[{"reporter": 11, "opponent": 5}, {"reporter": 5, "opponent": 11}],
        is_draw=True,
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert p1.rating > 1000.0            # андердог вырос
    assert p1.peak_rating == p1.rating   # пик обновился вместе с рейтингом


# ── pluralize_wins ──────────────────────────────────────────────────────────────

def test_pluralize_wins():
    from bot.utils import pluralize_wins
    assert pluralize_wins(1) == "1 победа"
    assert pluralize_wins(2) == "2 победы"
    assert pluralize_wins(5) == "5 побед"
    assert pluralize_wins(11) == "11 побед"
    assert pluralize_wins(21) == "21 победа"


# ── Итоги дня: новые секции ─────────────────────────────────────────────────────

async def test_daily_summary_new_sections(monkeypatch):
    """Итоги дня содержат полоску формы, Нагибателя дня и Дерби дня."""
    import bot.scheduler as sched

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session", factory)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for _ in range(3):  # Alice 3–0 над Bob сегодня
            s.add(_completed(p1, p2, p1.id, 10.0, now))
        await s.commit()

    bot = AsyncMock()
    await sched.send_daily_summary(bot)
    await engine.dispose()

    text = bot.send_message.call_args_list[0][0][1]
    assert "🟩" in text                    # полоска формы
    assert "Нагибатель дня" in text
    assert "Чаще всего самбовались" in text
    assert "3 победы подряд" in text       # текущая серия Alice
    assert "Отрицательный рост" in text     # Bob ушёл в минус
    assert "Матчи дня:" in text             # общий лог матчей со счётом


async def test_weekly_digest_standings_and_heroes(monkeypatch):
    """Итоги недели: компактный «Топ недели» вместо списка матчей + новые герои."""
    import bot.scheduler as sched

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session", factory)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for i in range(3):  # Alice 3–0 над Bob за неделю
            s.add(_completed(p1, p2, p1.id, 10.0, now - timedelta(hours=5 - i)))
        # драматичный матч недели — дьюсы, решилось в 3-й партии (drama ≥ 8)
        s.add(Match(
            challenger_id=p1.id, challenged_id=p2.id,
            status=MatchStatus.completed, winner_id=p1.id,
            sets_data=[{"w": 12, "l": 10}, {"w": 10, "l": 12}, {"w": 12, "l": 10}],
            rating_change=10.0, completed_at=now - timedelta(hours=1),
        ))
        await s.commit()

    bot = AsyncMock()
    await sched.send_weekly_digest(bot)
    await engine.dispose()

    text = bot.send_message.call_args_list[0][0][1]
    assert "Топ недели" in text                # стендинг вместо списка матчей
    assert "Матчи:" not in text                # длинного списка матчей больше нет
    assert "Чаще всего самбовались" in text     # дерби недели
    assert "Нагибатель недели" in text          # серия за неделю
    assert "Матч недели" in text
    assert "Отрицательный рост" in text


# ── send_match_reminders (напоминание про незавершённый матч, от 24ч) ──────────

async def test_reminder_sent_for_stale_match(monkeypatch):
    """Матч старше 24 часов без reminder_sent — оба участника получают напоминание."""
    import bot.scheduler as sched

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session", factory)

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(Match(
            challenger_id=p1.id, challenged_id=p2.id,
            status=MatchStatus.accepted, accepted_at=old,
        ))
        await s.commit()

    bot = AsyncMock()
    await sched.send_match_reminders(bot)
    await engine.dispose()

    assert bot.send_message.call_count == 2  # оба участника
    texts = [c.args[1] for c in bot.send_message.call_args_list]
    assert any("Alice" not in t and "Bob" in t for t in texts)  # Alice видит про Bob
    assert any("Bob" not in t and "Alice" in t for t in texts)  # Bob видит про Alice
    for c in bot.send_message.call_args_list:
        assert c.kwargs.get("reply_markup") is not None  # busy_with_match_kb с кнопками


async def test_reminder_not_sent_for_fresh_match(monkeypatch):
    """Матч моложе 24 часов — напоминание не шлётся."""
    import bot.scheduler as sched

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session", factory)

    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(Match(
            challenger_id=p1.id, challenged_id=p2.id,
            status=MatchStatus.accepted, accepted_at=recent,
        ))
        await s.commit()

    bot = AsyncMock()
    await sched.send_match_reminders(bot)
    await engine.dispose()

    bot.send_message.assert_not_called()


async def test_reminder_not_sent_twice(monkeypatch):
    """Идемпотентность: повторный запуск не шлёт напоминание уже отмеченному матчу."""
    import bot.scheduler as sched

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session", factory)

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(Match(
            challenger_id=p1.id, challenged_id=p2.id,
            status=MatchStatus.accepted, accepted_at=old,
        ))
        await s.commit()

    bot = AsyncMock()
    await sched.send_match_reminders(bot)
    await sched.send_match_reminders(bot)
    await engine.dispose()

    assert bot.send_message.call_count == 2  # не 4 — второй прогон ничего не шлёт


async def test_monthly_summary_renamed_and_heroes(monkeypatch):
    """Итоги месяца: «Тяжелее всех» → «Отрицательный рост» + дерби/нагибатель."""
    import bot.scheduler as sched

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched, "async_session", factory)

    # Окно прошлого месяца вычисляем так же, как функция — чтобы тест не зависел от даты
    msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + sched.MSK_OFFSET
    month_end = msk_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_last = month_end - timedelta(days=1)
    month_start = prev_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    inside = (month_start + timedelta(days=10)) - sched.MSK_OFFSET

    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        for i in range(3):  # Alice 3–0 над Bob в прошлом месяце
            s.add(_completed(p1, p2, p1.id, 10.0, inside + timedelta(hours=i)))
        await s.commit()

    bot = AsyncMock()
    await sched.send_monthly_summary(bot)
    await engine.dispose()

    text = bot.send_message.call_args_list[0][0][1]
    assert "Отрицательный рост" in text
    assert "Тяжелее всех" not in text
    assert "Чаще всего самбовались" in text
    assert "Нагибатель месяца" in text


# ── fsm_reset_notice: сброс FSM рестартом бота (MemoryStorage) ─────────────────

async def test_fsm_reset_notice_answers_callback_and_shows_message():
    """При пустом состоянии (рестарт бота) нажатие кнопки шага ввода/подтверждения
    не должно вешать спиннер — колбэк отвечен, пользователю показано объяснение."""
    cb = _callback(1, "confirm_42")

    await fsm_reset_notice(cb)

    cb.answer.assert_awaited_once()
    cb.message.edit_text.assert_awaited_once()
    text = cb.message.edit_text.call_args[0][0]
    assert "перезапускался" in text


async def test_fsm_reset_notice_covers_all_step_callbacks():
    """Хендлер срабатывает на все callback_data шагов ввода/подтверждения."""
    for data in ("finish_sets_1", "undo_set_1", "redo_1", "confirm_1"):
        cb = _callback(1, data)
        await fsm_reset_notice(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()


# ── Гонки и граничные сценарии (CAS-guard, повторные тапы, невалидный ввод) ────

async def test_confirm_result_double_tap_records_once(db):
    """Двойной тап «Всё верно» по одному матчу: результат записан ровно один раз,
    рейтинг пересчитан один раз, второй вызов не падает и не начисляет дельту повторно."""
    p1, p2 = _player(1, "Winner", rating=1000.0), _player(2, "Loser", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    st = await _confirming_state(m.id, p1.id, [{"reporter": 11, "opponent": 5}])
    bot = AsyncMock()

    cb1 = _callback(1, f"confirm_{m.id}")
    await confirm_result(cb1, db, st, bot)
    winner_rating_after_first = p1.rating
    loser_rating_after_first = p2.rating
    assert m.status == MatchStatus.completed
    assert winner_rating_after_first != 1000.0

    # Второй тап — состояние уже очищено первым вызовом в реальном апдейте,
    # но здесь эмулируем гонку: колбэк с тем же match_id прилетает повторно
    # до того, как клиент успел получить новое состояние (CAS должен блокировать).
    st2 = await _confirming_state(m.id, p1.id, [{"reporter": 11, "opponent": 5}])
    cb2 = _callback(1, f"confirm_{m.id}")
    await confirm_result(cb2, db, st2, bot)

    assert p1.rating == winner_rating_after_first  # дельта не начислена повторно
    assert p2.rating == loser_rating_after_first   # без изменений от второго тапа
    cb2.message.edit_text.assert_awaited_once()
    assert "уже завершён" in cb2.message.edit_text.call_args.args[0]
    cb2.answer.assert_awaited()


async def test_confirm_result_on_declined_match_is_blocked(db):
    """Матч уже отменён (declined) → внести результат нельзя, CAS не проходит."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = Match(
        challenger_id=p1.id, challenged_id=p2.id,
        status=MatchStatus.declined, accepted_at=datetime(2026, 6, 1, 12, 0, 0),
    )
    db.add(m)
    await db.commit()

    st = await _confirming_state(m.id, p1.id, [{"reporter": 11, "opponent": 5}])
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert m.status == MatchStatus.declined  # не затёрто
    assert p1.rating == 1000.0 and p2.rating == 1000.0
    cb.message.edit_text.assert_awaited_once()
    assert "уже завершён" in cb.message.edit_text.call_args.args[0]


async def test_double_cancel_second_call_gets_clean_response(db):
    """Два подряд вызова отмены одного матча (гонка обоих участников): второй
    получает корректный ответ, а не исключение или дублирующее уведомление."""
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    bot = AsyncMock()
    cb1 = _callback(1, f"cancel_yes_{m.id}")
    await do_cancel_match(cb1, db, bot)
    assert m.status == MatchStatus.declined
    first_notify_calls = bot.send_message.call_count

    cb2 = _callback(2, f"cancel_yes_{m.id}")
    await do_cancel_match(cb2, db, bot)

    cb2.answer.assert_any_call("Матч уже завершён или не найден.", show_alert=True)
    assert bot.send_message.call_count == first_notify_calls  # уведомление не задублировано


async def test_start_report_on_missing_match_shows_alert(db):
    """Ввод результата по матчу, которого больше нет в БД, — внятная ошибка,
    без исключения."""
    p1 = _player(1, "Alice")
    db.add(p1)
    await db.commit()

    st = _state(1)
    cb = _callback(1, "report_9999")
    await start_report(cb, db, st)

    cb.answer.assert_awaited_once_with("Матч не найден или уже завершён.", show_alert=True)
    assert await st.get_state() is None


@pytest.mark.parametrize("text", ["10:9", "11:10", "-1:5", "5:-1", "abc:def", "11:"])
async def test_process_set_score_rejects_invalid_inputs(db, text):
    """Нарушение правил настольного тенниса или мусор вместо цифр — партия не
    добавляется, пользователь получает объяснение."""
    st = await _prep_input_state(1)
    msg = _message(1, text)
    await process_set_score(msg, st)
    data = await st.get_data()
    assert data["sets_data"] == []
    msg.answer.assert_called()


async def test_confirm_result_winner_floor_not_exceeded_downward(db):
    """Победитель-новичок с рейтингом на полу (1000.0) не проваливается ниже него,
    даже формально выигрывая с отрицательной дельтой не бывает — но пол
    проверяется явно на стороне проигравшего."""
    p1 = _player(1, "Winner", rating=1000.0)
    p2 = _player(2, "Loser", rating=1000.0)
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    st = await _confirming_state(m.id, p1.id, [{"reporter": 11, "opponent": 9}])
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert p2.rating >= 1000.0 - 1e-9  # проигравший-новичок не ниже пола 1000.0


async def test_confirm_result_veteran_floor_is_900(db):
    """Проигравший-ветеран (15+ завершённых матчей) не проваливается ниже пола 900.0."""
    p1 = _player(1, "Winner", rating=1000.0)
    p2 = _player(2, "Loser", rating=901.0)
    p3 = _player(3, "Filler")
    db.add_all([p1, p2, p3])
    await db.flush()

    # У p2 ровно 15 завершённых матчей → уже ветеран (пол 900)
    for i in range(15):
        db.add(Match(
            challenger_id=p2.id, challenged_id=p3.id,
            status=MatchStatus.completed, winner_id=p3.id,
            sets_data=[{"w": 11, "l": 0}], rating_change=5.0,
            completed_at=datetime(2026, 5, 1 + i, 12, 0, 0),
        ))
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    st = await _confirming_state(m.id, p1.id, [{"reporter": 11, "opponent": 0}])
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert p2.rating >= 900.0 - 1e-9


# ── Новые пасхалки после матча ───────────────────────────────────────────────

def _egg_ctx(**overrides) -> dict:
    """Минимальный ctx для прямого вызова _send_winner_eggs — изолирует одно
    условие за раз, без прогона через полный ELO-расчёт confirm_result."""
    ctx = {
        "flawless": False, "clean_sweep": False, "shutout": False, "deuce_decider": False,
        "comeback": False, "marathon": False,
        "old_winner_rating": 1000.0, "old_loser_rating": 1000.0,
        "previous_wins": 1, "streak": 1, "loss_streak_before": 0,
        "first_blood": False, "revenge": False, "first_time_top1": False, "winner_total": 5,
        "loss_streak": 0, "prev_losses": 0, "loser_total": 5,
    }
    ctx.update(overrides)
    return ctx


def _texts(bot: AsyncMock) -> list[str]:
    return [c.args[1] for c in bot.send_message.await_args_list]


# -- shutout / deuce_decider / круглый рейтинг (winner-only) --

async def test_shutout_egg_fires_when_all_sets_blank():
    winner, loser = _player(1, "A"), _player(2, "B")
    winner.rating = 1013.7   # не кратно 50 — не мешает другому условию
    bot = AsyncMock()
    await _send_winner_eggs(bot, winner, loser, _egg_ctx(shutout=True))
    assert any("Читы включил" in t for t in _texts(bot))


async def test_no_shutout_egg_when_one_set_not_blank():
    """flawless (хотя бы одна партия 11:0) может сработать, а shutout (все) — нет."""
    winner, loser = _player(1, "A"), _player(2, "B")
    winner.rating = 1013.7
    bot = AsyncMock()
    await _send_winner_eggs(bot, winner, loser, _egg_ctx(flawless=True, shutout=False))
    texts = _texts(bot)
    assert any("Flawless" in t for t in texts)
    assert not any("Читы включил" in t for t in texts)


async def test_deuce_decider_egg_fires():
    winner, loser = _player(1, "A"), _player(2, "B")
    winner.rating = 1013.7
    bot = AsyncMock()
    await _send_winner_eggs(bot, winner, loser, _egg_ctx(deuce_decider=True))
    assert any("Драматично" in t for t in _texts(bot))


async def test_no_deuce_decider_egg_when_false():
    winner, loser = _player(1, "A"), _player(2, "B")
    winner.rating = 1013.7
    bot = AsyncMock()
    await _send_winner_eggs(bot, winner, loser, _egg_ctx(deuce_decider=False))
    assert not any("Драматично" in t for t in _texts(bot))


async def test_round_rating_egg_fires_on_multiple_of_50():
    winner, loser = _player(1, "A"), _player(2, "B")
    winner.rating = 1050.0
    bot = AsyncMock()
    await _send_winner_eggs(bot, winner, loser, _egg_ctx())
    assert any("Ровно 1050.0" in t for t in _texts(bot))


async def test_no_round_rating_egg_on_non_round_value():
    winner, loser = _player(1, "A"), _player(2, "B")
    winner.rating = 1013.7
    bot = AsyncMock()
    await _send_winner_eggs(bot, winner, loser, _egg_ctx())
    assert not any("Как ты это подгадал" in t for t in _texts(bot))


# -- время матча: ночь / выходной / вечер пятницы --

async def test_time_based_egg_night():
    p1, p2 = _player(1, "A"), _player(2, "B")
    bot = AsyncMock()
    # 2026-06-03 (среда) 01:00 UTC -> 04:00 МСК — ночь
    await _send_time_based_eggs(bot, [p1, p2], datetime(2026, 6, 3, 1, 0, 0))
    assert any("не спится" in t for t in _texts(bot))


async def test_time_based_egg_weekend():
    p1, p2 = _player(1, "A"), _player(2, "B")
    bot = AsyncMock()
    # 2026-06-06 (суббота) 12:00 UTC -> 15:00 МСК — выходной, не ночь
    await _send_time_based_eggs(bot, [p1, p2], datetime(2026, 6, 6, 12, 0, 0))
    assert any("ради тенниса" in t for t in _texts(bot))


async def test_time_based_egg_friday_evening():
    p1, p2 = _player(1, "A"), _player(2, "B")
    bot = AsyncMock()
    # 2026-06-05 (пятница) 16:00 UTC -> 19:00 МСК
    await _send_time_based_eggs(bot, [p1, p2], datetime(2026, 6, 5, 16, 0, 0))
    assert any("неделю красиво" in t for t in _texts(bot))


async def test_time_based_egg_silent_on_regular_afternoon():
    p1, p2 = _player(1, "A"), _player(2, "B")
    bot = AsyncMock()
    # 2026-06-03 (среда) 12:00 UTC -> 15:00 МСК — обычный будний день
    await _send_time_based_eggs(bot, [p1, p2], datetime(2026, 6, 3, 12, 0, 0))
    bot.send_message.assert_not_called()


async def test_time_based_egg_night_takes_priority_over_weekend():
    """Суббота 2 часа ночи по МСК — оба условия подходят, срабатывает только ночь."""
    p1, p2 = _player(1, "A"), _player(2, "B")
    bot = AsyncMock()
    # 2026-06-06 (суббота) 23:00 UTC 5 июня -> 02:00 МСК 6 июня
    await _send_time_based_eggs(bot, [p1, p2], datetime(2026, 6, 5, 23, 0, 0))
    assert bot.send_message.await_count == 2   # по разу каждому, не больше
    assert all("не спится" in t for t in _texts(bot))


# -- юбилейная личная встреча --

async def test_h2h_milestone_egg_fires_at_10th_meeting(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(10):
        db.add(_completed(p1, p2, p1.id, 5.0, base + timedelta(days=i)))
    await db.commit()

    bot = AsyncMock()
    await _send_h2h_milestone_egg(bot, db, p1, p2)
    assert any("Юбилейная битва" in t and "10-я" in t for t in _texts(bot))


async def test_h2h_milestone_egg_silent_on_non_round_count(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    base = datetime(2026, 6, 1, 12, 0, 0)
    for i in range(11):
        db.add(_completed(p1, p2, p1.id, 5.0, base + timedelta(days=i)))
    await db.commit()

    bot = AsyncMock()
    await _send_h2h_milestone_egg(bot, db, p1, p2)
    bot.send_message.assert_not_called()


# -- быстрый реванш --

async def test_quick_rematch_egg_fires_within_10_minutes(db):
    """Гэп меряется от СТАРТА текущего матча (created_at), а не от его конца —
    та же семантика, что у ачивки no_rest_win."""
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    prev = _completed(p1, p2, p1.id, 5.0, datetime(2026, 6, 1, 12, 0, 0))
    db.add(prev)
    await db.commit()

    current = Match(
        challenger_id=p2.id, challenged_id=p1.id, status=MatchStatus.completed,
        winner_id=p2.id, sets_data=[{"w": 11, "l": 5}], rating_change=5.0,
        created_at=datetime(2026, 6, 1, 12, 5, 0), completed_at=datetime(2026, 6, 1, 12, 5, 0),
    )
    db.add(current)
    await db.commit()

    bot = AsyncMock()
    await _send_quick_rematch_egg(bot, db, p1, p2, current.created_at, current.id)
    assert any("Не наигрался" in t for t in _texts(bot))


async def test_quick_rematch_egg_fires_even_if_rematch_itself_runs_long(db):
    """РЕГРЕССИЯ: раньше гэп мерился от completed_at этого матча — честный
    быстрый реванш, который сам оказался долгим (много партий), не считался
    «быстрым», хотя начался сразу после предыдущего."""
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    prev = _completed(p1, p2, p1.id, 5.0, datetime(2026, 6, 1, 12, 0, 0))
    db.add(prev)
    await db.commit()

    # начат через 30 секунд после предыдущего, но сам матч тянулся 12 минут
    current = Match(
        challenger_id=p2.id, challenged_id=p1.id, status=MatchStatus.completed,
        winner_id=p2.id, sets_data=[{"w": 11, "l": 5}], rating_change=5.0,
        created_at=datetime(2026, 6, 1, 12, 0, 30), completed_at=datetime(2026, 6, 1, 12, 12, 30),
    )
    db.add(current)
    await db.commit()

    bot = AsyncMock()
    await _send_quick_rematch_egg(bot, db, p1, p2, current.created_at, current.id)
    assert any("Не наигрался" in t for t in _texts(bot))


async def test_quick_rematch_egg_silent_when_gap_too_large(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    prev = _completed(p1, p2, p1.id, 5.0, datetime(2026, 6, 1, 12, 0, 0))
    db.add(prev)
    await db.commit()

    current = Match(
        challenger_id=p2.id, challenged_id=p1.id, status=MatchStatus.completed,
        winner_id=p2.id, sets_data=[{"w": 11, "l": 5}], rating_change=5.0,
        created_at=datetime(2026, 6, 1, 13, 0, 0), completed_at=datetime(2026, 6, 1, 13, 0, 0),
    )
    db.add(current)
    await db.commit()

    bot = AsyncMock()
    await _send_quick_rematch_egg(bot, db, p1, p2, current.created_at, current.id)
    bot.send_message.assert_not_called()


async def test_quick_rematch_egg_silent_without_prior_match(db):
    p1, p2 = _player(1, "A"), _player(2, "B")
    db.add_all([p1, p2])
    await db.flush()
    current = _completed(p1, p2, p1.id, 5.0, datetime(2026, 6, 1, 12, 0, 0))
    db.add(current)
    await db.commit()

    bot = AsyncMock()
    await _send_quick_rematch_egg(bot, db, p1, p2, current.created_at, current.id)
    bot.send_message.assert_not_called()


# -- интеграционная проверка: полный флоу через confirm_result --

async def test_confirm_result_shutout_egg_fires_end_to_end(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    m = await _accepted_match(db, p1, p2)
    await db.commit()

    st = await _confirming_state(
        m.id, p1.id,
        [{"reporter": 11, "opponent": 0}, {"reporter": 11, "opponent": 0}],
    )
    cb, bot = _callback(1, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)

    assert any("Читы включил" in t for t in _texts(bot))
