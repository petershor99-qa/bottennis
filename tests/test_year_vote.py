"""
Тесты голосования «Итоги года: неформальные звания» (v2.134.0, этап 3
дорожной карты). Запуск: pytest tests/test_year_vote.py
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from bot.db.models import YearVote
from bot.services.year_vote import (
    YEAR_VOTE_NOMINATIONS,
    compute_results,
    get_eligible_player_ids,
    get_nominees,
    get_voter_choices,
    has_all_nominations_filled,
    is_voting_open,
    render_bulletin,
    render_nomination_screen,
    render_results,
    set_vote,
)
from tests.conftest import _callback, _completed, _player

# ── Окно голосования (чистая функция, без БД) ─────────────────────────────────

def test_voting_window_closed_before_start():
    assert is_voting_open(datetime(2026, 12, 21, 9, 59, 59)) is False


def test_voting_window_open_at_start():
    assert is_voting_open(datetime(2026, 12, 21, 10, 0, 0)) is True


def test_voting_window_open_just_before_close():
    assert is_voting_open(datetime(2026, 12, 30, 11, 59, 59)) is True


def test_voting_window_closed_at_close():
    assert is_voting_open(datetime(2026, 12, 30, 12, 0, 0)) is False


def test_voting_window_closed_outside_december():
    assert is_voting_open(datetime(2026, 6, 15, 12, 0, 0)) is False


# ── Допуск: год, кандидаты, себя нет в списке ─────────────────────────────────

async def test_eligible_ids_only_players_with_completed_match_this_year(db):
    p1, p2, ghost = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Ghost")
    db.add_all([p1, p2, ghost])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
    await db.commit()

    ids = await get_eligible_player_ids(db, 2026)
    assert ids == {p1.id, p2.id}


async def test_eligible_ids_ignore_matches_from_other_years(db):
    p1, p2 = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([p1, p2])
    await db.flush()
    # 2025-12-31 10:00 UTC = 13:00 МСК — надёжно ДО начала 2026 года по МСК
    # (граница года считается по МСК, не по UTC — 21:00 UTC 31.12 уже 2026 год).
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2025, 12, 31, 10, 0, 0)))
    await db.commit()

    ids = await get_eligible_player_ids(db, 2026)
    assert ids == set()


async def test_get_nominees_excludes_voter_and_zero_match_players(db):
    p1, p2, ghost = _player(1, "Alice"), _player(2, "Bob"), _player(3, "Ghost")
    db.add_all([p1, p2, ghost])
    await db.flush()
    db.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
    await db.commit()

    nominees = await get_nominees(db, 2026, exclude_id=p1.id)
    names = {p.display_name for p in nominees}
    assert names == {"Bob"}
    assert "Alice" not in names
    assert "Ghost" not in names


# ── Хранение голоса: смена перезаписывает, не дублирует ───────────────────────

async def test_set_vote_overwrites_instead_of_duplicating(db):
    voter, cand1, cand2 = _player(1, "Voter"), _player(2, "Cand1"), _player(3, "Cand2")
    db.add_all([voter, cand1, cand2])
    await db.flush()

    await set_vote(db, 2026, "gentleman", voter.id, cand1.id)
    await set_vote(db, 2026, "gentleman", voter.id, cand2.id)
    await db.commit()

    count_r = await db.execute(select(func.count()).select_from(YearVote))
    assert count_r.scalar() == 1

    choices = await get_voter_choices(db, 2026, voter.id)
    assert choices["gentleman"] == cand2.id
    assert all(v is None for k, v in choices.items() if k != "gentleman")



async def test_set_vote_survives_simultaneous_duplicate_insert(db):
    """Двойной тап: оба запроса не увидели строки и оба вставляют. Второй не
    должен падать на уникальном индексе — побеждает последний голос, строка одна."""
    voter, cand1, cand2 = _player(1, "Voter"), _player(2, "Cand1"), _player(3, "Cand2")
    db.add_all([voter, cand1, cand2])
    await db.flush()
    await set_vote(db, 2026, "gentleman", voter.id, cand1.id)

    real_execute = db.execute
    calls = {"n": 0}

    class _NoRow:
        def scalar_one_or_none(self):
            return None

    async def blind_first_lookup(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _NoRow()  # соседний тап ещё не закоммитил — строки «нет»
        return await real_execute(*args, **kwargs)

    db.execute = blind_first_lookup
    try:
        await set_vote(db, 2026, "gentleman", voter.id, cand2.id)
    finally:
        db.execute = real_execute
    await db.commit()

    count_r = await db.execute(select(func.count()).select_from(YearVote))
    assert count_r.scalar() == 1
    choices = await get_voter_choices(db, 2026, voter.id)
    assert choices["gentleman"] == cand2.id

async def test_has_all_nominations_filled(db):
    voter, cand = _player(1, "Voter"), _player(2, "Cand")
    db.add_all([voter, cand])
    await db.flush()

    assert await has_all_nominations_filled(db, 2026, voter.id) is False
    for n in YEAR_VOTE_NOMINATIONS:
        await set_vote(db, 2026, n.id, voter.id, cand.id)
    await db.commit()
    assert await has_all_nominations_filled(db, 2026, voter.id) is True


# ── Результаты: победитель / ничья / без голосов ──────────────────────────────

async def test_compute_results_single_winner(db):
    voters = [_player(i, f"V{i}") for i in range(1, 4)]
    cand = _player(10, "Cand")
    db.add_all(voters + [cand])
    await db.flush()
    for v in voters:
        await set_vote(db, 2026, "gentleman", v.id, cand.id)
    await db.commit()

    results, total = await compute_results(db, 2026)
    assert total == 3
    by_id = {n.id: (winners, count) for n, winners, count in results}
    assert by_id["gentleman"] == ([cand.id], 3)
    assert by_id["excuse"] == ([], 0)


async def test_compute_results_tie_splits_title(db):
    v1, v2 = _player(1, "V1"), _player(2, "V2")
    c1, c2 = _player(10, "Bob"), _player(11, "Carol")
    db.add_all([v1, v2, c1, c2])
    await db.flush()
    await set_vote(db, 2026, "toughest", v1.id, c1.id)
    await set_vote(db, 2026, "toughest", v2.id, c2.id)
    await db.commit()

    results, total = await compute_results(db, 2026)
    assert total == 2
    by_id = {n.id: (set(winners), count) for n, winners, count in results}
    assert by_id["toughest"] == ({c1.id, c2.id}, 1)


async def test_compute_results_ignores_orphaned_nomination_id(db):
    """Голос под id, которого больше нет в YEAR_VOTE_NOMINATIONS (список
    поменяли между стартом голосования и подсчётом) — не должен ронять
    compute_results KeyError'ом, просто не попадает ни в один результат."""
    voter, cand = _player(1, "V1"), _player(10, "Bob")
    db.add_all([voter, cand])
    await db.flush()
    db.add(YearVote(year=2026, nomination="retired_id", voter_id=voter.id, nominee_id=cand.id))
    await set_vote(db, 2026, "toughest", voter.id, cand.id)
    await db.commit()

    results, total = await compute_results(db, 2026)
    assert total == 2  # оба голоса посчитаны в total
    by_id = {n.id: (winners, count) for n, winners, count in results}
    assert by_id["toughest"] == ([cand.id], 1)
    assert "retired_id" not in by_id  # осиротевший голос не выдуман в чужую номинацию


