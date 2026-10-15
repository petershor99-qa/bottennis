"""
Цвет показателей стиля (v2.180.0): ВИН, КЛЧ, ДОЖ, КМБ, ДОМ, СТБ на карточке
игрока и на вкладке «Стиль». Утверждено владельцем 2026-10-15 (вариант А).

У показателей разная «норма» (доминирование почти у всех 55–70, камбэки —
10–30), поэтому цвет — относительно клуба, а не по общим порогам:
- gray  — мало данных (те же пороги выборки, что у архетипов стиля);
- white — как в среднем по клубу (в «коридоре» от медианы);
- green — сильная сторона (выше медианы больше чем на коридор);
- gold  — лучший в клубе (первый с отрывом от второго хотя бы в 1 пункт);
- red   — слабое место (ниже медианы больше чем на коридор). У камбэков
  красного нет: мало камбэков — не слабость, игрок выигрывает с первой партии.
Сравнение — только среди игроков с достаточной выборкой; если таких меньше
трёх, сравнивать не с кем — белый.

Коридоры подобраны заранее и правятся одной строкой (CORRIDOR) после того,
как владелец посмотрит на реальные цифры клуба.
"""
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player
from bot.services.stats import _AXIS_SAMPLE_GATES, _MATCHES_FOR_STABILITY, _build_style_radar
from bot.utils import _ru_plural, get_career_matches

CORRIDOR: dict[str, float] = {
    "Винрейт": 8, "Клатч": 10, "Дожимание": 8, "Камбэки": 6, "Доминирование": 3, "Стабильность": 8,
}
NO_RED = {"Камбэки"}
MIN_MATCHES = 10            # винрейт и стабильность — от 10 матчей
MIN_PLAYERS_TO_COMPARE = 3
MIN_MATCHES_FOR_RADAR = 5   # как у радара (bot/webapp/api.py)

# Чего не хватает для оценки: (ключ статистики, нужно, формы слова)
_SAMPLE_WORDS: dict[str, tuple[str, int, tuple[str, str, str]]] = {
    "Винрейт": ("matches", MIN_MATCHES, ("матча", "матчей", "матчей")),
    "Стабильность": ("matches", _MATCHES_FOR_STABILITY, ("матча", "матчей", "матчей")),
    "Клатч": (_AXIS_SAMPLE_GATES["Клатч"][0], _AXIS_SAMPLE_GATES["Клатч"][1], ("дьюса", "дьюсов", "дьюсов")),
    "Дожимание": (_AXIS_SAMPLE_GATES["Дожимание"][0], _AXIS_SAMPLE_GATES["Дожимание"][1],
                  ("выигранной первой партии", "выигранных первых партий", "выигранных первых партий")),
    "Доминирование": (_AXIS_SAMPLE_GATES["Доминирование"][0], _AXIS_SAMPLE_GATES["Доминирование"][1],
                      ("победы", "побед", "побед")),
    "Камбэки": (_AXIS_SAMPLE_GATES["Камбэки"][0], _AXIS_SAMPLE_GATES["Камбэки"][1], ("победы", "побед", "побед")),
}


def _sample(axis: str, s: dict) -> tuple[int, int]:
    key, need, _ = _SAMPLE_WORDS[axis]
    have = s["wins"] + s["draws"] + s["losses"] if key == "matches" else int(s.get(key) or 0)
    return have, need


def has_sample(axis: str, s: dict) -> bool:
    have, need = _sample(axis, s)
    return have >= need


def _sample_hint(axis: str, s: dict) -> str:
    have, need = _sample(axis, s)
    one, few, many = _SAMPLE_WORDS[axis][2]
    # «из 5 дьюсов», «из 10 матчей» — форма по «нужно»
    word = _ru_plural(need, one, few, many).split(" ", 1)[1]
    return f"Мало данных: {have} из {need} {word}"


_reference: dict[tuple, dict[str, list[tuple[int, float]]]] = {}


async def club_reference(session: AsyncSession) -> dict[str, list[tuple[int, float]]]:
    """Значения каждой оси у игроков с достаточной выборкой: {ось: [(id, значение)]}.
    Кэш — пока не появился новый матч (ключ: число и последний id завершённых)."""
    count, last = (await session.execute(
        select(func.count(Match.id), func.max(Match.id)).where(Match.status == MatchStatus.completed)
    )).one()
    key = (count, last)
    if key in _reference:
        return _reference[key]
    from bot.webapp.api import stats_cached  # здесь, чтобы не было кругового импорта

    ref: dict[str, list[tuple[int, float]]] = {axis: [] for axis in CORRIDOR}
    for player in (await session.execute(select(Player))).scalars().all():
        matches = await get_career_matches(session, player.id, with_opponents=True)
        if len(matches) < MIN_MATCHES_FOR_RADAR:
            continue
        s = stats_cached(player, matches)
        radar = _build_style_radar(s)
        if not radar:
            continue
        for axis, value in radar.items():
            if axis in ref and has_sample(axis, s):
                ref[axis].append((player.id, float(value)))
    _reference.clear()
    _reference[key] = ref
    return ref


def tier(axis: str, value: float, s: dict, player_id: int, ref: dict[str, list[tuple[int, float]]]) -> tuple[str, str]:
    """(цвет, пояснение) показателя игрока."""
    if axis not in CORRIDOR:
        return "white", ""
    if not has_sample(axis, s):
        return "gray", _sample_hint(axis, s)
    values = ref.get(axis, [])
    if len(values) < MIN_PLAYERS_TO_COMPARE:
        return "white", "Пока не с кем сравнить"
    mid = median(v for _, v in values)
    others = sorted((v for pid, v in values if pid != player_id), reverse=True)
    if others and value - others[0] >= 1:
        return "gold", "Лучший в клубе"
    diff = value - mid
    if diff > CORRIDOR[axis]:
        return "green", f"Выше среднего по клубу на {round(diff)}"
    if diff < -CORRIDOR[axis] and axis not in NO_RED:
        return "red", f"Ниже среднего по клубу на {round(-diff)}"
    return "white", f"Как в среднем по клубу ({round(mid)})"
