"""
Тесты логики «Матч дня» и форматирования счёта.
Запуск: pytest tests/test_match_of_day.py
"""
from datetime import datetime
from types import SimpleNamespace

from bot.utils import (
    BLOWOUT_PHRASES,
    CLOSE_DECIDER_FRAGMENTS,
    COMEBACK_OPENERS,
    DEUCE_FRAGMENTS,
    DRAMA_THRESHOLD,
    MARATHON_FRAGMENTS,
    MID_DEUCE_FRAGMENTS,
    PLAIN_WIN_PHRASES,
    SOFT_COMEBACK_OPENERS,
    UPSET_FRAGMENT_TEMPLATES,
    _stable_pool_index,
    match_drama_reason,
    match_drama_score,
    match_report,
    match_score_challenger_first,
    pick_match_of_day,
)


def make_match(sets, winner_id=1, challenger_id=1, challenged_id=2,
               rating_change=10.0, completed_at=None, match_id=1):
    """Лёгкая заглушка матча (атрибуты, которых хватает функциям драмы)."""
    return SimpleNamespace(
        id=match_id,
        sets_data=sets,
        winner_id=winner_id,
        challenger_id=challenger_id,
        challenged_id=challenged_id,
        rating_change=rating_change,
        completed_at=completed_at or datetime(2026, 5, 29, 12, 0, 0),
    )


# ── match_drama_score ─────────────────────────────────────────────────────────

def test_empty_sets_zero_drama():
    assert match_drama_score(make_match([])) == 0.0


def test_single_set_blowout_is_low():
    """Сухой 1-партийный разгром — минимальная драма, ниже порога."""
    m = make_match([{"w": 11, "l": 2}], rating_change=10.0)
    assert match_drama_score(m) < DRAMA_THRESHOLD


