from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb(active_matches: list | None = None) -> InlineKeyboardMarkup:
    """Главное меню. Если переданы активные матчи [(match_id, opponent_name), ...],
    сверху добавляются заметные кнопки «Внести результат» по каждому из них."""
    b = InlineKeyboardBuilder()
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
    b.row(InlineKeyboardButton(text="📈 История рейтинга", callback_data="rating_history"))
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


def rematch_kb(opponent_id: int) -> InlineKeyboardMarkup:
    """Клавиатура после матча — предлагает реванш."""
    b = InlineKeyboardBuilder()
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
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in players:
        if p.telegram_id != exclude_telegram_id:
            rank_str = f"#{rank_map[p.id]}  " if rank_map and p.id in rank_map else ""
            if my_rating is not None:
                diff = p.rating - my_rating
                icon = "💪 " if diff > 35 else ("😊 " if diff < -35 else "⚡ ")
            else:
                icon = ""
            # ❄️ приоритетнее 🔥 — неактивный игрок важнее стрика
            if inactive_ids and p.id in inactive_ids:
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
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📋 Внести результат", callback_data=f"report_{match_id}"))
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
        text="🏅 Достижения",
        callback_data=f"player_achievements_{player_id}",
    ))
    b.row(InlineKeyboardButton(text="« К рейтингу", callback_data="menu_leaderboard"))
    return b.as_markup()


def h2h_kb(player_id: int, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура под экраном личных встреч (с пагинацией)."""
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"h2h_{player_id}_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"h2h_{player_id}_{page + 1}"))
    if nav:
        b.row(*nav)
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


def rating_history_kb() -> InlineKeyboardMarkup:
    """Клавиатура под экраном истории рейтинга (без кнопки 'История рейтинга')."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📜 Вся история матчей", callback_data="history_0"))
    b.row(InlineKeyboardButton(text="« К статистике", callback_data="menu_stats"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def cancel_match_confirm_kb(match_id: int) -> InlineKeyboardMarkup:
    """Подтверждение отмены матча."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"cancel_yes_{match_id}"),
        InlineKeyboardButton(text="↩️ Нет", callback_data="menu_matches"),
    )
    return b.as_markup()


