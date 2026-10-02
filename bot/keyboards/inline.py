from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.digests import DIGEST_KINDS
from bot.services.year_vote import YEAR_VOTE_NOMINATIONS
from bot.utils import (
    REPLY_KB_LEADERBOARD,
    REPLY_KB_PROFILE,
    favor_icon,
    random_challenge_button_label,
)


def main_reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под строкой ввода — три самых частых действия
    всегда под рукой, не нужно подниматься к инлайн-кнопкам главного меню.

    Отдельный механизм от InlineKeyboardMarkup: нажатие шлёт текст кнопки как
    обычное сообщение (см. хендлеры-двойники в challenge.py/leaderboard.py/
    profile.py — F.message(F.text == "...")), поэтому у результата нет «своего»
    сообщения для редактирования, каждый тап шлёт новое сообщение в чат — как
    и любое другое уведомление бота (пасхалки, вызовы и т.д.), не хуже.

    Подпись первой кнопки случайная из CHALLENGE_BUTTON_LABELS (v2.117.0) —
    хендлер в challenge.py матчит ПУЛ вариантов (F.text.in_(...)), не одну
    строку, иначе после смены подписи кнопка перестала бы что-либо делать.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text=random_challenge_button_label()),
            KeyboardButton(text=REPLY_KB_LEADERBOARD),
            KeyboardButton(text=REPLY_KB_PROFILE),
        ]],
        resize_keyboard=True,
    )