def test_five_set_thriller_is_high():
    """5 партий с дьюсами и концовкой впритык — высокая драма."""
    sets = [
        {"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 11, "l": 8},
        {"w": 7, "l": 11}, {"w": 13, "l": 11},
    ]
    m = make_match(sets, winner_id=1)
    assert match_drama_score(m) >= DRAMA_THRESHOLD


def test_deuce_adds_drama():
    """Партия за 11 (дьюс) добавляет балл."""
    base = make_match([{"w": 11, "l": 5}, {"w": 11, "l": 5}, {"w": 11, "l": 5}])
    deuce = make_match([{"w": 11, "l": 5}, {"w": 11, "l": 5}, {"w": 13, "l": 11}])
    assert match_drama_score(deuce) > match_drama_score(base)


def test_comeback_adds_drama():
    """Победитель проиграл стартовую партию (камбэк) — больше драмы."""
    no_cb = make_match([{"w": 11, "l": 6}, {"w": 11, "l": 6}, {"w": 6, "l": 11}], winner_id=1)
    cb = make_match([{"w": 6, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}], winner_id=1)
    assert match_drama_score(cb) > match_drama_score(no_cb)


def test_draw_has_no_comeback_bonus():
    """У ничьей (winner_id=None) не начисляется камбэк-бонус, не падает."""
    m = make_match([{"w": 6, "l": 11}, {"w": 11, "l": 6}], winner_id=None)
    assert match_drama_score(m) >= 0


# ── pick_match_of_day ─────────────────────────────────────────────────────────

def test_pick_none_when_all_trivial():
    """Если все матчи — сухие разгромы, матча дня нет."""
    matches = [
        make_match([{"w": 11, "l": 2}], rating_change=5.0),
        make_match([{"w": 11, "l": 3}, {"w": 11, "l": 1}], rating_change=5.0),
    ]
    assert pick_match_of_day(matches) is None


def test_pick_highest_drama():
    """Выбирается самый драматичный матч."""
    boring = make_match([{"w": 11, "l": 2}], rating_change=5.0)
    thriller = make_match(
        [{"w": 11, "l": 9}, {"w": 9, "l": 11}, {"w": 13, "l": 11}],
        winner_id=1, rating_change=20.0,
    )
    chosen = pick_match_of_day([boring, thriller])
    assert chosen is thriller


def test_pick_empty_list():
    assert pick_match_of_day([]) is None


# ── match_drama_reason ────────────────────────────────────────────────────────

def test_reason_marathon():
    sets = [{"w": 11, "l": 9}] * 5
    assert "марафон" in match_drama_reason(make_match(sets, winner_id=1)).lower()


def test_reason_comeback():
    sets = [{"w": 6, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 8}]
    assert "камбэк" in match_drama_reason(make_match(sets, winner_id=1)).lower()


def test_reason_draw():
    sets = [{"w": 11, "l": 6}, {"w": 6, "l": 11}]
    assert "ничья" in match_drama_reason(make_match(sets, winner_id=None)).lower()


def test_reason_capitalized():
    """Подпись начинается с заглавной буквы."""
    r = match_drama_reason(make_match([{"w": 11, "l": 9}] * 5, winner_id=1))
    assert r[0].isupper()


# ── match_score_challenger_first ──────────────────────────────────────────────

def test_score_winner_is_challenger():
    """Победил challenger — счёт без инверсии."""
    m = make_match([{"w": 11, "l": 7}, {"w": 11, "l": 5}], winner_id=1, challenger_id=1)
    assert match_score_challenger_first(m) == "11:7, 11:5"


def test_score_winner_is_challenged_inverts():
    """Победил challenged — счёт инвертируется в перспективу challenger."""
    # winner_id=2 (challenged), sets хранятся в перспективе победителя
    m = make_match([{"w": 11, "l": 7}, {"w": 11, "l": 5}],
                   winner_id=2, challenger_id=1, challenged_id=2)
    assert match_score_challenger_first(m) == "7:11, 5:11"


def test_score_draw_challenger_perspective():
    """Ничья — счёт уже в перспективе challenger, без инверсии."""
    m = make_match([{"w": 11, "l": 9}, {"w": 9, "l": 11}], winner_id=None, challenger_id=1)
    assert match_score_challenger_first(m) == "11:9, 9:11"


def test_score_empty():
    assert match_score_challenger_first(make_match([])) == ""


# ── match_report ───────────────────────────────────────────────────────────────

def test_report_comeback_only():
    """Проиграл первые ДВЕ партии (0:2), а не только первую — camebac-фрагмент
    в репортаже строже, чем «камбэк» в match_drama_reason. 4 партии — без марафона (нужно 5).

    Открывающая фраза — из COMEBACK_OPENERS (v2.118.0, было 1 фиксированная),
    индекс через _stable_pool_index — ожидаемое значение считаем из того же
    пула, а не хардкодим литерал, чтобы тест не ломался при правке формулировок."""
    sets = [{"w": 6, "l": 11}, {"w": 8, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}]
    m = make_match(sets, winner_id=1, rating_change=5.0)
    text = match_report(m, "Игрок")
    idx = _stable_pool_index(m.id, "comeback", len(COMEBACK_OPENERS))
    expected = COMEBACK_OPENERS[idx].format(name="Игрок").strip()
    assert text == expected
    assert "0:2" in text
    assert "дьюс" not in text
    assert "апсет" not in text


def test_report_comeback_opener_varies_by_match_id():
    """Разные match_id дают разные открывающие фразы — иначе пул был бы
    бесполезен (всегда один и тот же индекс). Индекс теперь через хэш
    (_stable_pool_index, v2.119.0), поэтому точное совпадение «N подряд id
    → все N вариантов пула» больше не гарантировано (в отличие от простого
    m.id % len) — проверяем достаточную вариативность на широкой выборке,
    не строгое покрытие ровно len(COMEBACK_OPENERS) подряд идущих id."""
    sets = [{"w": 6, "l": 11}, {"w": 8, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}]
    openers = {
        match_report(make_match(sets, winner_id=1, rating_change=5.0, match_id=mid), "Игрок")
        for mid in range(40)
    }
    assert len(openers) >= len(COMEBACK_OPENERS) - 1


def test_report_only_first_set_lost_is_not_comeback():
    """Проиграл только первую партию (не вторую) — НЕ считается комбэком для
    репортажа (в отличие от match_drama_reason, где хватает одной)."""
    sets = [{"w": 6, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}]
    m = make_match(sets, winner_id=1, rating_change=5.0)
    text = match_report(m, "Игрок")
    assert "влетел в яму" not in text
    # строгие 4 фактора не сработали — «мягкий» камбэк (v2.131.0), не сухая строка
    opener = SOFT_COMEBACK_OPENERS[_stable_pool_index(m.id, "soft_comeback", len(SOFT_COMEBACK_OPENERS))]
    close = CLOSE_DECIDER_FRAGMENTS[_stable_pool_index(m.id, "close", len(CLOSE_DECIDER_FRAGMENTS))]
    assert text == (opener.format(name="Игрок") + close[0].upper() + close[1:] + ".").strip()
    assert text != match_drama_reason(m)


def test_report_marathon_only():
    """Фрагмент — из MARATHON_FRAGMENTS (v2.118.0, было 1 фиксированный),
    индекс через _stable_pool_index (v2.119.0 — хэш вместо линейного
    смещения, см. докстринг _stable_pool_index про декорреляцию пулов)."""
    sets = [{"w": 11, "l": 9}] * 5
    m = make_match(sets, winner_id=1, rating_change=5.0)
    text = match_report(m, "Игрок")
    idx = _stable_pool_index(m.id, "marathon", len(MARATHON_FRAGMENTS))
    fragment = MARATHON_FRAGMENTS[idx]
    expected = fragment[0].upper() + fragment[1:] + "."
    assert text == expected
    assert "влетел в яму" not in text and "провалил старт" not in text


def test_report_deuce_decider_only():
    """Фрагмент — из DEUCE_FRAGMENTS (v2.118.0, было 1 фиксированный),
    индекс через _stable_pool_index. Капитализация первой буквы — тот же
    живой компромисс, что и раньше (фрагменты начинаются со строчной
    «а»/«решающая»)."""
    sets = [{"w": 11, "l": 5}, {"w": 11, "l": 5}, {"w": 13, "l": 11}]
    m = make_match(sets, winner_id=1, rating_change=5.0)
    text = match_report(m, "Игрок")
    idx = _stable_pool_index(m.id, "deuce", len(DEUCE_FRAGMENTS))
    fragment = DEUCE_FRAGMENTS[idx]
    expected = fragment[0].upper() + fragment[1:] + "."
    assert text == expected


def test_report_upset_only():
    """Шаблон — из UPSET_FRAGMENT_TEMPLATES (v2.118.0, было 1 фиксированный),
    индекс через _stable_pool_index, число очков подставляется как раньше."""
    sets = [{"w": 11, "l": 5}, {"w": 11, "l": 5}]
    m = make_match(sets, winner_id=1, rating_change=25.0)
    text = match_report(m, "Игрок")
    idx = _stable_pool_index(m.id, "upset", len(UPSET_FRAGMENT_TEMPLATES))
    template = UPSET_FRAGMENT_TEMPLATES[idx]
    fragment = template.format(delta=25.0)
    expected = fragment[0].upper() + fragment[1:] + "."
    assert text == expected


def test_report_all_factors_combined():
    """Все 4 фактора — открывающая фраза + 3 хвостовых фрагмента через
    запятую, порядок марафон→дьюс→апсет не поменялся при переходе на пулы."""
    sets = [{"w": 6, "l": 11}, {"w": 8, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}, {"w": 13, "l": 11}]
    m = make_match(sets, winner_id=1, rating_change=20.0)
    text = match_report(m, "Игрок")

    opener_idx = _stable_pool_index(m.id, "comeback", len(COMEBACK_OPENERS))
    opener = COMEBACK_OPENERS[opener_idx].format(name="Игрок")
    tail_fragments = [
        MARATHON_FRAGMENTS[_stable_pool_index(m.id, "marathon", len(MARATHON_FRAGMENTS))],
        DEUCE_FRAGMENTS[_stable_pool_index(m.id, "deuce", len(DEUCE_FRAGMENTS))],
        UPSET_FRAGMENT_TEMPLATES[_stable_pool_index(m.id, "upset", len(UPSET_FRAGMENT_TEMPLATES))].format(delta=20.0),
    ]
    joined = ", ".join(tail_fragments)
    tail = joined[0].upper() + joined[1:] + "."
    expected = (opener + tail).strip()

    assert text == expected


def test_report_dramatic_pools_decorrelated_across_factors():
    """Смещения (+0/+1/+2/+3) на m.id для разных пулов не должны совпадать
    настолько, чтобы полный текст всегда был одним и тем же набором фраз —
    иначе весь смысл раздельных пулов на 4 фактора теряется."""
    sets = [{"w": 6, "l": 11}, {"w": 8, "l": 11}, {"w": 11, "l": 6}, {"w": 11, "l": 6}, {"w": 13, "l": 11}]
    texts = {
        match_report(make_match(sets, winner_id=1, rating_change=20.0, match_id=mid), "Игрок")
        for mid in range(20)
    }
    assert len(texts) > 10


def test_report_blowout_uses_phrase_pool():
    """Уверенный разгром 3-0 (0 партий отдано) — ни один из 4 факторов драмы
    не сработал, match_drama_reason падает на плоское «Уверенный разгром» —
    репортаж (v2.105.0) заменяет её фразой из BLOWOUT_PHRASES, а не отдаёт
    плоскую строку как раньше."""
    sets = [{"w": 11, "l": 3}, {"w": 11, "l": 4}, {"w": 11, "l": 2}]
    m = make_match(sets, winner_id=1, rating_change=5.0, match_id=3)
    assert match_drama_reason(m) == "Уверенный разгром"
    result = match_report(m, "Игрок")
    assert result in BLOWOUT_PHRASES
    assert result == BLOWOUT_PHRASES[3 % len(BLOWOUT_PHRASES)]


def test_report_plain_win_uses_phrase_pool():
    """Победа без разгрома (хотя бы 1 партия отдана) и без драм-факторов —
    match_drama_reason падает на «Напряжённый матч», репортаж берёт фразу
    из PLAIN_WIN_PHRASES."""
    sets = [{"w": 11, "l": 9}, {"w": 11, "l": 6}, {"w": 6, "l": 11}, {"w": 11, "l": 7}]
    m = make_match(sets, winner_id=1, rating_change=5.0, match_id=5)
    assert match_drama_reason(m) == "Напряжённый матч"
    result = match_report(m, "Игрок")
    assert result in PLAIN_WIN_PHRASES
    assert result == PLAIN_WIN_PHRASES[5 % len(PLAIN_WIN_PHRASES)]


def test_report_stable_index_by_match_id():
    """Одинаковый match_id всегда даёт одну и ту же фразу — не «прыгает»
    при повторном рендере той же карточки/дайджеста (тот же принцип, что у
    match_phrase)."""
    sets = [{"w": 11, "l": 3}, {"w": 11, "l": 4}, {"w": 11, "l": 2}]
    m = make_match(sets, winner_id=1, rating_change=5.0, match_id=7)
    assert match_report(m, "Игрок") == match_report(m, "Игрок")


def test_report_mid_deuce_uses_phrase_pool():
    """Дьюс в НЕ решающей партии (v2.131.0) — раньше репортаж отдавал сухое
    «Дьюс на тоненького», теперь фразу из MID_DEUCE_FRAGMENTS."""
    sets = [{"w": 12, "l": 10}, {"w": 11, "l": 3}]
    m = make_match(sets, winner_id=1, rating_change=5.0, match_id=1)
    idx = _stable_pool_index(m.id, "mid_deuce", len(MID_DEUCE_FRAGMENTS))
    fragment = MID_DEUCE_FRAGMENTS[idx]
    assert match_report(m, "Игрок") == fragment[0].upper() + fragment[1:] + "."


def test_report_close_decider_uses_phrase_pool():
    """2:1 впритык без дьюса и проигранного старта (v2.131.0) — раньше сухое
    «Решилось в последней партии», теперь фраза из CLOSE_DECIDER_FRAGMENTS."""
    sets = [{"w": 11, "l": 9}, {"w": 8, "l": 11}, {"w": 11, "l": 9}]
    m = make_match(sets, winner_id=1, rating_change=5.0, match_id=2)
    assert match_drama_reason(m) == "Решилось в последней партии"
    idx = _stable_pool_index(m.id, "close", len(CLOSE_DECIDER_FRAGMENTS))
    fragment = CLOSE_DECIDER_FRAGMENTS[idx]
    assert match_report(m, "Игрок") == fragment[0].upper() + fragment[1:] + "."


def test_report_soft_factors_combine():
    """Проигранная первая + дьюс в середине + 2:1 — открывающая фраза и оба
    хвостовых фрагмента, порядок: близкая развязка, затем дьюс."""
    sets = [{"w": 9, "l": 11}, {"w": 12, "l": 10}, {"w": 11, "l": 7}]
    m = make_match(sets, winner_id=1, rating_change=5.0, match_id=4)
    text = match_report(m, "Игрок")
    opener = SOFT_COMEBACK_OPENERS[_stable_pool_index(m.id, "soft_comeback", len(SOFT_COMEBACK_OPENERS))]
    close = CLOSE_DECIDER_FRAGMENTS[_stable_pool_index(m.id, "close", len(CLOSE_DECIDER_FRAGMENTS))]
    deuce = MID_DEUCE_FRAGMENTS[_stable_pool_index(m.id, "mid_deuce", len(MID_DEUCE_FRAGMENTS))]
    joined = f"{close}, {deuce}"
    assert text == (opener.format(name="Игрок") + joined[0].upper() + joined[1:] + ".").strip()


def test_report_soft_comeback_escapes_winner_name():
    sets = [{"w": 9, "l": 11}, {"w": 11, "l": 7}, {"w": 11, "l": 6}]
    text = match_report(make_match(sets, winner_id=1, rating_change=5.0), "<b>X</b>")
    assert "<b>X</b>" not in text and "&lt;b&gt;" in text


def test_report_typical_top_matches_never_dry():
    """Регрессия на жалобу «опять скучные репорты»: любой матч из 3 партий,
    способный пройти порог «топ-матча», не должен давать сухую строку из
    match_drama_reason."""
    dry = {"Дьюс на тоненького", "Решилось в последней партии", "Дьюсы"}
    variants = [
        [{"w": 12, "l": 10}, {"w": 9, "l": 11}, {"w": 11, "l": 5}],
        [{"w": 11, "l": 9}, {"w": 8, "l": 11}, {"w": 11, "l": 9}],
        [{"w": 8, "l": 11}, {"w": 11, "l": 7}, {"w": 11, "l": 6}],
        [{"w": 12, "l": 10}, {"w": 13, "l": 11}],
    ]
    for sets in variants:
        for mid in range(10):
            m = make_match(sets, winner_id=1, rating_change=5.0, match_id=mid)
            text = match_report(m, "Игрок")
            assert text not in dry
            assert not text.startswith("Камбэк после")


def test_report_draw_falls_back_to_drama_reason():
    sets = [{"w": 11, "l": 9}, {"w": 9, "l": 11}]
    m = make_match(sets, winner_id=None, rating_change=5.0)
    assert match_report(m, "") == match_drama_reason(m)


def test_report_empty_sets_falls_back():
    m = make_match([], winner_id=1)
    assert match_report(m, "Игрок") == match_drama_reason(m)
