"""
Данные страниц Mini App (v2.160.0) — только чтение. Расчёты не дублируются:
таблица берётся из `compute_leaderboard` (та же, что на экране бота), звания —
`rank_title`. Здесь только сборка JSON и подписи, чтобы все русские строки
приложения были в одном месте и проверялись тестами, а не жили в JS.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.leaderboard import LeaderboardRow, compute_leaderboard
from bot.utils import _ru_plural, pluralize_wins, rank_title


def _subtitle(row: LeaderboardRow) -> str:
    """«Чемпион · Тим лид», «Миддл · серия 5 побед» — вторая строка под именем."""
    parts = []
    if row.is_champion:
        parts.append("Чемпион")
    elif row.is_challenger:
        parts.append("Претендент")
    elif row.is_mvp:
        parts.append("MVP месяца")
    parts.append(rank_title(row.rating))
    if row.streak >= 3:
        parts.append(f"серия {pluralize_wins(row.streak)}")
    elif row.inactive:
        parts.append("не играл 7+ дней")
    return " · ".join(parts)


def _week_label(change: int) -> str:
    """«+1 место», «−2 места», пусто без изменений."""
    if change == 0:
        return ""
    sign = "+" if change > 0 else "−"
    return sign + _ru_plural(abs(change), "место", "места", "мест")


def _gap_line(rows: list[LeaderboardRow], viewer_index: int | None) -> str:
    """«До #1 Боб: 13.2 очка.» или у лидера «Отрыв от #2 Вика: 13.2 очка.»
    Не показывается, если разрыв не положителен: выше стоит закреплённый
    чемпион с рейтингом ниже (место #1 не занимается по очкам)."""
    if viewer_index is None:
        return ""
    me = rows[viewer_index]
    if viewer_index > 0:
        above = rows[viewer_index - 1]
        gap = round(above.rating - me.rating, 1)
        return f"До #{above.rank} {above.name}: {gap:.1f} очка." if gap > 0 else ""
    if len(rows) > 1:
        lead = round(me.rating - rows[1].rating, 1)
        return f"Отрыв от #{rows[1].rank} {rows[1].name}: {lead:.1f} очка." if lead > 0 else ""
    return ""


async def leaderboard_payload(session: AsyncSession, viewer_player_id: int) -> dict:
    rows = await compute_leaderboard(session)
    viewer_index = next((i for i, r in enumerate(rows) if r.player_id == viewer_player_id), None)
    return {
        "title": "Рейтинг клуба",
        "players_label": _ru_plural(len(rows), "игрок", "игрока", "игроков"),
        "rows": [
            {
                "rank": r.rank,
                "name": r.name,
                "initial": (r.name.strip()[:1] or "?").upper(),
                "is_viewer": r.player_id == viewer_player_id,
                "subtitle": _subtitle(r),
                "rating": f"{r.rating:.1f}",
                "week_change": r.week_change,
                "week_label": _week_label(r.week_change),
            }
            for r in rows
        ],
        "gap": _gap_line(rows, viewer_index),
        "empty": "Пока нет сыгранных матчей." if not rows else "",
    }