def main_menu_kb(
    share_match_id: int | None = None, rematch_opponent_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Главное меню. Кнопки «Внести результат» убраны в v2.148.0: счёт пишут
    прямо в чат (подсказка стоит на экране начала матча и в приветствии).

    share_match_id — если задан, сверху добавляется кнопка «Карточка победы».
    Нужно для уведомления победителю, когда счёт внёс проигравший — победитель
    тогда видит не интерактивный экран результата (тот у репортёра), а просто
    это уведомление, и кнопку карточки больше некуда прицепить.

    rematch_opponent_id — если задан, под карточкой добавляется «⚔️ Реванш»
    (v2.143.0): уведомление второго участника матча (того, кто счёт не вносил),
    у репортёра реванш уже есть на экране результата (rematch_kb)."""
    b = InlineKeyboardBuilder()
    if share_match_id is not None:
        b.row(InlineKeyboardButton(
            text="📤 Карточка победы", callback_data=f"share_card_{share_match_id}",
        ))
    if rematch_opponent_id is not None:
        b.row(InlineKeyboardButton(
            text="⚔️ Реванш", callback_data=f"rematch_{rematch_opponent_id}",
        ))
    b.row(InlineKeyboardButton(text=random_challenge_button_label(), callback_data="menu_play"))
    b.row(InlineKeyboardButton(text="🏆 Рейтинг клуба", callback_data="menu_leaderboard"))
    b.row(
        InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_stats"),
        InlineKeyboardButton(text="🎯 С кем сыграть?", callback_data="menu_matches"),
    )
    b.row(InlineKeyboardButton(text="❓ Справка", callback_data="menu_help"))
    return b.as_markup()


def notifications_kb(muted: set[str]) -> InlineKeyboardMarkup:
    """Экран «Рассылки» (v2.145.0): по кнопке на автосводку, по одной в ряд.
    ✅ — получает, 🔕 — отключена; тап переключает (notif_toggle_{ключ})."""
    b = InlineKeyboardBuilder()
    for key, title in DIGEST_KINDS:
        mark = "🔕" if key in muted else "✅"
        b.row(InlineKeyboardButton(text=f"{title} {mark}", callback_data=f"notif_toggle_{key}"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def help_toc_kb(sections: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Оглавление справки (v2.152.0): по кнопке на раздел, по одной в ряд
    (подписи длинные — в два столбца обрезались бы), «Настроить рассылки» и «В меню»."""
    b = InlineKeyboardBuilder()
    for key, title in sections:
        b.row(InlineKeyboardButton(text=title, callback_data=f"help_sec_{key}"))
    b.row(InlineKeyboardButton(text="🔔 Настроить рассылки", callback_data="menu_notifications"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def help_section_kb(key: str) -> InlineKeyboardMarkup:
    """Под разделом справки: назад к оглавлению и в меню; под «Когда приходят
    сводки» дополнительно вход в настройку рассылок."""
    b = InlineKeyboardBuilder()
    if key == "digests":
        b.row(InlineKeyboardButton(text="🔔 Настроить рассылки", callback_data="menu_notifications"))
    b.row(InlineKeyboardButton(text="« К справке", callback_data="menu_help"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def _section_rows(b: InlineKeyboardBuilder, sections: list[tuple[str, str]], prefix: str) -> None:
    """Кнопки разделов статистики по 2 в ряд (нечётная последняя — одна)."""
    for i in range(0, len(sections), 2):
        b.row(*[
            InlineKeyboardButton(text=title, callback_data=f"{prefix}{key}")
            for key, title in sections[i:i + 2]
        ])


def stats_section_kb() -> InlineKeyboardMarkup:
    """Под экраном раздела личной статистики — назад к основному экрану."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« В мой профиль", callback_data="menu_stats"))
    return b.as_markup()


def player_stats_section_kb(player_id: int) -> InlineKeyboardMarkup:
    """Под экраном раздела статистики другого игрока — назад к его профилю."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« К профилю", callback_data=f"player_profile_{player_id}"))
    return b.as_markup()


def stats_kb(sections: list[tuple[str, str]] | None = None) -> InlineKeyboardMarkup:
    """Клавиатура под экраном статистики.

    По 2 кнопки в ряд (v2.114.0) — 6 экранных ссылок в один столбец растягивали
    экран на 7 строк, читалось хуже, чем сгруппированное. Подписи сокращены
    (полные названия — в тексте самих экранов, тут только ярлыки для навигации).

    sections (v2.137.0) — [(ключ, заголовок), ...] разделов подробной
    статистики (STATS_SECTIONS, profile.py), только непустые; идут ПЕРВЫМИ
    рядами, по 2 в ряд, callback_data "stat_sec_{ключ}".
    """
    b = InlineKeyboardBuilder()
    if sections:
        _section_rows(b, sections, "stat_sec_")
    b.row(
        InlineKeyboardButton(text="📊 График рейтинга", callback_data="rating_chart"),
        InlineKeyboardButton(text="🔥 Карта активности", callback_data="activity_heatmap_me"),
    )
    b.row(
        InlineKeyboardButton(text="🕸 Радар стиля", callback_data="style_radar"),
        InlineKeyboardButton(text="🎬 Моя карьера", callback_data="career_recap"),
    )
    b.row(
        InlineKeyboardButton(text="🏅 Достижения", callback_data="my_achievements"),
        InlineKeyboardButton(text="📜 История матчей", callback_data="history_0"),
    )
    b.row(InlineKeyboardButton(text="📅 Сегодня в клубе", callback_data="menu_today"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def achievements_kb(category_progress: list[tuple[str, int, int]]) -> InlineKeyboardMarkup:
    """Клавиатура под оглавлением своих достижений (v2.133.0) — по кнопке на
    категорию, по одной в ряд, прогресс прямо на кнопке («🔥 Серии 5/9»).
    category_progress — [(категория, получено, всего), ...] из
    _achievement_category_progress (profile.py), в порядке CATEGORY_ORDER —
    индекс в этом списке и есть callback_data "ach_cat_{i}"."""
    b = InlineKeyboardBuilder()
    for i, (category, earned, total) in enumerate(category_progress):
        b.row(InlineKeyboardButton(
            text=f"{category} {earned}/{total}", callback_data=f"ach_cat_{i}",
        ))
    b.row(InlineKeyboardButton(text="« В мой профиль", callback_data="menu_stats"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def club_records_kb(categories: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """Оглавление «Рекордов клуба»: по кнопке на непустую категорию, по одной в
    ряд, число рекордов прямо на кнопке. categories — [(ключ, заголовок, число)]."""
    b = InlineKeyboardBuilder()
    for key, title, count in categories:
        b.row(InlineKeyboardButton(text=f"{title} {count}", callback_data=f"rec_cat_{key}"))
    b.row(InlineKeyboardButton(text="« К рейтингу клуба", callback_data="menu_leaderboard"))
    return b.as_markup()


def records_category_kb() -> InlineKeyboardMarkup:
    """Под экраном одной категории рекордов — назад к оглавлению."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« Все рекорды", callback_data="club_records"))
    return b.as_markup()


def player_achievements_kb(
    player_id: int, category_progress: list[tuple[str, int, int]],
) -> InlineKeyboardMarkup:
    """Клавиатура под оглавлением достижений другого игрока — те же кнопки
    категорий, что у achievements_kb, но callback_data "pach_{player_id}_{i}"."""
    b = InlineKeyboardBuilder()
    for i, (category, earned, total) in enumerate(category_progress):
        b.row(InlineKeyboardButton(
            text=f"{category} {earned}/{total}", callback_data=f"pach_{player_id}_{i}",
        ))
    b.row(InlineKeyboardButton(text="« К профилю", callback_data=f"player_profile_{player_id}"))
    return b.as_markup()


def achievement_category_kb(player_id: int | None = None) -> InlineKeyboardMarkup:
    """Клавиатура под экраном ОДНОЙ категории достижений — назад к оглавлению
    (своему, если player_id не задан, иначе к оглавлению этого игрока)."""
    b = InlineKeyboardBuilder()
    back_cb = "my_achievements" if player_id is None else f"player_achievements_{player_id}"
    b.row(InlineKeyboardButton(text="« Все категории", callback_data=back_cb))
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


def hall_of_fame_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Клавиатура для листания «Зала славы» (v2.116.0) — список правлений не
    ограничен по росту (в отличие от рекордов клуба), с каждой сменой трона
    становится длиннее; без пагинации в худшем случае (все правления с
    максимально драматичным нарративом) уже на ~15 записях упирается в лимит
    Telegram на сообщение (4096 символов) — поймано регресс-тестом."""
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"hall_of_fame_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"hall_of_fame_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="« К рейтингу клуба", callback_data="menu_leaderboard"))
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
    mvp_id: int | None = None,
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
            # 👑/🗡 приоритетнее 🌟 (босс-файт важнее звания месяца), 🌟
            # приоритетнее ❄️/🔥 (MVP месяца заметнее формы недели)
            if champion_id is not None and p.id == champion_id:
                badge = " 👑"
            elif challenger_id is not None and p.id == challenger_id:
                badge = " 🗡"
            elif mvp_id is not None and p.id == mvp_id:
                badge = " 🌟"
            elif inactive_ids and p.id in inactive_ids:
                badge = " ❄️"
            elif streak_map and streak_map.get(p.id, 0) >= 3:
                badge = " 🔥"
            else:
                badge = ""
            b.row(InlineKeyboardButton(
                text=f"{rank_str}{icon}{p.display_name}{badge}  ({round(p.rating)} pts)",
                callback_data=f"challenge_{p.id}",
            ))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
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
    """Клавиатура под таблицей рейтинга — кнопки профилей игроков.

    4 экранных ссылки ниже сгруппированы по 2 в ряд и с сокращёнными подписями
    (v2.114.0) — тот же приём и мотивация, что у stats_kb ниже."""
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
    b.row(
        InlineKeyboardButton(text="🏆 Рекорды клуба", callback_data="club_records"),
        InlineKeyboardButton(text="📋 Все матчи клуба", callback_data="club_matches_0"),
    )
    b.row(
        InlineKeyboardButton(text="⚔️ Кто кого бьёт", callback_data="dominance_matrix"),
        InlineKeyboardButton(text="🌡 Кто в форме", callback_data="form_index"),
    )
    b.row(InlineKeyboardButton(text="🏛 Трон", callback_data="hall_of_fame_0"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def club_matches_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Листание экрана «Все матчи клуба» (v2.156.0) + возврат к рейтингу клуба."""
    b = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"club_matches_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"club_matches_{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="« К рейтингу клуба", callback_data="menu_leaderboard"))
    return b.as_markup()


def back_to_leaderboard_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« К рейтингу клуба", callback_data="menu_leaderboard"))
    return b.as_markup()


def back_to_stats_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="« В мой профиль", callback_data="menu_stats"))
    return b.as_markup()


def year_vote_invite_kb() -> InlineKeyboardMarkup:
    """Кнопка под приглашением/напоминанием голосования — открывает бюллетень."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🗳 Голосовать", callback_data="yv_open"))
    return b.as_markup()


def year_vote_bulletin_kb() -> InlineKeyboardMarkup:
    """Клавиатура бюллетеня — по кнопке на номинацию, callback_data "yv_nom_{id}"
    (id — стабильный строковый id номинации, НЕ позиционный индекс в
    YEAR_VOTE_NOMINATIONS — список можно менять составом/порядком одной правкой,
    а бюллетень уже разослан игрокам с зашитыми кнопками; индекс после такой
    правки указывал бы уже на другую номинацию у тех, кто не обновил экран)."""
    b = InlineKeyboardBuilder()
    for n in YEAR_VOTE_NOMINATIONS:
        b.row(InlineKeyboardButton(text=f"{n.emoji} {n.name}", callback_data=f"yv_nom_{n.id}"))
    b.row(InlineKeyboardButton(text="« В меню", callback_data="back_to_menu"))
    return b.as_markup()


def year_vote_nomination_kb(
    nomination_id: str, candidates: list, current_pick: int | None,
) -> InlineKeyboardMarkup:
    """Экран выбора кандидата в одной номинации. candidates — допущенные игроки
    (без самого голосующего), current_pick — id уже выбранного (если есть) —
    помечается ✅ на кнопке, чтобы было видно текущий выбор при смене."""
    b = InlineKeyboardBuilder()
    for p in candidates:
        prefix = "✅ " if p.id == current_pick else ""
        b.row(InlineKeyboardButton(
            text=f"{prefix}{p.display_name}",
            callback_data=f"yv_pick_{nomination_id}_{p.id}",
        ))
    b.row(InlineKeyboardButton(text="« Назад к бюллетеню", callback_data="yv_open"))
    return b.as_markup()


def player_profile_kb(
    player_id: int, viewer_id: int | None = None, can_challenge: bool = True,
    sections: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура под профилем другого игрока. sections (v2.137.0) — разделы
    подробной статистики, callback_data "pstat_{player_id}_{ключ}"."""
    b = InlineKeyboardBuilder()
    if viewer_id is not None and viewer_id != player_id:
        if can_challenge:
            b.row(
                InlineKeyboardButton(text="⚔️ Вызвать", callback_data=f"challenge_{player_id}"),
                InlineKeyboardButton(
                    text="🎲 Сколько очков за матч", callback_data=f"what_if_{player_id}",
                ),
            )
        else:
            b.row(InlineKeyboardButton(
                text="🎲 Сколько очков за матч", callback_data=f"what_if_{player_id}",
            ))
        b.row(
            InlineKeyboardButton(text="🆚 Личные встречи", callback_data=f"h2h_{player_id}_0"),
            InlineKeyboardButton(text="🆚 Сравнить стили", callback_data=f"style_cmp_{player_id}"),
        )
    if sections:
        _section_rows(b, sections, f"pstat_{player_id}_")
    b.row(
        InlineKeyboardButton(
            text="📊 График рейтинга",
            callback_data=f"player_chart_{player_id}",
        ),
        InlineKeyboardButton(
            text="🕸 Радар стиля",
            callback_data=f"player_style_radar_{player_id}",
        ),
    )
    b.row(
        InlineKeyboardButton(
            text="🏅 Достижения",
            callback_data=f"player_achievements_{player_id}",
        ),
        InlineKeyboardButton(
            text="📜 История матчей",
            callback_data=f"player_history_{player_id}_0",
        ),
    )
    b.row(InlineKeyboardButton(text="« К рейтингу клуба", callback_data="menu_leaderboard"))
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
    b.row(InlineKeyboardButton(text="🆚 Сравнить стили", callback_data=f"style_cmp_{player_id}"))
    b.row(InlineKeyboardButton(text="« К профилю", callback_data=f"player_profile_{player_id}"))
    return b.as_markup()


def what_if_kb(player_id: int, can_challenge: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура под калькулятором «Что если?» (v2.126.0 — раньше экран был
    без единой кнопки, тупиковый: живая жалоба пользователя).

    can_challenge=False — та же причина, что у h2h_kb выше: зритель или
    соперник уже заняты другим активным матчем.
    """
    b = InlineKeyboardBuilder()
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


