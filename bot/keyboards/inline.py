from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils import favor_icon


def main_reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под строкой ввода — три самых частых действия
    всегда под рукой, не нужно подниматься к инлайн-кнопкам главного меню.

    Отдельный механизм от InlineKeyboardMarkup: нажатие шлёт текст кнопки как
    обычное сообщение (см. хендлеры-двойники в challenge.py/leaderboard.py/
    profile.py — F.message(F.text == "...")), поэтому у результата нет «своего»
    сообщения для редактирования, каждый тап шлёт новое сообщение в чат — как
    и любое другое уведомление бота (пасхалки, вызовы и т.д.), не хуже.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text="🏓 Вызвать на матч"),
            KeyboardButton(text="📊 Рейтинг"),
            KeyboardButton(text="📈 Статистика"),
        ]],
        resize_keyboard=True,
    )


def main_menu_kb(
    active_matches: list | None = None, share_match_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Главное меню. Если переданы активные матчи [(match_id, opponent_name), ...],
    сверху добавляются заметные кнопки «Внести результат» по каждому из них.

    share_match_id — если задан, сверху добавляется кнопка «Карточка победы».
    Нужно для уведомления победителю, когда счёт внёс проигравший — победитель
    тогда видит не интерактивный экран результата (тот у репортёра), а просто
    это уведомление, и кнопку карточки больше некуда прицепить."""
    b = InlineKeyboardBuilder()
    if share_match_id is not None:
        b.row(InlineKeyboardButton(
            text="📤 Карточка победы", callback_data=f"share_card_{share_match_id}",
        ))
    if active_matches:
        for match_id, opponent_name in active_matches:
            b.row(InlineKeyboardButton(
                text=f"📋 Внести результат — vs {opponent_name}",
                callback_data=f"report_{match_id}",
            ))
    b.row(InlineKeyboardButton(text="🏓 Вызвать на матч", callback_data="menu_play"))
    b.row(InlineKeyboardButton(text="📊 Рейтинг", callback_data="menu_leaderboard"))
    b.row(
        InlineKeyboardButton(text="📈 Статистика", callback_data="menu_stats"),
        InlineKeyboardButton(text="🎮 Мои матчи", callback_data="menu_matches"),
    )
    return b.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def stats_kb() -> InlineKeyboardMarkup:
    """Клавиатура под экраном статистики."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📜 Вся история матчей", callback_data="history_0"))
    b.row(InlineKeyboardButton(text="📊 График рейтинга", callback_data="rating_chart"))
    b.row(InlineKeyboardButton(text="🏅 Достижения", callback_data="my_achievements"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def achievements_kb() -> InlineKeyboardMarkup:
    """Клавиатура под экраном своих достижений."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« К статистике", callback_data="menu_stats"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def player_achievements_kb(player_id: int) -> InlineKeyboardMarkup:
    """Клавиатура под экраном достижений другого игрока."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« К профилю", callback_data=f"player_profile_{player_id}"))
    return b.as_markup()


