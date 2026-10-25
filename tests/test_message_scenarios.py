"""
Сценарии матча целиком: какие сообщения получает каждый игрок и в каком порядке
(v2.212.0; основа — ручной прогон 21.10, после которого нашли «Трон твой» за обгон
по очкам и неверные склонения). Проверяются ключевые фразы, порядок и звук, а не
весь текст: пулы фраз и подписи правятся без правки этих тестов.
Запуск: pytest tests/test_message_scenarios.py
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from bot.handlers.match_result import confirm_result
from bot.utils import NEWCOMER_THRESHOLD
from tests.conftest import _callback, _completed, _player
from tests.test_boss_fight import _accepted_match, _confirming_state, _seed_matches

WIN = [{"reporter": 11, "opponent": 5}, {"reporter": 11, "opponent": 5}]
DRAW = [{"reporter": 11, "opponent": 5}, {"reporter": 5, "opponent": 11}]
BASE = datetime(2026, 6, 1, 12, 0, 0)

RESULT = "Результат матча внесён"
OVERTAKE = "Тебя догнал по очкам"


async def _play(db, challenger, challenged, reporter, sets, boss=False, draw=False):
    """Вносит результат матча; возвращает бота с записью всех отправленных сообщений."""
    m = await _accepted_match(db, challenger, challenged, is_boss_fight=boss)
    await db.commit()
    st = await _confirming_state(m.id, reporter.id, sets, is_draw=draw)
    cb, bot = _callback(reporter.telegram_id, f"confirm_{m.id}"), AsyncMock()
    await confirm_result(cb, db, st, bot)
    return bot


def _sent(bot, telegram_id):
    """[(текст, без звука?)] в порядке отправки игроку."""
    return [(c.args[1], bool(c.kwargs.get("disable_notification")))
            for c in bot.send_message.await_args_list if c.args[0] == telegram_id]


def _index(sent, needle):
    for i, (text, _quiet) in enumerate(sent):
        if needle in text:
            return i
    return None


async def _club_with_champion(db):
    """Чемпион и претендент с историей (чемпион должен быть в рейтинге до матча)."""
    champion = _player(1, "Champion", rating=1000.0)
    champion.is_champion = True
    runner = _player(2, "Runner", rating=1010.0)
    filler = _player(3, "Filler")
    db.add_all([champion, runner, filler])
    await db.flush()
    await _seed_matches(db, champion, filler, 2, BASE)
    await _seed_matches(db, runner, filler, NEWCOMER_THRESHOLD, BASE)
    await db.commit()
    return champion, runner, filler


async def test_boss_fight_loser_reads_result_before_throne_news(db):
    champion, runner, _ = await _club_with_champion(db)
    bot = await _play(db, runner, champion, runner, WIN, boss=True)
    loser = _sent(bot, champion.telegram_id)
    result, news = _index(loser, RESULT), _index(loser, "Новый чемпион")
    assert result is not None and news is not None and result < news
    achievement = _index(loser, "Смена караула")
    assert achievement is not None and result < achievement
    assert _index(_sent(bot, runner.telegram_id), "Трон твой") is not None


async def test_boss_fight_defense_news_after_result(db):
    champion, runner, _ = await _club_with_champion(db)
    bot = await _play(db, runner, champion, champion, WIN, boss=True)
    challenger = _sent(bot, runner.telegram_id)
    result, news = _index(challenger, RESULT), _index(challenger, "Трон удержан")
    assert result is not None and news is not None and result < news
    assert _index(_sent(bot, champion.telegram_id), "Трон твой") is None


async def test_boss_fight_broadcasts_are_silent_but_result_has_sound(db):
    champion, runner, _ = await _club_with_champion(db)
    bot = await _play(db, runner, champion, runner, WIN, boss=True)
    loser = _sent(bot, champion.telegram_id)
    assert loser[_index(loser, RESULT)][1] is False          # результат — со звуком
    assert loser[_index(loser, "Новый чемпион")][1] is True  # рассылка — тихо


async def test_overtake_by_points_gives_no_throne_and_quiet_note_to_champion(db):
    champion, runner, filler = await _club_with_champion(db)
    runner.rating = 995.0
    await db.commit()
    bot = await _play(db, runner, filler, runner, WIN)
    assert runner.rating > champion.rating and runner.is_champion is False
    to_runner = _sent(bot, runner.telegram_id)
    assert _index(to_runner, "обошёл чемпиона") is not None
    assert _index(to_runner, "Трон твой") is None
    note = _sent(bot, champion.telegram_id)
    assert note[_index(note, OVERTAKE)][1] is True


async def test_draw_has_no_throne_news_and_quiet_eggs(db):
    a, b = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([a, b])
    await db.flush()
    bot = await _play(db, a, b, a, DRAW, draw=True)
    for p in (a, b):
        sent = _sent(bot, p.telegram_id)
        assert sent[_index(sent, "Договорнячок")][1] is True
        assert _index(sent, "Трон") is None


async def test_first_win_ever_gets_achievement_not_duplicate_first_blood_egg(db):
    a, b = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([a, b])
    await db.flush()
    bot = await _play(db, a, b, a, WIN)
    winner = _sent(bot, a.telegram_id)
    assert _index(winner, "Одержать первую победу в карьере") is not None     # ачивка
    assert _index(winner, "Первая кровь — ") is None                         # пасхалка не дублирует


async def test_first_win_over_this_opponent_still_gets_first_blood_egg(db):
    a, b, c = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Carl")
    db.add_all([a, b, c])
    await db.flush()
    db.add(_completed(a, c, a.id, rc=5.0, when=BASE))        # у Алисы уже есть победа — над другим
    await db.commit()
    bot = await _play(db, a, b, a, WIN)
    winner = _sent(bot, a.telegram_id)
    egg = _index(winner, "Первая кровь — ")
    assert egg is not None and winner[egg][1] is True


async def test_personal_records_arrive_as_one_quiet_message(db):
    a, b, c = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Carl")
    db.add_all([a, b, c])
    await db.flush()
    for i in range(4):                                       # серия побед и «лучшие» значения вперёд
        db.add(_completed(a, c, a.id, rc=5.0, when=BASE - timedelta(days=30 - i)))
    await db.commit()
    bot = await _play(db, a, b, a, WIN)
    records = [(t, q) for t, q in _sent(bot, a.telegram_id) if "Личные рекорды" in t]
    assert len(records) == 1
    text, quiet = records[0]
    assert quiet is True and text.startswith("💎 <b>Личные рекорды</b>") and text.count("личный рекорд") >= 2