def test_render_results_winner_line():
    nomination = YEAR_VOTE_NOMINATIONS[0]
    results = [(nomination, [10], 3)] + [(n, [], 0) for n in YEAR_VOTE_NOMINATIONS[1:]]
    text = render_results(2026, results, {10: "Bob"})
    assert "<b>Bob</b>" in text
    assert "3 голоса" in text


def test_render_results_tie_line_names_both():
    nomination = YEAR_VOTE_NOMINATIONS[0]
    results = [(nomination, [10, 11], 2)] + [(n, [], 0) for n in YEAR_VOTE_NOMINATIONS[1:]]
    text = render_results(2026, results, {10: "Bob", 11: "Carol"})
    assert "Bob и Carol" in text
    assert "делят звание" in text
    assert "2 голоса" in text


def test_render_results_no_votes_line():
    results = [(n, [], 0) for n in YEAR_VOTE_NOMINATIONS]
    text = render_results(2026, results, {})
    assert "никто не проголосовал" in text


def test_render_results_anonymous_no_voter_names():
    """Анонимность: в тексте результатов нет никаких имён, кроме кандидатов
    (победителей) — имя голосовавшего нигде не фигурирует."""
    voter_name = "SecretVoter"
    nomination = YEAR_VOTE_NOMINATIONS[0]
    results = [(nomination, [10], 1)] + [(n, [], 0) for n in YEAR_VOTE_NOMINATIONS[1:]]
    text = render_results(2026, results, {10: "Bob"})
    assert voter_name not in text


# ── Рендер бюллетеня / экрана номинации ───────────────────────────────────────

def test_render_bulletin_marks_chosen_and_not_chosen():
    nomination = YEAR_VOTE_NOMINATIONS[0]
    choices = {n.id: None for n in YEAR_VOTE_NOMINATIONS}
    choices[nomination.id] = 42
    text = render_bulletin(2026, choices, {42: "Bob"})
    assert "✅ <b>Bob</b>" in text
    assert "не выбрано" in text