def rematch_kb(
    opponent_id: int, can_rematch: bool = True, share_match_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после матча — предлагает реванш.

    can_rematch=False — сразу после боссфайта: реванш заблокирован, пока
    нечемпион пары не сыграет с третьим (см. boss_fight_rematch_blocked).
    share_match_id — если задан, сверху добавляется кнопка «Карточка победы»
    (только когда этот экран смотрит именно победитель — ничьи её не получают).
    """
    b = InlineKeyboardBuilder()
    if share_match_id is not None:
        b.row(InlineKeyboardButton(
            text="📤 Карточка победы", callback_data=f"share_card_{share_match_id}",
        ))
    if can_rematch:
        b.row(InlineKeyboardButton(text="⚔️ Реванш", callback_data=f"rematch_{opponent_id}"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def history_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Клавиатура для листания истории матчей."""
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"history_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"history_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def players_list_kb(
    players,
    exclude_telegram_id: int,
    my_rating: float | None = None,
    rank_map: dict[int, int] | None = None,
    streak_map: dict[int, int] | None = None,
    inactive_ids: set[int] | None = None,
    champion_id: int | None = None,
    challenger_id: int | None = None,
    boss_fight_target: tuple[int, str] | None = None,
) -> InlineKeyboardMarkup:
    """boss_fight_target — (champion_id, champion_name), передаётся только когда
    зритель сам является текущим претендентом: первой строкой добавляется
    ярлык прямого вызова на босс-файт. Чемпион также остаётся в обычном
    списке ниже — двойная точка входа, это осознанно (см. CLAUDE.md)."""
    b = InlineKeyboardBuilder()
    if boss_fight_target is not None:
        bf_id, bf_name = boss_fight_target
        b.row(InlineKeyboardButton(
            text=f"⚔️ БОСС-ФАЙТ — {bf_name}",
            callback_data=f"challenge_{bf_id}",
        ))
    for p in players:
        if p.telegram_id != exclude_telegram_id:
            rank_str = f"#{rank_map[p.id]}  " if rank_map and p.id in rank_map else ""
            icon = favor_icon(p.rating - my_rating) if my_rating is not None else ""
            # 👑/🗡 приоритетнее ❄️/🔥 (босс-файт важнее формы), ❄️ приоритетнее 🔥
            if champion_id is not None and p.id == champion_id:
                badge = " 👑"
            elif challenger_id is not None and p.id == challenger_id:
                badge = " 🗡"
            elif inactive_ids and p.id in inactive_ids:
                badge = " ❄️"
            elif streak_map and streak_map.get(p.id, 0) >= 3:
                badge = " 🔥"
            else:
                badge = ""
            b.row(InlineKeyboardButton(
                text=f"{rank_str}{icon}{p.display_name}{badge}  ({round(p.rating, 1)} pts)",
                callback_data=f"challenge_{p.id}",
            ))
    b.row(InlineKeyboardButton(text="« Назад", callback_data="back_to_menu"))
    return b.as_markup()



def active_match_kb(match_id: int) -> InlineKeyboardMarkup:
    """Клавиатура экрана начала матча. Результат вносится прямым вводом счёта
    в чат — отдельной кнопки для этого нет (см. подсказку в тексте сообщения)."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="❌ Отменить матч", callback_data=f"cancel_match_{match_id}"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def busy_with_match_kb(match_id: int) -> InlineKeyboardMarkup:
    """Клавиатура экрана «у тебя уже есть активный матч» (блокировка нового
    вызова). В отличие от active_match_kb здесь есть кнопка быстрого внесения
    результата — человек уже пытался сделать что-то ДРУГОЕ (вызвать нового
    соперника), поэтому подсказка «просто напиши счёт» тут неуместна, нужен
    явный путь вперёд."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📋 Внести результат сразу", callback_data=f"report_{match_id}"))
    b.row(InlineKeyboardButton(text="❌ Отменить матч", callback_data=f"cancel_match_{match_id}"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def after_set_kb(match_id: int, has_sets: bool) -> InlineKeyboardMarkup:
    """Клавиатура после ввода счёта партии."""
    b = InlineKeyboardBuilder()
    if has_sets:
        b.row(InlineKeyboardButton(text="🏁 Завершить матч", callback_data=f"finish_sets_{match_id}"))
        b.row(InlineKeyboardButton(text="↩️ Убрать последнюю партию", callback_data=f"undo_set_{match_id}"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="cancel_report"))
    return b.as_markup()


def leaderboard_kb(players) -> InlineKeyboardMarkup:
    """Клавиатура под таблицей рейтинга — кнопки профилей игроков."""
    b = InlineKeyboardBuilder()
    btns = [
        InlineKeyboardButton(
            text=f"#{i + 1} {p.display_name[:16]}",
            callback_data=f"player_profile_{p.id}",
        )
        for i, p in enumerate(players)
    ]
    for i in range(0, len(btns), 2):
        b.row(*btns[i:i + 2])
    b.row(InlineKeyboardButton(text="🏆 Рекорды клуба", callback_data="club_records"))
    b.row(InlineKeyboardButton(text="⚔️ Матрица доминирования", callback_data="dominance_matrix"))
    b.row(InlineKeyboardButton(text="🌡 Индекс формы", callback_data="form_index"))
    b.row(InlineKeyboardButton(text="🏛 Зал славы", callback_data="hall_of_fame"))
    b.row(InlineKeyboardButton(text="📅 Сегодня", callback_data="menu_today"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def back_to_leaderboard_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« К рейтингу", callback_data="menu_leaderboard"))
    return b.as_markup()


def player_profile_kb(
    player_id: int, viewer_id: int | None = None, can_challenge: bool = True
) -> InlineKeyboardMarkup:
    """Клавиатура под профилем другого игрока."""
    b = InlineKeyboardBuilder()
    if viewer_id is not None and viewer_id != player_id:
        if can_challenge:
            b.row(InlineKeyboardButton(text="⚔️ Вызвать", callback_data=f"challenge_{player_id}"))
        b.row(InlineKeyboardButton(text="🆚 Личные встречи", callback_data=f"h2h_{player_id}_0"))
    b.row(InlineKeyboardButton(
        text="📜 Вся история матчей",
        callback_data=f"player_history_{player_id}_0",
    ))
    b.row(InlineKeyboardButton(
        text="📊 График рейтинга",
        callback_data=f"player_chart_{player_id}",
    ))
    b.row(InlineKeyboardButton(
        text="🏅 Достижения",
        callback_data=f"player_achievements_{player_id}",
    ))
    b.row(InlineKeyboardButton(text="« К рейтингу", callback_data="menu_leaderboard"))
    return b.as_markup()


def h2h_kb(
    player_id: int, page: int = 0, total_pages: int = 1, can_challenge: bool = True
) -> InlineKeyboardMarkup:
    """Клавиатура под экраном личных встреч (с пагинацией).

    can_challenge=False — зритель или соперник уже заняты другим активным
    матчем, кнопку «Вызвать» скрываем, чтобы не вести к тупиковому нажатию.
    """
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"h2h_{player_id}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"h2h_{player_id}_{page + 1}"))
    if nav:
        b.row(*nav)
    if can_challenge:
        b.row(InlineKeyboardButton(text="⚔️ Вызвать", callback_data=f"challenge_{player_id}"))
    b.row(InlineKeyboardButton(text="« К профилю", callback_data=f"player_profile_{player_id}"))
    return b.as_markup()


def player_history_kb(player_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Клавиатура для листания истории матчей другого игрока."""
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="← Назад",
            callback_data=f"player_history_{player_id}_{page - 1}",
        ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            text="Вперёд →",
            callback_data=f"player_history_{player_id}_{page + 1}",
        ))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(
        text="« К профилю",
        callback_data=f"player_profile_{player_id}",
    ))
    return b.as_markup()


def cancel_match_confirm_kb(match_id: int) -> InlineKeyboardMarkup:
    """Подтверждение отмены матча."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"cancel_yes_{match_id}"),
        InlineKeyboardButton(text="↩️ Нет", callback_data="menu_matches"),
    )
    return b.as_markup()