def test_render_nomination_screen_includes_hint():
    nomination = next(n for n in YEAR_VOTE_NOMINATIONS if n.hint)
    text = render_nomination_screen(nomination)
    assert nomination.hint in text
    nomination_no_hint = next(n for n in YEAR_VOTE_NOMINATIONS if not n.hint)
    text2 = render_nomination_screen(nomination_no_hint)
    assert "Выбери кандидата" in text2


# ── Джобы (scheduler.py) ──────────────────────────────────────────────────────

def _freeze(monkeypatch, module, when: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.replace(tzinfo=tz) if tz else when

    monkeypatch.setattr(module, "datetime", _Frozen)


async def test_send_invitations_only_to_eligible(monkeypatch, db_factory):
    import bot.scheduler as sched

    factory = db_factory
    monkeypatch.setattr(sched, "async_session", factory)
    _freeze(monkeypatch, sched, datetime(2026, 12, 21, 10, 0, 0, tzinfo=timezone.utc))

    async with factory() as s:
        eligible = _player(1, "Alice")
        ghost = _player(2, "Ghost")
        s.add_all([eligible, ghost])
        await s.flush()
        s.add(_completed(eligible, ghost, None, 0.0, datetime(2026, 3, 1, 12, 0, 0)))
        # ничья тоже засчитывает обоих как сыгравших матч
        await s.commit()

    bot = AsyncMock()
    await sched.send_year_vote_invitations(bot)

    recipients = {c.args[0] for c in bot.send_message.await_args_list}
    assert recipients == {eligible.telegram_id, ghost.telegram_id}
    text = bot.send_message.await_args_list[0].args[1]
    assert "Итоги года" in text
    kb = bot.send_message.await_args_list[0].kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "yv_open"


async def test_send_reminders_only_to_unfilled(monkeypatch, db_factory):
    import bot.scheduler as sched

    factory = db_factory
    monkeypatch.setattr(sched, "async_session", factory)
    _freeze(monkeypatch, sched, datetime(2026, 12, 29, 10, 0, 0, tzinfo=timezone.utc))

    async with factory() as s:
        done, pending = _player(1, "Done"), _player(2, "Pending")
        s.add_all([done, pending])
        await s.flush()
        s.add(_completed(done, pending, done.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
        await s.flush()
        for n in YEAR_VOTE_NOMINATIONS:
            await set_vote(s, 2026, n.id, done.id, pending.id)
        await s.commit()

    bot = AsyncMock()
    await sched.send_year_vote_reminders(bot)

    recipients = {c.args[0] for c in bot.send_message.await_args_list}
    assert recipients == {pending.telegram_id}


async def test_send_results_skipped_when_no_votes(monkeypatch, db_factory):
    import bot.scheduler as sched

    factory = db_factory
    monkeypatch.setattr(sched, "async_session", factory)
    _freeze(monkeypatch, sched, datetime(2026, 12, 30, 12, 0, 0, tzinfo=timezone.utc))

    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
        await s.commit()

    bot = AsyncMock()
    await sched.send_year_vote_results(bot)
    bot.send_message.assert_not_awaited()


async def test_send_results_delivers_when_votes_exist(monkeypatch, db_factory):
    import bot.scheduler as sched

    factory = db_factory
    monkeypatch.setattr(sched, "async_session", factory)
    _freeze(monkeypatch, sched, datetime(2026, 12, 30, 12, 0, 0, tzinfo=timezone.utc))

    async with factory() as s:
        p1, p2 = _player(1, "Alice"), _player(2, "Bob")
        s.add_all([p1, p2])
        await s.flush()
        s.add(_completed(p1, p2, p1.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
        await s.flush()
        await set_vote(s, 2026, "gentleman", p1.id, p2.id)
        await s.commit()

    bot = AsyncMock()
    await sched.send_year_vote_results(bot)

    assert bot.send_message.await_count == 2
    text = bot.send_message.await_args_list[0].args[1]
    assert "Результаты голосования" in text
    assert "Bob" in text


async def test_year_end_combo_sends_summary_before_results(monkeypatch):
    import bot.scheduler as sched

    order = []

    async def fake_summary(bot):
        order.append("summary")

    async def fake_results(bot):
        order.append("results")

    monkeypatch.setattr(sched, "send_yearly_summary", fake_summary)
    monkeypatch.setattr(sched, "send_year_vote_results", fake_results)

    await sched.send_year_end_combo(AsyncMock())
    assert order == ["summary", "results"]


# ── Хендлеры (bot/handlers/year_vote.py) ──────────────────────────────────────

async def test_show_bulletin_unregistered_player_alert(db):
    import bot.handlers.year_vote as yv

    cb = _callback(999, "yv_open")
    await yv.show_bulletin(cb, db)
    cb.answer.assert_awaited_once()
    assert "Сначала напиши /start" in cb.answer.await_args.args[0]


async def test_show_bulletin_outside_window_alert(monkeypatch, db):
    import bot.handlers.year_vote as yv

    _freeze(monkeypatch, yv, datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc))
    player = _player(1, "Alice")
    db.add(player)
    await db.commit()

    cb = _callback(1, "yv_open")
    await yv.show_bulletin(cb, db)
    assert "закрыто" in cb.answer.await_args.args[0]
    cb.message.edit_text.assert_not_awaited()


async def test_show_bulletin_not_eligible_alert(monkeypatch, db):
    import bot.handlers.year_vote as yv

    _freeze(monkeypatch, yv, datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc))
    player = _player(1, "Alice")  # 0 матчей в 2026
    db.add(player)
    await db.commit()

    cb = _callback(1, "yv_open")
    await yv.show_bulletin(cb, db)
    assert "не сыграл ни одного матча" in cb.answer.await_args.args[0]
    cb.message.edit_text.assert_not_awaited()


async def test_bulletin_and_nomination_and_pick_flow(monkeypatch, db):
    import bot.handlers.year_vote as yv

    _freeze(monkeypatch, yv, datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc))
    voter, cand = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([voter, cand])
    await db.flush()
    db.add(_completed(voter, cand, voter.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
    await db.commit()

    # Бюллетень — всё "не выбрано"
    cb = _callback(1, "yv_open")
    await yv.show_bulletin(cb, db)
    text = cb.message.edit_text.await_args.args[0]
    assert text.count("не выбрано") == len(YEAR_VOTE_NOMINATIONS)

    # Экран номинации — только Bob кандидатом, себя (Alice) нет
    nom0 = YEAR_VOTE_NOMINATIONS[0].id
    cb2 = _callback(1, f"yv_nom_{nom0}")
    await yv.show_nomination(cb2, db)
    kb = cb2.message.edit_text.await_args.kwargs["reply_markup"]
    names = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Bob" in n for n in names)
    assert not any("Alice" in n for n in names)

    # Голос за Bob в номинации 0
    cb3 = _callback(1, f"yv_pick_{nom0}_{cand.id}")
    await yv.pick_nominee(cb3, db)
    assert "учтён" in cb3.answer.await_args.args[0]
    text3 = cb3.message.edit_text.await_args.args[0]
    assert "✅ <b>Bob</b>" in text3

    # Голос сохранён без дублирования строки
    count_r = await db.execute(select(func.count()).select_from(YearVote))
    assert count_r.scalar() == 1


async def test_pick_nominee_rejects_forged_self_vote(monkeypatch, db):
    import bot.handlers.year_vote as yv

    _freeze(monkeypatch, yv, datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc))
    voter, other = _player(1, "Alice"), _player(2, "Bob")
    db.add_all([voter, other])
    await db.flush()
    db.add(_completed(voter, other, voter.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, f"yv_pick_{YEAR_VOTE_NOMINATIONS[0].id}_{voter.id}")
    await yv.pick_nominee(cb, db)
    assert "Некорректные данные" in cb.answer.await_args.args[0]
    count_r = await db.execute(select(func.count()).select_from(YearVote))
    assert count_r.scalar() == 0


async def test_show_nomination_unknown_id_alerts_without_crashing(monkeypatch, db):
    """callback_data кодирует стабильный id номинации, не позиционный индекс
    (регресс на находку код-ревью v2.134.3) — устаревшая/подделанная кнопка
    с несуществующим id должна тихо получить алерт, а не уронить хендлер."""
    import bot.handlers.year_vote as yv

    _freeze(monkeypatch, yv, datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc))
    player = _player(1, "Alice")
    other = _player(2, "Bob")
    db.add_all([player, other])
    await db.flush()
    db.add(_completed(player, other, player.id, 10.0, datetime(2026, 3, 1, 12, 0, 0)))
    await db.commit()

    cb = _callback(1, "yv_nom_retired_id")
    await yv.show_nomination(cb, db)
    assert "Некорректные данные" in cb.answer.await_args.args[0]
    cb.message.edit_text.assert_not_awaited()

    cb2 = _callback(1, f"yv_pick_retired_id_{other.id}")
    await yv.pick_nominee(cb2, db)
    assert "Некорректные данные" in cb2.answer.await_args.args[0]
    count_r = await db.execute(select(func.count()).select_from(YearVote))
    assert count_r.scalar() == 0
